"""In-memory Skynet state keyed by case id, persisted to data/state.json (owner: B).

The base World for a case comes from data/world.json (case `cable`, built by server.seed + server.fusion)
or from server.real.cases.get_world(case). Runtime changes (user zones and their evaluated events/alerts,
operator actions, alert statuses, reports, webhooks) live here and are layered on top of the base
World by `world(case)`. Nothing here mutates the base World.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("SKYNET_DATA_DIR") or ROOT / "data")
WORLD_PATH = Path(os.environ.get("SKYNET_WORLD") or DATA / "world.json")
STATE_PATH = DATA / "state.json"
REPORTS_DIR = DATA / "reports"
DEFAULT_CASE = "cable"
REAL_TTL_S = 300

_lock = threading.RLock()
_base: dict = {}          # case -> {"world": dict, "mtime": float|None, "loaded": float}
_state: dict = {}


class CaseUnavailable(Exception):
    pass


def _empty_state() -> dict:
    return {"cases": {}, "reports": {}, "webhooks": [], "webhook_log": [], "counters": {}}


def _empty_case() -> dict:
    # zones: user-created or edited zones by id; deleted: ids hidden from the base world;
    # results: zone id -> {"events", "alerts"} from server.zones.evaluate_zone.
    return {"zones": {}, "deleted": [], "results": {}, "actions": [], "op_events": [], "alert_status": {}}


def _load_state() -> None:
    global _state
    try:
        _state = json.loads(STATE_PATH.read_text())
        for k, v in _empty_state().items():
            _state.setdefault(k, v)
    except Exception:
        _state = _empty_state()


def save() -> None:
    with _lock:
        DATA.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_state, separators=(",", ":")))
        tmp.replace(STATE_PATH)


def next_id(name: str, prefix: str, width: int = 4) -> str:
    with _lock:
        n = _state["counters"].get(name, 0) + 1
        _state["counters"][name] = n
        return f"{prefix}{n:0{width}d}"


def case_state(case: str) -> dict:
    with _lock:
        cs = _state["cases"].setdefault(case, _empty_case())
        for k, v in _empty_case().items():
            cs.setdefault(k, v)
        return cs


# ---------------------------------------------------------------- base worlds

def _placeholder_world() -> dict:
    return {"scenario": {"id": "gof-seeded", "label": "Gulf of Finland · seeded scenario (not built yet)",
                         "start": 1794355200, "end": 1794384000, "now": 1794382200, "seed": 7,
                         "illustrative": True, "bbox": [22.5, 59.2, 28.5, 60.6]},
            "assets": [], "zones": [], "vessels": [], "events": [], "alerts": [], "actions": [], "_placeholder": True}


def _build_cable() -> dict:
    try:
        from server import seed  # owner A
        w = seed.build_world(7)
    except Exception as exc:  # A not ready yet: keep the app usable
        print(f"[store] cable world unavailable ({exc!r}); serving placeholder")
        return _placeholder_world()
    try:  # rebuild the missing data/world.json (same as `python -m server.seed`)
        if not WORLD_PATH.exists():
            WORLD_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = WORLD_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(w))
            tmp.replace(WORLD_PATH)
    except Exception as exc:
        print(f"[store] could not write {WORLD_PATH}: {exc!r}")
    return w


def base_world(case: str = DEFAULT_CASE) -> dict:
    case = case or DEFAULT_CASE
    with _lock:
        c = _base.get(case)
        if case == DEFAULT_CASE:
            mtime = WORLD_PATH.stat().st_mtime if WORLD_PATH.exists() else None
            stale_placeholder = c is not None and c.get("placeholder") and time.time() - c["loaded"] > 10
            if c is None or (mtime is not None and c["mtime"] != mtime) or stale_placeholder:
                if mtime is not None:
                    try:
                        w = json.loads(WORLD_PATH.read_text())
                    except Exception as exc:  # partially written by the seeder: retry next request
                        if c:
                            return c["world"]
                        print(f"[store] could not read {WORLD_PATH}: {exc!r}")
                        w = _build_cable()
                else:
                    w = _build_cable()
                c = _base[case] = {"world": w, "mtime": mtime, "loaded": time.time(),
                                   "placeholder": w.get("_placeholder", False)}
            return c["world"]
        if c is not None and time.time() - c["loaded"] < REAL_TTL_S:
            return c["world"]
    try:
        from server.real import cases  # owner C
        w = cases.get_world(case)
    except Exception as exc:
        raise CaseUnavailable(f"Case '{case}' is not available: {exc}") from exc
    if not isinstance(w, dict) or not w:
        raise CaseUnavailable(f"Case '{case}' is not available")
    with _lock:
        _base[case] = {"world": w, "mtime": None, "loaded": time.time()}
    return w


# ---------------------------------------------------------------- composed world

def world(case: str = DEFAULT_CASE, exclude_zone: str | None = None) -> dict:
    """Base World + runtime state. `exclude_zone` leaves out that zone's evaluated events and alerts
    (used when re-evaluating it). Returned containers are fresh; vessels/assets are shared, do not mutate."""
    base = base_world(case)
    with _lock:
        cs = case_state(case)
        hidden = set(cs["deleted"]) | set(cs["zones"])
        zones = [z for z in base.get("zones") or [] if z.get("id") not in hidden]
        zones += list(cs["zones"].values())

        # events/alerts of zones that were edited or deleted are replaced by their evaluation result
        replaced = {zid for zid in hidden if any(z.get("id") == zid for z in base.get("zones") or [])}
        events = [e for e in base.get("events") or [] if e.get("zone_id") not in replaced]
        alerts = [a for a in base.get("alerts") or [] if a.get("zone_id") not in replaced]
        seen_e = {e.get("id") for e in events}
        seen_a = {a.get("id") for a in alerts}
        for zid, res in cs["results"].items():
            if zid == exclude_zone or zid in cs["deleted"]:
                continue
            for e in res.get("events") or []:
                if e.get("id") not in seen_e:
                    seen_e.add(e.get("id"))
                    events.append(e)
            for a in res.get("alerts") or []:
                if a.get("id") not in seen_a:
                    seen_a.add(a.get("id"))
                    alerts.append(a)
        events += list(cs["op_events"])
        events.sort(key=lambda e: (int(e.get("t") or 0), str(e.get("id"))))
        st = cs["alert_status"]
        alerts = [{**a, "status": st[a["id"]]} if a.get("id") in st else a for a in alerts]
        alerts.sort(key=lambda a: (int(a.get("t") or 0), str(a.get("id"))))
        actions = list(base.get("actions") or []) + list(cs["actions"])

    out = dict(base)
    out.update(zones=zones, events=events, alerts=alerts, actions=actions)
    out.setdefault("assets", [])
    out.setdefault("vessels", [])
    return out


def find(items, _id):
    for x in items or []:
        if str(x.get("id")) == str(_id):
            return x
    return None


# ---------------------------------------------------------------- mutations

def put_zone(case: str, zone: dict, result: dict) -> tuple[list, list]:
    """Store a zone and its evaluation. Returns (new_alerts, all_alerts_of_zone)."""
    with _lock:
        cs = case_state(case)
        zid = zone["id"]
        if zid in cs["deleted"]:
            cs["deleted"].remove(zid)
        prev = {a.get("id") for a in (cs["results"].get(zid) or {}).get("alerts") or []}
        if not prev:
            prev = {a.get("id") for a in base_world(case).get("alerts") or [] if a.get("zone_id") == zid}
        cs["zones"][zid] = zone
        alerts = result.get("alerts") or []
        cs["results"][zid] = {"events": result.get("events") or [], "alerts": alerts}
        save()
        return [a for a in alerts if a.get("id") not in prev], alerts


def delete_zone(case: str, zid: str) -> bool:
    with _lock:
        cs = case_state(case)
        in_base = any(z.get("id") == zid for z in base_world(case).get("zones") or [])
        if zid not in cs["zones"] and not in_base:
            return False
        cs["zones"].pop(zid, None)
        cs["results"].pop(zid, None)
        if in_base and zid not in cs["deleted"]:
            cs["deleted"].append(zid)
        save()
        return True


def new_zone_id(case: str) -> str:
    w = world(case)
    n = 1
    for z in w["zones"]:
        try:
            n = max(n, int(str(z.get("id", "")).split("-")[1]) + 1)
        except (IndexError, ValueError):
            pass
    return f"Z-{n}"


def add_action(case: str, action: dict, event: dict, status: str) -> None:
    with _lock:
        cs = case_state(case)
        cs["actions"].append(action)
        cs["op_events"].append(event)
        cs["alert_status"][action["alert_id"]] = status
        save()


def add_report(report: dict) -> None:
    with _lock:
        _state["reports"][report["id"]] = report
        save()


def get_report(rid: str) -> dict | None:
    return _state["reports"].get(rid)


def list_reports(case: str | None = None) -> list:
    return [r for r in _state["reports"].values() if case is None or r.get("case") == case]


def add_webhook(url: str) -> dict:
    with _lock:
        for h in _state["webhooks"]:
            if h["url"] == url:
                return h
        h = {"id": next_id("webhook", "WH-"), "url": url, "events": ["alert.created"], "created": int(time.time())}
        _state["webhooks"].append(h)
        save()
        return h


def webhooks() -> list:
    return list(_state["webhooks"])


def log_webhook(entry: dict) -> None:
    with _lock:
        _state["webhook_log"].append(entry)
        del _state["webhook_log"][:-500]
        save()


def webhook_log() -> list:
    return list(reversed(_state["webhook_log"]))


def reset() -> None:
    """Reseed: drop cached base worlds, all zones except the seeded ones (Z-1), actions, statuses and
    reports. Webhook registrations are kept (they are API integrations); their log is cleared."""
    global _state
    with _lock:
        hooks = _state.get("webhooks") or []
        _state = _empty_state()
        _state["webhooks"] = hooks
        _base.clear()
        shutil.rmtree(REPORTS_DIR, ignore_errors=True)
        save()


_load_state()
