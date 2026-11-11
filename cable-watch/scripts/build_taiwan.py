"""Score Taiwan Strait ships from Global Fishing Watch and save data/taiwan-gfw.json.

Taiwan has no open AIS archive like Denmark's, so this uses GFW (needs GFW_API_TOKEN):
- loitering events of non-fishing ships (cargo, tanker, bunker, passenger, other): where a ship sat
  still or crawled, from when to when, at what average speed;
- Sentinel-1 ship detections that matched no AIS ship ("dark" ships).
Each loitering event is scored against the TeleGeography cable routes with the same rules as the replay.

Run: uv run python scripts/build_taiwan.py 2026-09-01 2026-11-06
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import shapely

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cable_watch import gfw  # noqa: E402
from cable_watch.detect import tier  # noqa: E402
from cable_watch.geo import geo, project  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
BBOX = (117.5, 21.5, 123.0, 26.8)  # Taiwan Strait, Matsu, Kinmen, Penghu and the east coast
NON_FISHING = ["CARGO", "CARRIER", "BUNKER", "OTHER", "NON_FISHING", "PASSENGER"]
# Flags of convenience used by the ships in Taiwan's cable cases: Hong Tai 58 (Togo), Shunxin-39 (Cameroon),
# Xingshun 39 (Tanzania) (Taiwan Coast Guard; IAI 2026).
CASE_FLAGS = {"TGO": "多哥", "CMR": "喀麥隆", "TZA": "坦尚尼亞"}
TYPE = {"cargo": "Cargo", "passenger": "Passenger", "bunker": "Tanker", "carrier": "Cargo"}


def epoch(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def loitering(start, end):
    c, out, off = gfw._client(), [], 0
    while off is not None:
        r = c.post("/events", params={"offset": off, "limit": 200}, json={
            "datasets": [gfw.DATASETS["loitering"]], "startDate": start, "endDate": end,
            "geometry": gfw._polygon(BBOX), "vesselTypes": NON_FISHING})
        r.raise_for_status()
        d = r.json()
        out += d["entries"]
        off = d.get("nextOffset")
    return out


def box_of(e):
    """GFW's bounding box of an event (the corners come in either order) as [w, s, e, n]."""
    b = [float(v) for v in e.get("boundingBox") or []]
    if len(b) != 4:
        return None
    return [round(min(b[0], b[2]), 5), round(min(b[1], b[3]), 5), round(max(b[0], b[2]), 5), round(max(b[1], b[3]), 5)]


def crossed(g, b):
    """Name of a cable that the event's bounding box overlaps, or None."""
    if not b:
        return None
    xs, ys = project([b[0], b[2], b[2], b[0]], [b[1], b[1], b[3], b[3]])
    hit = g.tree.query(shapely.polygons(np.column_stack([xs, ys])), predicate="intersects")
    return g.cables[g.owner[hit[0]]]["name"] if len(hit) else None


def itinerary(vid, start, end):
    """The ship's port visits, loitering and encounters in time order: the closest thing to a track GFW serves
    while its per-vessel presence endpoint is paused."""
    r = gfw._client().get("/events", params={
        "vessels[0]": vid, "datasets[0]": gfw.DATASETS["loitering"], "datasets[1]": "public-global-port-visits-events:latest",
        "datasets[2]": "public-global-encounters-events:latest", "start-date": start, "end-date": end, "limit": 200, "offset": 0})
    r.raise_for_status()
    out = []
    for e in sorted(r.json()["entries"], key=lambda e: e["start"]):
        pv = e.get("port_visit") or {}
        port = (pv.get("intermediateAnchorage") or pv.get("startAnchorage") or {}).get("name")
        out.append({"type": e["type"], "start": epoch(e["start"]), "end": epoch(e["end"]),
                    "lon": round(e["position"]["lon"], 5), "lat": round(e["position"]["lat"], 5), "box": box_of(e),
                    "port": port.title() if port else None,
                    "knots": round(e["loitering"]["averageSpeedKnots"], 1) if e.get("loitering") else None})
    return out


