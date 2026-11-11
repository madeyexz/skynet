"""Build the static layers Cable Watch needs, from public sources.

Writes into cable-watch/data/:
  cables.geojson    TeleGeography telecom cables + EMODnet pipelines in the Baltic and Danish straits
  land.geojson      Natural Earth 10 m land, clipped to the same area (used to skip harbour traffic)
  watchlist.json    OpenSanctions maritime vessels tagged sanction / shadow fleet / detained, by IMO and MMSI

  cables-taiwan.geojson, land-taiwan.geojson   the same for the Taiwan Strait (CW_REGION=taiwan; no pipelines)

Run: uv run python scripts/build_data.py            (Baltic)
     CW_REGION=taiwan uv run python scripts/build_data.py cables land
"""

import csv
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import httpx
from shapely.geometry import box, mapping, shape

DATA = Path(__file__).resolve().parent.parent / "data"
REGION = os.environ.get("CW_REGION", "baltic")
AREA = {"baltic": box(3.0, 53.0, 31.5, 66.5),     # mouth of the Skagerrak to the Gulf of Finland and Bothnia
        "taiwan": box(116.5, 20.5, 123.8, 27.5)}[REGION]  # Taiwan Strait, Matsu and Kinmen to east of Taiwan
SUFFIX = "" if REGION == "baltic" else f"-{REGION}"

client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": "cable-watch-hackathon"})


def cables():
    out = []
    geo = client.get("https://www.submarinecablemap.com/api/v3/cable/cable-geo.json").json()
    for f in geo["features"]:
        g = shape(f["geometry"]).intersection(AREA)
        if g.is_empty:
            continue
        p = f["properties"]
        out.append({"type": "Feature", "geometry": mapping(g),
                    "properties": {"id": p["id"], "name": p["name"], "kind": "telecom", "source": "TeleGeography"}})
    if REGION != "baltic":  # EMODnet covers European waters only
        pipes = {"features": []}
    else:
        pipes = client.get("https://ows.emodnet-humanactivities.eu/wfs", params={
            "service": "WFS", "version": "2.0.0", "request": "GetFeature", "typeNames": "emodnet:pipelines",
            "outputFormat": "application/json", "bbox": "53,3,66.5,31.5,urn:ogc:def:crs:EPSG::4326"}).json()
    for i, f in enumerate(pipes["features"]):
        g = shape(f["geometry"])
        # EMODnet serves this layer as lat/lon pairs; swap when the first coordinate looks like a latitude.
        if g.bounds[0] > 40:
            from shapely.ops import transform
            g = transform(lambda x, y, z=None: (y, x), g)
        g = g.intersection(AREA)
        p = f["properties"]
        # Unnamed entries are mostly short coastal pipes, and the North Sea oil-field lines are out of scope;
        # keep the named Baltic trunk lines (Balticconnector, Nord Stream).
        if g.is_empty or not p.get("name") or g.centroid.x < 9.5:
            continue
        out.append({"type": "Feature", "geometry": mapping(g.simplify(0.0003)),
                    "properties": {"id": f"pipe-{i}", "name": p["name"].title(), "kind": "pipeline",
                                   "source": "EMODnet Human Activities"}})
    (DATA / f"cables{SUFFIX}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": out}))
    print("cables", len(out))


def land():
    z = client.get("https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip").content
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        zipfile.ZipFile(io.BytesIO(z)).extractall(d)
        import shapefile  # pyshp, installed with the prep group
        r = shapefile.Reader(str(Path(d) / "ne_10m_land.shp"))
        feats = []
        whole = [shape(s.__geo_interface__).intersection(AREA) for s in r.shapes()]
        whole = [g.simplify(0.002) for g in whole if not g.is_empty]
        # Cut into 0.25° tiles: distance checks against a few huge coastline polygons are very slow.
        for x in range(int(AREA.bounds[0] * 4), int(AREA.bounds[2] * 4) + 1):
            for y in range(int(AREA.bounds[1] * 4), int(AREA.bounds[3] * 4) + 1):
                cell = box(x / 4, y / 4, (x + 1) / 4, (y + 1) / 4)
                for g in whole:
                    if g.intersects(cell):
                        piece = g.intersection(cell)
                        if not piece.is_empty and piece.area > 0:
                            feats.append({"type": "Feature", "geometry": mapping(piece), "properties": {}})
    (DATA / f"land{SUFFIX}.geojson").write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    print("land", len(feats))


def watchlist():
    text = client.get("https://data.opensanctions.org/datasets/latest/maritime/maritime.csv").text
    keep = ("sanction", "mare.shadow", "mare.detained")
    by_imo, by_mmsi = {}, {}
    for row in csv.DictReader(io.StringIO(text)):
        if row["type"] != "VESSEL":
            continue
        tags = sorted({t for t in row["risk"].split(";") if t in keep})
        if not tags:
            continue
        for key, table in ((row["imo"].removeprefix("IMO"), by_imo), (row["mmsi"], by_mmsi)):
            for k in filter(None, key.split(";")):
                e = table.setdefault(k, {"name": row["caption"], "tags": [], "datasets": []})
                e["tags"] = sorted(set(e["tags"]) | set(tags))
                e["datasets"] = sorted(set(e["datasets"]) | set(row["datasets"].split(";")))
    (DATA / "watchlist.json").write_text(json.dumps({"imo": by_imo, "mmsi": by_mmsi}))
    print("watchlist imo", len(by_imo), "mmsi", len(by_mmsi))


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    for step in sys.argv[1:] or ["cables", "land", "watchlist"]:
        globals()[step]()
