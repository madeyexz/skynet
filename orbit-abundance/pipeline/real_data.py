"""Pull real Copernicus data for the eastern Libya -> Crete corridor and write web/data/real.json.

What it measures, all from public data:
  1. Revisit today: every Sentinel-1 and Sentinel-2 acquisition over three points
     (Tobruk, mid-corridor, Gavdos approach) in the last DAYS days, and the gaps between them.
  2. Latency today: ESA processing time minus sensing end time for each Sentinel-1 product.
  3. Ship detections: a CFAR-style detector on Sentinel-1 GRD (VV+VH) over Tobruk port and
     anchorage for every pass, and over a 33 km wide strip of the open-sea corridor for the
     latest passes.

Run: .venv/bin/python pipeline/real_data.py
"""

import datetime as dt
import json
import math
import os
import sys

import numpy as np
import planetary_computer as pc
import pystac_client
import rasterio
import shapefile
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject
from scipy import ndimage
from shapely.geometry import Point, box, shape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "web", "data")
CACHE = os.path.join(ROOT, "data_cache")
os.makedirs(OUT, exist_ok=True)

END = dt.datetime(2026, 11, 11, 23, 59, tzinfo=dt.timezone.utc)
DAYS = 60
START = END - dt.timedelta(days=DAYS)

POINTS = {
    "tobruk": {"name": "Tobruk port (departure)", "lon": 23.98, "lat": 32.08},
    "mid": {"name": "Mid-corridor (open sea)", "lon": 24.05, "lat": 33.40},
    "gavdos": {"name": "South of Gavdos (arrival approach)", "lon": 24.08, "lat": 34.65},
}
TOBRUK_BBOX = (23.85, 31.99, 24.20, 32.17)  # harbour bay + outer anchorage
TOBRUK_RES = 0.0001  # ~10 m
STRIP_BBOX = (23.88, 32.25, 24.23, 34.70)  # open-sea strip, Tobruk -> Gavdos (~33 x 270 km)
STRIP_RES = 0.00015  # ~15 m
STRIP_PASSES = 4

cat = pystac_client.Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1", modifier=pc.sign_inplace
)


def log(*a):
    print(*a, flush=True)
    sys.stdout.flush()


def search(coll, geom):
    items = cat.search(
        collections=[coll],
        intersects=geom,
        datetime=f"{START.isoformat()}/{END.isoformat()}",
        max_items=2000,
    ).items()
    return list(items)


