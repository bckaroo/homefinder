# Development Log

Append-only log of each round of development on this repo (part of DevOS
project **PROJ-041**). New entries go at the top.

## Round 3 — 2026-10-31 · Ownership transfer to xiaogou; scope reframed against the product ask

**Status:** DEV-317 and DEV-321 → DONE. Claimed by `xiaogou` (Hermes).
DEV-319 claimed; DEV-322 filed READY; DEV-323 filed SPEC.

### Transfer

Andrew handed PROJ-041 from `xiaomao` to `xiaogou` on 2026-10-31. The board came
over clean: `origin/main` at `15d5528`, no stashes, no unpushed commits. xiaomao
stood down and confirmed in writing that the repo holds all tracked work and
that nothing exists outside it except superseded `/tmp` scratch and raw
downloads.

Ownership was NOT taken on trust. Both REVIEW cards were closed only after the
new owner re-ran the pipeline himself:

| artifact | md5 | vs DEV-321 baseline |
|---|---|---|
| `station_times.csv` | `ef75f7ee` | identical |
| `graph_stats.json` | `bcdfee28` | identical |
| `tract_rent.csv` | `faf1938d` | identical |
| `tract_joined.csv` | `08ba1000` | identical |
| `shortlist.csv` | `d3294822` | identical (359 rows) |
| `web/index.html` | `a85a0f45` | identical |

All four stages ran under `/usr/bin/python3` (geopandas 1.1.3) — a different
interpreter from the one the baseline was produced on. `git status` returned
zero diffs afterwards, which is the reproduction proof: identical bytes, not a
passing test suite that tolerates drift.

### Scope reframed

The build answers a narrower question than the product ask. As committed,
`config.py` pins `PENN1` as an analysis constant and the only commute mode is
transit; `WALK_MPS`/`MAX_WALK_MIN` are walk-to-station access, not a mode.
There is no cycling anywhere in the pipeline and no street-network router.

The product is an apartment-finding tool for the NY metro: a place is a
candidate if it reaches work inside 60 minutes **by transit, cycling or
walking**, filtered against a rent ceiling, across a set of factors. Filed:

- **DEV-322** (READY) — walking and cycling modes via Valhalla in Docker over a
  Geofabrik NY-metro extract, with `walk_min` and `cycle_min` as separate
  columns. Valhalla over OSRM because OSRM's foot profile is a speed-reduced car
  profile and ignores stairs, footways and turn restrictions. No routing engine
  is installed and no OSM extract exists on disk, so this is a spike, not a
  config change.
- **DEV-323** (SPEC) — a user-supplied destination replacing the hardcoded
  Penn 1 origin.
- **DEV-319** broadened in place (not duplicated) to add a mode selector, a
  ranked shortlist view beside the choropleth, and an on-canvas destination.

### Defects received from the outgoing owner (not previously in this log)

Recorded verbatim in substance, since they are honest gaps in the shipped build:

- ZORI is read by `join_all.py` and used by nothing. The ACS 1-year table was
  pulled to probe the topcode and then abandoned unused.
- `MAX_WALK_MIN = 22` nulls `commute_min` for far-from-station tracts, so outer
  tracts are *absent* from the map rather than shown as long commutes. Reads as
  a coverage failure to anyone who does not know the gate exists.
- No bus network: bus-dependent outer Queens/Brooklyn tracts inherit the nearest
  rail station's time, overstating the real commute.
- The `>= $3,501` topcode rendering is a decision, not a proof. 19.7% of
  Manhattan tracts are censored, and any downstream score treating those as
  "expensive" is inferring from an unknown.
- Dead code: unused `haversine()` and `Point` import in `join_all.py`; leftover
  `OUT` in `build_rent.py`. `data/tracts.geojson` is written into `data/` but
  gitignored, which reads as a tracked artifact and is not.

### Follow-ups

- Bus coverage is the largest single accuracy gap for the outer boroughs and is
  not yet filed.
- The censored-tract problem gets worse as modes are added: a tract can be
  cheap-by-median and unaffordable in fact. Listings-level data would resolve
  it; ACS medians cannot.
- No `Makefile`/driver: stage order still matters (running `build_html` before
  `join_all` silently consumes a stale `tract_joined.csv`).

## Round 2 — 2026-10-31 · DEV-317: Metro-North reachability bug cleared; pipeline migrated from /tmp

**Status:** DEV-317 → REVIEW. Claimed by `xiaomao`.

### Three bugs found and fixed in the transit graph

Each produced plausible-looking output, which is why they survived the first pass.

1. **Metro-North had zero reachable stations** despite 8,276 peak trips loading
   cleanly. Cause: MNR's AM-peak timetable is almost entirely *inbound*. Edges
   were built in listed order only, so outbound reachability from Penn covered
   nothing. **Fix:** every trip contributes edges in both directions — an
   inbound working implies a same-duration outbound ride.

