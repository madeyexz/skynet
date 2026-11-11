"""Constellation access simulator for the Libya -> Crete corridor. Writes web/data/sim.json.

For each constellation size N it propagates N circular LEO satellites (J2 nodal drift, spherical
Earth) for 7 days at 30 s steps and records when each of three route points (Tobruk, mid-corridor,
south of Gavdos) is inside a satellite's field of regard. It stores pass counts in 10-minute bins,
plus daylight per bin, so the web page can compute detection odds for any boat, sensor and price.

It also simulates the real Sentinel-1C/1D pair (693 km, dawn-dusk, right-looking IW swath) so the
page can compare the model with the real acquisition record pulled by real_data.py.
"""

import json
import math
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "data")

RE = 6371.0
MU = 398600.4418
J2 = 1.08263e-3
WE = 7.2921159e-5  # rad/s
DAYS = 7
DT = 30.0
BIN_MIN = 10
T0_JD = 2461355.5  # 2026-11-11 00:00 UTC

POINTS = [("tobruk", 23.98, 32.08), ("mid", 24.05, 33.40), ("gavdos", 24.08, 34.65)]
N_LIST = [2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]
# Field of regard as a ground-range ring around the sub-satellite point (km).
# SAR: ICEYE Gen4 advertises a 400 km high-resolution field of regard; we allow 100-500 km off-track.
# Optical: off-nadir <= ~30 deg from 550 km is ~330 km.
FOR = {"sar": (100.0, 500.0), "optical": (0.0, 330.0)}


def gmst(jd):
    t = (jd - 2451545.0) / 36525
    g = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t
    return math.radians(g % 360)


def sun_ra_dec(jd):
    n = jd - 2451545.0
    L = math.radians((280.460 + 0.9856474 * n) % 360)
    g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.020) * math.sin(2 * g)
    eps = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    dec = math.asin(math.sin(eps) * math.sin(lam))
    return ra, dec


def sun_elev(lon, lat, jd):
    ra, dec = sun_ra_dec(jd)
    ha = gmst(jd) + math.radians(lon) - ra
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(dec) + math.cos(la) * math.cos(dec) * math.cos(ha)))


def shell(n, inc_deg, alt, seed, raan0=None, raan_spread=2 * math.pi):
    """Walker-like shell: P planes, RAAN spread, phases offset between planes."""
    if n <= 0:
        return np.zeros((0, 4))
    planes = max(1, int(round(math.sqrt(n))))
    per = int(math.ceil(n / planes))
    rng = np.random.default_rng(seed)
    base = rng.uniform(0, 2 * math.pi) if raan0 is None else raan0
    out = []
    for p in range(planes):
        for s in range(per):
            if len(out) >= n:
                break
            raan = base + raan_spread * p / planes
            u = 2 * math.pi * s / per + 2 * math.pi * p / (planes * per)
            out.append((math.radians(inc_deg), raan, u, alt))
    return np.array(out)


def sso_inc(alt):
    a = RE + alt
    n = math.sqrt(MU / a ** 3)
    rate = 2 * math.pi / (365.2422 * 86400)
    return math.degrees(math.acos(-rate / (1.5 * n * J2 * (RE / a) ** 2)))


def subsat(els, t):
    """els: (k,4) inc, raan0, u0, alt. t: (m,) seconds. Returns ECEF unit vectors (k,m,3) and velocity dir."""
    inc, raan0, u0, alt = [els[:, i:i + 1] for i in range(4)]
    a = RE + alt
    n = np.sqrt(MU / a ** 3)
    draan = -1.5 * n * J2 * (RE / a) ** 2 * np.cos(inc)
    raan = raan0 + draan * t[None, :]
    u = u0 + n * t[None, :]
    th = gmst(T0_JD) + WE * t[None, :]
    lon_node = raan - th
    cu, su, ci, si = np.cos(u), np.sin(u), np.cos(inc), np.sin(inc)
    co, so = np.cos(lon_node), np.sin(lon_node)
    x = co * cu - so * su * ci
    y = so * cu + co * su * ci
    z = su * si
    # velocity direction in the rotating frame (ignoring Earth rotation term for side-of-track test)
    vx = -co * su - so * cu * ci
    vy = -so * su + co * cu * ci
    vz = cu * si
    return np.stack([x, y, z], -1), np.stack([vx, vy, vz], -1)


def unit(lon, lat):
    lo, la = math.radians(lon), math.radians(lat)
    return np.array([math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)])


