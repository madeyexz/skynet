"""Orbit Analyst agent: plan a question, fetch Sentinel scenes, measure the change.

Data: Copernicus Sentinel-1 RTC and Sentinel-2 L2A from Microsoft Planetary Computer,
ESA WorldCover 2021, OpenStreetMap Nominatim, and a Danish Maritime Authority AIS extract.
"""

from __future__ import annotations

import gzip
import csv
import io
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import numpy as np
import rasterio
from PIL import Image
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import rasterize
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import reproject, transform_geom
from rasterio.vrt import WarpedVRT
from scipy import ndimage

ROOT = Path(__file__).parent
MEDIA = ROOT / "cache" / "media"
AIS_FILE = ROOT / "data" / "ais_skagen_2026-11-06.csv.gz"
STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
UA = {"User-Agent": "orbit-analyst-demo/0.1 (hackathon)"}

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif,.tiff")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("VSI_CACHE", "TRUE")

# SkyFi list prices (skyfi.com/pricing, FAQ and 2025 price sheet). Quote only.
SKYFI = {
    "sar_archive_scene": 450.0,
    "sar_tasking_scene": 675.0,
    "sar_scene_km2": 25.0,
    "optical_50cm_km2": 8.0,
    "optical_30cm_km2": 22.5,
    "optical_min_km2": 5.0,
}


class Log:
    """Collects the agent's steps so the UI can show them while it works."""

    def __init__(self, sink=None):
        self.steps: list[dict] = []
        self.t0 = time.time()
        self.sink = sink

    def __call__(self, text: str, kind: str = "step"):
        self.steps.append({"t": round(time.time() - self.t0, 1), "kind": kind, "text": text})
        if self.sink:
            self.sink(self.steps)


# ---------------------------------------------------------------- planning

MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})
MONTHS["sept"] = 9

STOP = {"how", "what", "where", "when", "which", "show", "is", "are", "was", "were", "did", "does",
        "has", "have", "the", "a", "an", "any", "find", "count", "tell", "me", "i", "my", "in", "at",
        "of", "off", "near", "around", "since", "from", "to", "after", "before", "between", "and",
        "much", "many", "new", "there", "flood", "flooding", "floods", "ships", "vessels", "dark",
        "built", "build", "construction", "change", "changed", "port", "water", "claim", "insurance"}


@dataclass
class Plan:
    question: str
    task: str                       # flood | vessels | construction
    place: str
    lat: float
    lon: float
    bbox: list[float]               # lon_min, lat_min, lon_max, lat_max
    before: tuple[str, str]
    after: tuple[str, str]
    sensor: str = "auto"            # sar | optical | auto
    res: float = 20.0
    timeline_until: str | None = None
    notes: list[str] = field(default_factory=list)
    polygon: list | None = None     # [[lon, lat], ...]: only look inside this zone


def classify(q: str) -> str:
    s = q.lower()
    if re.search(r"\b(ship|ships|vessel|vessels|boat|boats|ais|fishing|tanker|dark fleet|dark)\b", s):
        return "vessels"
    if re.search(r"\b(flood|inundat|under ?water|waterlogged|water level|submerged|monsoon|typhoon|hurricane|storm surge)", s):
        return "flood"
    return "construction"


def _end_of_month(y: int, m: int) -> date:
    return (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1))


def parse_dates(q: str, task: str, today: date | None = None):
    """Return (before_window, after_window) from phrases like 'Sep 2026', '2020 to Jan 2026'."""
    today = today or datetime.now(timezone.utc).date()
    s = q.lower()
    found: list[tuple[int, date, date]] = []  # (position, start, end)
    for m in re.finditer(r"(\d{4})-(\d{2})-(\d{2})", s):
        d = date(int(m[1]), int(m[2]), int(m[3]))
        found.append((m.start(), d, d))
    for m in re.finditer(r"\b(\d{1,2})\s+(" + "|".join(MONTHS) + r")[a-z]*\.?,?\s+(\d{4})", s):
        d = date(int(m[3]), MONTHS[m[2]], int(m[1]))
        found.append((m.start(), d, d))
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s):
        d = date(int(m[3]), MONTHS[m[1]], int(m[2]))
        found.append((m.start(), d, d))
    taken = [(p, p + 12) for p, _, _ in found]
    for m in re.finditer(r"\b(" + "|".join(MONTHS) + r")[a-z]*\.?\s+(\d{4})", s):
        if any(a <= m.start() < b for a, b in taken):
            continue
        y, mo = int(m[2]), MONTHS[m[1]]
        found.append((m.start(), date(y, mo, 1), _end_of_month(y, mo)))
    for m in re.finditer(r"\b(19|20)(\d{2})\b", s):
        if any(a <= m.start() < b + 8 for a, b in [(p, p + 20) for p, _, _ in found]):
            continue
        y = int(m[0])
        found.append((m.start(), date(y, 1, 1), date(y, 12, 31)))
    found.sort()
    windows = [(a, min(b, today)) for _, a, b in found]

    if task == "vessels":
        d = windows[-1] if windows else (today - timedelta(days=12), today)
        return None, (d[0].isoformat(), d[1].isoformat())
    if len(windows) >= 2:
        b, a = windows[0], windows[-1]
        return (b[0].isoformat(), b[1].isoformat()), (a[0].isoformat(), a[1].isoformat())
    if len(windows) == 1:
        a0, a1 = windows[0]
        if task == "flood":
            return ((a0 - timedelta(days=40)).isoformat(), (a0 - timedelta(days=1)).isoformat()), \
                   (a0.isoformat(), a1.isoformat())
        # 'since 2020' -> before is that year, after is the last few months
        if re.search(r"\bsince\b", s):
            return (a0.isoformat(), a1.isoformat()), ((today - timedelta(days=90)).isoformat(), today.isoformat())
        return ((a0 - timedelta(days=365)).isoformat(), (a1 - timedelta(days=365)).isoformat()), \
               (a0.isoformat(), a1.isoformat())
    if task == "flood":
        return ((today - timedelta(days=60)).isoformat(), (today - timedelta(days=25)).isoformat()), \
               ((today - timedelta(days=24)).isoformat(), today.isoformat())
    return ((today - timedelta(days=365 + 90)).isoformat(), (today - timedelta(days=365)).isoformat()), \
           ((today - timedelta(days=90)).isoformat(), today.isoformat())


def geocode(text: str):
    r = httpx.get("https://nominatim.openstreetmap.org/search",
                  params={"q": text, "format": "json", "limit": 1, "accept-language": "en"},
                  headers=UA, timeout=20)
    r.raise_for_status()
    js = r.json()
    if not js:
        return None
    h = js[0]
    bb = [float(x) for x in h["boundingbox"]]  # lat_min, lat_max, lon_min, lon_max
    return {"name": h["display_name"], "lat": float(h["lat"]), "lon": float(h["lon"]),
            "bbox": [bb[2], bb[0], bb[3], bb[1]]}


