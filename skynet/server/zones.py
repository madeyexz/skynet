"""Watch zones: purpose presets, rule evaluation and metrics.

A zone is a corridor (buffered line), a polygon or a watchlist of vessel ids. `evaluate_zone` runs the zone's
enabled rules over the current world and returns events and alerts (ids prefixed with the zone id). It groups
rule hits per vessel, so one vessel gives one alert per zone (one moment, one decision).
"""
import time

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Polygon

from . import fusion as F

DAY = 86400
WINDOWS = {"1h": 3600, "6h": 6 * 3600, "24h": DAY, "30d": 30 * DAY}
SEV = {"low": 0, "medium": 1, "high": 2}


def _p(key, label, value, unit):
    return {"key": key, "label": label, "value": value, "unit": unit}


def _r(rid, label, severity, window=None, params=None, enabled=True):
    return {"id": rid, "label": label, "enabled": enabled, "severity": severity, "window": window,
            "params": params or []}


def _m(mid, label, unit, enabled=True):
    return {"id": mid, "label": label, "enabled": enabled, "unit": unit}


PRESETS = {
    "cable": {
        "label": "Cable / pipeline protection",
        "description": "Protect a seabed cable or pipeline: alert on ships that go dark, slow down, drag an anchor "
                       "or lie still over the asset.",
        "rules": [
            _r("dark_in_zone", "Tracker switched off inside zone", "high", None,
               [_p("min_minutes", "Longer than", 20, "min")]),
            _r("slow_speed", "Slow inside zone", "medium", None,
               [_p("min_kn", "Slower than", 3, "kn"), _p("min_minutes", "For longer than", 30, "min")]),
            _r("anchor_drag_pattern", "Anchor-drag pattern (slow, heading swings)", "high", None,
               [_p("max_kn", "Slower than", 3, "kn"), _p("min_minutes", "For longer than", 20, "min"),
                _p("min_swing", "Average heading swing above", 25, "°")]),
            _r("no_anchor_area", "Stopped or anchored outside an anchorage", "medium", None,
               [_p("min_minutes", "Longer than", 30, "min")]),
            _r("loitering", "Loitering near the asset", "medium", None,
               [_p("minutes", "Longer than", 60, "min"), _p("km", "Within", 2, "km")]),
            _r("false_position", "False position (AIS does not match radar)", "high", None,
               [_p("min_km", "Offset above", 2, "km")]),
        ],
        "metrics": [_m("vessels_in_zone", "Vessels currently in zone", "vessels"),
                    _m("dark_minutes_in_zone", "Dark minutes in zone", "min"),
                    _m("closest_approach_to_asset", "Closest approach to the asset", "km"),
                    _m("time_over_asset", "Time within 1 km of the asset", "min"),
                    _m("highest_risk_score", "Highest risk score in zone", "score"),
                    _m("alert_to_ack_time", "Alert to acknowledgement", "min")],
    },
    "sanctions": {
        "label": "Sanctions / dark fleet",
        "description": "Watch vessels or an area for dark periods, ship-to-ship transfers, identity changes and "
                       "designated owners.",
        "rules": [
            _r("dark_period", "Dark period", "high", "30d", [_p("hours", "Longer than", 6, "h")]),
            _r("ship_to_ship_meeting", "Ship-to-ship meeting at sea", "high", "30d",
               [_p("max_m", "Closer than", 500, "m"), _p("min_minutes", "For longer than", 40, "min")]),
            _r("identity_change", "Name, flag or MMSI change", "medium", "30d"),
            _r("spoofing", "Spoofed position", "high", "30d"),
            _r("flagged_port_call", "Call at a flagged port", "medium", "30d"),
            _r("designated_owner", "Owner on a sanctions list", "high", None),
        ],
        "metrics": [_m("dark_hours_30d", "Dark hours, last 30 days", "h"),
                    _m("sts_meeting_count", "Ship-to-ship meetings", "meetings"),
                    _m("identity_changes", "Identity changes", "changes"),
                    _m("risk_score_trend", "Risk score", "score")],
    },
    "insurance": {
        "label": "Insurance / war risk",
        "description": "Follow an insured voyage: listed-area entries, GNSS jamming exposure, incidents near the "
                       "route and dark periods during the policy.",
        "rules": [
            _r("listed_area_entry", "Entry into a listed area", "high", None),
            _r("jamming_exposure", "GNSS jamming exposure", "medium", None),
            _r("incidents_near_route", "Incidents near the route", "medium", None,
               [_p("km", "Within", 50, "km")]),
            _r("dark_during_policy", "Dark during the policy", "high", "30d",
               [_p("hours", "Longer than", 2, "h")]),
        ],
        "metrics": [_m("voyage_risk_score", "Voyage risk score", "score"),
                    _m("hours_in_high_risk_zones", "Hours in high-risk zones", "h"),
                    _m("insured_value_exposed", "Insured value exposed", "USD m"),
                    _m("jamming_events_on_route", "Jamming events on route", "events")],
    },
    "custom": {
        "label": "Custom",
        "description": "Start empty and build your own metrics, for example slow vessels in a rolling hour.",
        "rules": [],
        "metrics": [_m("vessels_in_zone", "Vessels currently in zone", "vessels"),
                    _m("highest_risk_score", "Highest risk score in zone", "score")],
    },
}

