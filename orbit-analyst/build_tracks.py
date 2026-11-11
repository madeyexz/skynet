"""Build the Skagen ship-track replay from one day of Danish Maritime Authority AIS.

Download http://aisdata.ais.dk.s3.eu-central-1.amazonaws.com/aisdk-2026-11-06.zip, then run
`uv run python build_tracks.py aisdk-2026-11-06.zip`. Writes data/tracks_skagen_2026-11-06.json.gz.
"""

import csv
import gzip
import io
import json
import math
import sys
import zipfile
from datetime import datetime, timezone

from agent import ROOT

BBOX = [10.2, 57.45, 11.2, 57.95]  # lon_min, lat_min, lon_max, lat_max
OUT = ROOT / "data" / "tracks_skagen_2026-11-06.json.gz"


def dist_m(a, b):
    dy = (a[1] - b[1]) * 111320
    dx = (a[2] - b[2]) * 111320 * math.cos(math.radians(a[1]))
    return math.hypot(dx, dy)


def main(zip_path: str):
    t0 = datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp()
    ships: dict[str, dict] = {}
    statuses: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        name = z.namelist()[0]
        with z.open(name) as raw:
            rd = csv.reader(io.TextIOWrapper(raw, encoding="utf-8", errors="replace"))
            next(rd)
            for r in rd:
                if r[1] not in ("Class A", "Class B"):
                    continue
                try:
                    lat, lon = float(r[3]), float(r[4])
                except ValueError:
                    continue
                if not (BBOX[1] <= lat <= BBOX[3] and BBOX[0] <= lon <= BBOX[2]):
                    continue
                t = datetime.strptime(r[0], "%d/%m/%Y %H:%M:%S").replace(tzinfo=timezone.utc).timestamp() - t0
                s = ships.setdefault(r[2], {"mmsi": r[2], "name": "", "type": "", "length": "", "dest": "",
                                            "class": r[1], "raw": []})
                for k, i in (("name", 12), ("type", 13), ("length", 16), ("dest", 19)):
                    if r[i] and r[i] not in ("Undefined", "Unknown") and not s[k]:
                        s[k] = r[i].strip()
                st = r[5]
                if st not in statuses:
                    statuses.append(st)
                sog = float(r[7]) if r[7] else -1
                cog = float(r[8]) if r[8] else -1
                s["raw"].append((int(t), lat, lon, sog, cog, statuses.index(st)))

    out = []
    for s in ships.values():
        pts = sorted(s.pop("raw"))
        kept = []
        for p in pts:
            if kept:
                last = kept[-1]
                dt = p[0] - last[0]
                if dt <= 0:
                    continue
                d = dist_m(p, last)
                if d / dt > 26:  # faster than 50 knots: a bad fix
                    continue
                # One fix a minute while moving, one every 10 minutes while still.
                if dt < 60 or (d < 40 and dt < 600):
                    continue
            kept.append(p)
        if len(kept) < 2:
            continue
        s["p"] = [[p[0], round(p[1], 5), round(p[2], 5), round(p[3], 1), round(p[4]), p[5]] for p in kept]
        out.append(s)
    data = {"id": "skagen-2026-11-06", "title": "Skagen · 6 Nov 2026", "kind": "replay", "t0": t0,
            "bbox": BBOX, "statuses": statuses,
            "source": "Danish Maritime Authority AIS (aisdata.ais.dk), aisdk-2026-11-06",
            "ships": out}
    OUT.parent.mkdir(exist_ok=True)
    with gzip.open(OUT, "wt") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"{len(out)} ships, {sum(len(s['p']) for s in out)} points -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
