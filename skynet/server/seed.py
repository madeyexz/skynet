"""Deterministic seeded world for the default `cable` use case.

Gulf of Finland, 2026-11-11 00:00-08:00 UTC. Inspired by the December 2024 Estlink 2 cable damage,
but every vessel, name, MMSI and IMO here is illustrative. `python -m server.seed` writes data/world.json.

The seed only writes the three space layers (claimed AIS, observed SAR, emitted RF) and the fused track.
Everything derived (states, dark periods, events, risk, alerts) comes from fusion.fuse().
"""
import copy
import json
import math
from pathlib import Path

import numpy as np
import shapely
from pyproj import Geod, Transformer
from shapely.geometry import LineString, shape
from shapely.ops import substring

from . import fusion
from .zones import PRESETS

ROOT = Path(__file__).resolve().parent.parent
CW_DATA = ROOT.parent / "cable-watch" / "data"
OUT = ROOT / "data" / "world.json"

START, END, NOW = 1794355200, 1794384000, 1794382200
STEP = 120
BBOX = [22.5, 59.2, 28.5, 60.6]
KN = 1852 / 3600          # metres per second in one knot
DAY = 86400
GEOD = Geod(ellps="WGS84")
T = np.arange(START, END + 1, STEP)


def hm(h, m=0):
    return START + h * 3600 + m * 60


class Proj:
    def __init__(self, crs):
        self.f = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        self.i = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)

    def xy(self, pts):
        a = np.asarray(pts, float).reshape(-1, 2)
        x, y = self.f.transform(a[:, 0], a[:, 1])
        return np.column_stack([x, y])

    def ll(self, x, y):
        lon, lat = self.i.transform(np.asarray(x, float), np.asarray(y, float))
        return np.asarray(lon), np.asarray(lat)

    def line(self, pts, offset=0.0):
        ls = LineString(self.xy(pts))
        return ls.offset_curve(offset) if offset else ls


P = Proj("EPSG:3035")
P_GULF = Proj("+proj=laea +lat_0=26.7 +lon_0=50.3 +units=m")   # Arabian Gulf, for T-11


class Mover:
    """Keyframes (t, x, y) in metres; sampled onto the 2-minute grid afterwards."""

    def __init__(self, t0, xy):
        xy = np.asarray(xy, float).reshape(-1)
        self.k = [(float(t0), float(xy[0]), float(xy[1]))]

    @property
    def t(self):
        return self.k[-1][0]

    @property
    def pos(self):
        return np.array(self.k[-1][1:])

    def go(self, pts, kn):
        coords = list(pts.coords) if isinstance(pts, LineString) else list(np.asarray(pts, float).reshape(-1, 2))
        for x, y in coords:
            t, x0, y0 = self.k[-1]
            d = math.hypot(x - x0, y - y0)
            if d > 1:
                self.k.append((t + d / (kn * KN), float(x), float(y)))
        return self

    def ramp(self, line, kn0, kn1, step_m=150):
        """Move along `line` (which starts at the current position) with speed changing linearly."""
        L = line.length
        n = max(1, int(L / step_m))
        for i in range(1, n + 1):
            s = L * i / n
            v = kn0 + (kn1 - kn0) * (s - L / (2 * n)) / L
            p = line.interpolate(s)
            t = self.k[-1][0] + (L / n) / (max(v, 0.05) * KN)
            self.k.append((t, p.x, p.y))
        return self

    def wait(self, until, rng, r=80.0, period=5 * 3600):
        """Lie at anchor or drift on the spot: a slow swing around the anchor point."""
        t, x, y = self.k[-1]
        ph = rng.uniform(0, 2 * math.pi)
        cx, cy = x - r * math.cos(ph), y - r * math.sin(ph)
        t0 = t
        while t + STEP <= until:
            t += STEP
            a = ph + 2 * math.pi * (t - t0) / period
            self.k.append((t, cx + r * math.cos(a) + rng.normal(0, 4), cy + r * math.sin(a) + rng.normal(0, 4)))
        if t < until:
            self.k.append((until, self.k[-1][1], self.k[-1][2]))
        return self

    def drift(self, until, kn, bearing_deg, rng, wobble=4.0):
        """Steady drift (engines stopped, wind and current) on an almost constant heading."""
        t, x, y = self.k[-1]
        while t + STEP <= until:
            t += STEP
            b = math.radians(bearing_deg + rng.normal(0, wobble))
            v = kn * (1 + rng.normal(0, 0.08)) * KN * STEP
            x, y = x + v * math.sin(b), y + v * math.cos(b)
            self.k.append((t, x, y))
        return self

    def steps(self, vecs):
        """Explicit 2-minute displacement vectors (metres)."""
        t, x, y = self.k[-1]
        for dx, dy in vecs:
            t, x, y = t + STEP, x + dx, y + dy
            self.k.append((t, x, y))
        return self

    def sample(self, proj, t=T):
        k = np.array(self.k)
        x = np.interp(t, k[:, 0], k[:, 1])
        y = np.interp(t, k[:, 0], k[:, 2])
        return proj.ll(x, y)


def kinematics(lon, lat):
    az, _, d = GEOD.inv(lon[:-1], lat[:-1], lon[1:], lat[1:])
    sog = np.append(d / STEP / KN, d[-1] / STEP / KN)
    cog = np.append(az % 360, az[-1] % 360)
    return sog, cog


