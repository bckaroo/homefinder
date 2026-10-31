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

## Stage 3b — asking rents (`scripts/build_asking.py`)

Zillow ZORI, latest month, published per ZIP code. It is a *market* index — the
typical advertised rent for a constant-quality unit — so it answers a different
question from ACS B25064, which is what sitting tenants pay. Their ratio is the
uplift a mover faces on arrival.

There is no free tract-level asking series, so the index is allocated areally:

1. ZIP Code Tabulation Areas for the study envelope are pulled from TIGERweb
   (`PUMA_TAD_TAZ_UGA_ZCTA/MapServer/11`, ~765 polygons, 16 MB) and cached at
   `data/raw/geo/zcta520_metro.geojson`. The national shapefile is 529 MB.
2. Tracts and ZCTAs are projected to UTM 18N (EPSG:32618) and intersected.
   Areas in a geographic CRS would weight latitude, not ground.
3. Each tract takes the area-weighted mean ZORI of the ZCTAs it overlaps,
   ignoring slivers under 100 m². `ask_share` records how much of the tract the
   matched ZCTAs cover; `ask_nzips` records how many contributed, so a blended
   tract is visible rather than hidden.
4. `premium = zori / ACS all-unit rent`, computed only where the ACS value is
   above $500 (below that the median is group quarters or a handful of
   subsidised units, and the ratio explodes) and below the $3,501 topcode.
   `prem_relmoe` carries the ACS relative margin of error; above 0.30 the ratio
   is drawn faded in the map and flagged in the detail card.

Coverage: 3,130 of 3,457 mapped tracts (90%) fall in a ZCTA that carries an
index. The remainder are left unpainted — not extrapolated from a neighbour,
which would invent a rent for exactly the tracts where the data is thinnest.

Measured result: across mapped tracts the market asks a median **1.56×** the
paid median (p10 1.30×, p90 2.06×). The gap widens with turnover and is widest
where rent stabilisation holds the sitting stock down, which is the expected
direction, and is why the map ships both views rather than one "rent" number.

Caveat to keep in view: ZORI excludes utilities while ACS gross rent includes
them, so a few percent of the premium is definitional, not market.

## Stage 4 — map (`scripts/build_html.py`)

Static MapLibre GL JS 6.16.0 app. The engine is an ES module, so the page is
served over http (`scripts/serve.py`, port 3118) rather than opened from disk.

Colour logic, evaluated per tract:

| Colour | Meaning |
|---|---|
| green | studio *or* 1BR median in budget, reliable, not capped |
| amber | topcoded (`≥$3,501`) or relative MOE > 0.30 — value not assertable |
| red | cheapest reliable value above the rent ceiling |
| grey | cheapest reliable value below the rent floor |
| absent | commute exceeds the slider, or no station |

Two further colour modes reuse the same shell (`Colour by` in the Explore pane):

| Mode | Encoding |
|---|---|
| Asking | continuous teal, $1,800 → $4,800+ ZORI |
| Premium | warm ramp, 1.0× → 2.0×+ asking over paid; low-confidence ratios washed toward paper |

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
python3 scripts/build_asking.py  # -> data/tract_asking.csv (+ ZCTA cache)
python3 scripts/join_all.py      # -> data/tract_joined.csv, data/shortlist.csv
python3 scripts/build_html.py    # -> web/index.html
```

Scripts currently read GTFS from `/tmp/gtfs_*` and ACS from `/tmp/*.dat` — see
follow-ups in the DEVLOG. Hardcoded paths are the next cleanup.