def access_bins(els, ring, right_only=False, chunk=200):
    t = np.arange(0, DAYS * 86400, DT)
    nb = DAYS * 24 * 60 // BIN_MIN
    counts = np.zeros((len(POINTS), nb), np.int32)
    tg = np.stack([unit(lo, la) for _, lo, la in POINTS])  # (3,3)
    rmin, rmax = ring[0] / RE, ring[1] / RE
    for c0 in range(0, len(els), chunk):
        e = els[c0:c0 + chunk]
        r, v = subsat(e, t)  # (k,m,3)
        dots = np.einsum("kmi,pi->kmp", r, tg).clip(-1, 1)
        ang = np.arccos(dots)
        if right_only:
            side = np.einsum("kmi,pi->kmp", np.cross(v, r), tg)
        m = len(t)
        for p in range(len(POINTS)):
            a = ang[:, :, p] <= rmax
            # One overflight = one contiguous run within rmax. Its closest approach (the run's
            # minimum ground range, at mid-run) must fall inside the ring [rmin, rmax]: that is
            # the cross-track distance a side-looking or off-nadir sensor has to reach.
            pad = np.concatenate([np.zeros((a.shape[0], 1), bool), a, np.zeros((a.shape[0], 1), bool)], 1)
            d = np.diff(pad.astype(np.int8), axis=1)
            ks, starts = np.nonzero(d == 1)
            _, ends = np.nonzero(d == -1)
            if len(starts) == 0:
                continue
            flat = ang[:, :, p].ravel()
            g0 = ks * m + starts
            idx = np.empty(2 * len(g0), np.int64)
            idx[0::2] = g0
            idx[1::2] = np.minimum(ks * m + ends, flat.size - 1)
            mins = np.minimum.reduceat(flat, idx)[0::2]
            midi = (starts + ends - 1) // 2
            ok = mins >= rmin
            if right_only:
                ok &= side[ks, midi, p] > 0
            b = (midi * DT // (BIN_MIN * 60)).astype(int)
            sel = ok & (b < nb)
            np.add.at(counts[p], b[sel], 1)
    return counts


def gap_stats(counts):
    out = []
    for p in range(counts.shape[0]):
        idx = np.nonzero(counts[p])[0]
        per_week = float(counts[p].sum()) / DAYS * 7
        if len(idx) < 2:
            out.append({"per_week": per_week, "mean_gap_h": None, "max_gap_h": None})
            continue
        g = np.diff(idx) * BIN_MIN / 60
        g = np.append(g, (idx[0] + counts.shape[1] - idx[-1]) * BIN_MIN / 60)
        out.append({"per_week": round(per_week, 2), "mean_gap_h": round(float(g.mean()), 2),
                    "max_gap_h": round(float(g.max()), 2)})
    return out


def constellation(n):
    """Notional abundant constellation: half sun-synchronous at 550 km, half 53 deg like Starlink shells."""
    sso = shell(n - n // 2, sso_inc(550), 550, seed=n)
    mid = shell(n // 2, 53.0, 550, seed=n + 1)
    return np.concatenate([sso, mid]) if len(mid) else sso


def main():
    nb = DAYS * 24 * 60 // BIN_MIN
    day = np.zeros((len(POINTS), nb), np.int8)
    for p, (_, lo, la) in enumerate(POINTS):
        for b in range(nb):
            jd = T0_JD + (b + 0.5) * BIN_MIN / 1440
            day[p, b] = 1 if sun_elev(lo, la, jd) > 10 else 0

    res = {"days": DAYS, "bin_min": BIN_MIN, "points": [p[0] for p in POINTS], "n_list": N_LIST,
           "for_km": FOR, "daylight": day.tolist(), "sar": {}, "optical": {}, "stats": {"sar": {}, "optical": {}}}

    # Sentinel-1C/1D: 693 km, 98.18 deg, ascending node 18:00 local, 180 deg apart, right-looking IW 29-46 deg incidence
    ra, _ = sun_ra_dec(T0_JD)
    raan = ra + math.radians((18 - 12) * 15)
    s1 = np.array([(math.radians(98.18), raan, 0.0, 693.0), (math.radians(98.18), raan, math.pi, 693.0)])
    s1c = access_bins(s1, (340, 600), right_only=True)
    res["s1_model"] = {"counts": s1c.tolist(), "stats": gap_stats(s1c)}
    print("S1 model", res["s1_model"]["stats"], flush=True)

    for n in N_LIST:
        els = constellation(n)
        for k, ring in FOR.items():
            c = access_bins(els, ring)
            res[k][str(n)] = c.tolist()
            res["stats"][k][str(n)] = gap_stats(c)
        print(n, res["stats"]["sar"][str(n)][1], res["stats"]["optical"][str(n)][1], flush=True)

    with open(os.path.join(OUT, "sim.json"), "w") as f:
        json.dump(res, f, separators=(",", ":"))
    print("wrote sim.json")


if __name__ == "__main__":
    main()
