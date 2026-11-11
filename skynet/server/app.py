"""Skynet FastAPI app: static web/, UI API (/api/*) and Data API (/v1/*) (owner: B).

Every endpoint takes `?case=` (default `cable`). State is per case (see server/store.py).
"""
from __future__ import annotations

import asyncio
import copy
import hmac
import ipaddress
import os
import shutil
import socket
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import BackgroundTasks, Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from server import report as rpt
from server import store

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
API_KEY = os.environ.get("SKYNET_API_KEY", "sk_live_skynet_demo")  # public /v1 read key (printed in the docs)
ADMIN_KEY = os.environ.get("SKYNET_ADMIN_KEY")  # when set, write routes need `x-admin-key`; unset = open (local dev)
ALLOW_PRIVATE_WEBHOOKS = os.environ.get("SKYNET_ALLOW_PRIVATE_WEBHOOKS") == "1"  # local testing only


def _find_chrome() -> str:
    if os.environ.get("SKYNET_CHROME"):
        return os.environ["SKYNET_CHROME"]
    fallback = "/home/user/.agent-browser/browsers/chrome-157.0.8103.38/chrome"
    for c in (shutil.which("chromium"), shutil.which("chromium-browser"), shutil.which("google-chrome"), fallback):
        if c and Path(c).exists():
            return c
    return fallback


CHROME = _find_chrome()
DEFAULT_CREATOR = "Ian (demo)"
SEV_RANK = {"low": 1, "medium": 2, "high": 3}
ACTION_LABELS = {"dispatch_patrol": "Dispatch nearest patrol", "forward_owner": "Forward to cable owner",
                 "dismiss": "Not suspicious", "watch": "Watch this vessel", "escalate": "Escalate"}

