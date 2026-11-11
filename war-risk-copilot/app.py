"""War-risk pricing copilot: live risk score, price band and evidence for one ship on one voyage."""

import asyncio
import csv
import gzip
import io
import json
import math
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import h3
import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import evidence as ev

HERE = Path(__file__).parent
CACHE = HERE / "cache"
CACHE.mkdir(exist_ok=True)
TTL = 6 * 3600
UA = {"User-Agent": "Mozilla/5.0 war-risk-copilot (hackathon demo)"}

PORTWATCH = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/FeatureServer/0/query"
CHOKEPOINTS = {"Strait of Hormuz": "chokepoint6", "Bab el-Mandeb": "chokepoint4"}
CRISIS_START = date(2026, 2, 28)
REGION = (-8.0, 32.5, 30.0, 70.0)  # lat0, lat1, lon0, lon1 for jamming hexes

app = FastAPI()
STATE: dict = {}


# ---------- live feeds ----------

def _maybe_gunzip(b: bytes) -> bytes:
    return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b


async def _cached(name: str, fetch):
    p = CACHE / f"{name}.json"
    if p.exists() and time.time() - p.stat().st_mtime < TTL:
        return json.loads(p.read_text())
    try:
        data = await fetch()
        data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
        p.write_text(json.dumps(data))
        return data
    except Exception as e:  # keep serving the last good copy
        if p.exists():
            d = json.loads(p.read_text())
            d["stale"] = str(e)
            return d
        raise


async def fetch_portwatch():
    out = {}
    async with httpx.AsyncClient(timeout=40, headers=UA) as c:
        for name, pid in CHOKEPOINTS.items():
            r = await c.get(PORTWATCH, params={
                "where": f"portid='{pid}' AND date >= DATE '2025-02-28'",
                "outFields": "date,n_total,n_tanker,n_container", "orderByFields": "date ASC",
                "resultRecordCount": 1000, "f": "json"})
            rows = [f["attributes"] for f in r.json()["features"]]
            base = [x["n_total"] for x in rows if x["date"] < CRISIS_START.isoformat()]
            out[name] = {"series": [[x["date"], x["n_total"], x["n_tanker"]] for x in rows],
                         "baseline": round(sum(base) / len(base), 1)}
    return {"chokepoints": out}


async def fetch_gpsjam():
    async with httpx.AsyncClient(timeout=60, headers=UA, follow_redirects=True) as c:
        man = _maybe_gunzip((await c.get("https://gpsjam.org/data/manifest.csv")).content).decode()
        day = man.strip().splitlines()[-1].split(",")[0]
        raw = _maybe_gunzip((await c.get(f"https://gpsjam.org/data/{day}-h3_4.csv")).content).decode()
    hexes = []
    for r in csv.DictReader(io.StringIO(raw)):
        lat, lon = h3.cell_to_latlng(r["hex"])
        if not (REGION[0] < lat < REGION[1] and REGION[2] < lon < REGION[3]):
            continue
        g, b = int(r["count_good_aircraft"]), int(r["count_bad_aircraft"])
        if g + b < 3:
            continue
        frac = b / (g + b)
        hexes.append({"h": r["hex"], "lat": round(lat, 3), "lon": round(lon, 3), "n": g + b, "bad": round(frac, 3),
                      "poly": [[round(a, 3), round(o, 3)] for a, o in h3.cell_to_boundary(r["hex"])] if frac >= 0.02 else None})
    return {"day": day, "hexes": hexes}


async def fetch_sdn():
    async with httpx.AsyncClient(timeout=90, headers=UA, follow_redirects=True) as c:
        txt = (await c.get("https://www.treasury.gov/ofac/downloads/sdn.csv")).content.decode("latin-1")
    vessels = []
    for r in csv.reader(io.StringIO(txt)):
        if len(r) > 11 and r[2] == "vessel":
            imo = re.findall(r"IMO (\d{7})", r[11])
            vessels.append({"id": r[0], "name": r[1].strip(), "program": r[3], "type": r[6], "flag": r[9].replace("-0-", "").strip(),
                            "owner": r[10].replace("-0-", "").strip(), "imo": imo[0] if imo else ""})
    return {"vessels": vessels}