def rows(t, lon, lat, sog, cog):
    return [[int(a), round(float(b), 5), round(float(c), 5), round(float(d), 1), int(round(float(e))) % 360]
            for a, b, c, d, e in zip(t, lon, lat, sog, cog)]


def imo_number(rng):
    d = [9] + [int(x) for x in rng.integers(0, 10, 5)]
    d[1] = max(d[1], 1)
    c = sum(a * w for a, w in zip(d, [7, 6, 5, 4, 3, 2])) % 10
    return "".join(map(str, d)) + str(c)


MID = {"Finland": 230, "Estonia": 276, "Malta": 248, "Liberia": 636, "Marshall Islands": 538, "Panama": 352,
       "Antigua and Barbuda": 305, "Cyprus": 209, "Netherlands": 244, "Germany": 211, "Russia": 273,
       "Sweden": 265, "Norway": 257, "Bahamas": 311, "Portugal": 263, "Hong Kong": 477, "Singapore": 563,
       "Greece": 240, "Cook Islands": 518, "Cameroon": 613, "Gabon": 626, "Comoros": 616, "Palau": 511,
       "Sierra Leone": 667, "Tanzania": 677, "Togo": 671, "Denmark": 219}


def mmsi_for(flag, rng):
    return int(f"{MID[flag]}{int(rng.integers(100000, 999999)):06d}")


# ---------------------------------------------------------------- geography (approximate, at sea)

WB = [(28.6, 60.12), (27.8, 60.15), (27.1, 60.18), (26.8, 60.02), (26.2, 59.96), (25.5, 59.93), (24.6, 59.81),
      (24.0, 59.71), (23.3, 59.58), (22.4, 59.50)]
EB = [(22.4, 59.47), (23.3, 59.55), (24.0, 59.68), (24.6, 59.78), (25.5, 59.90), (26.2, 59.93), (26.8, 59.97),
      (27.05, 59.92), (27.4, 59.95), (27.8, 60.05), (28.6, 60.06)]
UST_LUGA_IN = [(27.4, 59.95), (28.0, 59.93), (28.25, 59.85), (28.28, 59.80)]
KOTKA_OUT = [(26.9, 60.30), (26.75, 60.10), (26.6, 60.00), (26.2, 59.96)]
HEL_STO = [(22.4, 59.62), (23.5, 59.72), (24.6, 59.93), (24.95, 60.10), (24.95, 60.125)]
FERRY_A = [(24.95, 60.13), (24.95, 60.05), (24.85, 59.75), (24.70, 59.60), (24.74, 59.48), (24.75, 59.465)]
FERRY_B = [(24.97, 60.12), (24.98, 60.05), (24.88, 59.75), (24.73, 59.60), (24.77, 59.48), (24.78, 59.47)]

CABLE_A = [(25.55, 60.35), (25.62, 60.27), (25.72, 60.20), (25.85, 60.10), (26.05, 59.95), (26.30, 59.80),
           (26.55, 59.66), (26.80, 59.53), (26.95, 59.46), (27.05, 59.36)]

ANCHORAGES = [
    {"id": "hel-roads", "name": "Helsinki roads", "kind": "anchorage",
     "coordinates": [[24.85, 60.03], [25.12, 60.03], [25.12, 60.11], [24.85, 60.11]]},
    {"id": "hel-port", "name": "Helsinki port approach", "kind": "port",
     "coordinates": [[24.88, 60.11], [25.02, 60.11], [25.02, 60.18], [24.88, 60.18]]},
    {"id": "tal-roads", "name": "Tallinn roads", "kind": "anchorage",
     "coordinates": [[24.55, 59.47], [24.73, 59.47], [24.73, 59.56], [24.55, 59.56]]},
    {"id": "tal-port", "name": "Tallinn port approach", "kind": "port",
     "coordinates": [[24.72, 59.44], [24.83, 59.44], [24.83, 59.49], [24.72, 59.49]]},
    {"id": "kotka-approach", "name": "Kotka approach", "kind": "port",
     "coordinates": [[26.80, 60.25], [27.02, 60.25], [27.02, 60.42], [26.80, 60.42]]},
    {"id": "ust-luga-roads", "name": "Ust-Luga roads", "kind": "anchorage",
     "coordinates": [[28.12, 59.70], [28.42, 59.70], [28.42, 59.86], [28.12, 59.86]]},
    {"id": "ras-tanura", "name": "Ras Tanura anchorage", "kind": "anchorage",
     "coordinates": [[50.20, 26.60], [50.50, 26.60], [50.50, 26.85], [50.20, 26.85]]},
]

CONTEXT_CABLES = [
    ("finland-estonia-connection-1-fec-1", "fec-1", "FEC-1", "telecom"),
    ("finland-estonia-connection-2-fec-2", "fec-2", "FEC-2", "telecom"),
    ("finland-estonia-2-eesf-2", "eesf-2", "EESF-2", "telecom"),
    ("finland-estonia-3-eesf-3", "eesf-3", "EESF-3", "telecom"),
    ("c-lion1", "c-lion1", "C-Lion1", "telecom"),
    ("pipe-85", "balticconnector", "Balticconnector", "gas"),
    ("bcs-north---phase-1", "bcs-north-1", "BCS North - Phase 1", "telecom"),
]


