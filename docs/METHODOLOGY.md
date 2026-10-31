# Methodology — Penn 1 reachability & affordability

How the pipeline turns GTFS timetables plus ACS rent tables into a per-tract
commute and affordability picture.

## Stage 1 — transit graph (`scripts/build_graph.py`)

**Nodes** are `(feed, stop_id)` pairs. The feed qualifier is mandatory: 23 of
Metro-North's 114 stop_ids are *also* valid subway stop_ids (`101`, `106`,
`108`, `110`, …). Any edge written with a bare stop_id silently connects two
subway stops instead of bridging to MNR.

**Edges**, three kinds:

1. **Ride edges** — consecutive stops in a peak trip, at scheduled elapsed time.
   Written **bidirectionally**. The AM peak is directionally lopsided (MNR's is
   almost entirely inbound); a one-way graph leaves inbound-only networks with
   no outbound reachability at all.
2. **Parent-station edges** — `location_type=1` stations are containers; trips
   reference platforms (`631N`/`631S`). Without bidirectional platform↔parent
   links each parent is an isolated island. This recovered ~500 subway stops.
3. **Walking transfers** — ≤600 m between stops of *different* feeds, at
   1.35 m/s, floored at 60 s. Represents hub circulation.

**Transfers.txt** is used where trustworthy. MTA's Metro-North feed ships 16,742
transfer rows that are all `from == to` self-loops with no timing — discarded.

**Origin set** — every stop within 900 m of Penn 1 (40.75056, −73.99386). Penn is
a large complex; 900 m covers the 1/2/3 and A/C/E platforms, PATH 33rd, and the
LIRR concourse. **Grand Central is 1,441 m away** and is *not* an origin — it is
reached via subway (34 St → 42 St, then the platform↔parent walk edge).

**Dijkstra** from the origin set, minimising total seconds.

### Verification

| Feed | Stations reached | Median | Max |
|---|---|---|---|
| Subway | 1,425 | 24.3 min | 62.8 |
| Metro-North | 110 / 114 | 59.1 min | 145.6 |
| LIRR | 126 | 40.0 min | 171.0 |
| PATH | 64 / 64 | 17.6 min | 34.6 |

1,725 of 1,793 total stops reachable; 1,635 within 60 minutes.

Sanity anchors: Newport→33 St PATH ≈ 18 min (matches), median Metro-North
station ≈ 59 min (the median MNR station is in Westchester, not the Bronx),
LIRR max 171 min = Montauk on a peak working.

## Stage 2 — tract rent (`scripts/build_rent.py`)

ACS 5-year 2020–2024, B25031 (rent by bedrooms) and B25064 (all-unit median),
fetched as keyless bulk `.dat` files. Joined to 11-digit tract `GEOID` via
`Geos20235YR.txt`. Sentinels scrubbed in both estimates and MOEs — see
`DATA-PROVENANCE.md`.

## Stage 3 — join (`scripts/join_all.py`)

Tracts (NY + NJ) → nearest reachable station by **representative point** →
`commute_min = ride_min + walk_min`, where `walk_min` uses 1.35 m/s.

`commute_min` is set to null when `walk_min > 22` — a station more than 22
minutes' walk away is not a station serving that tract, and claiming otherwise
manufactures short commutes for rural tracts.

## Stage 4 — map (`scripts/build_html.py`)

Self-contained Leaflet app, ~1.6 MB, no server needed.

Colour logic, evaluated per tract:

| Colour | Meaning |
|---|---|
| green | studio *or* 1BR median in budget, reliable, not capped |
| amber | topcoded (`≥$3,501`) or relative MOE > 0.30 — value not assertable |
| red | cheapest reliable value above the rent ceiling |
| grey | cheapest reliable value below the rent floor |
| absent | commute exceeds the slider, or no station |

Capped values render as `≥$3,501`, never as a number.

## Reproducing

```bash
# GTFS feeds (gitignored; re-fetch)
mkdir -p data/raw && cd data/raw
curl -O https://web.mta.info/developers/data/nyct/subway/google_transit.zip
# ...etc — see DATA-PROVENANCE.md for exact URLs

# ACS tables
curl -O https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25064.dat
curl -O https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25031.dat

python3 scripts/build_graph.py   # -> data/station_times.csv
python3 scripts/build_rent.py    # -> data/tract_rent.csv
python3 scripts/join_all.py      # -> data/tract_joined.csv, data/shortlist.csv
python3 scripts/build_html.py    # -> web/index.html
```

Scripts currently read GTFS from `/tmp/gtfs_*` and ACS from `/tmp/*.dat` — see
follow-ups in the DEVLOG. Hardcoded paths are the next cleanup.
