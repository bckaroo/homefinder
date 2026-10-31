#!/usr/bin/env python3
"""
Allocate Zillow ZORI asking rents from ZIP codes down to census tracts.

ZORI (Zillow Observed Rent Index) is a *market asking rent*: the typical rent
on a newly listed unit, hedonic-adjusted to a constant quality, published
monthly per ZIP code. ACS B25064 is what tenants already in place pay. The two
answer different questions, and their ratio is the uplift a mover actually
faces when they arrive in a neighbourhood.

There is no free tract-level asking-rent series, so the ZIP index has to be
allocated. The method here is areal: each tract takes the area-weighted mean
ZORI of the ZCTAs it overlaps. A tract sitting inside one ZIP inherits that
ZIP's index exactly; a tract split between two gets a blend and is flagged by
n_zips. Areas are measured in UTM 18N so weighting is not distorted by
latitude.

Output (committed): data/tract_asking.csv
    geoid, zori, zori_month, ask_nzips, ask_share, premium

Usage:
    python3 scripts/build_asking.py [--force]
"""
import argparse
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

# TIGERweb REST: current ZIP Code Tabulation Areas. The national shapefile is
# 529 MB, so the metro envelope is pulled instead (~16 MB, ~765 ZCTAs) and
# cached under data/raw/geo/.
ZCTA_LAYER = ("https://tigerweb.geo.census.gov/arcgis/rest/services/"
              "TIGERweb/PUMA_TAD_TAZ_UGA_ZCTA/MapServer/11/query")
UTM18N = 32618


def fetch_zcta(force=False):
    dest = Path(C.ZCTA_METRO)
    if dest.exists() and not force:
        print(f"  skip (exists) {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    params = {
        "where": "1=1",
        "geometry": C.ZCTA_BBOX,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ZCTA5",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "geojson",
    }
    url = ZCTA_LAYER + "?" + urllib.parse.urlencode(params)
    print(f"  get ZIP Code Tabulation Areas for bbox {C.ZCTA_BBOX}")
    tmp = dest.with_suffix(".part")
    req = urllib.request.Request(url, headers={"User-Agent": "transit-housing-analytics/1.0"})
    with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)
    print(f"      {dest.stat().st_size/1e6:.1f} MB")
    return dest


def zori_series():
    """Latest-month ZORI for NY and NJ ZIPs, indexed by 5-digit ZIP."""
    z = pd.read_csv(C.require(C.ZORI_ZIP, "run scripts/fetch_data.py"),
                    dtype={"RegionName": str})
    date_cols = [c for c in z.columns if c[:2] == "20"]
    month = date_cols[-1]
    z = z[["RegionName", "State", month]].rename(columns={"RegionName": "zip", month: "zori"})
    z["zip"] = z.zip.str.zfill(5)
    z = z[z.State.isin(["NY", "NJ"]) & z.zori.notna()]
    print(f"  ZORI {month}: {len(z):,} NY/NJ ZIPs, median ${z.zori.median():,.0f}")
    return z.set_index("zip").zori, month


