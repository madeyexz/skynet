# Orbit Analyst

Ask any place on Earth. You ask in plain words. The agent picks radar (Sentinel-1) or colour (Sentinel-2), reads real Copernicus images from before and after through Microsoft Planetary Computer, measures the change, and answers with numbers.

```sh
uv run uvicorn app:app            # web app
uv run python demos.py [id]       # rebuild the cached demo answers in cache/
```

- `agent.py`: planning (place, dates, task), data access, and the flood, vessel and built-change analyses.
- `demos.py`: the three cached demo questions (Bang Ban flood, Skagen dark vessels, Chancay port).
- `data/ais_skagen_2026-11-06.csv.gz`: Danish Maritime Authority AIS (aisdata.ais.dk), cut to 10.2–11.2 E, 57.45–57.95 N and 30 minutes around the two Sentinel-1 passes on 6 Nov 2026. AIS matching works only there.
- The "Get a sharper image" panel shows SkyFi list prices ($450 / $675 per SAR scene, $8–22.50 per km² optical). It is a quote only and places no order.

## Ship tracker (`/track`)

- **Replay · Skagen 6 Nov**: every Class A/B AIS ship in 10.2–11.2 E, 57.45–57.95 N for the whole day (`data/tracks_skagen_2026-11-06.json.gz`, built by `build_tracks.py` from the DMA zip). Both Sentinel-1 passes (05:39 and 16:53 UTC) sit on the timeline with their radar targets and image (`uv run python demos.py track-passes`).
- **Live · Gulf of Finland**: real-time AIS from Fintraffic Digitraffic (no key), polled every 15 s, kept for 3 h in `cache/live_tracks.json`.
- **Simulated live**: the Skagen day replayed on today's clock, for when the live feed is down.
- Draw a rectangle or polygon zone. The tracker lists AIS switch-on/off inside it, AIS gaps over 30 min, ships stopped 90 min without reporting anchored, ships alongside (within 200 m for 30 min), and enter/exit. "Scan the zone with radar" runs the vessel detector only inside the zone, on the Sentinel-1 pass nearest the scrubber time.
- Notes keep a place, a time, a tag and an optional ship, in `cache/notes.json`.
