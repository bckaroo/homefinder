
"""Parcel-level access to parks, measured two ways.

Two measures, because "park access" is two different questions:
  * any park  -- is there a park of >= 0.25 acre within the walk?  This is the
                 everyday-access measure, and inside the city it is the
                 meaningful one: Bryant (9.6 ac), Union Square (6.5) and Madison
                 Square (6.2) all fail a 10-acre cut, and they are the open space
                 those neighbourhoods actually use.
  * major park -- >= 100 acres, or a waterfront park of >= 20 acres (the
                 destination measure the map has always used).

Reporting only the second number overstates park poverty in dense Manhattan.

Walking distance is measured as straight-line distance from the tax lot
coordinate to the nearest major-park boundary, at two thresholds:
  400 m  ~ 5 minute walk
  800 m  ~ 10 minute walk
Straight-line is a *screen*, not a network walk: rivers, rail yards and
expressways make the real walk longer. Flagged as such in the outputs.

Outputs
  data/parcels_park_access.csv      every NYC tax lot with its distance + flags
  data/park_access_by_tract.csv     per 2020 tract: share of residential lots near a park
  web/park_access_by_tract.json     small lookup consumed by build_html.py

Source: PLUTO 26v2 (lat/long per tax lot), parks = web/parks.geojson
"""
import json, math, os, sys, time, urllib.request, urllib.parse
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(HERE, "data")
RAW  = os.path.join(DATA, "raw")
os.makedirs(RAW, exist_ok=True)

MAJOR_ACRES = 100.0
NEAR_M  = 400.0
WALK_M  = 800.0

PLUTO = "https://data.cityofnewyork.us/resource/64uk-42ks.csv"
COLS = ["bbl", "address", "borough", "bct2020", "latitude", "longitude",
        "unitsres", "lotarea", "yearbuilt", "landuse", "cd"]


def fetch_pluto():
    out = os.path.join(RAW, "pluto_lots.csv")
    if os.path.exists(out) and os.path.getsize(out) > 50_000_000:
        print("pluto: cached", round(os.path.getsize(out)/1e6, 1), "MB")
        return out
    rows = []
    step, offset = 50000, 0
    while True:
        q = urllib.parse.urlencode({
            "$select": ",".join(COLS),
            "$order": "bbl",
            "$limit": step,
            "$offset": offset,
        })
        for attempt in range(4):
            try:
                req = urllib.request.Request(PLUTO + "?" + q,
                                             headers={"User-Agent": "park-access/1.0"})
                with urllib.request.urlopen(req, timeout=180) as r:
                    body = r.read().decode("utf-8")
                break
            except Exception as e:
                print("  retry", attempt, e); time.sleep(5 * (attempt + 1))
        else:
            raise SystemExit("pluto fetch failed")
        lines = body.strip().split("\n")
        if offset == 0:
            rows.append(lines[0])
        rows.extend(lines[1:])
        print(f"  pluto rows {offset + len(lines) - 1}")
        if len(lines) - 1 < step:
            break
        offset += step
    open(out, "w").write("\n".join(rows) + "\n")
    print("  wrote", out, round(os.path.getsize(out)/1e6, 1), "MB")
    return out


