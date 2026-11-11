# Orbit Analyst · Abundance demo (Libya → Crete)

A demo for the YC W27 RFS "Startups in a World of Abundant Space Access".

- **Real data:** Sentinel-1C/1D radar and Sentinel-2 from the Microsoft Planetary Computer, covering 60 days over eastern Libya and the Crete approaches. The pipeline measures revisit and processing latency. It also runs a VV+VH local-contrast ship detector over Tobruk port, on every pass, and over a 33 × 270 km strip of open sea, on the 4 latest passes.
- **Model:** an orbital access simulator propagates constellations of 2 to 10,000 satellites for 7 days. The same code, run with the real Sentinel-1 orbits, predicts 3.0 looks per week; the real record shows 2.2–3.4. The page turns pass counts into "% of crossings detected" for each sensor, boat, budget and price.

## Run

```sh
cd orbit-abundance
uv venv .venv && uv pip install --python .venv/bin/python pystac-client planetary-computer rasterio numpy scipy shapely pyproj pillow pyshp
mkdir -p data_cache && curl -L -o data_cache/ne_10m_land.zip https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip && (cd data_cache && unzip -o ne_10m_land.zip)
.venv/bin/python pipeline/real_data.py   # ~20 min, writes web/data/real.json + PNGs
.venv/bin/python pipeline/simulate.py    # ~2 min, writes web/data/sim.json
python3 -m http.server 8000 --directory web
```

`web/data/` already holds the outputs of a run on 11 Nov 2026, so the page works without re-running the pipeline.

## Limits

- Detections are bright objects on water. They are not checked against AIS, and length is ±20 m.
- Ships moored inside 150 m of the shore are excluded.
- The detection model is calibrated to Sentinel-1 recall vs length (Paolo et al., *Nature* 2024). Hull factors and optical scene sizes are assumptions; the page lists them.