def find_place(q: str):
    m = re.search(r"(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)", q)
    if m:
        lat, lon = float(m[1]), float(m[2])
        return {"name": f"{lat:.4f}, {lon:.4f}", "lat": lat, "lon": lon, "bbox": None}
    # Candidate names: runs of capitalised words, joined across commas ("Bang Ban, Ayutthaya").
    tokens = re.findall(r"[A-Za-zÀ-ÿ'’\-]+|,", q)
    runs, cur = [], []
    for tok in tokens:
        if tok == ",":
            if cur:
                cur.append(",")
            continue
        if tok[0].isupper() and tok.lower() not in STOP and tok.lower() not in MONTHS:
            cur.append(tok)
        else:
            if cur:
                runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    names = [" ".join(run).replace(" ,", ",").strip(", ") for run in runs]
    # The first name is usually the most specific ("Paiporta near Valencia, Spain"), so try it with the
    # later names as context first, then alone.
    cands = [", ".join(names)] if len(names) > 1 else []
    for words in names:
        cands.append(words)
        parts = [p.strip() for p in words.split(",") if p.strip()]
        if len(parts) > 1:
            cands.append(parts[0])
    if not cands:  # fall back to the words after a preposition
        m = re.search(r"\b(?:in|at|off|near|around|of)\s+([a-z][a-z\s\-]+)", q, re.I)
        if m:
            cands.append(m[1].strip())
    cands = list(dict.fromkeys(cands))
    for c in cands[:4]:
        g = geocode(c)
        if g:
            return g
    return None


def aoi_bbox(lat: float, lon: float, task: str, hint=None):
    """A box around the place: about 14 km for floods, 26 km for ships, 3.5 km for construction."""
    side = {"flood": 14000, "vessels": 26000, "construction": 3500}[task]
    if hint and task != "vessels":
        w = (hint[2] - hint[0]) * 111320 * math.cos(math.radians(lat))
        h = (hint[3] - hint[1]) * 111320
        side = float(np.clip(max(w, h), side * 0.5, side * 1.6))
    dlat = side / 2 / 111320
    dlon = side / 2 / (111320 * math.cos(math.radians(lat)))
    return [round(lon - dlon, 5), round(lat - dlat, 5), round(lon + dlon, 5), round(lat + dlat, 5)]


def make_plan(question: str, log: Log) -> Plan:
    task = classify(question)
    log(f"Read the question as a **{ {'flood': 'flood extent', 'vessels': 'vessel count', 'construction': 'built change'}[task] }** question.")
    place = find_place(question)
    if not place:
        raise ValueError("I could not find a place name in the question. Name a town, port or region, "
                         "or give coordinates such as 14.38, 100.47.")
    log(f"Found the place with OpenStreetMap: {place['name']} ({place['lat']:.4f}, {place['lon']:.4f}).")
    before, after = parse_dates(question, task)
    if task == "flood" and after[0] == after[1]:
        # One day rarely has a radar pass; look at the week that starts on it.
        end = min(date.fromisoformat(after[0]) + timedelta(days=6), datetime.now(timezone.utc).date())
        after = (after[0], end.isoformat())
    bbox = aoi_bbox(place["lat"], place["lon"], task, place.get("bbox"))
    res = {"flood": 20.0, "vessels": 10.0, "construction": 10.0}[task]
    return Plan(question, task, place["name"], place["lat"], place["lon"], bbox, before, after, res=res)


# ---------------------------------------------------------------- data access

_tokens: dict[str, tuple[float, str]] = {}


def sign(href: str, collection: str) -> str:
    exp, tok = _tokens.get(collection, (0, ""))
    if time.time() > exp:
        r = httpx.get(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}", timeout=30)
        r.raise_for_status()
        tok = r.json()["token"]
        _tokens[collection] = (time.time() + 1800, tok)
    return f"{href}?{tok}"


def search(collection: str, bbox, dt: tuple[str, str], limit=100, query=None):
    body = {"collections": [collection], "bbox": bbox, "datetime": f"{dt[0]}T00:00:00Z/{dt[1]}T23:59:59Z",
            "limit": limit, "sortby": [{"field": "datetime", "direction": "asc"}]}
    if query:
        body["query"] = query
    r = httpx.post(f"{STAC}/search", json=body, timeout=60)
    r.raise_for_status()
    return r.json()["features"]


@dataclass
class Grid:
    crs: str
    transform: object
    width: int
    height: int
    res: float

    @property
    def pixel_km2(self):
        return self.res * self.res / 1e6


def make_grid(bbox, res) -> Grid:
    lon, lat = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    epsg = (32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1
    tr = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True)
    xs, ys = tr.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = int(math.ceil((x1 - x0) / res)), int(math.ceil((y1 - y0) / res))
    return Grid(f"EPSG:{epsg}", from_origin(x0, y1, res, res), w, h, res)


def grid_lonlat(grid: Grid, rows, cols):
    xs = grid.transform.c + (np.asarray(cols) + 0.5) * grid.res
    ys = grid.transform.f - (np.asarray(rows) + 0.5) * grid.res
    tr = Transformer.from_crs(grid.crs, "EPSG:4326", always_xy=True)
    return tr.transform(xs, ys)


def read(href: str, collection: str, grid: Grid, resampling=Resampling.average) -> np.ndarray:
    with rasterio.open(sign(href, collection)) as src:
        with WarpedVRT(src, crs=grid.crs, transform=grid.transform, width=grid.width, height=grid.height,
                       resampling=resampling, src_nodata=src.nodata if src.nodata is not None else 0,
                       nodata=np.nan, dtype="float32") as vrt:
            return vrt.read(1).astype("float32")


def mosaic(items, asset, collection, grid, resampling=Resampling.average):
    out = None
    for it in items:
        a = read(it["assets"][asset]["href"], collection, grid, resampling)
        out = a if out is None else np.where(np.isnan(out), a, out)
    return out


def group_by_day(items):
    days: dict[str, list] = {}
    for it in items:
        days.setdefault(it["properties"]["datetime"][:10], []).append(it)
    return days


def worldcover(grid: Grid, bbox):
    items = search("esa-worldcover", bbox, ("2021-01-01", "2021-12-31"))
    if not items:
        return None
    return mosaic(items, "map", "esa-worldcover", grid, Resampling.nearest)


# ---------------------------------------------------------------- rendering

def save_png(arr: np.ndarray, name: str) -> str:
    MEDIA.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr).save(MEDIA / name, optimize=True)
    return f"/media/{name}"


