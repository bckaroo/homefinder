# Development Log

Append-only log of each round of development on this repo (part of DevOS
project **PROJ-041**). New entries go at the top.

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
