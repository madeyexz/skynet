"""Score every ship in a few days of Danish AIS and save the result as the replay case.

Input: CSVs written by extract_dk.sh. Output: data/replay-<name>.json
Run: uv run python scripts/build_replay.py yi-peng-3 /home/user/data/dk/2024-11-1{7,8,9}.csv
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cable_watch import sentinel  # noqa: E402
from cable_watch.detect import Track, score, tier  # noqa: E402
from cable_watch.geo import Others  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"
COLS = ["ts", "mmsi", "lat", "lon", "nav", "sog", "cog", "imo", "name", "type", "length"]


def load(paths):
    df = pd.concat(pd.read_csv(p, names=COLS, dtype={"imo": str, "name": str, "type": str, "nav": str},
                               on_bad_lines="skip") for p in paths)
    t = pd.to_datetime(df["ts"], format="%d/%m/%Y %H:%M:%S", utc=True)
    df["t"] = (t - pd.Timestamp("1970-01-01", tz="UTC")) // pd.Timedelta(seconds=1)
    return df.sort_values(["mmsi", "t"])


def first(s):
    s = s[s.notna() & (s != "Unknown") & (s != "Undefined")]
    return s.iloc[0] if len(s) else ""


def main(case, paths):
    wl = json.loads((DATA / "watchlist.json").read_text())
    df = load(paths)
    others = Others(df["t"].to_numpy(), df["lon"].to_numpy(float), df["lat"].to_numpy(float), df["mmsi"].to_numpy(),
                    df["sog"].to_numpy(float))
    out, n_total, n_near = [], 0, 0
    for mmsi, g in df.groupby("mmsi", sort=False):
        if mmsi < 100_000_000 or len(g) < 10:
            continue
        n_total += 1
        tr = Track(int(mmsi), g["t"].to_numpy(), g["lon"].to_numpy(float), g["lat"].to_numpy(float),
                   g["sog"].to_numpy(float), g["cog"].to_numpy(float),
                   g["nav"].isin(["At anchor", "Moored"]).to_numpy(),
                   name=str(first(g["name"])), imo=str(first(g["imo"])), ship_type=str(first(g["type"])),
                   length=float(g["length"].max()) if g["length"].notna().any() else float("nan"))
        r = score(tr, wl, others)
        if r.min_cable_m < 20_000:
            n_near += 1
        if r.score == 0:
            continue
        step = max(1, len(g) // 600)
        out.append({"mmsi": r.mmsi, "name": tr.name, "imo": tr.imo, "type": tr.ship_type,
                    "length": None if np.isnan(tr.length) else tr.length, "score": r.score, "tier": tier(r.score),
                    "signals": r.signals, "crossings": r.crossings,
                    "track": [[round(float(a), 5), round(float(b), 5), int(c), None if np.isnan(d) else float(d)]
                              for a, b, c, d in zip(tr.lon[::step], tr.lat[::step], tr.t[::step], tr.sog[::step])]})
    out.sort(key=lambda v: -v["score"])
    # Sentinel-1 passes for each AIS gap of the ships a human would look at.
    for v in out:
        if v["tier"] == "none" or v["score"] < 40:
            continue
        for s in v["signals"]:
            if s["kind"] == "ais_gap":
                s["s1"] = sentinel.passes(s["lon"], s["lat"], s["start"], s["end"])
                time.sleep(0.3)
    summary = {"ships": n_total, "near_cable": n_near,
               "flagged": sum(v["score"] > 0 for v in out),
               "medium": sum(v["tier"] == "medium" for v in out), "high": sum(v["tier"] == "high" for v in out),
               "start": int(df["t"].min()), "end": int(df["t"].max()), "fixes": len(df)}
    (DATA / f"replay-{case}.json").write_text(json.dumps({"summary": summary, "vessels": out}, ensure_ascii=False))
    traffic(case, df, {v["mmsi"] for v in out if v["score"] >= 40})
    print(summary)
    for v in out[:15]:
        print(v["score"], v["mmsi"], v["name"], v["type"], [s["kind"] for s in v["signals"]])


def traffic(case, df, flagged, step=900):
    """Every moving ship's position every 15 minutes, for the animated map: frames of [ship, lon*1e4, lat*1e4]."""
    d = df[(df["sog"] >= 0.5) | df["mmsi"].isin(flagged)].copy()
    d["b"] = d["t"] // step
    last = d.groupby(["b", "mmsi"], sort=True)[["lon", "lat"]].last().reset_index()
    ships = sorted(last["mmsi"].unique().tolist())
    idx = {m: i for i, m in enumerate(ships)}
    b0 = int(last["b"].min())
    frames = [[] for _ in range(int(last["b"].max()) - b0 + 1)]
    for b, m, lon, lat in last.itertuples(index=False):
        frames[int(b) - b0] += [idx[m], round(lon * 1e4), round(lat * 1e4)]
    (DATA / f"replay-{case}-traffic.json").write_text(json.dumps(
        {"t0": b0 * step, "step": step, "mmsi": ships, "frames": frames}, separators=(",", ":")))
    print("traffic frames", len(frames), "points", len(last))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
