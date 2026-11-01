"""Map geometry for HomeFinder.

Why this module exists
----------------------
The map used to render `cb_2023_*_tract_500k.shp` simplified at 0.0012 deg
(~130 m). Both halves of that were wrong at street zoom: the cartographic
boundary file is generalised to 1:500,000, and a 130 m Douglas-Peucker pass
turns every block into a long straight chord. Zoomed in, neighbourhoods looked
like janky quadrilaterals that ignored the street grid.

So: full-resolution TIGER/Line tracts (1:100,000, street-following), clipped
against real water polygons and simplified at street scale in metres rather
than degrees.

Clipping matters because TIGER tracts are *not* coast-clipped — a shoreline
tract's polygon runs out over the harbour, and shading it paints colour on
open water. `cb_*_500k` was coast-clipped, which is the one thing it was good
for; TIGER has to be cut by hand. AREAWATER supplies the cut, restricted to
big water (ocean, bay, lake, reservoir) so that storm drains and brook
polygons do not shave slivers off every block. Water is buffered outward
slightly before the cut so no hairline of colour is left sitting on the
basemap's water at high zoom.
"""
from __future__ import annotations

import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import mapping

import config as C

# MTFCC codes in TIGER AREAWATER that read as "water body" on this map.
WATER_MTFCC = ("H2030", "H2040", "H2051", "H2053", "H2081")
# Rivers live in H3010 (Stream/River) alongside brook-sized drainage polygons,
# and in New York the *big* rivers are H3010: the Hudson is 13.6 km2 of H3010,
# the East River 8.0 km2. Excluding H3010 outright left both rivers unclipped,
# which is exactly the bug this floor replaces dropping the class entirely.
# So take H3010 only when the polygon is big enough to be a river rather than a
# drain, so a creek crossing a block does not shave a channel through it.
STREAM_MTFCC = "H3010"

# EPSG:32618 (UTM 18N) covers NYC and the NJ/NY metro: metres for simplification.
UTM = 32618


def _read_tracts() -> gpd.GeoDataFrame:
    frames = []
    for path in (C.TIGER_TRACT_NY, C.TIGER_TRACT_NJ):
        frames.append(gpd.read_file(
            C.require(path, "run scripts/fetch_data.py")
        )[["GEOID", "ALAND", "AWATER", "geometry"]])
    geo = pd.concat(frames, ignore_index=True)
    geo = gpd.GeoDataFrame(geo, geometry="geometry", crs=frames[0].crs)
    geo["GEOID"] = geo.GEOID.astype(str)
    return geo.to_crs(UTM)


def _county_water(fips: str, buffer_m: float = 0.0) -> shapely.Geometry:
    """Union of big water polygons for one county, in UTM metres."""
    path = C.AREAWATER_DIR / f"tl_2024_{fips}_areawater.zip"
    if not path.exists():
        _fetch_areawater(fips)
    w = gpd.read_file(path).to_crs(UTM)
    # Area floors only make sense once projected: `w.area` in degrees is
    # meaningless. Big rivers (Hudson, East River, Harlem) clear the floor by
    # orders of magnitude; storm drains and brooks do not.
    keep = w.MTFCC.isin(WATER_MTFCC) | (
        (w.MTFCC == STREAM_MTFCC) & (w.area >= C.MIN_STREAM_M2)
    )
    w = w[keep]
    if w.empty:
        return shapely.GeometryCollection()
    geom = shapely.union_all(shapely.make_valid(w.geometry.values))
    return geom.buffer(buffer_m, quad_segs=2) if buffer_m else geom


def _fetch_areawater(fips: str) -> None:
    import urllib.request

    C.AREAWATER_DIR.mkdir(parents=True, exist_ok=True)
    url = f"https://www2.census.gov/geo/tiger/TIGER2024/AREAWATER/tl_2024_{fips}_areawater.zip"
    dest = C.AREAWATER_DIR / f"tl_2024_{fips}_areawater.zip"
    print(f"  fetching {dest.name}")
    urllib.request.urlretrieve(url, dest)
    with zipfile.ZipFile(dest) as z:
        z.testzip()


def _drop_small_parts(geom, min_area_m2: float):
    """Discard speck islands: they cost vertices and read as map noise."""
    if geom is None or geom.is_empty:
        return geom
    if geom.geom_type == "Polygon":
        return geom if geom.area >= min_area_m2 else None
    parts = [p for p in geom.geoms if p.area >= min_area_m2]
    if not parts:
        return None
    return shapely.MultiPolygon(parts) if len(parts) > 1 else parts[0]


