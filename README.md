# HomeFinder

PROJ-041 · DevOS-tracked. Repository folder stays `transit-housing-analysis-map`; the GitHub remote is `bckaroo/homefinder`.

**Live:** https://bckaroo.github.io/homefinder/ — published by `.github/workflows/pages.yml`, which uploads the committed `web/` directory as the Pages artifact. Pages only accepts `/` or `/docs` as a branch source, so `web/` is deployed via Actions rather than moved. Rebuild locally with `scripts/build_html.py`, commit `web/`, push `main`, and the site redeploys.

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

**Why the tracts used to look janky.** Three separate causes, all fixed:

1. Geometry came from the *cartographic* boundary file, `cb_2023_*_tract_500k`,
   which is generalised to 1:500,000 — roughly one vertex per 100 m of
   shoreline, straight chords across a street grid.
2. It was then simplified at `0.0012°` (~130 m). On top of a 1:500k source that
   turned every block into a long diagonal cut.
3. Each polygon was simplified on its own. Neighbours share an edge, so both
   sides got a *different* chord and every boundary opened a hairline gap and a
   staircase — the "mortar lines" look at street zoom.

The map now reads full-resolution TIGER/Line 2024 tracts (1:100,000,
street-following) and simplifies them **as a coverage**
(`shapely.coverage_simplify`, 15 m): each shared edge is simplified once, so
adjacent tracts keep identical edges and no gaps open. See `scripts/geo_prep.py`.

**Water.** TIGER tracts are *not* coast-clipped (unlike the CB files), so a
shoreline tract's polygon runs out over the harbour. `geo_prep` subtracts
TIGER AREAWATER — ocean, bay, lake and reservoir polygons only, buffered 25 m
outward so no hairline of colour is left sitting on the basemap's water.
Rivers need care: TIGER files **the Hudson (13.6 km²) and East River (8.0 km²)
as H3010, the same class as brook-sized drains**. Excluding that class leaves
both rivers unclipped — colour painted across the Hudson — while subtracting all
of it shaves a channel through every block a creek grazes. So H3010 is taken
only above a 0.2 km² area floor (`config.MIN_STREAM_M2`): rivers in, drains out.

Simplification runs after the cut, and a 15 m tolerance pulls a long shoreline
back over the water by up to 15 m — roughly 0.12 km² along the Manhattan
waterfront alone. So the water is cut twice: once buffered 25 m before
simplification, then exactly at the water edge afterwards. The only error left
points away from the water.

**Water-body tracts.** Tracts that are essentially water — `AWATER / (ALAND +
AWATER) >= 0.90` (`config.WATER_SHARE_MAX`) — are removed from the selection
entirely. ACS still tabulates a rent median for a harbour tract and the router
still returns a commute from its centroid, so left in they shade open water and
offer a "neighbourhood" nobody can live in. Two tracts in the current extent
qualify (Eastchester Bay 36005001904, Jamaica Bay 36081099705). Tracts that
merely *contain* a lot of water — Bay Ridge, Sunset Park, Broad Channel — are
real neighbourhoods and stay.

## Interface

The shell is modelled on the Allemannsdata Montréal case study: warm paper
(`#edf0e5`), a teal accent, a serif display face over Public Sans, floating
cards with a single soft shadow, and a bottom-right detail panel rather than
map popups. Three tabs — Explore, Shortlist, Sources — share one card.

The basemap is OpenFreeMap `positron`, re-tinted to the page paper: the
residential wash is switched off, woods warmed, and street-name and POI labels
dropped so place names are the only text. Tract fills sit at 72% opacity over
it, with a hairline paper-coloured seam between tracts.

### Depth

Buildings are extruded live from the same OpenFreeMap vector tiles the basemap
uses — the OpenMapTiles `building` layer carries `render_height` and
`render_min_height`, so no footprint download and no second tileset. 3D engages
itself the first time the camera passes z14; the rail button overrides it.

The extrusion is deliberately **58% opaque**: solid enough to read as a city,
translucent enough that the tract colour underneath still tells you what the
block costs. Streets are re-drawn over the mass at 40% opacity for the same
reason — without a street grid the blocks read as foam, and at full opacity the
grid shoots through the towers and flattens them again.

Street trees are the 2015 NYC Street Tree Census (`uvpi-gqnh`, 683,788 trees),
decimated 1-in-4 to 170,947 points by `scripts/fetch_trees.py` and fetched
lazily the first time the camera passes z13.8. The canopy layer sits *above*
the extrusion: MapLibre depth-tests 3D against 3D, so a layer beneath the
buildings is swallowed whole, whereas drawn after it the crowns read the way a
tree in front of a facade does in a pitched view.

The engine is **MapLibre GL JS 6.16.0**, vendored into `web/vendor/` so the page
does not depend on a CDN at runtime. v6 is published as an ES module only
(`maplibre-gl.mjs` + its `maplibre-gl-worker.mjs`), which has two consequences:
the app script is `type="module"`, and the page must be served over http —
opening `web/index.html` from disk fails with a CORS error on the import.
`scripts/serve.py` (systemd: `housing-explorer.service`, port 3118) serves the
build and registers the `.mjs` MIME type that `python3 -m http.server` omits.

