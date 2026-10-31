#!/usr/bin/env python3
"""
Phase 2: tract-level rent (studio + 1BR) from ACS 5-yr 2024 summary files.
B25031 = median gross rent by bedrooms (002=no bed/studio, 003=1BR, ...)
B25064 = median gross rent, all units.

ACS tables are keyless bulk .dat downloads from www2.census.gov.
"""
import pandas as pd, numpy as np, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

OUT = str(C.DATA)



NYC_COUNTIES = {"36061", "36047", "36081", "36085", "36005"}  # Man, Bk, Qns, SI, Bx
NEARBY = NYC_COUNTIES | {
    "36059","36103","36119","36087","36079","36027","36071","36033","36039","36111",
    "34003","34017","34023","34025","34029","34031","34035","34037","34039","34013",
    "34019","34027","34041","34021","34005","34007",
}


def load_table(path, cols):
    p = C.require(path, "run scripts/fetch_data.py to download ACS tables")
    df = pd.read_csv(p, sep="|", dtype=str, low_memory=False)
    df["GEO_ID"] = df["GEO_ID"].astype(str)
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # tract geoids look like 1400000US36061000100 -> keep trailing 11 chars
    df = df[df["GEO_ID"].str.startswith("1400000US")]
    df["geoid"] = df["GEO_ID"].str[9:]
    df.loc[df["geoid"].str.len() != 11, "geoid"] = None
    df = df.dropna(subset=["geoid"])
    return df[["geoid"] + cols]


def main():
    print("Loading ACS 5-yr 2024 rent tables...")
    b25064 = load_table(C.ACS_5YR_2024_B25064, ["B25064_E001", "B25064_M001"])
    b25064 = b25064.rename(columns={"B25064_E001": "rent_all", "B25064_M001": "rent_all_moe"})

    # B25031: E002 studio(no bedroom), E003 1BR, E004 2BR, E005 3BR, E006 4BR, E007 5+BR
    # Every B25031 variable is a MEDIAN GROSS RENT in dollars, not a count.
    # E001 is the all-sizes median, identical to B25064_E001 (verified across all
    # 80,142 tracts), so it is not carried separately -- it was previously
    # renamed to `units_total`, which read as a household count and was not.
    cols31 = [f"B25031_E00{i}" for i in range(2, 8)] + [f"B25031_M00{i}" for i in (2, 3)]
    b25031 = load_table(C.ACS_5YR_2024_B25031, cols31)
    b25031 = b25031.rename(columns={
        "B25031_E002": "rent_studio", "B25031_M002": "rent_studio_moe",
        "B25031_E003": "rent_1br", "B25031_M003": "rent_1br_moe",
        "B25031_E004": "rent_2br", "B25031_E005": "rent_3br",
        "B25031_E006": "rent_4br", "B25031_E007": "rent_5br",
    })
    print(f"  B25064 rows: {len(b25064):,}   B25031 rows: {len(b25031):,}")

    # unit counts by bedrooms come from B25032 (tenure by units in structure is different)
    # use B25041/B25042? simpler: B25031 already gives median rent, and B25032 gives counts.
    df = b25064.merge(b25031, on="geoid", how="outer")
    df["county"] = df["geoid"].str[:5]
    df = df[df["county"].isin(NEARBY)].copy()
    print(f"  after county filter: {len(df):,} tracts")

    # ACS sentinels: -666666666 estimate NA, -333333333 control/no sampling error,
    # -222222222 not applicable, -555555555 insufficient data. Scrub BOTH estimates and MOEs.
    for c in [c for c in df.columns if c.startswith(("rent_", "units_"))]:
        df.loc[df[c] < 0, c] = np.nan

    df.to_csv(C.OUT_TRACT_RENT, index=False)
    print(f"  wrote {C.OUT_TRACT_RENT}")

    nyc = df[df["county"].isin(NYC_COUNTIES)]
    print(f"\nNYC tracts: {len(nyc)}")
    for c in ["rent_studio","rent_1br","rent_all"]:
        s = nyc[c].dropna()
        print(f"  {c}: n={len(s)}, p25={s.quantile(.25):,.0f}, "
              f"med={s.median():,.0f}, p75={s.quantile(.75):,.0f}")
    st = nyc["rent_studio"].dropna()
    one = nyc["rent_1br"].dropna()
    print(f"\n  studios $2-3K: {((st>=2000)&(st<=3000)).sum()} tracts")
    print(f"  1BRs   $2-3K: {((one>=2000)&(one<=3000)).sum()} tracts")


if __name__ == "__main__":
    main()
