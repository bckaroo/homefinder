#!/usr/bin/env python3
"""
Reporting tool (not part of the build pipeline): rent levels by neighbourhood.

Answers "roughly what does a 1BR cost in this area". Two area systems, because
one label does not fit the whole metro:

  * Inside NYC        -> Neighborhood Tabulation Areas (2020 NTAs, DCP). Built
                         from census tracts, so 2023 tracts nest exactly.
  * Outside NYC       -> county (NJ counties, Westchester, Nassau, ...).

Rent figures are ACS 5-year tract medians aggregated as the median of tract
medians. That is a level indicator, not a rent you can sign: ACS reports what
tenants PAY, pooled over five years, and it is topcoded at $3,501. The uplift
block at the end compares ACS to Zillow ZORI (asking rents) per borough so the
paid-vs-asked gap is measured rather than asserted.

NOTE: the area column is `area_name`, never `area` — on a GeoDataFrame `.area`
is the geometry-area property, and any `df.area.isna()` silently tests that
instead of the column.

Usage: python3 scripts/report_areas.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
from build_modes import tract_centroids

NTA_GEOJSON = C.RAW / "geo" / "nyc_nta2020.geojson"
OUT_NTA = C.DATA / "rent_by_area_nta.csv"
OUT_COUNTY = C.DATA / "rent_by_area_county.csv"

NYC_COUNTIES = {"36005", "36047", "36061", "36081", "36085"}
COUNTY_BOROUGH = {"61": "Manhattan", "47": "Brooklyn", "81": "Queens",
                  "05": "Bronx", "85": "Staten Island"}
ZORI_PREFIX = {"103": "Staten Island", "104": "Bronx", "100": "Manhattan",
               "101": "Manhattan", "102": "Manhattan", "112": "Brooklyn",
               "113": "Queens", "114": "Queens", "116": "Queens"}
MAX_REL_MOE = 0.50      # drop tract estimates too noisy to rank with

SUMMARY_COLS = ["area_name", "area_type", "tracts", "tracts_1br", "br1_p25",
                "br1_med", "br1_p75", "all_med", "med_commute",
                "pct_within_60", "pct_in_budget", "borough"]


def label_areas(cents):
    """Attach a human area name to each tract centroid."""
    cents = gpd.GeoDataFrame(cents.copy(),
                             geometry=gpd.points_from_xy(cents.lon, cents.lat),
                             crs=4326)
    cents["state_county"] = cents.geoid.str[:5]
    cents["area_name"] = None
    cents["area_type"] = None
    cents["borough"] = None

    # --- NYC: neighbourhoods ---
    if NTA_GEOJSON.exists():
        nta = gpd.read_file(NTA_GEOJSON).to_crs(4326)[["ntaname", "boroname", "geometry"]]
        nyc_mask = cents.state_county.isin(NYC_COUNTIES)
        nyc = cents[nyc_mask]
        joined = gpd.sjoin(nyc, nta, how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        for idx, row in joined.iterrows():
            if isinstance(row.ntaname, str):
                cents.loc[idx, "area_name"] = row.ntaname
                cents.loc[idx, "area_type"] = "neighbourhood"
                cents.loc[idx, "borough"] = row.boroname
        # slivers whose representative point missed a polygon: nearest NTA
        missing = cents[cents["area_name"].isna() & nyc_mask]
        if len(missing):
            near = gpd.sjoin_nearest(missing[["geometry"]], nta, how="left")
            near = near[~near.index.duplicated(keep="first")]
            for idx, row in near.iterrows():
                cents.loc[idx, "area_name"] = row.ntaname
                cents.loc[idx, "area_type"] = "neighbourhood"
                cents.loc[idx, "borough"] = row.boroname
    else:
        print(f"! {NTA_GEOJSON} missing — NYC areas fall back to county")

    # --- outside NYC (and any NYC fallback): county ---
    county_zip = C.RAW / "cb_2023_us_county_500k.zip"
    names = {}
    if county_zip.exists():
        cty = gpd.read_file(f"zip://{county_zip}")[["GEOID", "NAMELSAD"]]
        names = dict(zip(cty.GEOID.astype(str), cty.NAMELSAD))
    else:
        print(f"! {county_zip} missing — areas labelled by FIPS")

    rest = cents[cents["area_name"].isna()]
    for idx, row in rest.iterrows():
        cents.loc[idx, "area_name"] = names.get(row.state_county, row.state_county)
        cents.loc[idx, "area_type"] = "county"
    return pd.DataFrame(cents.drop(columns="geometry"))


def summarize(df, min_tracts=3):
    rows = []
    for name, g in df.groupby("area_name"):
        # 1BR series: drop topcoded tracts, then drop estimates whose margin of
        # error is more than half the estimate -- a $900 +/- $700 tract cannot
        # rank a neighbourhood.
        g1 = g[g.rent_1br.notna() & (g.rent_1br < 3501)].copy()
        g1 = g1[(g1.rent_1br_moe / g1.rent_1br).fillna(1.0) <= MAX_REL_MOE]
        one = g1.rent_1br
        allo = g.rent_all.dropna()
        allo = allo[allo < 3501]
        comm = g.commute_min.dropna()
        if len(one) < min_tracts:
            continue
        rows.append({
            "area_name": name,
            "area_type": g.area_type.iloc[0],
            "tracts": len(g),
            "tracts_1br": len(one),
            "br1_p25": round(one.quantile(.25)),
            "br1_med": round(one.median()),
            "br1_p75": round(one.quantile(.75)),
            "all_med": round(allo.median()) if len(allo) else np.nan,
            "med_commute": round(comm.median(), 1) if len(comm) else np.nan,
            "pct_within_60": round(100 * (comm <= 60).mean(), 0) if len(comm) else np.nan,
            "pct_in_budget": round(100 * one.between(C.BUDGET_LO, C.BUDGET_HI).mean(), 0),
            "borough": g.borough.dropna().iloc[0] if g.borough.notna().any() else None,
        })
    if not rows:
        return pd.DataFrame(columns=SUMMARY_COLS)
    return pd.DataFrame(rows).sort_values("br1_med")


def asking_factors(tracts):
    """Asking/paid rent ratio by borough and county, from ZORI vs ACS.

    ZORI is an asking-rent index (Zillow, all homes); ACS is what tenants pay.
    Their ratio is the uplift a renter actually faces. Applied to a 1BR ACS
    median it is an approximation -- ZORI here is not bedroom-specific -- but
    it is measured, not assumed, and the direction is what matters.
    """
    zp = C.RAW / "Zip_zori_uc_sfrcondomfr_sm_month.csv"
    if not zp.exists():
        return {}, {}, None
    z = pd.read_csv(zp, dtype={"RegionName": str})
    date_cols = [c for c in z.columns if c[:2] == "20"]
    z = z[["RegionName", "State", "CountyName", date_cols[-1]]].rename(
        columns={date_cols[-1]: "zori"})
    z = z[z.State.isin(["NY", "NJ"]) & z.zori.notna()].copy()

    paid = tracts[(tracts.rent_all.notna()) & (tracts.rent_all < 3501)].copy()
    paid["borough"] = paid.COUNTYFP.astype(int).astype(str).str.zfill(2).map(COUNTY_BOROUGH)

    borough_ratio = {}
    for b, g in paid[paid.borough.notna()].groupby("borough"):
        pref = {k for k, v in ZORI_PREFIX.items() if v == b}
        zz = z[z.RegionName.str[:3].isin(pref)]
        if len(zz) and g.rent_all.median() > 0:
            borough_ratio[b] = round(zz.zori.median() / g.rent_all.median(), 3)

    county_ratio = {}
    for name, g in paid[paid.borough.isna()].groupby("area_name"):
        zz = z[z.CountyName == name]
        if len(zz) and g.rent_all.median() > 0:
            county_ratio[name] = round(zz.zori.median() / g.rent_all.median(), 3)

    metro = round(z.zori.median() / paid.rent_all.median(), 3) if len(paid) else None
    return borough_ratio, county_ratio, metro


def add_asking_estimate(areas, borough_ratio, county_ratio, metro):
    """Estimate the asking rent a listing site would show for each area."""
    def one(row):
        f = borough_ratio.get(row.borough) if isinstance(row.borough, str) else None
        if f is None:
            f = county_ratio.get(row.area_name, metro)
        return round(row.br1_med * f) if f and pd.notna(row.br1_med) else np.nan

    areas = areas.copy()
    areas["asking_1br_est"] = areas.apply(one, axis=1)
    return areas


def zori_uplift(tracts):
    """Measured paid-vs-asking gap: ACS rent_all vs ZORI, per borough.

    ZORI is keyed by ZIP; ACS here is keyed by tract. Match on borough, derived
    from tract COUNTYFP -- grouping ACS by NTA name and ZORI by borough yields
    zero overlap and an empty table.
    """
    zp = C.RAW / "Zip_zori_uc_sfrcondomfr_sm_month.csv"
    if not zp.exists():
        return None
    z = pd.read_csv(zp, dtype={"RegionName": str})
    date_cols = [c for c in z.columns if c[:2] == "20"]
    z = z[["RegionName", "State", date_cols[-1]]].rename(columns={date_cols[-1]: "zori"})
    z = z[z.State.isin(["NY", "NJ"]) & z.zori.notna()].copy()
    z["borough"] = z.RegionName.str[:3].map(ZORI_PREFIX)
    z = z[z.borough.notna()]
    zori = z.groupby("borough").zori.median()

    acs = tracts[(tracts.rent_all.notna()) & (tracts.rent_all < 3501)].copy()
    # COUNTYFP is numeric in the CSV; mapping it with zero-padded string keys
    # silently yields all-NaN and an empty table.
    acs["borough"] = acs.COUNTYFP.astype(int).astype(str).str.zfill(2).map(COUNTY_BOROUGH)
    acs = acs[acs.borough.notna()]
    acs_by = acs.groupby("borough").rent_all.median()

    out = pd.DataFrame({"acs_paid_median": acs_by,
                        "zori_asking_median": zori}).dropna()
    out["asking_over_paid"] = (out.zori_asking_median / out.acs_paid_median).round(2)
    out.index.name = "borough"
    return out


def main():
    costs = pd.read_csv(C.OUT_TRACT_JOINED, dtype={"geoid": str})
    cents = tract_centroids()
    cents["geoid"] = cents.geoid.astype(str)
    labelled = label_areas(cents)

    df = costs.merge(labelled[["geoid", "area_name", "area_type", "borough"]],
                     on="geoid", how="left")
    print(f"tracts: {len(df):,}  |  unlabelled: {df.area_name.isna().sum():,}")

    nta = summarize(df[df.area_type == "neighbourhood"])
    cty = summarize(df[df.area_type == "county"])
    br, cr, metro = asking_factors(df)
    nta = add_asking_estimate(nta, br, cr, metro)
    cty = add_asking_estimate(cty, br, cr, metro)
    nta.to_csv(OUT_NTA, index=False)
    cty.to_csv(OUT_COUNTY, index=False)
    print(f"wrote {OUT_NTA}  ({len(nta)} neighbourhoods)")
    print(f"wrote {OUT_COUNTY}  ({len(cty)} counties)")

    print("\n=== asking / paid uplift (ZORI vs ACS) ===")
    for k, v in sorted(br.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<14} {v:.2f}x")
    print(f"  metro median   {metro:.2f}x")
    if cr:
        print("  counties:", ", ".join(f"{k.replace(' County','')} {v:.2f}x"
                                       for k, v in sorted(cr.items(), key=lambda kv: -kv[1])[:6]))

    pd.set_option("display.width", 230)
    cols = ["area_name", "tracts_1br", "br1_med", "asking_1br_est", "br1_p25",
            "br1_p75", "med_commute", "pct_within_60"]

    print("\n=== NYC neighbourhoods, cheapest asking 1BR first ===")
    show = nta[nta.tracts_1br >= 4].sort_values("asking_1br_est")
    print(show[cols].head(20).to_string(index=False))
    print(f"\n... {len(show)} with data. Most expensive:")
    print(show[cols].tail(6).to_string(index=False))

    print("\n=== outer counties (>=25% of tracts within 60 min) ===")
    cshow = cty[(cty.pct_within_60 >= 25) & (cty.tracts_1br >= 5)].sort_values("asking_1br_est")
    print(cshow[["area_name", "tracts_1br", "br1_med", "asking_1br_est", "all_med",
                 "med_commute", "pct_within_60"]].to_string(index=False))

    # --- where the budget band actually lands, in asking terms ---
    band = nta[(nta.asking_1br_est.between(C.BUDGET_LO, C.BUDGET_HI))
               & (nta.pct_within_60 >= 90)].sort_values("med_commute")
    print(f"\n=== asking 1BR in ${C.BUDGET_LO:,}-${C.BUDGET_HI:,}, >=90% within 60 min "
          f"(n={len(band)}) ===")
    print(band[["area_name", "br1_med", "asking_1br_est", "med_commute"]]
          .head(25).to_string(index=False))

    up = zori_uplift(df)
    if up is not None and len(up):
        print("\n=== ACS (paid) vs ZORI (asking), all bedrooms ===")
        print(up.to_string())


if __name__ == "__main__":
    main()