ACTIONS = {
    "cable": [{"id": "dispatch_patrol", "label": "Dispatch nearest patrol"},
              {"id": "forward_owner", "label": "Forward to cable owner"},
              {"id": "dismiss", "label": "Not suspicious"}],
    "spoofing": [{"id": "request_retask", "label": "Request radar re-task"},
                 {"id": "flag_registry", "label": "Flag to registry"},
                 {"id": "dismiss", "label": "Not suspicious"}],
    "sanctions": [{"id": "open_report", "label": "Open due-diligence report"},
                  {"id": "flag_registry", "label": "Flag to registry"},
                  {"id": "dismiss", "label": "Not suspicious"}],
    "insurance": [{"id": "notify_underwriter", "label": "Notify underwriter"},
                  {"id": "open_report", "label": "Open voyage report"},
                  {"id": "dismiss", "label": "Not suspicious"}],
}


# ---------------------------------------------------------------- geometry

_TF = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
_TI = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)


def _poly(zone):
    g = zone.get("geometry") or {}
    if g.get("type") == "corridor" and len(g.get("line") or []) >= 2:
        a = np.asarray(g["line"], float)
        x, y = _TF.transform(a[:, 0], a[:, 1])
        buf = LineString(np.column_stack([x, y])).buffer(float(g.get("width_km") or 4) * 500, quad_segs=4)
        bx, by = np.asarray(buf.exterior.coords).T
        lon, lat = _TI.transform(bx, by)
        return Polygon(np.column_stack([lon, lat]))
    if g.get("type") == "polygon" and len(g.get("coordinates") or []) >= 3:
        c = g["coordinates"]
        c = c[0] if isinstance(c[0][0], (list, tuple)) else c
        p = Polygon(c)
        return p if p.is_valid else p.buffer(0)
    return None


def zone_polygon(zone, world=None):
    p = _poly(zone)
    if p is None or p.is_empty:
        return None
    if p.geom_type != "Polygon":
        p = max(p.geoms, key=lambda q: q.area)
    return [[round(x, 5), round(y, 5)] for x, y in list(p.exterior.coords)[:-1]]


def _masks(ctx, zone):
    g = zone.get("geometry") or {}
    out = {}
    if g.get("type") == "watchlist":
        ids = set(g.get("vessel_ids") or [])
        for pv in ctx.vessels:
            if pv.id in ids:
                out[pv.id] = np.ones(len(pv.t), bool)
        return out
    p = _poly(zone)
    if p is None:
        return out
    shapely.prepare(p)
    for pv in ctx.vessels:
        if len(pv.t):
            m = shapely.contains_xy(p, pv.lon, pv.lat)
            if m.any():
                out[pv.id] = m
    return out


