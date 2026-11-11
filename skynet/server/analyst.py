"""Skynet Analyst: POST /api/analyst. A tool-calling agent over the app's own data.

With ANTHROPIC_API_KEY (or OPENAI_API_KEY) set, a real LLM picks the tools. Otherwise a deterministic
planner parses the question and calls the same tools. Either way the answer is built only from tool
results: when the tools return nothing, the answer says so (`empty: true`) and never invents data.
"""
from __future__ import annotations

import inspect
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
SEV = {"low": 1, "medium": 2, "high": 3}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
WARRISK_SOURCES = [
    {"label": "JMIC advisories (UKMTO)", "url": "https://www.ukmto.org/partner-products/jmic-products"},
    {"label": "IMF PortWatch chokepoint transits", "url": "https://portwatch.imf.org/"},
    {"label": "gpsjam.org GNSS interference", "url": "https://gpsjam.org/"},
    {"label": "OFAC SDN list", "url": "https://sanctionssearch.ofac.treas.gov/"},
    {"label": "JWC listed areas (Lloyd's Market Association)", "url": "https://www.lmalloyds.com/LMA/Underwriting/Marine/JWLA/JWLA.aspx"},
]


class Question(BaseModel):
    question: str
    t: int | None = None
    case: str | None = None


# ---------------------------------------------------------------- world access

def _call_world_fn(fn, case):
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    try:
        return fn(case) if params else fn()
    except TypeError:
        return fn()


def load_world(case: str | None) -> dict:
    """World for a case: server.store (B) first, then server.real.cases (C), then data/world.json."""
    case = case or "cable"
    try:
        from server import store  # type: ignore  (owner B: world(case) = base world + runtime state)
        w = store.world(case)
        if isinstance(w, dict) and "vessels" in w:
            return w
    except Exception:
        pass
    if case != "cable":
        try:
            from server.real import cases  # type: ignore
            w = cases.get_world(case)
            if w:
                return w
        except Exception:
            pass
    try:
        from server import store  # type: ignore
        for name in ("get_world", "world_for", "load_world", "get"):
            fn = getattr(store, name, None)
            if callable(fn):
                w = _call_world_fn(fn, case)
                if isinstance(w, dict) and "vessels" in w:
                    return w
        for name in ("WORLD", "world", "STATE"):
            w = getattr(store, name, None)
            if isinstance(w, dict):
                if "vessels" in w:
                    return w
                if isinstance(w.get(case), dict) and "vessels" in w[case]:
                    return w[case]
    except Exception:
        pass
    p = ROOT / "data" / "world.json"
    if p.exists():
        return json.loads(p.read_text())
    try:
        from server import seed  # type: ignore
        return seed.build_world()
    except Exception:
        return {"scenario": None, "assets": [], "zones": [], "vessels": [], "events": [], "alerts": [], "actions": []}


# ---------------------------------------------------------------- formatting / geometry

def hhmm(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M UTC")


def day_label(t: int) -> str:
    d = datetime.fromtimestamp(t, timezone.utc)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b')}"


def when(t: int) -> str:
    return f"{day_label(t)} {hhmm(t)}"


def latlon(lon: float, lat: float) -> str:
    return f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'} {abs(lon):.3f}°{'E' if lon >= 0 else 'W'}"


def _seg_km(p, a, b):
    """Distance in km from point p to segment ab (all [lon, lat]), local equirectangular."""
    k = 111.32 * math.cos(math.radians(p[1]))
    ax, ay = (a[0] - p[0]) * k, (a[1] - p[1]) * 110.57
    bx, by = (b[0] - p[0]) * k, (b[1] - p[1]) * 110.57
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    u = 0 if L == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L))
    return math.hypot(ax + u * dx, ay + u * dy)


def lines_of(geom) -> list:
    if not geom:
        return []
    if geom.get("type") == "LineString":
        return [geom["coordinates"]]
    if geom.get("type") == "MultiLineString":
        return list(geom["coordinates"])
    if geom.get("type") == "Point":
        return [[geom["coordinates"], geom["coordinates"]]]
    return []


def dist_to_asset_km(lon, lat, asset) -> float:
    best = math.inf
    for line in lines_of(asset.get("geometry")):
        for a, b in zip(line, line[1:] or line):
            best = min(best, _seg_km((lon, lat), a, b))
    return best


def bbox_of(points, pad=0.05):
    if not points:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [round(min(xs) - pad, 4), round(min(ys) - pad, 4), round(max(xs) + pad, 4), round(max(ys) + pad, 4)]


