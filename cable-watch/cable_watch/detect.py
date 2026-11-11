"""Score one vessel's AIS track for behaviour that matches past cable-damage incidents.

The rules come from the published cases:
- Eagle S (Estlink 2, 25 Dec 2024): speed and engine revolutions dropped while the anchor dragged ~90 km
  (Helsinki District Court judgment).
- Hong Tai 58 (TP3, 25 Feb 2025): anchored 0.5 nmi from the cable for ~2.5 days, then zigzagged over it
  (Tainan District Prosecutors Office indictment).
- Yi Peng 3 (BCS / C-Lion1, Nov 2024): slowed near the cables and switched AIS off for hours (Kpler / Wikipedia).
- Shunxin-39 (TPE, Jan 2025) and many others: sanctioned or flag-hopping ships (OpenSanctions as a proxy).

Every signal carries a plain-language reason, so a human can check the score.
"""

from dataclasses import dataclass, field

import numpy as np
import shapely

from .geo import geo, project

NEAR_M = 1_000         # "over the cable"
LAND_M = 1_500         # closer than this to land counts as harbour / coastal traffic and is not scored
GAP_S = 45 * 60        # an AIS silence longer than this is a gap
SERVICE_TYPES = {"pilot", "sar", "law enforcement", "military", "tug", "port tender", "diving",
                 "anti-pollution", "medical", "towing", "towing long/wide", "dredging",
                 # the same, as AIS numeric ship types (Digitraffic)
                 "31", "32", "33", "35", "50", "51", "52", "53", "54", "55", "58"}


@dataclass
class Track:
    mmsi: int
    t: np.ndarray        # epoch seconds, sorted
    lon: np.ndarray
    lat: np.ndarray
    sog: np.ndarray      # knots, nan when unknown
    cog: np.ndarray      # degrees, nan when unknown
    anchored: np.ndarray  # bool, navigational status "at anchor" or "moored"
    name: str = ""
    imo: str = ""
    ship_type: str = ""
    length: float = float("nan")


@dataclass
class Result:
    mmsi: int
    score: int = 0
    signals: list = field(default_factory=list)   # {kind, points, reason, start, end, lon, lat, cable}
    min_cable_m: float = float("inf")
    crossings: int = 0


def _runs(mask):
    """Start/end index pairs (inclusive) of consecutive True values."""
    if not mask.any():
        return []
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1), np.flatnonzero(d == -1) - 1))


def _dur(t, i, j):
    return float(t[j] - t[i])


