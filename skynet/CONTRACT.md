# Skynet build contract (internal, for the people building it)

Skynet: "Air traffic control and a black box, for the sea."
It compares what a ship CLAIMS (AIS) with what SPACE OBSERVES (satellite radar, RF) and turns the
mismatch into ALERTS (Live) and REPORTS (Rewind). Every alert and report comes from one vessel record.

Product name is **Skynet** everywhere (UI, reports, README, API docs, service names). Never "Truewake".

## Product structure (OVERRIDES anything below that conflicts)

Skynet is built around **three features**, which are the three modes in the top nav:

1. **Detect**: the full-screen map. It stacks the three space layers (Claimed = AIS, Observed = SAR radar,
   Emitted = RF) and shows each vessel's fused state: verified (blue dot), dark/radar-only (orange
   diamond), or false position (dashed ring at the claimed AIS spot, linked by a dashed line to the real
   observed position). Mismatches pulse. Clicking a vessel opens a small vessel card with its risk score,
   reasons and sources, plus "Open in Report" and "Watch this vessel".
2. **Alert**: the alert feed. It slides in only when something needs a decision: a headline, 3–4 facts,
   a mini track map and 2–3 actions that are logged to the record. It also has the recipient phone
   view (390x844), a simulated delivery log, and **Watch zones**. Watch zones are the configuration
   that decides what becomes an alert: purpose presets, rules, metrics and recipients.
3. **Report**: Rewind replay with a time scrubber and a per-vessel event timeline, then the A4 evidence
   record with fingerprint and PDF export (cable / sanctions / insurance templates).

Supporting pieces: the Analyst ask bar (top bar, every mode), the Data API with docs (`/docs.html`), and
the Demo stage (`/demo.html`), whose 3 beats are literally Detect → Alert → Report (Prove).

**Use cases are not separate products.** The earlier demos are use cases that feed the same
Detect → Alert → Report pipeline. The user picks one with a use-case switcher in the top bar
(`GET /api/cases`, `GET /api/world?case=<id>`):

| case id | Use case | Data | Owner |
|---|---|---|---|
| `cable` (default) | Cable protection, Gulf of Finland, seeded scenario inspired by Estlink 2 (illustrative) | seeded | A |
| `baltic-2024` | Cable protection on real data: western Baltic 17–19 Nov 2024 (Yi Peng 3, BCS East-West and C-Lion1 cuts) | Danish Maritime Authority AIS + cable-watch scoring | C |
| `dark-skagen` | Sanctions / dark vessels: Sentinel-1 radar vs AIS off Skagen, 6 Nov 2026 | orbit-analyst (real SAR detections matched to DMA AIS) | C |
| `war-risk` | Insurance / war risk: Hormuz transit for tanker T-11 | war-risk-copilot (JMIC incidents, JWC listed area, gpsjam, PortWatch, OFAC SDN) | C |
| `taiwan` | Cable protection, Taiwan Strait | cable-watch GFW loitering events + SAR detections with no AIS, 27 cables | C |
| `migration` | Border / search-and-rescue: Libya→Crete corridor | orbit-abundance real Sentinel-1 strip detections, revisit and latency numbers | C |

Every case returns a full World in the same schema (assets, zones, vessels, events, alerts). Real cases set
`scenario.illustrative=false`, `scenario.sources=[{label,url}]`, and may set
`scenario.facts=[{label, value, source}]`, a few sourced numbers shown as one quiet line, not as a KPI
grid. Real cases may leave a layer empty (for example, no RF, or no tracks for some vessels), and the UI
must still work. A case may also set `scenario.time_mode: "static"` when it has a single snapshot time.

`ctx.mode` is `'detect' | 'alert' | 'report'` (replaces live/rewind/zones). Mount points:
`#topbar` (lead), `#detect` (layer legend, vessel card; D), `#alert` (feed, phone, deliveries; F),
`#zones` (zone toolbar, wizard, side sheet; visible in Alert mode, openable from Detect via bus `open-zones`; G),
`#report` (scrubber, timeline, report launcher; E), `#analyst-panel` (I), `#toast` (lead).
Bus additions: `case` ({id}) after the world for a new case loads; `open-zones` ({vessel_id?}); `open-report` as before.
Map layer id prefixes: `base-*` / `det-*` (D), `al-*` (F), `zn-*` (G), `rw-*` (E), `an-*` (I).