def parse_t(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------------------------------------------- revisit + latency


def s1_revisit(items):
    out = {}
    for key, p in POINTS.items():
        pt = Point(p["lon"], p["lat"])
        acq = {}
        for it in items:
            if it.properties.get("sar:instrument_mode") != "IW":
                continue
            if not shape(it.geometry).contains(pt):
                continue
            t = it.datetime
            k = (it.properties["sat:absolute_orbit"], it.properties.get("platform", it.id[:3]))
            end = parse_t(it.properties["end_datetime"])
            proc = it.properties.get("s1:processing_datetime")
            lat_min = (parse_t(proc) - end).total_seconds() / 60 if proc else None
            acq[k] = {
                "t": t.isoformat(),
                "platform": it.id[:3],
                "orbit": it.properties["sat:orbit_state"],
                "timeliness": it.properties.get("s1:product_timeliness"),
                "latency_min": round(lat_min, 1) if lat_min is not None else None,
            }
        out[key] = sorted(acq.values(), key=lambda a: a["t"])
    return out


def s2_revisit(items):
    out = {}
    for key, p in POINTS.items():
        pt = Point(p["lon"], p["lat"])
        acq = {}
        for it in items:
            if not shape(it.geometry).contains(pt):
                continue
            k = it.datetime.strftime("%Y%m%d%H%M")
            cc = it.properties.get("eo:cloud_cover")
            prev = acq.get(k)
            if prev is None or (cc is not None and cc < prev["cloud"]):
                acq[k] = {"t": it.datetime.isoformat(), "platform": it.id[:3], "cloud": round(cc, 1), "id": it.id}
        out[key] = sorted(acq.values(), key=lambda a: a["t"])
    return out


def gap_stats(acqs):
    ts = sorted(parse_t(a["t"]) for a in acqs)
    gaps = [(b - a).total_seconds() / 3600 for a, b in zip(ts, ts[1:])]
    if not gaps:
        return {"n": len(ts)}
    return {
        "n": len(ts),
        "per_week": round(len(ts) / DAYS * 7, 2),
        "mean_gap_h": round(float(np.mean(gaps)), 1),
        "median_gap_h": round(float(np.median(gaps)), 1),
        "max_gap_h": round(float(np.max(gaps)), 1),
    }


# ---------------------------------------------------------------- raster helpers


def fixed_grid(bbox, res):
    w = int(round((bbox[2] - bbox[0]) / res))
    h = int(round((bbox[3] - bbox[1]) / res))
    return from_origin(bbox[0], bbox[3], res, res), (h, w)


def read_s1_band(href, bbox, tr, shp):
    """Read a GRD band (GCP geometry) into a fixed EPSG:4326 grid. 0 = no data."""
    dst = np.zeros(shp, dtype=np.float32)
    with rasterio.open(href) as src, WarpedVRT(src, crs="EPSG:4326") as vrt:
        vb = vrt.bounds
        ib = (max(bbox[0], vb.left), max(bbox[1], vb.bottom), min(bbox[2], vb.right), min(bbox[3], vb.top))
        if ib[0] >= ib[2] or ib[1] >= ib[3]:
            return dst
        win = vrt.window(*ib).round_offsets().round_lengths()
        a = vrt.read(1, window=win).astype(np.float32)
        wtr = vrt.window_transform(win)
    reproject(a, dst, src_transform=wtr, src_crs="EPSG:4326", dst_transform=tr, dst_crs="EPSG:4326",
              src_nodata=0, dst_nodata=0, resampling=Resampling.nearest)
    return dst


def land_mask_ne(bbox, tr, shp):
    r = shapefile.Reader(os.path.join(CACHE, "ne_10m_land.shp"))
    bb = box(*bbox).buffer(0.2)
    geoms = []
    for s in r.shapes():
        g = shape(s.__geo_interface__)
        if g.intersects(bb):
            geoms.append(g.intersection(bb))
    if not geoms:
        return np.zeros(shp, bool)
    return rasterize(geoms, out_shape=shp, transform=tr, fill=0, default_value=1).astype(bool)


def ring_mean(img, valid, outer, guard):
    """Mean of img over valid pixels in an outer window minus an inner guard window."""
    x = np.where(valid, img, 0).astype(np.float32)
    v = valid.astype(np.float32)
    so = ndimage.uniform_filter(x, outer) * outer * outer
    vo = ndimage.uniform_filter(v, outer) * outer * outer
    sg = ndimage.uniform_filter(x, guard) * guard * guard
    vg = ndimage.uniform_filter(v, guard) * guard * guard
    n = np.maximum(vo - vg, 1)
    return (so - sg) / n, (vo - vg) / (outer * outer - guard * guard)


def detect(vv, vh, water, tr, px_m, t_db=11.0):
    """Two-polarisation local-contrast (CFAR-like) detector. Returns list of detections."""
    valid = water & (vv > 0) & (vh > 0)
    iv, ih = vv ** 2, vh ** 2
    outer, guard = (61, 21) if px_m <= 11 else (41, 15)
    bv, fv = ring_mean(iv, valid, outer, guard)
    bh, _ = ring_mean(ih, valid, outer, guard)
    thr = 10 ** (t_db / 10)
    cand = valid & (fv > 0.5) & (((iv > thr * bv) & (ih > 2.5 * bh)) | (ih > thr * 1.6 * bh) & (iv > 2.5 * bv))
    cand = ndimage.binary_closing(cand, iterations=1)
    lab, n = ndimage.label(cand)
    dets = []
    if n == 0:
        return dets
    objs = ndimage.find_objects(lab)
    for i, sl in enumerate(objs, start=1):
        m = lab[sl] == i
        area = int(m.sum())
        if area < 2:
            continue
        rows, cols = np.nonzero(m)
        rows = rows + sl[0].start
        cols = cols + sl[1].start
        lon, lat = tr * (cols.mean() + 0.5, rows.mean() + 0.5)
        mx = 111320 * math.cos(math.radians(lat)) * tr.a
        my = 111320 * abs(tr.e)
        pts = np.stack([cols * mx, rows * my], 1)
        if len(pts) > 2:
            c = np.cov(pts.T)
            ev = np.linalg.eigvalsh(c)
            major = 4 * math.sqrt(max(ev[-1], 0))
        else:
            major = 0
        ext = max(major, math.sqrt(area * mx * my))
        # Sentinel-1 IW resolution is ~20 m; subtract part of the blur and floor at 10 m.
        length = max(10.0, ext - 10.0)
        peak = float(10 * np.log10(max(iv[rows, cols].max() / max(bv[rows, cols].mean(), 1e-6), 1e-6)))
        dets.append({"lon": round(float(lon), 5), "lat": round(float(lat), 5), "len_m": round(length),
                     "px": area, "contrast_db": round(peak, 1)})
    # merge sidelobe fragments of the same ship (within 150 m)
    dets.sort(key=lambda d: -d["px"])
    kept = []
    for d in dets:
        near = False
        for k in kept:
            dx = (d["lon"] - k["lon"]) * 111320 * math.cos(math.radians(d["lat"]))
            dy = (d["lat"] - k["lat"]) * 111320
            if dx * dx + dy * dy < max(150, k["len_m"] * 0.8) ** 2:
                near = True
                break
        if not near:
            kept.append(d)
    return kept


def to_png(vv, path, dets, tr, max_side=1400, mask=None):
    db = 10 * np.log10(np.maximum(vv.astype(np.float32) ** 2, 1))
    lo, hi = np.percentile(db[vv > 0], [2, 99.7]) if (vv > 0).any() else (0, 1)
    g = np.clip((db - lo) / (hi - lo), 0, 1)
    rgb = np.stack([g * 255] * 3, -1).astype(np.uint8)
    rgb[vv == 0] = (20, 24, 32)
    img = Image.fromarray(rgb)
    s = min(1.0, max_side / max(img.size))
    if s < 1:
        img = img.resize((int(img.size[0] * s), int(img.size[1] * s)), Image.LANCZOS)
    img.save(path, optimize=True)
    return s


# ---------------------------------------------------------------- Sentinel-2 water mask + RGB chip


def s2_tobruk(s2_items):
    tr, shp = fixed_grid(TOBRUK_BBOX, TOBRUK_RES)
    bb = box(*TOBRUK_BBOX)
    cands = [it for it in s2_items if shape(it.geometry).contains(bb)]
    cands.sort(key=lambda it: it.properties["eo:cloud_cover"])
    it = cands[0]
    log("S2 water mask from", it.id, it.properties["eo:cloud_cover"])

    def rd(asset, resamp=Resampling.bilinear):
        dst = np.zeros(shp, np.float32)
        with rasterio.open(it.assets[asset].href) as src:
            from rasterio.warp import transform_bounds
            sb = transform_bounds("EPSG:4326", src.crs, *TOBRUK_BBOX)
            win = src.window(*sb).round_offsets().round_lengths()
            a = src.read(window=win).astype(np.float32)
            reproject(a, dst if a.shape[0] == 1 else np.zeros((a.shape[0],) + shp, np.float32),
                      src_transform=src.window_transform(win), src_crs=src.crs, dst_transform=tr,
                      dst_crs="EPSG:4326", resampling=resamp)
            if a.shape[0] > 1:
                out = np.zeros((a.shape[0],) + shp, np.float32)
                reproject(a, out, src_transform=src.window_transform(win), src_crs=src.crs, dst_transform=tr,
                          dst_crs="EPSG:4326", resampling=resamp)
                return out
        return dst

    # L2A processing baseline >= 04.00 adds a +1000 offset to reflectance
    g = rd("B03") - 1000
    n = rd("B08") - 1000
    ndwi = (g - n) / np.maximum(np.abs(g + n), 1)
    water = (ndwi > 0.0) & (n < 600)
    water = ndimage.binary_opening(water, iterations=2)
    # keep only the large connected sea body (drop inland pans)
    lab, k = ndimage.label(water)
    if k:
        sizes = ndimage.sum(water, lab, range(1, k + 1))
        water = np.isin(lab, 1 + np.nonzero(sizes > 20000)[0])
    tci = rd("visual")
    rgb = np.moveaxis(tci, 0, -1).clip(0, 255).astype(np.uint8)
    img = Image.fromarray(rgb)
    s = 1400 / max(img.size)
    img = img.resize((int(img.size[0] * s), int(img.size[1] * s)), Image.LANCZOS)
    img.save(os.path.join(OUT, "tobruk_s2.png"), optimize=True)
    return water, {"id": it.id, "t": it.datetime.isoformat(), "cloud": it.properties["eo:cloud_cover"]}


# ---------------------------------------------------------------- main


def group_datatakes(items):
    dts = {}
    for it in items:
        if it.properties.get("sar:instrument_mode") != "IW":
            continue
        k = (it.id[:3], it.properties["sat:absolute_orbit"])
        dts.setdefault(k, []).append(it)
    return dts


def main():
    corridor = box(22.5, 31.9, 25.5, 35.0).__geo_interface__
    log("searching S1 ...")
    s1 = search("sentinel-1-grd", corridor)
    log("S1 items", len(s1))
    log("searching S2 ...")
    s2 = search("sentinel-2-l2a", corridor)
    log("S2 items", len(s2))

    res = {"window": {"start": START.isoformat(), "end": END.isoformat(), "days": DAYS}, "points": POINTS}
    r1 = s1_revisit(s1)
    r2 = s2_revisit(s2)
    res["s1"] = {k: {"acq": v, "stats": gap_stats(v)} for k, v in r1.items()}
    res["s2"] = {}
    for k, v in r2.items():
        clear = [a for a in v if a["cloud"] <= 20]
        res["s2"][k] = {"acq": v, "stats": gap_stats(v), "clear_stats": gap_stats(clear)}
    lats = [a["latency_min"] for v in r1.values() for a in v if a["latency_min"] is not None]
    res["s1_latency_min"] = {"median": float(np.median(lats)), "p90": float(np.percentile(lats, 90)),
                             "min": float(np.min(lats)), "n": len(lats)}
    log(json.dumps({k: v["stats"] for k, v in res["s1"].items()}), res["s1_latency_min"])
    log(json.dumps({k: [v["stats"], v["clear_stats"]] for k, v in res["s2"].items()}))

    # Tobruk port time series
    water, s2meta = s2_tobruk(s2)
    land_buf = ndimage.binary_dilation(~water, iterations=15)  # ~150 m from quay or shore
    water_core = water & ~land_buf
    tr, shp = fixed_grid(TOBRUK_BBOX, TOBRUK_RES)
    bb = box(*TOBRUK_BBOX)
    series = []
    dts = group_datatakes(s1)
    latest = None
    for k, its in sorted(dts.items(), key=lambda kv: kv[1][0].datetime):
        its = [it for it in its if shape(it.geometry).intersects(bb)]
        if not its:
            continue
        vv = np.zeros(shp, np.float32)
        vh = np.zeros(shp, np.float32)
        for it in its:
            vv = np.maximum(vv, read_s1_band(it.assets["vv"].href, TOBRUK_BBOX, tr, shp))
            vh = np.maximum(vh, read_s1_band(it.assets["vh"].href, TOBRUK_BBOX, tr, shp))
        cov = float(((vv > 0) & water_core).sum() / max(water_core.sum(), 1))
        if cov < 0.9:
            log("skip partial", its[0].id, round(cov, 2))
            continue
        d = detect(vv, vh, water_core, tr, 10)
        rec = {"t": its[0].datetime.isoformat(), "id": its[0].id, "orbit": its[0].properties["sat:orbit_state"],
               "n": len(d), "n_small": sum(1 for x in d if x["len_m"] < 40),
               "n_mid": sum(1 for x in d if 40 <= x["len_m"] < 100), "n_large": sum(1 for x in d if x["len_m"] >= 100),
               "dets": d}
        series.append(rec)
        log("tobruk", rec["t"][:16], rec["orbit"], rec["n"], rec["n_small"], rec["n_mid"], rec["n_large"])
        latest = (vv, rec)
    if latest:
        vv, rec = latest
        to_png(vv, os.path.join(OUT, "tobruk_s1_latest.png"), rec["dets"], tr)
    res["tobruk"] = {"bbox": TOBRUK_BBOX, "s2_mask_scene": s2meta, "series": series,
                     "water_km2": round(float(water_core.sum()) * (TOBRUK_RES * 111.32) ** 2
                                        * math.cos(math.radians(32.08)), 1)}

    # open-sea strip, latest passes
    tr2, shp2 = fixed_grid(STRIP_BBOX, STRIP_RES)
    land = land_mask_ne(STRIP_BBOX, tr2, shp2)
    sea = ~ndimage.binary_dilation(land, iterations=40)  # ~600 m off the coastline
    sb = box(*STRIP_BBOX)
    strip_runs = []
    order = sorted(dts.items(), key=lambda kv: kv[1][0].datetime, reverse=True)
    for k, its in order:
        if len(strip_runs) >= STRIP_PASSES:
            break
        its = [it for it in its if shape(it.geometry).intersects(sb)]
        covered = sum(shape(it.geometry).intersection(sb).area for it in its) / sb.area
        if covered < 0.6:
            continue
        log("strip pass", its[0].id, round(covered, 2), len(its), "slices")
        dets_all = []
        n_rows = shp2[0]
        chunk = 1600
        quick = []
        cov_px = 0
        for r0 in range(0, n_rows, chunk - 80):
            r1 = min(n_rows, r0 + chunk)
            top = STRIP_BBOX[3] - r0 * STRIP_RES
            bot = STRIP_BBOX[3] - r1 * STRIP_RES
            cb = (STRIP_BBOX[0], bot, STRIP_BBOX[2], top)
            ctr = from_origin(cb[0], cb[3], STRIP_RES, STRIP_RES)
            cshp = (r1 - r0, shp2[1])
            vv = np.zeros(cshp, np.float32)
            vh = np.zeros(cshp, np.float32)
            for it in its:
                if not shape(it.geometry).intersects(box(*cb)):
                    continue
                vv = np.maximum(vv, read_s1_band(it.assets["vv"].href, cb, ctr, cshp))
                vh = np.maximum(vh, read_s1_band(it.assets["vh"].href, cb, ctr, cshp))
            csea = sea[r0:r1]
            cov_px += int(((vv > 0) & csea).sum()) if r0 == 0 else int(((vv[40:] > 0) & csea[40:]).sum())
            d = detect(vv, vh, csea, ctr, 15)
            # drop detections in the overlap rows already handled by the previous chunk
            d = [x for x in d if r0 == 0 or x["lat"] < top - 40 * STRIP_RES]
            dets_all += d
            quick.append(vv[: (chunk - 80) if r1 < n_rows else None][::6, ::6])
        q = np.concatenate(quick, 0)
        name = f"strip_{its[0].id[:3]}_{its[0].datetime.strftime('%Y%m%dT%H%M')}.png"
        to_png(q, os.path.join(OUT, name), [], tr2, max_side=1800)
        sea_km2 = cov_px * (STRIP_RES * 111.32) ** 2 * math.cos(math.radians(33.5))
        run = {"t": its[0].datetime.isoformat(), "platform": its[0].id[:3], "orbit": its[0].properties["sat:orbit_state"],
               "png": name, "sea_km2_imaged": round(sea_km2), "dets": dets_all,
               "n": len(dets_all), "n_small": sum(1 for x in dets_all if x["len_m"] < 40),
               "n_mid": sum(1 for x in dets_all if 40 <= x["len_m"] < 100),
               "n_large": sum(1 for x in dets_all if x["len_m"] >= 100)}
        log("strip", run["t"][:16], run["n"], run["n_small"], run["n_mid"], run["n_large"], run["sea_km2_imaged"])
        strip_runs.append(run)
    res["strip"] = {"bbox": STRIP_BBOX, "runs": strip_runs}
    res["generated"] = dt.datetime.now(dt.timezone.utc).isoformat()
    with open(os.path.join(OUT, "real.json"), "w") as f:
        json.dump(res, f)
    log("wrote real.json")


if __name__ == "__main__":
    main()