v6 also brings the `sky` layer the v4 build could not have, so the horizon now
carries the same paper-toned haze the rest of the shell uses.

### Amenities

Parks and bikeways are the first layers on the map that describe a place
rather than a price, and neither exists in the basemap at a useful scale:
Positron washes every park the same pale green and drops greenways entirely.

Parks ship as every open space over **10 acres**, from two records stitched at
the city line. Inside the five boroughs the shapes, acreage and waterfront flag
are NYC Parks' own property records, so a flagship park is the agency's
classification and not an inference. Outside the city the polygons are
OpenStreetMap, with acreage measured in a local metric projection and
*waterfront* assigned only where the park edge comes within 120 m of a mapped
water body or the coastline — an intersection test, not a name match. OSM
shapes that merely re-trace a city park are dropped by a 50%-overlap test, so
nothing is drawn twice. Waterfront parks take a blue-green fill; inland parks
stay green; the fill is translucent enough that the tract colour still reads
through, because a big park should look like a hole in the rents, not a hole in
the map.

Bikeways are NYC DOT's current routes, facility classes I and II — greenways,
protected and off-street paths, striped lanes. Class III (shared and signed
street) is deliberately excluded: a painted shared lane is a designation, not
infrastructure. Class I draws from z10.4, class II only from z12.2, so the
picture at metro scale is the protected network rather than a web of bike
lanes. Named long-distance routes from OSM `route=bicycle` relations — the
East Coast Greenway and its kin — draw wider underneath and label along the
line. All of it is inserted beneath the transit casing: where a greenway shares
a bridge with a rail line, the rail still reads first.

Both files are fetched once, after the first paint, so the map is interactive
before the amenities land.

### Park access

The park-access view answers a question the rent choropleth cannot: how much of
a neighbourhood actually sits within a walk of a park worth walking to. Every
PLUTO tax lot — 857,347 records in the 26v2 release — is matched to the nearest
*major* park, and the straight-line distance from the lot's coordinate to that
park's boundary is measured in a local metric projection.

*Major* means 100 acres or more, or a waterfront park of at least 20 acres. The
acreage test alone would miss the river parks; the register's own waterfront
flag alone would let in community gardens and traffic islands, since it fires
on any park in a waterfront zone. Both conditions are combined into a `major`
property on `web/parks.geojson` by `scripts/fetch_amenities.py`, so the access
analysis and the park styling share one definition.

Two thresholds are reported, 400 m (about five minutes) and 800 m (about ten).
Tract figures are the share of *lots*, not of land or of people: a tract of
large industrial parcels and a tract of narrow rowhouses count the same way, so
the lot count travels with the figure in the detail card. Lots within 400 m of
a major park are 18.8% citywide; within 800 m, 37.6%. By borough at 800 m:
Manhattan 62%, Staten Island 69%, the Bronx 42%, Queens 32%, Brooklyn 26%.

**This is a screen, not a network walk.** Straight-line distance ignores the
river, rail yard or expressway between a lot and a park, so it overstates
access on waterfront edges and along the big cuts; a routed pedestrian walk
would show lower figures there.

PLUTO stops at the city line, which left the 1,141 tracts in Nassau, Suffolk,
Westchester, Bergen, Essex, Hudson, Passaic, Union and Middlesex blank.
`scripts/fetch_park_access_region.py` fills them with the finest denominator the
Census publishes for the whole region: 2020 census block groups, weighted by
2020 decennial population. Each block group's centre is run against the same
major-park set with the same thresholds, and the result rolls up to its 2020
tract — so outside the city the figure is the share of *residents* within the
walk, not the share of lots. Just under five million people live in those
tracts; 8.6% are within 400 m of a major park and 19.4% within 800 m, against
18.8% and 37.6% across the city's 857,271 lots.

The two denominators are checked against each other rather than assumed
compatible. On the 2,281 tracts where both exist the tract-level shares agree to
a median difference of 0 points, a mean of 3.4, r = 0.978 — the seam at the city
line can be read straight through. The bias is not quite zero though: weighted
across those same tracts the block-group method reads 20.8% and 41.5% against
the lots' 18.2% and 36.9%, about two points higher, because a block-group centre
sits nearer the middle of where people live than a lot coordinate does. The block-group method is coarser where block
groups are large, so exurban tracts in Suffolk and Middlesex carry more
approximation than a Brooklyn block does.

The park set outside the city comes from OpenStreetMap, stitched to the NYC
Parks register at the city line; the regional destinations that dominate the
suburban numbers — Palisades, South Mountain, Watchung, Garret Mountain,
Harriman, Liberty State Park, Branch Brook, Eisenhower — are all present, but
OSM polygon areas run small where the mapping is thin (Brookdale Park is mapped
at 65 acres against its 121, so it just misses the 100-acre cut).

`scripts/fetch_park_access.py` writes the lot-level table
(`data/parcels_park_access.csv`, 92 MB, not committed) and the per-tract
roll-up the map reads (`data/park_access.json`); the block-group pass writes
`data/park_access_region.json`.

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