def _param(rule, key, default):
    for p in rule.get("params") or []:
        if p.get("key") == key:
            try:
                return float(p.get("value"))
            except (TypeError, ValueError):
                return default
    return default


def _asset_id(ctx, zone):
    aid = zone.get("asset_id")
    return aid if aid in ctx.asset_geom else None


# ---------------------------------------------------------------- rules -> hits
# A hit is (rule, event dict, fire_t, extra) where extra carries numbers for the alert facts.

def _rule_hits(ctx, zone, rule, pv, mask):
    rid, sev = rule["id"], rule.get("severity") or "medium"
    zid, aid = zone["id"], _asset_id(ctx, zone)
    alab = ctx.asset_label(aid) if aid else zone.get("name") or zid
    hits = []
    t, sog = pv.t, np.nan_to_num(pv.sog, nan=99)
    win = WINDOWS.get(rule.get("window") or "", None)
    since = ctx.end - win if win else -np.inf

    def ev(etype, t0, t1, k, summary, facts, source, **kw):
        return F._ev(pv, etype, t0, t1, pv.lon[k], pv.lat[k], sev, summary, facts, source, zone_id=zid,
                     asset_id=aid, **kw)

    if rid == "dark_in_zone":
        mn = _param(rule, "min_minutes", 20) * 60
        for i, j in F.runs(mask & pv.dark_mask):
            if t[j] - t[i] < mn:
                continue
            k = int(np.searchsorted(t, t[i] + mn))
            d = next((d for d in pv.dark if d["start"] <= t[i] <= d["end"] + 1), None)
            start = d["start"] if d else t[i]
            hits.append((rule, ev("dark_period", start, d["end"] if d and not d["ongoing"] else None, i,
                                  f"{pv.name} has had no AIS for {F.fmt_dur(t[k] - start)}, "
                                  f"{F.fmt_dur(t[k] - t[i])} of it inside {zone.get('name')}.",
                                  [f"AIS off since {F.fmt_t(start)}", f"Inside the zone since {F.fmt_t(t[i])}"], "ais"),
                         int(t[k]), {"dark_since": int(start)}))
    elif rid == "slow_speed":
        kn, mn = _param(rule, "min_kn", 3), _param(rule, "min_minutes", 30) * 60
        for i, j in F.runs(mask & (sog >= 0.5) & (sog < kn)):
            if t[j] - t[i] >= mn:
                k = int(np.searchsorted(t, t[i] + mn))
                hits.append((rule, ev("slow_speed", t[i], t[j], k,
                                      f"{pv.name} moved at {sog[i:j + 1].mean():.1f} kn inside {zone.get('name')} "
                                      f"for {F.fmt_dur(t[j] - t[i])}.",
                                      [f"Under {kn:g} kn {F.fmt_t(t[i])}–{F.fmt_t(t[j])}"], "fusion"),
                             int(t[k]), {"i": i, "j": j}))
    elif rid == "anchor_drag_pattern":
        if pv.type in F.SERVICE:
            return hits
        for i, j, fire in F.drag_runs(pv, max_kn=_param(rule, "max_kn", 3), min_s=_param(rule, "min_minutes", 20) * 60,
                                      min_swing=_param(rule, "min_swing", 25), mask=mask):
            sp = pv.sog[i:j + 1]
            n = F.crossings(pv, aid, i, j) if aid else 0
            facts = [f"Speed {sp.min():.1f}–{sp.max():.1f} kn for {F.fmt_dur(t[j] - t[i])}",
                     f"Heading swung up to {pv.swing[i + 1:j + 1].max():.0f}° between fixes",
                     f"Track crossed {alab} {n} time{'s' if n != 1 else ''}" if n else None]
            hits.append((rule, ev("anchor_drag_pattern", t[i], t[j], i,
                                  f"{pv.name} crept over {alab} with its heading swinging, the anchor-drag pattern "
                                  "seen in the Eagle S case.", facts, "fusion"), fire, {"i": i, "j": j}))
    elif rid == "no_anchor_area":
        mn = _param(rule, "min_minutes", 30) * 60
        for i, j in F.runs(mask & (sog < 0.5) & ~pv.in_anchorage):
            if t[j] - t[i] >= mn:
                k = int(np.searchsorted(t, t[i] + mn))
                hits.append((rule, ev("loitering", t[i], t[j], i,
                                      f"{pv.name} stopped inside {zone.get('name')}, outside any anchorage, for "
                                      f"{F.fmt_dur(t[j] - t[i])}.", [f"Stopped {F.fmt_t(t[i])}–{F.fmt_t(t[j])}"],
                                      "fusion"), int(t[k]), {"i": i, "j": j}))
    elif rid == "loitering":
        mn, km = _param(rule, "minutes", 60) * 60, _param(rule, "km", 2)
        near = pv.asset_dist(aid) < km * 1000 if aid else mask
        if pv.type in F.NO_LOITER:
            return hits
        for i, j in F.runs((mask | near) & near & (sog < 2) & ~pv.in_anchorage):
            if t[j] - t[i] >= mn:
                k = int(np.searchsorted(t, t[i] + mn))
                hits.append((rule, ev("loitering", t[i], t[j], i,
                                      f"{pv.name} idled under 2 kn within {km:g} km of {alab} for "
                                      f"{F.fmt_dur(t[j] - t[i])}.", [f"Under 2 kn {F.fmt_t(t[i])}–{F.fmt_t(t[j])}"],
                                      "fusion"), int(t[k]), {"i": i, "j": j}))
    elif rid in ("false_position", "spoofing", "jamming_exposure"):
        for i, j in pv.spoof_runs():
            if t[j] < since:
                continue
            inz = np.flatnonzero(mask[i:j + 1])
            if not len(inz):
                continue
            k = i + int(inz[0])
            facts, place = F.spoof_facts(ctx, pv, i, j)
            c = pv.claimed_at(t[i])
            e = F._ev(pv, "spoofing", t[i], None if j == len(t) - 1 else t[j + 1],
                      c[1] if c is not None else pv.lon[i], c[2] if c is not None else pv.lat[i], sev,
                      (f"{pv.name}'s AIS put it on land near {place} while radar saw it at sea"
                       if place else f"{pv.name}'s AIS position does not match where radar sees it")
                      + f" inside {zone.get('name')}.", facts[:4], "ais", zone_id=zid, asset_id=aid)
            hits.append((rule, e, int(max(t[k], t[i] + 240)), {"i": i, "j": j, "place": place}))
    elif rid in ("dark_period", "dark_during_policy"):
        hrs = _param(rule, "hours", 6 if rid == "dark_period" else 2)
        for d in pv.dark + pv.archive:
            if d["minutes"] < hrs * 60 or d["end"] < since:
                continue
            m = (t >= d["start"]) & (t <= d["end"])
            if len(t) and m.any() and not (mask & m).any() and zone["geometry"].get("type") != "watchlist":
                continue
            k = pv.idx(d["start"])
            e = F._ev(pv, "dark_period", d["start"], None if d.get("ongoing") else d["end"],
                      d.get("lon", pv.lon[k]), d.get("lat", pv.lat[k]), sev,
                      f"{pv.name} had its AIS off for {F.fmt_dur(d['minutes'] * 60)}.",
                      [f"AIS off {F.fmt_day(d['start'])} → {F.fmt_day(d['end'])}", d.get("note")], "ais",
                      zone_id=zid, archive=bool(d.get("note") or d.get("lon_end")))
            fire = d["start"] + int(hrs * 3600)
            hits.append((rule, e, int(max(fire, ctx.start)) if fire < ctx.start else int(fire), {}))
    elif rid == "ship_to_ship_meeting":
        for s in ctx.sts(_param(rule, "max_m", 500), _param(rule, "min_minutes", 40) * 60):
            if pv.id not in (s["a"], s["b"]):
                continue
            k = pv.idx(s["t0"])
            if zone["geometry"].get("type") != "watchlist" and not mask[k:pv.idx(s["t1"]) + 1].any():
                continue
            other = s["b"] if s["a"] == pv.id else s["a"]
            on = ctx.by_id[other].name
            e = F._ev(pv, "ship_to_ship_meeting", s["t0"], s["t1"], s["lon"], s["lat"], sev,
                      f"{pv.name} lay alongside {on} at sea for {F.fmt_dur(s['t1'] - s['t0'])}.",
                      [f"Alongside {on} ({other}) {F.fmt_t(s['t0'])}–{F.fmt_t(s['t1'])}",
                       f"Closest {s['dmin'] / 1000:.2f} km"], "fusion", zone_id=zid, other=other)
            hits.append((rule, e, int(s["t0"] + _param(rule, "min_minutes", 40) * 60), {}))
    elif rid == "identity_change":
        for e in F.identity_events(ctx, pv):
            if e["t"] >= since:
                e.update(zone_id=zid, severity=sev)
                hits.append((rule, e, int(max(e["t"], ctx.start)), {}))
    elif rid == "designated_owner":
        own = pv.v.get("owner") or {}
        if own.get("designated"):
            e = F._ev(pv, "identity_change", ctx.start, None, pv.lon[0], pv.lat[0], sev,
                      f"{pv.name}'s owner {own.get('name')} is on the {own.get('list', 'sanctions list')}.",
                      [f"Owner: {own.get('name')}", f"List: {own.get('list')}"], "fusion", zone_id=zid)
            e["source"] = "fusion"
            hits.append((rule, e, ctx.start, {}))
    elif rid == "flagged_port_call":
        for pc in pv.v.get("port_calls") or []:
            if pc.get("flagged") and pc["arrived"] >= since:
                k = pv.idx(pc["arrived"])
                hits.append((rule, ev("loitering", pc["arrived"], pc.get("departed"), k,
                                      f"{pv.name} called at {pc['port']}, a flagged port.", [], "ais"),
                             int(max(pc["arrived"], ctx.start)), {}))
    return hits