def assets():
    out = [{"id": "cable-a", "alias": "Cable A", "name": "Estlink 2 (approx. route)", "kind": "power",
            "geometry": {"type": "LineString", "coordinates": [list(p) for p in CABLE_A]},
            "owner": "Grid operator (illustrative)", "source": "approximate", "protected": True}]
    fc = json.loads((CW_DATA / "cables.geojson").read_text())
    by_id = {f["properties"]["id"]: f for f in fc["features"]}
    for src, aid, alias, kind in CONTEXT_CABLES:
        f = by_id.get(src)
        if not f:
            continue
        out.append({"id": aid, "alias": alias, "name": f["properties"]["name"], "kind": kind,
                    "geometry": f["geometry"], "owner": None, "source": "TeleGeography via cable-watch",
                    "protected": False})
    return out


def zone_z1():
    p = copy.deepcopy(PRESETS["cable"])
    return {"id": "Z-1", "name": "Cable A corridor", "purpose": "cable",
            "geometry": {"type": "corridor", "line": [list(c) for c in CABLE_A], "width_km": 4},
            "asset_id": "cable-a", "rules": p["rules"], "metrics": p["metrics"],
            "recipients": [{"name": "Coast guard duty officer", "channel": "push", "address": "",
                            "min_severity": "medium", "quiet_hours": None},
                           {"name": "Grid operator control room", "channel": "email",
                            "address": "control-room@grid.example", "min_severity": "medium",
                            "quiet_hours": None}],
            "created": START - 30 * DAY, "created_by": "Ian (demo)"}


# ---------------------------------------------------------------- vessels

def base(vid, name, flag, vtype, length, rng, **kw):
    v = {"id": vid, "mmsi": mmsi_for(flag, rng), "imo": imo_number(rng), "name": name, "flag": flag,
         "type": vtype, "length_m": length, "illustrative": True, "voyage": None}
    v.update(kw)
    return v


def lane_mover(lane_pts, kn, frac, offset, rng, t0=START, t1=END):
    """Start somewhere along a lane so the ship stays on it for the whole window."""
    line = P.line(lane_pts, offset)
    need = kn * KN * (t1 - t0)
    s0 = max(0.0, (line.length - need - 2000) * frac)
    m = Mover(t0, np.array(line.interpolate(s0).coords[0]))
    m.go(substring(line, s0, line.length), kn)
    if m.t < t1:
        m.wait(t1, rng)
    return m


def ferry_mover(route, kn, first_dep, at_helsinki, dwell_min, rng):
    line = P.line(route)
    rev = LineString(list(line.coords)[::-1])
    legs = [line, rev] if at_helsinki else [rev, line]
    m = Mover(START, np.array(legs[0].coords[0]))
    m.wait(first_dep, rng, r=15)
    i = 0
    while m.t < END:
        m.go(legs[i % 2], kn)
        m.wait(min(END, m.t + dwell_min * 60), rng, r=15)
        i += 1
    return m


def lawnmower(center, rng, legs=24, leg_m=4200, gap_m=600, kn=3.2, heading=60):
    """Trawling: long slow legs, turning at each end."""
    c = P.xy([center])[0]
    h = math.radians(heading)
    ux, uy = math.sin(h), math.cos(h)
    px, py = math.cos(h), -math.sin(h)
    pts = []
    for i in range(legs):
        off = (i % 6 - 2.5) * gap_m
        a = c + np.array([px, py]) * off
        s = 1 if i % 2 == 0 else -1
        pts.append(a - s * np.array([ux, uy]) * leg_m / 2)
        pts.append(a + s * np.array([ux, uy]) * leg_m / 2)
    m = Mover(START, pts[0])
    m.go(np.array(pts[1:]), kn)
    if m.t < END:
        m.wait(END, rng, r=40)
    return m


def track_from(m, proj=P, noise_m=3.0, rng=None):
    lon, lat = m.sample(proj)
    sog, cog = kinematics(lon, lat)
    return lon, lat, sog, cog


def claimed_from(lon, lat, sog, cog, rng, keep=None, noise_m=8.0):
    keep = np.ones(len(T), bool) if keep is None else keep
    dl = rng.normal(0, noise_m, (len(T), 2))
    clon = lon + dl[:, 0] / (111320 * np.cos(np.radians(lat)))
    clat = lat + dl[:, 1] / 110540
    csog = np.clip(sog + rng.normal(0, 0.1, len(T)), 0, None)
    ccog = (cog + rng.normal(0, 1.5, len(T))) % 360
    return rows(T[keep], clon[keep], clat[keep], csog[keep], ccog[keep])


def cable_crossing(lane_pts):
    a = P.line(CABLE_A)
    lane = P.line(lane_pts)
    p = lane.intersection(a)
    p = p if p.geom_type == "Point" else list(p.geoms)[0]
    return lane, lane.project(p), a


