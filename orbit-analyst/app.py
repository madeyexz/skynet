"""Orbit Analyst web app. Run: uv run uvicorn app:app"""

import json
import math
import re
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
import tracks
from demos import CACHE, DEMOS

app = FastAPI(title="Orbit Analyst")
app.add_middleware(GZipMiddleware, minimum_size=2000)
pool = ThreadPoolExecutor(max_workers=2)
jobs: dict[str, dict] = {}
lock = threading.Lock()

agent.MEDIA.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=agent.ROOT / "static"), name="static")
app.mount("/media", StaticFiles(directory=agent.MEDIA), name="media")


def norm(q: str) -> str:
    return re.sub(r"\W+", " ", q.lower()).strip()


def cached(demo_id: str):
    path = CACHE / f"{demo_id}.json"
    return json.loads(path.read_text()) if path.exists() else None


@app.get("/")
def index():
    return FileResponse(agent.ROOT / "static" / "index.html")


@app.get("/track")
def track_page():
    return FileResponse(agent.ROOT / "static" / "track.html")


@app.get("/api/demos")
def demos():
    out = []
    for demo_id, plan in DEMOS.items():
        c = cached(demo_id) or {}
        out.append({"id": demo_id, "question": plan.question, "place": plan.place, "task": plan.task,
                    "headline": c.get("headline"), "sensor": c.get("sensor")})
    return out


class Ask(BaseModel):
    question: str = ""
    demo: str | None = None


def work(job_id: str, question: str):
    log = agent.Log(sink=lambda steps: jobs[job_id].update(steps=list(steps)))
    try:
        plan = agent.make_plan(question, log)
        result = agent.run(plan, log, job_id)
        result["steps"] = log.steps
        result["cached"] = False
        jobs[job_id].update(status="done", result=result, steps=list(log.steps))
    except Exception as e:  # report every failure to the user as a plain sentence
        traceback.print_exc()
        msg = str(e) if isinstance(e, ValueError) else f"The analysis stopped: {e.__class__.__name__}: {e}"
        log(msg, "error")
        jobs[job_id].update(status="error", error=msg, steps=list(log.steps))


@app.post("/api/ask")
def ask(body: Ask):
    demo_id = body.demo
    if not demo_id:
        demo_id = next((k for k, p in DEMOS.items() if norm(p.question) == norm(body.question)), None)
    job_id = "live-" + uuid.uuid4().hex[:10]
    if demo_id:
        result = cached(demo_id)
        if not result:
            raise HTTPException(404, "This demo has no cached answer. Run `uv run python demos.py`.")
        jobs[job_id] = {"status": "done", "steps": result["steps"], "result": result}
        return {"job": job_id, "cached": True}
    if len(body.question.strip()) < 8:
        raise HTTPException(400, "Ask a full question that names a place.")
    with lock:
        jobs[job_id] = {"status": "running", "steps": [], "question": body.question}
    pool.submit(work, job_id, body.question)
    return {"job": job_id, "cached": False}


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job")
    return jobs[job_id]


# ---------------------------------------------------------------- tracker

@app.on_event("startup")
def start_live():
    tracks.live.start()


@app.get("/api/tracks/{source}")
def get_tracks(source: str):
    if source == "skagen":
        return tracks.replay()
    if source == "live":
        return tracks.live.snapshot()
    if source == "sim":
        return tracks.sim_snapshot()
    raise HTTPException(404, "Unknown track source")


class Scan(BaseModel):
    polygon: list[list[float]]  # [[lon, lat], ...]
    time: float | None = None   # seconds since the epoch: use the radar pass nearest to this moment


def zone_km2(poly):
    lat0 = math.radians(sum(p[1] for p in poly) / len(poly))
    xy = [(p[0] * 111.32 * math.cos(lat0), p[1] * 110.57) for p in poly]
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(xy, xy[1:] + xy[:1]))) / 2


def scan_work(job_id: str, body: Scan):
    log = agent.Log(sink=lambda steps: jobs[job_id].update(steps=list(steps)))
    try:
        lons, lats = [p[0] for p in body.polygon], [p[1] for p in body.polygon]
        bbox = [min(lons), min(lats), max(lons), max(lats)]
        when = body.time or time.time()
        d = datetime.fromtimestamp(when, timezone.utc).date()
        end = min(d + timedelta(days=1), datetime.now(timezone.utc).date())
        items = agent.search("sentinel-1-rtc", bbox, ((d - timedelta(days=6)).isoformat(), end.isoformat()))
        if not items:
            raise ValueError("No Sentinel-1 radar pass covers this zone in the 6 days before this moment.")
        best = min(items, key=lambda i: abs(datetime.fromisoformat(
            i["properties"]["datetime"].replace("Z", "+00:00")).timestamp() - when))
        day = best["properties"]["datetime"][:10]
        log(f"Zone: {zone_km2(body.polygon):.0f} km². The nearest radar pass to the chosen moment is "
            f"{best['properties']['datetime'][:16].replace('T', ' ')} UTC.")
        plan = agent.Plan(question="Scan this zone for ships", task="vessels", place="your zone",
                          lat=sum(lats) / len(lats), lon=sum(lons) / len(lons), bbox=bbox, before=None,
                          after=(day, day), res=10.0, polygon=body.polygon,
                          # the absolute orbit number picks every frame of that one pass
                          notes=[f"pick:_{best['id'].split('_')[6]}_"])
        result = agent.run(plan, log, job_id)
        result["steps"] = log.steps
        jobs[job_id].update(status="done", result=result, steps=list(log.steps))
    except Exception as e:
        traceback.print_exc()
        msg = str(e) if isinstance(e, ValueError) else f"The scan stopped: {e.__class__.__name__}: {e}"
        log(msg, "error")
        jobs[job_id].update(status="error", error=msg, steps=list(log.steps))


@app.post("/api/scan")
def scan(body: Scan):
    if len(body.polygon) < 3:
        raise HTTPException(400, "Draw a zone with at least three corners.")
    km2 = zone_km2(body.polygon)
    if km2 > 2500:
        raise HTTPException(400, f"The zone is {km2:.0f} km². Draw one under 2,500 km² so the scan stays under a minute.")
    job_id = "scan-" + uuid.uuid4().hex[:10]
    jobs[job_id] = {"status": "running", "steps": []}
    pool.submit(scan_work, job_id, body)
    return {"job": job_id}


class Note(BaseModel):
    source: str
    t: float                    # seconds since the epoch
    lat: float
    lon: float
    text: str
    tag: str = "note"           # note | suspicious | watch | cleared
    mmsi: str | None = None
    name: str | None = None


@app.get("/api/notes")
def notes(source: str | None = None):
    return [n for n in tracks.load_notes() if source is None or n["source"] == source]


@app.post("/api/notes")
def add_note(note: Note):
    if not note.text.strip():
        raise HTTPException(400, "Write something in the note.")
    with lock:
        ns = tracks.load_notes()
        n = note.model_dump() | {"id": uuid.uuid4().hex[:8], "created": time.time()}
        ns.append(n)
        tracks.save_notes(ns)
    return n


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str):
    with lock:
        ns = tracks.load_notes()
        tracks.save_notes([n for n in ns if n["id"] != note_id])
    return {"ok": True}