2. **Subway Grand Central was an isolated island.** GTFS `location_type=1`
   stations are containers; trips reference platforms (`631N`/`631S`).
   `631` had only synthetic parent↔parent edges, so it never joined the network
   — and the subway→MNR bridge at 42 St therefore carried nothing.
   **Fix:** bidirectional platform↔parent links (1,043 added). Recovered ~500
   subway stops on its own.

3. **stop_id namespace collision across feeds.** 23 of MNR's 114 stop_ids are
   *also* valid subway stop_ids (`101`, `106`, `108`, `110` …). The cross-feed
   bridge stored bare stop_id edge targets, so `subway:631 → MNR:1` silently
   became `subway:631 → subway:1`, a phantom edge. Every edge target is now a
   fully-qualified `(feed, stop_id)` pair.

Also discarded MTA Metro-North's `transfers.txt`: all 16,742 rows are degenerate
self-loops with no `min_transfer_time` — generated padding, useless for routing.

### Result

| Feed | Stations reached | Median to Penn 1 | Max |
|---|---|---|---|
| Subway | 1,425 | 24.3 min | 62.8 |
| Metro-North | 110 / 114 | 59.1 min | 145.6 |
| LIRR | 126 | 40.0 min | 171.0 |
| PATH | 64 / 64 | 17.6 min | 34.6 |

1,725 / 1,793 stops reachable; **1,635 within 60 minutes**. AC met: no feed
returns zero, and the times match known commutes (Newport→33 St PATH 18 min;
median MNR station 59 min — the median MNR stop is in Westchester; LIRR max
171 min = Montauk).

### Rent layer built ahead of DEV-316 (rent half only)

ACS 5-yr 2020–2024 B25031 + B25064 via keyless bulk `.dat` (the JSON API now
requires a key and redirects to `missing_key.html`; the summary files do not).

Two data traps handled explicitly:

- **Topcoding at $3,501.** 19.7% of Manhattan tracts are capped. Capped cells
  render as `≥$3,501`, never as a number, and are excluded from in-budget
  assertions.
- **ACS sentinels** (`-333333333`, `-222222222`) appear in MOEs as well as
  estimates. Scrubbing only estimates left 6,722 impossible negative MOEs.

Zillow ZORI downloaded and verified (uncapped, current to 2026-09-30, NYC range
$2,121–$8,183) but deliberately **not** blended into tract medians — it is a
different measure at a different geography. Documented as a cross-check.

### Reliability gates

