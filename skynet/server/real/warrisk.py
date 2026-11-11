"""War-risk voyage assessment, ported from war-risk-copilot (app.py `assess()` + evidence.py).

Evidence (JMIC incidents, JWC listed area, routes, rate bands, sources) is a copy of war-risk-copilot/evidence.py
in data/real/warrisk_evidence.json. Three live feeds refine the score:
- IMF PortWatch daily chokepoint transits,
- gpsjam.org GNSS interference hexes (aircraft ADS-B),
- OFAC SDN vessel list.
Each feed is cached under data/cache/ for 6 h. A daemon thread refreshes them, started lazily on the first call,
so neither startup nor a request waits on the network. A missing feed is reported in `missing`, and the score
uses only the factors that are available.

Public entry point: assess_voyage(ship: dict, route_id: str, as_of: str | None) -> dict
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

import h3
import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
EV = json.loads((ROOT / "data" / "real" / "warrisk_evidence.json").read_text())
CACHE = Path(os.environ.get("SKYNET_DATA_DIR") or ROOT / "data") / "cache"  # the release may be read-only
TTL = 6 * 3600
UA = {"User-Agent": "Mozilla/5.0 skynet war-risk (hackathon demo)"}

PORTWATCH = ("https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/Daily_Chokepoints_Data/"
             "FeatureServer/0/query")
CHOKEPOINTS = {"Strait of Hormuz": "chokepoint6", "Bab el-Mandeb": "chokepoint4"}
CRISIS_START = date(2026, 2, 28)
REGION = (-8.0, 32.5, 30.0, 70.0)  # lat0, lat1, lon0, lon1 for jamming hexes

INCIDENTS = EV["INCIDENTS"]
ROUTES = EV["ROUTES"]
LISTED_AREA = EV["LISTED_AREA"]          # [[lat, lon], ...]
RATES = EV["RATES"]
JMIC = EV["JMIC"]                        # {"109": url, ...}
SOURCES = EV["SOURCES"]                  # {key: [label, url]}

STATE: dict = {}
_lock = threading.Lock()
_started = False


# ---------- live feeds (sync, run in a daemon thread) ----------

def _gunzip(b: bytes) -> bytes:
    return gzip.decompress(b) if b[:2] == b"\x1f\x8b" else b


def fetch_portwatch() -> dict:
    out = {}
    with httpx.Client(timeout=40, headers=UA) as c:
        for name, pid in CHOKEPOINTS.items():
            r = c.get(PORTWATCH, params={
                "where": f"portid='{pid}' AND date >= DATE '2025-02-28'",
                "outFields": "date,n_total,n_tanker,n_container", "orderByFields": "date ASC",
                "resultRecordCount": 1000, "f": "json"})
            r.raise_for_status()
            rows = [f["attributes"] for f in r.json()["features"]]
            # PortWatch dates come as epoch milliseconds or ISO strings depending on the layer version.
            for x in rows:
                if isinstance(x["date"], (int, float)):
                    x["date"] = datetime.fromtimestamp(x["date"] / 1000, timezone.utc).date().isoformat()
                else:
                    x["date"] = str(x["date"])[:10]
            base = [x["n_total"] for x in rows if x["date"] < CRISIS_START.isoformat()]
            out[name] = {"series": [[x["date"], x["n_total"], x["n_tanker"]] for x in rows],
                         "baseline": round(sum(base) / len(base), 1) if base else None}
    return {"chokepoints": out}


def fetch_gpsjam() -> dict:
    with httpx.Client(timeout=60, headers=UA, follow_redirects=True) as c:
        man = _gunzip(c.get("https://gpsjam.org/data/manifest.csv").content).decode()
        day = man.strip().splitlines()[-1].split(",")[0]
        raw = _gunzip(c.get(f"https://gpsjam.org/data/{day}-h3_4.csv").content).decode()
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
                      "poly": [[round(a, 3), round(o, 3)] for a, o in h3.cell_to_boundary(r["hex"])]
                      if frac >= 0.02 else None})
    return {"day": day, "hexes": hexes}


def fetch_sdn() -> dict:
    with httpx.Client(timeout=90, headers=UA, follow_redirects=True) as c:
        txt = c.get("https://www.treasury.gov/ofac/downloads/sdn.csv").content.decode("latin-1")
    vessels = []
    for r in csv.reader(io.StringIO(txt)):
        if len(r) > 11 and r[2] == "vessel":
            imo = re.findall(r"IMO (\d{7})", r[11])
            vessels.append({"id": r[0], "name": r[1].strip(), "program": r[3], "type": r[6],
                            "flag": r[9].replace("-0-", "").strip(), "owner": r[10].replace("-0-", "").strip(),
                            "imo": imo[0] if imo else ""})
    return {"vessels": vessels}


FEEDS = {"portwatch": fetch_portwatch, "gpsjam": fetch_gpsjam, "sdn": fetch_sdn}


def _load_disk():
    """Last good copies from disk (any age), so the first request already has them."""
    for key in FEEDS:
        p = CACHE / f"{key}.json"
        if key not in STATE and p.exists():
            try:
                d = json.loads(p.read_text())
                d["_mtime"] = p.stat().st_mtime
                STATE[key] = d
            except ValueError:
                pass


def _refresh_once():
    CACHE.mkdir(parents=True, exist_ok=True)
    for key, fn in FEEDS.items():
        p = CACHE / f"{key}.json"
        if p.exists() and time.time() - p.stat().st_mtime < TTL and key in STATE and "error" not in STATE[key]:
            continue
        try:
            data = fn()
            data["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="minutes")
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data))
            tmp.replace(p)
            data["_mtime"] = time.time()
            STATE[key] = data
        except Exception as e:  # fail soft: keep the last good copy, else mark unavailable
            if key in STATE and "error" not in STATE[key]:
                STATE[key]["stale"] = f"{type(e).__name__}: {e}"
            else:
                STATE[key] = {"error": f"{type(e).__name__}: {e}"}


def _loop():
    while True:
        _refresh_once()
        time.sleep(TTL)


def ensure_started():
    """Load disk copies and start the refresh thread once. Never blocks on the network."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
        _load_disk()
        threading.Thread(target=_loop, name="warrisk-feeds", daemon=True).start()