# ---------------------------------------------------------------- alerts

PRIORITY = ["anchor_drag_pattern", "dark_in_zone", "false_position", "spoofing", "ship_to_ship_meeting",
            "dark_period", "dark_during_policy", "designated_owner", "identity_change", "slow_speed",
            "no_anchor_area", "loitering", "flagged_port_call", "jamming_exposure", "custom"]


def _alert(ctx, zone, pv, hits, events):
    hits = sorted(hits, key=lambda h: PRIORITY.index(h[0]["id"]) if h[0]["id"] in PRIORITY else 99)
    lead = hits[0][0]["id"]
    fire = min(h[2] for h in hits if SEV.get(h[0].get("severity"), 1) >= 1)
    sev = max((h[0].get("severity") or "medium" for h in hits), key=lambda s: SEV.get(s, 1))
    aid = _asset_id(ctx, zone)
    alab = ctx.asset_label(aid) if aid else zone.get("name")
    k = pv.idx(fire)
    noun = F.noun(pv.v)
    ids = {h[0]["id"] for h in hits}
    facts = []
    if lead in ("anchor_drag_pattern", "dark_in_zone"):
        dark = next((h[3]["dark_since"] for h in hits if h[0]["id"] == "dark_in_zone"), None)
        if dark is None and pv.dark_mask[k]:
            dark = next((d["start"] for d in pv.dark if d["start"] <= fire), None)
        where = f"over {alab}" if aid else f"in {alab}"
        headline = (f"Dark {noun} dragging a likely anchor {where}" if "anchor_drag_pattern" in ids and dark
                    else f"{noun.capitalize()} dragging a likely anchor {where}" if "anchor_drag_pattern" in ids
                    else f"Dark {noun} inside {zone.get('name')}")
        if dark:
            facts.append(f"AIS silent {F.fmt_dur(fire - dark)} (off since {F.fmt_t(dark)})")
        sar = [o for o in pv.obs if o["t"] <= fire]
        if sar:
            o = sar[-1]
            ox, oy = ctx.xy(o["lon"], o["lat"])
            d = float(shapely.distance(shapely.Point(float(ox), float(oy)), ctx.asset_geom[aid])) / 1000 if aid else None
            facts.append(f"Radar: {o.get('length_m', '?')} m hull" + (f" {d:.1f} km from {alab}" if d is not None else "")
                         + f" at {F.fmt_t(o['t'])}" + (", no AIS match" if not o.get("matched_to_ais") else ""))
        rf = [o for o in pv.rf if o["t"] <= fire and (not dark or o["t"] >= dark)]
        if rf:
            facts.append(f"Nav radar still emitting ({len(rf)} X-band hit{'s' if len(rf) != 1 else ''}"
                         f"{' since AIS went off' if dark else ''})")
        a = max(0, k - 15)
        sp = pv.sog[a:k + 1]
        facts.append(f"Speed {np.nanmedian(sp):.1f} kn, heading swings {pv.swing[a + 1:k + 1].max():.0f}°")
        actions = ACTIONS["cable"]
    elif lead in ("false_position", "spoofing"):
        h = next(h for h in hits if h[0]["id"] == lead)
        place = h[3].get("place")
        headline = (f"AIS puts {pv.name} on land near {place}; radar sees it at sea" if place
                    else f"{pv.name} broadcasting a false position")
        c = pv.claimed_at(fire)
        off = pv.offset_m[k] if np.isfinite(pv.offset_m[k]) else np.nanmax(pv.offset_m)
        facts.append(f"AIS vs radar: {off / 1000:.1f} km apart" + (", AIS on land" if place else ""))
        facts += [f for f in h[1]["facts"] if "jumped" in f or "crop circle" in f][:2]
        if aid:
            facts.append(f"Real position {pv.asset_dist(aid)[k] / 1000:.1f} km from {alab}")
        actions = ACTIONS["spoofing"]
    else:
        h = hits[0]
        headline = {"slow_speed": f"Slow {noun} drifting in {zone.get('name')}",
                    "no_anchor_area": f"{noun.capitalize()} stopped in {zone.get('name')}",
                    "loitering": f"{noun.capitalize()} loitering near {alab}",
                    "ship_to_ship_meeting": f"{pv.name} met another ship at sea",
                    "dark_period": f"{pv.name} went dark for hours",
                    "dark_during_policy": f"{pv.name} went dark during the policy",
                    "designated_owner": f"{pv.name}'s owner is designated",
                    "identity_change": f"{pv.name} changed identity"}.get(lead, f"{pv.name} needs a look")
        facts.append(h[1]["summary"].rstrip("."))
        for hh in hits[1:3]:
            facts.append(hh[1]["summary"].rstrip("."))
        if aid and len(pv.t):
            facts.append(f"{pv.asset_dist(aid)[k] / 1000:.1f} km from {alab} at {F.fmt_t(fire)}")
        facts.append(f"AIS {'on and matching radar' if pv.state[k] == 0 else 'off' if pv.state[k] == 1 else 'false'}")
        actions = ACTIONS.get(zone.get("purpose"), ACTIONS["cable"])
    return {"id": None, "vessel_id": pv.id, "event_ids": [h[1]["id"] for h in hits], "zone_id": zone["id"],
            "t": int(fire), "severity": sev, "headline": headline, "facts": [f for f in facts if f][:4],
            "actions": actions,
            "recipients": [{"name": r.get("name"), "channel": r.get("channel", "in_app")}
                           for r in zone.get("recipients") or []] or [{"name": "Duty officer", "channel": "in_app"}],
            "status": "open"}


