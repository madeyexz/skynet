# Skynet

**Air traffic control and a black box, for the sea.**

Ships report their own position over AIS, and more and more often that signal is switched off, jammed or faked.
Skynet compares what a ship **claims** (AIS) with what **space observes** (satellite radar, SAR) and **emits** (RF from the ship's own radar and radios), and turns the mismatch into three things:

1. **Detect**: the map stacks the three space layers. Verified ships are blue dots, dark (radar-only) ships are orange diamonds, and ships with a false position show a dashed ring at the claimed AIS spot, linked to the real position.
2. **Alert**: when something needs a decision, an alert slides in with one headline, 3–4 facts, a mini track and 2–3 actions. Every action is logged to the vessel record. Watch zones (cable, sanctions, insurance, custom) decide what becomes an alert, and who gets it on which channel.
3. **Report**: the same stored record, rewound with a time scrubber and turned into an A4 evidence record with a fingerprint, exported as a PDF.

Every alert and every report comes from one verified vessel record. Around the three features there is an Analyst ask bar, a Data API (`/docs.html`), and a 3-beat demo stage (`/demo.html`).

## Use cases

The earlier hackathon demos are **use cases** that feed the same Detect → Alert → Report pipeline. Pick one in the top bar:

| Use case | Data |
|---|---|
| Cable protection · Gulf of Finland (default) | Seeded and illustrative, inspired by the 2024 Estlink 2 incident. Context cables from TeleGeography. |
| Cable protection · Baltic, Nov 2024 | Real Danish Maritime Authority AIS for 17–19 Nov 2024 (BCS East-West and C-Lion1 cuts), scored with the Cable Watch rules |
| Dark vessels · Skagen | Real Sentinel-1 radar detections matched against DMA AIS, 6 Nov 2026 (from Orbit Analyst) |
| War risk · Hormuz | Real JMIC incidents, the JWC listed area, gpsjam, IMF PortWatch and OFAC SDN (from the war-risk copilot), with the illustrative tanker T-11 |

### Use-case data

Everything the use cases need lives in `data/`; the source demos are no longer in this repo.

- `data/geo/`: `cables.geojson` (TeleGeography submarine cables plus EMODnet pipelines such as Balticconnector) and `land.geojson` (Natural Earth 10 m land), both built by Cable Watch for the Baltic. The seed uses the cables; fusion uses the land mask.
- `data/real/`: the precomputed Baltic 2024 and Skagen worlds (`baltic-2024.json`, `dark-skagen.json`), the Skagen radar images (`media/`) and the war-risk evidence (`warrisk_evidence.json`). War risk is built at runtime from the evidence file and the live PortWatch, gpsjam and OFAC SDN feeds (cached in `data/cache/`).

Serving only reads these files. To rebuild the Baltic and Skagen worlds you need the original source extracts (Cable Watch's `replay-yi-peng-3.json`; Orbit Analyst's Skagen passes, AIS tracks and radar overlays) in their original layout under `SKYNET_SOURCE_DIR` (default `data/source/`, not committed). A checkout of this repo from before the source demos were removed works:

```sh
SKYNET_SOURCE_DIR=/path/to/old-checkout uv run python -m server.real.cases build   # or: build baltic-2024
```

The full list of files is in the `server/real/cases.py` docstring.

## Run

```sh
cd skynet
uv sync
uv run python -m server.seed          # writes data/world.json (deterministic, seed 7)
uv run uvicorn server.app:app --port 8787
```

Open http://localhost:8787. In an Amp orb it runs as the service `skynet`:
`amp orb service start skynet --command 'cd /home/user/workspace/repo/skynet && uv run uvicorn server.app:app --host 0.0.0.0 --port 8787' --port 8787 --portal`.

Keys: `Space` plays or pauses the clock. `1` `2` `3` switch Detect, Alert and Report. `/demo.html?auto=1` plays the three demo beats back to back for recording.

## Reset the demo

Click **Reset** in the top bar, or run `curl -X POST localhost:8787/api/reset`. This reseeds the world and clears operator actions, reports and user zones; the seeded Cable A corridor stays. `rm data/state.json` does the same while the server is stopped.

## Deploy (Cloudflare Workers + Containers)

The hosted demo is a Worker (`worker/index.js`) in front of one container that runs this FastAPI app and Chromium for PDFs (`Dockerfile`). It needs Docker running locally, a Workers Paid plan, and an API token with Workers Scripts: Edit, Containers: Edit and Account Settings: Read.

```sh
npm install
npx wrangler deploy                                       # builds the image, pushes it, deploys the Worker
openssl rand -hex 24 | npx wrangler secret put SKYNET_ADMIN_KEY
```

When `SKYNET_ADMIN_KEY` is set, `/api/reset`, zone create/edit/delete, `POST /v1/zones` and `POST /v1/webhooks` need the header `x-admin-key`. The app asks for the key once in the browser and keeps it in localStorage. Webhook URLs must be public http(s) hosts; private, loopback and link-local addresses are refused. State lives in the one container and resets when it sleeps (after 2 h idle).

## Data API

```sh
curl -H 'Authorization: Bearer sk_live_skynet_demo' localhost:8787/v1/vessels/D-04/record
```

The endpoints are `GET /v1/vessels/{id}/record?from&to`, `GET /v1/vessels/{id}/risk`, `GET /v1/zones/{id}/events?since`, `POST /v1/zones` and `POST /v1/webhooks` (for `alert.created`). The docs are at `/docs.html`.

## Where to plug in real data

| Layer | Now | Plug in |
|---|---|---|
| Claimed (AIS), live | Not wired in this build (cut for time) | AISStream.io websocket or Fintraffic Digitraffic (keyless, `meri.digitraffic.fi`) into `vessel.claimed` as `[t, lon, lat, sog, cog]` |
| Claimed (AIS), history | DMA AIS extracts (Baltic 2024, Skagen) | Any AIS archive into `vessel.claimed` |
| Observed (SAR) | Seeded in the default case; real Sentinel-1 detections in the Skagen case | Global Fishing Watch SAR presence API (needs `GFW_API_TOKEN`) into `vessel.observed` |
| Emitted (RF) | Simulated | An RF geolocation provider's detections into `vessel.emitted` |
| Fusion and risk | `server/fusion.py` (pure, deterministic) | Runs unchanged on real layers |
| Delivery channels | Simulated push and email; real outbound webhooks | A push or email provider in `server/app.py` deliveries |

## What is simulated

- The default Gulf of Finland scenario: every vessel name, time and figure is illustrative.
- The RF layer, the alert delivery channels (except webhooks), and authentication (one demo API key).
- The Analyst uses a rule-based planner over Skynet's own data (no LLM key in this environment). It answers the spec's example questions and says so when the data has nothing; set `ANTHROPIC_API_KEY` to use tool-calling with an LLM.
- The real use cases use the real numbers from their sources, and the UI labels them "Real data".