app = FastAPI(title="Skynet", description="Air traffic control and a black box, for the sea.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["Authorization", "x-api-key", "Content-Type"])


@app.exception_handler(store.CaseUnavailable)
async def _case_unavailable(_req: Request, exc: store.CaseUnavailable):
    return JSONResponse({"detail": str(exc), "error": "case_unavailable"}, status_code=404)


class AdminKeyRequired(Exception):
    pass


@app.exception_handler(AdminKeyRequired)
async def _admin_key_required(_req: Request, _exc: AdminKeyRequired):
    return JSONResponse({"error": "admin_key_required",
                         "detail": "This action needs the Skynet admin key. Send it as the 'x-admin-key' header."},
                        status_code=401)


def _admin(x_admin_key: str | None) -> None:
    """Write routes: require `x-admin-key` when SKYNET_ADMIN_KEY is set; open when it is unset (local dev)."""
    if ADMIN_KEY and not hmac.compare_digest((x_admin_key or "").encode(), ADMIN_KEY.encode()):
        raise AdminKeyRequired()


# ---------------------------------------------------------------- helpers

def _case(case: str | None) -> str:
    return (case or store.DEFAULT_CASE).strip() or store.DEFAULT_CASE


def _scn(world: dict) -> dict:
    return world.get("scenario") or {}


def _now(world: dict) -> int:
    sc = _scn(world)
    return int(sc.get("now") or sc.get("end") or time.time())


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def _self_url(request: Request) -> str:
    """Loopback URL of this server process (for headless Chrome)."""
    server = request.scope.get("server") or ("127.0.0.1", 8787)
    return f"http://127.0.0.1:{server[1] or 8787}"


def _position_at(v: dict, t: int):
    tr = v.get("track") or v.get("claimed") or []
    if not tr:
        o = (v.get("observed") or [None])[0]
        return (o["lon"], o["lat"]) if o else (None, None)
    if t <= tr[0][0]:
        return tr[0][1], tr[0][2]
    for a, b in zip(tr, tr[1:]):
        if a[0] <= t <= b[0]:
            f = 0 if b[0] == a[0] else (t - a[0]) / (b[0] - a[0])
            return a[1] + f * (b[1] - a[1]), a[2] + f * (b[2] - a[2])
    return tr[-1][1], tr[-1][2]


def _zones_mod():
    from server import zones  # owner A
    return zones


def _evaluate(case: str, zone: dict) -> dict:
    """Evaluate a zone against the case world (without that zone's previous results)."""
    w = store.world(case, exclude_zone=zone["id"])
    w["zones"] = [z for z in w["zones"] if z.get("id") != zone["id"]] + [zone]
    out = {"events": [], "alerts": [], "metrics": []}
    try:
        zm = _zones_mod()
        res = zm.evaluate_zone(w, zone) or {}
        out["events"] = list(res.get("events") or [])
        out["alerts"] = list(res.get("alerts") or [])
    except Exception as exc:
        out["warning"] = f"Zone saved, but rule evaluation is unavailable: {exc}"
        return out
    for a in out["alerts"]:
        a.setdefault("zone_id", zone["id"])
        a.setdefault("status", "open")
        if not a.get("recipients"):
            a["recipients"] = [{"name": r.get("name"), "channel": r.get("channel")} for r in zone.get("recipients") or []
                               if SEV_RANK.get(a.get("severity"), 0) >= SEV_RANK.get(r.get("min_severity") or "low", 1)]
    for e in out["events"]:
        e.setdefault("zone_id", zone["id"])
    return out


def _metrics(case: str, zone: dict, t: int | None = None) -> list:
    w = store.world(case)
    try:
        return _zones_mod().zone_metrics(w, zone, int(t) if t is not None else _now(w)) or []
    except Exception as exc:
        print(f"[app] zone_metrics failed: {exc!r}")
        return []


def _save_zone(case: str, zone: dict, background: BackgroundTasks) -> dict:
    res = _evaluate(case, zone)
    new_alerts, alerts = store.put_zone(case, zone, res)
    for a in new_alerts:
        background.add_task(fire_webhooks, case, a)
    out = {"zone": zone, "events": res["events"], "alerts": alerts, "metrics": _metrics(case, zone)}
    if res.get("warning"):
        out["warning"] = res["warning"]
    return out


def _create_zone(case: str, body: dict, background: BackgroundTasks) -> dict:
    w = store.world(case)
    zone = copy.deepcopy(body or {})
    if not zone.get("id") or store.find(w["zones"], zone["id"]):
        zone["id"] = store.new_zone_id(case)
    zone.setdefault("name", f"Watch zone {zone['id']}")
    zone.setdefault("purpose", "custom")
    zone.setdefault("asset_id", None)
    zone.setdefault("rules", [])
    zone.setdefault("metrics", [])
    zone.setdefault("recipients", [])
    zone.setdefault("created", _now(w))
    zone.setdefault("created_by", DEFAULT_CREATOR)
    if not zone.get("geometry"):
        raise HTTPException(422, "Zone needs a geometry (corridor, polygon or watchlist).")
    return _save_zone(case, zone, background)


def _blocked_ip(addr: str) -> bool:
    """True for any address that is not a public unicast host (private, loopback, link-local, multicast, ...)."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or getattr(ip, "is_site_local", False) or not ip.is_global)


async def _webhook_url_error(url: str) -> str | None:
    """Why `url` may not be used as a webhook target (SSRF guard), or None if it is fine.
    Checked at registration and again right before every send, since DNS can change."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return "Webhook URL is not a valid URL."
    if parts.scheme not in ("http", "https"):
        return "Webhook URL must use http or https."
    host = (parts.hostname or "").rstrip(".").lower()
    if not host:
        return "Webhook URL needs a hostname."
    if ALLOW_PRIVATE_WEBHOOKS:
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return "Webhook URL must be a public host, not localhost."
    try:
        ipaddress.ip_address(host)
        literal = True
    except ValueError:
        literal = False
    if literal and _blocked_ip(host):
        return f"Webhook URL must be a public host; {host} is a private or reserved address."
    try:
        infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(
            host, port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM), timeout=5)
    except asyncio.TimeoutError:
        return f"Webhook host '{host}' could not be resolved (DNS timeout)."
    except (OSError, UnicodeError) as exc:
        return f"Webhook host '{host}' could not be resolved: {exc}"
    addrs = {i[4][0] for i in infos}
    if not addrs:
        return f"Webhook host '{host}' could not be resolved."
    bad = sorted(a for a in addrs if _blocked_ip(a))
    if bad:
        return f"Webhook URL must be a public host; '{host}' resolves to a private or reserved address ({bad[0]})."
    return None


