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
