"""Ship-track sources for the tracker: the Skagen AIS replay and a live feed from Digitraffic.

Digitraffic (Fintraffic, Finland) publishes real-time AIS for the Baltic around Finland with no key.
It only gives the latest fix per ship, so the server polls it and keeps the last three hours.
"""

from __future__ import annotations

import gzip
import json
import threading
import time
from collections import deque
from datetime import datetime, timezone

import httpx

from agent import ROOT

REPLAY_FILE = ROOT / "data" / "tracks_skagen_2026-11-06.json.gz"
PASSES_FILE = ROOT / "cache" / "track-skagen-passes.json"
NOTES_FILE = ROOT / "cache" / "notes.json"
LIVE_FILE = ROOT / "cache" / "live_tracks.json"

DT_API = "https://meri.digitraffic.fi/api/ais/v1"
DT_HEADERS = {"Digitraffic-User": "orbit-analyst/hackathon", "Accept-Encoding": "gzip"}
LIVE_CENTER = (59.95, 25.0)   # Gulf of Finland, between Helsinki and Tallinn
LIVE_RADIUS_KM = 70
LIVE_KEEP_S = 3 * 3600
LIVE_POLL_S = 15

NAV_STATUS = {0: "Under way using engine", 1: "At anchor", 2: "Not under command", 3: "Restricted manoeuvrability",
              4: "Constrained by her draught", 5: "Moored", 6: "Aground", 7: "Engaged in fishing",
              8: "Under way sailing", 15: "Unknown value"}


def ship_type(code) -> str:
    try:
        c = int(code)
    except (TypeError, ValueError):
        return ""
    if c == 30:
        return "Fishing"
    if c in (31, 32, 52):
        return "Tug"
    if c == 35:
        return "Military"
    if c == 36:
        return "Sailing"
    if c == 37:
        return "Pleasure"
    if 40 <= c <= 49:
        return "HSC"
    if c == 50:
        return "Pilot"
    if c == 51:
        return "SAR"
    if 60 <= c <= 69:
        return "Passenger"
    if 70 <= c <= 79:
        return "Cargo"
    if 80 <= c <= 89:
        return "Tanker"
    return "Other"


_replay = None


def replay():
    global _replay
    if _replay is None:
        with gzip.open(REPLAY_FILE, "rt") as f:
            _replay = json.load(f)
        if PASSES_FILE.exists():
            _replay["passes"] = json.loads(PASSES_FILE.read_text())
    return _replay


