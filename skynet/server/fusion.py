"""Skynet fusion engine.

Compares what each ship CLAIMS (AIS, `claimed`) with what space OBSERVES (SAR, `observed`) and what it
EMITS (RF, `emitted`), and with Skynet's fused track (`track`). From that it derives, per vessel:
`states`, `dark_periods`, `risk`, and world-level `events` and `alerts` (zone-driven, via zones.py).

`fuse(world) -> world` is pure and deterministic: it works on a deep copy and uses no clock or randomness.

Risk weights follow cable-watch's published rules (cable_watch/detect.py) where they fit:
stopped over a cable 30-40, slow over a cable 25-35, zigzag 20, speed drop 25 (the Eagle S anchor-drag
pattern), AIS gap 25-40, watchlist 20, each kind counted once, capped at 100.
"""
import copy
import json
import math
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import Polygon, shape

ROOT = Path(__file__).resolve().parent.parent
LAND_FILE = ROOT / "data" / "geo" / "land.geojson"

DAY = 86400
KN = 1852 / 3600
STEP = 120
DARK_GAP_S = 15 * 60          # an AIS silence longer than this is a dark period
MATCH_M = 1_500               # a radar contact this close to the AIS position (same time) is matched
MISMATCH_M = 3_000            # further than this is a position mismatch
SPOOF_M = 2_000               # claimed vs fused track further than this is a false position
JUMP_KN = 60                  # implied speed between two AIS reports above this is a position jump
SERVICE = {"patrol", "tug", "pilot", "sar", "search and rescue", "law enforcement", "military"}
NO_LOITER = SERVICE | {"passenger", "fishing"}
NO_STS = SERVICE | {"passenger", "fishing"}
SEV = {"low": 0, "medium": 1, "high": 2}

PLACES = [("Helsinki", 24.94, 60.17), ("Tallinn", 24.75, 59.44), ("Porvoo", 25.66, 60.39), ("Sköldvik", 25.55, 60.31),
          ("Loviisa", 26.23, 60.46), ("Kotka", 26.94, 60.47), ("Hamina", 27.20, 60.57), ("Kunda", 26.53, 59.50),
          ("Aseri", 26.86, 59.45), ("Sillamäe", 27.76, 59.40), ("Narva-Jõesuu", 28.04, 59.46), ("Loksa", 25.72, 59.58),
          ("Ust-Luga", 28.40, 59.68), ("Gogland", 27.00, 60.05), ("Vaindloo", 26.36, 59.82), ("Hanko", 22.95, 59.82),
          ("Paldiski", 24.05, 59.35), ("Muuga", 24.95, 59.50), ("Primorsk", 28.61, 60.35),
          ("Ras Tanura", 50.16, 26.64), ("Strait of Hormuz", 56.40, 26.50)]


# ---------------------------------------------------------------- formatting

def fmt_t(t):
    return time.strftime("%H:%M UTC", time.gmtime(int(t)))


def fmt_day(t):
    return time.strftime("%d %b %H:%M UTC", time.gmtime(int(t))).lstrip("0")


def fmt_ll(lon, lat):
    return f"{abs(lat):.2f}°{'N' if lat >= 0 else 'S'} {abs(lon):.2f}°{'E' if lon >= 0 else 'W'}"


def fmt_dur(sec):
    m = int(round(sec / 60))
    if m < 90:
        return f"{m} min"
    h, mm = divmod(m, 60)
    return f"{h} h {mm} min" if h < 6 else f"{sec / 3600:.1f} h"


