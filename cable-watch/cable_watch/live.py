"""Live mode: poll Fintraffic Digitraffic AIS (open, no key) and keep the last 24 hours of tracks.

Digitraffic returns only each ship's latest position, so the history builds up while the server runs.
Fixes are appended to data/live/fixes.jsonl so a restart keeps them.
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import numpy as np

from .detect import Track, score, tier

API = "https://meri.digitraffic.fi/api/ais/v1"
HEADERS = {"Digitraffic-User": "cable-watch-hackathon", "Accept-Encoding": "gzip"}
STORE = Path(__file__).resolve().parent.parent / "data" / "live"
KEEP_S = 24 * 3600


class Live:
    def __init__(self, watchlist):
        self.watchlist = watchlist
        self.fixes: dict[int, list] = {}   # mmsi -> [t, lon, lat, sog, cog, anchored]
        self.meta: dict[int, dict] = {}
        self.results: list = []
        self.started = time.time()
        self.last_poll = 0.0
        self.error = ""
        STORE.mkdir(parents=True, exist_ok=True)
        self.log = STORE / "fixes.jsonl"
        if self.log.exists():
            cut = time.time() - KEEP_S
            for line in self.log.read_text().splitlines():
                m, *f = json.loads(line)
                if f[0] >= cut:
                    self.fixes.setdefault(m, []).append(f)
        # When collection began, kept across restarts (each ship's first fix can be hours old).
        mark = STORE / "started"
        if self.fixes and mark.exists():
            self.started = max(float(mark.read_text()), time.time() - KEEP_S)
        else:
            mark.write_text(str(self.started))

    async def run(self):
        async with httpx.AsyncClient(headers=HEADERS, timeout=60) as c:
            n = 0
            while True:
                try:
                    if n % 30 == 0:
                        await self._vessels(c)
                    await self._poll(c)
                    if n % 2 == 0:
                        await asyncio.to_thread(self._score)
                    self.error = ""
                except Exception as e:  # keep polling through network errors
                    self.error = f"{type(e).__name__}: {e}"
                n += 1
                await asyncio.sleep(60)

    async def _vessels(self, c):
        for v in (await c.get(f"{API}/vessels")).json():
            self.meta[v["mmsi"]] = v

    async def _poll(self, c):
        d = (await c.get(f"{API}/locations")).json()
        new = []
        for f in d["features"]:
            p = f["properties"]
            t = p["timestampExternal"] / 1000
            lon, lat = f["geometry"]["coordinates"]
            sog = None if p["sog"] >= 102.2 else p["sog"]
            cog = None if p["cog"] >= 360 else p["cog"]
            row = [t, lon, lat, sog, cog, p["navStat"] in (1, 5)]
            tr = self.fixes.setdefault(f["mmsi"], [])
            if not tr or t > tr[-1][0] + 20:
                tr.append(row)
                new.append([f["mmsi"], *row])
        with self.log.open("a") as fh:
            fh.writelines(json.dumps(r) + "\n" for r in new)
        cut = time.time() - KEEP_S
        for m in list(self.fixes):
            self.fixes[m] = [r for r in self.fixes[m] if r[0] >= cut]
            if not self.fixes[m]:
                del self.fixes[m]
        self.last_poll = time.time()

    def track(self, mmsi) -> Track | None:
        f = self.fixes.get(mmsi)
        if not f:
            return None
        a = np.array([[r[0], r[1], r[2], np.nan if r[3] is None else r[3], np.nan if r[4] is None else r[4], r[5]]
                      for r in f], float)
        m = self.meta.get(mmsi, {})
        return Track(mmsi, a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5].astype(bool),
                     name=m.get("name", ""), imo=str(m.get("imo") or ""), ship_type=str(m.get("shipType", "")))

    def _score(self):
        out = []
        for mmsi in list(self.fixes):
            tr = self.track(mmsi)
            if tr is None or len(tr.t) < 2:
                continue
            r = score(tr, self.watchlist)
            out.append({"mmsi": mmsi, "name": tr.name, "imo": tr.imo, "type": tr.ship_type, "score": r.score,
                        "tier": tier(r.score), "signals": r.signals, "crossings": r.crossings,
                        "near": r.min_cable_m < 20_000,
                        "last": [round(float(tr.lon[-1]), 5), round(float(tr.lat[-1]), 5), int(tr.t[-1])]})
        out.sort(key=lambda v: -v["score"])
        self.results = out

    def positions(self):
        """[lon, lat, t, mmsi, score] for every ship's latest fix."""
        sc = {v["mmsi"]: v["score"] for v in self.results}
        return [[round(f[-1][1], 5), round(f[-1][2], 5), int(f[-1][0]), m, sc.get(m, 0)] for m, f in list(self.fixes.items())]

    def summary(self):
        r = self.results
        return {"ships": len(self.fixes), "near_cable": sum(v["near"] for v in r),
                "flagged": sum(v["score"] > 0 for v in r), "medium": sum(v["tier"] == "medium" for v in r),
                "high": sum(v["tier"] == "high" for v in r), "start": int(self.started), "end": int(self.last_poll),
                "fixes": sum(len(v) for v in self.fixes.values()), "error": self.error}