def sar_png(vv_lin: np.ndarray, name: str, lo=-22.0, hi=2.0) -> str:
    db = 10 * np.log10(np.clip(vv_lin, 1e-5, None))
    g = np.clip((db - lo) / (hi - lo), 0, 1) ** 0.9
    g = np.where(np.isnan(vv_lin), 0, g)
    return save_png((g * 255).astype("uint8"), name)


def sar_map_png(vv: np.ndarray, grid: Grid, bbox, name: str, zone=None, max_px=1800) -> dict:
    """The radar image on a longitude/latitude grid, so it lines up on a web map. Outside the zone is clear."""
    w, s, e, n = bbox
    aspect = (e - w) * math.cos(math.radians((s + n) / 2)) / (n - s)
    W, H = (max_px, int(max_px / aspect)) if aspect >= 1 else (int(max_px * aspect), max_px)
    src = np.where(zone, vv, np.nan) if zone is not None else vv
    dst = np.full((H, W), np.nan, "float32")
    reproject(src, dst, src_transform=grid.transform, src_crs=grid.crs, src_nodata=np.nan,
              dst_transform=from_bounds(w, s, e, n, W, H), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    db = 10 * np.log10(np.clip(dst, 1e-5, None))
    g = (np.clip((db + 25) / 25, 0, 1) * 255).astype("uint8")
    a = np.where(np.isnan(dst), 0, 235).astype("uint8")
    MEDIA.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.dstack([g, a]), "LA").save(MEDIA / name, optimize=True)
    return {"url": f"/media/{name}", "bounds": [[s, w], [n, e]]}


def rgb_png(r, g, b, name: str, gain=None) -> str:
    stack = np.dstack([r, g, b])
    if gain is None:
        gain = np.nanpercentile(stack, 98) or 1
    img = np.clip(stack / gain, 0, 1) ** (1 / 1.6)
    img = np.nan_to_num(img)
    return save_png((img * 255).astype("uint8"), name)


def overlay_png(masks: list[tuple[np.ndarray, tuple[int, int, int, int]]], name: str) -> str:
    h, w = masks[0][0].shape
    out = np.zeros((h, w, 4), "uint8")
    for m, color in masks:
        out[m] = color
    return save_png(out, name)


# ---------------------------------------------------------------- helpers

def item_ref(it, collection):
    p = it["properties"]
    return {"id": it["id"], "collection": collection, "datetime": p["datetime"],
            "platform": p.get("platform"), "orbit": p.get("sat:orbit_state"),
            "url": f"https://planetarycomputer.microsoft.com/api/stac/v1/collections/{collection}/items/{it['id']}"}


def s2_scale(item):
    base = item["properties"].get("s2:processing_baseline", "00.00")
    offset = 1000.0 if float(base) >= 4.0 else 0.0
    return offset


def s2_read_day(items, grid, bands=("B02", "B03", "B04", "B08", "B11")):
    off = s2_scale(items[0])
    out = {}
    for b in bands:
        a = mosaic(items, b, "sentinel-2-l2a", grid, Resampling.bilinear if b == "B11" else Resampling.average)
        out[b] = np.clip((a - off) / 10000.0, 0, 1.5)
    scl = mosaic(items, "SCL", "sentinel-2-l2a", grid, Resampling.nearest)
    out["SCL"] = scl
    return out


def aoi_cloud(items, grid) -> float | None:
    """Percent of the box under cloud or cloud shadow, from the Sentinel-2 scene classification."""
    try:
        scl = mosaic(items, "SCL", "sentinel-2-l2a", make_grid_coarse(grid), Resampling.nearest)
    except Exception:
        return None
    valid = ~np.isnan(scl) & (scl > 0)
    if valid.sum() == 0:
        return None
    cloud = np.isin(scl, [3, 8, 9, 10]) & valid
    return round(100 * cloud.sum() / valid.sum(), 1)


def make_grid_coarse(grid: Grid, res=100.0) -> Grid:
    f = res / grid.res
    return Grid(grid.crs, from_origin(grid.transform.c, grid.transform.f, res, res),
                max(1, int(grid.width / f)), max(1, int(grid.height / f)), res)


def optical_check(plan: Plan, grid: Grid, log: Log, window):
    """Look at the colour images for a date window and report the cloud over the box."""
    items = search("sentinel-2-l2a", plan.bbox, window)
    days = group_by_day(items)
    res = []
    for d, its in days.items():
        res.append((d, aoi_cloud(its, grid), its))
    return res


def area_str(km2: float) -> str:
    return f"{km2:.1f} km²" if km2 < 10 else f"{km2:.0f} km²"


def quote(plan: Plan, task: str) -> dict:
    w = (plan.bbox[2] - plan.bbox[0]) * 111.32 * math.cos(math.radians(plan.lat))
    h = (plan.bbox[3] - plan.bbox[1]) * 111.32
    km2 = w * h
    scenes = max(1, math.ceil(km2 / SKYFI["sar_scene_km2"]))
    opt_km2 = max(km2, SKYFI["optical_min_km2"])
    rec = "optical" if task == "construction" else "sar"
    return {
        "area_km2": round(km2, 1),
        "recommended": rec,
        "options": [
            {"kind": "sar", "label": "SAR archive, 1 m spotlight", "unit": f"$450 per 25 km² scene × {scenes}",
             "price": SKYFI["sar_archive_scene"] * scenes,
             "why": "Sees through cloud and at night. Use when an archive pass already exists."},
            {"kind": "sar", "label": "SAR new tasking, 1 m spotlight", "unit": f"$675 per 25 km² scene × {scenes}",
             "price": SKYFI["sar_tasking_scene"] * scenes,
             "why": "A new radar pass on the date you pick, through cloud."},
            {"kind": "optical", "label": "Optical archive, 50 cm", "unit": f"$8 per km² × {opt_km2:.1f} km²",
             "price": round(SKYFI["optical_50cm_km2"] * opt_km2, 2),
             "why": "Colour image that shows single buildings and cars, if the sky was clear."},
            {"kind": "optical", "label": "Optical archive, 30 cm", "unit": f"$22.50 per km² × {opt_km2:.1f} km²",
             "price": round(SKYFI["optical_30cm_km2"] * opt_km2, 2),
             "why": "The sharpest colour image. Small boats, vehicles and roof detail."},
        ],
        "source": "SkyFi list prices (skyfi.com/pricing and FAQ). Optical minimum order 5 km².",
        "note": "This is a quote only. No order is placed.",
    }


# ---------------------------------------------------------------- flood

def water_mask(vv_lin):
    db = 10 * np.log10(np.clip(vv_lin, 1e-5, None))
    db = ndimage.median_filter(np.nan_to_num(db, nan=0.0), size=3)
    m = (db < -17.0) & ~np.isnan(vv_lin)
    m = ndimage.binary_opening(m, iterations=1)
    return m