def d04(rng):
    """KAIROS STAR: westbound out of the eastern Gulf, slows, goes dark at 05:40 and drags across Cable A."""
    lane, s_c, cable = cable_crossing(WB)
    s_entry = s_c - 2250            # about the corridor edge along the lane (half width 2 km, oblique crossing)
    t_dark, t_entry, t_lift, t_back = hm(5, 40), hm(6, 24), hm(7, 24), hm(7, 30)
    s_dark = s_entry - 3.4 * KN * (t_entry - t_dark)
    s_slow = s_dark - 8.6 * KN * 1800
    s0 = s_slow - 11.6 * KN * (hm(5, 10) - START)
    m = Mover(START, np.array(lane.interpolate(s0).coords[0]))
    m.go(substring(lane, s0, s_slow), 11.6)
    m.ramp(substring(lane, s_slow, s_dark), 11.4, 5.0)
    m.ramp(substring(lane, s_dark, s_entry), 4.6, 2.4)
    # Anchor-drag phase: erratic heading, 1-2.5 kn, net drift west across the cable.
    n = int((t_lift - m.t) // STEP)
    base_dir = np.array(lane.interpolate(s_entry + 500).coords[0]) - np.array(lane.interpolate(s_entry).coords[0])
    base_b = math.degrees(math.atan2(base_dir[0], base_dir[1]))
    swings = [55, 62, 20, -48, -70, -35, 10, 48, 66, 30, -22, -64, -40, 15, 58, 70, 25, -30, -66, -50, 5, 44, 60, 18,
              -36, -58]
    vecs = []
    for i in range(n):
        kn = 1.3 + 0.8 * abs(math.sin(i * 0.9)) + rng.normal(0, 0.08)
        b = math.radians(base_b + 0.8 * swings[i % len(swings)] + rng.normal(0, 4))
        vecs.append((kn * KN * STEP * math.sin(b), kn * KN * STEP * math.cos(b)))
    vecs = np.array(vecs)
    target = np.array(lane.interpolate(s_c + 900).coords[0]) + np.array([0.0, 150.0])
    want = target - m.pos
    have = vecs.sum(0)
    ang = math.atan2(want[1], want[0]) - math.atan2(have[1], have[0])
    sc = np.linalg.norm(want) / np.linalg.norm(have)
    R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]]) * sc
    m.steps(vecs @ R.T)
    s_now = lane.project(shapely.Point(m.pos))
    m.go(np.array(lane.interpolate(s_now + 400).coords[0]), 4.0)
    s_now = lane.project(shapely.Point(m.pos))
    m.ramp(substring(lane, s_now, s_now + 1500), 5.0, 9.0)
    s_now = lane.project(shapely.Point(m.pos))
    m.go(substring(lane, s_now, lane.length), 9.2)
    lon, lat, sog, cog = track_from(m)
    keep = (T <= t_dark) | (T >= t_back)
    v = base("D-04", "KAIROS STAR", "Cook Islands", "tanker", 228, rng)
    v["imo"] = "9329760"
    v["mmsi"] = 518009876
    v["track"] = rows(T, lon, lat, sog, cog)
    v["claimed"] = claimed_from(lon, lat, sog, cog, rng, keep)
    v["identity_history"] = [{"t": START - 400 * DAY, "name": "KAIROS STAR", "flag": "Cook Islands", "mmsi": v["mmsi"]}]
    v["dest"] = "Port Said (declared)"
    return v


S17_LAND = (26.705, 59.488)        # inland, east of Kunda, Estonia


def s17(rng):
    """VEGA STRAND: eastbound at sea along the Estonian coast; from 06:58 its AIS says it is on land."""
    line = P.line([(24.9, 59.74), (25.5, 59.76), (26.0, 59.72), (26.4, 59.62), (26.70, 59.58), (27.0, 59.57),
                   (27.4, 59.55)])
    s_x = line.project(line.intersection(P.line(CABLE_A)))
    slow_kn, t_x = 5.6, hm(7, 6)
    s_slow = s_x - slow_kn * KN * (t_x - hm(6, 0))
    fast_kn = 6.8
    s0 = s_slow - fast_kn * KN * (hm(6, 0) - START)
    m = Mover(START, np.array(line.interpolate(s0).coords[0]))
    m.go(substring(line, s0, s_slow), fast_kn)
    m.go(substring(line, s_slow, line.length), slow_kn)
    lon, lat, sog, cog = track_from(m)
    v = base("S-17", "VEGA STRAND", "Cameroon", "cargo", 139, rng)
    v["track"] = rows(T, lon, lat, sog, cog)
    # Claimed: true until 06:46, then a jump on land, a short "crop circle" arc, a flick back, then land again.
    c = claimed_from(lon, lat, sog, cog, rng)
    land = P.xy([S17_LAND])[0]
    out = []
    for i, r in enumerate(c):
        t = r[0]
        if t < hm(6, 58) or t == hm(7, 14):
            out.append(r)
            continue
        if hm(7, 2) <= t <= hm(7, 10):
            k = (t - hm(7, 2)) // STEP           # 0..4
            a = math.radians(30 + 66 * k)
            x, y = land + np.array([1100 * math.cos(a), 1100 * math.sin(a)])
            sg, cg = 4.6 + rng.normal(0, 0.2), (math.degrees(-a) + 360) % 360
        else:
            x, y = land + rng.normal(0, 25, 2) + np.array([0.0, -(t - hm(6, 58)) * 0.012])
            sg, cg = abs(rng.normal(0.2, 0.1)), rng.uniform(0, 360)
        lo, la = P.ll([x], [y])
        out.append([t, round(float(lo[0]), 5), round(float(la[0]), 5), round(float(sg), 1), int(cg) % 360])
    v["claimed"] = out
    v["identity_history"] = [{"t": START - 220 * DAY, "name": "VEGA STRAND", "flag": "Cameroon", "mmsi": v["mmsi"]}]
    v["dest"] = "Sillamae"
    return v