def feed_status() -> dict:
    out = {}
    for k in FEEDS:
        s = STATE.get(k)
        if not s or "error" in s:
            out[k] = {"ok": False, "error": (s or {}).get("error", "not loaded yet")}
        else:
            out[k] = {"ok": True, "fetched_at": s.get("fetched_at"), "stale": s.get("stale")}
    return out


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
    for name, lvl, (a0, a1, o0, o1) in EV["ZONES"]:
        if a0 <= p[0] <= a1 and o0 <= p[1] <= o1:
            return name, lvl
    return None, "LOW"


def near(p, samples, km):
    """True if point p (lat, lon) is within km of any sample; samples is a list or an (n, 2) array of lat, lon."""
    a = np.radians(np.asarray(samples, float))
    la, lo = math.radians(p[0]), math.radians(p[1])
    h = np.sin((a[:, 0] - la) / 2) ** 2 + math.cos(la) * np.cos(a[:, 0]) * np.sin((a[:, 1] - lo) / 2) ** 2
    return float(6371 * 2 * np.arcsin(np.sqrt(h.min()))) <= km


# ---------- scoring ----------

SHIP_DEFAULTS = {"name": "", "imo": "", "type": "tanker", "hull_usd_m": 100, "flag": "", "owner_country": "",
                 "nexus_us_uk_il": False, "israel_call": False, "saudi_link": False, "ais_on": True,
                 "speed_kn": 13, "port_days": 2}


def sdn_match(ship):
    vs = STATE.get("sdn", {}).get("vessels", [])
    nm = str(ship.get("name") or "").upper().replace("(ILLUSTRATIVE)", "").strip()
    imo = str(ship.get("imo") or "")
    for v in vs:
        if (imo and v["imo"] == imo) or (nm and v["name"].upper() == nm):
            return v
    return None


def traffic_for(chokepoint, as_of: date):
    cp = STATE.get("portwatch", {}).get("chokepoints", {}).get(chokepoint)
    if not cp or not cp.get("baseline"):
        return None
    s = [x for x in cp["series"] if x[0] <= as_of.isoformat()]
    last7, prev7 = s[-7:], s[-14:-7]
    if not last7:
        return None
    avg = sum(x[1] for x in last7) / len(last7)
    pavg = sum(x[1] for x in prev7) / max(1, len(prev7))
    return {"avg7": round(avg, 1), "prev7": round(pavg, 1), "baseline": cp["baseline"],
            "ratio": round(avg / cp["baseline"], 3), "last_day": last7[-1][0]}


def incident_source(inc) -> dict:
    return {"label": f"JMIC Advisory Update {inc['upd']}", "url": JMIC.get(str(inc["upd"]), "")}


def _src(key):
    label, url = SOURCES[key]
    return {"label": label, "url": url}