def run_flood(plan: Plan, log: Log, job: str) -> dict:
    grid = make_grid(plan.bbox, plan.res)
    log("Checked the colour (Sentinel-2) images first, because colour is easier to read.")
    opt = optical_check(plan, grid, log, (plan.before[0], plan.timeline_until or plan.after[1]))
    clouds = [c for _, c, _ in opt if c is not None]
    after_clouds = [c for d, c, _ in opt if c is not None and plan.after[0] <= d <= plan.after[1]]
    if after_clouds:
        log(f"Found {len(after_clouds)} colour images after the start date. Cloud over the box: "
            f"{min(after_clouds):.0f}–{max(after_clouds):.0f}%.")
    why = (f"the colour images are at least {min(after_clouds):.0f}% cloud" if after_clouds and min(after_clouds) > 30
           else "it sees through cloud on every pass")
    log(f"Chose **radar (Sentinel-1)**, because {why}. Calm water is dark to radar.", "decision")

    items = search("sentinel-1-rtc", plan.bbox, (plan.before[0], plan.timeline_until or plan.after[1]))
    if not items:
        raise ValueError("No Sentinel-1 radar images cover this place in these dates.")
    # Compare each pass with the first pre-event pass from the same orbit, so the radar looks at the
    # ground from the same side in both images.
    def day(i):
        return i["properties"]["datetime"][:10]

    def key(i):
        return (i["properties"]["sat:orbit_state"], i["properties"].get("sat:relative_orbit"))

    # A pass over a big box can be split into several frames: join them into one image per pass.
    frames: dict = {}
    for i in items:
        frames.setdefault((day(i), key(i)), []).append(i)
    items = [dict(v[0], _frames=v) for v in frames.values()]

    base: dict = {}
    for i in items:
        if day(i) < plan.after[0] and key(i) not in base:
            base[key(i)] = i
    if not base:
        raise ValueError("No radar image before the event to compare with.")
    after_items = [i for i in items if plan.after[0] <= day(i) <= plan.after[1] and key(i) in base]
    if not after_items:
        raise ValueError("No radar image in the 'after' dates yet.")
    a_item = after_items[-1]
    b_item = base[key(a_item)]
    log(f"Found {len(items)} radar passes. Before: {day(b_item)}. After: {day(a_item)} "
        f"({a_item['properties']['sat:orbit_state']} pass, same orbit).")

    log("Read the VV radar band on a 20 m grid.")
    base_water: dict = {}

    def vv(it):
        return mosaic(it["_frames"], "vv", "sentinel-1-rtc", grid)

    def baseline(k):
        if k not in base_water:
            vb = vv(base[k])
            base_water[k] = (vb, water_mask(vb))
        return base_water[k]

    def new_water(it):
        vb, wb_ = baseline(key(it))
        v = vv(it)
        wa_ = water_mask(v)
        nw = ndimage.binary_opening(wa_ & ~wb_, iterations=1)
        lab, n = ndimage.label(nw)
        sizes = ndimage.sum(nw, lab, range(1, n + 1))
        return v, wa_, np.isin(lab, np.nonzero(sizes >= 5)[0] + 1)

    vv_a, wa, new = new_water(a_item)
    vv_b, wb = base_water[key(a_item)]
    new_km2 = float(new.sum() * grid.pixel_km2)
    perm_km2 = float((wa & wb).sum() * grid.pixel_km2)
    log(f"Marked radar-dark pixels (VV below −17 dB) as open water. New water: {area_str(new_km2)}.")

    wc = worldcover(grid, plan.bbox)
    crop_txt, crop_pct = "", None
    later_sources: list = []
    if wc is not None:
        crop = wc == 40
        crop_km2 = float(crop.sum() * grid.pixel_km2)
        if crop_km2 > 1:
            crop_pct = 100 * float((new & crop).sum()) / float(crop.sum())
            crop_txt = f"{crop_pct:.0f}% of the cropland in the box"
            log(f"Overlaid ESA WorldCover cropland ({area_str(crop_km2)}): {crop_pct:.0f}% of it is newly under water.")
        built_km2 = float((wc == 50).sum() * grid.pixel_km2)
    else:
        built_km2 = 0

    timeline = []
    later = [i for i in items if day(i) > max(day(b) for b in base.values()) and key(i) in base]
    later = later[-(6 if plan.timeline_until else 4):]
    if a_item not in later:
        later = [a_item] + later[1:]
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(baseline, {key(i) for i in later}))
        areas = list(ex.map(lambda it: new_km2 if it is a_item else float(new_water(it)[2].sum() * grid.pixel_km2),
                            later))
    for it, km2 in zip(later, areas):
        timeline.append({"date": day(it), "km2": round(km2, 1), "orbit": it["properties"]["sat:orbit_state"]})
        if it is not a_item:
            later_sources.append(item_ref(it, "sentinel-1-rtc"))
    if len(timeline) > 1:
        log("Measured the later radar passes too: " +
            ", ".join(f"{t['date']}: {area_str(t['km2'])}" for t in timeline) + ".")

    before_png = sar_png(vv_b, f"{job}_before.png")
    after_png = sar_png(vv_a, f"{job}_after.png")
    ov = overlay_png([(wb & wa, (60, 110, 200, 110)), (new, (0, 160, 255, 200))], f"{job}_overlay.png")
    log("Drew the new water in blue over the radar images.")

    latest = timeline[-1] if timeline else {"date": a_item["properties"]["datetime"][:10], "km2": new_km2}
    headline = f"{area_str(new_km2)} of new water"
    answer = (f"On {a_item['properties']['datetime'][:10]} radar shows **{area_str(new_km2)} of new open water** in the box "
              f"around {plan.place.split(',')[0]}, compared with {b_item['properties']['datetime'][:10]}"
              + (f". That is **{crop_txt}**." if crop_txt else ".")
              + (f" By {latest['date']} it grew to **{area_str(latest['km2'])}**." if latest["km2"] > new_km2 + 0.5 else "")
              + f" Water that was there before (rivers, ponds): {area_str(perm_km2)}.")
    a_day = date.fromisoformat(day(a_item))
    near = [(abs((date.fromisoformat(d) - a_day).days), d, c) for d, c, _ in opt if c is not None]
    near = min(near) if near else None
    if near:
        log(f"The colour image closest to the radar pass ({near[1]}) is {near[2]:.0f}% cloud over the box.")
        answer += (f" The colour image closest to the radar pass ({near[1]}) is **{near[2]:.0f}% cloud** over the box"
                   + (f"; colour images in the after dates range from {min(after_clouds):.0f}% to "
                      f"{max(after_clouds):.0f}% cloud." if after_clouds else "."))
    stats = [{"label": "New water", "value": area_str(new_km2)},
             {"label": "Cropland flooded", "value": f"{crop_pct:.0f}%" if crop_pct is not None else "n/a"},
             {"label": f"By {latest['date']}", "value": area_str(latest["km2"])},
             {"label": f"Cloud on {near[1]}" if near else "Colour image cloud",
              "value": f"{near[2]:.0f}%" if near else "n/a"}]
    limits = [
        "Radar sees open, calm water. It can miss water under trees, in narrow streets, and shallow water "
        "with rice plants standing out of it, so the real area can be larger.",
        "Wind on the water and wet bare soil can look like water for one pass.",
        f"Pixels are 20 m, so it cannot see single flooded houses or roads. Built-up land in the box: {area_str(built_km2)}.",
        "Each number is one moment: the time of the satellite pass, not the flood peak.",
    ]
    sources = [item_ref(b_item, "sentinel-1-rtc"), item_ref(a_item, "sentinel-1-rtc")] + later_sources
    sources += [item_ref(its[0], "sentinel-2-l2a") | {"cloud_pct": c} for d, c, its in opt if d >= plan.after[0]][:4]
    sources.append({"id": "ESA WorldCover 2021 v200", "collection": "esa-worldcover",
                    "url": "https://planetarycomputer.microsoft.com/dataset/esa-worldcover"})
    return {"sensor": "sar", "headline": headline, "answer": answer, "stats": stats, "timeline": timeline,
            "images": {"before": before_png, "after": after_png, "overlay": ov,
                       "before_label": f"Radar · {b_item['properties']['datetime'][:10]}",
                       "after_label": f"Radar · {a_item['properties']['datetime'][:10]}"},
            "legend": [{"color": "#00a0ff", "label": "New water"}, {"color": "#3c6ec8", "label": "Water before"}],
            "limits": limits, "sources": sources, "markers": []}