def t11(rng):
    """MARLOW SPIRIT: Suezmax at the Ras Tanura anchorage, loading for Kaohsiung via Hormuz."""
    m = Mover(START, P_GULF.xy([(50.352, 26.718)])[0])
    m.wait(END, rng, r=120, period=7 * 3600)
    lon, lat = m.sample(P_GULF)
    sog, cog = kinematics(lon, lat)
    v = base("T-11", "MARLOW SPIRIT", "Panama", "tanker", 274, rng)
    v["track"] = rows(T, lon, lat, sog, cog)
    v["claimed"] = claimed_from(lon, lat, sog, cog, rng)
    old_mmsi = mmsi_for("Comoros", rng)
    v["identity_history"] = [
        {"t": START - 2100 * DAY, "name": "KAPPA STAR", "flag": "Comoros", "mmsi": old_mmsi},
        {"t": hm(0) - 13 * DAY - 4 * 3600, "name": "MARLOW SPIRIT", "flag": "Panama", "mmsi": v["mmsi"]},
    ]
    gap0 = hm(0) - 16 * DAY + 19 * 3600 + 20 * 60
    v["archive_dark_periods"] = [{"start": gap0, "end": gap0 + 504 * 60, "minutes": 504, "lon": 55.62, "lat": 26.30,
                                  "lon_end": 54.10, "lat_end": 26.05,
                                  "note": "AIS off after leaving the Strait of Hormuz westbound"}]
    v["port_calls"] = [{"port": "Ras Tanura", "country": "Saudi Arabia", "arrived": START - 2 * DAY + 6 * 3600,
                        "departed": None, "flagged": False}]
    v["voyage"] = {"route_id": "rastanura-kaohsiung", "label": "Ras Tanura → Kaohsiung", "via": "Strait of Hormuz",
                   "hull_usd_m": 100, "departs": "2026-11-12"}
    v["dest"] = "Kaohsiung"
    return v


def sts_pair(rng):
    """ORION GRACE (D-12) and ALMA BAY (V-22) lie alongside west of Gogland, 02:12-03:26."""
    meet = P.xy([(26.55, 60.13)])[0]
    t_meet, t_part = hm(2, 12), hm(3, 26)
    drift_kn, drift_b = 0.35, 120.0
    # D-12 from the east.
    l12 = P.line([(27.75, 60.17), (27.1, 60.19), (26.62, 60.14)])
    l12 = LineString(list(l12.coords) + [tuple(meet)])
    s0 = max(0.0, l12.length - 10.5 * KN * (t_meet - START - 900) - 1500)
    a = Mover(START, np.array(l12.interpolate(s0).coords[0]))
    a.go(substring(l12, s0, l12.length - 1500), 10.5)
    a.ramp(substring(l12, l12.length - 1500, l12.length), 6.0, 0.6)
    a.wait(t_meet, rng, r=20)
    a.drift(t_part, drift_kn, drift_b, np.random.default_rng(11), wobble=3)
    west = P.line([(26.4, 60.05), (26.2, 59.96)] + WB[5:])
    a.go(np.array([west.coords[0]]), 7.0)
    a.go(west, 11.0)
    lon, lat, sog, cog = track_from(a)
    d12 = base("D-12", "ORION GRACE", "Gabon", "tanker", 249, rng)
    d12["track"] = rows(T, lon, lat, sog, cog)
    d12["claimed"] = claimed_from(lon, lat, sog, cog, rng, ~((T > hm(2, 30)) & (T < hm(3, 2))))
    d12["identity_history"] = [{"t": START - 160 * DAY, "name": "ORION GRACE", "flag": "Gabon", "mmsi": d12["mmsi"]}]
    d12["watchlists"] = ["Shadow-fleet watchlist (illustrative)"]
    d12["owner"] = {"name": "Orion Grace Shipping Ltd (illustrative)", "designated": True,
                    "list": "Illustrative sanctions list"}
    d12["dest"] = "For orders"
    # V-22 from the west, 140 m alongside.
    side = meet + np.array([140.0, 40.0])
    l22 = P.line([(25.2, 59.92), (25.9, 60.0), (26.45, 60.11)])
    l22 = LineString(list(l22.coords) + [tuple(side)])
    s0 = max(0.0, l22.length - 9.5 * KN * (t_meet - START - 1500) - 1500)
    b = Mover(START, np.array(l22.interpolate(s0).coords[0]))
    b.go(substring(l22, s0, l22.length - 1500), 9.5)
    b.ramp(substring(l22, l22.length - 1500, l22.length), 5.5, 0.5)
    b.wait(t_meet, rng, r=20)
    b.drift(t_part, drift_kn, drift_b, np.random.default_rng(11), wobble=3)
    back = P.line([(26.3, 60.06), (25.5, 59.95), (24.6, 59.84), (24.0, 59.74), (23.3, 59.61), (22.4, 59.53)])
    b.go(np.array([back.coords[0]]), 7.5)
    b.go(back, 10.0)
    lon, lat, sog, cog = track_from(b)
    v22 = base("V-22", "ALMA BAY", "Palau", "tanker", 183, rng)
    v22["track"] = rows(T, lon, lat, sog, cog)
    v22["claimed"] = claimed_from(lon, lat, sog, cog, rng)
    v22["identity_history"] = [{"t": START - 300 * DAY, "name": "ALMA BAY", "flag": "Palau", "mmsi": v22["mmsi"]}]
    v22["dest"] = "For orders"
    return [d12, v22]