def interp(track, t):
    """Interpolated [lon, lat, sog, cog] at t from [[t, lon, lat, sog, cog], ...]; None outside the track."""
    if not track or t < track[0][0] or t > track[-1][0]:
        return None
    lo, hi = 0, len(track) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if track[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    a, b = track[lo], track[hi]
    f = 0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
    out = [a[i] + (b[i] - a[i]) * f if i < 3 else a[i] for i in range(1, min(len(a), 5))]
    while len(out) < 4:
        out.append(None)
    return out


def state_at(v, t):
    for s in v.get("states") or []:
        if s["start"] <= t <= s["end"]:
            return s["state"]
    return None


def position_at(world, v, t):
    """Best-known position at t, without extrapolating beyond what the record covers."""
    p = interp(v.get("track") or [], t)
    if p:
        return {"lon": p[0], "lat": p[1], "sog": p[2], "cog": p[3], "basis": "fused track"}
    static = (world.get("scenario") or {}).get("time_mode") == "static"
    tol = math.inf if static else 1800
    cands = [(abs(r[0] - t), r[1], r[2], "AIS claimed") for r in v.get("claimed") or []]
    cands += [(abs(o["t"] - t), o["lon"], o["lat"], o.get("sensor") or "SAR") for o in v.get("observed") or []]
    cands = [c for c in cands if c[0] <= tol]
    if not cands:
        return None
    c = min(cands, key=lambda c: c[0])
    return {"lon": c[1], "lat": c[2], "sog": None, "cog": None, "basis": c[3]}


def window(world):
    sc = world.get("scenario") or {}
    ts = [r[0] for v in world.get("vessels", []) for r in (v.get("track") or v.get("claimed") or [])]
    start = sc.get("start") or (min(ts) if ts else None)
    end = sc.get("end") or (max(ts) if ts else None)
    return start, end


def now_of(world, t=None):
    sc = world.get("scenario") or {}
    if t:
        return int(t)
    return int(sc.get("now") or sc.get("end") or datetime.now(timezone.utc).timestamp())


# ---------------------------------------------------------------- lookups

def find_vessel(world, ref):
    if not ref:
        return None
    r = str(ref).strip().lower()
    for v in world.get("vessels", []):
        if r in (str(v.get("id", "")).lower(), str(v.get("mmsi", "")), str(v.get("imo", "")).lower(),
                 str(v.get("name", "")).lower()):
            return v
    for v in world.get("vessels", []):
        if len(r) >= 4 and r in str(v.get("name", "")).lower():
            return v
    return None


def _asset_keys(a):
    keys = {str(a.get("id", "")).lower(), str(a.get("alias") or "").lower(), str(a.get("name") or "").lower()}
    keys.add(re.sub(r"\s*\(.*?\)", "", str(a.get("name") or "")).strip().lower())
    return {k for k in keys if len(k) >= 3}


def find_asset(world, ref):
    assets = world.get("assets", [])
    if not ref:
        return next((a for a in assets if a.get("protected")), assets[0] if assets else None)
    r = str(ref).strip().lower()
    for a in assets:
        if r in _asset_keys(a):
            return a
    for a in assets:
        if any(r in k or k in r for k in _asset_keys(a)):
            return a
    return None


def find_zone(world, ref):
    zones = world.get("zones", [])
    if not ref:
        return zones[0] if zones else None
    r = str(ref).strip().lower()
    for z in zones:
        if r in (str(z.get("id", "")).lower(), str(z.get("name", "")).lower()):
            return z
    for z in zones:
        if r in str(z.get("name", "")).lower():
            return z
    return None


def vessel_brief(v):
    return {k: v.get(k) for k in ("id", "name", "mmsi", "imo", "flag", "type", "length_m")}


def risk_of(v):
    r = v.get("risk") or {}
    return {"score": r.get("score"), "level": r.get("level")} if r.get("score") is not None else None


# ---------------------------------------------------------------- tools

def tool_list_vessels(world, t, filter=None, **_):
    f = filter or {}
    if isinstance(f, str):
        f = {"state": f} if f in ("dark", "spoofing", "verified") else {"query": f}
    out = []
    for v in world.get("vessels", []):
        st = state_at(v, t)
        if f.get("state") and st != f["state"]:
            continue
        if f.get("type") and str(v.get("type", "")).lower() != str(f["type"]).lower():
            continue
        score = (v.get("risk") or {}).get("score") or 0
        if f.get("min_risk") is not None and score < f["min_risk"]:
            continue
        q = str(f.get("query") or "").lower()
        if q and q not in json.dumps(vessel_brief(v)).lower():
            continue
        pos = position_at(world, v, t)
        out.append({**vessel_brief(v), "state": st, "risk": risk_of(v),
                    "top_reason": ((v.get("risk") or {}).get("reasons") or [{}])[0].get("text"),
                    "position": pos and {"lon": round(pos["lon"], 4), "lat": round(pos["lat"], 4)}})
    if f.get("sort", "risk") == "risk":
        out.sort(key=lambda x: -((x["risk"] or {}).get("score") or 0))
    lim = int(f.get("limit") or 50)
    return {"t": t, "count": len(out), "vessels": out[:lim]}


def tool_vessels_near_asset(world, t, asset=None, km=2, **_):
    a = find_asset(world, asset)
    if not a:
        return {"error": f"No asset called '{asset}' in this case",
                "assets": [x.get("alias") or x.get("name") for x in world.get("assets", [])]}
    km = float(km or 2)
    hits = []
    for v in world.get("vessels", []):
        p = position_at(world, v, t)
        if not p:
            continue
        d = dist_to_asset_km(p["lon"], p["lat"], a)
        if d <= km:
            claimed = interp(v.get("claimed") or [], t)
            hits.append({**vessel_brief(v), "km": round(d, 2), "state": state_at(v, t), "risk": risk_of(v),
                         "sog": p["sog"] if p["sog"] is None else round(p["sog"], 1),
                         "lon": round(p["lon"], 4), "lat": round(p["lat"], 4), "basis": p["basis"],
                         "ais_reporting": claimed is not None})
    hits.sort(key=lambda h: h["km"])
    return {"asset": {"id": a.get("id"), "alias": a.get("alias"), "name": a.get("name")}, "km": km, "t": t,
            "count": len(hits), "vessels": hits}


def _resolve_day(world, day):
    """'tuesday' / 'today' / 'yesterday' / 'tomorrow' / '2026-11-10' / 'march' -> (start, end) epoch."""
    ref = datetime.fromtimestamp(now_of(world), timezone.utc).date()
    d = str(day).strip().lower()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
        s = date.fromisoformat(d)
        e = s + timedelta(days=1)
    elif d in ("today", "now", "right now"):
        s, e = ref, ref + timedelta(days=1)
    elif d == "yesterday":
        s = ref - timedelta(days=1)
        e = ref
    elif d == "tomorrow":
        s = ref + timedelta(days=1)
        e = s + timedelta(days=1)
    elif d.replace("last ", "") in DAYS:
        wd = DAYS.index(d.replace("last ", ""))
        back = (ref.weekday() - wd) % 7
        if d.startswith("last ") and back == 0:
            back = 7
        s = ref - timedelta(days=back)
        e = s + timedelta(days=1)
    elif d.replace("last ", "") in MONTHS:
        m = MONTHS.index(d.replace("last ", "")) + 1
        y = ref.year if (m < ref.month or (m == ref.month and not d.startswith("last "))) else ref.year - 1
        s = date(y, m, 1)
        e = date(y + (m == 12), m % 12 + 1, 1)
    else:
        return None
    ts = lambda x: int(datetime(x.year, x.month, x.day, tzinfo=timezone.utc).timestamp())
    return ts(s), ts(e), d


def tool_vessel_position(world, t, vessel=None, at=None, day=None, **_):
    v = find_vessel(world, vessel)
    start, end = window(world)
    if not v:
        return {"error": f"No vessel '{vessel}' in this case"}
    if day:
        rd = _resolve_day(world, day)
        if not rd:
            return {"error": f"Could not read the day '{day}'"}
        s, e, label = rd
        track = v.get("track") or [[r[0], r[1], r[2], None, None] for r in v.get("claimed") or []]
        pts = [r for r in track if s <= r[0] < e]
        if not pts:
            near = None
            if track:
                r = track[0] if s < track[0][0] else track[-1]
                near = {"t": r[0], "lon": r[1], "lat": r[2]}
            return {"vessel": vessel_brief(v), "empty": True, "requested": {"from": s, "to": e, "label": label},
                    "covered": {"from": start, "to": end}, "nearest": near}
        step = max(1, len(pts) // 8)
        sample = pts[::step] + ([pts[-1]] if pts[-1] is not pts[::step][-1] else [])
        assets = world.get("assets", [])
        closest = None
        for a in assets:
            for r in pts:
                d = dist_to_asset_km(r[1], r[2], a)
                if closest is None or d < closest[0]:
                    closest = (d, a, r[0])
        darks = [d for d in v.get("dark_periods") or [] if d["end"] >= s and d["start"] < e]
        evs = [x for x in world.get("events", []) if x.get("vessel_id") == v["id"] and s <= x["t"] < e]
        return {"vessel": vessel_brief(v), "requested": {"from": s, "to": e, "label": label},
                "covered": {"from": pts[0][0], "to": pts[-1][0]},
                "first": {"t": pts[0][0], "lon": round(pts[0][1], 4), "lat": round(pts[0][2], 4)},
                "last": {"t": pts[-1][0], "lon": round(pts[-1][1], 4), "lat": round(pts[-1][2], 4)},
                "samples": [[r[0], round(r[1], 4), round(r[2], 4)] for r in sample],
                "closest_asset": closest and {"asset": closest[1].get("alias") or closest[1].get("name"),
                                              "km": round(closest[0], 2), "t": closest[2]},
                "dark_periods": darks, "events": [_ev_brief(x) for x in evs]}
    tt = int(at or t)
    p = position_at(world, v, tt)
    if not p:
        return {"vessel": vessel_brief(v), "empty": True, "requested": {"from": tt, "to": tt},
                "covered": {"from": start, "to": end}}
    near = sorted(((dist_to_asset_km(p["lon"], p["lat"], a), a) for a in world.get("assets", [])), key=lambda x: x[0])
    return {"vessel": vessel_brief(v), "t": tt, "lon": round(p["lon"], 4), "lat": round(p["lat"], 4),
            "sog": p["sog"], "basis": p["basis"], "state": state_at(v, tt),
            "claimed": (lambda c: c and {"lon": round(c[0], 4), "lat": round(c[1], 4)})(interp(v.get("claimed") or [], tt)),
            "nearest_asset": near and {"asset": near[0][1].get("alias") or near[0][1].get("name"), "km": round(near[0][0], 2)}}


def _ev_brief(e):
    return {k: e.get(k) for k in ("id", "vessel_id", "type", "t", "end", "severity", "summary", "source", "zone_id",
                                  "asset_id", "lon", "lat")}


def tool_vessel_events(world, t, vessel=None, **kw):
    v = find_vessel(world, vessel)
    if not v:
        return {"error": f"No vessel '{vessel}' in this case"}
    f = kw.get("from") or kw.get("from_") or 0
    to = kw.get("to") or t
    evs = [x for x in world.get("events", []) if x.get("vessel_id") == v["id"] and f <= x["t"] <= to]
    evs.sort(key=lambda x: x["t"])
    return {"vessel": vessel_brief(v), "from": f, "to": to, "count": len(evs), "events": [_ev_brief(x) for x in evs]}


def tool_zone_summary(world, t, zone=None, **_):
    z = find_zone(world, zone)
    if not z:
        return {"error": f"No zone '{zone}' in this case", "zones": [x.get("id") for x in world.get("zones", [])]}
    evs = [x for x in world.get("events", []) if x.get("zone_id") == z["id"] and x["t"] <= t]
    als = [x for x in world.get("alerts", []) if x.get("zone_id") == z["id"] and x["t"] <= t]
    metrics = None
    try:
        from server import zones as zmod  # type: ignore
        metrics = zmod.zone_metrics(world, z, t)
    except Exception:
        pass
    evs.sort(key=lambda x: (-SEV.get(x.get("severity"), 0), -x["t"]))
    return {"zone": {"id": z["id"], "name": z.get("name"), "purpose": z.get("purpose"), "asset_id": z.get("asset_id")},
            "t": t, "event_count": len(evs), "alert_count": len(als),
            "alerts": [{k: a.get(k) for k in ("id", "vessel_id", "t", "severity", "headline", "status")} for a in als],
            "events": [_ev_brief(x) for x in evs[:12]], "metrics": metrics,
            "vessel_ids": sorted({x["vessel_id"] for x in evs if x.get("vessel_id")})}


def tool_risk(world, t, vessel=None, **_):
    v = find_vessel(world, vessel)
    if not v:
        return {"error": f"No vessel '{vessel}' in this case"}
    r = v.get("risk") or {}
    return {"vessel": vessel_brief(v), "score": r.get("score"), "level": r.get("level"),
            "reasons": r.get("reasons") or [], "state": state_at(v, t), "voyage": v.get("voyage"),
            "dark_periods": v.get("dark_periods") or []}


async def tool_assess_voyage(world, t, vessel=None, route=None, date=None, **_):
    v = find_vessel(world, vessel) or next((x for x in world.get("vessels", []) if x.get("voyage")), None)
    if not v:
        return {"error": f"No vessel '{vessel}' in this case"}
    voy = v.get("voyage") or {}
    route = route or voy.get("route_id")
    if not route:
        return {"error": f"{v['id']} has no voyage on record; name a route"}
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(date)):
        rd = _resolve_day(world, date)
        date = rd and datetime.fromtimestamp(rd[0], timezone.utc).date().isoformat()
    as_of = date or voy.get("departs")
    try:
        from server.real import warrisk  # type: ignore
        fn = warrisk.assess_voyage
    except Exception as e:
        return {"error": "War-risk engine not available (server.real.warrisk.assess_voyage is missing)",
                "detail": str(e), "vessel": vessel_brief(v), "voyage": voy, "missing": True}
    ship = {"name": v.get("name", ""), "imo": str(v.get("imo") or ""), "type": v.get("type") or "tanker",
            "hull_usd_m": voy.get("hull_usd_m") or 100, "flag": v.get("flag") or "", "mmsi": v.get("mmsi"),
            "ais_on": state_at(v, t) != "dark", "id": v.get("id")}
    try:
        res = fn(ship, route, as_of)
        if inspect.isawaitable(res):
            res = await res
    except Exception as e:
        return {"error": f"War-risk engine failed: {e}", "vessel": vessel_brief(v), "voyage": voy}
    if hasattr(res, "model_dump"):
        res = res.model_dump()
    return {"vessel": vessel_brief(v), "voyage": voy, "route": route, "as_of": as_of, "assessment": res}


TOOLS = {
    "list_vessels": tool_list_vessels, "vessels_near_asset": tool_vessels_near_asset,
    "vessel_position": tool_vessel_position, "vessel_events": tool_vessel_events,
    "zone_summary": tool_zone_summary, "risk": tool_risk, "assess_voyage": tool_assess_voyage,
}

TOOL_SPECS = [
    ("list_vessels", "List vessels in the current case, sorted by risk. Filter by state at time t (verified, dark, spoofing), type, min_risk, limit.",
     {"filter": {"type": "object", "properties": {"state": {"type": "string", "enum": ["verified", "dark", "spoofing"]},
                                                   "type": {"type": "string"}, "min_risk": {"type": "number"},
                                                   "limit": {"type": "integer"}}}}),
    ("vessels_near_asset", "Vessels within km of an asset (cable/pipeline, by alias like 'Cable A' or name) at time t.",
     {"asset": {"type": "string"}, "km": {"type": "number"}, "t": {"type": "integer", "description": "epoch seconds; default now"}}),
    ("vessel_position", "Where a vessel was at time `at` (epoch) or during a `day` (weekday name, 'today', 'yesterday', month name, or YYYY-MM-DD). Returns empty with the covered window when no data exists.",
     {"vessel": {"type": "string"}, "at": {"type": "integer"}, "day": {"type": "string"}}),
    ("vessel_events", "Events on a vessel's record between from and to (epoch seconds).",
     {"vessel": {"type": "string"}, "from": {"type": "integer"}, "to": {"type": "integer"}}),
    ("zone_summary", "Alerts, events and metrics for a watch zone (id like 'Z-1' or name) up to t.",
     {"zone": {"type": "string"}}),
    ("risk", "A vessel's Skynet risk score, level and reasons.", {"vessel": {"type": "string"}}),
    ("assess_voyage", "War-risk insurance assessment for a vessel on a route on a date (JMIC incidents, JWC listed area, gpsjam, PortWatch, OFAC SDN). Defaults to the vessel's voyage on record.",
     {"vessel": {"type": "string"}, "route": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD or 'tomorrow'"}}),
]


async def run_tool(world, t, name, args):
    fn = TOOLS[name]
    args = dict(args or {})
    tt = int(args.pop("t", None) or t)
    res = fn(world, tt, **args)
    if inspect.isawaitable(res):
        res = await res
    return res


def summarize(name, res):
    if not isinstance(res, dict):
        return "done"
    if res.get("error"):
        return res["error"]
    if res.get("empty"):
        return "no data in the requested window"
    if name == "list_vessels":
        return f"{res['count']} vessel{'s' * (res['count'] != 1)}"
    if name == "vessels_near_asset":
        return f"{res['count']} vessel{'s' * (res['count'] != 1)} within {res['km']:g} km of {res['asset']['alias'] or res['asset']['name']}"
    if name == "vessel_position":
        if "samples" in res:
            return f"{len(res['samples'])} positions {hhmm(res['covered']['from'])}–{hhmm(res['covered']['to'])}"
        return f"{latlon(res['lon'], res['lat'])} at {hhmm(res['t'])}"
    if name == "vessel_events":
        return f"{res['count']} event{'s' * (res['count'] != 1)}"
    if name == "zone_summary":
        return f"{res['alert_count']} alerts, {res['event_count']} events"
    if name == "risk":
        return f"score {res.get('score')} ({res.get('level')})"
    if name == "assess_voyage":
        a = res.get("assessment") or {}
        return f"war-risk {a.get('score')} ({a.get('level')})"
    return "done"


# ---------------------------------------------------------------- answer helpers

def layer_sources(world, vessel_ids=(), layers=("ais", "sar", "rf", "fusion")):
    names = {"ais": "AIS claimed positions", "sar": "SAR radar detections", "rf": "RF emissions",
             "fusion": "Skynet fusion record", "zones": "Watch zone rules"}
    sc = world.get("scenario") or {}
    tag = " (simulated)" if sc.get("illustrative", True) else ""
    out = []
    for l in layers:
        if l == "rf" and not any(v.get("emitted") for v in world.get("vessels", [])):
            continue
        if l == "sar" and not any(v.get("observed") for v in world.get("vessels", [])):
            continue
        url = f"/v1/vessels/{vessel_ids[0]}/record" if l == "fusion" and vessel_ids else None
        out.append({"label": names[l] + (tag if l in ("ais", "sar", "rf") else ""), "url": url})
    for s in sc.get("sources") or []:
        out.append({"label": s.get("label"), "url": s.get("url")})
    return out


def template_for(world, v=None):
    if v and v.get("voyage"):
        return "insurance"
    p = next((z.get("purpose") for z in world.get("zones", []) if z.get("purpose") in ("cable", "sanctions", "insurance")), None)
    if v and str(v.get("id", "")).startswith(("S", "D")) and not world.get("assets"):
        return "sanctions"
    return p or ("cable" if world.get("assets") else "sanctions")


def show_action(vessel_ids, bbox=None, t=None, label="Show on map"):
    return {"type": "show_on_map", "label": label, "vessel_ids": list(vessel_ids), "bbox": bbox, "t": t}


def report_action(vessel_id, template):
    return {"type": "report", "label": f"Export {template} report", "vessel_id": vessel_id, "template": template}


def state_word(s):
    return {"dark": "AIS dark", "spoofing": "false AIS position", "verified": "verified"}.get(s or "", s or "unknown")


def answer(q, **kw):
    base = {"question": q, "verdict": "", "risk": None, "reasons": [], "sources": [], "actions": [],
            "tool_calls": [], "empty": False, "engine": "planner"}
    base.update(kw)
    base["reasons"] = [r for r in base["reasons"] if r][:3]
    return base


def window_text(world):
    s, e = window(world)
    if not s:
        return "this case has no time-stamped data"
    return f"{when(s)} – {hhmm(e)}" if day_label(s) == day_label(e) else f"{when(s)} – {when(e)}"


# ---------------------------------------------------------------- planner

def parse(world, q, t):
    ql = q.lower()
    intent = {"vessels": [], "asset": None, "zone": None, "km": None, "day": None}
    ids = {str(v["id"]).upper(): v for v in world.get("vessels", [])}
    for m in re.finditer(r"\b([a-z]{1,2})[- ]?(\d{1,4})\b", ql):
        cand = f"{m.group(1).upper()}-{m.group(2)}"
        if cand in ids and cand not in intent["vessels"]:
            intent["vessels"].append(cand)
    for v in world.get("vessels", []):
        nm = str(v.get("name") or "").lower()
        if len(nm) >= 4 and nm in ql and v["id"] not in intent["vessels"]:
            intent["vessels"].append(v["id"])
        if v.get("mmsi") and str(v["mmsi"]) in ql and v["id"] not in intent["vessels"]:
            intent["vessels"].append(v["id"])
    best = None
    for a in world.get("assets", []):
        for k in _asset_keys(a):
            if re.search(r"(?<![a-z0-9])" + re.escape(k) + r"(?![a-z0-9])", ql) and (best is None or len(k) > best[0]):
                best = (len(k), a)
    intent["asset"] = best and best[1]
    for z in world.get("zones", []):
        if re.search(r"\b" + re.escape(str(z["id"]).lower()) + r"\b", ql) or str(z.get("name", "")).lower() in ql:
            intent["zone"] = z
    m = re.search(r"(\d+(?:\.\d+)?)\s*(km|kilomet|nm\b|nmi|nautical|mile)", ql)
    if m:
        intent["km"] = float(m.group(1)) * (1 if m.group(2).startswith("k") else 1.852)
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", ql)
    if m:
        intent["day"] = m.group(1)
    else:
        m = re.search(r"\b(last\s+)?(" + "|".join(DAYS + MONTHS) + r"|today|yesterday|tomorrow)\b", ql)
        if m:
            intent["day"] = (m.group(1) or "") .strip() + (" " if m.group(1) else "") + m.group(2)
            intent["day"] = intent["day"].strip()
    return intent


async def plan_and_answer(world, q, t):
    ql = q.lower()
    it = parse(world, q, t)
    calls = []

    async def call(name, **args):
        res = await run_tool(world, t, name, args)
        calls.append({"tool": name, "args": args, "summary": summarize(name, res)})
        return res

    def done(**kw):
        return answer(q, tool_calls=calls, **kw)

    vessel = it["vessels"][0] if it["vessels"] else None
    if not vessel:
        m = re.search(r"\b([dstv])-?(\d{2,3})\b", ql)
        if m:  # a vessel code that is not in this case: say so instead of guessing
            ref = f"{m.group(1).upper()}-{m.group(2)}"
            res = await call("risk", vessel=ref)
            return done(verdict=f"No vessel {ref} in this case, so there is nothing to report.", empty=True,
                        reasons=[f"This case has {len(world.get('vessels', []))} vessels; data covered: {window_text(world)}.",
                                 "Switch use case in the top bar if the vessel belongs to another one."])

    # 1. war-risk quote
    if re.search(r"\b(quote|hormuz|transit|war[- ]?risk|premium|insur|underwrit|price)", ql):
        if not vessel:
            vv = next((v for v in world.get("vessels", []) if v.get("voyage")), None)
            vessel = vv and vv["id"]
        if not vessel:
            return done(verdict="No vessel with a voyage on record in this case, so there is nothing to quote.",
                        empty=True, reasons=["Name a vessel, or switch to the war-risk case (T-11)."])
        when_ = it["day"] if it["day"] else None
        res = await call("assess_voyage", vessel=vessel, **({"date": when_} if when_ else {}))
        rk = await call("risk", vessel=vessel)
        v = find_vessel(world, vessel)
        if res.get("missing") or res.get("error"):
            voy = res.get("voyage") or {}
            return done(verdict=f"Can't price {vessel} yet: {res.get('error')}.", empty=True,
                        risk=risk_of(v) if v else None,
                        reasons=[voy and f"Voyage on record: {voy.get('label')} via {voy.get('via')}, departs {voy.get('departs')}, hull ${voy.get('hull_usd_m')}m.",
                                 rk.get("score") is not None and f"Skynet behaviour risk {rk['score']} ({rk['level']}): {(rk.get('reasons') or [{}])[0].get('text', '')}"],
                        sources=layer_sources(world, [vessel]),
                        actions=[show_action([vessel], t=t), report_action(vessel, "insurance")])
        return done(**quote_answer(world, v, res, rk, t))

    # 2. vessels near an asset
    near_words = re.search(r"\b(near|within|close to|around|over|next to|by)\b", ql)
    if it["asset"] or (it["km"] and near_words and world.get("assets")) or (re.search(r"\bcable|pipeline", ql) and near_words):
        a = it["asset"] or find_asset(world, None)
        if not a:
            m = re.search(r"\b(cable|pipeline)\s+([a-z0-9-]+)", ql)
            res = await call("vessels_near_asset", asset=m.group(0) if m else None, km=it["km"] or 2, t=t)
            return done(**near_answer(world, res, t))
        km = it["km"] or 2
        res = await call("vessels_near_asset", asset=a.get("alias") or a.get("id"), km=km, t=t)
        return done(**near_answer(world, res, t))

    # 3. where was a vessel
    if vessel and (it["day"] or re.search(r"\b(where|position|located|location|track|go|went)\b", ql)):
        if it["day"] and it["day"] not in ("today", "now"):
            res = await call("vessel_position", vessel=vessel, day=it["day"])
        elif re.search(r"\bwhere was|\btoday\b|\btrack\b|\bwent\b", ql):
            res = await call("vessel_position", vessel=vessel, day="today")
        else:
            res = await call("vessel_position", vessel=vessel, at=t)
        return done(**position_answer(world, res, t))

    # 4. zone
    if it["zone"] or re.search(r"\bzone\b", ql):
        z = it["zone"] or find_zone(world, None)
        if not z:
            return done(verdict="This case has no watch zones yet.", empty=True,
                        reasons=["Create one in Alert mode → Watch zones."])
        res = await call("zone_summary", zone=z["id"])
        return done(**zone_answer(world, res, t))

    # 5. a named vessel: risk + events
    if vessel:
        rk = await call("risk", vessel=vessel)
        evs = await call("vessel_events", vessel=vessel, to=t)
        return done(**vessel_answer(world, rk, evs, t))

    # 6. dark / spoofing lists
    for word, st in (("dark", "dark"), ("ais off", "dark"), ("spoof", "spoofing"), ("false position", "spoofing"),
                     ("fake", "spoofing")):
        if word in ql:
            res = await call("list_vessels", filter={"state": st})
            return done(**list_answer(world, res, t, st))

    # 7. riskiest / suspicious
    if re.search(r"riski|risky|highest|most suspicious|suspicious|worst|top|dangerous|concern|watch", ql):
        res = await call("list_vessels", filter={"limit": 5})
        return done(**list_answer(world, res, t, None))

    # 8. what happened (no vessel)
    if re.search(r"what happened|summary|overview|anything|alerts?|events?", ql):
        z = find_zone(world, None)
        if z:
            res = await call("zone_summary", zone=z["id"])
            return done(**zone_answer(world, res, t))
        res = await call("list_vessels", filter={"limit": 5})
        return done(**list_answer(world, res, t, None))

    return done(verdict="I couldn't match that question to Skynet's data, so I won't guess.", empty=True,
                reasons=["I can answer about vessels (D-04, T-11…), assets (Cable A), zones (Z-1), dark or spoofing ships, and war-risk quotes.",
                         f"Data covered in this case: {window_text(world)}."])


def near_answer(world, res, t):
    if res.get("error"):
        return {"verdict": res["error"] + ".", "empty": True,
                "reasons": [f"Assets in this case: {', '.join(map(str, res.get('assets') or [])) or 'none'}."]}
    hits, a = res["vessels"], res["asset"]
    name = a.get("alias") or a.get("name")
    start, end = window(world)
    if not hits:
        cov = start is not None and not (start <= t <= end)
        return {"verdict": f"No vessels within {res['km']:g} km of {name} at {when(t)}.", "empty": True,
                "reasons": [f"Data covered: {window_text(world)}." if cov else
                            f"Checked {len(world.get('vessels', []))} vessels' fused positions (AIS + radar) at {hhmm(t)}."],
                "sources": layer_sources(world), "actions": [show_action([], bbox=bbox_of([p for l in lines_of(world_asset(world, a['id']).get('geometry')) for p in l]), t=t)]}
    top = max(hits, key=lambda h: (h["risk"] or {}).get("score") or 0)
    parts = [f"{h['id']} ({state_word(h['state'])}, {h['km']:.1f} km)" for h in hits[:4]]
    verdict = f"{len(hits)} ship{'s' * (len(hits) != 1)} within {res['km']:g} km of {name} at {hhmm(t)}: " + ", ".join(parts) + \
              (f" and {len(hits) - 4} more" if len(hits) > 4 else "") + "."
    reasons = []
    for h in sorted(hits, key=lambda h: -((h["risk"] or {}).get("score") or 0))[:3]:
        v = find_vessel(world, h["id"])
        why = ((v.get("risk") or {}).get("reasons") or [{}])[0].get("text")
        spd = f", {h['sog']:.1f} kn" if h.get("sog") is not None else ""
        ais = "" if h["ais_reporting"] else ", no AIS at this time"
        reasons.append(f"{h['id']} {h['name']}: {h['km']:.2f} km from the cable{spd}{ais}" +
                       (f", risk {(h['risk'] or {}).get('score')}. {why.rstrip('.')}." if h.get("risk") and why else "."))
    ids = [h["id"] for h in hits]
    return {"verdict": verdict, "risk": top["risk"], "reasons": reasons,
            "sources": layer_sources(world, [top["id"]]),
            "actions": [show_action(ids, bbox=bbox_of([[h["lon"], h["lat"]] for h in hits], 0.03), t=t),
                        report_action(top["id"], "cable" if world.get("assets") else template_for(world))]}


def world_asset(world, aid):
    return next((a for a in world.get("assets", []) if a.get("id") == aid), {})


def position_answer(world, res, t):
    if res.get("error"):
        return {"verdict": res["error"] + ".", "empty": True}
    v = res["vessel"]
    vv = find_vessel(world, v["id"])
    if res.get("empty"):
        req = res["requested"]
        what = req.get("label") or ""
        span = f"{day_label(req['from'])}" if req["to"] - req["from"] <= 86400 else \
            f"{day_label(req['from'])} – {day_label(req['to'] - 1)}"
        reasons = [f"Skynet's record for {v['id']} covers {window_text(world)} only; {what.title() or 'that time'} ({span}) is outside it.",
                   "Nothing was invented: no AIS, radar or RF data exists for that period in this case."]
        n = res.get("nearest")
        if n:
            reasons.append(f"Closest data point: {when(n['t'])} at {latlon(n['lon'], n['lat'])}.")
        return {"verdict": f"No data for {v['id']} {v.get('name') or ''} on {span}.".replace("  ", " "), "empty": True,
                "reasons": reasons, "risk": None, "sources": layer_sources(world, [v["id"]]),
                "actions": [show_action([v["id"]], t=n["t"] if n else None, label="Show nearest data on map")] if n else []}
    if "samples" in res:
        f, l = res["first"], res["last"]
        ca = res.get("closest_asset")
        verdict = f"{v['id']} {v.get('name')} on {day_label(f['t'])}: from {latlon(f['lon'], f['lat'])} at {hhmm(f['t'])} to {latlon(l['lon'], l['lat'])} at {hhmm(l['t'])}" + \
                  (f", closest to {ca['asset']} at {ca['km']:.1f} km ({hhmm(ca['t'])})." if ca else ".")
        reasons = []
        for d in res.get("dark_periods") or []:
            reasons.append(f"AIS dark {d.get('minutes') or round((d['end'] - d['start']) / 60)} min, {hhmm(d['start'])}–{hhmm(d['end'])}; position from radar/RF.")
        for e in sorted(res.get("events") or [], key=lambda e: -SEV.get(e.get("severity"), 0)):
            if e["type"] != "dark_period":
                reasons.append(f"{hhmm(e['t'])}: {e['summary']}")
        if res["covered"]["to"] - res["covered"]["from"] < 0.9 * (res["requested"]["to"] - res["requested"]["from"]):
            reasons.append(f"Record covers {hhmm(res['covered']['from'])}–{hhmm(res['covered']['to'])} of that day only.")
        pts = [[s[1], s[2]] for s in res["samples"]]
        return {"verdict": verdict, "risk": risk_of(vv), "reasons": reasons[:3],
                "sources": layer_sources(world, [v["id"]]),
                "actions": [show_action([v["id"]], bbox=bbox_of(pts, 0.03), t=l["t"], label="Show track on map"),
                            report_action(v["id"], template_for(world, vv))]}
    na = res.get("nearest_asset")
    verdict = f"{v['id']} {v.get('name')} at {hhmm(res['t'])}: {latlon(res['lon'], res['lat'])}, {state_word(res['state'])}" + \
              (f", {na['km']:.1f} km from {na['asset']}." if na else ".")
    reasons = [f"Position basis: {res['basis']}."]
    if res.get("claimed") and res["state"] == "spoofing":
        reasons.append(f"AIS claims {latlon(res['claimed']['lon'], res['claimed']['lat'])} instead.")
    if res.get("sog") is not None:
        reasons.append(f"Speed {res['sog']:.1f} kn.")
    return {"verdict": verdict, "risk": risk_of(vv), "reasons": reasons, "sources": layer_sources(world, [v["id"]]),
            "actions": [show_action([v["id"]], bbox=bbox_of([[res["lon"], res["lat"]]], 0.05), t=res["t"]),
                        report_action(v["id"], template_for(world, vv))]}


def zone_answer(world, res, t):
    if res.get("error"):
        return {"verdict": res["error"] + ".", "empty": True}
    z = res["zone"]
    if not res["event_count"] and not res["alert_count"]:
        return {"verdict": f"Nothing recorded in {z['id']} {z['name']} up to {when(t)}.", "empty": True,
                "reasons": [f"Data covered: {window_text(world)}."], "sources": layer_sources(world, layers=("fusion", "zones"))}
    worst = sorted(res["alerts"], key=lambda a: (-SEV.get(a.get("severity"), 0), a["t"]))
    head = f" Most serious: {worst[0]['headline']} ({worst[0]['vessel_id']}, {hhmm(worst[0]['t'])})." if worst else ""
    verdict = f"{z['id']} {z['name']}: {res['alert_count']} alert{'s' * (res['alert_count'] != 1)} and {res['event_count']} events up to {hhmm(t)}.{head}"
    reasons = [f"{hhmm(e['t'])} {e['vessel_id']}: {e['summary']}" for e in res["events"][:3]]
    top_v = worst[0]["vessel_id"] if worst else (res["vessel_ids"] or [None])[0]
    vv = find_vessel(world, top_v)
    pts = [[e["lon"], e["lat"]] for e in res["events"] if e.get("lon") is not None]
    acts = [show_action(res["vessel_ids"], bbox=bbox_of(pts, 0.05), t=t)]
    if top_v:
        acts.append(report_action(top_v, z.get("purpose") if z.get("purpose") in ("cable", "sanctions", "insurance") else template_for(world, vv)))
    return {"verdict": verdict, "risk": risk_of(vv) if vv else None, "reasons": reasons,
            "sources": layer_sources(world, [top_v] if top_v else [], layers=("ais", "sar", "rf", "fusion", "zones")), "actions": acts}


def vessel_answer(world, rk, evs, t):
    if rk.get("error"):
        return {"verdict": rk["error"] + ".", "empty": True}
    v = rk["vessel"]
    vv = find_vessel(world, v["id"])
    if rk.get("score") is None and not evs.get("count"):
        return {"verdict": f"No risk score or events on record for {v['id']} up to {hhmm(t)}.", "empty": True,
                "reasons": [f"Data covered: {window_text(world)}."], "sources": layer_sources(world, [v["id"]])}
    verdict = f"{v['id']} {v.get('name')} ({v.get('flag')}, {v.get('type')}): risk {rk.get('score')} ({rk.get('level')}), {state_word(rk.get('state'))} at {hhmm(t)}, {evs['count']} events on record."
    reasons = [r.get("text") for r in sorted(rk.get("reasons") or [], key=lambda r: -(r.get("points") or 0))]
    if not reasons:
        reasons = [f"{hhmm(e['t'])}: {e['summary']}" for e in evs["events"][-3:]]
    p = position_at(world, vv, t)
    return {"verdict": verdict, "risk": risk_of(vv), "reasons": reasons, "sources": layer_sources(world, [v["id"]]),
            "actions": [show_action([v["id"]], bbox=p and bbox_of([[p["lon"], p["lat"]]], 0.05), t=t),
                        report_action(v["id"], template_for(world, vv))]}


def list_answer(world, res, t, state):
    vs = res["vessels"]
    if not vs:
        what = {"dark": "AIS-dark", "spoofing": "spoofing"}.get(state, "")
        return {"verdict": f"No {what} vessels at {when(t)}.".replace("No  ", "No "), "empty": True,
                "reasons": [f"Checked {len(world.get('vessels', []))} vessels' fused states at {hhmm(t)}.",
                            f"Data covered: {window_text(world)}."], "sources": layer_sources(world)}
    top = vs[0]
    if state:
        label = {"dark": "AIS dark (radar/RF only)", "spoofing": "broadcasting a false AIS position"}[state]
        verdict = f"{res['count']} vessel{'s' * (res['count'] != 1)} {label} at {hhmm(t)}: " + ", ".join(f"{v['id']} {v['name']}" for v in vs[:4]) + "."
    else:
        verdict = f"Riskiest vessel: {top['id']} {top['name']}, risk {(top['risk'] or {}).get('score')} ({(top['risk'] or {}).get('level')}), {state_word(top['state'])} at {hhmm(t)}."
    reasons = [f"{v['id']} {v['name']}: {(v['risk'] or {}).get('score')} — {v['top_reason'] or 'no flagged behaviour'}" for v in vs[:3]]
    pts = [[v["position"]["lon"], v["position"]["lat"]] for v in vs[:5] if v.get("position")]
    return {"verdict": verdict, "risk": top["risk"], "reasons": reasons, "sources": layer_sources(world, [top["id"]]),
            "actions": [show_action([v["id"] for v in vs[:5]], bbox=bbox_of(pts, 0.05), t=t),
                        report_action(top["id"], template_for(world, find_vessel(world, top["id"])))]}


def quote_answer(world, v, res, rk, t):
    a = res.get("assessment") or {}
    score, level = a.get("score"), str(a.get("level") or "")
    price = a.get("price") or {}
    voy = res.get("voyage") or {}
    route = a.get("route") or {}
    rlabel = route.get("label") or voy.get("label") or res.get("route")
    insurable = price.get("insurable", a.get("sdn") is None)

    def usd(x):
        return f"${x / 1e6:.2f}m" if x and x >= 1e6 else f"${x / 1e3:.0f}k" if x else "n/a"
    band = ""
    if price.get("lo_pct") is not None:
        band = f"{price['lo_pct']:.2f}–{price['hi_pct']:.2f}% of hull ({usd(price.get('lo_usd'))}–{usd(price.get('hi_usd'))}" + \
               (f", {price['blocks']}×7-day block" if price.get("blocks") else "") + ")"
    lu = level.upper()
    if not insurable:
        verdict = f"Do not quote {v['id']}: sanctions match on the war-risk check ({score}/100 {lu})."
    elif lu in ("SEVERE", "EXTREME"):
        verdict = f"Refer, don't auto-quote {v['id']} for {rlabel} on {res.get('as_of')}: war-risk {score}/100 {lu}" + (f"; indicative {band}." if band else ".")
    elif lu in ("HIGH",):
        verdict = f"Quote {v['id']} with conditions for {rlabel} on {res.get('as_of')}: war-risk {score}/100 {lu}" + (f", {band}." if band else ".")
    else:
        verdict = f"Quote {v['id']} for {rlabel} on {res.get('as_of')}: war-risk {score}/100 {lu}" + (f", {band}." if band else ".")
    factors = sorted(a.get("factors") or [], key=lambda f: -((f.get("value") or 0) * (f.get("weight") or 1)))
    reasons = [f"{f.get('label')}: {f.get('detail')}" for f in factors[:2]]
    if rk.get("score") is not None:
        top = (rk.get("reasons") or [{}])[0].get("text")
        reasons.append(f"Skynet behaviour record: risk {rk['score']} ({rk['level']})" + (f", {top}" if top else "") + ".")
    srcs = []
    for s in a.get("sources") or []:
        if isinstance(s, dict):
            srcs.append({"label": s.get("label") or s.get("name"), "url": s.get("url")})
        elif isinstance(s, str):
            srcs.append({"label": s, "url": None})
    if not srcs:
        srcs = WARRISK_SOURCES
    srcs = srcs + [x for x in layer_sources(world, [v["id"]]) if x not in srcs]
    lvl = {"LOW": "low", "ELEVATED": "medium"}.get(lu, "high")
    pts = [[p[1], p[0]] for p in a.get("track") or [] if len(p) >= 2]
    return {"verdict": verdict, "risk": {"score": score, "level": lvl, "label": lu}, "reasons": reasons,
            "sources": srcs,
            "actions": [show_action([v["id"]], bbox=bbox_of(pts, 0.5) if pts else None, t=t),
                        report_action(v["id"], "insurance")]}


# ---------------------------------------------------------------- LLM engine (only with a key)

SYSTEM = """You are Skynet's maritime analyst. Skynet compares what ships CLAIM (AIS) with what space OBSERVES
(SAR radar, RF). Answer the operator's question using ONLY the tools. Never invent positions, vessels, numbers
or sources. If tools return nothing for the requested time, say so plainly, set empty=true and state the
covered window. Current time t = {t} ({when}). Case: {case}. Scenario: {label}. Data window: {window}.
When done, call final_answer with a one-line verdict, 2-3 short reasons, risk (if relevant), the vessel ids
to show on the map, and which vessel/template (cable|sanctions|insurance) to report on."""

FINAL_SPEC = ("final_answer", "Return the answer to the operator.",
              {"verdict": {"type": "string"}, "reasons": {"type": "array", "items": {"type": "string"}},
               "risk_score": {"type": "number"}, "risk_level": {"type": "string"},
               "vessel_ids": {"type": "array", "items": {"type": "string"}},
               "report_vessel_id": {"type": "string"}, "report_template": {"type": "string", "enum": ["cable", "sanctions", "insurance"]},
               "empty": {"type": "boolean"}})


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


def _trim(res, n=6000):
    s = json.dumps(res, default=str)
    return s if len(s) <= n else s[:n] + '..."(truncated)"'


async def llm_answer(world, q, t, case):
    sc = world.get("scenario") or {}
    sys = SYSTEM.format(t=t, when=when(t), case=case or "cable", label=sc.get("label"), window=window_text(world))
    specs = TOOL_SPECS + [FINAL_SPEC]
    calls, raw = [], []
    akey, okey = os.environ.get("ANTHROPIC_API_KEY"), os.environ.get("OPENAI_API_KEY")
    final = None
    async with httpx.AsyncClient(timeout=15) as http:
        if akey:
            model = os.environ.get("SKYNET_ANALYST_MODEL", "claude-sonnet-4-5")
            tools = [{"name": n, "description": d, "input_schema": _schema(p, ["verdict"] if n == "final_answer" else ())} for n, d, p in specs]
            msgs = [{"role": "user", "content": q}]
            for _ in range(6):
                r = await http.post("https://api.anthropic.com/v1/messages",
                                    headers={"x-api-key": akey, "anthropic-version": "2023-06-01"},
                                    json={"model": model, "max_tokens": 1200, "system": sys, "tools": tools, "messages": msgs})
                r.raise_for_status()
                body = r.json()
                msgs.append({"role": "assistant", "content": body["content"]})
                uses = [c for c in body["content"] if c.get("type") == "tool_use"]
                if not uses:
                    txt = " ".join(c.get("text", "") for c in body["content"] if c.get("type") == "text").strip()
                    final = {"verdict": txt.split("\n")[0][:300]}
                    break
                results = []
                for u in uses:
                    if u["name"] == "final_answer":
                        final = u["input"]
                        continue
                    res = await run_tool(world, t, u["name"], u["input"]) if u["name"] in TOOLS else {"error": "unknown tool"}
                    raw.append((u["name"], res))
                    calls.append({"tool": u["name"], "args": u["input"], "summary": summarize(u["name"], res)})
                    results.append({"type": "tool_result", "tool_use_id": u["id"], "content": _trim(res)})
                if final:
                    break
                msgs.append({"role": "user", "content": results})
        else:
            model = os.environ.get("SKYNET_ANALYST_MODEL", "gpt-4o")
            tools = [{"type": "function", "function": {"name": n, "description": d, "parameters": _schema(p)}} for n, d, p in specs]
            msgs = [{"role": "system", "content": sys}, {"role": "user", "content": q}]
            for _ in range(6):
                r = await http.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {okey}"},
                                    json={"model": model, "messages": msgs, "tools": tools})
                r.raise_for_status()
                m = r.json()["choices"][0]["message"]
                msgs.append(m)
                if not m.get("tool_calls"):
                    final = {"verdict": (m.get("content") or "").split("\n")[0][:300]}
                    break
                for tc in m["tool_calls"]:
                    name, args = tc["function"]["name"], json.loads(tc["function"]["arguments"] or "{}")
                    if name == "final_answer":
                        final = args
                        res = {"ok": True}
                    else:
                        res = await run_tool(world, t, name, args) if name in TOOLS else {"error": "unknown tool"}
                        raw.append((name, res))
                        calls.append({"tool": name, "args": args, "summary": summarize(name, res)})
                    msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": _trim(res)})
                if final:
                    break
    if not final:
        raise RuntimeError("LLM did not finish")
    ids = [i for i in final.get("vessel_ids") or [] if find_vessel(world, i)]
    srcs = []
    for name, res in raw:
        if name == "assess_voyage" and isinstance(res, dict) and res.get("assessment"):
            a = res["assessment"]
            srcs += [s if isinstance(s, dict) else {"label": s, "url": None} for s in a.get("sources") or []] or WARRISK_SOURCES
    srcs += [s for s in layer_sources(world, ids) if s not in srcs]
    pts = [[p["lon"], p["lat"]] for p in (position_at(world, find_vessel(world, i), t) for i in ids) if p]
    acts = [show_action(ids, bbox=bbox_of(pts, 0.05), t=t)] if ids else []
    rv = final.get("report_vessel_id")
    if rv and find_vessel(world, rv):
        acts.append(report_action(rv, final.get("report_template") or template_for(world, find_vessel(world, rv))))
    risk = {"score": final["risk_score"], "level": final.get("risk_level")} if final.get("risk_score") is not None else None
    empty = bool(final.get("empty")) or (bool(raw) and all(isinstance(r, dict) and (r.get("empty") or r.get("error") or r.get("count") == 0) for _, r in raw))
    return answer(q, verdict=final.get("verdict", ""), reasons=final.get("reasons") or [], risk=risk, sources=srcs,
                  actions=acts, tool_calls=calls, empty=empty, engine=f"llm:{model}")


# ---------------------------------------------------------------- endpoint

async def ask(question: str, t: int | None = None, case: str | None = None) -> dict:
    world = load_world(case)
    tt = now_of(world, t)
    q = (question or "").strip()
    if not q:
        return answer(q, verdict="Ask a question about the vessels, assets or zones on the map.", empty=True)
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        try:
            return await llm_answer(world, q, tt, case)
        except Exception:
            pass  # fall back to the deterministic planner
    try:
        return await plan_and_answer(world, q, tt)
    except Exception as e:  # never 500 the ask bar; never guess either
        return answer(q, verdict="The analyst hit an error reading the data, so it has no answer.", empty=True,
                      reasons=[f"{type(e).__name__}: {e}"])


@router.post("/api/analyst")
async def analyst(body: Question, case: str | None = None):
    return await ask(body.question, body.t, body.case or case)
