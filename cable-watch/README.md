# Cable Watch 海纜守望

Flags ships that behave like the ships in past undersea-cable incidents: stopping, loitering, zigzagging, slowing down or switching AIS off over a cable. Each flag carries a plain-language reason and the Sentinel-1 radar passes for that time and place.

- **Replay**: 17–19 Nov 2024 in the western Baltic (BCS East-West and C-Lion1 cuts), from Danish Maritime Authority AIS.
- **Taiwan Strait**: three sourced incidents (Hong Tai 58 / TP3, Shunxin-39 / TPE, Matsu 2023), plus Global Fishing Watch loitering events of non-fishing ships scored against Taiwan's cables, and Sentinel-1 ships with no AIS (needs `GFW_API_TOKEN`).
- **Live**: Gulf of Finland and the northern Baltic, from Fintraffic Digitraffic open AIS, polled every minute. History builds up while the server runs.

## Run

```sh
uv sync --all-groups
uv run python scripts/build_data.py               # cables.geojson, land.geojson, watchlist.json
# replay (needs ~1.7 GB of Danish AIS zips from http://aisdata.ais.dk/2024/)
scripts/extract_dk.sh aisdk-2024-11-17.zip /tmp/dk/2024-11-17.csv   # and 18, 19
uv run python scripts/build_replay.py yi-peng-3 /tmp/dk/2024-11-1{7,8,9}.csv
CW_REGION=taiwan uv run python scripts/build_data.py cables land
uv run python scripts/build_taiwan.py 2026-09-01 2026-11-06   # needs GFW_API_TOKEN
uv run uvicorn cable_watch.app:app --port 8000
```

## Rules (`cable_watch/detect.py`)

| Signal | Points | Based on |
|---|---|---|
| Stopped or at anchor within 1 km of a cable ≥ 30 min | 30–40 (10 if ≥ 2 other ships are parked within 5 km: an anchorage) | Hong Tai 58, TP3, Feb 2025 |
| 0.5–5 kn within 1 km of a cable ≥ 20 min | 25–35 | Yi Peng 3, Nov 2024 |
| ≥ 3 course reversals within 2 km of a cable, offshore | 20 | Hong Tai 58 zigzag |
| Speed below 60 % of the ship's median for ≥ 30 min near a cable | 25 | Eagle S anchor drag, Dec 2024 |
| AIS gap > 45 min that crosses or ends near a cable | 25–40 (10 when no other ship nearby was heard either) | Yi Peng 3 AIS off 7.5 h |
| Sanctioned, shadow-fleet or detained ship (OpenSanctions) | 20 | Eagle S |

Each kind counts once (its highest instance); the total is capped at 100.
| (Taiwan) Flag of Togo, Cameroon or Tanzania | 15 | Hong Tai 58, Shunxin-39 / Xingshun 39 |