def v30(rng):
    """BALTIC WREN: leaves Helsinki roads, slows and drifts inside the Cable A corridor from about 06:50."""
    cable = P.line(CABLE_A)
    cp = cable.interpolate(cable.project(shapely.Point(P.xy([(26.425, 59.73)])[0])))
    # Unit normal pointing west of the cable at that point.
    q = cable.interpolate(cable.project(cp) + 100)
    d = np.array(q.coords[0]) - np.array(cp.coords[0])
    d /= np.linalg.norm(d)
    nrm = np.array([d[1], -d[0]]) if d[1] < 0 else np.array([-d[1], d[0]])
    if nrm[0] > 0:
        nrm = -nrm
    edge = np.array(cp.coords[0]) + nrm * 2300
    inside = np.array(cp.coords[0]) + nrm * 900
    route = P.line([(25.02, 60.07), (25.4, 59.95), (26.0, 59.82)])
    route = LineString(list(route.coords) + [tuple(edge)])
    t_edge = hm(6, 48)
    dep = t_edge - route.length / (9.8 * KN)
    m = Mover(START, np.array(route.coords[0]))
    m.wait(dep, rng, r=60)
    m.go(route, 9.8)
    m.ramp(LineString([tuple(edge), tuple(inside)]), 2.6, 1.0)
    m.drift(END, 0.75, 150, rng, wobble=5)
    lon, lat, sog, cog = track_from(m)
    v = base("V-30", "BALTIC WREN", "Antigua and Barbuda", "cargo", 118, rng)
    v["track"] = rows(T, lon, lat, sog, cog)
    v["claimed"] = claimed_from(lon, lat, sog, cog, rng)
    v["dest"] = "Sillamae"
    return v


def v27(rng):
    """LUNA renames to ARGO LUNA and reflags Tanzania -> Sierra Leone at 03:12, same IMO."""
    m = lane_mover(EB[:9] + UST_LUGA_IN[1:], 10.8, 0.55, -350, rng)
    lon, lat, sog, cog = track_from(m)
    v = base("V-27", "ARGO LUNA", "Sierra Leone", "bulk carrier", 190, rng)
    old = mmsi_for("Tanzania", rng)
    v["track"] = rows(T, lon, lat, sog, cog)
    v["claimed"] = claimed_from(lon, lat, sog, cog, rng)
    v["identity_history"] = [{"t": START - 700 * DAY, "name": "LUNA", "flag": "Tanzania", "mmsi": old},
                             {"t": hm(3, 12), "name": "ARGO LUNA", "flag": "Sierra Leone", "mmsi": v["mmsi"]}]
    v["dest"] = "Ust-Luga"
    return v


def loiterers(rng):
    out = []
    # V-33: drifts mid-Gulf waiting for a berth, then anchors at Helsinki roads.
    m = Mover(START, P.xy([(25.55, 59.86)])[0])
    m.drift(hm(1, 0), 0.6, 240, rng, wobble=25)
    m.drift(hm(3, 30), 0.9, 300, rng, wobble=30)
    m.drift(hm(5, 20), 0.7, 60, rng, wobble=30)
    m.go(P.xy([(25.3, 59.95), (25.06, 60.05)]), 8.5)
    m.wait(END, rng, r=70)
    lon, lat, sog, cog = track_from(m)
    v = base("V-33", "HANNA K", "Netherlands", "cargo", 89, rng)
    v.update(track=rows(T, lon, lat, sog, cog), claimed=claimed_from(lon, lat, sog, cog, rng), dest="Helsinki")
    out.append(v)
    # V-35: waits east of Vaindloo for orders, then heads for Ust-Luga.
    m = Mover(START, P.xy([(27.3, 59.80)])[0])
    m.drift(hm(2, 10), 0.8, 200, rng, wobble=30)
    m.drift(hm(4, 0), 0.6, 20, rng, wobble=30)
    m.go(P.xy([(27.6, 59.90), (28.0, 59.93), (28.25, 59.85), (28.28, 59.80)]), 9.0)
    m.wait(END, rng, r=90)
    lon, lat, sog, cog = track_from(m)
    v = base("V-35", "STELLAR ODA", "Liberia", "bulk carrier", 180, rng)
    v.update(track=rows(T, lon, lat, sog, cog), claimed=claimed_from(lon, lat, sog, cog, rng), dest="Ust-Luga")
    out.append(v)
    return out


