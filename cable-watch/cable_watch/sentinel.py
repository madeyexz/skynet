"""Sentinel-1 passes from the Copernicus Data Space catalogue (no account needed for search)."""

from datetime import datetime, timedelta, timezone

import httpx

CATALOGUE = "https://catalogue.dataspace.copernicus.eu/odata/v1/Products"
_cache: dict = {}


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def passes(lon: float, lat: float, start: float, end: float) -> list[dict]:
    """Sentinel-1 GRD scenes that cover a point between two epoch-second times, one entry per pass."""
    key = (round(lon, 2), round(lat, 2), int(start) // 600, int(end) // 600)
    if key in _cache:
        return _cache[key]
    f = (f"Collection/Name eq 'SENTINEL-1' and contains(Name,'GRD') and "
         f"OData.CSC.Intersects(area=geography'SRID=4326;POINT({lon:.4f} {lat:.4f})') and "
         f"ContentDate/Start gt {_iso(start)} and ContentDate/Start lt {_iso(end)}")
    r = httpx.get(CATALOGUE, params={"$filter": f, "$top": 100, "$orderby": "ContentDate/Start asc"}, timeout=40)
    r.raise_for_status()
    out, seen = [], set()
    for p in r.json().get("value", []):
        t = datetime.fromisoformat(p["ContentDate"]["Start"].replace("Z", "+00:00"))
        sat = p["Name"][:3]
        k = (sat, t.replace(second=0, microsecond=0) - timedelta(minutes=t.minute % 5))
        if k in seen:  # the catalogue lists each slice twice (COG and SAFE) and a pass in several slices
            continue
        seen.add(k)
        out.append({"time": t.strftime("%Y-%m-%d %H:%M UTC"), "epoch": int(t.timestamp()), "satellite": sat.replace("S1", "Sentinel-1"),
                    "product": p["Name"].removesuffix(".SAFE"),
                    "browser": f"https://browser.dataspace.copernicus.eu/?zoom=11&lat={lat:.4f}&lng={lon:.4f}"
                               f"&datasetId=S1_AWS_IW_VVVH&fromTime={t.strftime('%Y-%m-%d')}T00%3A00%3A00.000Z"
                               f"&toTime={t.strftime('%Y-%m-%d')}T23%3A59%3A59.999Z"})
    _cache[key] = out
    return out
