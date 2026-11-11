"""Routes for the real-data cases (owner C). app.py includes `router`."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from server.real import cases

router = APIRouter()
MEDIA = Path(__file__).resolve().parents[2] / "data" / "real" / "media"


@router.get("/api/cases")
def list_cases():
    return cases.cases()


@router.get("/api/real/media/{name}")
def media(name: str):
    p = MEDIA / name
    if Path(name).name != name or not p.is_file():
        raise HTTPException(404, "no such image")
    return FileResponse(p, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/api/warrisk/assess")
async def warrisk_assess(route_id: str = "rastanura-kaohsiung", vessel_id: str = "T-11", as_of: str | None = None,
                         case: str = "war-risk"):
    """Debug: assess a vessel from a case world (default T-11) on a war-risk route."""
    from server.real import warrisk

    def run():
        ship = dict(cases.T11_SHIP)
        if vessel_id != "T-11" or case != "war-risk":
            try:
                w = cases.get_world(case)
            except KeyError as e:
                raise HTTPException(404, str(e))
            v = next((v for v in w["vessels"] if v["id"] == vessel_id), None)
            if v is None:
                raise HTTPException(404, f"vessel {vessel_id!r} not in case {case!r}")
            ship = {"name": v.get("name") or "", "imo": v.get("imo") or "", "type": v.get("type") or "tanker",
                    "flag": v.get("flag") or "", "hull_usd_m": (v.get("voyage") or {}).get("hull_usd_m", 100)}
        try:
            return warrisk.assess_voyage(ship, route_id, as_of)
        except KeyError as e:
            raise HTTPException(404, str(e))

    return await asyncio.to_thread(run)
