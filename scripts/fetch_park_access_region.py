
"""Park access outside New York City — census block-group population.

PLUTO tax lots exist only in the five boroughs, so the lot-level measure in
fetch_park_access.py stops at the city line and roughly a thousand tracts in
Nassau, Suffolk, Westchester, Bergen, Essex, Hudson, Passaic, Union and
Middlesex came back blank on the map.

This script fills them with the finest population denominator the Census
publishes for the whole region: 2020 census block groups, weighted by 2020
decennial population. For each block group it measures the straight-line
distance from the block group's centre to the nearest major-park boundary
(same definition, same projection, same thresholds as the lot-level pass), then
rolls the block groups up to their 2020 tract.

Output
  data/park_access_region.json   {geoid: {p400, p800, pop, nbg}}

Inside the city the lot measure stays authoritative — a tax lot is the actual
thing you would walk from, a block-group centroid is an approximation of it.
Block groups are computed for the whole region anyway so the two methods can be
compared on the same ground (the README records that check).

Sources: TIGER/Line 2024 block groups (36, 34), 2020 Census PL P1_001N,
parks = web/parks.geojson (`major` property).
"""
import json, math, os, sys, urllib.request, zipfile, io
import numpy as np
import shapely
from shapely.geometry import shape
from shapely.ops import transform as sh_transform

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
RAW = os.path.join(DATA, "raw")
CACHE = os.path.join(DATA, "cache")
os.makedirs(CACHE, exist_ok=True)

NEAR_M = 400.0
WALK_M = 800.0

# counties present in the map's tract set
COUNTIES = ["34003", "34013", "34017", "34023", "34031", "34039",
            "36005", "36047", "36059", "36061", "36081", "36085",
            "36103", "36119"]
NYC = {"36061", "36005", "36047", "36081", "36085"}

API = "https://api.census.gov/data/2020/dec/pl"


