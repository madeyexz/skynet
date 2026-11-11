"""Real-data use cases for Skynet. Each one is a full World in the CONTRACT schema.

- baltic-2024  cable-watch replay: Danish Maritime Authority AIS 17–19 Nov 2024, BCS East-West and C-Lion1 cuts
- dark-skagen  orbit-analyst: two real Sentinel-1 passes off Skagen on 6 Nov 2026, matched to DMA AIS
- war-risk     war-risk-copilot: JMIC incidents, JWC listed area, live PortWatch / gpsjam / OFAC SDN (built at runtime)
- taiwan       cable-watch: Global Fishing Watch loitering events and Sentinel-1 detections with no AIS
- migration    orbit-abundance: Sentinel-1 strip detections in the Libya → Crete corridor, revisit and latency

Real numbers come from the source demos' data files and are labelled with their source. Events, risk and alerts
are computed from that data with the demos' own rules. Nothing about the real ships is invented. The one made-up
ship is T-11 in war-risk, which is marked illustrative.

Static worlds are prebuilt into data/real/<case>.json (and images into data/real/media/) with
    uv run python -m server.real.cases build
get_world() loads them lazily, or builds them from the demo folders if a file is missing.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, box, shape

ROOT = Path(__file__).resolve().parents[2]       # skynet/
REPO = ROOT.parent
REAL = ROOT / "data" / "real"
MEDIA = REAL / "media"
CW = REPO / "cable-watch" / "data"
OA = REPO / "orbit-analyst"
AB = REPO / "orbit-abundance" / "web" / "data"
MEDIA_URL = "/api/real/media/"

CASES = [
    {"id": "cable", "label": "Cable protection · Gulf of Finland", "purpose": "cable", "data": "seeded",
     "source": "Seeded scenario inspired by Estlink 2 (illustrative)",
     "description": "A dark tanker drags an anchor over a power cable, a spoofer, a sanctioned tanker. Illustrative."},
    {"id": "baltic-2024", "label": "Cable cuts · western Baltic, Nov 2024", "purpose": "cable", "data": "real",
     "source": "Danish Maritime Authority AIS + cable-watch scoring, TeleGeography cables, OpenSanctions",
     "description": "17–19 Nov 2024 replay around the BCS East-West and C-Lion1 cuts: Yi Peng 3 and the ships that "
                    "stopped, crawled or went silent over a cable."},
    {"id": "dark-skagen", "label": "Dark vessels · Skagen, 6 Nov 2026", "purpose": "sanctions", "data": "real",
     "source": "Sentinel-1C radar (Microsoft Planetary Computer) matched to Danish Maritime Authority AIS",
     "description": "Two radar passes off Skagen. Radar targets that match an AIS position are verified; "
                    "the rest are dark targets."},
    {"id": "war-risk", "label": "War risk · Hormuz transit for T-11", "purpose": "insurance", "data": "real",
     "source": "JMIC advisories, JWC listed areas, IMF PortWatch, gpsjam, OFAC SDN",
     "description": "Should we quote tanker T-11 (illustrative) for Ras Tanura → Kaohsiung via Hormuz? "
                    "Risk score, price band and the incidents near the route."},
]
STATIC_CASES = ("baltic-2024", "dark-skagen")


# ---------------------------------------------------------------- helpers

def r5(x):
    return None if x is None else round(float(x), 5)


def hav_m(lon1, lat1, lon2, lat2):
    la1, la2 = math.radians(lat1), math.radians(lat2)
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.asin(math.sqrt(h))


def bearing(lon1, lat1, lon2, lat2):
    la1, la2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def epoch(s: str) -> int:
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def day_epoch(s: str) -> int:
    return int(datetime.fromisoformat(s[:10]).replace(tzinfo=timezone.utc).timestamp())


def utc(t, fmt="%-d %b %H:%M UTC"):
    return datetime.fromtimestamp(t, timezone.utc).strftime(fmt)


def make_track(pts, max_pts=400):
    """pts: [(t, lon, lat, sog|None)] sorted. Returns [[t, lon, lat, sog, cog]] with cog from the next fix."""
    pts = [p for p in pts if p[1] is not None and p[2] is not None]
    if len(pts) > max_pts:
        step = math.ceil(len(pts) / max_pts)
        pts = pts[::step] + ([pts[-1]] if (len(pts) - 1) % step else [])
    out = []
    for i, (t, lon, lat, sog) in enumerate(pts):
        j = i + 1 if i + 1 < len(pts) else i - 1
        cog = None
        if j >= 0 and (pts[j][1], pts[j][2]) != (lon, lat):
            a, b = (pts[i], pts[j]) if j > i else (pts[j], pts[i])
            cog = round(bearing(a[1], a[2], b[1], b[2]))
        out.append([int(t), r5(lon), r5(lat), None if sog is None else round(float(sog), 1), cog])
    return out


def states_for(start, end, dark_periods, base="verified"):
    out, cur = [], start
    for d in sorted(dark_periods, key=lambda d: d["start"]):
        if d["start"] > cur:
            out.append({"start": cur, "end": d["start"], "state": base})
        out.append({"start": max(cur, d["start"]), "end": d["end"], "state": "dark"})
        cur = max(cur, d["end"])
    if cur < end:
        out.append({"start": cur, "end": end, "state": base})
    return out


def level_of(score):
    return "high" if score >= 60 else "medium" if score >= 40 else "low"


def sev_of(points):
    return "high" if points >= 35 else "medium" if points >= 25 else "low"


def vessel(**kw):
    v = {"id": None, "mmsi": None, "imo": None, "name": "", "flag": None, "type": "unknown", "length_m": None,
         "illustrative": False, "track": [], "claimed": [], "observed": [], "emitted": [], "states": [],
         "dark_periods": [], "identity_history": [], "risk": {"score": 0, "level": "low", "reasons": []},
         "voyage": None}
    v.update(kw)
    if not v["identity_history"] and (v["name"] or v["mmsi"]):
        first = v["track"] or v["claimed"]
        t0 = first[0][0] if first else (v["observed"][0]["t"] if v["observed"] else None)
        v["identity_history"] = [{"t": t0, "name": v["name"], "flag": v["flag"], "mmsi": v["mmsi"]}]
    return v


def lines_of(geom):
    if geom["type"] == "LineString":
        return [geom["coordinates"]]
    if geom["type"] == "MultiLineString":
        return geom["coordinates"]
    return []


def round_geom(geom):
    if geom["type"] == "LineString":
        return {"type": "LineString", "coordinates": [[r5(a), r5(b)] for a, b, *_ in geom["coordinates"]]}
    return {"type": "MultiLineString",
            "coordinates": [[[r5(a), r5(b)] for a, b, *_ in ln] for ln in geom["coordinates"]]}


def alias_of(name):
    m = re.search(r"\(([^)]+)\)\s*$", name)
    return m.group(1) if m else name


def _aeqd(lon0, lat0):
    crs = f"+proj=aeqd +lat_0={lat0} +lon_0={lon0} +units=m +ellps=WGS84"
    return (Transformer.from_crs("EPSG:4326", crs, always_xy=True),
            Transformer.from_crs(crs, "EPSG:4326", always_xy=True))


def buffer_rings(lines, km, lon0, lat0, clip=None, max_parts=3, simplify_m=400):
    """Buffer lon/lat polylines by `km` on each side; return up to max_parts rings [[lon, lat], ...] (not closed)."""
    fwd, inv = _aeqd(lon0, lat0)
    geoms = []
    for ln in lines:
        g = LineString([(a, b) for a, b, *_ in ln])
        if clip is not None:
            g = g.intersection(box(*clip))
        for part in shapely.get_parts(g):
            if part.geom_type == "LineString" and len(part.coords) > 1:
                xy = np.array(part.coords)
                x, y = fwd.transform(xy[:, 0], xy[:, 1])
                geoms.append(LineString(np.column_stack([x, y])).buffer(km * 1000))
    if not geoms:
        return []
    u = shapely.union_all(geoms).simplify(simplify_m)
    parts = sorted(shapely.get_parts(u), key=lambda p: -p.area)[:max_parts]
    out = []
    for p in parts:
        xy = np.array(p.exterior.coords)[:-1]
        lon, lat = inv.transform(xy[:, 0], xy[:, 1])
        out.append([[r5(a), r5(b)] for a, b in zip(lon, lat)])
    return out


FALLBACK_RULES = {
    "cable": [
        {"id": "dark_in_zone", "label": "Tracker switched off inside zone", "enabled": True, "severity": "high",
         "window": None, "params": [{"key": "min_minutes", "label": "Longer than", "value": 45, "unit": "min"}]},
        {"id": "slow_speed", "label": "Slow over the asset", "enabled": True, "severity": "medium", "window": None,
         "params": [{"key": "min_kn", "label": "Below", "value": 5, "unit": "kn"},
                    {"key": "min_minutes", "label": "For", "value": 20, "unit": "min"}]},
        {"id": "anchor_drag_pattern", "label": "Anchor-drag pattern", "enabled": True, "severity": "high",
         "window": None, "params": []},
        {"id": "loitering", "label": "Stopped near the asset", "enabled": True, "severity": "medium", "window": None,
         "params": [{"key": "minutes", "label": "Longer than", "value": 30, "unit": "min"},
                    {"key": "km", "label": "Within", "value": 1, "unit": "km"}]},
        {"id": "false_position", "label": "AIS position does not match radar", "enabled": True,
         "severity": "high", "window": None, "params": []}],
    "sanctions": [
        {"id": "dark_period", "label": "AIS off longer than", "enabled": True, "severity": "medium", "window": "30d",
         "params": [{"key": "hours", "label": "Longer than", "value": 2, "unit": "h"}]},
        {"id": "ship_to_ship_meeting", "label": "Ship-to-ship meeting", "enabled": True, "severity": "high",
         "window": None, "params": []},
        {"id": "identity_change", "label": "Identity change", "enabled": True, "severity": "medium",
         "window": None, "params": []},
        {"id": "spoofing", "label": "Spoofed position", "enabled": True, "severity": "high", "window": None,
         "params": []}],
    "insurance": [
        {"id": "listed_area_entry", "label": "Enters a JWC listed area", "enabled": True, "severity": "high",
         "window": None, "params": []},
        {"id": "jamming_exposure", "label": "GNSS jamming on route", "enabled": True, "severity": "medium",
         "window": None, "params": []},
        {"id": "incidents_near_route", "label": "Incidents near route", "enabled": True, "severity": "high",
         "window": "30d", "params": [{"key": "km", "label": "Within", "value": 46, "unit": "km"}]},
        {"id": "dark_during_policy", "label": "AIS off during the policy", "enabled": True, "severity": "medium",
         "window": None, "params": []}],
    "custom": [],
}
FALLBACK_METRICS = {
    "cable": ["vessels_in_zone", "dark_minutes_in_zone", "closest_approach_to_asset", "highest_risk_score"],
    "sanctions": ["dark_hours_30d", "sts_meeting_count", "identity_changes", "risk_score_trend"],
    "insurance": ["voyage_risk_score", "hours_in_high_risk_zones", "insured_value_exposed", "jamming_events_on_route"],
    "custom": ["vessels_in_zone"],
}


def make_zone(zid, name, purpose, geometry, asset_id=None, recipients=None, created=None):
    return {"id": zid, "name": name, "purpose": purpose, "geometry": geometry, "asset_id": asset_id,
            "rules": json.loads(json.dumps(FALLBACK_RULES[purpose])),
            "metrics": [{"id": m, "label": m.replace("_", " ").capitalize(), "enabled": True}
                        for m in FALLBACK_METRICS[purpose]],
            "recipients": recipients or [{"name": "Duty officer", "channel": "in_app", "address": "",
                                          "min_severity": "medium", "quiet_hours": None}],
            "created": created, "created_by": "Skynet (real case)", "preset": True}


def apply_presets(world):
    """Use A's purpose presets (server.zones.PRESETS) for the case zones when they are importable."""
    try:
        from server.zones import PRESETS  # owner A
    except Exception:
        return world
    for z in world.get("zones", []):
        p = PRESETS.get(z.get("purpose")) if z.get("preset") else None
        if p:
            z["rules"] = json.loads(json.dumps(p.get("rules", z["rules"])))
            if p.get("metrics"):
                z["metrics"] = [{"id": m["id"], "label": m["label"], "enabled": m.get("enabled", True)}
                                for m in p["metrics"]]
    return world


