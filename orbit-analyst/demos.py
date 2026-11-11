"""The three cached demo questions. Run `uv run python demos.py [id]` to rebuild their cache."""

import json
import sys

from agent import ROOT, Log, Plan, run

CACHE = ROOT / "cache"

DEMOS = {
    "bang-ban-flood": Plan(
        question="Was the farmland at Bang Ban, Ayutthaya, Thailand flooded in September 2026? "
                 "I need numbers for a crop insurance claim.",
        task="flood", place="Bang Ban, Ayutthaya, Thailand", lat=14.4075, lon=100.4686,
        bbox=[100.4036, 14.3446, 100.5336, 14.4704],
        before=("2026-08-10", "2026-08-31"), after=("2026-09-01", "2026-09-30"),
        timeline_until="2026-10-01", res=20.0),
    "skagen-dark-vessels": Plan(
        question="Are there dark vessels off Skagen on 6 Nov 2026? Which ships are not sending AIS?",
        task="vessels", place="Skagen, Denmark", lat=57.7209, lon=10.5839,
        bbox=[10.40, 57.58, 10.98, 57.86],
        before=None, after=("2026-11-06", "2026-11-06"), res=10.0),
    "chancay-port": Plan(
        question="How much has the Port of Chancay, Peru been built out from 2020 to Jan 2026?",
        task="construction", place="Port of Chancay, Chancay, Peru", lat=-11.585, lon=-77.270,
        bbox=[-77.290, -11.605, -77.250, -11.565],
        before=("2020-01-01", "2020-12-31"), after=("2026-01-01", "2026-01-31"), res=10.0),
}


def build(demo_id: str):
    log = Log()
    plan = DEMOS[demo_id]
    log(f"Read the question as a demo question: {plan.place}.")
    out = run(plan, log, demo_id)
    out["id"] = demo_id
    out["steps"] = log.steps
    out["cached"] = True
    CACHE.mkdir(exist_ok=True)
    (CACHE / f"{demo_id}.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    print(json.dumps({k: out[k] for k in ("headline", "answer", "stats", "timeline", "seconds")}, indent=1,
                     ensure_ascii=False))


def build_track_passes():
    """Both Sentinel-1 passes over Skagen on 6 Nov 2026, for the tracker timeline."""
    import copy
    passes = []
    for scene in ("20261106T053940", "20261106T165300"):
        plan = copy.deepcopy(DEMOS["skagen-dark-vessels"])
        plan.notes = [f"pick:{scene}"]
        log = Log()
        out = run(plan, log, f"track-skagen-{scene}")
        passes.append({k: out[k] for k in ("pass_time", "scene", "headline", "answer", "stats", "markers",
                                           "map_overlay", "seconds")})
        print(out["pass_time"], out["headline"])
    (CACHE / "track-skagen-passes.json").write_text(json.dumps(passes, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    for d in sys.argv[1:] or [*DEMOS, "track-passes"]:
        build_track_passes() if d == "track-passes" else build(d)
