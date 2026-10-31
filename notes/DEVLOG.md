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