def _round_coords(coords, nd: int = 5):
    if isinstance(coords, (int, float)):
        return round(float(coords), nd)
    return [_round_coords(c, nd) for c in coords]


def build(keep: pd.DataFrame, log=print):
    """Return (geodataframe in EPSG:4326, n_water_body_tracts_removed).

    `keep` is the joined tract table; only its `geoid` column is used to decide
    which tracts make the map.
    """
    geo = _read_tracts()

    # Tracts that are essentially water carry no housing, yet ACS still
    # tabulates a rent median for them and the router still returns a commute
    # from their centroid. Tracts that merely *contain* a lot of water (Bay
    # Ridge, Sunset Park, Broad Channel) are real neighbourhoods and stay.
    tot = geo.ALAND + geo.AWATER
    geo["water_share"] = (geo.AWATER / tot.replace(0, np.nan)).fillna(0.0)
    water_body = geo.water_share >= C.WATER_SHARE_MAX
    dropped = set(geo.GEOID[water_body])
    geo = geo[~water_body]

    m = geo.merge(keep, left_on="GEOID", right_on="geoid", how="inner")
    m = gpd.GeoDataFrame(m.drop(columns=["GEOID"]), geometry="geometry", crs=UTM).reset_index(drop=True)
    n_water = len(dropped & set(keep.geoid))
    log(f"  tracts on map: {len(m)} (water-body tracts removed: {n_water}, "
        f">= {C.WATER_SHARE_MAX:.0%} water)")

    # Cut the water out county by county. Doing it per county keeps each
    # difference against a manageable union instead of one metro-wide monster.
    fips_col = m.geoid.str[:5]
    before = len(m)
    for fips in sorted(fips_col.unique()):
        water = _county_water(fips, buffer_m=C.WATER_BUFFER_M)
        if water.is_empty:
            continue
        idx = m.index[fips_col == fips]
        cut = shapely.difference(
            shapely.make_valid(m.loc[idx, "geometry"].values), water
        )
        m.loc[idx, "geometry"] = [
            _drop_small_parts(g, C.MIN_PART_M2) for g in cut
        ]
    m = m[m.geometry.notna() & ~m.geometry.is_empty].copy()

    # Simplify in metres, *as a coverage*: every tract is simplified with its
    # neighbours' shared edges held identical. Simplifying each polygon on its
    # own (the obvious way) pulls both sides of a shared edge apart into
    # different chords, which leaves hairline gaps and stair-steps along every
    # boundary — the "janky" look at street zoom. 1:500k source plus a 130 m
    # pass was the rest of that complaint; at 15 m a block keeps its corners.
    try:
        simplified = shapely.coverage_simplify(
            shapely.make_valid(m.geometry.values), tolerance=C.SIMPLIFY_M
        )
        m["geometry"] = [g if g is not None else o for g, o in zip(simplified, m.geometry)]
        log(f"  simplified as a coverage at {C.SIMPLIFY_M} m (shared edges preserved)")
    except Exception as exc:  # geometry that is not a clean coverage
        log(f"  coverage simplify unavailable ({exc}); falling back to per-feature")
        m["geometry"] = m.geometry.simplify(C.SIMPLIFY_M, preserve_topology=True)
    # Second pass: simplify at 15 m pulls a long shoreline back over the water
    # by up to the tolerance (a 15 m band along eight kilometres of Manhattan
    # waterfront is ~0.12 km2 of colour floating on the river). Re-cut exactly
    # at the water edge so the only error left points *away* from the water.
    for fips in sorted(fips_col.unique()):
        water = _county_water(fips, buffer_m=0)
        if water.is_empty:
            continue
        idx = m.index[fips_col == fips]
        cut = shapely.difference(
            shapely.make_valid(m.loc[idx, "geometry"].values), water
        )
        m.loc[idx, "geometry"] = [
            _drop_small_parts(g, C.MIN_PART_M2) for g in cut
        ]
    m = m[m.geometry.notna() & ~m.geometry.is_empty].copy()

    m = m.to_crs(4326)

    log(f"  water clipping dropped {before - len(m)} tracts that were water only")
    return m, n_water


def feature_collection(m: gpd.GeoDataFrame, props) -> dict:
    """`props` maps a row to its GeoJSON properties dict."""
    feats = []
    for _, r in m.iterrows():
        geom = mapping(r.geometry)
        feats.append({
            "type": "Feature",
            "properties": props(r),
            "geometry": {
                "type": geom["type"],
                "coordinates": _round_coords(geom["coordinates"]),
            },
        })
    return {"type": "FeatureCollection", "features": feats}