# ---------------------------------------------------------------- vessels

def load_ais():
    if not AIS_FILE.exists():
        return None
    rows = []
    with gzip.open(AIS_FILE, "rt", newline="") as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            try:
                ts = datetime.strptime(r[0], "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
                rows.append((ts.timestamp(), r[2], float(r[3]), float(r[4]), r[7], r[8], r[12], r[13], r[16], r[1]))
            except (ValueError, IndexError):
                continue
    return rows


_ais_cache = None


def ais_at(t: float, bbox, window=1500):
    """One position per ship near time t, moved to t along its course and speed."""
    global _ais_cache
    if _ais_cache is None:
        _ais_cache = load_ais() or []
    best: dict[str, tuple] = {}
    for row in _ais_cache:
        ts, mmsi, lat, lon = row[0], row[1], row[2], row[3]
        if abs(ts - t) > window or not (-90 <= lat <= 90):
            continue
        if mmsi not in best or abs(ts - t) < abs(best[mmsi][0] - t):
            best[mmsi] = row
    out = []
    for mmsi, (ts, _, lat, lon, sog, cog, name, stype, length, mob) in best.items():
        if mob not in ("Class A", "Class B"):
            continue  # aids to navigation, base stations, SAR aircraft
        try:
            v = float(sog) * 0.514444
            c = math.radians(float(cog))
            dt = t - ts
            if v > 0.3 and float(sog) < 60:
                lat += v * dt * math.cos(c) / 111320
                lon += v * dt * math.sin(c) / (111320 * math.cos(math.radians(lat)))
        except ValueError:
            v = 0
        if bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]:
            out.append({"mmsi": mmsi, "lat": lat, "lon": lon, "sog": sog, "name": name.strip(),
                        "type": stype, "length": length, "class": mob})
    return out


def detect_ships(vv: np.ndarray, sea: np.ndarray, res: float):
    """Bright points on dark sea: pixel brighter than 6× its sea background (about +8 dB)."""
    v = np.where(sea & ~np.isnan(vv), vv, np.nan)
    filled = np.where(np.isnan(v), 0, v)
    w = np.where(np.isnan(v), 0, 1).astype("float32")
    k = 31
    bg = ndimage.uniform_filter(filled, k) / np.maximum(ndimage.uniform_filter(w, k), 1e-3)
    db = 10 * np.log10(np.clip(filled, 1e-6, None))
    hit = sea & (filled > 6 * np.maximum(bg, 1e-4)) & (db > -10)
    hit = ndimage.binary_dilation(hit, iterations=1) & (filled > 3 * np.maximum(bg, 1e-4))
    # One ship often breaks into several bright blobs; join blobs closer than about 100 m.
    joined = ndimage.binary_closing(hit, structure=np.ones((3, 3)), iterations=int(50 / res) or 1)
    lab, n = ndimage.label(joined)
    lab = lab * hit
    objs = ndimage.find_objects(lab)
    out = []
    for i, sl in enumerate(objs, start=1):
        m = lab[sl] == i
        npx = int(m.sum())
        if npx < 2:
            continue
        peak = float(np.nanmax(db[sl][m]))
        # Total return of the blob above the sea background, in dB m²: a size measure that smearing keeps.
        rcs = float(np.sum(np.clip(filled[sl][m] - bg[sl][m], 0, None)) * res * res)
        rcs_db = 10 * math.log10(max(rcs, 1e-3))
        # Length: longest side of the bright core (within 6 dB of the peak), so sidelobes do not count.
        core = m & (db[sl] > peak - 6)
        rr, cc = np.nonzero(core)
        npx = int(core.sum())
        if npx > 2:
            cov = np.cov(np.vstack([rr, cc]))
            ev = np.linalg.eigvalsh(cov)
            length = float(4 * math.sqrt(max(ev[-1], 0.25)) * res)
        else:
            length = 2 * res
        r0 = sl[0].start + float(rr.mean())
        c0 = sl[1].start + float(cc.mean())
        out.append({"row": r0, "col": c0, "pixels": npx, "length_m": round(length), "peak_db": round(peak, 1),
                    "rcs_db": round(rcs_db, 1)})
    return out


LARGE_RCS_DB = 33.0