def main():
    import shapely
    from shapely.geometry import shape
    from shapely.ops import transform as sh_transform
    import csv

    parks = json.load(open(os.path.join(HERE, "web", "parks.geojson")))["features"]
    # `major` is set by fetch_amenities.py: 100+ acres, or a waterfront park of
    # at least 20 acres. The raw NYC waterfront flag fires on community gardens
    # and traffic islands, so it cannot be used on its own to define a park
    # worth walking to.
    major = [f for f in parks if f["properties"].get("major")]
    anyp = [f for f in parks if f["properties"].get("any")]
    print(f"parks: {len(parks)} total | {len(anyp)} any-scale | {len(major)} major")

    # local metric projection around the city (equirectangular, good to <0.1%)
    LAT0, LON0 = 40.70, -73.95
    MX = 111320.0 * math.cos(math.radians(LAT0))
    MY = 110540.0
    def to_xy(x, y, z=None):
        return ((np.asarray(x) - LON0) * MX, (np.asarray(y) - LAT0) * MY)

    geoms = [shape(f["geometry"]) for f in major]
    props = [f["properties"] for f in major]
    proj = [sh_transform(to_xy, g) for g in geoms]
    tree = shapely.STRtree(proj)

    any_proj = [sh_transform(to_xy, shape(f["geometry"])) for f in anyp]
    any_props = [f["properties"] for f in anyp]
    any_tree = shapely.STRtree(any_proj)
    print("index built (major + any)")

    csv_path = fetch_pluto()

    out_rows, tract = [], {}
    n = 0
    with open(csv_path, newline="") as fh:
        rd = csv.DictReader(fh)
        for r in rd:
            try:
                lat, lon = float(r["latitude"]), float(r["longitude"])
            except (TypeError, ValueError):
                continue
            x, y = (lon - LON0) * MX, (lat - LAT0) * MY
            pt = shapely.Point(x, y)
            # nearest major park boundary
            pi = int(tree.nearest(pt))
            dist = float(pt.distance(proj[pi]))
            n400 = dist <= NEAR_M
            n800 = dist <= WALK_M
            # nearest any-scale park boundary
            ai = int(any_tree.nearest(pt))
            dist_a = float(pt.distance(any_proj[ai]))
            a400 = dist_a <= NEAR_M
            a800 = dist_a <= WALK_M
            out_rows.append((r["bbl"], r["borough"], r["address"], r["bct2020"],
                             r["unitsres"], r["yearbuilt"], r["landuse"],
                             round(lat, 6), round(lon, 6), round(dist, 1),
                             int(n400), int(n800), props[pi]["name"],
                             round(dist_a, 1), int(a400), int(a800),
                             any_props[ai]["name"]))
            tr = r["bct2020"]
            if tr:
                # lots, near400, near800, resunits, any400, any800
                a = tract.setdefault(tr, [0, 0, 0, 0, 0, 0])
                a[0] += 1; a[1] += n400; a[2] += n800
                a[4] += a400; a[5] += a800
                try:
                    a[3] += int(float(r["unitsres"] or 0))
                except ValueError:
                    pass
            n += 1
            if n % 100000 == 0:
                print("  lots", n)

    csv_out = os.path.join(DATA, "parcels_park_access.csv")
    with open(csv_out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["bbl", "borough", "address", "tract2020", "units_res",
                    "year_built", "land_use", "lat", "lon",
                    "dist_m", "within_400m", "within_800m", "nearest_park",
                    "dist_any_m", "within_400m_any", "within_800m_any",
                    "nearest_any_park"])
        w.writerows(out_rows)
    print(f"wrote {csv_out}  ({len(out_rows)} lots)")

    # tract aggregation: 2020 tract key -> the map's tract geoid (11-digit)
    BORO = {"MN": "36061", "BX": "36005", "BK": "36047", "QN": "36081", "SI": "36085"}
    tract_out = {}
    for tr, (lots, n4, n8, _units, a4, a8) in tract.items():
        if not tr or len(tr) != 7:
            continue
        b = tr[0]; t = tr[1:]
        fips = {"1": "36061", "2": "36005", "3": "36047", "4": "36081", "5": "36085"}[b]
        geoid = fips + t.zfill(6)
        # p400/p800 stay the major-park measure (unchanged meaning for existing
        # consumers); a400/a800 are the any-scale measure.
        tract_out[geoid] = dict(lots=lots, p400=round(100 * n4 / lots, 1),
                                p800=round(100 * n8 / lots, 1),
                                a400=round(100 * a4 / lots, 1),
                                a800=round(100 * a8 / lots, 1))
    json.dump(tract_out, open(os.path.join(DATA, "park_access.json"), "w"))
    json.dump(tract_out, open(os.path.join(HERE, "web", "park_access.json"), "w"))
    print(f"tracts with park access: {len(tract_out)}")


if __name__ == "__main__":
    main()