**Owners (these replace the owner notes in the layout below):**
- A: server/seed.py, server/fusion.py, server/zones.py, data/world.json
- B: server/app.py, server/store.py, server/report.py. app.py includes `server.real.routes.router` (C) and
  `server.analyst.router` (I) inside try/except. `/api/world?case=X` for X != `cable` returns
  `server.real.cases.get_world(X)`. Actions, zones and reports work per case: state is keyed by case id.
- C: server/real/__init__.py, server/real/cases.py, server/real/live_ais.py, server/real/warrisk.py, server/real/routes.py, data/real/*
- I: server/analyst.py (defines `router` with POST /api/analyst), web/js/analyst.js, web/css/analyst.css, web/docs.html, web/css/docs.css
- D: web/js/map.js (`export function initMap(ctx)`: base layers, assets, vessels by state), web/js/detect.js, web/css/detect.css
- F: web/js/alert.js, web/css/alert.css
- G: web/js/zones.js, web/css/zones.css
- E: web/js/report-mode.js, web/css/report-mode.css, web/report.html, web/js/report.js, web/css/report.css
- H: web/demo.html, web/js/demo.js, web/css/demo.css
- lead: web/index.html, web/css/base.css, web/js/app.js, web/js/api.js, README.md, CONTRACT.md

app.js creates the MapLibre map (OpenFreeMap positron style `https://tiles.openfreemap.org/styles/positron`),
waits for `load`, calls `initMap(ctx)` from map.js, then calls `init(ctx)` of detect.js, alert.js,
zones.js, report-mode.js and analyst.js, each in its own try/catch. Each module shows and hides its own
mount point when the bus `mode` event fires. index.html loads MapLibre GL 4.7.1 and Lucide 0.460.0 from unpkg as globals
(`maplibregl`, `lucide`), and links every module's CSS file.

## Layout

```
skynet/
  pyproject.toml            uv project (fastapi, uvicorn, httpx, numpy, shapely, pyproj, h3)
  server/                   Python package, run as: uv run uvicorn server.app:app --port $PORT (cwd skynet/)
    app.py                  FastAPI app: static web/, /api/*, /v1/*            (owner: B)
    store.py                in-memory state + JSON persistence + reset        (owner: B)
    report.py               report assembly, fingerprint, PDF via headless Chrome (owner: B)
    seed.py                 deterministic synthetic world -> data/world.json  (owner: A)
    fusion.py               fusion engine: events, dark periods, risk scores  (owner: A)
    zones.py                purpose presets, zone rule evaluation, metrics    (owner: A)
    analyst.py              /api/analyst: tool-calling analyst                (owner: C)
    real/                   real-data adapters from the four demos            (owner: C)
      __init__.py
      routes.py             APIRouter: /api/live/ais, /api/cases, /api/world?case= support helpers
      warrisk.py            war-risk assess() ported from war-risk-copilot (JMIC, PortWatch, gpsjam, OFAC)
      cases.py              Baltic Nov 2024 replay (cable-watch), Skagen SAR vs AIS (orbit-analyst),
                            Taiwan GFW (cable-watch), Libya-Crete coverage numbers (orbit-abundance)
      live_ais.py           Fintraffic Digitraffic poller (real, keyless)
  data/                     world.json (generated by seed), state.json (runtime, gitignored), real/ copies
  web/                      static frontend, no build step, ES modules
    index.html              shell: top bar, #map, overlay mount points       (owner: lead)
    css/base.css            design tokens + shared components                (owner: lead)
    js/app.js               boot, mode router, bus, clock, world loading     (owner: lead)
    js/api.js               fetch helpers                                     (owner: lead)
    js/map.js               MapLibre map + base layers + vessel layers       (owner: D, lead wrote the start)
    js/live.js css/live.css                       Live mode, alert feed, phone view   (owner: D)
    js/rewind.js css/rewind.css                   Rewind mode, scrubber, timeline     (owner: E)
    report.html js/report.js css/report.css       A4 evidence record                  (owner: E)
    js/zones.js css/zones.css                     zone drawing, wizard, side sheet    (owner: F)
    js/analyst.js css/analyst.css                 ask bar + answer card               (owner: G)
    docs.html css/docs.css                        Data API docs page                  (owner: G)
    demo.html js/demo.js css/demo.css             3-beat Estlink 2 stage              (owner: H)
  README.md                                                                   (owner: lead)
```

Only edit files you own. If you need something from another owner's file, code against this
contract and note the need in your final message. Shared CSS tokens live in `css/base.css`; add
your own styles in your own CSS file, scoped under your mount point's id/class prefix.

The dev server runs as the orb service `skynet` on port **8787** (`http://localhost:8787`).
The lead starts it; it auto-reloads on Python changes (`--reload`). Do not start another server on 8787.
To test your own backend code without the service, run it on another port (8790+ ) or call functions directly.

## Time

All times are integer epoch seconds (UTC). The seeded scenario covers
**2026-11-11 00:00 to 08:00 UTC** (`start`=1794355200, `end`=1794384000); `now` = 07:30 UTC
(1794382200). Live mode plays the clock from `now - 50 min` forward at 30x so alerts arrive within
the first minute or two of watching; the clock stops at `end`.

## World (`GET /api/world`, optional `?case=<id>` for real cases)

```jsonc
{
  "scenario": {"id": "gof-seeded", "label": "Gulf of Finland · seeded scenario", "start": 1794355200,
               "end": 1794384000, "now": 1794382200, "seed": 7, "illustrative": true,
               "bbox": [22.5, 59.2, 28.5, 60.6]},          // [minLon, minLat, maxLon, maxLat] for the map
  "assets": [{
    "id": "cable-a", "alias": "Cable A", "name": "Estlink 2 (approx. route)", "kind": "power|telecom|gas",
    "geometry": {"type": "LineString", "coordinates": [[lon, lat], ...]},   // or MultiLineString
    "owner": "Grid operator (illustrative)", "source": "approximate | TeleGeography via cable-watch",
    "protected": true                                        // the one the seeded corridor zone protects
  }],
  "zones": [Zone],                                           // see Zones
  "vessels": [{
    "id": "D-04",                       // short display code; prefixes: D dark, S spoofing, T tanker, V other
    "mmsi": 538009876, "imo": "9329760", "name": "KAIROS STAR", "flag": "Cook Islands", "type": "tanker",
    "length_m": 228, "illustrative": true,
    "track":    [[t, lon, lat, sog, cog], ...],   // fused / verified track (what Skynet believes), 2-min steps
    "claimed":  [[t, lon, lat, sog, cog], ...],   // AIS reports as broadcast (gaps when dark, false positions when spoofing)
    "observed": [{"id": "SAR-0193", "t": t, "lon": .., "lat": .., "length_m": 230, "matched_to_ais": false,
                  "sensor": "SAR (simulated Sentinel-1 class)"}],
    "emitted":  [{"id": "RF-0441", "t": t, "lon": .., "lat": .., "band": "X-band navigation radar",
                  "sensor": "RF (simulated)"}],
    "states":   [{"start": t, "end": t, "state": "verified|dark|spoofing"}],   // covers start..end
    "dark_periods": [{"start": t, "end": t, "minutes": 94}],
    "identity_history": [{"t": t, "name": "..", "flag": "..", "mmsi": ..}],    // usually one entry
    "risk": {"score": 87, "level": "low|medium|high",
             "reasons": [{"text": "AIS off 94 min while radar still saw the hull over Cable A", "points": 35,
                          "source": "ais|sar|rf|registry|sanctions|fusion"}]},
    "voyage": null | {"route_id": "rastanura-kaohsiung", "label": "Ras Tanura → Kaohsiung", "via": "Strait of Hormuz",
                      "hull_usd_m": 100, "departs": "2026-11-12"}            // only the T-11 tanker
  }],
  "events": [{
    "id": "E-0012", "vessel_id": "D-04",
    "type": "dark_period|position_mismatch|spoofing|identity_change|ship_to_ship_meeting|loitering|slow_speed|anchor_drag_pattern|zone_entry|zone_exit|closest_approach_to_asset",
    "t": t, "end": t|null, "lon": .., "lat": .., "severity": "low|medium|high",
    "summary": "One plain sentence.", "facts": ["fact", "fact"],
    "source": "ais|sar|rf|fusion|operator", "zone_id": "Z-1"|null, "asset_id": "cable-a"|null,
    "other_vessel_id": null|"V-22"            // ship_to_ship_meeting
  }],
  "alerts": [{
    "id": "A-0003", "vessel_id": "D-04", "event_ids": ["E-0012", "E-0013"], "zone_id": "Z-1",
    "t": t,                                   // when it fires (Live shows it once clock >= t)
    "severity": "high",
    "headline": "Dark tanker dragging a likely anchor over Cable A",
    "facts": ["AIS silent 94 min", "Radar: 230 m hull 0.4 km from the cable", "Nav radar still emitting", "Speed 1.8 kn, heading swings 70°"],
    "actions": [{"id": "dispatch_patrol", "label": "Dispatch nearest patrol"},
                {"id": "forward_owner", "label": "Forward to cable owner"},
                {"id": "dismiss", "label": "Not suspicious"}],
    "recipients": [{"name": "Coast guard duty officer", "channel": "push"},
                   {"name": "Grid operator control room", "channel": "email"}],
    "status": "open|actioned|dismissed"
  }],
  "actions": [Action]                         // operator actions logged so far (see below)
}
```

Seed content (A): one cable-corridor zone `Z-1` around Cable A (Estlink 2 approx.), about 40 vessels in
the Gulf of Finland, one dark vessel `D-04` doing an anchor-drag pattern over Cable A, one spoofing
vessel `S-17` whose AIS puts it on land while radar sees it at sea, one tanker `T-11` on a risky route
(Ras Tanura → Kaohsiung via Hormuz; its track sits in the Gulf of Oman/Arabian Gulf), plus a
ship-to-ship meeting pair, one identity change, loiterers, and normal traffic.

Real context assets (A loads from `../cable-watch/data/cables.geojson`, source TeleGeography):
FEC-1, FEC-2, EESF-2, EESF-3, C-Lion1, Balticconnector, BCS North Phase 1.

## Zones

```jsonc
{
  "id": "Z-1", "name": "Cable A corridor", "purpose": "cable|sanctions|insurance|custom",
  "geometry": {"type": "corridor", "line": [[lon, lat], ...], "width_km": 4}
            | {"type": "polygon", "coordinates": [[lon, lat], ...]}           // ring, not closed
            | {"type": "watchlist", "vessel_ids": ["T-11", "V-22"]},
  "asset_id": "cable-a" | null,
  "rules": [{
    "id": "dark_in_zone", "label": "Tracker switched off inside zone", "enabled": true,
    "severity": "low|medium|high", "window": null | "1h|6h|24h|30d",
    "params": [{"key": "min_minutes", "label": "Longer than", "value": 20, "unit": "min"}]
  }],
  "metrics": [{"id": "vessels_in_zone", "label": "Vessels currently in zone", "enabled": true}
            | {"id": "custom-1", "label": "Slow vessels, rolling 1 h", "enabled": true,
               "custom": {"signal": "speed_below_kn", "param": 3, "aggregation": "count|sum|max|avg",
                          "window": "1h", "alert_above": 2}}],
  "recipients": [{"name": "Coast guard duty officer", "channel": "in_app|push|email|webhook",
                  "address": "", "min_severity": "medium", "quiet_hours": null | "22:00-06:00"}],
  "created": t, "created_by": "Ian (demo)"
}
```

`GET /api/presets` returns purpose presets (A, in zones.py `PRESETS`):

```jsonc
{"cable": {"label": "Cable / pipeline protection", "description": "...", "rules": [Rule], "metrics": [MetricDef]},
 "sanctions": {...}, "insurance": {...}, "custom": {"rules": [], "metrics": [...]}}
```
`MetricDef`: `{"id", "label", "enabled", "unit"}`. Custom metric signals: `speed_below_kn`, `dark_minutes`,
`risk_score`, `distance_to_asset_km`, `in_zone`.

Rule ids per purpose (spec):
- cable: dark_in_zone, slow_speed (min_kn 3, min_minutes 30), anchor_drag_pattern, no_anchor_area,
  loitering (minutes X, km Y of asset), false_position
- sanctions: dark_period (hours N), ship_to_ship_meeting, identity_change, spoofing, flagged_port_call,
  designated_owner
- insurance: listed_area_entry, jamming_exposure, incidents_near_route (km X), dark_during_policy
- custom: empty

Metric ids per purpose (spec):
- cable: vessels_in_zone, dark_minutes_in_zone, closest_approach_to_asset, time_over_asset,
  highest_risk_score, alert_to_ack_time
- sanctions: dark_hours_30d, sts_meeting_count, identity_changes, risk_score_trend
- insurance: voyage_risk_score, hours_in_high_risk_zones, insured_value_exposed, jamming_events_on_route

`zones.py` must expose (A):
- `PRESETS: dict`
- `evaluate_zone(world: dict, zone: dict) -> {"events": [Event], "alerts": [Alert]}` (new ids prefixed with the zone id)
- `zone_metrics(world: dict, zone: dict, t: int) -> [{"id", "label", "value", "unit", "detail"}]`
- `zone_polygon(zone, world) -> [[lon, lat], ...]` (corridor buffered to a ring; watchlist -> None)

`seed.py` must expose `build_world(seed: int = 7) -> dict` (calls fusion), and `python -m server.seed`
writes `data/world.json`. `fusion.py` exposes `fuse(world) -> world` (fills states, dark_periods, events,
risk, alerts) and is pure/deterministic.

## UI API (B, unless noted)

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/world | `?case=` (C supplies real cases) | World |
| GET | /api/presets | | presets |
| POST | /api/zones | Zone without id | `{"zone", "events", "alerts", "metrics"}` |
| PUT | /api/zones/{id} | Zone | same |
| DELETE | /api/zones/{id} | | `{"ok": true}` |
| GET | /api/zones/{id}/summary | `?t=` | `{"zone", "metrics", "events", "alerts"}` |
| POST | /api/alerts/{id}/actions | `{"action": "dispatch_patrol", "by": "Duty officer", "note": ""}` | Action |
| GET | /api/deliveries | `?alert_id=` | `[{"id", "alert_id", "recipient", "channel", "status": "delivered", "t", "preview": {"title", "body"}}]` (simulated) |
| GET | /api/vessels/{id}/record | `?from&to` | Record (same as /v1) |
| POST | /api/reports | `{"vessel_id", "template": "cable|sanctions|insurance", "from", "to", "requested_by"}` | Report |
| GET | /api/reports/{id} | | Report |
| GET | /api/reports/{id}.pdf | | application/pdf (headless Chrome prints /report.html?id=..&print=1) |
| POST | /api/reset | | `{"ok": true}`; reseeds and clears actions, zones (except Z-1), reports |
| POST | /api/analyst | `{"question", "t"}` | Answer (C) |
| GET | /api/live/ais | | real Fintraffic AIS `[{"mmsi","name","lon","lat","sog","cog","t","type"}]` (C) |
| GET | /api/cases | | `[{"id","label","source","description"}]` (C) |

Action: `{"id": "ACT-0001", "alert_id", "vessel_id", "action", "label", "by", "t", "note"}`; it is
appended to `world.actions`, the alert's `status` changes (dismiss -> dismissed, others -> actioned),
and an event `{"type": "operator_action", "source": "operator", ...}` is added to that vessel's record.

Record (`/v1/vessels/{id}/record`):
```jsonc
{"vessel": {id, mmsi, imo, name, flag, type, length_m}, "from": t, "to": t,
 "verified_track": [[t, lon, lat]], "claimed_track": [[t, lon, lat]], "dark_periods": [...],
 "events": [Event incl. operator actions], "risk": {...},
 "evidence_url": "http://.../report.html?vessel=D-04", "fingerprint": "sha256:..."}
```
Fingerprint = sha256 of the canonical JSON (sorted keys, no spaces) of the record's events list.

Report:
```jsonc
{"id": "R-0001", "template": "cable", "title": "Cable incident record", "vessel": {...},
 "period": {"from": t, "to": t}, "requested_by": "Coast guard duty officer", "generated": t,
 "summary": "3-4 sentences", "timeline": [{"t", "source", "type", "text"}],
 "exhibits": [{"kind": "radar", "caption", "detection": {...}}, {"kind": "track", "caption", "claimed": [[lon,lat]], "verified": [[lon,lat]], "dark": [[[lon,lat]...]], "asset": GeoJSON|null}],
 "risk": {...}, "fingerprint": "sha256:...", "record_url": "/v1/vessels/D-04/record?from=..&to=.."}
```
Templates: `cable` "Cable incident record", `sanctions` "Vessel sanctions due-diligence",
`insurance` "Insurance voyage history / claims reconstruction".

## Data API (B)

API key: header `Authorization: Bearer sk_live_skynet_demo` (also accept `x-api-key`). 401 otherwise.
- `GET /v1/vessels/{id}/record?from&to`
- `GET /v1/vessels/{id}/risk` -> `{"vessel_id", "score", "level", "reasons", "as_of"}`
- `GET /v1/zones/{id}/events?since=`
- `POST /v1/zones` (Zone body; same as /api/zones)
- Webhooks: `POST /v1/webhooks {"url"}` registers; every new alert POSTs `{"type": "alert.created", "data": Alert}`
  to each URL (best effort) and logs it; `GET /v1/webhooks/deliveries` lists the log.

## Analyst answer (C)

```jsonc
{"question": "...", "verdict": "One line.", "risk": {"score": 71, "level": "high"} | null,
 "reasons": ["...", "..."], "sources": [{"label": "JMIC Advisory Update 112", "url": "..."}],
 "actions": [{"type": "show_on_map", "label": "Show on map", "vessel_ids": ["D-04"], "bbox": [..]|null, "t": t|null},
             {"type": "report", "label": "Export report", "vessel_id": "T-11", "template": "insurance"}],
 "tool_calls": [{"tool": "vessels_near_asset", "args": {...}, "summary": "3 vessels"}],
 "empty": false,                     // true when tools found nothing: verdict says so, no invented data
 "engine": "llm:<model>" | "planner"}
```

## Frontend shell (lead)

`index.html` mount points (all absolutely positioned over the full-screen `#map`):
`#topbar` (logo, mode nav, `#ask-mount` for the analyst bar, clock, demo button),
`#alert-feed` (Live, right), `#phone` (Live phone preview overlay), `#rewind` (Rewind UI),
`#zones` (zone drawing toolbar, wizard, side sheet), `#analyst-panel` (answer card), `#vessel-card`
(small vessel popover, owned by D), `#toast`.

`js/app.js` exports a single context object `ctx` and calls each module's `init(ctx)`:
```js
ctx = {
  map,                 // maplibregl.Map (after 'load')
  api,                 // see js/api.js
  bus,                 // EventTarget helper: bus.on(name, fn), bus.emit(name, detail)
  clock,               // {t, speed, playing, set(t), play(speed), pause(), on(fn)} emits 'tick' each frame
  world,               // current World (reloaded by app.reloadWorld())
  mode,                // 'live' | 'rewind' | 'zones'
  setMode(m),
  reloadWorld(),       // re-fetch /api/world, emits 'world'
  vesselAt(v, t),      // {lon, lat, sog, cog, state, claimed:{lon,lat}|null} interpolated
  fmtTime(t), fmtDate(t), fmtDuration(seconds)
}
```
Bus events: `world` (world reloaded), `mode` ({mode}), `tick` ({t}), `select-vessel` ({id}),
`focus` ({vessel_ids?, bbox?, t?}) — fly the map there, `open-rewind` ({vessel_id, t?}),
`open-report` ({vessel_id, template, from?, to?}), `zones-changed`, `alert` ({alert}) when Live shows one,
`toast` ({text}).

Modules: `live.js`, `rewind.js`, `zones.js`, `analyst.js` each `export function init(ctx)`.
They must work when the others fail to load (app.js imports each in a try/catch).

Map layer ids must be prefixed by owner: `base-*` (map.js), `live-*`, `rw-*`, `zn-*`, `an-*`.

## Design rules (all frontend)

- Font: SF Pro (stack in base.css), weights 400 and 500 only, letter-spacing -0.15px.
- Type sizes ONLY 12px, 13px, 14px, 24px. Use the CSS vars `--fs-12/13/14/24`.
- Text colors ONLY #292929 (primary), #5D5D5D (secondary), #9E9E9E (tertiary, only on >= 14px or decoration;
  for AA small text use #5D5D5D). Semantic colors: verified blue `--blue` #1F6FEB, dark orange `--orange`
  #E8590C, danger `--red` #D1242F, ok `--green` #1A7F37.
- Icons: Lucide (`<i data-lucide="name"></i>` then `lucide.createIcons()`), 14px in navigation, 20px in cards.
- Radii: 8px navigation, 16px cards, 999px pill CTAs. 44px minimum touch targets for buttons.
- Vercel-like: white surfaces, 1px #EAEAEA borders, soft shadow `--shadow`, generous whitespace.
- NO KPI tile grids, no gradients, no left-border accent cards. "One moment, one decision."
- Real `<button>`s and `<label>`s, visible focus rings, WCAG AA contrast.