def bbox_of(points, pad=0.15, q=(1, 99)):
    a = np.array(points, float)
    lo = np.percentile(a, q[0], axis=0)
    hi = np.percentile(a, q[1], axis=0)
    return [r5(lo[0] - pad), r5(lo[1] - pad), r5(hi[0] + pad), r5(hi[1] + pad)]


def magick_jpg(src: Path, name: str, width: int | None = None, quality=80) -> str | None:
    """Downscale and recompress an image into data/real/media with ImageMagick. Returns the served URL."""
    MEDIA.mkdir(parents=True, exist_ok=True)
    dst = MEDIA / name
    if not src.exists():
        return None
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        cmd = ["magick", str(src)] + (["-resize", f"{width}x"] if width else []) + \
              ["-strip", "-quality", str(quality), str(dst)]
        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        except Exception:
            import shutil
            dst = MEDIA / (Path(name).stem + src.suffix)
            shutil.copyfile(src, dst)
    return MEDIA_URL + dst.name


class Ids:
    def __init__(self):
        self.n = {}

    def __call__(self, prefix, width=4):
        self.n[prefix] = self.n.get(prefix, 0) + 1
        return f"{prefix}-{self.n[prefix]:0{width}d}"


# ---------------------------------------------------------------- cable-watch reasons, Chinese → English

_TAGS = {"制裁名單": "sanctions list", "影子船隊": "shadow fleet", "曾被港口國扣留": "detained by a port state"}
_FLAGS = {"多哥": "Togo", "喀麥隆": "Cameroon", "坦尚尼亞": "Tanzania"}
_FISHING = "（漁船，可能是拖網作業；拖網也會弄壞海纜，但多半不是蓄意）"
CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")


def _within(d):
    return "within 1 km of" if d is None else f"within {d} km of"


def translate(reason: str, kind: str, cable: str | None):
    """Translate one cable-watch signal reason. Returns (text, info) with the parsed numbers."""
    cab = cable or "a cable"
    info = {}
    fishing = _FISHING in reason
    clauses = [c for c in reason.replace(_FISHING, "").split("；") if c]
    out = []
    for c in clauses:
        m = re.fullmatch(r"在海纜(?:上方 1| ([\d.]+)) 公里內停留或下錨 ([\d.]+) 小時(?:（平均 ([\d.]+) 節）)?", c)
        if m:
            info.update(h=m[2], d=m[1])
            out.append(f"Stopped or at anchor {_within(m[1])} {cab} for {m[2]} h"
                       + (f" (average {m[3]} kn)" if m[3] else ""))
            continue
        m = re.fullmatch(r"在海纜上方 1 公里內以平均 ([\d.]+) 節低速航行 (\d+) 分鐘", c)
        if m:
            info.update(kn=m[1], m=m[2])
            out.append(f"Moved at {m[1]} kn on average {_within(None)} {cab} for {m[2]} min")
            continue
        m = re.fullmatch(r"在海纜(?:上方 1| ([\d.]+)) 公里內以平均 ([\d.]+) 節低速徘徊 ([\d.]+) 小時", c)
        if m:
            info.update(kn=m[2], h=m[3], d=m[1])
            out.append(f"Loitered at {m[2]} kn on average {_within(m[1])} {cab} for {m[3]} h")
            continue
        m = re.fullmatch(r"在海纜 2 公里內反覆掉頭 (\d+) 次（Z 字形航線）", c)
        if m:
            info.update(n=m[1])
            out.append(f"Reversed course {m[1]} times within 2 km of {cab} (zigzag)")
            continue
        m = re.fullmatch(r"航行中速度從平常的 ([\d.]+) 節掉到 ([\d.]+) 節，持續 (\d+) 分鐘，期間經過海纜 3 公里內（拖錨時會出現這種減速）", c)
        if m:
            info.update(kn0=m[1], kn=m[2], m=m[3])
            out.append(f"Speed fell from its usual {m[1]} kn to {m[2]} kn for {m[3]} min while passing within 3 km "
                       f"of {cab} (the slowdown seen when an anchor drags)")
            continue
        m = re.fullmatch(r"AIS 訊號中斷 ([\d.]+) 小時，(.+)", c)
        if m:
            info.update(h=m[1])
            rest = m[2]
            x = re.fullmatch(r"中斷前後的位置連線跨過 (.+)", rest)
            if x:
                info["crossed"] = x[1]
                out.append(f"AIS silent for {m[1]} h; the line between the last and the next fix crosses {x[1]}")
            elif rest == "中斷點離海纜 10 公里內":
                out.append(f"AIS silent for {m[1]} h, starting or ending within 10 km of a cable")
            else:
                return None, info
            continue
        m = re.fullmatch(r"中斷期間平均只走 ([\d.]+) 節（中斷前 ([\d.]+) 節），表示船在中斷時減速或停下", c)
        if m:
            out.append(f"it covered only {m[1]} kn on average while silent (vs {m[2]} kn before), so it slowed "
                       f"or stopped")
            continue
        if c == "但同一時間附近 10 公里內也沒有別的船有訊號，比較可能是岸台收不到（收訊死角）":
            info["coverage_hole"] = True
            out.append("but no other ship within 10 km was heard either, so a hole in shore-receiver coverage "
                       "is more likely")
            continue
        m = re.fullmatch(r"同一時間附近 10 公里內有 (\d+) 艘船照常發訊號，所以不是收訊死角", c)
        if m:
            info["others"] = m[1]
            out.append(f"{m[1]} other ship{'s' if m[1] != '1' else ''} within 10 km kept transmitting, so it was "
                       f"not a coverage hole")
            continue
        m = re.fullmatch(r"但(?:同一時間 |附近 )5 公里內還有 (\d+) 艘船也停著，比較像是錨地", c)
        if m:
            info["anchorage"] = m[1]
            out.append(f"but {m[1]} other ships were stopped within 5 km too, so this looks like an anchorage")
            continue
        m = re.fullmatch(r"停留期間的活動範圍和 (.+) 重疊", c)
        if m:
            out.append(f"the area it covered overlaps {m[1]}")
            continue
        m = re.fullmatch(r"OpenSanctions 列為：(.+)（(.+)）", c)
        if m:
            tags = [_TAGS.get(t, t) for t in m[1].split("、")]
            info["tags"] = tags
            out.append(f"OpenSanctions lists {m[2]}: {', '.join(tags)}")
            continue
        m = re.fullmatch(r"船旗是(.+)：和宏泰58（多哥）、順興39（喀麥隆、坦尚尼亞）同一類權宜船旗", c)
        if m:
            out.append(f"Flag of {_FLAGS.get(m[1], m[1])}: the same flag-of-convenience group as Hong Tai 58 "
                       f"(Togo) and Shunxin-39 (Cameroon, Tanzania)")
            continue
        return None, info
    text = "; ".join(out)
    if fishing:
        text += " (fishing vessel: probably trawling, which can also damage cables, mostly not deliberately)"
    if CJK.search(text):
        return None, info
    return text[:1].upper() + text[1:], info


RULE_LABEL = {"stopped": "stopped or at anchor over a cable", "slow": "slow over a cable", "zigzag": "zigzag",
              "speed_drop": "speed drop near a cable", "ais_gap": "AIS gap near a cable",
              "watchlist": "OpenSanctions watchlist", "flag": "flag of convenience"}
EVENT_TYPE = {"stopped": "loitering", "slow": "slow_speed", "zigzag": "loitering", "speed_drop": "anchor_drag_pattern",
              "ais_gap": "dark_period"}
FIRE_AFTER = {"stopped": 30 * 60, "slow": 20 * 60, "speed_drop": 30 * 60}


def en_reason(s):
    text, info = translate(s["reason"], s["kind"], s.get("cable"))
    if text is None:
        text = f"cable-watch signal: {RULE_LABEL.get(s['kind'], s['kind'])}" + (f" near {s['cable']}" if s.get("cable") else "")
        text = text[:1].upper() + text[1:]
    return text, info


