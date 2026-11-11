"""Global Fishing Watch API v3 (free token: https://globalfishingwatch.org/our-apis/tokens).

AIS events for all vessel types (loitering, AIS-off gaps) and Sentinel-1 ship detections that matched
no AIS ship, worldwide from 2012 / 2017 until about four days ago. Read the token from GFW_API_TOKEN or ~/.config/cable-watch/gfw_token.
"""

import os
from pathlib import Path

import httpx

TOKEN_FILE = Path.home() / ".config" / "cable-watch" / "gfw_token"

API = "https://gateway.api.globalfishingwatch.org/v3"
DATASETS = {"loitering": "public-global-loitering-events:latest", "gap": "public-global-gaps-events:latest"}


class NoToken(Exception):
    pass


def _client():
    token = os.environ.get("GFW_API_TOKEN", "").strip() or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")
    if not token:
        raise NoToken()
    return httpx.Client(base_url=API, timeout=120, headers={"Authorization": f"Bearer {token}"})


def _polygon(bbox):
    w, s, e, n = bbox
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def events(kind: str, start: str, end: str, bbox, limit=5000) -> list[dict]:
    """Loitering or gap events that overlap [start, end) (YYYY-MM-DD) inside bbox (w, s, e, n)."""
    out, offset = [], 0
    with _client() as c:
        while offset is not None and len(out) < limit:
            r = c.post("/events", params={"offset": offset, "limit": 200},
                       json={"datasets": [DATASETS[kind]], "startDate": start, "endDate": end, "geometry": _polygon(bbox)})
            r.raise_for_status()
            d = r.json()
            out += d.get("entries", [])
            offset = d.get("nextOffset")
    return out


def dark_detections(start: str, end: str, bbox) -> list[dict]:
    """Sentinel-1 ship detections with no matching AIS ship, as {date, lat, lon, detections}."""
    with _client() as c:
        r = c.post("/4wings/report", params={
            "spatial-resolution": "HIGH", "temporal-resolution": "DAILY", "format": "JSON",
            "datasets[0]": "public-global-sar-presence:latest", "filters[0]": "matched='false'",
            "date-range": f"{start},{end}", "spatial-aggregation": "false"}, json={"geojson": _polygon(bbox)})
        r.raise_for_status()
        rows = []
        for entry in r.json().get("entries", []):
            for v in entry.values():
                rows += v or []
        return rows