def assess_voyage(ship: dict, route_id: str = "rastanura-kaohsiung", as_of: str | None = None) -> dict:
    """Score one ship on one voyage. `ship` keys (all optional): name, imo, type ("tanker"|"container"|...),
    hull_usd_m, flag, owner_country, nexus_us_uk_il, israel_call, saudi_link, ais_on, speed_kn, port_days.
    `as_of` is an ISO date (default today, UTC). Never waits on the network."""
    ensure_started()
    if route_id not in ROUTES:
        raise KeyError(f"unknown route_id {route_id!r}; known: {', '.join(ROUTES)}")
    s = {**SHIP_DEFAULTS, **{k: v for k, v in (ship or {}).items() if v is not None}}
    route = ROUTES[route_id]
    samples = densify(route["pts"])
    today = date.fromisoformat(as_of[:10]) if as_of else datetime.now(timezone.utc).date()
    missing = []

    # 1. attacks and incidents within 25 NM (46 km) of the track, last 21 days, half-life 7 days
    near_inc, att_sum = [], 0.0
    for inc in INCIDENTS:
        age = (today - date.fromisoformat(inc["date"])).days
        if 0 <= age <= 21 and near((inc["lat"], inc["lon"]), samples, 46):
            w = EV["KIND_WEIGHT"][inc["kind"]] * 0.5 ** (age / 7)
            att_sum += w
            near_inc.append({**inc, "age": age, "w": round(w, 2), "source": incident_source(inc)})
    attack = 1 - math.exp(-att_sum / 2.5)
    attacks_n = sum(1 for i in near_inc if i["kind"] == "Attack")
    wk = sum(1 for i in near_inc if i["age"] <= 7 and i["kind"] == "Attack")
    pwk = sum(1 for i in near_inc if 7 < i["age"] <= 14 and i["kind"] == "Attack")

    # 2. JMIC threat level: worst zone crossed
    zones = {}
    for p in samples:
        z, lvl = zone_of(p)
        if z:
            zones[z] = lvl
    worst = max(zones.items(), key=lambda kv: EV["LEVEL_VALUE"][kv[1]], default=(None, "LOW"))
    threat = EV["LEVEL_VALUE"][worst[1]]

    # 3. GNSS jamming near the track (gpsjam)
    gj = STATE.get("gpsjam", {})
    jam = None
    near_hex, hot = [], []
    if gj.get("hexes"):
        sub = np.asarray(samples[::3], float)
        near_hex = [h for h in gj["hexes"] if near((h["lat"], h["lon"]), sub, 45)]
        hot = [h for h in near_hex if h["bad"] >= 0.10]
        jam = min(1.0, (len(hot) / len(near_hex) if near_hex else 0) / 0.25)
    else:
        missing.append("gpsjam")

    # 4. traffic getting through the chokepoint vs the pre-crisis year (PortWatch)
    tr = traffic_for(route["via"], today)
    traffic = (1 - min(1.0, tr["ratio"])) if tr else None
    if tr is None:
        missing.append("portwatch")

    # 5. ownership and sanctions links
    sdn = sdn_match(s)
    if not STATE.get("sdn", {}).get("vessels"):
        missing.append("ofac_sdn")
    link_flags = []
    if sdn:
        link_flags.append(f"OFAC SDN match: {sdn['name']} (IMO {sdn['imo'] or 'n/a'}, program {sdn['program']})")
    if s["nexus_us_uk_il"]:
        link_flags.append("US/UK/Israeli owner, flag, operator, UBO or financier")
    if s["israel_call"]:
        link_flags.append("Israeli port call since 7 Oct 2023")
    if s["saudi_link"] and route["via"] == "Bab el-Mandeb":
        link_flags.append("Saudi-linked on a Red Sea route (Houthi selective embargo)")
    links = 1.0 if (sdn or s["nexus_us_uk_il"]) else 0.8 if link_flags else 0.1

    # environment: weighted mean over the factors we have (same weights as war-risk-copilot)
    parts = [(0.40, attack), (0.27, threat), (0.20, traffic), (0.13, jam)]
    have = [(w, v) for w, v in parts if v is not None]
    env = sum(w * v for w, v in have) / sum(w for w, _ in have)
    score = round(100 * (0.75 * env + 0.25 * links))
    level = next(l for t, l in ((25, "LOW"), (45, "ELEVATED"), (62, "HIGH"), (85, "SEVERE"), (101, "EXTREME"))
                 if score < t)

    # price band, % of hull value per 7-day block
    if route["via"] == "Strait of Hormuz":
        band = RATES["hormuz_tanker" if s["type"] == "tanker" else "hormuz"]
    else:
        band = RATES["redsea_linked" if link_flags else "redsea"]
    pos = 0.65 * min(1.0, max(0.0, (env - 0.3) / 0.65))
    if link_flags:
        pos = max(pos, 0.9)
    w = band["hi"] - band["lo"]
    lo = max(band["lo"], band["lo"] + w * (pos - 0.18))
    hi = min(band["hi"], band["lo"] + w * (pos + 0.18))
    km_in = sum(hav_km(a, b) for a, b in zip(samples, samples[1:]) if in_poly(a, LISTED_AREA))
    days_in = km_in / (s["speed_kn"] * 1.852 * 24) + (s["port_days"] if in_poly(samples[0], LISTED_AREA) else 0)
    blocks = max(1, math.ceil(days_in / 7))
    hull = s["hull_usd_m"] * 1e6
    price = {"band": band, "lo_pct": round(lo, 2), "hi_pct": round(hi, 2), "blocks": blocks,
             "days_in_area": round(days_in, 1), "km_in_area": round(km_in),
             "lo_usd": round(hull * lo / 100 * blocks), "hi_usd": round(hull * hi / 100 * blocks),
             "prewar_usd": round(hull * RATES["prewar"]["pct"] / 100 * blocks), "insurable": sdn is None,
             "source": {"label": band["src"], "url": band["url"]}}

    trend = "worsening" if wk > pwk else "easing" if wk < pwk else "steady"
    factors = [
        {"key": "attacks", "label": "Attacks near track", "value": round(attack, 2), "weight": 0.30, "available": True,
         "detail": f"{len(near_inc)} JMIC-confirmed incidents within 25 NM in 21 days ({attacks_n} attacks); "
                   f"{wk} attacks in the last 7 days vs {pwk} the week before"},
        {"key": "threat", "label": "JMIC threat level", "value": threat, "weight": 0.20, "available": True,
         "detail": f"Worst zone crossed: {worst[0]} ({worst[1]}), JMIC Advisory Update 112"},
        {"key": "traffic", "label": "Traffic getting through", "value": None if traffic is None else round(traffic, 2),
         "weight": 0.15, "available": tr is not None,
         "detail": (f"{route['via']}: {tr['avg7']}/day over 7 days to {tr['last_day']} vs {tr['baseline']}/day "
                    f"pre-crisis ({round(100 * tr['ratio'])}%), IMF PortWatch") if tr
         else "IMF PortWatch unavailable: not scored"},
        {"key": "jamming", "label": "GNSS jamming", "value": None if jam is None else round(jam, 2), "weight": 0.10,
         "available": jam is not None,
         "detail": (f"{len(hot)} of {len(near_hex)} gpsjam cells near the track show ≥10% degraded aircraft "
                    f"({gj.get('day')})") if jam is not None else "gpsjam unavailable: not scored"},
        {"key": "links", "label": "Ownership & sanctions links", "value": links, "weight": 0.25, "available": True,
         "detail": "; ".join(link_flags) or ("No US/UK/Israel nexus, no Israeli calls, no OFAC SDN match"
                                             if "ofac_sdn" not in missing else
                                             "No US/UK/Israel nexus, no Israeli calls (OFAC SDN list not loaded)")},
    ]

    sources = [incident_source({"upd": u}) for u in sorted({i["upd"] for i in near_inc})]
    sources += [_src("jmic112"), _src("jwla033"), {"label": band["src"], "url": band["url"]},
                {"label": RATES["prewar"]["src"], "url": RATES["prewar"]["url"]}]
    if tr:
        sources.append(_src("portwatch"))
    if jam is not None:
        sources.append(_src("gpsjam"))
    if "ofac_sdn" not in missing:
        sources.append(_src("ofac"))
    seen, uniq = set(), []
    for x in sources:
        if x["url"] not in seen:
            seen.add(x["url"])
            uniq.append(x)

    return {"score": score, "level": level, "factors": factors, "price": price, "trend": trend, "traffic": tr,
            "incidents": sorted(near_inc, key=lambda i: i["age"]), "attacks_near_route": attacks_n,
            "sdn": sdn, "link_flags": link_flags, "zones": zones, "worst_zone": {"name": worst[0], "level": worst[1]},
            "route": {"id": route_id, "label": route["label"], "via": route["via"]},
            "as_of": today.isoformat(), "ship": s, "missing": missing, "feeds": feed_status(), "sources": uniq}


def jamming_geojson() -> dict | None:
    """gpsjam hexes (≥2% degraded) as a GeoJSON FeatureCollection, or None if the feed is not loaded."""
    gj = STATE.get("gpsjam", {})
    if not gj.get("hexes"):
        return None
    feats = []
    for h in gj["hexes"]:
        if h.get("poly"):
            ring = [[lon, lat] for lat, lon in h["poly"]]
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring + ring[:1]]},
                          "properties": {"bad": h["bad"], "n": h["n"], "kind": "jamming"}})
    return {"type": "FeatureCollection", "features": feats, "properties": {"day": gj.get("day")}}