def score(tr: Track, watchlist: dict | None = None, nearby=None) -> Result:
    """nearby(lon, lat, start, end, mmsi) -> how many other ships reported within 10 km in that window.
    It tells a ship that went quiet apart from a hole in the shore receivers' coverage."""
    g = geo()
    r = Result(tr.mmsi)
    if tr.ship_type.lower() in SERVICE_TYPES or len(tr.t) < 2:
        return r
    x, y = project(tr.lon, tr.lat)
    dist, cid = g.nearest_cable(x, y)
    r.min_cable_m = float(dist.min())
    if r.min_cable_m > 20_000:
        return r
    coastal = g.near_land(x, y, LAND_M)
    name = lambda i: g.cables[cid[i]]["name"] if cid[i] >= 0 else "海纜"
    sog = np.where(np.isnan(tr.sog), -1, tr.sog)

    def add(kind, points, reason, i, j, c=None):
        k = (i + j) // 2
        r.signals.append({"kind": kind, "points": points, "reason": reason, "start": int(tr.t[i]), "end": int(tr.t[j]),
                          "lon": round(float(tr.lon[k]), 5), "lat": round(float(tr.lat[k]), 5),
                          "cable": c or name(int(np.argmin(dist[i:j + 1])) + i)})

    over = (dist < NEAR_M) & ~coastal

    # 1. Stopped or at anchor over a cable.
    stopped = over & ((sog >= 0) & (sog < 0.5) | tr.anchored)
    for i, j in _runs(stopped):
        if _dur(tr.t, i, j) >= 30 * 60:
            h = _dur(tr.t, i, j) / 3600
            pts, why = (30 if h < 6 else 40), f"在海纜上方 1 公里內停留或下錨 {h:.1f} 小時"
            k = (i + j) // 2
            n = nearby(float(tr.lon[k]), float(tr.lat[k]), float(tr.t[i]), float(tr.t[j]), tr.mmsi,
                       radius_m=5_000, stopped=True) if nearby else 0
            if n >= 2:  # other ships parked nearby too: a designated anchorage, where lying over a cable is routine
                pts, why = 10, why + f"；但附近 5 公里內還有 {n} 艘船也停著，比較像是錨地"
            add("stopped", pts, why, i, j)

    # 2. Slow over a cable (loitering speed, not stopped).
    slow = over & (sog >= 0.5) & (sog < 5) & ~tr.anchored
    for i, j in _runs(slow):
        m = _dur(tr.t, i, j) / 60
        if m >= 20:
            why = f"在海纜上方 1 公里內以平均 {np.mean(sog[i:j + 1]):.1f} 節低速航行 {m:.0f} 分鐘"
            if tr.ship_type.lower() in ("fishing", "30"):
                why += "（漁船，可能是拖網作業；拖網也會弄壞海纜，但多半不是蓄意）"
            add("slow", 25 if m < 60 else 35, why, i, j)

    # 3. Zigzag: repeated course reversals near a cable while moving.
    # Away from land only: ferries and harbour traffic turn around near the cable landings.
    offshore = ~g.near_land(x, y, 3_000)
    moving = (dist < 2 * NEAR_M) & offshore & (sog > 2) & ~np.isnan(tr.cog)
    idx = np.flatnonzero(moving)
    if len(idx) > 3:
        dc = np.abs((np.diff(tr.cog[idx]) + 180) % 360 - 180)
        # Only turns between neighbouring fixes count: two passes in opposite directions are not a zigzag.
        consecutive = (np.diff(idx) == 1) & (np.diff(tr.t[idx]) < 15 * 60)
        rev = idx[1:][(dc > 100) & consecutive]
        if len(rev) >= 3:
            add("zigzag", 20, f"在海纜 2 公里內反覆掉頭 {len(rev)} 次（Z 字形航線）", int(rev[0]), int(rev[-1]))

    # 4. Unexplained speed drop at sea near a cable (anchor-drag signature: Eagle S kept going, slower).
    under_way = sog[(sog > 1) & ~coastal]
    if len(under_way) > 20 and np.median(under_way) > 6:
        med = float(np.median(under_way))
        drop = (sog > 1) & (sog < 0.6 * med) & ~coastal & ~tr.anchored
        for i, j in _runs(drop):
            if _dur(tr.t, i, j) >= 30 * 60 and dist[i:j + 1].min() < 3_000:
                add("speed_drop", 25, f"航行中速度從平常的 {med:.1f} 節掉到 {np.mean(sog[i:j + 1]):.1f} 節，持續 "
                    f"{_dur(tr.t, i, j) / 60:.0f} 分鐘，期間經過海纜 3 公里內（拖錨時會出現這種減速）", i, j)

    # 5. AIS gaps that start, end or pass over a cable.
    for i in np.flatnonzero(np.diff(tr.t) > GAP_S):
        j = i + 1
        crossed = g.segment_crosses(x[i], y[i], x[j], y[j])
        if crossed < 0 and min(dist[i], dist[j]) > 10_000:
            continue
        if coastal[i] and coastal[j]:
            continue
        h = _dur(tr.t, i, j) / 3600
        km = float(np.hypot(x[j] - x[i], y[j] - y[i])) / 1000
        implied = km / 1.852 / h
        before = sog[max(0, i - 5):i + 1]
        before = float(np.median(before[before >= 0])) if (before >= 0).any() else float("nan")
        where = f"中斷前後的位置連線跨過 {g.cables[crossed]['name']}" if crossed >= 0 else "中斷點離海纜 10 公里內"
        pts = 25
        reason = f"AIS 訊號中斷 {h:.1f} 小時，{where}"
        if before > 3 and implied < 0.6 * before:
            pts += 10
            reason += f"；中斷期間平均只走 {implied:.1f} 節（中斷前 {before:.1f} 節），表示船在中斷時減速或停下"
        if nearby:
            n = nearby(float(tr.lon[i]), float(tr.lat[i]), float(tr.t[i]), float(min(tr.t[j], tr.t[i] + 7200)), tr.mmsi)
            if n == 0:
                pts = 10
                reason += "；但同一時間附近 10 公里內也沒有別的船有訊號，比較可能是岸台收不到（收訊死角）"
            else:
                pts += 5
                reason += f"；同一時間附近 10 公里內有 {n} 艘船照常發訊號，所以不是收訊死角"
        add("ais_gap", pts, reason, i, j, g.cables[crossed]["name"] if crossed >= 0 else None)

    # Cable crossings, for context.
    step = np.flatnonzero((np.diff(tr.t) <= GAP_S) & (np.minimum(dist[:-1], dist[1:]) < 3_000))
    if len(step):
        segs = shapely.linestrings(np.stack([np.column_stack([x[step], y[step]]),
                                             np.column_stack([x[step + 1], y[step + 1]])], axis=1))
        r.crossings = len(np.unique(g.tree.query(segs, predicate="intersects")[0]))

    # 6. Watchlist (sanctions, shadow fleet, port-state detentions).
    if watchlist:
        hit = watchlist["imo"].get(str(tr.imo)) or watchlist["mmsi"].get(str(tr.mmsi))
        if hit and r.signals:
            tags = {"sanction": "制裁名單", "mare.shadow": "影子船隊", "mare.detained": "曾被港口國扣留"}
            label = "、".join(tags[t] for t in hit["tags"] if t in tags)
            r.signals.append({"kind": "watchlist", "points": 20, "reason": f"OpenSanctions 列為：{label}（{hit['name']}）",
                              "start": int(tr.t[0]), "end": int(tr.t[-1]), "lon": None, "lat": None, "cable": None})

    # Same kind counts once (its strongest instance), so a long track does not pile up points.
    best = {}
    for s in r.signals:
        best[s["kind"]] = max(best.get(s["kind"], 0), s["points"])
    r.score = min(100, sum(best.values()))
    r.signals.sort(key=lambda s: s["start"])
    return r


def tier(score: int) -> str:
    return "high" if score >= 60 else "medium" if score >= 40 else "low" if score > 0 else "none"