def allocate(tracts, zcta, zori):
    """Area-weighted ZORI per tract, plus how much of the tract it covers."""
    tracts = tracts[["geoid", "geometry"]].to_crs(UTM18N)
    tracts["t_area"] = tracts.area
    zcta = zcta[zcta.ZCTA5.isin(zori.index)].to_crs(UTM18N)
    print(f"  tracts {len(tracts):,} · ZCTAs carrying ZORI {len(zcta):,}")

    ov = gpd.overlay(tracts, zcta[["ZCTA5", "geometry"]], how="intersection",
                     keep_geom_type=True)
    ov["a"] = ov.area
    ov = ov[ov.a > 100]                       # ignore sliver intersections (<100 m²)
    ov["zori"] = ov.ZCTA5.map(zori)
    ov = ov[ov.zori.notna()]

    g = ov.groupby("geoid")
    out = g.apply(lambda d: pd.Series({
        "zori": (d.a * d.zori).sum() / d.a.sum(),
        "ask_nzips": d.ZCTA5.nunique(),
        "ask_share": d.a.sum(),
    }), include_groups=False)
    out = out.join(tracts.set_index("geoid")["t_area"])
    out["ask_share"] = (out.ask_share / out.t_area).clip(upper=1.0)
    return out[["zori", "ask_nzips", "ask_share"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-fetch ZCTA boundaries")
    args = ap.parse_args()
    C.ensure_dirs()

    zcta_path = fetch_zcta(args.force)
    zori, month = zori_series()

    tracts = gpd.read_file(C.require(C.OUT_TRACT_GEOJSON, "run scripts/join_all.py first"))
    zcta = gpd.read_file(zcta_path)
    alloc = allocate(tracts, zcta, zori)

    rent = pd.read_csv(C.require(C.OUT_TRACT_RENT),
                       usecols=["geoid", "rent_all", "rent_all_moe", "county"])
    rent["geoid"] = rent.geoid.astype(str).str.zfill(11)
    df = rent.merge(alloc, left_on="geoid", right_index=True, how="left")
    df["zori_month"] = month

    # A premium is only meaningful against a usable, uncapped paid rent. Tracts
    # reporting a few hundred dollars (group quarters, subsidised stock with a
    # handful of renters) would otherwise produce 5x-20x outliers that mean
    # nothing; their relative MOE also tells the UI how much to trust the ratio.
    paid = df.rent_all.where((df.rent_all >= C.MIN_RENT_FOR_PREMIUM) & (df.rent_all < C.TOPCAP))
    df["premium"] = (df.zori / paid).round(2)
    df["prem_relmoe"] = (df.rent_all_moe / df.rent_all).round(2)
    df.loc[df.premium.isna(), "prem_relmoe"] = float("nan")

    out = df[["geoid", "zori", "zori_month", "ask_nzips", "ask_share",
              "premium", "prem_relmoe"]].copy()
    out["zori"] = out.zori.round(0)
    out["ask_share"] = out.ask_share.round(3)
    out.to_csv(C.OUT_TRACT_ASKING, index=False)

    # ---- QA ----
    n = len(out)
    mapped = set(gpd.read_file(C.OUT_TRACT_GEOJSON, columns=["geoid"]).geoid.astype(str))
    have = out.zori.notna() & out.geoid.isin(mapped)
    print(f"\n  wrote {C.OUT_TRACT_ASKING.relative_to(C.ROOT)}  ({n:,} tracts)")
    print(f"  mapped tracts with an estimate : {have.sum():,} of {len(mapped):,}"
          f" ({100*have.sum()/len(mapped):.0f}%)")
    print(f"  median tract coverage          : {100*out.ask_share.median():.0f}% of area")
    print(f"  tracts straddling >1 ZIP       : {(out.ask_nzips > 1).sum():,}")
    p = out.premium.dropna()
    if len(p):
        lo = (out.prem_relmoe <= C.PREM_MOE_LIMIT).sum()
        print(f"  market premium (asking / paid) : median {p.median():.2f}×"
              f"  p10 {p.quantile(.1):.2f}×  p90 {p.quantile(.9):.2f}×")
        print(f"  premium low-confidence (relMOE>{C.PREM_MOE_LIMIT:.0%}) : "
              f"{int(out.premium.notna().sum()) - lo:,} tracts")
        by_b = out.dropna(subset=["premium"]).assign(
            b=lambda d: d.geoid.str[:5].map({"36061": "Manhattan", "36047": "Brooklyn",
                                             "36081": "Queens", "36005": "Bronx",
                                             "36085": "Staten Island"}))
        for b, g in by_b.dropna(subset=["b"]).groupby("b"):
            print(f"    {b:<14} {g.premium.median():.2f}×  (n={len(g):,})")
    else:
        print("  ! no premium computed — ACS rent_all missing")


if __name__ == "__main__":
    main()
