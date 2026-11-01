#!/usr/bin/env python3
"""Community & culture access: commercial districts, shop clusters, civic anchors.

Three questions, deliberately kept apart because they answer different things:

  1. DESIGNATION — where the zoning map says commerce belongs. Inside the five
     boroughs only: every tax lot carrying a commercial overlay (the city's
     device for neighbourhood retail inside a residence district), every lot
     zoned to a commercial district (C1-C8), and every lot inside a Business
     Improvement District polygon.

  2. BEHAVIOUR — where commerce actually is. A grid pass over OpenStreetMap
     shop/service points marks every place with at least MIN_SHOPS establishments
     within CLUSTER_M of one another. This works the same in Westchester as in
     Brooklyn, which designation cannot, and it is what the out-of-city tracts
     are measured against.

  3. CULTURE — libraries, museums, community and arts centres, theatres,
     cinemas and public markets, again from OSM so the definition is uniform
     across all 31 counties. Places of worship are counted separately: they are
     civic anchors, but they are dense enough to swamp the measure.

Distances are straight-line from a 2020 block-group centre (outside the city) or
the tax lot's coordinate (inside it) to the nearest anchor point, in the same
local metric projection the park passes use. Same thresholds: 400 m (about five
minutes' walk) and 800 m (about ten). A screen, not a routed walk.

Outputs:
    data/community/commerce_anchors.geojson   zone + cluster anchors (points)
    data/community/culture.geojson            culture venues (+ worship flag)
    data/commerce_access.json                 per tract, lot denominator (NYC)
    data/commerce_access_region.json          per tract, population denominator
"""

from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request

import numpy as np
import shapely
from shapely.geometry import Point, shape
from shapely.ops import transform as sh_transform

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
OUT = os.path.join(DATA, "community")

# study area, matching scripts/fetch_amenities.py and config.py
S, W, N, E = 40.30, -74.60, 41.40, -73.30
BBOX = f"{S},{W},{N},{E}"

NEAR_M = 400.0
WALK_M = 800.0
CLUSTER_M = 250.0      # neighbourhood radius for the shop-cluster test
MIN_SHOPS = 10         # establishments within CLUSTER_M => "a commercial area"
GRID_M = 150.0         # cluster candidate spacing

COUNTIES = ["34003", "34013", "34017", "34023", "34031", "34039",
            "36005", "36047", "36059", "36061", "36081", "36085",
            "36103", "36119"]
NYC = {"36061", "36005", "36047", "36081", "36085"}

UA = {"User-Agent": "homefinder-community/1.0 (regional housing explorer)"}
OVERPASS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
]

SHOPS = '[shop]'
CULTURE = ('[amenity~"^(library|museum|community_centre|arts_centre|theatre|'
           'cinema|marketplace)$"]')
WORSHIP = '[amenity=place_of_worship]'

# local metric projection, good to well under a metre across the study area
LAT0, LON0 = 40.70, -73.95
MX = 111320.0 * math.cos(math.radians(LAT0))
MY = 110540.0


def to_xy(x, y, z=None):
    return ((np.asarray(x) - LON0) * MX, (np.asarray(y) - LAT0) * MY)


def to_m(lon, lat):
    return ((lon - LON0) * MX, (lat - LAT0) * MY)


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# OSM
# --------------------------------------------------------------------------