def normal_traffic(rng):
    out = []

    def add(vid, name, flag, vtype, length, m, dest):
        lon, lat, sog, cog = track_from(m)
        v = base(vid, name, flag, vtype, length, rng)
        v.update(track=rows(T, lon, lat, sog, cog), claimed=claimed_from(lon, lat, sog, cog, rng), dest=dest)
        out.append(v)

    # Helsinki-Tallinn ferries (two overnight ro-pax, two day ferries that start around 06:30).
    add("V-01", "AURORA LINK", "Finland", "passenger", 212, ferry_mover(FERRY_A, 22, hm(0, 30), True, 70, rng), "Tallinn")
    add("V-02", "BALTIC SWIFT", "Estonia", "passenger", 185, ferry_mover(FERRY_B, 21, hm(1, 0), False, 80, rng), "Helsinki")
    add("V-03", "STELLA NORDICA", "Estonia", "passenger", 186, ferry_mover(FERRY_B, 24, hm(6, 30), True, 60, rng), "Tallinn")
    add("V-04", "FINNSUND", "Finland", "passenger", 218, ferry_mover(FERRY_A, 23, hm(6, 0), False, 60, rng), "Helsinki")
    # Overnight cruise ferry inbound from Stockholm.
    add("V-05", "AALAND STAR", "Finland", "passenger", 177, lane_mover(HEL_STO, 10.6, 0.0, 0, rng), "Helsinki")
    # At anchor on the roads.
    for vid, name, flag, vtype, length, pt, dest in [
        ("V-06", "KORONA", "Malta", "tanker", 144, (24.93, 60.065), "Helsinki"),
        ("V-07", "BELTANA", "Marshall Islands", "bulk carrier", 179, (25.06, 60.085), "Helsinki"),
        ("V-08", "TIKO", "Estonia", "cargo", 99, (24.62, 59.515), "Tallinn"),
        ("V-09", "CAPE ISLE", "Bahamas", "bulk carrier", 190, (24.68, 59.535), "Muuga")]:
        m = Mover(START, P.xy([pt])[0])
        m.wait(END, rng, r=90)
        add(vid, name, flag, vtype, length, m, dest)
    # Kotka traffic.
    add("V-10", "NORRVIK", "Germany", "container", 172,
        lane_mover(KOTKA_OUT + WB[5:], 12.5, 0.0, 0, rng), "Rotterdam")
    kin = EB[:6] + [(26.6, 60.0), (26.75, 60.10), (26.9, 60.30)]
    add("V-11", "RHEA LUX", "Portugal", "cargo", 135, lane_mover(kin, 11.2, 0.95, 250, rng), "Kotka")
    # East-west Gulf traffic to and from St Petersburg, Primorsk and Ust-Luga.
    wb = [("V-12", "NEVA HORIZON", "Liberia", "tanker", 228, 11.8, 0.05, -300, WB, "Rotterdam"),
          ("V-13", "MARIS PRIMA", "Cyprus", "container", 222, 14.2, 0.30, -200, WB, "Hamburg"),
          ("V-14", "SKAGEN ROVER", "Norway", "cargo", 120, 10.4, 0.62, -150, WB, "Aarhus"),
          ("V-15", "BALTIC LARK", "Sweden", "ro-ro", 190, 15.5, 0.85, 350, WB, "Gothenburg"),
          ("V-17", "AEGEAN VOW", "Greece", "tanker", 250, 12.4, 0.48, 0, WB, "Augusta"),
          ("V-18", "HELIOS BAY", "Singapore", "container", 260, 13.6, 0.95, -450, WB, "Antwerp"),
          ("V-32", "NORDKAPP CARRIER", "Finland", "ro-ro", 188, 16.0, 0.18, -300, WB, "Lubeck")]
    for vid, name, flag, vtype, length, kn, frac, off, lane, dest in wb:
        add(vid, name, flag, vtype, length, lane_mover(lane, kn, frac, off, rng), dest)
    v16 = [(28.28, 59.78), (28.1, 59.95), (27.6, 60.10)] + WB[2:]
    add("V-16", "SVIR TRADER", "Russia", "cargo", 140, lane_mover(v16, 10.2, 0.0, 0, rng), "Kiel")
    eb = [("V-19", "NORD SERENITY", "Marshall Islands", "tanker", 183, 12.0, 0.75, 300, EB[:9] + UST_LUGA_IN[1:], "Ust-Luga"),
          ("V-20", "ELBE TRADER", "Germany", "container", 170, 14.0, 0.25, -250, EB, "St Petersburg"),
          ("V-21", "SOLVANG MIST", "Norway", "LPG tanker", 190, 13.2, 0.55, 150, EB, "Ust-Luga"),
          ("V-23", "VIRGO STAR", "Panama", "bulk carrier", 199, 11.4, 0.10, -100, EB, "St Petersburg"),
          ("V-24", "IRIS AURORA", "Netherlands", "cargo", 120, 10.6, 0.90, 400, EB, "St Petersburg"),
          ("V-25", "SEABORN KITE", "Hong Kong", "tanker", 183, 12.6, 0.40, -400, EB, "Primorsk"),
          ("V-26", "LADOGA BREEZE", "Russia", "cargo", 130, 9.8, 0.68, 0, EB[:9] + UST_LUGA_IN[1:], "Ust-Luga"),
          ("V-28", "KESTREL MAJOR", "Malta", "tanker", 244, 12.9, 0.98, -200, EB, "Primorsk")]
    for vid, name, flag, vtype, length, kn, frac, off, lane, dest in eb:
        add(vid, name, flag, vtype, length, lane_mover(lane, kn, frac, off, rng), dest)
    # Fishing (trawling, slow and back and forth) and a patrol boat.
    add("V-36", "LIIVI", "Estonia", "fishing", 24, lawnmower((24.30, 59.61), rng, heading=75), "Fishing grounds")
    add("V-37", "MERIKOTKA", "Finland", "fishing", 28, lawnmower((25.85, 59.73), rng, heading=110), "Fishing grounds")
    add("V-38", "LAINE", "Estonia", "fishing", 21, lawnmower((27.65, 59.735), rng, heading=40, kn=3.0), "Fishing grounds")
    box = P.xy([(25.1, 59.95), (25.4, 59.95), (25.4, 60.02), (25.1, 60.02), (25.1, 59.95)])
    m = Mover(START, box[0])
    while m.t < END:
        m.go(box[1:], 6.0)
    add("V-40", "VARTIO 12", "Finland", "patrol", 49, m, "Patrol")
    return out