Coherence (1BR ≥ 90% of studio, rejected 224 tracts) + relative MOE ≤ 0.30
(ACS's own threshold). In-budget candidates: **558 ungated → 359 gated**.

### Artifacts

- `scripts/build_graph.py` — stage 1
- `scripts/build_rent.py` — ACS rent stage
- `scripts/join_all.py` — transit × rent join
- `scripts/build_html.py` — self-contained Leaflet map
- `web/index.html` — 1.6 MB map, no server required
- `data/station_times.csv` (1,725 stations), `data/tract_rent.csv`,
  `data/tract_joined.csv`, `data/shortlist.csv` (359 rows)
- `docs/METHODOLOGY.md`, `docs/DATA-PROVENANCE.md`

### Follow-ups

- Scripts hardcode `/tmp/gtfs_*` and `/tmp/*.dat`; parameterise input paths.
- No bus network — a bus-dependent tract inherits its nearest rail station's
  time, which overstates real commute.
- DEV-316 still needs the **income** half (B19013/B25070) for rent burden.
- ZORI not yet wired in; needs areal interpolation (tract↔ZIP is not 1:1) or a
  separate ZIP-level panel.

## Round 1 — 2026-10-31 · Repo scaffold
- Created via `devos init-repo PROJ-041`; registered at
  `/home/abuck/.openclaw/workspace/dev/transit-housing-analysis-map`.
- Added `.gitignore` (raw data, node_modules, build output) and `README.md`
  describing the four-stage scope and layout.
- Commit `03039cf` — scaffold. Private remote:
  https://github.com/bckaroo/transit-housing-analysis-map
- Prior work by xiaomao lives in `/tmp/penn_maps` (GTFS graph prototype,
  station_times.csv, 1085 stations). Not yet moved in — DEV-317 covers the
  migration and the open Metro-North reachability bug (0/114 MNR stations
  reachable from Penn despite 8,276 peak trips loading).

## Round 2 — 2026-10-31 · Rent reporting + OpenFreeMap basemap
- `report_areas.py`: ACS 1BR medians aggregated to 176 NTAs and 26 counties.
  Verified B25031 carries only medians (E001–E007), cross-checked against
  B25064 across 80,142 tracts — identical to the dollar. Removed `units_total`,
  which was B25031_E001 duplicated under a count-like name.
- Measured the paid-vs-asking gap from ZORI per borough rather than assuming
  it: Manhattan 2.19x, Bronx 1.81x, Brooklyn 1.76x, Queens 1.41x, metro 1.42x.
  `asking_1br_est` carries the estimate; the UI states the ratio in the header.
- **Basemap history, so nobody re-treads it:** CARTO `dark_all` now returns
  "API KEY REQUIRED" on every tile (silently — no console error, just
  watermarked tiles). OSM's volunteer tiles return 403 to any automated
  browser. Both were verified by screenshot, not by assumption.
- Now on **OpenFreeMap** (`tiles.openfreemap.org/styles/dark`) via **MapLibre
  GL 4.7.1**, which is what OFM serves — vector tiles only, so Leaflet cannot
  consume them. Attribution comes from the style: OpenFreeMap / OpenMapTiles /
  OpenStreetMap.
- Choropleth is one fill layer keyed on a per-feature `fill` property, so
  slider moves mutate source data instead of rebuilding layers.
- Verification needs WebGL: the harness browser has none, so the render check
  runs Playwright + SwiftShader (`--enable-unsafe-swiftshader`). A plain
  `--screenshot` with `--virtual-time-budget` fires before `map.on('load')`
  and produces a blank map — that is a harness artifact, not a page bug.
- Still open: walk/cycle modes (Valhalla tiles building), bus network, income
  half of DEV-316, NTA-level map layer.

## DEV-322 — bike + walk modes shipped (Valhalla)

Valhalla 3.5.1 runs in Docker over the Geofabrik `new-york-latest` +
`new-jersey-latest` extracts, tiles built locally, `scripts/build_modes.py`
routes every tract centroid to the anchor under `pedestrian` and `bicycle` and
writes `data/commute_modes.csv`; `join_all.py` left-merges it and `build_html.py`
adds a **Travel mode** selector so the choropleth, the ranked list and the popup
all switch between transit, bike and walk.

Three defects were in the first cut of this stage, all found by checking the
output against the router by hand rather than trusting the run's exit code:

1. **Matrix response read wrong.** Valhalla's `sources_to_targets` returns
   `matrix[source][target]`; the code took `[0]`, i.e. the first source's row,
   and silently kept **one tract per batch** — 18 walked and 11 cycled out of
   7,570, each with a time 60× too large.
2. **Seconds read as minutes.** `time` is always seconds in Valhalla; `units`
   only affects distance. Everything was stored 60× high.
3. **One unroutable source killed a whole batch.** Pedal costings cap path
   distance at 200 km; a single upstate tract in a 100-source matrix makes the
   request return HTTP 400 and lose all 100 rows. Fixed twice over: a
   `--radius-km` scope filter (default 30 km) and a per-source retry
   (`route_each`) so a bad source costs only itself.

Result: 3,274 tracts inside 30 km, 3,270 walk / 3,272 cycle resolved, walk
median 161 min, cycle median 76 min against the 60-minute screen. Under the
default $2,000–3,000 budget the shortlist is 400 transit / 298 cycle / 82 walk.

Render verified with `scripts/check_render.py` (Playwright + SwiftShader, system
Chrome — the bundled Playwright build drifts from the installed package):
zero page errors, all three modes shade correctly. A plain headless browser has
no WebGL, so `new maplibregl.Map()` throws there and the whole script never
reaches `draw()` — harness artifact, not a page bug, and the reason the render
check must pass `--enable-unsafe-swiftshader`.

## Water-body tracts removed from the selection

Two mapped tracts were essentially water — 36005001904 (Eastchester Bay) and
36081099705 (Jamaica Bay), each >90% `AWATER` — and both were shaded: ACS still
tabulates a rent median for a harbour tract and Valhalla still returns a commute
from its centroid. Rule: `AWATER / (ALAND + AWATER) >= config.WATER_SHARE_MAX`
(0.90) drops the tract at map build, with the count reported in
`meta.water_tracts_removed` so the figure in the page is auditable.

The threshold is deliberately high. At 0.5 it would delete Bay Ridge, Sunset
Park, Pelham Bay and Broad Channel — real neighbourhoods with a shoreline — so
the gate is checked against the ranked list, not just the water share.

No geometry clipping was needed. An A/B render (tracts drawn under the basemap
`water` fill vs. on top) showed the cartographic boundary tracts already follow
the shoreline: the bays were only shaded because of those two water tracts, and
putting the choropleth under the style's opaque landuse layers cost most of its
legibility. Reverted; geometry unchanged.