def evaluate_with(ctx, zone):
    zid = zone.get("id") or "Z"
    masks = _masks(ctx, zone)
    events, alerts = [], []
    for pv in ctx.vessels:
        if pv.id not in masks:
            continue
        hits = []
        for rule in zone.get("rules") or []:
            if rule.get("enabled", True):
                try:
                    hits += _rule_hits(ctx, zone, rule, pv, masks[pv.id])
                except Exception:
                    continue
        for h in hits:
            events.append(h[1])
            h[1]["id"] = f"{zid}-E{len(events):03d}"
        if any(SEV.get(h[0].get("severity"), 1) >= 1 for h in hits):
            alerts.append(_alert(ctx, zone, pv, hits, events))
    # Custom metrics with an alert threshold.
    for m in zone.get("metrics") or []:
        cu = m.get("custom")
        if not (m.get("enabled", True) and cu and cu.get("alert_above") is not None):
            continue
        try:
            series = _custom_series(ctx, zone, masks, cu)
        except Exception:
            continue
        over = [(t, v) for t, v in series if v > float(cu["alert_above"])]
        if over:
            t, v = over[0]
            alerts.append({"id": None, "vessel_id": None, "event_ids": [], "zone_id": zid, "t": int(t),
                           "severity": "medium", "headline": f"{m.get('label', 'Custom metric')} above {cu['alert_above']}",
                           "facts": [f"{m.get('label')}: {v:g} at {F.fmt_t(t)}",
                                     f"Threshold {cu['alert_above']}, window {cu.get('window') or 'now'}"],
                           "actions": ACTIONS.get(zone.get("purpose"), ACTIONS["cable"]),
                           "recipients": [{"name": r.get("name"), "channel": r.get("channel", "in_app")}
                                          for r in zone.get("recipients") or []], "status": "open"})
    alerts.sort(key=lambda a: a["t"])
    for n, a in enumerate(alerts, 1):
        a["id"] = f"{zid}-A{n:03d}"
    return {"events": events, "alerts": alerts}