def fire_t(s):
    k = s["kind"]
    return int(min(s["end"], s["start"] + FIRE_AFTER[k])) if k in FIRE_AFTER else int(s["end"])


def headline(name, s, info, sanctioned=False):
    cab = s.get("cable") or "a cable"
    who = (f"Sanctioned tanker {name}" if sanctioned else name) or "Unnamed ship"
    k = s["kind"]
    if k == "stopped":
        return f"{who} stopped {info.get('h', '?')} h over {cab}"
    if k == "slow":
        if "m" in info:
            return f"{who} crawled at {info.get('kn')} kn over {cab} for {info['m']} min"
        return f"{who} loitered at {info.get('kn')} kn over {cab} for {info.get('h')} h"
    if k == "zigzag":
        return f"{who} reversed course {info.get('n', '')} times over {cab}"
    if k == "speed_drop":
        return f"{who} slowed from {info.get('kn0')} to {info.get('kn')} kn near {cab}"
    if k == "ais_gap":
        where = f"across {info['crossed']}" if info.get("crossed") else "near a cable"
        return f"{who} went AIS-silent {info.get('h')} h {where}"
    return f"{who} flagged near {cab}"


def signal_world_parts(vs, ids, corridor_cables, cable_ids, zone_for_cable, alert_if, extra_facts, recipients,
                       actions, path_track=None, track_fn=None):
    """Shared by baltic-2024 and taiwan: cable-watch scored ships → vessels, events, alerts."""
    vessels, events, alerts = [], [], []
    for rank, src in enumerate(vs, 1):
        prefix = "T" if src.get("type") == "Tanker" else "V"
        vid = f"{prefix}-{rank:02d}"
        track = track_fn(src)
        sigs = src["signals"]
        dark = []
        for s in sigs:
            if s["kind"] == "ais_gap":
                dark.append({"start": int(s["start"]), "end": int(s["end"]),
                             "minutes": round((s["end"] - s["start"]) / 60)})
        t0 = track[0][0] if track else min(s["start"] for s in sigs)
        t1 = track[-1][0] if track else max(s["end"] for s in sigs)
        sanctioned = any(s["kind"] == "watchlist" for s in sigs)
        ev_ids, top = [], None
        best = {}
        for s in sigs:
            text, info = en_reason(s)
            s["_en"], s["_info"] = text, info
            best[s["kind"]] = max(best.get(s["kind"], (0, None)), (s["points"], text), key=lambda x: x[0])
            if s["kind"] not in EVENT_TYPE:
                continue
            eid = ids("E")
            s["_eid"] = eid
            ev_ids.append(eid)
            cab = s.get("cable")
            facts = [f"{s['points']} points · cable-watch rule: {RULE_LABEL[s['kind']]}",
                     f"{utc(s['start'])} → {utc(s['end'])}"]
            if cab:
                facts.append(f"Cable: {cab}")
            events.append({"id": eid, "vessel_id": vid, "type": EVENT_TYPE[s["kind"]], "t": int(s["start"]),
                           "end": int(s["end"]), "lon": r5(s["lon"]), "lat": r5(s["lat"]),
                           "severity": sev_of(s["points"]), "summary": text.split("; ")[0] + ".",
                           "detail": text + ".", "facts": facts, "source": "ais",
                           "zone_id": zone_for_cable.get(cab), "asset_id": cable_ids.get(cab),
                           "other_vessel_id": None})
            if top is None or s["points"] > top["points"]:
                top = s
        src_of = {"watchlist": "sanctions", "flag": "registry"}
        reasons = [{"text": txt, "points": pts, "source": src_of.get(k, "ais")}
                   for k, (pts, txt) in sorted(best.items(), key=lambda kv: -kv[1][0])]
        score = src["score"]
        v = vessel(id=vid, mmsi=src["mmsi"], imo=src.get("imo") or None, name=(src.get("name") or "").strip(),
                   flag=src.get("flag") or None, type=(src.get("type") or "unknown").lower(),
                   length_m=src.get("length"), track=track, claimed=[list(p) for p in track],
                   states=states_for(t0, t1, dark), dark_periods=dark,
                   risk={"score": score, "level": level_of(score), "reasons": reasons,
                         "rules": "cable-watch (cable_watch/detect.py): each kind counts once, total capped at 100"},
                   crossings=src.get("crossings"))
        vessels.append(v)
        for p in (path_track or {}).get(src["mmsi"], []):
            if p["type"] == "encounter":
                eid = ids("E")
                h = (p["end"] - p["start"]) / 3600
                events.append({"id": eid, "vessel_id": vid, "type": "ship_to_ship_meeting", "t": p["start"],
                               "end": p["end"], "lon": p["lon"], "lat": p["lat"], "severity": "medium",
                               "summary": f"Global Fishing Watch encounter event: alongside another vessel for "
                                          f"{h:.1f} h (the other vessel is not in this extract).",
                               "facts": [f"{utc(p['start'])} → {utc(p['end'])}", "Source: GFW encounters dataset"],
                               "source": "ais", "zone_id": None, "asset_id": None, "other_vessel_id": None})
        if top is not None and alert_if(src):
            sev = level_of(score)
            rest = [r["text"].split("; ")[0] for r in reasons if r["text"] != top["_en"]]
            flags = [x for x in rest if x.startswith(("OpenSanctions", "Flag of"))]
            facts = [f"Risk {score}/100 under cable-watch rules", top["_en"].split("; ")[0]] + flags + \
                extra_facts(src, v) + rest
            seen, fl = set(), []
            for f in facts:
                if f not in seen:
                    seen.add(f)
                    fl.append(f)
            others = [s["_eid"] for s in sigs if s.get("_eid") and s is not top and s["points"] >= 25][:3]
            alerts.append({"id": None, "vessel_id": vid, "event_ids": [top["_eid"]] + others,
                           "zone_id": zone_for_cable.get(top.get("cable")), "t": fire_t(top),
                           "severity": "high" if sev == "high" or sanctioned else sev,
                           "headline": headline(v["name"], top, top["_info"],
                                                sanctioned and v["type"] == "tanker"),
                           "facts": fl[:4], "actions": actions, "recipients": recipients, "status": "open"})
    alerts.sort(key=lambda a: a["t"])
    for i, a in enumerate(alerts, 1):
        a["id"] = f"A-{i:04d}"
    return vessels, events, alerts


def cable_assets(features, keep, protected=(), notes=None):
    out = []
    for f in features:
        p = f["properties"]
        if not keep(f):
            continue
        a = {"id": p["id"], "alias": alias_of(p["name"]), "name": p["name"], "kind": p.get("kind", "telecom"),
             "geometry": round_geom(f["geometry"]), "owner": "", "source": f"{p.get('source', 'TeleGeography')}"
             " via cable-watch", "protected": p["name"] in protected}
        a.update((notes or {}).get(p["name"], {}))
        out.append(a)
    return out


# ---------------------------------------------------------------- baltic-2024

BALTIC_SPECIAL = {414270000: "YI PENG 3", 273256410: "VASILY LANOVOY", 518999083: "CANGJIE",
                  314920000: "CONSTELLATION"}
BCS_CUT = 1731829800      # 17 Nov 2024 07:50 UTC
CLION_CUT = 1731895200    # 18 Nov 2024 02:00 UTC
WIKI_CUTS = "https://en.wikipedia.org/wiki/2024_Baltic_Sea_submarine_cable_disruptions"