def main(start, end):
    g = geo("taiwan")
    wl = json.loads((DATA / "watchlist.json").read_text())
    ev = [e for e in loitering(start, end) if e["vessel"].get("ssvid")]
    lon = np.array([e["position"]["lon"] for e in ev], float)
    lat = np.array([e["position"]["lat"] for e in ev], float)
    x, y = project(lon, lat)
    dist, cid = g.nearest_cable(x, y)
    coastal = g.near_land(x, y, 1_500)
    t0 = np.array([epoch(e["start"]) for e in ev])
    t1 = np.array([epoch(e["end"]) for e in ev])

    ships = {}
    for k, e in enumerate(ev):
        v = e["vessel"]
        s = ships.setdefault(v["ssvid"], {"mmsi": int(v["ssvid"]), "name": v.get("name") or "", "flag": v.get("flag") or "",
                                          "type": TYPE.get(v.get("type"), "Other"), "imo": "", "signals": [], "near": False,
                                          "crossings": 0, "gfw_id": v["id"]})
        if dist[k] > 3_000 or coastal[k]:
            s["near"] |= dist[k] < 20_000
            continue
        s["near"] = True
        lo = e["loitering"]
        h, kn = lo["totalTimeHours"], lo["averageSpeedKnots"]
        cable = g.cables[cid[k]]["name"]
        # Other ships loitering within 5 km at the same time: a designated anchorage.
        others = np.hypot(x - x[k], y - y[k]) < 5_000
        others &= (t0 < t1[k]) & (t1 > t0[k])
        n = len({ev[j]["vessel"]["ssvid"] for j in np.flatnonzero(others)} - {v["ssvid"]})
        where = f"海纜 {dist[k] / 1000:.1f} 公里內" if dist[k] >= 1_000 else "海纜上方 1 公里內"
        if kn < 0.5:
            kind, pts = "stopped", (40 if h >= 6 else 30)
            why = f"在{where}停留或下錨 {h:.1f} 小時（平均 {kn:.1f} 節）"
        else:
            kind, pts = "slow", (35 if h >= 1 else 25)
            why = f"在{where}以平均 {kn:.1f} 節低速徘徊 {h:.1f} 小時"
        if dist[k] >= 1_000:
            pts -= 10
        if n >= 2:
            pts, why = 10, why + f"；但同一時間 5 公里內還有 {n} 艘船也停著，比較像是錨地"
        b = box_of(e)
        over = crossed(g, b)
        if over:
            why += f"；停留期間的活動範圍和 {over} 重疊"
            cable = over
        s["signals"].append({"kind": kind, "points": pts, "reason": why, "start": int(t0[k]), "end": int(t1[k]),
                             "lon": round(float(lon[k]), 5), "lat": round(float(lat[k]), 5), "cable": cable, "box": b})

    out = []
    for s in ships.values():
        if not s["signals"]:
            continue
        hit = wl["mmsi"].get(str(s["mmsi"]))
        if hit:
            tags = {"sanction": "制裁名單", "mare.shadow": "影子船隊", "mare.detained": "曾被港口國扣留"}
            s["signals"].append({"kind": "watchlist", "points": 20, "start": s["signals"][0]["start"], "end": s["signals"][0]["end"],
                                 "reason": f"OpenSanctions 列為：{'、'.join(tags[t] for t in hit['tags'] if t in tags)}（{hit['name']}）",
                                 "lon": None, "lat": None, "cable": None})
        if s["flag"] in CASE_FLAGS:
            s["signals"].append({"kind": "flag", "points": 15, "start": s["signals"][0]["start"], "end": s["signals"][0]["end"],
                                 "reason": f"船旗是{CASE_FLAGS[s['flag']]}：和宏泰58（多哥）、順興39（喀麥隆、坦尚尼亞）同一類權宜船旗",
                                 "lon": None, "lat": None, "cable": None})
        best = {}
        for sg in s["signals"]:
            best[sg["kind"]] = max(best.get(sg["kind"], 0), sg["points"])
        s["score"] = min(100, sum(best.values()))
        s["tier"] = tier(s["score"])
        s["signals"].sort(key=lambda sg: sg["start"])
        top = max((sg for sg in s["signals"] if sg["lon"] is not None), key=lambda sg: sg["points"])
        s["last"] = [top["lon"], top["lat"], top["end"]]
        out.append(s)
    out.sort(key=lambda s: -s["score"])
    for s in out:
        if s["score"] >= 40:
            s["path"] = itinerary(s["gfw_id"], start, end)

    # Sentinel-1 ships with no AIS, and which of them lay within 2 km of a cable.
    sar = gfw.dark_detections(start, end, BBOX)
    sx, sy = project([r["lon"] for r in sar], [r["lat"] for r in sar])
    sd, _ = g.nearest_cable(sx, sy)
    sland = g.near_land(sx, sy, 1_500)
    sar_out = [[r["lon"], r["lat"], r["date"], int(r.get("detections", 1)), bool(d < 2_000 and not l)]
               for r, d, l in zip(sar, sd, sland)]

    summary = {"start": start, "end": end, "events": len(ev), "ships": len(ships),
               "near_cable": sum(s["near"] for s in ships.values()), "flagged": len(out),
               "medium": sum(s["tier"] == "medium" for s in out), "high": sum(s["tier"] == "high" for s in out),
               "sar_dark": sum(r[3] for r in sar_out), "sar_near": sum(r[3] for r in sar_out if r[4]),
               "sar_days": len({r[2] for r in sar_out})}
    (DATA / "taiwan-gfw.json").write_text(json.dumps({"summary": summary, "vessels": out, "sar": sar_out},
                                                          ensure_ascii=False, default=lambda o: o.item()))
    print(summary)
    for s in out[:12]:
        print(s["score"], s["mmsi"], s["name"], s["flag"], s["type"], [sg["reason"][:60] for sg in s["signals"]][:2])


if __name__ == "__main__":
    main(*sys.argv[1:3])
