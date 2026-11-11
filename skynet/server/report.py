"""Vessel record, fingerprint and report assembly (owner: B).

Everything here is pure: it reads a composed World (see CONTRACT.md) and returns dicts.
The record is the single source for alerts and reports: a report's fingerprint and record_url
always match GET /v1/vessels/{id}/record for the same period.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import datetime, timezone

TEMPLATES = {
    "cable": "Cable incident record",
    "sanctions": "Vessel sanctions due-diligence",
    "insurance": "Insurance voyage history / claims reconstruction",
}

SOURCE_LABELS = {
    "ais": "AIS",
    "sar": "Satellite radar",
    "rf": "RF",
    "operator": "Operator action",
    "fusion": "Fusion",
}

SEV_RANK = {"high": 3, "medium": 2, "low": 1}
VESSEL_KEYS = ("id", "mmsi", "imo", "name", "flag", "type", "length_m")
OVER_ASSET_KM = 1.0       # "over the asset" = verified position within 1 km of the asset line
NEAR_ASSET_KM = 25.0      # assets further than this are not drawn in non-cable exhibits
MAX_STEP_S = 600          # cap per-step duration when summing time (ignores long track gaps)


# ---------------------------------------------------------------- helpers

def fmt_t(t) -> str:
    if t is None:
        return "unknown time"
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def fmt_hm(t) -> str:
    return datetime.fromtimestamp(int(t), tz=timezone.utc).strftime("%H:%M UTC")


def fmt_minutes(m: float) -> str:
    m = int(round(m))
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h {r} min" if r else f"{h} h"


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(events: list) -> str:
    return "sha256:" + hashlib.sha256(canonical(events).encode("utf-8")).hexdigest()


def find_vessel(world: dict, vessel_id: str) -> dict | None:
    for v in world.get("vessels") or []:
        if str(v.get("id")) == str(vessel_id) or str(v.get("mmsi")) == str(vessel_id):
            return v
    return None


def default_period(world: dict, frm, to):
    sc = world.get("scenario") or {}
    frm = int(frm) if frm not in (None, "") else int(sc.get("start") or 0)
    to = int(to) if to not in (None, "") else int(sc.get("end") or time.time())
    return frm, to


def _overlaps(start, end, frm, to) -> bool:
    end = start if end is None else end
    return start is not None and start <= to and end >= frm


def _km_xy(lon, lat, lat0):
    return (lon * 111.320 * math.cos(math.radians(lat0)), lat * 110.574)


def _lines(geometry) -> list:
    if not geometry:
        return []
    if geometry.get("type") == "LineString":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiLineString":
        return geometry.get("coordinates") or []
    return []


def _seg_dist_km(p, a, b) -> float:
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    u = 0.0 if L == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + u * dx), py - (ay + u * dy))


def dist_to_asset_km(lon, lat, asset) -> float:
    best = math.inf
    for line in _lines(asset.get("geometry")):
        pts = [_km_xy(c[0], c[1], lat) for c in line]
        p = _km_xy(lon, lat, lat)
        if len(pts) == 1:
            best = min(best, math.hypot(p[0] - pts[0][0], p[1] - pts[0][1]))
        for a, b in zip(pts, pts[1:]):
            best = min(best, _seg_dist_km(p, a, b))
    return best


def _point_in_ring(lon, lat, ring) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x = x1 + (lat - y1) * (x2 - x1) / ((y2 - y1) or 1e-12)
            if lon < x:
                inside = not inside
    return inside


def _sum_time(track, pred) -> int:
    total = 0
    for a, b in zip(track, track[1:]):
        if pred(a):
            total += min(int(b[0]) - int(a[0]), MAX_STEP_S)
    return total


def _in_period(points, frm, to):
    return [p for p in points or [] if frm <= int(p[0]) <= to]


# ---------------------------------------------------------------- record

def build_record(world: dict, vessel_id: str, frm=None, to=None, base_url: str = "", case: str = "cable") -> dict | None:
    v = find_vessel(world, vessel_id)
    if v is None:
        return None
    frm, to = default_period(world, frm, to)
    vid = v["id"]
    events = [
        e for e in world.get("events") or []
        if (e.get("vessel_id") == vid or e.get("other_vessel_id") == vid)
        and _overlaps(e.get("t"), e.get("end"), frm, to)
    ]
    events.sort(key=lambda e: (int(e.get("t") or 0), str(e.get("id"))))
    track = _in_period(v.get("track"), frm, to)
    claimed = _in_period(v.get("claimed"), frm, to)
    q = f"vessel={vid}&case={case}"
    return {
        "vessel": {k: v.get(k) for k in VESSEL_KEYS},
        "from": frm,
        "to": to,
        "verified_track": [[p[0], p[1], p[2]] for p in track],
        "claimed_track": [[p[0], p[1], p[2]] for p in claimed],
        "dark_periods": [d for d in v.get("dark_periods") or [] if _overlaps(d.get("start"), d.get("end"), frm, to)],
        "events": events,
        "risk": v.get("risk"),
        "evidence_url": f"{base_url.rstrip('/')}/report.html?{q}&from={frm}&to={to}",
        "fingerprint": fingerprint(events),
    }


def record_url(vessel_id: str, frm: int, to: int, case: str = "cable") -> str:
    url = f"/v1/vessels/{vessel_id}/record?from={frm}&to={to}"
    return url if case == "cable" else f"{url}&case={case}"


# ---------------------------------------------------------------- report

def _pick_asset(world, v, events, track):
    assets = {a.get("id"): a for a in world.get("assets") or []}
    for e in events:
        if e.get("asset_id") in assets:
            return assets[e["asset_id"]]
    zones = {z.get("id"): z for z in world.get("zones") or []}
    for e in events:
        z = zones.get(e.get("zone_id"))
        if z and z.get("asset_id") in assets:
            return assets[z["asset_id"]]
    best, best_d = None, math.inf
    for a in assets.values():
        for p in track[:: max(1, len(track) // 60)] if track else []:
            d = dist_to_asset_km(p[1], p[2], a)
            if d < best_d:
                best, best_d = a, d
    return best if best_d <= NEAR_ASSET_KM else None


def _best_detection(v, frm, to, dark, asset):
    obs = [o for o in v.get("observed") or [] if o.get("t") is not None and frm <= int(o["t"]) <= to]
    if not obs:
        return None

    def in_dark(o):
        return any(d["start"] <= o["t"] <= d["end"] for d in dark)

    def key(o):
        d = dist_to_asset_km(o["lon"], o["lat"], asset) if asset else 0.0
        return (not in_dark(o), bool(o.get("matched_to_ais")), d, o["t"])

    return sorted(obs, key=key)[0]


def _timeline(events):
    rows = []
    for e in events:
        rows.append({
            "t": e.get("t"),
            "source": SOURCE_LABELS.get(e.get("source"), "Fusion"),
            "type": e.get("type"),
            "text": e.get("summary") or e.get("type", "").replace("_", " ").capitalize(),
            "event_id": e.get("id"),
            "severity": e.get("severity"),
        })
    return rows


def _cable_rows(v, asset, track, dark):
    if not asset:
        return [{"label": "Protected asset", "value": "No cable or pipeline within 25 km of the verified track"}]
    name = asset.get("alias") or asset.get("name") or asset.get("id")
    rows = [{"label": "Asset", "value": f"{name} ({asset.get('name')})" if asset.get("alias") else str(name)}]
    if track:
        dists = [(dist_to_asset_km(p[1], p[2], asset), p) for p in track]
        d, p = min(dists, key=lambda x: x[0])
        rows.append({"label": f"Closest approach to {name}", "value": f"{d:.2f} km at {fmt_t(p[0])}"})
        over = _sum_time(track, lambda q: dist_to_asset_km(q[1], q[2], asset) <= OVER_ASSET_KM)
        rows.append({"label": f"Time within {OVER_ASSET_KM:g} km of {name}", "value": fmt_minutes(over / 60)})
        dark_over = _sum_time(track, lambda q: dist_to_asset_km(q[1], q[2], asset) <= OVER_ASSET_KM
                              and any(x["start"] <= q[0] <= x["end"] for x in dark))
        rows.append({"label": "Of which with AIS off", "value": fmt_minutes(dark_over / 60)})
    else:
        rows.append({"label": f"Closest approach to {name}", "value": "No verified track in the period"})
    return rows


def _sanctions_rows(v, events, dark):
    dark_min = sum(d.get("minutes") or (d["end"] - d["start"]) / 60 for d in dark)
    sts = [e for e in events if e.get("type") == "ship_to_ship_meeting"]
    others = sorted({(e.get("other_vessel_id") if e.get("vessel_id") == v["id"] else e.get("vessel_id")) or "unknown" for e in sts})
    ids = v.get("identity_history") or []
    id_events = [e for e in events if e.get("type") == "identity_change"]
    changes = max(len(ids) - 1, 0, len(id_events))
    rows = [
        {"label": "Dark hours", "value": f"{dark_min / 60:.1f} h across {len(dark)} AIS gap{'s' if len(dark) != 1 else ''}"},
        {"label": "Ship-to-ship meetings", "value": f"{len(sts)}" + (f" (with {', '.join(others)})" if sts else "")},
        {"label": "Identity changes", "value": str(changes)},
    ]
    if len(ids) > 1:
        rows.append({"label": "Identities used", "value": "; ".join(
            f"{i.get('name')} / {i.get('flag')} / MMSI {i.get('mmsi')} from {fmt_t(i.get('t'))}" for i in ids)})
    spoof = [e for e in events if e.get("type") in ("spoofing", "position_mismatch")]
    if spoof:
        rows.append({"label": "False-position events", "value": str(len(spoof))})
    return rows


def _insurance_rows(world, v, track):
    rows = []
    voy = v.get("voyage")
    if voy:
        rows.append({"label": "Voyage", "value": f"{voy.get('label')}" + (f" via {voy['via']}" if voy.get("via") else "")
                     + (f", departs {voy['departs']}" if voy.get("departs") else "")})
        if voy.get("hull_usd_m") is not None:
            rows.append({"label": "Hull value", "value": f"USD {voy['hull_usd_m']} m"})
    else:
        rows.append({"label": "Voyage", "value": "No declared voyage on file"})
    listed = []
    for z in world.get("zones") or []:
        if z.get("purpose") != "insurance":
            continue
        g = z.get("geometry") or {}
        if g.get("type") == "polygon" and g.get("coordinates"):
            ring = g["coordinates"]
            secs = _sum_time(track, lambda q: _point_in_ring(q[1], q[2], ring))
            listed.append(f"{z.get('name')}: {fmt_minutes(secs / 60)}")
    rows.append({"label": "Listed-area exposure", "value": "; ".join(listed) if listed
                 else "No listed area is defined in this case"})
    risk = v.get("risk") or {}
    if risk:
        rows.append({"label": "Risk score", "value": f"{risk.get('score')}/100 ({risk.get('level')})"})
    return rows


def _summary(world, v, template, frm, to, events, dark, asset, track):
    s = []
    desc = ", ".join(str(x) for x in [v.get("type"), f"{v['flag']} flag" if v.get("flag") else None,
                                      f"{v['length_m']} m" if v.get("length_m") else None] if x)
    ident = " ".join(x for x in [f"MMSI {v['mmsi']}" if v.get("mmsi") else "", f"IMO {v['imo']}" if v.get("imo") else ""] if x)
    s.append(f"{v.get('name') or v['id']} ({desc}{'; ' + ident if ident else ''}) is recorded here from {fmt_t(frm)} to {fmt_t(to)}.")
    if dark:
        total = sum(d.get("minutes") or (d["end"] - d["start"]) / 60 for d in dark)
        longest = max(dark, key=lambda d: d["end"] - d["start"])
        s.append(f"AIS was silent for {fmt_minutes(total)} across {len(dark)} gap{'s' if len(dark) != 1 else ''}, "
                 f"the longest {fmt_minutes((longest['end'] - longest['start']) / 60)} from {fmt_hm(longest['start'])}.")
    else:
        s.append("No AIS gaps were recorded in the period.")
    sys_events = [e for e in events if e.get("type") != "operator_action"]
    if sys_events:
        top = max(sys_events, key=lambda e: (SEV_RANK.get(e.get("severity"), 0), -int(e.get("t") or 0)))
        lead = top.get("summary", "").rstrip(".")
        s.append(f"Skynet logged {len(sys_events)} event{'s' if len(sys_events) != 1 else ''}; the most severe, at "
                 f"{fmt_hm(top['t'])}: {lead}.")
    else:
        s.append("Skynet logged no events for this vessel in the period.")
    ops = [e for e in events if e.get("type") == "operator_action"]
    if template == "cable" and asset and track:
        d, p = min(((dist_to_asset_km(q[1], q[2], asset), q) for q in track), key=lambda x: x[0])
        s.append(f"Its closest verified approach to {asset.get('alias') or asset.get('name')} was {d:.2f} km at {fmt_hm(p[0])}"
                 + (f", and {len(ops)} operator action{'s were' if len(ops) != 1 else ' was'} logged." if ops else "."))
    elif template == "insurance" and v.get("risk"):
        r = v["risk"]
        s.append(f"Its current risk score is {r.get('score')}/100 ({r.get('level')})"
                 + (f" for the voyage {v['voyage']['label']}." if v.get("voyage") else "."))
    elif ops:
        s.append(f"{len(ops)} operator action{'s were' if len(ops) != 1 else ' was'} logged, the last by "
                 f"{ops[-1].get('by') or 'an operator'} at {fmt_hm(ops[-1]['t'])}.")
    return " ".join(s[:4])


def build_report(world: dict, vessel_id: str, template: str = "cable", frm=None, to=None,
                 requested_by: str | None = None, case: str = "cable", report_id: str = "R-0001",
                 base_url: str = "") -> dict | None:
    template = template if template in TEMPLATES else "cable"
    rec = build_record(world, vessel_id, frm, to, base_url=base_url, case=case)
    if rec is None:
        return None
    v = find_vessel(world, vessel_id)
    frm, to = rec["from"], rec["to"]
    events = rec["events"]
    dark = rec["dark_periods"]
    track = _in_period(v.get("track"), frm, to)
    asset = _pick_asset(world, v, events, track)
    det = _best_detection(v, frm, to, dark, asset)

    exhibits = []
    if det:
        bits = [f"Satellite radar detection {det.get('id')} at {fmt_t(det['t'])}"]
        if det.get("length_m"):
            bits.append(f"{det['length_m']} m hull")
        bits.append("not matched to any AIS broadcast" if not det.get("matched_to_ais") else "matched to this vessel's AIS")
        if asset:
            bits.append(f"{dist_to_asset_km(det['lon'], det['lat'], asset):.2f} km from {asset.get('alias') or asset.get('name')}")
        exhibits.append({"kind": "radar", "caption": ", ".join(bits) + ".", "detection": det, "image": None})
    dark_segs = []
    for d in dark:
        seg = [[p[1], p[2]] for p in track if d["start"] <= p[0] <= d["end"]]
        if seg:
            dark_segs.append(seg)
    claimed = [[p[1], p[2]] for p in rec["claimed_track"]]
    exhibits.append({
        "kind": "track",
        "caption": (f"Reconstructed track {fmt_hm(frm)}–{fmt_hm(to)}: AIS claimed ({len(claimed)} reports) vs verified "
                    f"({len(track)} fused positions); {len(dark_segs)} dark segment{'s' if len(dark_segs) != 1 else ''}"
                    + (f"; asset {asset.get('alias') or asset.get('name')}." if asset else ".")),
        "claimed": claimed,
        "verified": [[p[1], p[2]] for p in track],
        "dark": dark_segs,
        "asset": asset.get("geometry") if asset else None,
        "asset_name": (asset.get("alias") or asset.get("name")) if asset else None,
    })

    if template == "cable":
        section = {"title": "Cable exposure", "rows": _cable_rows(v, asset, track, dark)}
    elif template == "sanctions":
        section = {"title": "Sanctions indicators", "rows": _sanctions_rows(v, events, dark)}
    else:
        section = {"title": "Voyage and exposure", "rows": _insurance_rows(world, v, track)}

    sc = world.get("scenario") or {}
    return {
        "id": report_id,
        "case": case,
        "template": template,
        "title": TEMPLATES[template],
        "vessel": {**rec["vessel"], "illustrative": v.get("illustrative", sc.get("illustrative"))},
        "period": {"from": frm, "to": to},
        "requested_by": requested_by or "Skynet operator",
        "generated": int(time.time()),
        "summary": _summary(world, v, template, frm, to, events, dark, asset, track),
        "timeline": _timeline(events),
        "sections": [section],
        "exhibits": exhibits,
        "risk": v.get("risk"),
        "fingerprint": rec["fingerprint"],
        "record_url": record_url(v["id"], frm, to, case),
        "evidence_url": rec["evidence_url"],
        "scenario": {k: sc.get(k) for k in ("id", "label", "illustrative", "sources") if k in sc},
    }
