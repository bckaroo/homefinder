# Transit Housing Analysis Map

PROJ-041 · DevOS-tracked.

Housing affordability crossed with commute reachability: what does living in a
place actually cost you in rent and in time, by mode.

## Scope

The product is an **apartment-finding tool for the NY metro**: a location is a
candidate if it reaches work inside a time limit — *by transit, cycling or
walking* — while clearing a rent ceiling, across a set of factors rather than a
single accessibility number.

1. **Reachability, per mode.** Peak-hour multimodal GTFS graph (MTA subway,
   Metro-North, LIRR, PATH) with walk transfers at hub stations and Dijkstra
   from an employment anchor. Cycling and walking are *separate modes* over the
   street network, not a speed reduction on the transit result: Valhalla routes
   every tract centroid to the anchor under `pedestrian` and `bicycle` costings
   (`scripts/build_modes.py`). **Built: all three modes**, with bike/walk
   computed for the tracts inside a 30 km radius of the anchor — outside that,
   the tract is left unshaded rather than given a fabricated time.
2. **Destination.** The anchor is an input, not a constant — the tool answers
   for *your* workplace. Currently hardcoded to 1 Penn Plaza (DEV-323).
3. **Affordability** — ACS 5-year housing cost by census tract (B25031, B25064),
   with coherence and relative-MOE gates. Rent burden needs the income half
   (DEV-316). Rents are **tract medians, not listing prices**; no listings source
   is wired.
4. **Score** — combine commute minutes and rent burden into a sortable score
   with exposed weights (DEV-318).
5. **Map and shortlist** — choropleth *plus* a ranked list, mode selector,
   legend, title block and source note (DEV-319).

### What this does not do

No bus network — outer Queens/Brooklyn tracts inherit their nearest rail
station's time and read as better commutes than they are. Tracts beyond the
22-minute walk gate are omitted rather than shown as long commutes. Rents are
ACS medians with a `>= $3,501` topcode censoring 19.7% of Manhattan tracts.
Bike/walk times are free-flow street-network estimates with no weather, traffic
or e-bike assumptions, and only exist inside the 30 km routing radius.

## Layout

```
scripts/     pipeline stages (fetch, graph, join, score)
web/         map front end
data/        derived outputs (raw downloads are gitignored)
docs/        methodology and data provenance
notes/       DEVLOG.md — read this first
```

## Task board

`devos list --project PROJ-041` — or the dashboard at http://localhost:3102