def run_vessels(plan: Plan, log: Log, job: str) -> dict:
    grid = make_grid(plan.bbox, plan.res)
    log("Chose **radar (Sentinel-1)**. Steel ships are bright points on dark radar sea, day or night, "
        "through cloud.", "decision")
    items = search("sentinel-1-rtc", plan.bbox, plan.after)
    if not items:
        d0, d1 = date.fromisoformat(plan.after[0]), date.fromisoformat(plan.after[1])
        wide = ((d0 - timedelta(days=3)).isoformat(), (d1 + timedelta(days=3)).isoformat())
        items = search("sentinel-1-rtc", plan.bbox, wide)
        if items:
            log("No radar pass on those dates, so I widened the search by 3 days each side.", "warn")
    if not items:
        raise ValueError("No Sentinel-1 radar image of this sea in these dates.")
    it = items[-1]
    if plan.notes and plan.notes[0].startswith("pick:"):
        want = plan.notes[0][5:]
        picked = [i for i in items if want in i["id"]]
        it = picked[0] if picked else it
        if len(picked) > 1:  # one pass split into frames: read them as one image
            it = dict(it, _frames=picked)
    t = datetime.fromisoformat(it["properties"]["datetime"].replace("Z", "+00:00"))
    log(f"Found {len(items)} radar passes. Used {it['properties']['datetime'][:16].replace('T', ' ')} UTC "
        f"({it['properties']['sat:orbit_state']}).")
    log("Read the VV band on a 10 m grid.")
    vv = mosaic(it.get("_frames", [it]), "vv", "sentinel-1-rtc", grid)
    wc = worldcover(grid, plan.bbox)
    land = (wc > 0) & (wc != 80) if wc is not None else np.zeros_like(vv, bool)
    land = np.nan_to_num(land).astype(bool)
    near_land = ndimage.binary_dilation(land, iterations=25)  # 250 m from the coast
    sea = ~near_land & ~np.isnan(vv)
    zone = None
    if plan.polygon:
        ring = [list(p) for p in plan.polygon] + [list(plan.polygon[0])]
        geom = transform_geom("EPSG:4326", grid.crs, {"type": "Polygon", "coordinates": [ring]})
        zone = rasterize([(geom, 1)], out_shape=vv.shape, transform=grid.transform, fill=0).astype(bool)
        sea &= zone
    sea_km2 = float(sea.sum() * grid.pixel_km2)
    log(f"Masked land and a 250 m coastal strip with ESA WorldCover{' and kept only your zone' if zone is not None else ''}. "
        f"Open sea left: {area_str(sea_km2)}.")
    if sea_km2 < 0.05 * grid.width * grid.height * grid.pixel_km2:
        log("The box is mostly land. For ships, name a coast, strait or sea area.", "warn")
    dets = detect_ships(vv, sea, grid.res)
    lons, lats = grid_lonlat(grid, [d["row"] for d in dets], [d["col"] for d in dets])
    for d, lo, la in zip(dets, np.atleast_1d(lons), np.atleast_1d(lats)):
        d["lon"], d["lat"] = float(lo), float(la)
    log(f"Found **{len(dets)} bright ship-like targets** (more than 8 dB above the sea around them).")

    ais = ais_at(t.timestamp(), plan.bbox)
    # The AIS extract covers 10.2–11.2 E, 57.45–57.95 N, 30 minutes around each 6 Nov 2026 pass.
    have_ais = AIS_FILE.exists() and t.date().isoformat() == "2026-11-06" and \
        plan.bbox[0] >= 10.2 and plan.bbox[2] <= 11.2 and plan.bbox[1] >= 57.45 and plan.bbox[3] <= 57.95
    matched = 0
    if have_ais:
        log(f"Loaded Danish Maritime Authority AIS: {len(ais)} ships reported within 25 minutes of the pass.")
        # AIS positions inside the open-sea mask only.
        tr = Transformer.from_crs("EPSG:4326", grid.crs, always_xy=True)
        ais_sea = []
        for a in ais:
            x, y = tr.transform(a["lon"], a["lat"])
            c = int((x - grid.transform.c) / grid.res)
            r = int((grid.transform.f - y) / grid.res)
            if 0 <= r < grid.height and 0 <= c < grid.width:
                a["x"], a["y"], a["at_sea"] = x, y, bool(sea[r, c])
                ais_sea.append(a)
        pairs = []
        for i, d in enumerate(dets):
            x, y = tr.transform(d["lon"], d["lat"])
            d["x"], d["y"] = x, y
            for j, a in enumerate(ais_sea):
                dist = math.hypot(a["x"] - x, a["y"] - y)
                try:
                    knots = float(a["sog"])
                except ValueError:
                    knots = 0.0
                # A moving ship shows up shifted along the satellite track, about 80 m per knot.
                if dist < 500 + 80 * min(knots, 15):
                    pairs.append((dist, i, j))
        pairs.sort()
        used_d, used_a = set(), set()
        for dist, i, j in pairs:
            if i in used_d or j in used_a:
                continue
            used_d.add(i), used_a.add(j)
            dets[i]["ais"] = {k: ais_sea[j][k] for k in ("mmsi", "name", "type", "length", "sog")}
            dets[i]["match_m"] = round(dist)
        # Bright ships throw sidelobe crosses and wake blobs; fold leftovers near a matched ship into it.
        for i, d in enumerate(dets):
            if i in used_d:
                continue
            for k in used_d:
                a = dets[k]
                try:
                    reach = max(500.0, float(a["ais"]["length"] or 0) + 300)
                except ValueError:
                    reach = 500.0
                if math.hypot(a["x"] - d["x"], a["y"] - d["y"]) < reach:
                    d["part_of"] = k
                    break
        folded = sum("part_of" in d for d in dets)
        dets = [d for d in dets if "part_of" not in d]
        if folded:
            log(f"Joined {folded} extra blobs (sidelobes and wakes) to the matched ship next to them.")
        # Azimuth ghosts: a very bright ship repeats as a faint copy 1–8 km away along the satellite track.
        lat = math.radians((plan.bbox[1] + plan.bbox[3]) / 2)
        heading = math.asin(max(-1.0, min(1.0, math.cos(math.radians(98.18)) / math.cos(lat))))
        ux, uy = math.sin(heading), math.cos(heading)
        ghosts = 0
        for d in dets:
            if "ais" in d:
                continue
            for b in dets:
                if b["peak_db"] < d["peak_db"] + 12:
                    continue
                dx, dy = d["x"] - b["x"], d["y"] - b["y"]
                along, across = dx * ux + dy * uy, -dx * uy + dy * ux
                if 1000 < abs(along) < 8000 and abs(across) < 400:
                    d["ghost"] = True
                    ghosts += 1
                    break
        dets = [d for d in dets if not d.get("ghost")]
        if ghosts:
            log(f"Removed {ghosts} faint ghosts: copies of a bright ship 1–8 km along the satellite track.")
        log(f"Ships left: {len(dets)}.")
        matched = len(used_d)
        unseen = [a for j, a in enumerate(ais_sea) if j not in used_a and a["at_sea"]]
        log(f"Matched {matched} radar targets to AIS positions (within 500 m plus 80 m per knot, after "
            f"moving each ship along its course to the pass time).")
    else:
        unseen = []
        log("No AIS data for this place and time. This demo has AIS only for Skagen on 6 Nov 2026, "
            "so every target is listed without a match.", "warn")

    # Size from total return: in this Skagen pass, AIS ships under 30 m give 20–30 dB m² and anchored
    # ships over 80 m give 35–61 dB m². A smeared length is not used, because motion stretches it.
    for d in dets:
        d["size"] = "large" if d["rcs_db"] >= LARGE_RCS_DB else "small"
    dark = [d for d in dets if "ais" not in d]
    big = [d for d in dark if d["size"] == "large"]
    small = [d for d in dark if d["size"] == "small"]
    for d in dets:
        d["status"] = "ais" if "ais" in d else ("dark" if have_ais else "unknown")
        d.pop("x", None), d.pop("y", None)

    png = sar_png(vv, f"{job}_after.png", lo=-25, hi=0)
    # 'Before' panel: the same radar image with the land mask, to show what the agent looked at.
    sea_view = np.where(sea, vv, np.nan)
    png_b = sar_png(sea_view, f"{job}_before.png", lo=-25, hi=0)
    ov = overlay_png([(near_land, (250, 245, 235, 70))], f"{job}_overlay.png")
    map_overlay = sar_map_png(vv, grid, plan.bbox, f"{job}_map.png", zone)
    log("Circled every target: green has AIS, red has no AIS.")

    when = t.strftime("%-d %b %Y %H:%M UTC")
    if have_ais:
        headline = f"{len(dets)} ships, {len(dark)} without AIS"
        def n(k, one, many):
            return f"{k} {one if k == 1 else many}"
        where = "in your zone" if plan.polygon else f"in the box off {plan.place.split(',')[0]}"
        answer = (f"At {when} radar shows **{n(len(dets), 'ship', 'ships')}** on the open sea {where}. "
                  f"**{matched} match** a Danish Maritime Authority AIS position. ")
        if not dark:
            answer += "**Every radar target has AIS.**"
        else:
            answer += (f"**{len(dark)} {'has' if len(dark) == 1 else 'have'} no AIS** and "
                       f"{'is' if len(dark) == 1 else 'are'} flagged")
            if not big:
                answer += (": " + ("it is a small, weak target" if len(small) == 1 else "both are small, weak targets" if len(small) == 2
                                  else f"all {len(small)} are small, weak targets")
                           + " (the return of a boat under about 30 m). There are **no large dark ships**.")
            else:
                answer += f": {len(big)} give a large-ship return and {len(small)} {'is' if len(small) == 1 else 'are'} small."
        if unseen:
            short = sum(1 for a in unseen if not a["length"].isdigit() or int(a["length"]) < 30)
            answer += (f" {n(len(unseen), 'AIS ship', 'AIS ships')} on the open sea "
                       f"{'has' if len(unseen) == 1 else 'have'} no radar target"
                       + (f"; {short} of them {'is' if short == 1 else 'are'} under 30 m or send no length." if short else "."))
    else:
        headline = f"{len(dets)} ship-like targets"
        where = "in your zone" if plan.polygon else f"near {plan.place.split(',')[0]}"
        answer = (f"At {when} radar shows **{len(dets)} ship-like targets** on the open sea {where}. "
                  "There is no AIS data here, so I cannot say which ones are dark.")
    stats = [{"label": "Radar targets", "value": str(len(dets))},
             {"label": "Match AIS", "value": str(matched) if have_ais else "n/a"},
             {"label": "No AIS", "value": str(len(dark)) if have_ais else "n/a"},
             {"label": "Large dark ships", "value": str(len(big)) if have_ais else "n/a"}]
    limits = [
        "Boats under about 15 m and wooden or fibreglass boats are often too faint for 10 m radar.",
        "Size comes from the total radar return. A fast large ship smears and can look small "
        "(2 of the 30 AIS ships here did), and ships side by side can merge.",
        "A moving ship can appear a few hundred metres from its true place along the satellite track.",
        "Waves, wind farms, buoys and platforms can look like ships. Land and 250 m of coast are masked.",
        "AIS data in this demo covers only the Skagen extract for 6 Nov 2026.",
    ]
    sources = [item_ref(it, "sentinel-1-rtc"),
               {"id": "ESA WorldCover 2021 v200 (land mask)", "collection": "esa-worldcover",
                "url": "https://planetarycomputer.microsoft.com/dataset/esa-worldcover"}]
    if have_ais:
        sources.append({"id": "Danish Maritime Authority AIS, aisdk-2026-11-06", "collection": "ais",
                        "url": "http://aisdata.ais.dk/"})
    markers = [{"lat": round(d["lat"], 5), "lon": round(d["lon"], 5), "status": d["status"], "peak_db": d["peak_db"], "rcs_db": d["rcs_db"],
                "length_m": d["length_m"], "size": d["size"], "ais": d.get("ais"),
                "fx": round(d["col"] / grid.width, 5), "fy": round(d["row"] / grid.height, 5)} for d in dets]
    return {"sensor": "sar", "headline": headline, "answer": answer, "stats": stats, "timeline": [],
            "images": {"before": png_b, "after": png, "overlay": ov,
                       "before_label": "Open sea the agent searched", "after_label": f"Radar · {when}"},
            "legend": [{"color": "#28c878", "label": "Has AIS"}, {"color": "#ff4632", "label": "No AIS"}],
            "limits": limits, "sources": sources, "markers": markers,
            "pass_time": it["properties"]["datetime"], "scene": it["id"], "map_overlay": map_overlay}