def api_key():
    for p in (os.path.join(HERE, ".env"), os.path.expanduser("~/.hermes/.env")):
        if not os.path.exists(p):
            continue
        for line in open(p):
            if line.strip().startswith("CENSUS_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("CENSUS_API_KEY", "")


def bg_population():
    """2020 decennial population per block group (P1_001N), cached."""
    out = os.path.join(CACHE, "bg_pop_2020.json")
    if os.path.exists(out):
        d = json.load(open(out))
        if len(d) > 20000:
            print("bg population: cached", len(d), "block groups")
            return d
    key = api_key()
    if not key:
        raise SystemExit("CENSUS_API_KEY not found (checked .env and ~/.hermes/.env)")
    pop = {}
    for cty in COUNTIES:
        st, co = cty[:2], cty[2:]
        url = (f"{API}?get=NAME,P1_001N&for=block%20group:*"
               f"&in=state:{st}&in=county:{co}&key={key}")
        with urllib.request.urlopen(url, timeout=120) as r:
            rows = json.loads(r.read().decode())
        hdr, rows = rows[0], rows[1:]
        # the API returns "tract" and a single-digit "block group" separately;
        # the 12-digit geoid has to be built from both
        ip = hdr.index("P1_001N")
        it, ib = hdr.index("tract"), hdr.index("block group")
        for row in rows:
            pop[st + co + row[it].zfill(6) + row[ib]] = int(row[ip] or 0)
        print(f"  {cty}: {len(rows)} block groups")
    json.dump(pop, open(out, "w"))
    print("bg population:", len(pop), "block groups cached")
    return pop


def load_block_groups(want_tracts):
    """TIGER block group polygons for the counties we map, keyed by 12-digit."""
    out = {}
    for st in ("36", "34"):
        z = os.path.join(RAW, f"tl_2024_{st}_bg.zip")
        if not os.path.exists(z):
            raise SystemExit(f"missing {z} — download "
                             f"https://www2.census.gov/geo/tiger/TIGER2024/BG/tl_2024_{st}_bg.zip")
        import geopandas as gpd
        g = gpd.read_file(f"zip://{z}", engine="pyogrio")
        g = g[g.COUNTYFP.isin({c[2:] for c in COUNTIES if c[:2] == st})]
        for row in g.itertuples():
            geoid = row.STATEFP + row.COUNTYFP + row.TRACTCE + row.BLKGRPCE
            tract = geoid[:11]
            if tract in want_tracts:
                out[geoid] = row.geometry
        print(f"  state {st}: {len(g)} block groups in region")
    return out


def main():
    parks = json.load(open(os.path.join(HERE, "web", "parks.geojson")))["features"]
    major = [f for f in parks if f["properties"].get("major")]
    tracts = json.load(open(os.path.join(DATA, "tracts.geojson")))["features"]
    want = {f["properties"]["geoid"] for f in tracts
            if f["properties"]["geoid"][:5] in COUNTIES}
    print(f"parks: {len(major)} major | tracts to fill: {len(want)}")

    # same equirectangular projection as the lot-level pass
    LAT0, LON0 = 40.70, -73.95
    MX = 111320.0 * math.cos(math.radians(LAT0))
    MY = 110540.0

    def to_xy(x, y, z=None):
        return ((np.asarray(x) - LON0) * MX, (np.asarray(y) - LAT0) * MY)

    proj = [sh_transform(to_xy, shape(f["geometry"])) for f in major]
    tree = shapely.STRtree(proj)
    print("park index built")

    pop = bg_population()
    bgs = load_block_groups(want)
    print(f"block groups to measure: {len(bgs)}")

    agg = {}
    missing_pop = 0
    for geoid, geom in bgs.items():
        p = pop.get(geoid, 0)
        c = sh_transform(to_xy, geom.centroid)
        pi = int(tree.nearest(c))
        dist = float(c.distance(proj[pi]))
        a = agg.setdefault(geoid[:11], dict(pop=0, n4=0, n8=0, nbg=0, blank=0))
        a["nbg"] += 1
        if p <= 0:
            a["blank"] += 1
            continue
        a["pop"] += p
        if dist <= NEAR_M:
            a["n4"] += p
        if dist <= WALK_M:
            a["n8"] += p

    out = {}
    for geoid, a in agg.items():
        if a["pop"] <= 0:
            continue          # no residents: nothing to express as a share
        out[geoid] = dict(p400=round(100 * a["n4"] / a["pop"], 1),
                          p800=round(100 * a["n8"] / a["pop"], 1),
                          pop=a["pop"], nbg=a["nbg"])
        missing_pop += a["blank"]
    path = os.path.join(DATA, "park_access_region.json")
    json.dump(out, open(path, "w"))
    print(f"wrote {path}: {len(out)} tracts, {missing_pop} empty block groups skipped")
    print(f"  tracts with no residents in region: {len(want) - len(out)}")

    # --- method check: how do the two denominators compare inside the city? ----
    lot = json.load(open(os.path.join(DATA, "park_access.json")))
    common = [g for g in out if g[:5] in NYC and g in lot]
    if common:
        pop4 = sum(out[g]["p400"] * out[g]["pop"] for g in common)
        pop8 = sum(out[g]["p800"] * out[g]["pop"] for g in common)
        tot = sum(out[g]["pop"] for g in common)
        lots = sum(lot[g]["lots"] for g in common if g in lot)
        lot8 = sum(lot[g]["p800"] * lot[g]["lots"] for g in common if g in lot)
        lot4 = sum(lot[g]["p400"] * lot[g]["lots"] for g in common if g in lot)
        print("\nNYC, same tracts, two denominators:")
        print(f"  block-group population : {pop4/tot:.1f}% within 400 m, {pop8/tot:.1f}% within 800 m")
        print(f"  tax lots               : {lot4/lots:.1f}% within 400 m, {lot8/lots:.1f}% within 800 m")


if __name__ == "__main__":
    main()
