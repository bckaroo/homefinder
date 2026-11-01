#!/usr/bin/env python3
"""Community amenities: parks / open space and major bikeway corridors.

Two source families, deliberately mixed:

  * Inside the five boroughs we take the city's own records -- NYC Parks
    Properties carries surveyed acreage, the agency's park class and an
    explicit waterfront flag, so tiers there are authoritative rather than
    inferred.
  * Outside the city we take OpenStreetMap, because no single regional parks
    dataset covers 31 counties. Area and waterfront status are *computed*:
    acreage from a local metric projection, waterfront from an intersection
    test against OSM water polygons within 120 m of the park edge.

Bikeways follow the same split: NYC DOT's Bicycle Routes for the city (facility
classes I and II only -- class III is shared/signed street, not infrastructure),
and OSM `route=bicycle` relations for the named regional corridors.

Outputs (copied into web/ for the static app):
    data/amenities/parks.geojson
    data/amenities/bikeways.geojson

Overpass responses are cached under data/raw/osm/ so re-runs cost nothing.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, shape
from shapely.ops import polygonize, unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "amenities"
WEB = ROOT / "web"

# Study area, matching the map's ZCTA envelope in scripts/config.py.
S, W, N, E = 40.30, -74.60, 41.40, -73.30
BBOX = f"{S},{W},{N},{E}"

NYC_PARKS = "enfh-gkve"     # Parks Properties (polygons + acreage + waterfront)
NYC_BIKES = "mzxg-pwib"     # New York City Bike Routes (linework by facility class)

UA = {"User-Agent": "homefinder-amenities/1.0 (regional housing explorer)"}
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

MIN_PARK_ACRES = 10.0       # legacy: the old >=10-acre cut for the map layer
ANY_ACRES = 0.25            # smallest thing that counts as a park you can sit in.
                            # 0.25 ac ~ 1,000 m2. The NYC register carries 2,061
                            # properties and 1,849 of them are under 10 acres --
                            # every Manhattan square (Bryant 9.6, Union Square 6.5,
                            # Madison Square 6.2) was invisible to a 10-acre cut.

# OSM maps the green ribbons beside limited-access roads as leisure=park /
# protected_area: "Hutchinson River Parkway", "Belt Parkway/Shore Parkway".
# They are land, not destinations -- nobody picks an apartment to be near a
# parkway -- so a name carrying road infrastructure is not an amenity here.
NOT_A_PARK = re.compile(
    r"parkway|expressway|thruway|turnpike|\bbridge\b|\btunnel\b|\bviaduct\b"
    r"|service road|right of way|right-of-way|\bjunction\b",
    re.IGNORECASE,
)
WATERFRONT_BUFFER_M = 120   # park edge to water edge
SIMPLIFY_M = 12.0           # park outline tolerance
SIMPLIFY_LINE_M = 8.0       # bikeway tolerance


# ---------------------------------------------------------------- utilities

def log(msg: str) -> None:
    print(msg, flush=True)


def http(url: str, timeout: int = 300, data: bytes | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(key: str, query: str) -> dict:
    """Run an Overpass query once; cache the raw JSON on disk."""
    cache = RAW / "osm" / f"{key}.json"
    if cache.exists() and cache.stat().st_size > 1000:
        log(f"  {key}: cached ({cache.stat().st_size/1e6:.1f} MB)")
        return json.loads(cache.read_text())
    cache.parent.mkdir(parents=True, exist_ok=True)
    body = query.encode()
    last = None
    for attempt, endpoint in enumerate(OVERPASS * 3):
        try:
            t0 = time.time()
            log(f"  {key}: querying {endpoint} (attempt {attempt+1})")
            raw = http(endpoint, data=body)
            doc = json.loads(raw)
            cache.write_bytes(raw)
            log(f"  {key}: {len(raw)/1e6:.1f} MB in {time.time()-t0:.0f}s, "
                f"{len(doc.get('elements', []))} elements")
            return doc
        except Exception as exc:  # noqa: BLE001 - network flakiness is expected
            last = exc
            log(f"  {key}: {type(exc).__name__} {str(exc)[:120]}")
            time.sleep(8 * (attempt + 1))
    raise RuntimeError(f"Overpass failed for {key}: {last}")


# Local metric projection, good to well under a metre across the study area.
LON0, LAT0 = -73.95, 40.85
MX = 111320.0 * math.cos(math.radians(LAT0))
MY = 110540.0


def to_m(lon: float, lat: float) -> tuple[float, float]:
    return ((lon - LON0) * MX, (lat - LAT0) * MY)


def project(geom):
    from shapely.ops import transform

    return transform(lambda x, y, z=None: to_m(x, y), geom)


def round_geom(geom, nd: int = 5):
    """Round coordinates to 5 dp (~1.1 m) before serialising."""
    def walk(c):
        if isinstance(c, (list, tuple)):
            if c and isinstance(c[0], (int, float)):
                return [round(float(v), nd) for v in c]
            return [walk(v) for v in c]
        return c
    return walk(geom)


# ------------------------------------------------------------------ sources

def nyc_parks() -> list[dict]:
    path = RAW / "nyc" / "parks_properties_all.geojson"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # No acreage filter: the register's 2,061 properties are the city's own
        # definition of open space, and 1,849 of them are under 10 acres. Filtering
        # by size here silently deleted the entire pocket/square park network.
        url = (f"https://data.cityofnewyork.us/resource/{NYC_PARKS}.geojson"
               f"?$limit=50000")
        log(f"  nyc parks: downloading")
        path.write_bytes(http(url, timeout=180))
    doc = json.loads(path.read_text())
    feats = []
    for f in doc["features"]:
        p = {k.lower(): v for k, v in f["properties"].items()}
        geom = shape(f["geometry"])
        if geom.is_empty:
            continue
        feats.append({
            "name": (p.get("name311") or p.get("signname") or "Park").strip(),
            "acres": round(float(p.get("acres") or 0), 1),
            "waterfront": str(p.get("waterfront")).lower() == "true",
            "park_class": p.get("class") or "",
            "subcategory": p.get("subcategory") or "",
            "borough": p.get("borough") or "",
            "source": "NYC Parks Properties",
            "geom": geom,
        })
    log(f"  nyc parks: {len(feats)} features")
    return feats


def nyc_bikes() -> list[dict]:
    path = RAW / "nyc" / "bike_routes.geojson"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        where = urllib.parse.quote("status='Current' AND facilitycl IN ('I','II')")
        url = (f"https://data.cityofnewyork.us/resource/{NYC_BIKES}.geojson"
               f"?$where={where}&$limit=200000")
        log("  nyc bikes: downloading")
        path.write_bytes(http(url, timeout=240))
    doc = json.loads(path.read_text())
    feats = []
    for f in doc["features"]:
        p = {k.lower(): v for k, v in f["properties"].items()}
        geom = shape(f["geometry"])
        if geom.is_empty:
            continue
        feats.append({
            "name": (p.get("street") or "").strip(),
            "cls": p.get("facilitycl") or "",
            "facility": (p.get("ft_facilit") or "").strip(),
            "boro": p.get("boro") or "",
            "source": "NYC DOT",
            "geom": geom,
        })
    log(f"  nyc bikes: {len(feats)} segments")
    return feats


OSM_PARKS_Q = f"""[out:json][timeout:300];
(
  way["leisure"="park"]["name"]({BBOX});
  relation["leisure"="park"]["name"]({BBOX});
  way["leisure"="nature_reserve"]["name"]({BBOX});
  relation["leisure"="nature_reserve"]["name"]({BBOX});
  way["boundary"="protected_area"]["name"]({BBOX});
  relation["boundary"="protected_area"]["name"]({BBOX});
);
out geom;"""

OSM_WATER_Q = f"""[out:json][timeout:300];
(
  way["natural"="water"]["name"]({BBOX});
  relation["natural"="water"]["name"]({BBOX});
  way["natural"="coastline"]({BBOX});
);
out geom;"""

OSM_BIKE_Q = f"""[out:json][timeout:300];
(
  relation["route"="bicycle"]({BBOX});
);
out geom;"""


def rings_from_members(members: list[dict]) -> list:
    lines = []
    for m in members:
        g = m.get("geometry")
        if not g or len(g) < 2:
            continue
        lines.append(LineString([(pt["lon"], pt["lat"]) for pt in g]))
    if not lines:
        return []
    merged = unary_union(lines)
    return list(polygonize(merged))


def osm_parks() -> list[dict]:
    doc = overpass("parks", OSM_PARKS_Q)
    feats = []
    for e in doc["elements"]:
        tags = e.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        if e["type"] == "way":
            g = e.get("geometry") or []
            if len(g) < 4:
                continue
            ring = [(pt["lon"], pt["lat"]) for pt in g]
            geom = Polygon(ring) if ring[0] == ring[-1] else MultiPolygon()
        else:
            polys = rings_from_members(e.get("members") or [])
            geom = unary_union(polys) if polys else MultiPolygon()
        if geom.is_empty:
            continue
        kind = tags.get("leisure") or ("protected_area" if tags.get("boundary") else "")
        feats.append({
            "name": name,
            "kind": kind,
            "protection": tags.get("protect_class") or "",
            "operator": tags.get("operator") or "",
            "geom": geom,
        })
    log(f"  osm parks: {len(feats)} named polygons")
    return feats


def osm_water():
    doc = overpass("water", OSM_WATER_Q)
    polys, coords = [], []
    for e in doc["elements"]:
        tags = e.get("tags") or {}
        if e["type"] == "way":
            g = e.get("geometry") or []
            if len(g) < 2:
                continue
            line = LineString([(pt["lon"], pt["lat"]) for pt in g])
            if tags.get("natural") == "coastline":
                coords.append(line)
            elif len(g) >= 4 and g[0] == g[-1]:
                polys.append(Polygon([(pt["lon"], pt["lat"]) for pt in g]))
        else:
            polys.extend(rings_from_members(e.get("members") or []))
    log(f"  osm water: {len(polys)} polygons, {len(coords)} coastline ways")
    # Ponds disqualify themselves: "waterfront" should mean a river, bay, lake or
    # the sea, not a duck pond inside a neighbourhood park. Anything under ~100
    # acres of open water is dropped before the distance test.
    big = [p for p in polys if project(p).area >= 400_000]
    log(f"  osm water: {len(big)} water bodies >=100 ac kept for the waterfront test")
    water = unary_union([project(p) for p in big if p.is_valid]) if big else MultiPolygon()
    coast = unary_union([project(LineString(c.coords)) for c in coords]) if coords else LineString()
    return water, coast


def osm_bikes() -> list[dict]:
    doc = overpass("bike_routes", OSM_BIKE_Q)
    feats = []
    for e in doc["elements"]:
        tags = e.get("tags") or {}
        name = (tags.get("name") or "").strip()
        if not name:
            continue
        # Only the numbered/named networks are corridors: rcn regional, ncn
        # national, icn international. OSM also carries ~1,000 `lcn` local routes,
        # which are street-by-street neighbourhood designations -- exactly the
        # painted-shared-lane noise the NYC side excludes by dropping class III.
        if (tags.get("network") or "").strip() not in ("ncn", "rcn", "icn"):
            continue
        lines = []
        for m in e.get("members") or []:
            g = m.get("geometry")
            if g and len(g) >= 2:
                lines.append(LineString([(pt["lon"], pt["lat"]) for pt in g]))
        if not lines:
            continue
        feats.append({
            "name": name,
            "network": (tags.get("network") or "").strip(),
            "ref": (tags.get("ref") or "").strip(),
            "geom": MultiLineString(lines),
        })
    log(f"  osm bike corridors: {len(feats)} relations")
    return feats


# -------------------------------------------------------------------- build

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    log("fetching sources")

    nycp = nyc_parks()
    osm_p = osm_parks()
    water, coast = osm_water()
    nycb = nyc_bikes()
    osm_b = osm_bikes()

    # --- parks ------------------------------------------------------------
    log("assembling parks")
    nyc_proj = [(f, project(f["geom"])) for f in nycp]

    parks: list[dict] = []
    for f in nycp:
        # NYC Parks' property register also carries the parkway land itself
        # (Shore Parkway, Bronx River Parkway, Ocean Parkway Malls). Same rule as
        # OSM: a road corridor is not a place you choose to live near.
        if NOT_A_PARK.search(f["name"]):
            continue
        g = f["geom"].simplify(0.0)
        gp = project(g)
        parks.append({
            "type": "Feature",
            "properties": {
                "name": f["name"],
                "acres": f["acres"],
                "waterfront": 1 if f["waterfront"] else 0,
                "cls": f["park_class"].lower(),
                # "flagship" is NYC Parks' own class for the big destination
                # parks. "large" is the acreage test only: the register's
                # waterfront flag fires on community gardens and traffic islands
                # too, so it cannot promote a park into the large tier on its own.
                "tier": "flagship" if "flagship" in f["park_class"].lower()
                        else ("large" if f["acres"] >= 100
                              else ("park" if f["acres"] >= 10 else "pocket")),
                # "major" is the test used for park-access analysis: a
                # destination you would walk to. 100 acres, or a waterfront park
                # of at least 20 acres (a river edge is worth a walk even when
                # the ribbon is narrow).
                "major": 1 if (f["acres"] >= 100
                               or (f["waterfront"] and f["acres"] >= 20)) else 0,
                # "any" is the everyday-access test: is there *a park* within a
                # walk, not just a destination park. 0.25 ac floor drops bare
                # triangles and median strips while keeping every square.
                "any": 1 if f["acres"] >= ANY_ACRES else 0,
                "src": "nyc",
            },
            "geometry": {"type": f["geom"].geom_type,
                         "coordinates": round_geom(f["geom"].__geo_interface__["coordinates"])},
        })

    # STRtree so the duplicate test stays linear rather than 5k x 2k.
    nyc_geoms = [g for _, g in nyc_proj]
    tree = STRtree(nyc_geoms)

    water_parts = list(water.geoms) if water.geom_type == "MultiPolygon" else [water]
    water_parts = [p for p in water_parts if not p.is_empty]
    wtree = STRtree(water_parts)

    # A park is often mapped twice in OSM -- once as leisure=park, once inside a
    # protected_area boundary -- which would put the same name on the map twice.
    # Keep the largest polygon per name and drop the rest.
    best: dict[str, tuple[float, dict]] = {}
    for f in osm_p:
        if NOT_A_PARK.search(f["name"]):
            continue
        key = " ".join(f["name"].lower().split())
        area = project(f["geom"]).area
        if key not in best or area > best[key][0]:
            best[key] = (area, f)
    osm_p = [f for _, f in best.values()]
    log(f"  osm parks: {len(osm_p)} after name de-duplication")

    kept = 0
    for f in osm_p:
        gp = project(f["geom"])
        if gp.is_empty:
            continue
        area_m2 = gp.area
        acres = area_m2 / 4046.8564224
        if acres < ANY_ACRES:
            continue

        # Skip OSM shapes that merely re-trace a city park we already have.
        dup = False
        for idx in tree.query(gp):
            other = nyc_geoms[int(idx)]
            inter = gp.intersection(other).area
            if area_m2 and inter / area_m2 > 0.5:
                dup = True
                break
        if dup:
            continue

        waterfront = 0
        for idx in wtree.query(gp.buffer(WATERFRONT_BUFFER_M)):
            if gp.distance(water_parts[int(idx)]) <= WATERFRONT_BUFFER_M:
                waterfront = 1
                break
        if not waterfront and not coast.is_empty:
            waterfront = 1 if gp.distance(coast) <= WATERFRONT_BUFFER_M else 0

        simp = gp.simplify(SIMPLIFY_M, preserve_topology=True)
        if simp.is_empty:
            simp = gp
        simp = MultiPolygon([simp]) if simp.geom_type == "Polygon" else simp
        # back to lon/lat
        from shapely.ops import transform

        ll = transform(lambda x, y, z=None: (x / MX + LON0, y / MY + LAT0), simp)
        parks.append({
            "type": "Feature",
            "properties": {
                "name": f["name"],
                "acres": round(acres, 1),
                "waterfront": waterfront,
                "cls": f["kind"],
                "tier": ("large" if acres >= 100
                         else ("park" if acres >= 10 else "pocket")),
                "major": 1 if (acres >= 100 or (waterfront and acres >= 20)) else 0,
                "any": 1 if acres >= ANY_ACRES else 0,
                "src": "osm",
            },
            "geometry": {"type": ll.geom_type,
                         "coordinates": round_geom(ll.__geo_interface__["coordinates"])},
        })
        kept += 1

    log(f"  parks: {len(nycp)} NYC + {kept} OSM = {len(parks)} total")

    # labels: only the genuinely large shapes earn a name on the map
    parks.sort(key=lambda f: -f["properties"]["acres"])
    parks_doc = {"type": "FeatureCollection", "features": parks}

    # --- label points ------------------------------------------------------
    # MapLibre places one label per polygon *part*, so an archipelago -- Pelham
    # Bay Park, Gateway NRA, Jamaica Bay -- prints its name once per island.
    # Labels get their own point source instead: exactly one anchor per park
    # name, taken from the largest part of the largest polygon bearing that name.
    from shapely.geometry import shape as _shape

    label_best: dict[str, tuple[float, dict]] = {}
    for f in parks:
        key = " ".join(str(f["properties"]["name"]).lower().split())
        ac = float(f["properties"]["acres"] or 0)
        if key not in label_best or ac > label_best[key][0]:
            label_best[key] = (ac, f)

    label_feats = []
    for ac, f in label_best.values():
        g = _shape(f["geometry"])
        parts = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        parts = [p for p in parts if not p.is_empty] or [g]
        pt = max(parts, key=lambda p: p.area).representative_point()
        label_feats.append({
            "type": "Feature",
            "properties": {"name": f["properties"]["name"], "acres": ac,
                           "waterfront": f["properties"]["waterfront"]},
            "geometry": {"type": "Point",
                         "coordinates": [round(pt.x, 5), round(pt.y, 5)]},
        })
    labels_doc = {"type": "FeatureCollection", "features": label_feats}
    log(f"  park labels: {len(label_feats)} anchors (one per name)")

    # --- bikeways ---------------------------------------------------------
    log("assembling bikeways")
    bike_feats: list[dict] = []
    from shapely.ops import transform as _tf

    for f in nycb:
        gp = project(f["geom"]).simplify(SIMPLIFY_LINE_M, preserve_topology=False)
        ll = _tf(lambda x, y, z=None: (x / MX + LON0, y / MY + LAT0), gp)
        bike_feats.append({
            "type": "Feature",
            "properties": {"name": f["name"], "cls": f["cls"],
                           "facility": f["facility"], "net": "nyc"},
            "geometry": {"type": ll.geom_type,
                         "coordinates": round_geom(ll.__geo_interface__["coordinates"])},
        })
    for f in osm_b:
        lines = [project(LineString(l.coords)).simplify(SIMPLIFY_LINE_M, preserve_topology=False)
                 for l in f["geom"].geoms]
        gp = MultiLineString(lines)
        ll = _tf(lambda x, y, z=None: (x / MX + LON0, y / MY + LAT0), gp)
        bike_feats.append({
            "type": "Feature",
            "properties": {"name": f["name"], "cls": "R",
                           "facility": f["network"] or "regional route", "net": "osm"},
            "geometry": {"type": ll.geom_type,
                         "coordinates": round_geom(ll.__geo_interface__["coordinates"])},
        })
    log(f"  bikeways: {len(nycb)} NYC + {len(osm_b)} regional = {len(bike_feats)} features")
    bike_doc = {"type": "FeatureCollection", "features": bike_feats}

    # --- write ------------------------------------------------------------
    for name, doc in (("parks.geojson", parks_doc), ("parks-labels.geojson", labels_doc),
                      ("bikeways.geojson", bike_doc)):
        p = OUT / name
        p.write_text(json.dumps(doc, separators=(",", ":")))
        (WEB / name).write_bytes(p.read_bytes())
        log(f"  wrote {p.relative_to(ROOT)} ({p.stat().st_size/1e6:.2f} MB) -> web/{name}")

    wf = sum(1 for f in parks if f["properties"]["waterfront"])
    log(f"done: {len(parks)} parks ({wf} waterfront), {len(bike_feats)} bikeway segments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