def km_between(lon1, lat1, lon2, lat2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def place_near(lon, lat, max_km=35):
    best = min(PLACES, key=lambda p: km_between(lon, lat, p[1], p[2]))
    return best[0] if km_between(lon, lat, best[1], best[2]) <= max_km else None


def noun(v):
    t = (v.get("type") or "").lower()
    if "tanker" in t:
        return "tanker"
    if t in ("passenger", "ro-ro"):
        return "ferry" if t == "passenger" else "ro-ro ship"
    if t == "fishing":
        return "fishing boat"
    if t in ("cargo", "container", "bulk carrier"):
        return {"cargo": "cargo ship", "container": "container ship", "bulk carrier": "bulk carrier"}[t]
    return "vessel"


# ---------------------------------------------------------------- geometry helpers

@lru_cache(maxsize=32)
def transformers(crs):
    return (Transformer.from_crs("EPSG:4326", crs, always_xy=True),
            Transformer.from_crs(crs, "EPSG:4326", always_xy=True))


def crs_for(lon, lat):
    if -30 <= lon <= 45 and 30 <= lat <= 75:
        return "EPSG:3035"
    return f"+proj=laea +lat_0={lat:.1f} +lon_0={lon:.1f} +units=m"


@lru_cache(maxsize=1)
def _land():
    try:
        fc = json.loads(LAND_FILE.read_text())
    except (OSError, ValueError):
        return None
    geoms = np.array([shape(f["geometry"]) for f in fc["features"]], dtype=object)
    return shapely.STRtree(geoms), geoms


def inland(lon, lat, min_m=400.0):
    """True where a position is on land and at least `min_m` from the coast (so harbours do not count)."""
    lon, lat = np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(np.asarray(lat, float))
    out = np.zeros(len(lon), bool)
    L = _land()
    if L is None or not len(lon):
        return out
    tree, geoms = L
    pts = shapely.points(lon, lat)
    pi, gi = tree.query(pts, predicate="within")
    if len(pi):
        d = shapely.distance(pts[pi], shapely.boundary(geoms[gi])) * 111320 * np.cos(np.radians(lat[pi]))
        out[pi[d >= min_m]] = True
    return out


def near_land(lon, lat, within_deg=0.02):
    lon, lat = np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(np.asarray(lat, float))
    out = np.zeros(len(lon), bool)
    L = _land()
    if L is None or not len(lon):
        return out
    pi, _ = L[0].query(shapely.points(lon, lat), predicate="dwithin", distance=within_deg)
    out[pi] = True
    return out


def runs(mask):
    """(start, end) index pairs, inclusive, of consecutive True values."""
    mask = np.asarray(mask, bool)
    if not mask.any():
        return []
    d = np.diff(np.concatenate([[0], mask.astype(int), [0]]))
    return list(zip(np.flatnonzero(d == 1).tolist(), (np.flatnonzero(d == -1) - 1).tolist()))


def _arr(rowlist):
    if not rowlist:
        return np.zeros((0, 5))
    out = np.full((len(rowlist), 5), np.nan)
    for i, r in enumerate(rowlist):
        out[i, :min(5, len(r))] = [np.nan if x is None else x for x in r[:5]]
    return out[np.argsort(out[:, 0], kind="stable")]


def _nearest(ts, q, tol):
    q = np.atleast_1d(q)
    if not len(ts):
        return np.full(len(q), -1)
    i = np.searchsorted(ts, q)
    i0, i1 = np.clip(i - 1, 0, len(ts) - 1), np.clip(i, 0, len(ts) - 1)
    pick = np.where(np.abs(ts[i0] - q) <= np.abs(ts[i1] - q), i0, i1)
    return np.where(np.abs(ts[pick] - q) <= tol, pick, -1)


def _fill_kinematics(a, xs, ys):
    if len(a) < 2:
        return
    dt = np.maximum(np.diff(a[:, 0]), 1)
    dx, dy = np.diff(xs), np.diff(ys)
    sog = np.append(np.hypot(dx, dy) / dt / KN, np.nan)
    sog[-1] = sog[-2]
    bad = np.isnan(a[:, 3])
    a[bad, 3] = sog[bad]


class Vessel:
    """Numpy view of one vessel record, with the derived masks fusion and zones need."""

    def __init__(self, v, ctx):
        self.v, self.ctx = v, ctx
        self.id = v["id"]
        self.name = v.get("name") or self.id
        self.type = (v.get("type") or "").lower()
        cl = _arr(v.get("claimed"))
        tr = _arr(v.get("track"))
        self.fused = len(tr) > 0
        if not len(tr):
            tr = cl.copy()
        self.tr, self.cl = tr, cl
        f = ctx.fwd
        self.x, self.y = (np.asarray(a) for a in f.transform(tr[:, 1], tr[:, 2])) if len(tr) else (np.zeros(0),) * 2
        self.cx, self.cy = (np.asarray(a) for a in f.transform(cl[:, 1], cl[:, 2])) if len(cl) else (np.zeros(0),) * 2
        _fill_kinematics(self.tr, self.x, self.y)
        self.t, self.lon, self.lat, self.sog, self.cog = (self.tr[:, i] for i in range(5))
        self.ct = cl[:, 0] if len(cl) else np.zeros(0)
        self.obs = sorted(v.get("observed") or [], key=lambda o: o["t"])
        self.rf = sorted(v.get("emitted") or [], key=lambda o: o["t"])
        self._cache = {}

    def memo(self, key, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    # -- AIS gaps
    @property
    def dark(self):
        def calc():
            out, ct, tt = [], self.ct, self.t
            if not len(tt) or not self.fused:
                return out
            if not len(ct):
                return [{"start": int(tt[0]), "end": int(tt[-1]), "minutes": int((tt[-1] - tt[0]) // 60), "ongoing": True}]
            if ct[0] - tt[0] > DARK_GAP_S:
                out.append((tt[0], ct[0], False))
            for i in np.flatnonzero(np.diff(ct) > DARK_GAP_S):
                out.append((ct[i], ct[i + 1], False))
            if tt[-1] - ct[-1] > DARK_GAP_S:
                out.append((ct[-1], tt[-1], True))
            return [{"start": int(a), "end": int(b), "minutes": int(round((b - a) / 60)), "ongoing": o} for a, b, o in out]
        return self.memo("dark", calc)

    @property
    def archive(self):
        return list(self.v.get("archive_dark_periods") or [])

    @property
    def dark_mask(self):
        def calc():
            m = np.zeros(len(self.t), bool)
            for d in self.dark:
                m |= (self.t > d["start"]) & ((self.t < d["end"]) | d["ongoing"])
            return m
        return self.memo("dark_mask", calc)

    # -- claimed vs fused
    @property
    def cidx(self):
        return self.memo("cidx", lambda: _nearest(self.ct, self.t, 180))

    @property
    def c_inland(self):
        return self.memo("c_inland", lambda: inland(self.cl[:, 1], self.cl[:, 2]) if len(self.cl) else np.zeros(0, bool))

    @property
    def offset_m(self):
        """Distance between claimed and fused position at each track sample (nan when no AIS)."""
        def calc():
            ci = self.cidx
            d = np.full(len(self.t), np.nan)
            ok = ci >= 0
            d[ok] = np.hypot(self.cx[ci[ok]] - self.x[ok], self.cy[ci[ok]] - self.y[ok])
            return d
        return self.memo("offset", calc)

    @property
    def state(self):
        """0 verified, 1 dark, 2 spoofing, per track sample."""
        def calc():
            st = np.zeros(len(self.t), int)
            if not self.fused:
                return st
            st[self.dark_mask] = 1
            ci = self.cidx
            ok = ci >= 0
            land = np.zeros(len(self.t), bool)
            land[ok] = self.c_inland[ci[ok]]
            spoof = ok & ((np.nan_to_num(self.offset_m) > SPOOF_M) | land) & (st == 0)
            st[spoof] = 2
            # A single honest report in the middle of a spoofing run does not end it.
            r = runs(st == 2)
            for (a, b), (c, d) in zip(r, r[1:]):
                if self.t[c] - self.t[b] <= 8 * 60 and (st[b + 1:c] == 0).all():
                    st[b + 1:c] = 2
            return st
        return self.memo("state", calc)

    def spoof_runs(self):
        return [(i, j) for i, j in runs(self.state == 2)]

    @property
    def swing(self):
        def calc():
            s = np.zeros(len(self.t))
            if len(self.t) > 1:
                d = np.abs((np.diff(self.cog) + 180) % 360 - 180)
                s[1:] = np.nan_to_num(d)
            return s
        return self.memo("swing", calc)

    def asset_dist(self, aid):
        def calc():
            g = self.ctx.asset_geom.get(aid)
            if g is None or not len(self.t):
                return np.full(len(self.t), np.inf)
            return shapely.distance(shapely.points(self.x, self.y), g)
        return self.memo(("ad", aid), calc)

    def nearest_asset(self):
        """(distance m per sample, asset id per sample) over all assets."""
        def calc():
            best = np.full(len(self.t), np.inf)
            who = np.array([None] * len(self.t), dtype=object)
            for aid in self.ctx.asset_geom:
                d = self.asset_dist(aid)
                m = d < best
                best[m], who[m] = d[m], aid
            return best, who
        return self.memo("na", calc)

    @property
    def in_anchorage(self):
        def calc():
            u = self.ctx.anch_union
            if u is None or not len(self.t):
                return np.zeros(len(self.t), bool)
            return shapely.contains_xy(u, self.lon, self.lat)
        return self.memo("anch", calc)

    def idx(self, t):
        if not len(self.t):
            return -1
        return int(np.clip(np.searchsorted(self.t, t), 0, len(self.t) - 1))

    def claimed_at(self, t, tol=240):
        i = _nearest(self.ct, t, tol)[0]
        return None if i < 0 else self.cl[i]

    def risk_score(self):
        return int((self.v.get("risk") or {}).get("score") or 0)


class Ctx:
    def __init__(self, world):
        self.world = world
        sc = world.get("scenario") or {}
        vs = world.get("vessels") or []
        bbox = sc.get("bbox")
        if not bbox:
            pts = [(r[1], r[2]) for v in vs for r in (v.get("track") or v.get("claimed") or [])[:1]]
            bbox = [min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts)] \
                if pts else [0, 0, 1, 1]
        self.crs = crs_for((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        self.fwd, self.inv = transformers(self.crs)
        self.assets = {a["id"]: a for a in world.get("assets") or []}
        self.asset_geom = {}
        for a in self.assets.values():
            try:
                g = shape(a["geometry"])
                self.asset_geom[a["id"]] = shapely.transform(
                    g, lambda c: np.column_stack(self.fwd.transform(c[:, 0], c[:, 1])))
            except Exception:
                continue
        anch = [Polygon(a["coordinates"]) for a in world.get("anchorages") or [] if len(a.get("coordinates") or []) >= 3]
        self.anchorages = world.get("anchorages") or []
        self.anch_union = shapely.union_all(anch) if anch else None
        if self.anch_union is not None:
            shapely.prepare(self.anch_union)
        self.vessels = [Vessel(v, self) for v in vs]
        self.by_id = {pv.id: pv for pv in self.vessels}
        ts = [pv.t for pv in self.vessels if len(pv.t)]
        self.start = int(sc.get("start") or (min(t[0] for t in ts) if ts else 0))
        self.end = int(sc.get("end") or (max(t[-1] for t in ts) if ts else 0))
        self._sts = {}

    def xy(self, lon, lat):
        x, y = self.fwd.transform(np.asarray(lon, float), np.asarray(lat, float))
        return np.asarray(x), np.asarray(y)

    def ll(self, x, y):
        lon, lat = self.inv.transform(np.asarray(x, float), np.asarray(y, float))
        return np.asarray(lon), np.asarray(lat)

    def asset_label(self, aid):
        a = self.assets.get(aid) or {}
        return a.get("alias") or a.get("name") or aid

    def nearest_anchorage(self, lon, lat):
        best = None
        for a in self.anchorages:
            c = np.mean(np.asarray(a["coordinates"], float), axis=0)
            d = km_between(lon, lat, c[0], c[1])
            if best is None or d < best[1]:
                best = (a["name"], d)
        return best

    def sts(self, max_m=500.0, min_s=40 * 60, max_kn=1.0):
        """Pairs of ships lying alongside at sea: closer than max_m, both slower than max_kn, for min_s."""
        key = (max_m, min_s, max_kn)
        if key in self._sts:
            return self._sts[key]
        cands = [pv for pv in self.vessels if pv.type not in NO_STS and len(pv.t) > 2 and np.nanmin(pv.sog) < max_kn]
        out = []
        if len(cands) >= 2 and self.end > self.start:
            g = np.arange(self.start, self.end + 1, STEP)
            N = len(cands)
            X, Y, S = (np.full((N, len(g)), np.nan) for _ in range(3))
            A = np.zeros((N, len(g)), bool)
            for k, pv in enumerate(cands):
                m = (g >= pv.t[0]) & (g <= pv.t[-1])
                X[k, m] = np.interp(g[m], pv.t, pv.x)
                Y[k, m] = np.interp(g[m], pv.t, pv.y)
                S[k, m] = np.interp(g[m], pv.t, np.nan_to_num(pv.sog, nan=99))
                A[k, m] = np.interp(g[m], pv.t, pv.in_anchorage.astype(float)) > 0.5
            for i in range(N):
                for j in range(i + 1, N):
                    d = np.hypot(X[i] - X[j], Y[i] - Y[j])
                    with np.errstate(invalid="ignore"):
                        m = (d < max_m) & (S[i] < max_kn) & (S[j] < max_kn) & ~A[i] & ~A[j]
                    for a, b in runs(m):
                        if g[b] - g[a] >= min_s:
                            k = int(a + np.nanargmin(d[a:b + 1]))
                            lon, lat = self.ll(X[i, k], Y[i, k])
                            out.append({"a": cands[i].id, "b": cands[j].id, "t0": int(g[a]), "t1": int(g[b]),
                                        "dmin": float(np.nanmin(d[a:b + 1])), "lon": float(lon), "lat": float(lat)})
        self._sts[key] = out
        return out


# ---------------------------------------------------------------- events

def _ev(pv, etype, t, end, lon, lat, severity, summary, facts, source, zone_id=None, asset_id=None, other=None, **extra):
    e = {"id": None, "vessel_id": pv.id, "type": etype, "t": int(t), "end": None if end is None else int(end),
         "lon": None if lon is None else round(float(lon), 5), "lat": None if lat is None else round(float(lat), 5),
         "severity": severity, "summary": summary, "facts": [f for f in facts if f], "source": source,
         "zone_id": zone_id, "asset_id": asset_id, "other_vessel_id": other}
    e.update(extra)
    return e


def dark_events(ctx, pv):
    out = []
    for d in pv.dark:
        if d["minutes"] < 20:
            continue
        c = pv.claimed_at(d["start"], 600)
        lon, lat = (c[1], c[2]) if c is not None else (pv.lon[pv.idx(d["start"])], pv.lat[pv.idx(d["start"])])
        end = d["end"] if not d["ongoing"] else None
        sar = [o for o in pv.obs if d["start"] < o["t"] < d["end"]]
        rf = [o for o in pv.rf if d["start"] < o["t"] < d["end"]]
        m = (pv.t > d["start"]) & (pv.t < d["end"])
        near, who = pv.nearest_asset()
        aid, akm = None, None
        if m.any() and np.isfinite(near[m]).any():
            k = int(np.argmin(np.where(m, near, np.inf)))
            if near[k] < 5000:
                aid, akm = who[k], near[k] / 1000
        seen = bool(sar or rf)
        sev = "high" if d["minutes"] >= 60 and seen else "medium" if d["minutes"] >= 45 else "low"
        where = f" near {ctx.asset_label(aid)}" if aid else ""
        summary = (f"{pv.name} switched off its AIS for {fmt_dur(d['minutes'] * 60)}{where}"
                   + (", while radar and RF kept seeing the ship." if seen else "."))
        facts = [f"Last AIS report {fmt_t(d['start'])} at {fmt_ll(lon, lat)}" + (f", {c[3]:.1f} kn" if c is not None and not np.isnan(c[3]) else ""),
                 f"AIS back {fmt_t(d['end'])}" if not d["ongoing"] else "AIS still off at the end of the window",
                 f"{len(sar)} SAR detection{'s' if len(sar) != 1 else ''} and {len(rf)} X-band radar hit{'s' if len(rf) != 1 else ''} during the gap, none matched to AIS" if seen else "No radar or RF coverage during the gap",
                 f"Closest to {ctx.asset_label(aid)}: {akm:.1f} km" if aid else None]
        out.append(_ev(pv, "dark_period", d["start"], end, lon, lat, sev, summary, facts, "ais", asset_id=aid))
    for d in pv.archive:
        sev = "high" if d["minutes"] >= 360 else "medium"
        summary = f"{pv.name} had its AIS off for {fmt_dur(d['minutes'] * 60)} on {time.strftime('%d %b', time.gmtime(d['start'])).lstrip('0')}."
        facts = [f"Last AIS report {fmt_day(d['start'])} at {fmt_ll(d['lon'], d['lat'])}",
                 f"AIS back {fmt_day(d['end'])}" + (f" at {fmt_ll(d['lon_end'], d['lat_end'])}" if d.get("lon_end") is not None else ""),
                 d.get("note"), "From the AIS archive (illustrative)"]
        out.append(_ev(pv, "dark_period", d["start"], d["end"], d.get("lon"), d.get("lat"), sev, summary, facts, "ais",
                       archive=True))
    return out


def mismatch_events(ctx, pv):
    """SAR contacts that sit far from the AIS position reported at the same time."""
    bad = []
    for o in pv.obs:
        c = pv.claimed_at(o["t"])
        if c is None:
            continue
        ox, oy = ctx.xy(o["lon"], o["lat"])
        cx, cy = ctx.xy(c[1], c[2])
        d = float(np.hypot(ox - cx, oy - cy))
        if d > MISMATCH_M:
            bad.append((o, c, d))
    out = []
    groups = []
    for b in bad:
        if groups and b[0]["t"] - groups[-1][-1][0]["t"] <= 45 * 60:
            groups[-1].append(b)
        else:
            groups.append([b])
    for g in groups:
        o, c, d = g[0]
        land = bool(inland(c[1], c[2])[0])
        place = place_near(c[1], c[2])
        rf = [r for r in pv.rf if g[0][0]["t"] - 1800 <= r["t"] <= g[-1][0]["t"] + 1800 and (rc := pv.claimed_at(r["t"])) is not None
              and km_between(r["lon"], r["lat"], rc[1], rc[2]) * 1000 > MISMATCH_M]
        summary = f"Radar sees {pv.name} at sea {d / 1000:.1f} km from where its AIS says it is" + (" (on land)." if land else ".")
        facts = [f"{o['id']} at {fmt_t(o['t'])}: {o.get('length_m', '?')} m hull at {fmt_ll(o['lon'], o['lat'])}",
                 f"AIS at the same time: {fmt_ll(c[1], c[2])}" + (f", on land near {place}" if land and place else ", on land" if land else ""),
                 f"Offset {d / 1000:.1f} km" + (f", confirmed by {len(g)} radar passes" if len(g) > 1 else ""),
                 f"{len(rf)} X-band radar hit{'s' if len(rf) != 1 else ''} agree with the radar position, not the AIS one" if rf else None]
        out.append(_ev(pv, "position_mismatch", o["t"], g[-1][0]["t"] if len(g) > 1 else None, o["lon"], o["lat"], "high",
                       summary, facts, "sar"))
    return out


def spoof_facts(ctx, pv, i, j):
    """Facts about a false-position run on the claimed track between track samples i..j."""
    cm = (pv.ct >= pv.t[i] - 1) & (pv.ct <= pv.t[j] + 1)
    ci = np.flatnonzero(cm)
    facts, land_km, place = [], 0.0, None
    if not len(ci):
        return facts, None
    land = pv.c_inland[ci]
    if land.any():
        k = ci[np.flatnonzero(land)[0]]
        place = place_near(pv.cl[k, 1], pv.cl[k, 2])
        L = _land()
        if L is not None:
            p = shapely.points(pv.cl[k, 1], pv.cl[k, 2])
            gi = L[0].query(p, predicate="within")
            if len(gi):
                land_km = float(shapely.distance(p, shapely.boundary(L[1][gi[0]]))) * 111.32 * math.cos(math.radians(pv.cl[k, 2]))
        facts.append(f"AIS position {land_km:.1f} km inland" + (f" near {place}" if place else ""))
    # Jumps (including the jump into the run).
    a = max(ci[0] - 1, 0)
    seg = np.arange(a, ci[-1] + 1)
    if len(seg) > 1:
        d = np.hypot(np.diff(pv.cx[seg]), np.diff(pv.cy[seg]))
        dt = np.maximum(np.diff(pv.ct[seg]), 1)
        kn = d / dt / KN
        if kn.max() > JUMP_KN:
            k = int(np.argmax(d * (kn > JUMP_KN)))
            facts.append(f"Position jumped {d[k] / 1000:.1f} km in {fmt_dur(dt[k])} (implied {kn[k]:.0f} kn)")
    # "Crop circle": the reported positions turn through most of a circle in a small radius.
    b = np.degrees(np.arctan2(np.diff(pv.cx[ci]), np.diff(pv.cy[ci]))) if len(ci) > 2 else np.zeros(0)
    seglen = np.hypot(np.diff(pv.cx[ci]), np.diff(pv.cy[ci])) if len(ci) > 2 else np.zeros(0)
    if len(b) > 2:
        turn = (np.diff(b) + 180) % 360 - 180
        moving = (seglen[1:] > 150) & (seglen[:-1] > 150)
        best = (0, 0, 0)
        for s in range(len(turn)):
            tot = 0.0
            for e in range(s, min(len(turn), s + 10)):
                if not moving[e]:
                    break
                tot += turn[e]
                if abs(tot) > abs(best[0]):
                    best = (tot, s, e)
        if abs(best[0]) >= 200:
            s, e = best[1], best[2] + 2
            xs, ys = pv.cx[ci[s:e + 1]], pv.cy[ci[s:e + 1]]
            r = float(np.max(np.hypot(xs - xs.mean(), ys - ys.mean()))) / 1000
            mins = (pv.ct[ci[e]] - pv.ct[ci[s]]) / 60
            facts.append(f"Reported positions traced a {r:.1f} km arc ({min(abs(best[0]), 360):.0f}°) in {mins:.0f} min, "
                         f"the 'crop circle' pattern of GNSS spoofing")
    off = np.nanmax(pv.offset_m[i:j + 1]) if np.isfinite(pv.offset_m[i:j + 1]).any() else None
    if off:
        facts.append(f"Fused track from SAR and RF keeps it at sea, up to {off / 1000:.1f} km from the AIS position")
    return facts, place if land.any() else None


def spoof_events(ctx, pv):
    out = []
    for i, j in pv.spoof_runs():
        if pv.t[j] - pv.t[i] < 4 * 60 and j > i:
            continue
        facts, place = spoof_facts(ctx, pv, i, j)
        c = pv.claimed_at(pv.t[i])
        lon, lat = (c[1], c[2]) if c is not None else (pv.lon[i], pv.lat[i])
        if place:
            summary = f"{pv.name}'s AIS put it on land near {place} while it was at sea."
        else:
            summary = f"{pv.name}'s AIS positions jump and do not match where the ship really is."
        end = None if j == len(pv.t) - 1 else pv.t[j + 1]
        out.append(_ev(pv, "spoofing", pv.t[i], end, lon, lat, "high", summary, facts[:4], "ais"))
    return out


def identity_events(ctx, pv):
    out = []
    h = sorted(pv.v.get("identity_history") or [], key=lambda e: e["t"])
    for a, b in zip(h, h[1:]):
        if b["t"] < ctx.end - 365 * DAY:
            continue
        changed = [k for k in ("name", "flag", "mmsi") if a.get(k) != b.get(k)]
        if not changed:
            continue
        i = pv.idx(b["t"])
        inside = len(pv.t) and pv.t[0] <= b["t"] <= pv.t[-1]
        lon = b.get("lon", pv.lon[i] if inside else None)
        lat = b.get("lat", pv.lat[i] if inside else None)
        if lon is None and len(pv.t):
            lon, lat = pv.lon[0], pv.lat[0]
        what = " and ".join({"name": "name", "flag": "flag", "mmsi": "MMSI"}[k] for k in changed)
        summary = f"{a.get('name')} changed its {what} to {b.get('name')}" + (f" under the {b.get('flag')} flag." if "flag" in changed else ".")
        facts = [f"Name {a.get('name')} → {b.get('name')}" if "name" in changed else None,
                 f"Flag {a.get('flag')} → {b.get('flag')}" if "flag" in changed else None,
                 f"MMSI {a.get('mmsi')} → {b.get('mmsi')}" if "mmsi" in changed else None,
                 f"IMO {pv.v.get('imo')} unchanged, so it is the same hull" if pv.v.get("imo") else None,
                 f"Changed {fmt_day(b['t'])}"]
        out.append(_ev(pv, "identity_change", b["t"], None, lon, lat, "medium", summary, facts, "ais",
                       archive=bool(not inside)))
    return out


def sts_events(ctx):
    out = []
    for s in ctx.sts():
        for me, other in ((s["a"], s["b"]), (s["b"], s["a"])):
            pv, ov = ctx.by_id[me], ctx.by_id[other]
            dark = [d for pv_ in (pv, ov) for d in pv_.dark if d["start"] < s["t1"] and d["end"] > s["t0"]]
            listed = bool(pv.v.get("watchlists") or ov.v.get("watchlists"))
            anch = ctx.nearest_anchorage(s["lon"], s["lat"])
            summary = f"{pv.name} lay alongside {ov.name} at sea for {fmt_dur(s['t1'] - s['t0'])}, outside any port area."
            facts = [f"Alongside {ov.name} ({ov.id}) {fmt_t(s['t0'])}–{fmt_t(s['t1'])}",
                     f"Closest {s['dmin'] / 1000:.2f} km, both under 1 kn",
                     *(f"{ctx.by_id[x].name}'s AIS was off {d['minutes']} min of it" for x in (me, other)
                       for d in ctx.by_id[x].dark if d["start"] < s["t1"] and d["end"] > s["t0"]),
                     f"Nearest anchorage: {anch[0]}, {anch[1]:.0f} km away" if anch else None]
            out.append(_ev(pv, "ship_to_ship_meeting", s["t0"], s["t1"], s["lon"], s["lat"],
                           "high" if dark or listed else "medium", summary, facts[:4], "fusion", other=other))
    return out


def drag_runs(pv, near_m=3000, max_kn=3.0, min_s=30 * 60, min_swing=25.0, mask=None):
    """Anchor-drag signature: slow (0.5-max_kn kn) with heading swings, near an asset (or inside `mask`)."""
    if mask is None:
        near, _ = pv.nearest_asset()
        mask = near < near_m
    sog = np.nan_to_num(pv.sog, nan=-1)
    m = mask & (sog >= 0.5) & (sog <= max_kn)
    out = []
    for i, j in runs(m):
        # Slide a min_s window along the run: the first window with enough heading swing fires.
        for k in range(i + 1, j + 1):
            a = int(np.searchsorted(pv.t, pv.t[k] - min_s))
            if a >= i and pv.swing[a + 1:k + 1].mean() >= min_swing:
                out.append((a, j, int(pv.t[k])))
                break
    return out


def crossings(pv, aid, i, j):
    g = pv.ctx.asset_geom.get(aid)
    if g is None or j <= i:
        return 0
    a, b = max(i - 3, 0), min(j + 3, len(pv.t) - 1)
    segs = shapely.linestrings(np.stack([np.column_stack([pv.x[a:b], pv.y[a:b]]),
                                         np.column_stack([pv.x[a + 1:b + 1], pv.y[a + 1:b + 1]])], axis=1))
    return int(shapely.intersects(segs, g).sum())


def drag_events(ctx, pv):
    out = []
    if pv.type in SERVICE:
        return out
    near, who = pv.nearest_asset()
    for i, j, fire in drag_runs(pv):
        k = int(i + np.argmin(near[i:j + 1]))
        aid = who[k]
        before = pv.sog[max(0, i - 45):i]
        before = before[before > 4]
        dark = bool(pv.dark_mask[i:j + 1].mean() > 0.5)
        n = crossings(pv, aid, i, j)
        sp = pv.sog[i:j + 1]
        summary = (f"{pv.name} crept over {ctx.asset_label(aid)} at {sp.min():.1f}–{sp.max():.1f} kn with its heading "
                   f"swinging, the anchor-drag pattern seen in the Eagle S case.")
        facts = [f"Speed {sp.min():.1f}–{sp.max():.1f} kn for {fmt_dur(pv.t[j] - pv.t[i])}" + (f" ({np.median(before):.1f} kn before)" if len(before) else ""),
                 f"Heading swung up to {pv.swing[i + 1:j + 1].max():.0f}° between fixes",
                 f"Track crossed {ctx.asset_label(aid)} {n} time{'s' if n != 1 else ''}, closest {near[k] / 1000:.2f} km" if n else f"Closest to {ctx.asset_label(aid)}: {near[k] / 1000:.2f} km",
                 "AIS off throughout; positions from SAR and RF" if dark else None]
        out.append(_ev(pv, "anchor_drag_pattern", pv.t[i], pv.t[j], pv.lon[k], pv.lat[k], "high", summary, facts,
                       "fusion", asset_id=aid))
    return out


def loiter_events(ctx, pv, exclude):
    out = []
    if pv.type in NO_LOITER or len(pv.t) < 3:
        return out
    sog = np.nan_to_num(pv.sog, nan=99)
    m = (sog < 2) & ~pv.in_anchorage & ~exclude
    near, who = pv.nearest_asset()
    for i, j in runs(m):
        if pv.t[j] - pv.t[i] < 60 * 60:
            continue
        k = int(i + np.argmin(near[i:j + 1]))
        aid = who[k] if near[k] < 5000 else None
        anch = ctx.nearest_anchorage(pv.lon[k], pv.lat[k])
        summary = (f"{pv.name} idled under 2 kn for {fmt_dur(pv.t[j] - pv.t[i])} outside any anchorage"
                   + (f", {near[k] / 1000:.1f} km from {ctx.asset_label(aid)}." if aid else "."))
        facts = [f"Under 2 kn {fmt_t(pv.t[i])}–{fmt_t(pv.t[j])}, average {sog[i:j + 1].mean():.1f} kn",
                 f"Not in a designated anchorage; {anch[0]} is {anch[1]:.0f} km away" if anch else "Not in a designated anchorage",
                 f"Closest to {ctx.asset_label(aid)}: {near[k] / 1000:.2f} km" if aid else None,
                 "AIS on and matching radar" if (pv.state[i:j + 1] == 0).all() else None]
        out.append(_ev(pv, "loitering", pv.t[i], pv.t[j], pv.lon[k], pv.lat[k], "medium" if aid else "low",
                       summary, facts, "fusion", asset_id=aid))
    return out


def match_observations(ctx, pv):
    for o in pv.v.get("observed") or []:
        c = pv.claimed_at(o["t"])
        if c is None:
            o["matched_to_ais"] = False
            continue
        ox, oy = ctx.xy(o["lon"], o["lat"])
        cx, cy = ctx.xy(c[1], c[2])
        o["matched_to_ais"] = bool(np.hypot(ox - cx, oy - cy) <= MATCH_M)


def states_of(pv):
    if not len(pv.t):
        return []
    names = ["verified", "dark", "spoofing"]
    st = pv.state
    out = []
    for i, j in [(a, b) for s in range(3) for a, b in runs(st == s)]:
        out.append({"start": int(pv.t[i]), "end": int(pv.t[j + 1] if j + 1 < len(pv.t) else pv.t[j]),
                    "state": names[st[i]]})
    return sorted(out, key=lambda s: s["start"])


# ---------------------------------------------------------------- risk

def risk_of(ctx, pv, events):
    """Score 0-100 from plain reasons. Same kind counts once (its strongest instance), like cable-watch."""
    rs = []
    mine = [e for e in events if e["vessel_id"] == pv.id]

    def add(kind, points, text, source, t):
        rs.append({"kind": kind, "points": int(points), "text": text, "source": source, "t": int(t)})

    near, who = pv.nearest_asset()
    coastal = None
    # 1. AIS gaps (cable-watch: 25, +10 if it slowed during the gap, +5 if ships nearby kept transmitting).
    for d in pv.dark:
        if d["minutes"] < 20:
            continue
        m = (pv.t >= d["start"]) & (pv.t <= d["end"])
        if d["minutes"] < 45:
            add("ais_gap", 10, f"AIS off {d['minutes']} min", "ais", d["start"])
            continue
        pts, extra = 25, []
        i, j = np.flatnonzero(m)[[0, -1]]
        implied = math.hypot(pv.x[j] - pv.x[i], pv.y[j] - pv.y[i]) / max(pv.t[j] - pv.t[i], 1) / KN
        before = pv.sog[max(0, i - 5):i + 1]
        before = float(np.median(before)) if len(before) else float("nan")
        if before > 3 and implied < 0.6 * before:
            pts += 10
            extra.append(f"it slowed to {implied:.1f} kn meanwhile ({before:.1f} kn before)")
        others = 0
        for ov in ctx.vessels:
            if ov is pv or not len(ov.ct):
                continue
            cm = (ov.ct > d["start"]) & (ov.ct < d["end"])
            if cm.any():
                dist = np.min(np.hypot(ov.cx[cm] - pv.x[i], ov.cy[cm] - pv.y[i]))
                others += int(dist < 10_000)
        if others:
            pts += 5
            extra.append(f"{others} ship{'s' if others > 1 else ''} nearby kept transmitting, so not a coverage hole")
        seen = [o for o in pv.obs if d["start"] < o["t"] < d["end"]]
        k = int(np.argmin(np.where(m, near, np.inf)))
        where = f" over {ctx.asset_label(who[k])}" if near[k] < 2000 else f" {near[k] / 1000:.0f} km from {ctx.asset_label(who[k])}" if near[k] < 10_000 else ""
        text = f"AIS off {d['minutes']} min" + (f" while radar still saw the hull{where}" if seen else where)
        if extra:
            text += "; " + "; ".join(extra)
        add("ais_gap", pts, text + " (cable-watch AIS-gap rule, as in the Yi Peng 3 case)", "ais", d["start"])
    for d in pv.archive:
        if d["start"] >= ctx.end - 30 * DAY:
            add("ais_gap", 30 if d["minutes"] >= 360 else 25,
                f"AIS off {fmt_dur(d['minutes'] * 60)} on {time.strftime('%d %b', time.gmtime(d['start'])).lstrip('0')}"
                + (f" ({d['note']})" if d.get("note") else ""), "ais", d["start"])
    if pv.type not in SERVICE and len(pv.t) > 2:
        sog = np.nan_to_num(pv.sog, nan=-1)
        coastal = near_land(pv.lon, pv.lat, 0.025)
        over = (near < 1000) & ~coastal & ~pv.in_anchorage
        # 2. Stopped over a cable (30, or 40 beyond 6 h).
        for i, j in runs(over & (sog >= 0) & (sog < 0.5)):
            if pv.t[j] - pv.t[i] >= 30 * 60:
                h = (pv.t[j] - pv.t[i]) / 3600
                add("stopped", 30 if h < 6 else 40, f"Stopped within 1 km of {ctx.asset_label(who[i])} for {fmt_dur(h * 3600)} "
                    "(cable-watch stopped-over-cable rule, as in the Hong Tai 58 case)", "fusion", pv.t[i])
        # 3. Slow over a cable (25, or 35 beyond an hour).
        for i, j in runs(over & (sog >= 0.5) & (sog < 5)):
            mins = (pv.t[j] - pv.t[i]) / 60
            if mins >= 20:
                add("slow", 25 if mins < 60 else 35, f"Under 5 kn within 1 km of {ctx.asset_label(who[i])} for {mins:.0f} min, "
                    f"average {sog[i:j + 1].mean():.1f} kn (cable-watch slow-over-cable rule)", "fusion", pv.t[i])
        # 4. Speed drop near a cable while under way: the Eagle S anchor-drag signature.
        uw = sog[(sog > 1) & ~coastal]
        if len(uw) > 20 and np.median(uw) > 6:
            med = float(np.median(uw))
            drop = (sog > 1) & (sog < 0.6 * med) & ~coastal
            for i, j in runs(drop):
                if pv.t[j] - pv.t[i] >= 30 * 60 and near[i:j + 1].min() < 3000:
                    k = int(i + np.argmin(near[i:j + 1]))
                    sw = pv.swing[i + 1:j + 1].max() if j > i else 0
                    add("speed_drop", 25, f"Speed fell from {med:.1f} to {sog[i:j + 1].mean():.1f} kn for "
                        f"{fmt_dur(pv.t[j] - pv.t[i])} near {ctx.asset_label(who[k])}"
                        + (f", heading swinging up to {sw:.0f}°" if sw >= 40 else "")
                        + ": the anchor-drag pattern seen in the Eagle S case", "fusion", pv.t[i])
        # 5. Zigzag over a cable (cable-watch: >= 3 course reversals over 100° within 2 km, moving > 2 kn).
        mv = (near < 2000) & (sog > 2)
        rev = np.flatnonzero(mv & (pv.swing > 100))
        if len(rev) >= 3:
            add("zigzag", 20, f"Reversed course {len(rev)} times within 2 km of {ctx.asset_label(who[rev[0]])} "
                "(cable-watch zigzag rule, as in the Hong Tai 58 case)", "fusion", pv.t[rev[0]])
    # 6. False position (Skynet rule): AIS says one place, radar sees the hull somewhere else.
    for e in mine:
        if e["type"] == "position_mismatch":
            on_land = any("on land" in f for f in e["facts"])
            add("false_position", 40, "AIS put it on land while radar saw it at sea"
                + (f" {e['summary'].split(' at sea ')[1].split(' from ')[0]} away" if " at sea " in e["summary"] else "")
                if on_land else e["summary"].rstrip("."), "sar", e["t"])
        if e["type"] == "spoofing":
            sig = [f for f in e["facts"] if "jumped" in f or "crop circle" in f]
            if sig:
                add("spoof_signature", 20, "AIS shows GNSS spoofing signatures: " + "; ".join(
                    s.split(",")[0].replace("Reported positions traced", "positions traced").replace("Position jumped", "a position jump of")
                    for s in sig), "ais", e["t"])
        if e["type"] == "ship_to_ship_meeting":
            o = ctx.by_id.get(e["other_vessel_id"])
            add("sts", 20, f"Lay alongside {o.name if o else e['other_vessel_id']} at sea for {fmt_dur(e['end'] - e['t'])}, "
                "outside any port area (ship-to-ship transfer pattern)", "fusion", e["t"])
        if e["type"] == "loitering" and not any(r["kind"] in ("stopped", "slow") for r in rs):
            add("loitering", 10, e["summary"].replace(f"{pv.name} idled", "Idled").rstrip("."), "fusion", e["t"])
    # 7. Identity change within 30 days (flag hopping, a watchlist proxy in cable-watch's sources).
    h = sorted(pv.v.get("identity_history") or [], key=lambda x: x["t"])
    for a, b in zip(h, h[1:]):
        if b["t"] >= ctx.end - 30 * DAY:
            add("identity_change", 20, f"Changed name and flag {time.strftime('%d %b', time.gmtime(b['t'])).lstrip('0')} "
                f"({a.get('name')}, {a.get('flag')} → {b.get('name')}, {b.get('flag')}); same IMO", "registry", b["t"])
    # 8. Watchlists and designated owners (cable-watch watchlist rule: 20).
    lists = list(pv.v.get("watchlists") or [])
    own = pv.v.get("owner") or {}
    if own.get("designated"):
        lists.append(f"owner {own.get('name')} on the {own.get('list', 'sanctions list')}")
    if lists:
        add("watchlist", 20, "Listed: " + "; ".join(lists) + " (cable-watch watchlist rule)", "sanctions", ctx.start)
    # 9. Planned route through a war-risk area.
    voy = pv.v.get("voyage") or {}
    if "hormuz" in (voy.get("via") or "").lower():
        add("route", 15, f"Next voyage {voy.get('label')} runs via the Strait of Hormuz; check the current JWC listed "
            "areas and JMIC advisories", "fusion", ctx.start)
    best = {}
    for r in rs:
        if r["kind"] not in best or r["points"] > best[r["kind"]]["points"]:
            best[r["kind"]] = r
    reasons = sorted(best.values(), key=lambda r: -r["points"])
    score = min(100, sum(r["points"] for r in reasons))
    level = "high" if score >= 60 else "medium" if score >= 40 else "low"
    return {"score": score, "level": level, "reasons": reasons}


def risk_at(risk, t):
    """Score as it stood at time t (reasons that had happened by then)."""
    best = {}
    for r in (risk or {}).get("reasons", []):
        if r.get("t", 0) <= t:
            best[r.get("kind", r["text"])] = max(best.get(r.get("kind", r["text"]), 0), r["points"])
    return min(100, sum(best.values()))


# ---------------------------------------------------------------- merge zone results

_DUP = {"dark_period", "anchor_drag_pattern", "loitering", "position_mismatch", "spoofing", "ship_to_ship_meeting",
        "identity_change"}


def _overlap(a, b):
    a0, a1 = a["t"], a["end"] if a["end"] is not None else a["t"]
    b0, b1 = b["t"], b["end"] if b["end"] is not None else b["t"]
    return a0 <= b1 + 600 and b0 <= a1 + 600


def merge_zone(events, zev, zal, zone):
    """Fold a zone's rule hits into the world events: reuse a matching global event instead of duplicating it."""
    remap = {}
    for z in zev:
        hit = None
        if z["type"] in _DUP:
            for e in events:
                if e["vessel_id"] == z["vessel_id"] and e["type"] == z["type"] and _overlap(e, z):
                    hit = e
                    break
        if hit is not None:
            hit["zone_id"] = hit["zone_id"] or zone["id"]
            hit["asset_id"] = hit["asset_id"] or z.get("asset_id")
            remap[z["id"]] = hit["id"]
        else:
            z = dict(z)
            remap[z["id"]] = z["id"]
            events.append(z)
    for a in zal:
        a["event_ids"] = list(dict.fromkeys(remap.get(i, i) for i in a["event_ids"]))
    return events, zal


# ---------------------------------------------------------------- main

def fuse(world):
    from . import zones as Z

    world = copy.deepcopy(world)
    ctx = Ctx(world)
    events = []
    for pv in ctx.vessels:
        match_observations(ctx, pv)
        pv.v["states"] = states_of(pv)
        pv.v["dark_periods"] = sorted(
            [{"start": d["start"], "end": d["end"], "minutes": d["minutes"], **({"ongoing": True} if d["ongoing"] else {})}
             for d in pv.dark]
            + [{"start": d["start"], "end": d["end"], "minutes": d["minutes"], "archive": True,
                "lon": d.get("lon"), "lat": d.get("lat")} for d in pv.archive], key=lambda d: d["start"])
        events += dark_events(ctx, pv) + mismatch_events(ctx, pv) + spoof_events(ctx, pv) + identity_events(ctx, pv)
    events += sts_events(ctx)
    for pv in ctx.vessels:
        dr = drag_events(ctx, pv)
        events += dr
        excl = np.zeros(len(pv.t), bool)
        for e in dr:
            excl |= (pv.t >= e["t"]) & (pv.t <= e["end"])
        for s in ctx.sts():
            if pv.id in (s["a"], s["b"]):
                excl |= (pv.t >= s["t0"] - 1800) & (pv.t <= s["t1"] + 1800)
        events += loiter_events(ctx, pv, excl)
    for pv in ctx.vessels:
        pv.v["risk"] = risk_of(ctx, pv, events)
    events.sort(key=lambda e: (e["t"], e["vessel_id"], e["type"]))
    for n, e in enumerate(events, 1):
        e["id"] = f"E-{n:04d}"
    # Zone-driven events and alerts.
    alerts = []
    for zone in world.get("zones") or []:
        res = Z.evaluate_with(ctx, zone)
        events, zal = merge_zone(events, res["events"], res["alerts"], zone)
        alerts += zal
        poly = Z.zone_polygon(zone, world)
        if poly:
            zone["polygon"] = poly
    # Final ids, in time order.
    events.sort(key=lambda e: (e["t"], e["vessel_id"], e["type"]))
    remap = {}
    for n, e in enumerate(events, 1):
        remap[e["id"]] = f"E-{n:04d}"
        e["id"] = remap[e["id"]]
    alerts.sort(key=lambda a: a["t"])
    for n, a in enumerate(alerts, 1):
        a["id"] = f"A-{n:04d}"
        a["event_ids"] = [remap.get(i, i) for i in a["event_ids"]]
    world["events"] = events
    world["alerts"] = alerts
    world.setdefault("actions", [])
    return world