def evaluate_zone(world, zone):
    return evaluate_with(F.Ctx(world), zone)


# ---------------------------------------------------------------- metrics

def _signal(ctx, zone, pv, mask, sig, param):
    if sig == "speed_below_kn":
        return mask & (np.nan_to_num(pv.sog, nan=99) < float(param or 3))
    if sig == "dark_minutes":
        return np.where(mask & pv.dark_mask, F.STEP / 60, 0.0)
    if sig == "risk_score":
        return np.where(mask, pv.risk_score(), 0.0)
    if sig == "distance_to_asset_km":
        aid = _asset_id(ctx, zone)
        d = pv.asset_dist(aid) / 1000 if aid else np.full(len(pv.t), np.inf)
        return np.where(mask, d, np.inf)
    return mask.astype(float)   # in_zone


def _custom_value(ctx, zone, masks, cu, t):
    win = WINDOWS.get(cu.get("window") or "", 0)
    agg, sig = cu.get("aggregation") or "count", cu.get("signal") or "in_zone"
    vals = []
    for pv in ctx.vessels:
        if pv.id not in masks:
            continue
        sel = (pv.t <= t) & (pv.t >= t - win)
        s = np.asarray(_signal(ctx, zone, pv, masks[pv.id], sig, cu.get("param")), float)[sel]
        inz = masks[pv.id][sel]
        if not inz.any():
            continue
        if sig == "distance_to_asset_km":
            vals.append(float(s.min()))
        elif sig == "dark_minutes":
            vals.append(float(s.sum()))
        elif sig == "risk_score":
            vals.append(float(s.max()))
        else:
            vals.append(float(s.any()))
    if not vals:
        return 0.0
    if agg == "count":
        return float(sum(1 for v in vals if v > 0 and np.isfinite(v)))
    if agg == "sum":
        return float(sum(v for v in vals if np.isfinite(v)))
    if agg == "max":
        return float(max(vals))
    return float(np.mean([v for v in vals if np.isfinite(v)] or [0]))