async def fire_webhooks(case: str, alert: dict) -> None:
    """alert.created: POST to every registered URL, best effort (2 s timeout), and log each attempt."""
    hooks = store.webhooks()
    if not hooks:
        return
    payload = {"type": "alert.created", "case": case, "data": alert}
    async with httpx.AsyncClient(timeout=2.0, follow_redirects=False) as client:
        for h in hooks:
            t0 = time.time()
            entry = {"id": store.next_id("webhook_delivery", "WHD-"), "webhook_id": h["id"], "url": h["url"],
                     "type": "alert.created", "case": case, "alert_id": alert.get("id"), "t": int(t0)}
            blocked = await _webhook_url_error(h["url"])
            if blocked:
                entry.update(status_code=None, ok=False, error=f"Blocked: {blocked}"[:300], ms=0)
                store.log_webhook(entry)
                print(f"[webhook] alert.created {alert.get('id')} -> {h['url']} blocked: {blocked}")
                continue
            try:
                r = await client.post(h["url"], json=payload, headers={"User-Agent": "Skynet-Webhooks/1.0"})
                entry.update(status_code=r.status_code, ok=r.is_success, error=None)
            except Exception as exc:
                entry.update(status_code=None, ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
            entry["ms"] = int((time.time() - t0) * 1000)
            store.log_webhook(entry)
            print(f"[webhook] alert.created {alert.get('id')} -> {h['url']} {entry.get('status_code') or entry['error']}")


def _record(request: Request, case: str, vessel_id: str, frm, to) -> dict:
    w = store.world(case)
    rec = rpt.build_record(w, vessel_id, frm, to, base_url=_base_url(request), case=case)
    if rec is None:
        raise HTTPException(404, f"Vessel '{vessel_id}' not found in case '{case}'.")
    return rec


# ---------------------------------------------------------------- UI API

@app.get("/api/world")
def get_world(case: str | None = None):
    return store.world(_case(case))


@app.get("/api/presets")
def presets():
    try:
        return _zones_mod().PRESETS
    except Exception as exc:
        raise HTTPException(503, f"Presets unavailable: {exc}")


@app.post("/api/zones")
def create_zone(background: BackgroundTasks, body: dict = Body(...), case: str | None = None,
                x_admin_key: str | None = Header(None)):
    _admin(x_admin_key)
    return _create_zone(_case(case), body, background)


@app.put("/api/zones/{zone_id}")
def update_zone(zone_id: str, background: BackgroundTasks, body: dict = Body(...), case: str | None = None,
                x_admin_key: str | None = Header(None)):
    _admin(x_admin_key)
    case = _case(case)
    old = store.find(store.world(case)["zones"], zone_id)
    if old is None:
        raise HTTPException(404, f"Zone '{zone_id}' not found.")
    zone = {**old, **copy.deepcopy(body or {}), "id": zone_id}
    return _save_zone(case, zone, background)


@app.delete("/api/zones/{zone_id}")
def delete_zone(zone_id: str, case: str | None = None, x_admin_key: str | None = Header(None)):
    _admin(x_admin_key)
    if not store.delete_zone(_case(case), zone_id):
        raise HTTPException(404, f"Zone '{zone_id}' not found.")
    return {"ok": True}


@app.get("/api/zones/{zone_id}/summary")
def zone_summary(zone_id: str, t: int | None = None, case: str | None = None):
    case = _case(case)
    w = store.world(case)
    zone = store.find(w["zones"], zone_id)
    if zone is None:
        raise HTTPException(404, f"Zone '{zone_id}' not found.")
    return {"zone": zone, "metrics": _metrics(case, zone, t),
            "events": [e for e in w["events"] if e.get("zone_id") == zone_id],
            "alerts": [a for a in w["alerts"] if a.get("zone_id") == zone_id]}


@app.post("/api/alerts/{alert_id}/actions")
def alert_action(alert_id: str, body: dict = Body(...), case: str | None = None):
    case = _case(case)
    w = store.world(case)
    alert = store.find(w["alerts"], alert_id)
    if alert is None:
        raise HTTPException(404, f"Alert '{alert_id}' not found.")
    act_id = str((body or {}).get("action") or "").strip()
    if not act_id:
        raise HTTPException(422, "Body needs an 'action' id.")
    label = next((a.get("label") for a in alert.get("actions") or [] if a.get("id") == act_id), None) \
        or ACTION_LABELS.get(act_id) or act_id.replace("_", " ").capitalize()
    sc = _scn(w)
    t = body.get("t")
    if t is None:
        t = max(int(alert.get("t") or 0), _now(w))
        if sc.get("end"):
            t = min(t, int(sc["end"]))
    t = int(t)
    by = body.get("by") or "Duty officer"
    note = body.get("note") or ""
    n = store.next_id(f"action:{case}", "", 4)
    action = {"id": f"ACT-{n}", "alert_id": alert_id, "vessel_id": alert.get("vessel_id"), "action": act_id,
              "label": label, "by": by, "t": t, "note": note}
    v = rpt.find_vessel(w, alert.get("vessel_id")) or {}
    lon, lat = _position_at(v, t) if v else (None, None)
    status = "dismissed" if act_id == "dismiss" else "actioned"
    facts = [f"Alert {alert_id}: {alert.get('headline')}", f"Alert status set to {status}"]
    if note:
        facts.append(f"Note: {note}")
    event = {"id": f"E-OP-{n}", "vessel_id": alert.get("vessel_id"), "type": "operator_action", "t": t, "end": None,
             "lon": lon, "lat": lat, "severity": "low", "summary": f"{by}: {label}.", "facts": facts,
             "source": "operator", "zone_id": alert.get("zone_id"), "asset_id": None, "other_vessel_id": None,
             "alert_id": alert_id, "action_id": action["id"], "action": act_id, "by": by}
    store.add_action(case, action, event, status)
    return action


def _preview(alert: dict, channel: str, vessel: dict | None) -> dict:
    sev = (alert.get("severity") or "").capitalize()
    name = (vessel or {}).get("name")
    who = f"{alert.get('vessel_id')}" + (f" {name}" if name else "")
    facts = alert.get("facts") or []
    if channel == "push":
        return {"title": f"{sev} alert · {who}", "body": alert.get("headline") or ""}
    if channel == "email":
        body = (alert.get("headline") or "") + "\n\n" + "\n".join(f"• {f}" for f in facts)
        body += f"\n\nOpen the record: /report.html?vessel={alert.get('vessel_id')}"
        return {"title": f"[Skynet] {sev}: {alert.get('headline')}", "body": body}
    if channel == "webhook":
        return {"title": "POST alert.created", "body": f'{{"type":"alert.created","data":{{"id":"{alert.get("id")}",'
                                                         f'"vessel_id":"{alert.get("vessel_id")}","severity":"{alert.get("severity")}"}}}}'}
    return {"title": alert.get("headline") or "", "body": " · ".join(facts[:3])}


@app.get("/api/deliveries")
def deliveries(alert_id: str | None = None, case: str | None = None):
    """Simulated notification deliveries: one per alert recipient, a few seconds after the alert fires."""
    w = store.world(_case(case))
    zones = {z.get("id"): z for z in w["zones"]}
    out = []
    for a in w["alerts"]:
        if alert_id and a.get("id") != alert_id:
            continue
        recips = a.get("recipients") or [
            r for r in (zones.get(a.get("zone_id")) or {}).get("recipients") or []
            if SEV_RANK.get(a.get("severity"), 0) >= SEV_RANK.get(r.get("min_severity") or "low", 1)]
        v = rpt.find_vessel(w, a.get("vessel_id"))
        for i, r in enumerate(recips):
            ch = r.get("channel") or "in_app"
            out.append({"id": f"DL-{a.get('id')}-{i + 1}", "alert_id": a.get("id"), "recipient": r.get("name"),
                        "channel": ch, "address": r.get("address") or None, "status": "delivered",
                        "t": int(a.get("t") or 0) + 2 + 3 * i, "preview": _preview(a, ch, v)})
    return out


@app.get("/api/vessels/{vessel_id}/record")
def ui_record(request: Request, vessel_id: str, frm: int | None = Query(None, alias="from"),
              to: int | None = None, case: str | None = None):
    return _record(request, _case(case), vessel_id, frm, to)


@app.post("/api/reports")
def create_report(request: Request, body: dict = Body(...), case: str | None = None):
    case = _case(case or body.get("case"))
    vid = body.get("vessel_id")
    if not vid:
        raise HTTPException(422, "Body needs 'vessel_id'.")
    w = store.world(case)
    if rpt.find_vessel(w, vid) is None:
        raise HTTPException(404, f"Vessel '{vid}' not found in case '{case}'.")
    rid = store.next_id("report", "R-")
    rep = rpt.build_report(w, vid, body.get("template") or "cable", body.get("from"), body.get("to"),
                           body.get("requested_by"), case=case, report_id=rid, base_url=_base_url(request))
    store.add_report(rep)
    return rep


@app.get("/api/reports")
def list_reports(case: str | None = None):
    return [{k: r.get(k) for k in ("id", "case", "template", "title", "vessel", "period", "generated", "fingerprint")}
            for r in store.list_reports(_case(case))]


async def _print_pdf(url: str, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f"{out.stem}.{uuid.uuid4().hex}.tmp.pdf")  # unique per render, so concurrent requests don't clash
    profile = tempfile.mkdtemp(prefix="skynet-chrome-")
    try:
        proc = await asyncio.create_subprocess_exec(
            CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-pdf-header-footer",
            "--hide-scrollbars", "--virtual-time-budget=8000", f"--user-data-dir={profile}",
            f"--print-to-pdf={tmp}", url,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise HTTPException(504, "PDF rendering timed out.")
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise HTTPException(500, f"PDF rendering failed: {err.decode(errors='replace')[-400:]}")
        tmp.replace(out)
    finally:
        shutil.rmtree(profile, ignore_errors=True)
        tmp.unlink(missing_ok=True)


@app.get("/api/reports/{report_id}.pdf")
async def report_pdf(request: Request, report_id: str, case: str | None = None, fresh: bool = False):
    rep = store.get_report(report_id)
    if rep is None:
        raise HTTPException(404, f"Report '{report_id}' not found.")
    rcase = rep.get("case") or _case(case)
    out = store.REPORTS_DIR / f"{report_id}.pdf"
    if fresh or not out.exists():
        url = f"{_self_url(request)}/report.html?id={report_id}&case={rcase}&print=1"
        await _print_pdf(url, out)
    vid = (rep.get("vessel") or {}).get("id") or "vessel"
    return FileResponse(out, media_type="application/pdf", filename=f"Skynet-{report_id}-{vid}.pdf")


@app.get("/api/reports/{report_id}")
def get_report(report_id: str, case: str | None = None):
    rep = store.get_report(report_id)
    if rep is None:
        raise HTTPException(404, f"Report '{report_id}' not found.")
    return rep


@app.post("/api/reset")
def reset(x_admin_key: str | None = Header(None)):
    _admin(x_admin_key)
    store.reset()
    store.base_world(store.DEFAULT_CASE)
    return {"ok": True}


# ---------------------------------------------------------------- Data API (/v1)

def _auth(authorization: str | None, x_api_key: str | None) -> None:
    token = (x_api_key or "").strip()
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if token != API_KEY:
        raise HTTPException(401, "Missing or invalid API key. Send 'Authorization: Bearer <key>' or 'x-api-key: <key>'.",
                            headers={"WWW-Authenticate": "Bearer"})


@app.get("/v1/vessels/{vessel_id}/record")
def v1_record(request: Request, vessel_id: str, frm: int | None = Query(None, alias="from"), to: int | None = None,
              case: str | None = None, authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    return _record(request, _case(case), vessel_id, frm, to)


@app.get("/v1/vessels/{vessel_id}/risk")
def v1_risk(vessel_id: str, case: str | None = None, authorization: str | None = Header(None),
            x_api_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    w = store.world(_case(case))
    v = rpt.find_vessel(w, vessel_id)
    if v is None:
        raise HTTPException(404, f"Vessel '{vessel_id}' not found.")
    r = v.get("risk") or {}
    return {"vessel_id": v["id"], "score": r.get("score"), "level": r.get("level"), "reasons": r.get("reasons") or [],
            "as_of": _now(w)}


@app.get("/v1/zones/{zone_id}/events")
def v1_zone_events(zone_id: str, since: int | None = None, case: str | None = None,
                   authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    w = store.world(_case(case))
    if store.find(w["zones"], zone_id) is None:
        raise HTTPException(404, f"Zone '{zone_id}' not found.")
    evs = [e for e in w["events"] if e.get("zone_id") == zone_id and (since is None or int(e.get("t") or 0) >= since)]
    return {"zone_id": zone_id, "since": since, "events": evs}


@app.post("/v1/zones")
def v1_create_zone(background: BackgroundTasks, body: dict = Body(...), case: str | None = None,
                   authorization: str | None = Header(None), x_api_key: str | None = Header(None),
                   x_admin_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    _admin(x_admin_key)
    return _create_zone(_case(case), body, background)


@app.post("/v1/webhooks")
async def v1_add_webhook(body: dict = Body(...), authorization: str | None = Header(None),
                         x_api_key: str | None = Header(None), x_admin_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    _admin(x_admin_key)
    url = str((body or {}).get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(422, "Body needs an http(s) 'url'.")
    problem = await _webhook_url_error(url)
    if problem:
        raise HTTPException(422, problem)
    return store.add_webhook(url)


@app.get("/v1/webhooks")
def v1_list_webhooks(authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    return store.webhooks()


@app.get("/v1/webhooks/deliveries")
def v1_webhook_deliveries(authorization: str | None = Header(None), x_api_key: str | None = Header(None)):
    _auth(authorization, x_api_key)
    return store.webhook_log()


# ---------------------------------------------------------------- other owners' routers

try:
    from server.real.routes import router as real_router  # owner C
    app.include_router(real_router)
except Exception as exc:
    print(f"[app] server.real.routes not loaded: {exc!r}")

try:
    from server.analyst import router as analyst_router  # owner I
    app.include_router(analyst_router)
except Exception as exc:
    print(f"[app] server.analyst not loaded: {exc!r}")


# ---------------------------------------------------------------- static web/ (last, so APIs win)

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