def build_baltic():
    d = json.loads((CW / "replay-yi-peng-3.json").read_text())
    summ = d["summary"]
    vs = d["vessels"][:56] + [v for v in d["vessels"][56:] if v["mmsi"] in BALTIC_SPECIAL]
    fc = json.loads((CW / "cables.geojson").read_text())["features"]
    cut = {"C-Lion1": CLION_CUT, "BCS East-West Interlink": BCS_CUT}
    ids = Ids()

    def track_fn(src):
        return make_track([(t, lon, lat, sog) for lon, lat, t, sog in src["track"]], max_pts=400)

    pts = [(p[1], p[2]) for v in vs for p in track_fn(v)]
    bbox = bbox_of(pts, pad=0.2)
    view = [bbox[0] - 1.5, bbox[1] - 1, bbox[2] + 3, bbox[3] + 1]

    def keep(f):
        g = shape(f["geometry"])
        return g.intersects(box(*view))

    notes = {"C-Lion1": {"owner": "Cinia", "cut_t": CLION_CUT, "status": "Cut 18 Nov 2024 02:00 UTC"},
             "BCS East-West Interlink": {"cut_t": BCS_CUT, "status": "Cut 17 Nov 2024 07:50 UTC"}}
    assets = cable_assets(fc, keep, protected=set(cut), notes=notes)
    cable_ids = {f["properties"]["name"]: f["properties"]["id"] for f in fc}
    cut_lines = [ln for f in fc if f["properties"]["name"] in cut for ln in lines_of(f["geometry"])]
    rings = buffer_rings(cut_lines, 5, 16.0, 56.5, max_parts=2)
    zones = []
    for i, ring in enumerate(rings, 1):
        zones.append(make_zone(f"Z-{i}", "Cut cables corridor: C-Lion1 + BCS East-West (±5 km)" if len(rings) == 1
                               else f"Cut cables corridor {i} (±5 km)", "cable",
                               {"type": "polygon", "coordinates": ring}, asset_id=cable_ids["C-Lion1"],
                               recipients=[{"name": "Coast guard duty officer", "channel": "push", "address": "",
                                            "min_severity": "medium", "quiet_hours": None},
                                           {"name": "Cable operator NOC", "channel": "email", "address": "",
                                            "min_severity": "medium", "quiet_hours": None}],
                               created=summ["start"]))
    zone_for_cable = {name: "Z-1" for name in cut} if zones else {}

    def alert_if(src):
        return src["score"] >= 60 or src["mmsi"] in BALTIC_SPECIAL

    def extra(src, v):
        out = []
        if src["mmsi"] == 414270000:
            out += ["BCS East-West cut 17 Nov 07:50 UTC; C-Lion1 cut 18 Nov 02:00 UTC",
                    "Both cut sites lie east of Danish AIS coverage, so this record shows only its approach"]
        out.append(f"{src.get('type') or 'Unknown type'}" + (f", {v['length_m']:.0f} m" if v["length_m"] else "") +
                   f", MMSI {v['mmsi']}")
        return out

    vessels, events, alerts = signal_world_parts(
        vs, ids, set(cut), cable_ids, zone_for_cable, alert_if, extra,
        recipients=[{"name": "Coast guard duty officer", "channel": "push"},
                    {"name": "Cable operator NOC", "channel": "email"}],
        actions=[{"id": "dispatch_patrol", "label": "Ask coast guard to hail the ship"},
                 {"id": "forward_owner", "label": "Forward to cable owner"},
                 {"id": "dismiss", "label": "Not suspicious"}],
        track_fn=track_fn)
    yp = next(v for v in vessels if v["mmsi"] == 414270000)
    yp["note"] = ("Yi Peng 3 scores only 30 here: both cut sites lie east of Danish AIS coverage, "
                  "so this AIS record shows its approach, not the cuts.")

    scenario = {
        "id": "baltic-2024", "case": "baltic-2024", "purpose": "cable",
        "label": "Western Baltic · 17–19 Nov 2024 · real AIS", "start": summ["start"], "end": 1732060800,
        "now": CLION_CUT + 50 * 60, "illustrative": False, "bbox": bbox, "time_mode": "replay",
        "sources": [{"label": "Danish Maritime Authority AIS, 17–19 Nov 2024", "url": "http://aisdata.ais.dk/"},
                    {"label": "TeleGeography submarine cable map (via cable-watch)",
                     "url": "https://www.submarinecablemap.com/"},
                    {"label": "OpenSanctions (sanctions, shadow fleet, detentions)",
                     "url": "https://www.opensanctions.org/"},
                    {"label": "2024 Baltic Sea submarine cable disruptions (cut times)", "url": WIKI_CUTS}],
        "facts": [
            {"label": "Ships in Danish AIS, 17–19 Nov 2024", "value": f"{summ['ships']:,} ({summ['fixes']:,} fixes)",
             "source": "Danish Maritime Authority AIS"},
            {"label": "Flagged by cable-watch rules", "value": f"{summ['flagged']} ({summ['high']} high, "
             f"{summ['medium']} medium)", "source": "cable-watch scoring"},
            {"label": "Cable cuts", "value": "BCS East-West 17 Nov 07:50 UTC · C-Lion1 18 Nov 02:00 UTC",
             "source": "Wikipedia: 2024 Baltic Sea submarine cable disruptions"},
            {"label": "Yi Peng 3", "value": "score 30: the cut sites lie east of Danish AIS coverage",
             "source": "cable-watch scoring"}],
        "notes": [f"Top {len(vessels)} of the {summ['flagged']} flagged ships, plus Yi Peng 3 and the three "
                  "sanctioned or shadow-fleet tankers.",
                  "No satellite radar or RF in this case: observed and emitted layers are empty.",
                  "Dark periods are the AIS gaps that cable-watch scored near a cable."],
    }
    return {"scenario": scenario, "assets": assets, "zones": zones, "vessels": vessels, "events": events,
            "alerts": alerts, "actions": []}


# ---------------------------------------------------------------- dark-skagen

SKAGEN_BOX = [10.4, 57.58, 10.98, 57.86]
SCENE_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-rtc/items/"


