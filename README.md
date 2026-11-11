> Built at the c0mpiled-18 hackathon on 2026-11-11 (theme: Multiplayer AI).

# Skynet

**Air traffic control and a black box, for the sea.**

Skynet catches ships that lie about where they are. It compares what a ship **claims** over AIS with what satellites **see** (radar) and **hear** (the ship's own radio and radar emissions). It then turns each mismatch into an **alert** for whoever has to act now and an **evidence record** for whoever has to prove it later.

| | |
|---|---|
| ▶ **Live app** | **https://skynet--hackathon-yc.onamp.dev/** (open it, no sign-in) |
| 🎬 **90-second demo** | https://skynet--hackathon-yc.onamp.dev/demo.html?auto=1 |
| 📄 **Data API docs** | https://skynet--hackathon-yc.onamp.dev/docs.html (demo key `sk_live_skynet_demo`) |

## The problem

Every large ship broadcasts its position over AIS. More and more, that signal is switched off, jammed or faked:

- **Undersea cables and pipelines:** ships drag anchors across them with AIS off. Examples are Estlink 2 and the Eagle S in Dec 2024, and the C-Lion1 and BCS East-West cuts in Nov 2024.
- **Sanctions:** shadow-fleet tankers go dark, spoof positions, change names and flags, and meet ship-to-ship.
- **Insurance:** war-risk premiums at Hormuz run at several percent of hull value per transit. Underwriters and claims teams need to know where a ship really was.

Today this takes analysts staring at maps. Skynet is built on the opposite principle: *the product finds you; you don't watch it.*

## How it works

One verified vessel record per ship, fused from three layers:

| Layer | What it is | Map symbol |
|---|---|---|
| **Claimed** | AIS position reports, as the ship broadcasts them | 🔵 blue dot (verified) |
| **Observed** | satellite radar (SAR) detections of the hull | 🔶 orange diamond (dark: radar sees it, AIS silent) |
| **Emitted** | RF detections of the ship's own navigation radar and radios | dashed ring (false position: AIS says one place, radar another) |

A fusion engine turns mismatches into events: dark periods, spoofing and position jumps, identity changes, ship-to-ship meetings, loitering, slow speed, and the **anchor-drag pattern** (low speed, erratic heading, track crossing a cable). Each vessel gets a 0–100 risk score. Every point in the score comes with a plain-English reason and its source.

The same record powers the three features:

1. **Detect:** a full-screen map of the three layers, with mismatches highlighted. Click any ship for its risk and reasons.
2. **Alert:** alerts slide in only when a decision is needed: one headline, 3–4 facts, a mini track, and actions such as "Dispatch nearest patrol". Every action is logged to the record. **Watch zones** decide what becomes an alert. You draw an area or corridor, pick a purpose (cable protection, sanctions, insurance or custom), adjust the rules, and choose who gets alerted.
3. **Report:** rewind any ship with a time scrubber, then generate an A4 **evidence record** with a timeline, exhibits and a SHA-256 fingerprint of the underlying events, and export it as a PDF. There are templates for cable incidents, sanctions due diligence, and insurance voyage history or claims.

Around them sit an **Analyst** ask bar that only answers from Skynet's data and says when the data has nothing, and a **Data API** for underwriting, screening and claims systems.

## Try it in 2 minutes

Open the [live app](https://skynet--hackathon-yc.onamp.dev/). The clock is already playing.

1. **Detect:** D-04 *KAIROS STAR*, a 228 m tanker, shows as an orange diamond over **Cable A**. Its AIS is off, but radar still sees the hull and its navigation radar is still transmitting. Click it to see risk **90/100** and why. S-17 shows AIS claiming a position on land while radar sees it at sea.
2. **Alert** (press `2`): "Dark tanker dragging a likely anchor over Cable A" arrives for the coast guard and the grid operator. Click **Dispatch nearest patrol**, which is logged to the record. Click **See what the recipient sees** for the phone view.
3. **Report** (press `3`): replay D-04 with its dark period highlighted, click **Generate report**, then **Export PDF**.
4. **Ask** in the top bar: *"Should we quote tanker T-11 for a Hormuz transit tomorrow?"* The answer is a risk score and a premium band built from real JMIC incidents, gpsjam, IMF PortWatch and the OFAC list.
5. **Switch use case** in the top bar to see the same pipeline on real data.

`Space` plays or pauses the clock, and `1` `2` `3` switch between Detect, Alert and Report.

## Use cases

| Use case | Data | Real or illustrative |
|---|---|---|
| **Cable protection · Gulf of Finland** (default) | Seeded scenario inspired by the 2024 Estlink 2 damage: 40 ships, a dark tanker dragging an anchor, a spoofer, a sanctioned tanker. Cables from TeleGeography. | Illustrative (labelled in the app) |
| **Cable cuts · western Baltic, Nov 2024** | Danish Maritime Authority AIS for 17–19 Nov 2024 (1,843 ships, 3.5M position reports) scored against the BCS East-West and C-Lion1 cuts. Includes Yi Peng 3 and three sanctioned or shadow-fleet tankers flagged by OpenSanctions. | Real |
| **Dark vessels · Skagen** | Two real Sentinel-1 radar passes on 6 Nov 2026, matched against Danish AIS: 15 radar targets with no AIS. | Real |
| **War risk · Hormuz** | 22 JMIC incidents, the JWC listed area, live gpsjam GNSS-jamming data, IMF PortWatch transits and the OFAC SDN list, for the illustrative tanker T-11. | Real evidence, illustrative ship |

Simulated in this build: the RF layer, push and email delivery (webhooks are real), and authentication (one demo API key). The Analyst uses a rule-based planner over Skynet's own data; set `ANTHROPIC_API_KEY` to switch it to LLM tool calling.

## Data API

```sh
curl -H 'Authorization: Bearer sk_live_skynet_demo' \
  https://skynet--hackathon-yc.onamp.dev/v1/vessels/D-04/record
```

The record response has the verified track, dark periods, events, risk score with reasons, an evidence URL and the fingerprint.

| Endpoint | Purpose |
|---|---|
| `GET /v1/vessels/{id}/record?from&to` | full evidence record |
| `GET /v1/vessels/{id}/risk` | risk score and reasons |
| `GET /v1/zones/{id}/events?since` | events in a watch zone |
| `POST /v1/zones` | create a watch zone (purpose, rules, metrics) |
| `POST /v1/webhooks` | subscribe to `alert.created` |

On the hosted demo, the write endpoints and the in-app Reset and zone editing also need an admin key held by the team. Read access and alert actions are open to everyone.

## Run it locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/madeyexz/skynet.git
cd skynet/skynet
uv sync
uv run uvicorn server.app:app --port 8787
```

Then open http://localhost:8787. Locally there is no admin key, so every action is open. PDF export needs Chrome or Chromium on your `PATH`, or `SKYNET_CHROME` set to its path. Deploy notes (Amp Box, Cloudflare Containers) are in [`skynet/README.md`](skynet/README.md).

## Repository layout

```
skynet/
  server/          FastAPI app
    seed.py          deterministic seeded world (Gulf of Finland)
    fusion.py        claimed vs observed vs emitted → events, dark periods, risk
    zones.py         watch-zone purposes, rules, metrics
    report.py        evidence records and fingerprints
    analyst.py       ask bar
    real/            the real-data use cases (Baltic 2024, Skagen, war risk)
  web/             no-build frontend: MapLibre map, Detect / Alert / Report, demo.html, docs.html
  data/            seeded world, precomputed real cases, cables and land geometry
  scripts/         Amp Box build and start
```

## How we built it

Fittingly for a Multiplayer AI hackathon, Skynet was built by people and agents working together in [Amp](https://ampcode.com). A shared Space coordinated the work. Four exploration demos (satellite Q&A, cable-cut risk scoring, a war-risk pricing copilot, and a satellite-coverage model) were built in parallel threads. Nine subagents then merged them into one product, each owning its own part of the code. Those demos now live on as the real-data use cases above.

## Sources

Danish Maritime Authority AIS · ESA Copernicus Sentinel-1 via Microsoft Planetary Computer · TeleGeography submarine cable map · EMODnet · Natural Earth · OpenSanctions · JMIC / UKMTO advisories · Lloyd's Market Association JWC listed areas · gpsjam.org · IMF PortWatch · US Treasury OFAC SDN list · OpenFreeMap / OpenStreetMap.