class LiveFeed:
    """Polls Digitraffic and keeps a rolling track per ship."""

    def __init__(self):
        self.tracks: dict[int, deque] = {}
        self.meta: dict[int, dict] = {}
        self.statuses = list(NAV_STATUS.values())
        self.last_ok = 0.0
        self.last_error = ""
        self.meta_at = 0.0
        self.lock = threading.Lock()
        self.started = False
        self.started_at = time.time()

    def start(self):
        if self.started:
            return
        self.started = True
        self.started_at = time.time()
        if LIVE_FILE.exists():  # keep the history across server restarts
            try:
                saved = json.loads(LIVE_FILE.read_text())
                if time.time() - saved["saved"] < LIVE_KEEP_S:
                    self.tracks = {int(k): deque(tuple(p) for p in v) for k, v in saved["tracks"].items()}
                    self.started_at = saved["started_at"]
            except (ValueError, KeyError):
                pass
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        n = 0
        while True:
            try:
                self.poll()
                n += 1
                if n % 8 == 0:
                    with self.lock:
                        data = {"saved": time.time(), "started_at": self.started_at,
                                "tracks": {k: list(v) for k, v in self.tracks.items()}}
                    LIVE_FILE.write_text(json.dumps(data, separators=(",", ":")))
            except Exception as e:  # keep polling; the API reports the error to the page
                self.last_error = f"{e.__class__.__name__}: {e}"
            time.sleep(LIVE_POLL_S)

    def poll(self):
        with httpx.Client(headers=DT_HEADERS, timeout=30) as c:
            if time.time() - self.meta_at > 3600:
                for v in c.get(f"{DT_API}/vessels").json():
                    a, b = v.get("referencePointA") or 0, v.get("referencePointB") or 0
                    self.meta[v["mmsi"]] = {"name": (v.get("name") or "").strip(), "type": ship_type(v.get("shipType")),
                                            "length": str(a + b) if a + b else "",
                                            "dest": (v.get("destination") or "").strip()}
                self.meta_at = time.time()
            r = c.get(f"{DT_API}/locations", params={"latitude": LIVE_CENTER[0], "longitude": LIVE_CENTER[1],
                                                      "radius": LIVE_RADIUS_KM})
            r.raise_for_status()
            feats = r.json()["features"]
        now = time.time()
        with self.lock:
            for f in feats:
                p = f["properties"]
                t = int(p["timestampExternal"] / 1000)
                lon, lat = f["geometry"]["coordinates"]
                dq = self.tracks.setdefault(p["mmsi"], deque())
                if dq and dq[-1][0] >= t:
                    continue
                stat = NAV_STATUS.get(p.get("navStat"), "Unknown value")
                sog = p.get("sog") if p.get("sog") is not None and p.get("sog") < 102 else -1
                cog = p.get("cog") if p.get("cog") is not None and p.get("cog") < 360 else -1
                dq.append((t, round(lat, 5), round(lon, 5), sog, round(cog), self.statuses.index(stat)))
            for dq in self.tracks.values():
                while dq and dq[0][0] < now - LIVE_KEEP_S:
                    dq.popleft()
            self.tracks = {k: v for k, v in self.tracks.items() if v}
        self.last_ok = now
        self.last_error = ""

    def snapshot(self):
        now = time.time()
        t0 = int(now - LIVE_KEEP_S)
        ships = []
        with self.lock:
            for mmsi, dq in self.tracks.items():
                m = self.meta.get(mmsi, {})
                ships.append({"mmsi": str(mmsi), "name": m.get("name", ""), "type": m.get("type", ""),
                              "length": m.get("length", ""), "dest": m.get("dest", ""), "class": "",
                              "p": [[t - t0, lat, lon, sog, cog, st] for t, lat, lon, sog, cog, st in dq]})
        lat, lon = LIVE_CENTER
        return {"id": "live-gulf-of-finland", "title": "Live · Gulf of Finland", "kind": "live", "t0": t0,
                "now": int(now - t0), "bbox": [lon - 1.25, lat - 0.63, lon + 1.25, lat + 0.63],
                "circle": [lat, lon, LIVE_RADIUS_KM], "collecting_since": int(max(self.started_at, t0) - t0),
                "statuses": self.statuses, "ok": now - self.last_ok < 120, "error": self.last_error,
                "polled": int(self.last_ok), "source": "Fintraffic Digitraffic real-time AIS (meri.digitraffic.fi)",
                "ships": ships, "passes": []}


live = LiveFeed()


def sim_snapshot():
    """Mock live feed: the Skagen AIS day replayed on today's clock, last three hours only."""
    r = replay()
    now = time.time()
    tod = now % 86400
    lo = max(0.0, tod - LIVE_KEEP_S)
    t0 = now - (tod - lo)
    ships = []
    for s in r["ships"]:
        p = [[round(pt[0] - lo), *pt[1:]] for pt in s["p"] if lo <= pt[0] <= tod]
        if p:
            ships.append({k: v for k, v in s.items() if k != "p"} | {"p": p})
    passes = []
    for ps in r.get("passes", []):
        rel = datetime.fromisoformat(ps["pass_time"].replace("Z", "+00:00")).timestamp() - r["t0"]
        if lo <= rel <= tod:
            passes.append(ps | {"pass_time": datetime.fromtimestamp(t0 + rel - lo, timezone.utc).isoformat()})
    return {"id": "sim-skagen", "title": "Simulated live · Skagen", "kind": "sim", "t0": t0, "now": int(tod - lo),
            "bbox": r["bbox"], "statuses": r["statuses"], "ok": True, "error": "",
            "replay_t0": r["t0"] + lo,  # the 6 Nov moment that t = 0 replays
            "source": "Simulated: Danish Maritime Authority AIS for 6 Nov 2026, replayed on today's clock",
            "ships": ships, "passes": passes}


def load_notes() -> list[dict]:
    return json.loads(NOTES_FILE.read_text()) if NOTES_FILE.exists() else []


def save_notes(notes: list[dict]):
    NOTES_FILE.parent.mkdir(exist_ok=True)
    NOTES_FILE.write_text(json.dumps(notes, indent=1, ensure_ascii=False))
