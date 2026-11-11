"""Cable Watch: FastAPI server for the live Baltic feed and the replayed incident."""

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import sentinel
from .geo import suffix
from .live import Live

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
watchlist = json.loads((DATA / "watchlist.json").read_text())
live = Live(watchlist)
replays = {p.stem.removeprefix("replay-"): json.loads(p.read_text()) for p in DATA.glob("replay-*.json")}


@asynccontextmanager
async def lifespan(_):
    task = asyncio.create_task(live.run())
    yield
    task.cancel()


app = FastAPI(title="Cable Watch", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=2048)


def _json_file(name):
    return Response((DATA / name).read_bytes(), media_type="application/json",
                    headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/layers/{layer}")
def layer(layer: str, region: str = "baltic"):
    if layer not in ("cables", "land") or region not in ("baltic", "taiwan"):
        raise HTTPException(404)
    return _json_file(f"{layer}{suffix(region)}.geojson")


@app.get("/api/replay/{case}")
def replay(case: str):
    if case not in replays:
        raise HTTPException(404)
    return replays[case]


@app.get("/api/replay/{case}/traffic")
def replay_traffic(case: str):
    if case not in replays:
        raise HTTPException(404)
    return _json_file(f"replay-{case}-traffic.json")


@app.get("/api/taiwan")
def taiwan():
    gfw = DATA / "taiwan-gfw.json"
    return {"incidents": json.loads((DATA / "taiwan-incidents.json").read_text()),
            "gfw": json.loads(gfw.read_text()) if gfw.exists() else None}


@app.get("/api/live")
def live_list():
    return {"summary": live.summary(), "vessels": [v for v in live.results if v["score"] > 0][:100],
            "positions": live.positions()}


@app.get("/api/live/{mmsi}")
def live_vessel(mmsi: int):
    tr = live.track(mmsi)
    if tr is None:
        raise HTTPException(404)
    v = next((v for v in live.results if v["mmsi"] == mmsi), {})
    return {**v, "track": [[round(float(a), 5), round(float(b), 5), int(c), None if np.isnan(d) else float(d)]
                           for a, b, c, d in zip(tr.lon, tr.lat, tr.t, tr.sog)]}


@app.get("/api/s1")
def s1(lon: float, lat: float, start: int, end: int):
    return sentinel.passes(lon, lat, start, end)


app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")
