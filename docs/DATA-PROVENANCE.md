# Data provenance

Sources for the Penn 1 housing/transit analysis. Every number in the map traces
to one of these. Retrieval date: **2026-10-31**.

## Transit — GTFS

| Feed | Source | Notes |
|---|---|---|
| MTA Subway | `web.mta.info` GTFS static (`gtfs_subway.zip`) | 1,489 stop records |
| Metro-North | `web.mta.info` GTFS static (`gtfs_mnr.zip`) | 114 stations; `transfers.txt` is 100% degenerate self-loops → discarded |
| LIRR | `web.mta.info` GTFS static (`gtfs_lirr.zip`) | 127 stations |
| PATH | `data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip` | 64 stops; 20 MB `stop_times.txt` |

Ride times are **scheduled**, not real-time. Each trip contributes edges in
*both* directions: an inbound AM-peak working implies a same-duration outbound
ride. Without this, MNR (whose AM schedule is almost entirely inbound) has zero
outbound reachability from Penn.

Peak window: trips with any departure between **07:00–09:00** local.

Cross-feed walking transfers are inferred at **≤600 m** between stops of
different feeds, at 1.35 m/s. This is an approximation of hub circulation
(34 St–Penn ↔ PATH 33rd; subway 42 St ↔ GCT) and is not an official transfer
list.

## Geography — census tracts

| Item | Source |
|---|---|
| NY tract boundaries | `www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_36_tract_500k.zip` |
| NJ tract boundaries | `www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_34_tract_500k.zip` |

Joined on `GEOID` (11-digit). Tracts assigned to the nearest reachable station
by tract representative point.

## Rent — ACS 5-year, 2020–2024

Keyless bulk downloads (the `api.census.gov` endpoints now require a key and
redirect to `missing_key.html`; the bulk summary files do not):

- `acsdt5y2024-b25064.dat` — B25064 median gross rent, all units
- `acsdt5y2024-b25031.dat` — B25031 median gross rent **by bedrooms**
  (E002 = studio/no bedroom, E003 = 1BR)
- `Geos20235YR.txt` — geography lookup for GEO_ID ↔ tract

### Sentinels (scrubbed)

| Value | Meaning |
|---|---|
| `-666666666` | estimate not available |
| `-333333333` | control total, no sampling error |
| `-222222222` | not applicable |
| `-555555555` | insufficient data |

These appear in **both** estimates and margins of error. Scrubbing only point
estimates leaves thousands of impossible negative MOEs (6,722 found).

### Topcoding — the important caveat

ACS rents are **topcoded at $3,501**. Any tract whose median exceeds the cap
returns exactly `3501`; a capped tract may truly be $3,600 or $9,000.

- **19.7% of Manhattan tracts** are capped (59 of 300 with data)
- Brooklyn 2.7%, Queens 2.0%, Bronx 0.0%, Staten Island 0.9%

The map **never renders `3501` as a number** — capped cells display `≥$3,501`.
Capped tracts cannot be ranked against each other and cannot be asserted
in-budget or over-budget from ACS alone.

## Rent — Zillow ZORI (uncapped, current)

`files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv`

Monthly, ZIP-level, **not topcoded**, current to **2026-09-30**. This is what
makes the ACS censoring tolerable.

**It is a different measure.** ZORI is an all-homes smoothed index; ACS B25031 is
a median of actual paid rents split by bedroom count. They agree in direction
but not in level, and ZIP↔tract is not one-to-one. ZORI is therefore kept as a
separate signal, **not** blended into tract medians.

Reference points (2026-09-30, all-homes):

| ZIP | Area | ZORI |
|---|---|---|
| 07302 | Jersey City | $3,888 |
| 07310 | Jersey City (Newport) | $4,429 |
| 07030 | Hoboken | $4,005 |

NYC ZIP range: **$2,121 – $8,183**.

## Reliability gates applied

A tract is marked **trustworthy** only if all hold:

1. **Coherent** — 1BR median ≥ 90% of studio median.
   Rejected 224 of 2,449 tracts (9.15%) that failed this; a 1BR renting below a
   studio is small-sample noise, not market reality.
2. **Relative MOE ≤ 0.30** on at least one of studio/1BR — ACS's own usability
   threshold. Median relative MOE is 0.15; the 75th percentile is 0.32.
3. Not topcoded on the value being asserted.

Effect: in-budget candidates fall from 558 (no gates) to **359** (gated).

## Known limitations

- **Scheduled, not actual, service.** No delays, no transfers-by-timetable.
- **Walk speed flat 1.35 m/s** regardless of terrain, weather, mobility.
- **No bus network.** Only rail modes are graphed; a bus-dependent tract
  inherits the nearest rail station's time, which can overstate its commute.
- **Tract centroids are a coarse proxy** for "where you'd live"; a large tract
  gives one number.
- **ACS lags ~18 months.** 2020–2024 data cannot describe a December 2026 move.
  ZORI is the current-market cross-check.
- **No rent burden yet.** DEV-316 adds income (B19013/B25070); the score in
  DEV-318 depends on it.