def _ais_at(ship, t, t0):
    """AIS position of a replay ship at epoch t (linear between fixes ≤ 10 min apart), or None."""
    p = ship["p"]
    rel = t - t0
    for a, b in zip(p, p[1:]):
        if a[0] <= rel <= b[0]:
            if b[0] - a[0] > 600:
                return None
            f = (rel - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0
            return a[2] + (b[2] - a[2]) * f, a[1] + (b[1] - a[1]) * f
    return None


def build_skagen():
    passes = json.loads((OA / "cache" / "track-skagen-passes.json").read_text())
    rp = json.loads(gzip.open(OA / "data" / "tracks_skagen_2026-11-06.json.gz", "rt").read())
    t0 = int(rp["t0"])
    start, end = t0 + 5 * 3600, t0 + 18 * 3600
    ships = {s["mmsi"]: s for s in rp["ships"]}
    ids = Ids()

    images, pinfo = [], []
    for ps in passes:
        t = epoch(ps["pass_time"])
        hhmm = utc(t, "%H%M")
        src = OA / "cache" / "media" / Path(ps["map_overlay"]["url"]).name
        url = magick_jpg(src, f"skagen-{hhmm}.jpg", width=1200, quality=78)
        (la0, lo0), (la1, lo1) = ps["map_overlay"]["bounds"]
        stats = {s["label"]: s["value"] for s in ps["stats"]}
        images.append({"id": f"s1-{hhmm}", "label": f"Sentinel-1C radar · 6 Nov 2026 {utc(t, '%H:%M')} UTC",
                       "url": url, "bounds": [lo0, la0, lo1, la1], "t": t, "scene": ps["scene"],
                       "source": SCENE_URL + ps["scene"]})
        pinfo.append({"t": t, "scene": ps["scene"], "url": url, "markers": ps["markers"], "stats": stats})

    # AIS positions at each pass, for nearest-AIS distances of the dark targets
    ais_at = {p["t"]: [(m, *xy) for m, s in ships.items() if (xy := _ais_at(s, p["t"], t0))] for p in pinfo}

    matched, darks = {}, []
    used = {p["t"]: {mk["ais"]["mmsi"] for mk in p["markers"] if mk["status"] == "ais"} for p in pinfo}
    for p in pinfo:
        for k, mk in enumerate(p["markers"]):
            ob = {"id": f"SAR-{utc(p['t'], '%H%M')}-{k + 1:02d}", "t": p["t"], "lon": r5(mk["lon"]),
                  "lat": r5(mk["lat"]), "length_m": mk["length_m"], "rcs_db": mk["rcs_db"], "size": mk["size"],
                  "matched_to_ais": mk["status"] == "ais", "sensor": "SAR · Sentinel-1C IW (Planetary Computer RTC)",
                  "scene": p["scene"], "image_url": p["url"]}
            if mk["status"] == "ais":
                mm = mk["ais"]["mmsi"]
                xy = _ais_at(ships[mm], p["t"], t0) if mm in ships else None
                if xy:
                    ob["ais_offset_m"] = round(hav_m(xy[0], xy[1], mk["lon"], mk["lat"]))
                matched.setdefault(mm, []).append(ob)
            else:
                near = min(((hav_m(lo, la, mk["lon"], mk["lat"]), m) for m, lo, la in ais_at[p["t"]]),
                           default=(None, None))
                ob["nearest_ais_m"] = None if near[0] is None else round(near[0])
                ob["nearest_ais"] = ships[near[1]]["name"] if near[1] else None
                if near[1] in used[p["t"]]:
                    ob["nearest_ais"] += " (matched to another radar target)"
                for d in darks:  # the same spot on both passes → one target
                    if any(hav_m(o["lon"], o["lat"], ob["lon"], ob["lat"]) < 300 for o in d):
                        d.append(ob)
                        break
                else:
                    darks.append([ob])

    vessels, events, alerts = [], [], []

    def ais_track(s, step_s):
        pts, last = [], -1e9
        for q in s["p"]:
            t = t0 + q[0]
            if start <= t <= end and t - last >= step_s:
                pts.append((t, q[2], q[1], q[3] if q[3] is not None and q[3] >= 0 else None))
                last = t
        return make_track(pts, max_pts=400)

    def in_box(tr):
        return any(SKAGEN_BOX[0] <= p[1] <= SKAGEN_BOX[2] and SKAGEN_BOX[1] <= p[2] <= SKAGEN_BOX[3] for p in tr)

    order = sorted(ships, key=lambda m: (m not in matched, ships[m]["name"] or "~"))
    n = 0
    for m in order:
        s = ships[m]
        tr = ais_track(s, 180 if m in matched else 600)
        if len(tr) < 2 or (m not in matched and not in_box(tr)):
            continue
        n += 1
        typ = (s.get("type") or "unknown").lower()
        vid = f"{'T' if typ == 'tanker' else 'V'}-{n:02d}"
        obs = matched.get(m, [])
        reasons = [{"text": f"AIS position confirmed by Sentinel-1 radar at {utc(o['t'], '%H:%M')} UTC"
                            + (f" (radar target {o['ais_offset_m']} m from the AIS position)"
                               if o.get("ais_offset_m") is not None else ""), "points": 0, "source": "sar"}
                   for o in obs]
        try:
            length = float(s.get("length") or 0) or None
        except ValueError:
            length = None
        vessels.append(vessel(id=vid, mmsi=int(m), name=(s.get("name") or "").strip(), type=typ, length_m=length,
                              track=tr, claimed=[list(p) for p in tr], observed=obs,
                              states=states_for(tr[0][0], tr[-1][0], []),
                              risk={"score": 0, "level": "low", "reasons": reasons}, destination=s.get("dest") or None,
                              ais_class=s.get("class") or None))

    for i, obs in enumerate(darks, 1):
        vid = f"D-{i:02d}"
        large = any(o["size"] == "large" for o in obs)
        reasons = [{"text": f"Radar target with no AIS position within 500 m + 80 m per knot at "
                            f"{', '.join(utc(o['t'], '%H:%M') for o in obs)} UTC", "points": 30, "source": "sar"}]
        if large:
            o = max(obs, key=lambda o: o["rcs_db"])
            reasons.append({"text": f"Large-ship radar return ({o['rcs_db']} dB m²)", "points": 30, "source": "sar"})
        else:
            reasons.append({"text": "Weak return, typical of a boat under about 30 m; small boats often carry no AIS",
                            "points": -10, "source": "sar"})
        if len(obs) > 1:
            reasons.append({"text": f"Seen at the same spot on both passes, "
                                    f"{(obs[-1]['t'] - obs[0]['t']) / 3600:.0f} h apart: an anchored boat or a "
                                    f"fixed object (buoy, platform)", "points": 0, "source": "sar"})
        score = max(0, sum(r["points"] for r in reasons))
        ev_ids = []
        for o in obs:
            eid = ids("E")
            ev_ids.append(eid)
            near = (f"nearest AIS ship {o['nearest_ais']} {o['nearest_ais_m'] / 1000:.1f} km away"
                    if o.get("nearest_ais_m") is not None else "no AIS ship in the extract at that time")
            events.append({"id": eid, "vessel_id": vid, "type": "dark_period", "t": o["t"], "end": None,
                           "lon": o["lon"], "lat": o["lat"], "severity": "high" if large else "low",
                           "summary": f"Radar target with no AIS within {o['nearest_ais_m']:,} m at "
                                      f"{utc(o['t'], '%H:%M')} UTC." if o.get("nearest_ais_m") is not None else
                                      f"Radar target with no AIS at {utc(o['t'], '%H:%M')} UTC.",
                           "facts": [near.capitalize(),
                                     f"Radar return {o['rcs_db']} dB m²: " + ("large-ship return" if o["size"] ==
                                                                              "large" else "weak, boat under ~30 m"),
                                     f"Blob length ~{o['length_m']} m (motion smears it; not a reliable size)",
                                     f"Scene {o['scene']}"],
                           "source": "sar", "zone_id": "Z-1", "asset_id": None, "other_vessel_id": None,
                           "image_url": o["image_url"]})
        v = vessel(id=vid, name="Unidentified radar target", type="unknown", length_m=obs[0]["length_m"],
                   observed=obs, states=[{"start": start, "end": end, "state": "dark"}],
                   risk={"score": score, "level": level_of(score), "reasons": reasons},
                   length_note="Radar blob length. " + ("Return strength suggests a large ship." if large else
                                                        "Return strength suggests a boat under about 30 m."))
        vessels.append(v)
        o = obs[0]
        near = (f"Nearest AIS ship {o['nearest_ais']}: {o['nearest_ais_m'] / 1000:.1f} km"
                if o.get("nearest_ais_m") is not None else "No AIS ship nearby in the extract")
        facts = [near,
                 f"Radar return {o['rcs_db']} dB m²: " + ("large-ship return" if large else
                                                          "weak, typical of a boat under 30 m"),
                 "Small boats often carry no AIS; boats under ~15 m are often invisible to 10 m radar"
                 if not large else f"Blob length ~{o['length_m']} m (smeared by motion)"]
        if len(obs) > 1:
            facts.append(reasons[-1]["text"])
        alerts.append({"id": None, "vessel_id": vid, "event_ids": ev_ids, "zone_id": "Z-1", "t": o["t"],
                       "severity": "high" if large else "low",
                       "headline": (f"Large radar target with no AIS off Skagen at {utc(o['t'], '%H:%M')} UTC"
                                    if large else
                                    f"Small radar target with no AIS off Skagen at {utc(o['t'], '%H:%M')} UTC "
                                    f"(likely a boat under 30 m)"),
                       "facts": facts[:4],
                       "actions": [{"id": "task_followup", "label": "Get a sharper image (quote only)"},
                                   {"id": "forward_coastguard", "label": "Forward to coast guard"},
                                   {"id": "dismiss", "label": "Not suspicious"}],
                       "recipients": [{"name": "Sanctions analyst", "channel": "email"},
                                      {"name": "Coast guard duty officer", "channel": "push"}],
                       "status": "open"})
    alerts.sort(key=lambda a: (a["t"], a["severity"] != "high"))
    for i, a in enumerate(alerts, 1):
        a["id"] = f"A-{i:04d}"

    p1, p2 = pinfo
    zone = make_zone("Z-1", "Skagen radar box (Sentinel-1 search area)", "sanctions",
                     {"type": "polygon", "coordinates": [[SKAGEN_BOX[0], SKAGEN_BOX[1]], [SKAGEN_BOX[2], SKAGEN_BOX[1]],
                                                         [SKAGEN_BOX[2], SKAGEN_BOX[3]], [SKAGEN_BOX[0], SKAGEN_BOX[3]]]},
                     created=start)
    big1 = sum(1 for m in p1["markers"] if m["status"] == "dark" and m["size"] == "large")

    def pass_fact(p):
        s = p["stats"]
        return (f"{s['Radar targets']} radar targets: {s['Match AIS']} match AIS, {s['No AIS']} without "
                f"({s['Large dark ships']} large)")
    scenario = {
        "id": "dark-skagen", "case": "dark-skagen", "purpose": "sanctions",
        "label": "Skagen · 6 Nov 2026 · Sentinel-1 radar vs AIS", "start": start, "end": end,
        "now": p2["t"] + 37 * 60, "illustrative": False, "bbox": [10.3, 57.52, 11.08, 57.92], "time_mode": "replay",
        "images": images,
        "sources": [{"label": f"Sentinel-1C RTC scene {p['scene']}", "url": SCENE_URL + p["scene"]} for p in pinfo] +
                   [{"label": "Danish Maritime Authority AIS, aisdk-2026-11-06", "url": "http://aisdata.ais.dk/"},
                    {"label": "ESA WorldCover 2021 (land mask)",
                     "url": "https://planetarycomputer.microsoft.com/dataset/esa-worldcover"}],
        "facts": [
            {"label": f"{utc(p1['t'], '%H:%M')} UTC pass", "value": pass_fact(p1),
             "source": "Sentinel-1C (Planetary Computer) + DMA AIS"},
            {"label": f"{utc(p2['t'], '%H:%M')} UTC pass", "value": pass_fact(p2),
             "source": "Sentinel-1C (Planetary Computer) + DMA AIS"},
            {"label": "Match rule", "value": "radar target within 500 m + 80 m per knot of an AIS position",
             "source": "orbit-analyst"},
            {"label": "AIS ships in the extract", "value": f"{len(ships)} (Class A/B, 10.2–11.2 E, 57.45–57.95 N)",
             "source": "Danish Maritime Authority AIS"}],
        "notes": ["Boats under about 15 m and wooden or fibreglass boats are often too faint for 10 m radar.",
                  "Size comes from the total radar return; a fast large ship smears and can look small.",
                  "Waves, wind farms, buoys and platforms can look like ships. Land and 250 m of coast are masked.",
                  f"{big1} large dark target on the 05:39 pass sits at the edge of the radar box.",
                  "No RF layer in this case."],
    }
    return {"scenario": scenario, "assets": [], "zones": [zone], "vessels": vessels, "events": events,
            "alerts": alerts, "actions": []}


# ---------------------------------------------------------------- taiwan

TW_BBOX = [117.5, 21.5, 123.0, 26.8]
TW_INCIDENTS = {  # English versions of cable-watch/data/taiwan-incidents.json
    "hongtai58": {"title": "Hong Tai 58 cuts the Taiwan–Penghu No.3 cable (TPKM3)",
                  "summary": "Togo-flagged Hong Tai 58 anchored 0.5 nmi from the cable for about 56 h, ignored 7 "
                             "radio calls, then zigzagged over it while weighing anchor. The captain was sentenced "
                             "to 3 years.",
                  "labels": ["Taiwan Coast Guard news (via Pourquoi)", "Tainan District Prosecutors Office "
                             "indictment, 11 Apr 2025", "Nottingham Taiwan Research Hub"]},
    "shunxin39": {"title": "Shunxin-39 damages the TPE cable",
                  "summary": "Cameroon-flagged, Hong Kong-owned Shunxin-39 (also seen as Xingshun 39) was near the "
                             "damaged TPE cable NE of Yeliu and switched AIS off after the coast guard told it to "
                             "return. It was not boarded or charged.",
                  "labels": ["New York Times, 7 Jan 2025", "IAI (2026) What Lies Beneath"]},
    "matsu2023": {"title": "Taiwan–Matsu No.2 and No.3 cables cut",
                  "summary": "TPKM2 was cut on 2 Feb 2023 and TPKM3 on 8 Feb; Matsu lost normal internet for about "
                             "six weeks. Fishing boats and anchors cut Matsu's cables 27 times in 2018–2022.",
                  "labels": ["IAI (2026) What Lies Beneath", "MEMRI, 27 Feb 2025"]},
}


def build_taiwan():
    d = json.loads((CW / "taiwan-gfw.json").read_text())
    summ = d["summary"]
    fc = json.loads((CW / "cables-taiwan.geojson").read_text())["features"]
    incs = json.loads((CW / "taiwan-incidents.json").read_text())
    ids = Ids()
    start = day_epoch(summ["start"])
    end = day_epoch(summ["end"]) + 86400

    def track_fn(src):
        evs = src.get("path") or [{"start": s["start"], "end": s["end"], "lon": s["lon"], "lat": s["lat"],
                                   "knots": None, "type": "loitering"} for s in src["signals"] if s.get("lon")]
        pts = []
        for e in sorted(evs, key=lambda e: e["start"]):
            pts += [(e["start"], e["lon"], e["lat"], e.get("knots")), (e["end"], e["lon"], e["lat"], e.get("knots"))]
        pts = sorted({p[0]: p for p in pts}.values())
        return make_track(pts, max_pts=400)

    inc_cables = {}
    for inc in incs:
        for c in inc["cables"]:
            inc_cables.setdefault(c, []).append(inc["id"])
    notes = {c: {"incidents": v} for c, v in inc_cables.items()}
    assets = cable_assets(fc, lambda f: True, protected=set(inc_cables), notes=notes)
    cable_ids = {f["properties"]["name"]: f["properties"]["id"] for f in fc}
    zl = [ln for f in fc if f["properties"]["name"] in inc_cables for ln in lines_of(f["geometry"])]
    rings = buffer_rings(zl, 2, 120.5, 24.5, clip=TW_BBOX, max_parts=3, simplify_m=300)
    zones = [make_zone(f"Z-{i}", f"Incident cables corridor {i} (TPKM2, TPKM3, TPE ±2 km)", "cable",
                       {"type": "polygon", "coordinates": ring}, created=start) for i, ring in enumerate(rings, 1)]
    zpolys = [shapely.Polygon(r) for r in rings]

    def zone_for(lon, lat):
        p = shapely.Point(lon, lat)
        return next((f"Z-{i}" for i, zp in enumerate(zpolys, 1) if zp.contains(p)), None)

    def extra(src, v):
        out = [f"{v['type'].capitalize()}, flag {v['flag'] or 'unknown'}, MMSI {v['mmsi']}"]
        return out

    vessels, events, alerts = signal_world_parts(
        d["vessels"], ids, set(), cable_ids, {}, lambda s: s["tier"] == "high", extra,
        recipients=[{"name": "Coast guard duty officer", "channel": "push"},
                    {"name": "Cable operator NOC", "channel": "email"}],
        actions=[{"id": "dispatch_patrol", "label": "Ask coast guard to hail the ship"},
                 {"id": "forward_owner", "label": "Forward to cable owner"},
                 {"id": "dismiss", "label": "Not suspicious"}],
        path_track={v["mmsi"]: v.get("path") or [] for v in d["vessels"]}, track_fn=track_fn)
    for e in events:
        e["zone_id"] = zone_for(e["lon"], e["lat"]) if e["lon"] is not None else None
    for a in alerts:
        a["zone_id"] = next((e["zone_id"] for e in events if e["id"] == a["event_ids"][0]), None)
    for v in vessels:
        v["track_note"] = ("Global Fishing Watch event positions (loitering, port visits, encounters), not a full "
                           "AIS track; the line between events is drawn straight.")

    # Sentinel-1 detections with no AIS within 2 km of a cable (GFW, one row per day and grid cell)
    fwd, _ = _aeqd(120.5, 24.5)
    parts, owner = [], []
    for i, f in enumerate(fc):
        for ln in lines_of(f["geometry"]):
            xy = np.array([(a, b) for a, b, *_ in ln])
            x, y = fwd.transform(xy[:, 0], xy[:, 1])
            parts.append(LineString(np.column_stack([x, y])))
            owner.append(i)
    tree = shapely.STRtree(parts)
    sar = [r for r in d["sar"] if r[4]]
    sx, sy = fwd.transform(np.array([r[0] for r in sar]), np.array([r[1] for r in sar]))
    (pi, gi), dist = tree.query_nearest(shapely.points(sx, sy), return_distance=True, all_matches=False)
    near = sorted(zip(pi, gi, dist), key=lambda x: x[2])[:300]
    for k, (i, g, dm) in enumerate(sorted(near, key=lambda x: day_epoch(sar[x[0]][2])), 1):
        lon, lat, day, n, _ = sar[i]
        cab = fc[owner[g]]["properties"]["name"]
        vid = f"D-{k:03d}"
        t = day_epoch(day)
        ob = {"id": f"SAR-TW-{k:04d}", "t": t, "lon": r5(lon), "lat": r5(lat), "length_m": None,
              "matched_to_ais": False, "sensor": "SAR · Sentinel-1 via Global Fishing Watch", "detections": int(n),
              "time_precision": "day", "cable_m": round(float(dm))}
        eid = ids("E")
        events.append({"id": eid, "vessel_id": vid, "type": "dark_period", "t": t, "end": None, "lon": r5(lon),
                       "lat": r5(lat), "severity": "medium" if dm < 500 else "low",
                       "summary": f"Sentinel-1 ship detection with no matching AIS, {dm:,.0f} m from {cab} ({day}).",
                       "facts": [f"{int(n)} detection{'s' if n > 1 else ''} in this GFW grid cell on {day}",
                                 "GFW gives the day, not the pass time", f"Cable: {cab}"],
                       "source": "sar", "zone_id": zone_for(lon, lat), "asset_id": cable_ids.get(cab),
                       "other_vessel_id": None})
        vessels.append(vessel(id=vid, name="Unidentified radar target", type="unknown", observed=[ob],
                              states=[{"start": start, "end": end, "state": "dark"}],
                              risk={"score": 0, "level": "low", "reasons": [
                                  {"text": f"Radar detection with no AIS {dm:,.0f} m from {cab}; not scored (GFW "
                                           f"gives day-level detections only)", "points": 0, "source": "sar"}]}))

    # "now": the moment most high/medium ships are in a flagged event at once
    sigs = [s for v in d["vessels"] if v["score"] >= 40 for s in v["signals"] if s.get("lon")]
    cands = [s["start"] + 3600 for s in sigs]
    now = max(cands, key=lambda t: (sum(s["start"] <= t <= s["end"] for s in sigs), t)) if cands else end

    incidents = []
    for inc in incs:
        en = TW_INCIDENTS.get(inc["id"], {})
        labels = en.get("labels", [])
        incidents.append({"id": inc["id"], "title": en.get("title", inc["id"]), "date": inc["date"], "lon": inc["lon"],
                          "lat": inc["lat"], "approx": inc["approx"], "cables": inc["cables"],
                          "summary": en.get("summary", ""),
                          "sources": [{"label": labels[i] if i < len(labels) else lab, "url": url}
                                      for i, (lab, url) in enumerate(inc["sources"])]})
    scenario = {
        "id": "taiwan", "case": "taiwan", "purpose": "cable",
        "label": "Taiwan Strait · Sep–Nov 2026 · GFW events and radar", "start": start, "end": end,
        "now": int(now), "illustrative": False, "bbox": TW_BBOX, "time_mode": "replay", "incidents": incidents,
        "sources": [{"label": "Global Fishing Watch API v3 (loitering, port visits, encounters, SAR detections)",
                     "url": "https://globalfishingwatch.org/our-apis/"},
                    {"label": "TeleGeography submarine cable map (via cable-watch)",
                     "url": "https://www.submarinecablemap.com/"},
                    {"label": "OpenSanctions", "url": "https://www.opensanctions.org/"}] +
                   [s for i in incidents for s in i["sources"]][:4],
        "facts": [
            {"label": "Non-fishing ships with GFW loitering events", "value": f"{summ['ships']} ({summ['events']:,} "
             f"events, {summ['start']} to {summ['end']})", "source": "Global Fishing Watch API v3"},
            {"label": "Flagged near Taiwan's cables", "value": f"{summ['flagged']} ({summ['high']} high, "
             f"{summ['medium']} medium)", "source": "cable-watch rules on GFW events"},
            {"label": "Radar detections with no AIS", "value": f"{summ['sar_dark']:,} on {summ['sar_days']} days; "
             f"{summ['sar_near']} within 2 km of a cable", "source": "Global Fishing Watch (Sentinel-1)"},
            {"label": "Hong Tai 58, 25 Feb 2025", "value": "anchored ~56 h by TPKM3, then cut it; captain sentenced "
             "to 3 years", "source": "Tainan District Prosecutors Office"}],
        "notes": [f"All {summ['flagged']} flagged ships, plus the 300 radar detections with no AIS that lie "
                  "nearest a cable (of the {summ['sar_near']} within 2 km).",
                  "Tracks are GFW event positions, not continuous AIS.", "No RF layer in this case."],
    }
    scenario["notes"][0] = scenario["notes"][0].replace("{summ['sar_near']}", str(summ["sar_near"]))
    return {"scenario": scenario, "assets": assets, "zones": zones, "vessels": vessels, "events": events,
            "alerts": alerts, "actions": []}


# ---------------------------------------------------------------- migration

def build_migration():
    d = json.loads((AB / "real.json").read_text())
    pts = d["points"]
    line = [[pts[k]["lon"], pts[k]["lat"]] for k in ("tobruk", "mid", "gavdos")]
    strip = d["strip"]
    sb = strip["bbox"]
    ids = Ids()
    mid_acq = sorted(epoch(a["t"]) for a in d["s1"]["mid"]["acq"])
    runs = sorted(strip["runs"], key=lambda r: r["t"])
    images, vessels, events, alerts = [], [], [], []
    k = 0
    for run in runs:
        t = epoch(run["t"])
        url = magick_jpg(AB / run["png"], Path(run["png"]).stem + ".jpg", quality=82)
        images.append({"id": Path(run["png"]).stem, "label": f"Sentinel-{run['platform'][1:]} {run['orbit']} pass · "
                       f"{utc(t, '%-d %b %Y %H:%M')} UTC · open-sea strip", "url": url, "bounds": sb, "t": t,
                       "source": "https://planetarycomputer.microsoft.com/dataset/sentinel-1-grd"})
        seen, small = set(), []
        for det in run["dets"]:
            key = (det["lon"], det["lat"], det["len_m"])
            if key in seen:  # the pipeline listed one blob twice on 4 Nov
                continue
            seen.add(key)
            k += 1
            vid = f"D-{k:02d}"
            L = det["len_m"]
            ob = {"id": f"SAR-LY-{k:03d}", "t": t, "lon": r5(det["lon"]), "lat": r5(det["lat"]), "length_m": L,
                  "matched_to_ais": False, "ais_checked": False, "contrast_db": det["contrast_db"],
                  "sensor": f"SAR · Sentinel-{run['platform'][1:]} IW GRD ({run['orbit']} pass, Planetary Computer)",
                  "image_url": url, "note": "radar target, not checked against AIS; length ±20 m"}
            is_small = L < 40
            eid = ids("E")
            size = "trawler-size or smaller" if is_small else ("40–100 m" if L < 100 else "ship-size, ≥100 m")
            events.append({"id": eid, "vessel_id": vid, "type": "zone_entry", "t": t, "end": None, "lon": ob["lon"],
                           "lat": ob["lat"], "severity": "medium" if is_small else "low",
                           "summary": f"Radar target ~{L} m ({size}) in the corridor at {utc(t, '%-d %b %H:%M')} UTC, "
                                      f"not checked against AIS.",
                           "facts": [f"Contrast {det['contrast_db']} dB over the sea around it", "Length ±20 m",
                                     f"{run['platform']} {run['orbit']} pass"],
                           "source": "sar", "zone_id": "Z-1", "asset_id": "corridor", "other_vessel_id": None,
                           "image_url": url})
            reasons = [{"text": f"{'Small' if is_small else 'Ship-size'} radar target (~{L} m) in the crossing "
                                f"corridor, not checked against AIS", "points": 50 if is_small else 10,
                        "source": "sar"}]
            sc = reasons[0]["points"]
            vessels.append(vessel(id=vid, name="Radar target (not checked against AIS)", type="unknown",
                                  length_m=L, observed=[ob], states=[{"start": t - 3600, "end": t + 3600,
                                                                      "state": "dark"}],
                                  risk={"score": sc, "level": level_of(sc), "reasons": reasons,
                                        "rules": "Skynet corridor watch: < 40 m targets score 50, others 10"}))
            if is_small:
                small.append((vid, eid, ob))
        if small:
            nxt = next((a for a in mid_acq if a > t + 600), None)
            facts = [f"~{o['length_m']} m at {o['lat']:.3f} N, {o['lon']:.3f} E" for _, _, o in small][:2]
            facts.append("Not checked against AIS; length ±20 m; could be a fishing boat")
            facts.append(f"Next Sentinel-1 look mid-corridor: {utc(nxt)} ({(nxt - t) / 3600:.0f} h later)"
                         if nxt else "No later Sentinel-1 look in the 60-day record")
            alerts.append({"id": None, "vessel_id": small[0][0], "event_ids": [e for _, e, _ in small],
                           "zone_id": "Z-1", "t": t + 53 * 60, "severity": "medium",
                           "headline": f"{len(small)} small radar target{'s' if len(small) > 1 else ''} in the "
                                       f"corridor at {utc(t, '%-d %b %H:%M')} UTC: check with rescue coordination",
                           "facts": facts,
                           "actions": [{"id": "forward_rcc", "label": "Forward to rescue coordination centre"},
                                       {"id": "task_satellite", "label": "Request another satellite look"},
                                       {"id": "dismiss", "label": "Not a boat in distress"}],
                           "recipients": [{"name": "Rescue coordination centre duty officer", "channel": "email"},
                                          {"name": "NGO search-and-rescue desk", "channel": "push"}],
                           "status": "open", "fires_after": "median capture-to-product latency (53 min)"})
    for i, a in enumerate(alerts, 1):
        a["id"] = f"A-{i:04d}"
    last = epoch(runs[-1]["t"])
    s1m = d["s1"]["mid"]["stats"]
    lat = d["s1_latency_min"]
    max_gap = max(d["s1"][p]["stats"]["max_gap_h"] for p in ("tobruk", "mid", "gavdos"))
    zone = make_zone("Z-1", "Search and rescue corridor (Tobruk → south of Gavdos)", "custom",
                     {"type": "corridor", "line": line, "width_km": 33}, asset_id="corridor",
                     recipients=[{"name": "Rescue coordination centre duty officer", "channel": "email", "address": "",
                                  "min_severity": "medium", "quiet_hours": None}], created=epoch(runs[0]["t"]))
    asset = {"id": "corridor", "alias": "Corridor", "name": "Libya → Crete crossing corridor (Tobruk → south of Gavdos)",
             "kind": "route", "geometry": {"type": "LineString", "coordinates": line}, "owner": "",
             "source": "orbit-abundance route points", "protected": True}
    scenario = {
        "id": "migration", "case": "migration", "purpose": "custom",
        "label": "Libya → Crete corridor · Sentinel-1 strip passes, 3–10 Nov 2026", "start": epoch(runs[0]["t"]) - 6 * 3600,
        "end": last + 8 * 3600, "now": last + 40 * 60, "illustrative": False, "bbox": [22.9, 31.8, 25.2, 35.1],
        "time_mode": "replay", "images": images,
        "sources": [{"label": "Sentinel-1C/1D IW GRD, Microsoft Planetary Computer",
                     "url": "https://planetarycomputer.microsoft.com/dataset/sentinel-1-grd"},
                    {"label": "Paolo et al., Nature 2024 (Sentinel-1 recall vs vessel length)",
                     "url": "https://www.nature.com/articles/s41586-023-06825-8"},
                    {"label": "orbit-abundance pipeline (revisit, latency, strip detector)", "url": ""}],
        "facts": [
            {"label": "Sentinel-1 looks per week, mid-corridor", "value": f"{s1m['per_week']} (60 days)",
             "source": "Planetary Computer STAC record"},
            {"label": "Longest blind gap", "value": f"{max_gap:.0f} h", "source": "Planetary Computer STAC record"},
            {"label": "Median capture-to-product", "value": f"{lat['median']:.0f} min (n={lat['n']})",
             "source": "ESA processing timestamps"},
            {"label": "Modelled: 25 m boats detected", "value": "~27% today → ~95% with 100 wide-swath radar "
             "satellites", "source": "orbit-abundance simulator (modelled)"}],
        "notes": ["Detections are bright objects on water. They are not checked against AIS; length is ±20 m.",
                  "Ships moored within 150 m of the shore are excluded.", "No AIS or RF layer in this case."],
    }
    return {"scenario": scenario, "assets": [asset], "zones": [zone], "vessels": vessels, "events": events,
            "alerts": alerts, "actions": []}


# ---------------------------------------------------------------- war-risk (runtime: depends on live feeds)

T11_SHIP = {"name": "FORMOSA DAWN (illustrative)", "imo": "", "type": "tanker", "hull_usd_m": 100, "flag": "Panama",
            "owner_country": "Taiwan", "nexus_us_uk_il": False, "israel_call": False, "saudi_link": False,
            "ais_on": True, "speed_kn": 13, "port_days": 2}
T11_ROUTE = "rastanura-kaohsiung"
LEVEL_MAP = {"LOW": "low", "ELEVATED": "medium", "HIGH": "high", "SEVERE": "high", "EXTREME": "high"}
FACTOR_SRC = {"attacks": "fusion", "threat": "fusion", "traffic": "ais", "jamming": "rf", "links": "sanctions"}


def _circle(lon, lat, km=6, n=12):
    k = km / 111.0
    ring = [[r5(lon + k * math.cos(2 * math.pi * i / n) / math.cos(math.radians(lat))),
             r5(lat + k * math.sin(2 * math.pi * i / n))] for i in range(n)]
    return {"type": "Polygon", "coordinates": [ring + ring[:1]]}


def build_warrisk():
    from server.real import warrisk as wr
    now = int(time.time()) // 60 * 60
    today = datetime.fromtimestamp(now, timezone.utc).date()
    departs = today + timedelta(days=1)
    a = wr.assess_voyage(T11_SHIP, T11_ROUTE, departs.isoformat())
    route = wr.ROUTES[T11_ROUTE]
    ids = Ids()

    # T-11: alongside at Ras Tanura until departure tomorrow 00:00 UTC, then the planned route at 13 kn
    samples = wr.densify(route["pts"], step_km=20)
    t = day_epoch(departs.isoformat())
    tr = [[now, r5(samples[0][1]), r5(samples[0][0]), 0.0, None]]
    entry = exit_ = None
    for i, (la, lo) in enumerate(samples):
        if i:
            pla, plo = samples[i - 1]
            t += hav_m(plo, pla, lo, la) / (T11_SHIP["speed_kn"] * 0.514444)
        inside = wr.in_poly([la, lo], wr.LISTED_AREA)
        if inside and entry is None:
            entry = (int(t), lo, la)
        if not inside and entry is not None and exit_ is None:
            exit_ = (int(t), lo, la)
        nxt = samples[i + 1] if i + 1 < len(samples) else None
        cog = round(bearing(lo, la, nxt[1], nxt[0])) if nxt else None
        tr.append([int(t), r5(lo), r5(la), float(T11_SHIP["speed_kn"]), cog])
    arrive = tr[-1][0]

    contrib = {}
    have = [f for f in a["factors"] if f["available"] and f["key"] != "links"]
    wsum = sum({"attacks": 0.40, "threat": 0.27, "traffic": 0.20, "jamming": 0.13}[f["key"]] for f in have)
    for f in have:
        contrib[f["key"]] = 75 * {"attacks": 0.40, "threat": 0.27, "traffic": 0.20, "jamming": 0.13}[f["key"]] \
            * f["value"] / wsum
    links = next(f for f in a["factors"] if f["key"] == "links")
    contrib["links"] = 25 * links["value"]
    reasons = [{"text": f"{f['label']}: {f['detail']}", "points": round(contrib.get(f["key"], 0)),
                "source": FACTOR_SRC[f["key"]]} for f in a["factors"]]
    reasons.sort(key=lambda r: -r["points"])

    events, vessels = [], []
    t11_ev = []
    if entry:
        eid = ids("E")
        t11_ev.append(eid)
        events.append({"id": eid, "vessel_id": "T-11", "type": "zone_entry", "t": max(entry[0], now), "end": None,
                       "lon": r5(entry[1]), "lat": r5(entry[2]), "severity": "high",
                       "summary": ("Planned voyage starts inside the JWC listed area (Ras Tanura)." if entry[0] <= tr[1][0]
                                   else "Planned entry into the JWC listed area."),
                       "facts": [f"{a['price']['km_in_area']:,} km and {a['price']['days_in_area']} days in the listed "
                                 f"area", "JWC Listed Areas JWLA-033"],
                       "source": "fusion", "zone_id": "Z-1", "asset_id": None, "other_vessel_id": None,
                       "planned": True})
    if exit_:
        eid = ids("E")
        events.append({"id": eid, "vessel_id": "T-11", "type": "zone_exit", "t": exit_[0], "end": None,
                       "lon": r5(exit_[1]), "lat": r5(exit_[2]), "severity": "low",
                       "summary": "Planned exit from the JWC listed area.", "facts": [], "source": "fusion",
                       "zone_id": "Z-1", "asset_id": None, "other_vessel_id": None, "planned": True})

    near_refs = {i["ref"] for i in a["incidents"]}
    anon = {"Unknown", "Name withheld", "Multiple", "Unidentified"}
    sev = {"Attack": "high", "Enforcement": "medium", "Suspicious": "medium", "Advisory": "low"}
    inc_ev = []
    for k, inc in enumerate(sorted(wr.INCIDENTS, key=lambda i: i["date"]), 1):
        vid = f"V-{k:02d}"
        name = inc["vessel"] if inc["vessel"] not in anon else f"{inc['vessel']} ({inc['ref']})"
        t_inc = day_epoch(inc["date"])
        dist = min(wr.hav_km((inc["lat"], inc["lon"]), s) for s in samples)
        src = wr.incident_source(inc)
        eid = ids("E")
        if inc["ref"] in near_refs and inc["kind"] == "Attack":
            inc_ev.append(eid)
        events.append({"id": eid, "vessel_id": vid, "type": "incident", "kind": inc["kind"], "t": t_inc, "end": None,
                       "lon": r5(inc["lon"]), "lat": r5(inc["lat"]), "severity": sev[inc["kind"]],
                       "summary": f"{inc['kind']}: {name} ({inc['vtype']}). {inc['text']}",
                       "facts": [inc["ref"], inc["region"], f"{dist:.0f} km from the T-11 route"] +
                                (["Position approximate (geocoded from the JMIC narrative)"] if inc["approx"] else []),
                       "source": src["label"], "source_url": src["url"], "time_precision": "day",
                       "zone_id": "Z-1" if wr.in_poly([inc["lat"], inc["lon"]], wr.LISTED_AREA) else None,
                       "asset_id": None, "other_vessel_id": None, "ref": inc["ref"], "approx": inc["approx"]})
        vessels.append(vessel(id=vid, name=name, type=inc["vtype"].lower(),
                              risk={"score": 0, "level": "low", "reasons": [
                                  {"text": f"Subject of a JMIC-reported incident ({inc['ref']}, {inc['date']}); "
                                           f"not scored", "points": 0, "source": "fusion"}]},
                              incident={"ref": inc["ref"], "date": inc["date"], "kind": inc["kind"],
                                        "source": src}))

    t11 = vessel(id="T-11", name=T11_SHIP["name"], flag=T11_SHIP["flag"], type="tanker", length_m=None,
                 illustrative=True, track=tr, claimed=[], states=[{"start": now, "end": arrive, "state": "verified"}],
                 risk={"score": a["score"], "level": LEVEL_MAP[a["level"]], "reasons": reasons,
                       "war_risk_level": a["level"], "rules": "war-risk-copilot assess()"},
                 voyage={"route_id": T11_ROUTE, "label": route["label"], "via": route["via"],
                         "hull_usd_m": T11_SHIP["hull_usd_m"], "departs": departs.isoformat(),
                         "speed_kn": T11_SHIP["speed_kn"], "price": a["price"]},
                 track_note="Planned route at 13 kn from departure; illustrative ship (war-risk-copilot preset VLCC).")
    vessels.insert(0, t11)

    p = a["price"]
    n_att = a["attacks_near_route"]
    traffic = next(f for f in a["factors"] if f["key"] == "traffic")
    facts = [f"Risk {a['score']}/100 ({a['level']}), trend {a['trend']}",
             f"Price band {p['lo_pct']}–{p['hi_pct']}% of hull: ${p['lo_usd'] / 1e6:.1f}M–${p['hi_usd'] / 1e6:.1f}M "
             f"({p['blocks']} × 7-day block; pre-war ${p['prewar_usd'] / 1e6:.2f}M)",
             f"JMIC threat: {a['worst_zone']['name']} {a['worst_zone']['level']}",
             traffic["detail"] if traffic["available"] else
             "Not scored, feeds unavailable: " + ", ".join(a["missing"])]
    alert = {"id": "A-0001", "vessel_id": "T-11", "event_ids": t11_ev + inc_ev, "zone_id": "Z-1", "t": now,
             "severity": "high" if LEVEL_MAP[a["level"]] == "high" else "medium",
             "headline": f"T-11 route crosses JWC listed area with {n_att} attack{'s' if n_att != 1 else ''} within "
                         f"25 NM in 21 days",
             "facts": facts,
             "actions": [{"id": "quote_band", "label": f"Quote at {p['lo_pct']}–{p['hi_pct']}%"},
                         {"id": "refer_underwriter", "label": "Refer to senior underwriter"},
                         {"id": "dismiss", "label": "Decline to quote"}],
             "recipients": [{"name": "War-risk underwriter", "channel": "email"},
                            {"name": "Broker desk", "channel": "push"}],
             "status": "open", "price": {"lo_usd": p["lo_usd"], "hi_usd": p["hi_usd"], "lo_pct": p["lo_pct"],
                                         "hi_pct": p["hi_pct"], "blocks": p["blocks"]}}

    listed = [[r5(lo), r5(la)] for la, lo in wr.LISTED_AREA]
    gulf_line = [[lo, la] for la, lo in route["pts"][:9]]
    zones = [make_zone("Z-1", "JWC listed area (JWLA-033)", "insurance", {"type": "polygon", "coordinates": listed},
                       recipients=[{"name": "War-risk underwriter", "channel": "email", "address": "",
                                    "min_severity": "medium", "quiet_hours": None}], created=now),
             make_zone("Z-2", "T-11 route ±25 NM (Gulf and Gulf of Oman)", "insurance",
                       {"type": "corridor", "line": gulf_line, "width_km": 92.6}, created=now)]
    overlays = [{"id": "jmic-incidents", "kind": "incidents", "label": "JMIC incidents, 6 km marks",
                 "geojson": {"type": "FeatureCollection", "features": [
                     {"type": "Feature", "geometry": _circle(i["lon"], i["lat"]),
                      "properties": {"ref": i["ref"], "kind": i["kind"], "date": i["date"]}} for i in wr.INCIDENTS]}}]
    jam = wr.jamming_geojson()
    if jam:
        overlays.insert(0, {"id": "gpsjam", "kind": "jamming", "label": f"GNSS interference, gpsjam {jam['properties']['day']}",
                            "geojson": jam})
    st = wr.EV["JMIC_STATS"]
    rates = wr.RATES
    scenario = {
        "id": "war-risk", "case": "war-risk", "purpose": "insurance",
        "label": "Hormuz transit · tanker T-11 (illustrative) · live war-risk evidence",
        "start": day_epoch((today - timedelta(days=21)).isoformat()), "end": arrive, "now": now,
        "illustrative": False, "bbox": [47.5, 21.5, 61.5, 30.8], "time_mode": "static", "overlays": overlays,
        "sources": [{"label": f"JMIC Advisory Update {k}", "url": u} for k, u in sorted(wr.JMIC.items())] +
                   [{"label": wr.SOURCES[k][0], "url": wr.SOURCES[k][1]} for k in ("jwla033", "portwatch", "gpsjam", "ofac")] +
                   [{"label": rates["hormuz_tanker"]["src"], "url": rates["hormuz_tanker"]["url"]},
                    {"label": rates["prewar"]["src"], "url": rates["prewar"]["url"]}],
        "facts": [
            {"label": "Attacks within 25 NM of the route, 21 days", "value": str(n_att),
             "source": "JMIC Advisory Updates 109–112"},
            {"label": "Hormuz tanker war cover", "value": f"{rates['hormuz_tanker']['lo']:g}–"
             f"{rates['hormuz_tanker']['hi']:g}% of hull per transit (pre-war ~{rates['prewar']['pct']}%)",
             "source": "Brokers via Insurance Business, 14 Oct 2026; Marsh"},
            {"label": "Incidents since 1 Mar 2026", "value": str(st["incidents_since_mar1"]),
             "source": "JMIC Advisory Update 112"},
            {"label": "GNSS jamming", "value": "154 of 7,100 vessels in the Gulf and Gulf of Oman jammed on 5 Nov",
             "source": "Windward via JMIC Advisory Update 112"}],
        "feeds": a["feeds"], "missing_feeds": a["missing"], "assessment": {k: a[k] for k in (
            "score", "level", "factors", "price", "trend", "traffic", "attacks_near_route", "worst_zone", "route",
            "as_of", "missing", "sources")},
        "notes": ["T-11 / FORMOSA DAWN is an illustrative ship; the incidents, listed area, rates and feeds are real.",
                  "Incident positions are geocoded from the JMIC narrative; some are approximate.",
                  "Incident ships have no track: they appear as events and as marks on the map."],
    }
    return {"scenario": scenario, "assets": [], "zones": zones, "vessels": vessels, "events": events,
            "alerts": [alert], "actions": []}


# ---------------------------------------------------------------- cache + entry points

BUILDERS = {"baltic-2024": build_baltic, "dark-skagen": build_skagen, "war-risk": build_warrisk}
# build_taiwan and build_migration work but are cut from the demo (not listed in CASES or BUILDERS).
_cache: dict[str, dict] = {}
_lock = threading.Lock()
WARRISK_TTL = 600


def _dump(w):
    return json.dumps(w, separators=(",", ":"), ensure_ascii=False)


def build(case_id: str, save=True) -> dict:
    w = BUILDERS[case_id]()
    if save and case_id in STATIC_CASES:
        REAL.mkdir(parents=True, exist_ok=True)
        (REAL / f"{case_id}.json").write_text(_dump(w))
    return w


def get_world(case_id: str) -> dict:
    """Full World for a case id (a fresh copy each call). Raises KeyError for an unknown case."""
    if case_id == "cable":
        p = ROOT / "data" / "world.json"
        if not p.exists():
            raise KeyError("cable world not built yet (server.seed)")
        return json.loads(p.read_text())
    if case_id not in BUILDERS:
        raise KeyError(f"unknown case {case_id!r}; known: {', '.join(c['id'] for c in CASES)}")
    with _lock:
        c = _cache.get(case_id)
        if case_id == "war-risk":
            from server.real import warrisk as wr
            wr.ensure_started()
            feeds = tuple(sorted(k for k, s in wr.feed_status().items() if s["ok"]))
            if c is None or time.time() - c["at"] > WARRISK_TTL or c.get("feeds") != feeds:
                c = _cache[case_id] = {"s": _dump(build_warrisk()), "at": time.time(), "feeds": feeds}
        elif c is None:
            p = REAL / f"{case_id}.json"
            s = p.read_text() if p.exists() else _dump(build(case_id))
            c = _cache[case_id] = {"s": s, "at": time.time()}
    return apply_presets(json.loads(c["s"]))


def cases() -> list[dict]:
    return [dict(c) for c in CASES]


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "build":
        for cid in args[1:] or STATIC_CASES:
            t = time.time()
            w = build(cid)
            print(f"{cid}: {len(w['vessels'])} vessels, {len(w['events'])} events, {len(w['alerts'])} alerts, "
                  f"{len(_dump(w)) / 1e6:.2f} MB in {time.time() - t:.1f}s")
    else:
        print("usage: python -m server.real.cases build [case ...]")
