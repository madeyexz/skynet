"""Cable and land geometry in a metric projection, with fast nearest-cable lookups."""

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import shapely
from pyproj import Transformer
from shapely.geometry import shape

DATA = Path(__file__).resolve().parent.parent / "data"
_to_m = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)  # ETRS89 LAEA Europe, metres


def project(lon, lat):
    x, y = _to_m.transform(np.asarray(lon, float), np.asarray(lat, float))
    return np.asarray(x), np.asarray(y)


def suffix(region: str) -> str:
    return "" if region == "baltic" else f"-{region}"


class Geo:
    def __init__(self, region="baltic"):
        fc = json.loads((DATA / f"cables{suffix(region)}.geojson").read_text())
        self.cables = [f["properties"] for f in fc["features"]]
        parts, owner = [], []
        for i, f in enumerate(fc["features"]):
            g = shapely.transform(shape(f["geometry"]), lambda c: np.column_stack(project(c[:, 0], c[:, 1])))
            for p in shapely.get_parts(g):
                parts.append(p)
                owner.append(i)
        self.parts = np.array(parts, dtype=object)
        self.owner = np.array(owner)
        self.tree = shapely.STRtree(self.parts)
        land = json.loads((DATA / f"land{suffix(region)}.geojson").read_text())
        lp = []
        for f in land["features"]:
            g = shapely.transform(shape(f["geometry"]), lambda c: np.column_stack(project(c[:, 0], c[:, 1])))
            lp.extend(shapely.get_parts(g))
        self.land = shapely.STRtree(lp)

    def nearest_cable(self, x, y, max_m=20_000):
        """Distance in metres to the nearest cable (inf beyond max_m) and that cable's index (-1 if none)."""
        pts = shapely.points(x, y)
        d = np.full(len(pts), np.inf)
        cid = np.full(len(pts), -1)
        if len(pts):
            (pi, gi), dist = self.tree.query_nearest(pts, max_distance=max_m, return_distance=True, all_matches=False)
            d[pi] = dist
            cid[pi] = self.owner[gi]
        return d, cid

    def near_land(self, x, y, within_m=1_500):
        pts = shapely.points(x, y)
        out = np.zeros(len(pts), bool)
        if len(pts):
            pi, _ = self.land.query(pts, predicate="dwithin", distance=within_m)
            out[pi] = True
        return out

    def segment_crosses(self, x0, y0, x1, y1):
        """Index of the first cable that the straight line between two fixes crosses, or -1."""
        hits = self.tree.query(shapely.linestrings([[x0, y0], [x1, y1]]), predicate="intersects")
        return int(self.owner[hits[0]]) if len(hits) else -1


class Others:
    """Every fix of every ship, sorted by time, to count who else was transmitting near a place."""

    def __init__(self, t, lon, lat, mmsi, sog):
        o = np.argsort(t, kind="stable")
        self.t, self.m, self.sog = np.asarray(t)[o], np.asarray(mmsi)[o], np.asarray(sog, float)[o]
        self.x, self.y = project(np.asarray(lon)[o], np.asarray(lat)[o])

    def __call__(self, lon, lat, start, end, mmsi, radius_m=10_000, stopped=False):
        a, b = np.searchsorted(self.t, [start + 60, end])
        px, py = project([lon], [lat])
        near = np.hypot(self.x[a:b] - px[0], self.y[a:b] - py[0]) < radius_m
        if stopped:
            near &= self.sog[a:b] < 0.5
        return len(set(self.m[a:b][near].tolist()) - {mmsi})


@lru_cache(maxsize=4)
def geo(region="baltic") -> Geo:
    return Geo(region)