# ---------------------------------------------------------------- construction

def pick_clear(plan: Plan, grid: Grid, window, log: Log, prefer_last: bool):
    items = search("sentinel-2-l2a", plan.bbox, window, query={"eo:cloud_cover": {"lt": 40}})
    days = sorted(group_by_day(items).items(), reverse=prefer_last)
    clear = []
    for d, its in days[:10]:
        c = aoi_cloud(its, grid)
        if c is not None and c < 3:
            # Haze is not always flagged as cloud; it raises blue reflectance, so keep the darkest blue.
            blue = mosaic(its, "B02", "sentinel-2-l2a", make_grid_coarse(grid, 60.0))
            clear.append((float(np.nanmean(blue)) - s2_scale(its[0]), d, its, c))
            if len(clear) >= 4:
                break
    if not clear:
        return None
    _, d, its, c = min(clear, key=lambda x: x[0])
    return d, its, c


def run_construction(plan: Plan, log: Log, job: str) -> dict:
    grid = make_grid(plan.bbox, plan.res)
    log("Chose **colour (Sentinel-2)**. Fresh concrete, fill and bare ground differ in colour, and on a "
        "clear day colour shows them better than radar.", "decision")
    b = pick_clear(plan, grid, plan.before, log, prefer_last=False)
    a = pick_clear(plan, grid, plan.after, log, prefer_last=True)
    if not b or not a:
        raise ValueError("No cloud-free colour image in one of the date ranges. Try a longer range.")
    log(f"Picked the clearest images: before {b[0]} ({b[2]:.0f}% cloud), after {a[0]} ({a[2]:.0f}% cloud).")
    log("Read blue, green, red, near-infrared and short-wave infrared at 10 m.")
    B, A = s2_read_day(b[1], grid), s2_read_day(a[1], grid)

    def idx(D):
        ndwi = (D["B03"] - D["B08"]) / (D["B03"] + D["B08"] + 1e-6)
        ndvi = (D["B08"] - D["B04"]) / (D["B08"] + D["B04"] + 1e-6)
        mndwi = (D["B03"] - D["B11"]) / (D["B03"] + D["B11"] + 1e-6)
        return ndwi, ndvi, mndwi
    wb_, nvb, mb = idx(B)
    wa_, nva, ma = idx(A)
    water_b = ndimage.binary_opening((wb_ > 0.05) & (mb > 0.1), iterations=1)
    water_a = ndimage.binary_opening((wa_ > 0.05) & (ma > 0.1), iterations=1)
    # New land must be clearly dry now: mixed shore pixels and white surf stay out.
    # A thin breakwater mixes rock and sea in one pixel: still bright in near-infrared and in short-wave
    # infrared, which white surf is not.
    dry_a = ((wa_ < 0) & (ma < -0.05)) | ((A["B08"] > 0.08) & (ma < 0.15))
    new_land = water_b & dry_a  # no opening: it would erase a breakwater one or two pixels wide
    lab, n = ndimage.label(new_land)
    s = ndimage.sum(new_land, lab, range(1, n + 1))
    new_land = np.isin(lab, np.nonzero(s >= 20)[0] + 1)

    # Hard surface: grey and bright now (flat spectrum, no plants), and it was not grey before.
    def grey(D):
        vis = (D["B02"] + D["B03"] + D["B04"]) / 3
        slope = (D["B04"] - D["B02"]) / (D["B04"] + D["B02"] + 1e-6)
        swir = (D["B11"] - D["B08"]) / (D["B11"] + D["B08"] + 1e-6)
        return (slope < 0.10) & (vis > 0.09) & (swir < 0.20)
    land_before = ~water_b
    change = np.sqrt(sum((A[k] - B[k]) ** 2 for k in ("B02", "B03", "B04", "B08", "B11")))
    hard = grey(A) & ~grey(B) & land_before & ~water_a & (nva < 0.25) & (change > 0.05)
    hard = ndimage.binary_opening(hard, iterations=1)
    lab, n = ndimage.label(hard)
    s = ndimage.sum(hard, lab, range(1, n + 1))
    hard = np.isin(lab, np.nonzero(s >= 15)[0] + 1)
    px_ha = grid.res * grid.res / 1e4
    new_land_ha = float(new_land.sum() * px_ha)
    hard_ha = float(hard.sum() * px_ha)
    port = bool(re.search(r"\b(port|harbou?r|terminal|pier|breakwater|reclaim)", plan.question, re.I))
    what = "the terminal platform and breakwater" if port else "fill, piers or embankments"
    log(f"Water before and land after: **{new_land_ha:.0f} ha of new land** ({what}).")
    log(f"Grey, bright and plant-free now but not before: **{hard_ha:.0f} ha of new hard surface**.")

    gain = float(np.nanpercentile(np.dstack([A["B04"], A["B03"], A["B02"]]), 98))
    before_png = rgb_png(B["B04"], B["B03"], B["B02"], f"{job}_before.png", gain)
    after_png = rgb_png(A["B04"], A["B03"], A["B02"], f"{job}_after.png", gain)
    ov = overlay_png([(new_land, (255, 140, 0, 190)), (hard, (230, 30, 120, 190))], f"{job}_overlay.png")
    log("Drew new land in orange and new hard surface in pink.")

    headline = (f"{new_land_ha:.0f} ha of new land, {hard_ha:.0f} ha of new hard surface" if new_land_ha >= 2
                else f"{hard_ha:.0f} ha of new hard surface")
    answer = f"Between {b[0]} and {a[0]}, "
    if new_land_ha >= 2:
        answer += (f"**{new_land_ha:.0f} ha** that was water is now land: {what}. On land, **{hard_ha:.0f} ha** "
                   "turned into new hard surface (paving, yards, roofs, roads).")
    else:
        answer += (f"**{hard_ha:.0f} ha** of ground turned into new hard surface (paving, yards, roofs, roads). "
                   "No water was filled in.")
    stats = [{"label": "New land from water", "value": f"{new_land_ha:.0f} ha"},
             {"label": "New hard surface", "value": f"{hard_ha:.0f} ha"},
             {"label": "Before", "value": b[0]}, {"label": "After", "value": a[0]}]
    limits = [
        "10 m pixels show yards and platforms, not single buildings, cranes or containers.",
        "Grey bare ground, sand and wet soil can look like concrete, and dark asphalt can be missed.",
        "Tide and waves move the waterline by a pixel or two, so small edges are not counted.",
        "It measures the surface, not what the place is used for.",
    ]
    sources = [item_ref(b[1][0], "sentinel-2-l2a") | {"cloud_pct": b[2]},
               item_ref(a[1][0], "sentinel-2-l2a") | {"cloud_pct": a[2]}]
    return {"sensor": "optical", "headline": headline, "answer": answer, "stats": stats, "timeline": [],
            "images": {"before": before_png, "after": after_png, "overlay": ov,
                       "before_label": f"Colour · {b[0]}", "after_label": f"Colour · {a[0]}"},
            "legend": [{"color": "#ff8c00", "label": "New land"}, {"color": "#e61e78", "label": "New hard surface"}],
            "limits": limits, "sources": sources, "markers": []}


# ---------------------------------------------------------------- entry

def run(plan: Plan, log: Log, job: str) -> dict:
    t0 = time.time()
    if plan.task == "flood":
        out = run_flood(plan, log, job)
    elif plan.task == "vessels":
        out = run_vessels(plan, log, job)
    else:
        out = run_construction(plan, log, job)
    out.update({
        "question": plan.question, "task": plan.task, "place": plan.place, "lat": plan.lat, "lon": plan.lon,
        "bbox": plan.bbox, "before": plan.before, "after": plan.after,
        "quote": quote(plan, plan.task), "seconds": round(time.time() - t0, 1),
    })
    out["sources"].append({"id": "OpenStreetMap Nominatim (place search)", "collection": "osm",
                           "url": "https://nominatim.openstreetmap.org/"})
    log(f"Done in {out['seconds']:.0f} s.", "done")
    return out