def http(url, data=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def overpass(key, selector, timeout=280):
    """Run one Overpass query for every element matching `selector`; cache it."""
    cache = os.path.join(RAW, "osm", f"{key}.json")
    if os.path.exists(cache) and os.path.getsize(cache) > 1000:
        log(f"  {key}: cached ({os.path.getsize(cache)/1e6:.1f} MB)")
        return json.load(open(cache))
    q = (f"[out:json][timeout:{timeout}];"
         f"(nwr{selector}({BBOX}););out center;")
    body = urllib.parse.urlencode({"data": q}).encode()
    last = None
    for attempt in range(len(OVERPASS) * 2):
        ep = OVERPASS[attempt % len(OVERPASS)]
        try:
            raw = http(ep, data=body, timeout=timeout + 60)
            doc = json.loads(raw)
            open(cache, "wb").write(raw)
            log(f"  {key}: {len(raw)/1e6:.1f} MB, "
                f"{len(doc.get('elements', []))} elements")
            return doc
        except Exception as exc:  # noqa: BLE001 - network flakiness is expected
            last = exc
            log(f"  {key}: {type(exc).__name__} {str(exc)[:110]}")
            time.sleep(10 * (attempt + 1))
    raise SystemExit(f"Overpass failed for {key}: {last}")


def element_points(doc):
    """(lon, lat, tags) for every node/way/relation in an Overpass payload."""
    out = []
    for el in doc.get("elements", []):
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            c = el.get("center") or {}
            lat, lon = c.get("lat"), c.get("lon")
        if lat is None or lon is None:
            continue
        out.append((lon, lat, el.get("tags") or {}))
    return out


# --------------------------------------------------------------------------
# NYC designation: zoning lots + BIDs
# --------------------------------------------------------------------------

def bbl_key(v):
    """PLUTO writes BBL as '1000010010.00000000'; the zoning file as an integer."""
    return str(v).split(".")[0]


def nyc_designation():
    """Lots flagged by the zoning map or sitting inside a BID."""
    zp = os.path.join(RAW, "zoning_lots.csv")
    pp = os.path.join(RAW, "pluto_lots.csv")
    for p in (zp, pp):
        if not os.path.exists(p):
            raise SystemExit(f"missing {p}")

    zone = {}
    with open(zp, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            bbl = bbl_key(row["BBL"])
            ov = (row.get("Commercial Overlay 1", "") or "").strip() + \
                 (row.get("Commercial Overlay 2", "") or "").strip()
            zd = " ".join((row.get(f"Zoning District {i}", "") or "").strip()
                          for i in (1, 2, 3, 4)).strip()
            cls = []
            if ov:
                cls.append("overlay")
            # C1-C8 are the commercial districts proper; a C district mapped on
            # the same lot as a residence district arrives as "R6A C2-4" here.
            if any(tok.upper().startswith("C") and tok[1:2].isdigit()
                   for tok in zd.replace("/", " ").split()):
                cls.append("C")
            if cls:
                zone[bbl] = cls
    log(f"zoning lots: {len(zone):,} carry a commercial overlay or C district")

    bids = []
    bp = os.path.join(RAW, "nyc", "bids.geojson")
    if os.path.exists(bp):
        for f in json.load(open(bp))["features"]:
            if f.get("geometry"):
                bids.append(sh_transform(to_xy, shape(f["geometry"])))
        log(f"BID polygons: {len(bids)}")
    bid_tree = shapely.STRtree(bids) if bids else None

    lots = []
    with open(pp, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            bbl = bbl_key(row["bbl"])
            cls = zone.get(bbl, [])
            in_bid = False
            if bid_tree is not None:
                p = Point(*to_m(lon, lat))
                in_bid = len(bid_tree.query(p, predicate="intersects")) > 0
            if cls or in_bid:
                lots.append(dict(bbl=bbl, lon=lon, lat=lat,
                                 cls=list(cls) + (["BID"] if in_bid else []),
                                 landuse=row.get("landuse")))
    log(f"NYC anchor lots: {len(lots):,} "
        f"(BID-only {sum(1 for l in lots if l['cls'] == ['BID']):,})")
    return lots


# --------------------------------------------------------------------------
# Behaviour: OSM shop clusters
# --------------------------------------------------------------------------

def shop_clusters(points):
    """Shop points that sit inside a dense enough neighbourhood of shops.

    A 150 m grid is laid over the study area; each cell is a candidate, and a
    candidate qualifies when MIN_SHOPS establishments lie within CLUSTER_M of
    its centre. Every shop inside a qualifying cell becomes an anchor, so the
    measured distance is to real shops in a real cluster rather than to a
    synthesised centroid.
    """
    if not points:
        return []
    xy = np.array([[to_m(lon, lat)[0], to_m(lon, lat)[1]] for lon, lat, _ in points])
    cell = np.floor(xy / GRID_M).astype(int)
    lookup = {}
    for i, (cx, cy) in enumerate(cell):
        lookup.setdefault((cx, cy), []).append(i)

    r2 = CLUSTER_M ** 2
    keep = set()
    for (cx, cy), idx in lookup.items():
        # neighbours: a 250 m radius touches at most 2 cells either way at 150 m
        near = []
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                near.extend(lookup.get((cx + dx, cy + dy), ()))
        if len(near) < MIN_SHOPS:
            continue
        cxo, cyo = (cx + 0.5) * GRID_M, (cy + 0.5) * GRID_M
        sub = xy[near]
        d2 = (sub[:, 0] - cxo) ** 2 + (sub[:, 1] - cyo) ** 2
        inside = [near[j] for j in np.nonzero(d2 <= r2)[0]]
        if len(inside) >= MIN_SHOPS:
            keep.update(inside)
    log(f"shop clusters: {len(keep):,} of {len(points):,} shops sit in a cluster "
        f"(>= {MIN_SHOPS} within {CLUSTER_M:.0f} m)")
    return sorted(keep)


# --------------------------------------------------------------------------
# measurement
# --------------------------------------------------------------------------

def nearest(tree, pts, p):
    i = int(tree.nearest(p))
    return float(p.distance(pts[i]))


def write_cells(cell=200.0, src=None):
    """Anchor points to a 200 m grid: one square per place with commerce.

    135k lots and shops would be a dead weight in the browser; the grid keeps
    the shape of the commercial fabric at about a tenth of the bytes.
    """
    src = src or os.path.join(OUT, "commerce_anchors.geojson")
    feats = json.load(open(src))["features"]
    grid = {}
    for f in feats:
        lon, lat = f["geometry"]["coordinates"]
        x, y = to_m(lon, lat)
        key = (int(x // cell), int(y // cell))
        g = grid.setdefault(key, [0, 0])
        g[0 if f["properties"]["src"] == "zone" else 1] += 1
    cells = []
    for (cx, cy), (z, c) in grid.items():
        x0, y0 = cx * cell, cy * cell
        lon0, lat0 = x0 / MX + LON0, y0 / MY + LAT0
        lon1, lat1 = (x0 + cell) / MX + LON0, (y0 + cell) / MY + LAT0
        kind = "both" if z and c else ("zone" if z else "cluster")
        cells.append(dict(type="Feature",
                          geometry=dict(type="Polygon",
                                        coordinates=[[[round(lon0, 5), round(lat0, 5)],
                                                      [round(lon1, 5), round(lat0, 5)],
                                                      [round(lon1, 5), round(lat1, 5)],
                                                      [round(lon0, 5), round(lat1, 5)],
                                                      [round(lon0, 5), round(lat0, 5)]]]),
                          properties=dict(kind=kind, n=z + c)))
    web = os.path.join(HERE, "web")
    os.makedirs(web, exist_ok=True)
    json.dump(dict(type="FeatureCollection", features=cells),
              open(os.path.join(web, "commerce_cells.geojson"), "w"),
              separators=(",", ":"))
    log(f"wrote web/commerce_cells.geojson: {len(cells):,} cells of {cell:.0f} m")
    return len(cells)


def write_bids(tol=0.00005):
    """BID outlines for the map: the source is drawn at survey resolution."""
    src = os.path.join(RAW, "nyc", "bids.geojson")
    if not os.path.exists(src):
        return
    out = []
    for f in json.load(open(src))["features"]:
        if not f.get("geometry"):
            continue
        g = shape(f["geometry"]).simplify(tol, preserve_topology=True)
        out.append(dict(type="Feature",
                        geometry=json.loads(json.dumps(g.__geo_interface__)),
                        properties={"name": f["properties"].get("bid_name")
                                    or f["properties"].get("BID_NAME") or ""}))
    path = os.path.join(HERE, "web", "bids.geojson")
    json.dump(dict(type="FeatureCollection", features=out), open(path, "w"),
              separators=(",", ":"))
    log(f"wrote web/bids.geojson: {len(out)} BIDs, {os.path.getsize(path)/1e3:.0f} KB")


def main():
    os.makedirs(OUT, exist_ok=True)
    tracts = json.load(open(os.path.join(DATA, "tracts.geojson")))["features"]
    want = {f["properties"]["geoid"] for f in tracts
            if f["properties"]["geoid"][:5] in COUNTIES}

    # ---- anchors ---------------------------------------------------------
    lots = nyc_designation()
    zone_pts = [Point(*to_m(l["lon"], l["lat"])) for l in lots]
    zone_tree = shapely.STRtree(zone_pts) if zone_pts else None

    log("shops: querying Overpass")
    shops = element_points(overpass("shops_region", SHOPS))
    keep = shop_clusters(shops)
    cluster_pts = [Point(*to_m(shops[i][0], shops[i][1])) for i in keep]
    cluster_tree = shapely.STRtree(cluster_pts) if cluster_pts else None
    log(f"cluster anchors: {len(cluster_pts):,}")

    log("culture: querying Overpass")
    cult = element_points(overpass("culture_region", CULTURE))
    worship = element_points(overpass("worship_region", WORSHIP))
    cult_pts = [Point(*to_m(lon, lat)) for lon, lat, _ in cult]
    tree_c = shapely.STRtree(cult_pts) if cult_pts else None
    wor_pts = [Point(*to_m(lon, lat)) for lon, lat, _ in worship]
    tree_w = shapely.STRtree(wor_pts) if wor_pts else None
    log(f"culture: {len(cult_pts):,} venues, {len(wor_pts):,} places of worship")

    # ---- anchors to disk (for the map layer) -----------------------------
    feats = []
    for l in lots:
        feats.append(dict(type="Feature",
                          geometry=dict(type="Point",
                                        coordinates=[round(l["lon"], 5),
                                                     round(l["lat"], 5)]),
                          properties=dict(cls="+".join(l["cls"]), src="zone")))
    for i in keep:
        lon, lat, tags = shops[i]
        feats.append(dict(type="Feature",
                          geometry=dict(type="Point",
                                        coordinates=[round(lon, 5), round(lat, 5)]),
                          properties=dict(cls="cluster", src="osm",
                                          name=tags.get("name", ""))))
    json.dump(dict(type="FeatureCollection", features=feats),
              open(os.path.join(OUT, "commerce_anchors.geojson"), "w"))
    log(f"wrote commerce_anchors.geojson: {len(feats):,} points")
    write_cells()
    write_bids()

    cfeats = []
    for lon, lat, tags in cult:
        cfeats.append(dict(type="Feature",
                           geometry=dict(type="Point",
                                         coordinates=[round(lon, 5), round(lat, 5)]),
                           properties=dict(kind=tags.get("amenity", ""),
                                           name=tags.get("name", ""))))
    json.dump(dict(type="FeatureCollection", features=cfeats),
              open(os.path.join(OUT, "culture.geojson"), "w"))
    log(f"wrote culture.geojson: {len(cfeats):,} venues")

    # ---- NYC: lot denominator -------------------------------------------
    nyc_out = {}
    agg = {}
    pluto_lots = []
    with open(os.path.join(RAW, "pluto_lots.csv"), newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            try:
                lat, lon = float(row["latitude"]), float(row["longitude"])
            except (TypeError, ValueError):
                continue
            pluto_lots.append((lon, lat, row.get("bct2020") or ""))
    log(f"PLUTO lots with coordinates: {len(pluto_lots):,}")

    # PLUTO's bct2020 is the CT2010-style key: one borough digit plus six tract
    # digits ("1000500"), not an 11-digit geoid. Same mapping the park pass uses.
    BORO = {"1": "36061", "2": "36005", "3": "36047",
            "4": "36081", "5": "36085"}
    for lon, lat, bct in pluto_lots:
        if len(bct) != 7 or bct[0] not in BORO:
            continue
        tract = BORO[bct[0]] + bct[1:].zfill(6)
        if tract not in want:
            continue
        p = Point(*to_m(lon, lat))
        a = agg.setdefault(tract, dict(lots=0, z4=0, z8=0, c4=0, c8=0,
                                       k8=0, w8=0))
        a["lots"] += 1
        if zone_tree is not None and len(zone_tree):
            d = nearest(zone_tree, zone_pts, p)
            if d <= NEAR_M:
                a["z4"] += 1
            if d <= WALK_M:
                a["z8"] += 1
        if cluster_tree is not None and len(cluster_tree):
            d = nearest(cluster_tree, cluster_pts, p)
            if d <= NEAR_M:
                a["c4"] += 1
            if d <= WALK_M:
                a["c8"] += 1
        if tree_c is not None and len(tree_c):
            if nearest(tree_c, cult_pts, p) <= WALK_M:
                a["k8"] += 1
        if tree_w is not None and len(tree_w):
            if nearest(tree_w, wor_pts, p) <= WALK_M:
                a["w8"] += 1

    for tract, a in agg.items():
        n = a["lots"]
        if n < 1:
            continue
        nyc_out[tract] = dict(lots=n, pop=None,
                              z400=round(100 * a["z4"] / n, 1),
                              z800=round(100 * a["z8"] / n, 1),
                              c400=round(100 * a["c4"] / n, 1),
                              c800=round(100 * a["c8"] / n, 1),
                              k800=round(100 * a["k8"] / n, 1),
                              w800=round(100 * a["w8"] / n, 1))
    json.dump(nyc_out, open(os.path.join(DATA, "commerce_access.json"), "w"))
    log(f"wrote commerce_access.json: {len(nyc_out)} tracts")

    # ---- region: population denominator ---------------------------------
    import fetch_park_access_region as pr

    pop = pr.bg_population()
    bgs = pr.load_block_groups(want)
    log(f"block groups to measure: {len(bgs):,}")

    reg = {}
    racc = {}
    for geoid, geom in bgs.items():
        n = pop.get(geoid, 0)
        a = racc.setdefault(geoid[:11], dict(pop=0, z4=0, z8=0, c4=0, c8=0,
                                             k8=0, w8=0, nbg=0))
        a["nbg"] += 1
        if n <= 0:
            continue
        c = sh_transform(to_xy, geom.centroid)
        a["pop"] += n
        if zone_tree is not None and len(zone_tree):
            if nearest(zone_tree, zone_pts, c) <= WALK_M:
                a["z8"] += n
            if nearest(zone_tree, zone_pts, c) <= NEAR_M:
                a["z4"] += n
        if cluster_tree is not None and len(cluster_tree):
            if nearest(cluster_tree, cluster_pts, c) <= WALK_M:
                a["c8"] += n
            if nearest(cluster_tree, cluster_pts, c) <= NEAR_M:
                a["c4"] += n
        if tree_c is not None and len(tree_c):
            if nearest(tree_c, cult_pts, c) <= WALK_M:
                a["k8"] += n
        if tree_w is not None and len(tree_w):
            if nearest(tree_w, wor_pts, c) <= WALK_M:
                a["w8"] += n

    for tract, a in racc.items():
        n = a["pop"]
        if n <= 0:
            continue
        reg[tract] = dict(lots=None, pop=n,
                          z400=round(100 * a["z4"] / n, 1),
                          z800=round(100 * a["z8"] / n, 1),
                          c400=round(100 * a["c4"] / n, 1),
                          c800=round(100 * a["c8"] / n, 1),
                          k800=round(100 * a["k8"] / n, 1),
                          w800=round(100 * a["w8"] / n, 1))
    json.dump(reg, open(os.path.join(DATA, "commerce_access_region.json"), "w"))
    log(f"wrote commerce_access_region.json: {len(reg)} tracts")

    # ---- headline numbers ------------------------------------------------
    def share(d, key, w):
        tot = sum(v[w] for v in d.values() if v[w])
        return sum(v[key] * v[w] for v in d.values() if v[w]) / tot if tot else 0

    sub = {g: v for g, v in reg.items() if g[:5] not in NYC}

    log("")
    log("                                  within 400 m    within 800 m")
    log(f"NYC lots, designated commerce      {share(nyc_out,'z400','lots'):5.1f}%"
        f"          {share(nyc_out,'z800','lots'):5.1f}%")
    log(f"NYC lots, shop clusters            {share(nyc_out,'c400','lots'):5.1f}%"
        f"          {share(nyc_out,'c800','lots'):5.1f}%")
    log(f"NYC lots, culture venue                  -          "
        f"{share(nyc_out,'k800','lots'):5.1f}%")
    log(f"NYC lots, place of worship               -          "
        f"{share(nyc_out,'w800','lots'):5.1f}%")
    log(f"suburban residents, shop clusters  {share(sub,'c400','pop'):5.1f}%"
        f"          {share(sub,'c800','pop'):5.1f}%")
    log(f"suburban residents, culture venue        -          "
        f"{share(sub,'k800','pop'):5.1f}%")
    log(f"suburban residents, worship              -          "
        f"{share(sub,'w800','pop'):5.1f}%")


if __name__ == "__main__":
    main()