async def refresh():
    for key, fn in (("portwatch", fetch_portwatch), ("gpsjam", fetch_gpsjam), ("sdn", fetch_sdn)):
        try:
            STATE[key] = await _cached(key, fn)
        except Exception as e:
            STATE.setdefault(key, {"error": str(e)})


@app.on_event("startup")
async def _startup():
    await refresh()

    async def loop():
        while True:
            await asyncio.sleep(TTL)
            await refresh()
    asyncio.create_task(loop())


# ---------- geometry ----------

def hav_km(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def densify(pts, step_km=8):
    out = [pts[0]]
    for a, b in zip(pts, pts[1:]):
        n = max(1, int(hav_km(a, b) / step_km))
        out += [[a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n] for i in range(1, n + 1)]
    return out


def in_poly(p, poly):
    y, x = p
    inside = False
    for (y1, x1), (y2, x2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def zone_of(p):
    for name, lvl, (a0, a1, o0, o1) in ev.ZONES:
        if a0 <= p[0] <= a1 and o0 <= p[1] <= o1:
            return name, lvl
    return None, "LOW"


def near(p, samples, km):
    return min(hav_km(p, s) for s in samples) <= km


# ---------- scoring ----------

class Ship(BaseModel):
    name: str = ""
    imo: str = ""
    type: str = "tanker"
    hull_usd_m: float = 100
    flag: str = ""
    owner_country: str = ""
    nexus_us_uk_il: bool = False
    israel_call: bool = False
    saudi_link: bool = False
    ais_on: bool = True


class Voyage(BaseModel):
    ship: Ship
    route: str = "rastanura-kaohsiung"
    speed_kn: float = 13
    port_days: float = 2
    as_of: str | None = None


def sdn_match(ship: Ship):
    vs = STATE.get("sdn", {}).get("vessels", [])
    nm = ship.name.upper().replace("(ILLUSTRATIVE)", "").strip()
    for v in vs:
        if (ship.imo and v["imo"] == ship.imo) or (nm and v["name"].upper() == nm):
            return v
    return None


def traffic_for(chokepoint, as_of: date):
    cp = STATE.get("portwatch", {}).get("chokepoints", {}).get(chokepoint)
    if not cp:
        return None
    s = [x for x in cp["series"] if x[0] <= as_of.isoformat()]
    last7 = s[-7:]
    prev7 = s[-14:-7]
    avg = sum(x[1] for x in last7) / max(1, len(last7))
    pavg = sum(x[1] for x in prev7) / max(1, len(prev7))
    return {"avg7": round(avg, 1), "prev7": round(pavg, 1), "baseline": cp["baseline"], "ratio": round(avg / cp["baseline"], 3),
            "last_day": last7[-1][0] if last7 else None}


def assess(v: Voyage):
    route = ev.ROUTES[v.route]
    samples = densify(route["pts"])
    today = date.fromisoformat(v.as_of) if v.as_of else date.today()

    # 1. attacks and incidents within 25 NM of the track, last 21 days, half-life 7 days
    near_inc, att_sum = [], 0.0
    for inc in ev.INCIDENTS:
        age = (today - date.fromisoformat(inc["date"])).days
        if 0 <= age <= 21 and near((inc["lat"], inc["lon"]), samples, 46):
            w = ev.KIND_WEIGHT[inc["kind"]] * 0.5 ** (age / 7)
            att_sum += w
            near_inc.append({**inc, "age": age, "w": round(w, 2)})
    attack = 1 - math.exp(-att_sum / 2.5)
    wk = sum(1 for i in near_inc if i["age"] <= 7 and i["kind"] == "Attack")
    pwk = sum(1 for i in near_inc if 7 < i["age"] <= 14 and i["kind"] == "Attack")

    # 2. JMIC threat level: worst zone crossed
    zones = {}
    for s in samples:
        z, lvl = zone_of(s)
        if z:
            zones[z] = lvl
    worst = max(zones.items(), key=lambda kv: ev.LEVEL_VALUE[kv[1]], default=(None, "LOW"))
    threat = ev.LEVEL_VALUE[worst[1]]

    # 3. GNSS jamming near the track (gpsjam, aircraft-derived)
    gj = STATE.get("gpsjam", {})
    near_hex = [h for h in gj.get("hexes", []) if near((h["lat"], h["lon"]), samples[::3], 45)]
    hot = [h for h in near_hex if h["bad"] >= 0.10]
    jam_share = len(hot) / len(near_hex) if near_hex else 0
    jam = min(1.0, jam_share / 0.25)

    # 4. traffic getting through the chokepoint vs pre-crisis year
    tr = traffic_for(route["via"], today)
    traffic = 1 - min(1.0, tr["ratio"]) if tr else 0.5

    # 5. who the ship is linked to
    s = v.ship
    sdn = sdn_match(s)
    link_flags = []
    if sdn:
        link_flags.append(f"OFAC SDN match: {sdn['name']} (IMO {sdn['imo'] or 'n/a'}, program {sdn['program']})")
    if s.nexus_us_uk_il:
        link_flags.append("US/UK/Israeli owner, flag, operator, UBO or financier")
    if s.israel_call:
        link_flags.append("Israeli port call since 7 Oct 2023")
    if s.saudi_link and route["via"] == "Bab el-Mandeb":
        link_flags.append("Saudi-linked on a Red Sea route (Houthi selective embargo)")
    links = 1.0 if (sdn or s.nexus_us_uk_il) else 0.8 if link_flags else 0.1

    env = 0.40 * attack + 0.27 * threat + 0.20 * traffic + 0.13 * jam
    score = round(100 * (0.75 * env + 0.25 * links))
    level = next(l for t, l in ((25, "LOW"), (45, "ELEVATED"), (62, "HIGH"), (85, "SEVERE"), (101, "EXTREME")) if score < t)

    # price band
    if route["via"] == "Strait of Hormuz":
        band = ev.RATES["hormuz_tanker" if s.type == "tanker" else "hormuz"]
    else:
        band = ev.RATES["redsea_linked" if link_flags else "redsea"]
    # conditions place an unlinked ship in the lower two thirds of the band; linked ships pay the top
    # (Lloyd's List: ships with a US/UK/Israeli nexus pay ~3x others)
    pos = 0.65 * min(1.0, max(0.0, (env - 0.3) / 0.65))
    if link_flags:
        pos = max(pos, 0.9)
    w = band["hi"] - band["lo"]
    lo = max(band["lo"], band["lo"] + w * (pos - 0.18))
    hi = min(band["hi"], band["lo"] + w * (pos + 0.18))

    # exposure inside the JWC listed area
    km_in = sum(hav_km(a, b) for a, b in zip(samples, samples[1:]) if in_poly(a, ev.LISTED_AREA))
    days_in = km_in / (v.speed_kn * 1.852 * 24) + (v.port_days if in_poly(samples[0], ev.LISTED_AREA) else 0)
    blocks = max(1, math.ceil(days_in / 7))
    hull = s.hull_usd_m * 1e6
    insurable = sdn is None
    price = {"band": band, "lo_pct": round(lo, 2), "hi_pct": round(hi, 2), "blocks": blocks, "days_in_area": round(days_in, 1),
             "km_in_area": round(km_in), "lo_usd": round(hull * lo / 100 * blocks), "hi_usd": round(hull * hi / 100 * blocks),
             "prewar_usd": round(hull * ev.RATES["prewar"]["pct"] / 100 * blocks), "insurable": insurable}

    trend = ("worsening" if wk > pwk else "easing" if wk < pwk else "steady")
    factors = [
        {"key": "attacks", "label": "Attacks near track", "value": round(attack, 2), "weight": 0.30,
         "detail": f"{len(near_inc)} JMIC-confirmed incidents within 25 NM in 21 days; {wk} attacks in the last 7 days vs {pwk} the week before"},
        {"key": "threat", "label": "JMIC threat level", "value": threat, "weight": 0.20,
         "detail": f"Worst zone crossed: {worst[0]} — {worst[1]}"},
        {"key": "traffic", "label": "Traffic getting through", "value": round(traffic, 2), "weight": 0.15,
         "detail": (f"{route['via']}: {tr['avg7']}/day over 7 days vs {tr['baseline']}/day pre-crisis ({round(100*tr['ratio'])}%)" if tr else "PortWatch unavailable")},
        {"key": "jamming", "label": "GNSS jamming", "value": round(jam, 2), "weight": 0.10,
         "detail": f"{len(hot)} of {len(near_hex)} gpsjam cells near the track show ≥10% degraded aircraft ({gj.get('day', 'n/a')})"},
        {"key": "links", "label": "Ownership & sanctions links", "value": links, "weight": 0.25,
         "detail": "; ".join(link_flags) or "No US/UK/Israel nexus, no Israeli calls, no OFAC SDN match"},
    ]

    # colour each stretch of the track by local risk for the map
    segs = []
    pts = samples[::4] + [samples[-1]]
    for a in pts:
        z, lvl = zone_of(a)
        loc = ev.LEVEL_VALUE[lvl] if in_poly(a, ev.LISTED_AREA) else 0.05
        if any(hav_km(a, (i["lat"], i["lon"])) < 46 for i in near_inc if i["kind"] == "Attack"):
            loc = max(loc, 1.0)
        segs.append([round(a[0], 3), round(a[1], 3), round(loc, 2)])

    return {"score": score, "level": level, "factors": factors, "price": price, "trend": trend, "traffic": tr,
            "incidents": sorted(near_inc, key=lambda i: i["age"]), "sdn": sdn, "link_flags": link_flags,
            "zones": zones, "track": segs, "route": {"id": v.route, **{k: route[k] for k in ("label", "via")}},
            "as_of": today.isoformat(), "ship": s.model_dump()}


# ---------- API ----------

@app.get("/api/meta")
def meta():
    gj = STATE.get("gpsjam", {})
    pw = STATE.get("portwatch", {})
    return {
        "presets": ev.PRESETS, "routes": {k: {"label": r["label"], "via": r["via"], "pts": r["pts"]} for k, r in ev.ROUTES.items()},
        "incidents": ev.INCIDENTS, "listed_area": ev.LISTED_AREA, "facts": ev.FACTS,
        "sources": ev.SOURCES, "jmic": ev.JMIC, "jmic_stats": ev.JMIC_STATS, "rates": ev.RATES,
        "jam": [{"poly": h["poly"], "bad": h["bad"]} for h in gj.get("hexes", []) if h["poly"]],
        "jam_day": gj.get("day"), "transits": pw.get("chokepoints", {}),
        "fresh": {k: STATE.get(k, {}).get("fetched_at") for k in ("portwatch", "gpsjam", "sdn")},
        "sdn_count": len(STATE.get("sdn", {}).get("vessels", [])),
    }


@app.post("/api/assess")
def api_assess(v: Voyage):
    return assess(v)


@app.get("/api/sdn")
def api_sdn(q: str):
    q = q.upper().strip()
    vs = STATE.get("sdn", {}).get("vessels", [])
    return [v for v in vs if q and (q in v["name"].upper() or q == v["imo"])][:8]


app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(HERE / "static" / "index.html")