# ---------------------------------------------------------------- space layers

SAR_PASSES = [hm(0, 52), hm(2, 36), hm(4, 12), hm(5, 6), hm(5, 56), hm(6, 18), hm(6, 40), hm(7, 2), hm(7, 22), hm(7, 44)]
RF_PASSES = [hm(1, 30), hm(3, 4), hm(4, 40), hm(5, 48), hm(6, 8), hm(6, 30), hm(6, 50), hm(7, 10), hm(7, 30), hm(7, 50)]
KEY = {"D-04", "S-17", "D-12"}


def space_layers(vessels, rng):
    sar_n, rf_n = 101, 401
    for v in vessels:
        v["observed"], v["emitted"] = [], []
    for t in SAR_PASSES:
        k = int((t - START) // STEP)
        for v in vessels:
            p = 0.55 if v["length_m"] < 40 else 0.93
            hit = rng.random()
            if v["id"] not in KEY and hit > p:
                continue
            _, lon, lat = v["track"][k][:3]
            e = rng.normal(0, 70, 2)
            v["observed"].append({"id": f"SAR-{sar_n:04d}", "t": int(t),
                                  "lon": round(lon + e[0] / (111320 * math.cos(math.radians(lat))), 5),
                                  "lat": round(lat + e[1] / 110540, 5),
                                  "length_m": int(round(v["length_m"] * (1 + rng.normal(0, 0.035)))),
                                  "matched_to_ais": None, "sensor": "SAR (simulated Sentinel-1 class)"})
            sar_n += 1
    for t in RF_PASSES:
        k = int((t - START) // STEP)
        for v in vessels:
            hit = rng.random()
            if v["id"] not in KEY and (hit > 0.75 or v["type"] == "fishing" and hit > 0.4):
                continue
            _, lon, lat = v["track"][k][:3]
            e = rng.normal(0, 450, 2)
            v["emitted"].append({"id": f"RF-{rf_n:04d}", "t": int(t),
                                 "lon": round(lon + e[0] / (111320 * math.cos(math.radians(lat))), 5),
                                 "lat": round(lat + e[1] / 110540, 5),
                                 "band": "X-band navigation radar", "sensor": "RF (simulated)"})
            rf_n += 1


def build_world(seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    vessels = [d04(rng), s17(rng), t11(rng), *sts_pair(rng), v27(rng), v30(rng), *loiterers(rng),
               *normal_traffic(rng)]
    for v in vessels:
        v.setdefault("identity_history", [{"t": START - int(rng.integers(90, 2000)) * DAY, "name": v["name"],
                                           "flag": v["flag"], "mmsi": v["mmsi"]}])
        v.setdefault("watchlists", [])
        v.setdefault("states", [])
        v.setdefault("dark_periods", [])
        v.setdefault("risk", {"score": 0, "level": "low", "reasons": []})
    space_layers(vessels, rng)
    order = {"D": 0, "S": 1, "T": 2, "V": 3}
    vessels.sort(key=lambda v: (order[v["id"][0]], v["id"]))
    world = {
        "scenario": {"id": "gof-seeded", "label": "Gulf of Finland · seeded scenario", "start": START, "end": END,
                     "now": NOW, "seed": seed, "illustrative": True, "bbox": BBOX, "time_mode": "live",
                     "description": "Seeded scenario inspired by the December 2024 Estlink 2 cable damage. "
                                    "Vessels, identities and detections are illustrative."},
        "assets": assets(),
        "anchorages": ANCHORAGES,
        "zones": [zone_z1()],
        "vessels": vessels,
        "events": [],
        "alerts": [],
        "actions": [],
    }
    return fusion.fuse(world)


def main():
    w = build_world()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(w, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB): {len(w['vessels'])} vessels, {len(w['events'])} events, "
          f"{len(w['alerts'])} alerts")
    for a in w["alerts"]:
        print(" ", a["id"], fusion.fmt_t(a["t"]), a["vessel_id"], a["severity"], a["headline"])


if __name__ == "__main__":
    main()