def _custom_series(ctx, zone, masks, cu):
    return [(int(t), _custom_value(ctx, zone, masks, cu, t)) for t in range(ctx.start, ctx.end + 1, 600)]


def zone_metrics(world, zone, t):
    ctx = F.Ctx(world)
    t = int(t or (world.get("scenario") or {}).get("now") or ctx.end)
    masks = _masks(ctx, zone)
    aid = _asset_id(ctx, zone)
    out = []
    for m in zone.get("metrics") or []:
        if not m.get("enabled", True):
            continue
        mid, value, unit, detail = m.get("id"), None, m.get("unit", ""), ""
        try:
            if m.get("custom"):
                value = round(_custom_value(ctx, zone, masks, m["custom"], t), 1)
                detail = f"{m['custom'].get('aggregation', 'count')} over {m['custom'].get('window') or 'now'}"
            elif mid == "vessels_in_zone":
                ids = [pv.id for pv in ctx.vessels if pv.id in masks and masks[pv.id][pv.idx(t)] and pv.t[0] <= t]
                value, unit, detail = len(ids), "vessels", ", ".join(ids[:6])
            elif mid == "dark_minutes_in_zone":
                value = int(sum(((pv.t <= t) & masks[pv.id] & pv.dark_mask).sum() * 2 for pv in ctx.vessels
                                if pv.id in masks))
                unit = "min"
            elif mid in ("closest_approach_to_asset", "time_over_asset"):
                best, who, over = np.inf, None, 0
                for pv in ctx.vessels:
                    if pv.id not in masks or not aid:
                        continue
                    d = pv.asset_dist(aid)[pv.t <= t]
                    if len(d) and d.min() < best:
                        best, who = float(d.min()), pv.id
                    over += int((d < 1000).sum()) * 2
                if mid == "closest_approach_to_asset":
                    value, unit, detail = (round(best / 1000, 2) if np.isfinite(best) else None), "km", who or ""
                else:
                    value, unit = over, "min"
            elif mid in ("highest_risk_score", "risk_score_trend", "voyage_risk_score"):
                rs = [(F.risk_at(pv.v.get("risk"), t), pv.id) for pv in ctx.vessels if pv.id in masks
                      and (mid != "voyage_risk_score" or pv.v.get("voyage"))]
                value, detail = (max(rs)[0], max(rs)[1]) if rs else (0, "")
                unit = "score"
            elif mid == "alert_to_ack_time":
                al = {a["id"]: a for a in world.get("alerts") or [] if a.get("zone_id") == zone.get("id")}
                ds = [(x["t"] - al[x["alert_id"]]["t"]) / 60 for x in world.get("actions") or []
                      if x.get("alert_id") in al and x.get("t") is not None]
                value, unit, detail = (round(float(np.mean(ds)), 1) if ds else None), "min", f"{len(ds)} acknowledged"
            elif mid == "dark_hours_30d":
                value = round(sum(d["minutes"] for pv in ctx.vessels if pv.id in masks
                                  for d in pv.dark + pv.archive if d["start"] >= t - 30 * DAY and d["start"] <= t) / 60, 1)
                unit = "h"
            elif mid == "sts_meeting_count":
                value = sum(1 for s in ctx.sts() if (s["a"] in masks or s["b"] in masks) and s["t0"] <= t)
                unit = "meetings"
            elif mid == "identity_changes":
                value = sum(max(0, len([h for h in pv.v.get("identity_history") or [] if h["t"] >= t - 365 * DAY
                                        and h["t"] <= t])) for pv in ctx.vessels if pv.id in masks
                            and len(pv.v.get("identity_history") or []) > 1)
                unit = "changes"
            elif mid == "insured_value_exposed":
                value = sum((pv.v.get("voyage") or {}).get("hull_usd_m") or 0 for pv in ctx.vessels if pv.id in masks)
                unit = "USD m"
            elif mid == "jamming_events_on_route":
                value = sum(len(pv.spoof_runs()) for pv in ctx.vessels if pv.id in masks)
                unit = "events"
            elif mid == "hours_in_high_risk_zones":
                value, unit, detail = 0, "h", "no listed area in the seeded world"
        except Exception as exc:  # never break the side sheet
            value, detail = None, f"unavailable ({type(exc).__name__})"
        out.append({"id": mid, "label": m.get("label", mid), "value": value, "unit": unit, "detail": detail})
    return out
