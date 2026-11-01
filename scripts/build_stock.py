#!/usr/bin/env python3
"""
Phase 4: tract-level housing stock age from ACS 5-yr 2024 summary files.

B25034 = YEAR STRUCTURE BUILT -- ten bands of HOUSING UNITS (not buildings).
B25035 = median year structure built.

B25034 cell order is descending, so the indices are counter-intuitive:
  E001 total | E002 2020+ | E003 2010-19 | E004 2000-09 | E005 1990-99
  E006 1980-89 | E007 1970-79 | E008 1960-69 | E009 1950-59 | E010 1940-49
  E011 1939 or earlier

We collapse those into the four bands the map colours:
  pre_1940      = 1939 or earlier                (pre-war)
  y1940_1979    = 1940-49, 50-59, 60-69, 70-79  (post-war boom)
  y1980_1999    = 1980-89, 1990-99              (late 20th c.)
  y2000_plus    = 2000-09, 10-19, 20+           (new stock)
plus two finer cuts ACS can still resolve regionally: built since 2010, since 2020.

Counts are UNITS, not buildings. A 200-unit tower built in 2018 counts 200 times;
a 1920 two-family counts twice. That is the right denominator for "what housing
can I actually rent", and it is why the newest band is noisy in small tracts.
"""
import pandas as pd, numpy as np, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

BANDS = {
    "pre_1940":   [11],
    "y1940_1979": [10, 9, 8, 7],
    "y1980_1999": [6, 5],
    "y2000_plus": [4, 3, 2],
}
FINER = {
    "new_2010": [3, 2],
    "new_2020": [2],
}


def load_table(path, prefix, n_cells):
    p = C.require(path, "run scripts/fetch_data.py to download ACS tables")
    cols = []
    for i in range(1, n_cells + 1):
        cols += [f"{prefix}_E{i:03d}", f"{prefix}_M{i:03d}"]
    df = pd.read_csv(p, sep="|", dtype=str, low_memory=False)
    df = df[df["GEO_ID"].astype(str).str.startswith("1400000US")]
    df["geoid"] = df["GEO_ID"].astype(str).str[9:]
    df = df[df["geoid"].str.len() == 11]
    for c in cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    # ACS sentinels (-666666666 etc.) are negative; scrub both E and M
    for c in cols:
        df.loc[df[c] < 0, c] = np.nan
    return df[["geoid"] + cols].copy()


def band_sum(df, idxs, prefix="B25034"):
    """Sum estimates; combine MOEs in quadrature (independent bands)."""
    est = df[[f"{prefix}_E{i:03d}" for i in idxs]].sum(axis=1, min_count=1)
    moe = np.sqrt((df[[f"{prefix}_M{i:03d}" for i in idxs]] ** 2).sum(axis=1, min_count=1))
    return est, moe


def share_moe(num, num_moe, den, den_moe):
    """ACS derived proportion: sqrt(M_num^2 - p^2 * M_den^2) / den, floored at 0."""
    p = num / den
    inner = num_moe ** 2 - (p ** 2) * (den_moe ** 2)
    return np.sqrt(inner.clip(lower=0)) / den


def main():
    print("Loading ACS 5-yr 2024 B25034 (year built) / B25035 (median year)...")
    b34 = load_table(C.ACS_5YR_2024_B25034, "B25034", 11)
    b35 = load_table(C.ACS_5YR_2024_B25035, "B25035", 1)
    b35 = b35.rename(columns={"B25035_E001": "med_year", "B25035_M001": "med_year_moe"})
    df = b34.merge(b35, on="geoid", how="left")
    df["county"] = df["geoid"].str[:5]
    df = df[df["county"].isin(C.REGION_COUNTIES)].copy()
    print(f"  {len(df):,} tracts in the {len(C.REGION_COUNTIES)}-county region")

    total = df["B25034_E001"]
    total_moe = df["B25034_M001"]

    out = df[["geoid", "county"]].copy()
    out["units_total"] = total
    out["units_total_moe"] = total_moe

    for name, idxs in {**BANDS, **FINER}.items():
        est, moe = band_sum(df, idxs)
        out[f"units_{name}"] = est
        out[f"share_{name}"] = est / total
        out[f"share_{name}_moe"] = share_moe(est, moe, total, total_moe)
        out[f"relmoe_{name}"] = moe / est.replace(0, np.nan)

    out["med_year"] = df["med_year"]
    out["med_year_moe"] = df["med_year_moe"]

    # Confidence: a share is only coloured when the tract has enough units and
    # the new-stock numerator is precise enough to mean something.
    # Colour everything with a real housing base. The composition is a partition
    # of the same units, so it stays legible even where a single band's MOE is wide;
    # precision is reported per tract instead of used as a paint gate.
    has_base = out["units_total"].fillna(0) >= C.MIN_UNITS_FOR_STOCK_SHARE
    wide = out["relmoe_y2000_plus"].fillna(0) > C.WIDE_MARGIN_RELMOE
    out["stock_conf"] = np.where(~has_base, "none", np.where(wide, "low", "ok"))
    # nothing to say at all: no housing units in the tract
    out.loc[~has_base, [c for c in out.columns if c.startswith("share_")]] = np.nan

    out.to_csv(C.OUT_TRACT_STOCK, index=False)
    print(f"  wrote {C.OUT_TRACT_STOCK}")

    ok = out[out["stock_conf"] != "none"]
    print(f"\nTracts coloured: {len(ok):,} / {len(out):,} "
          f"({100 * len(ok) / len(out):.0f}%); "
          f"wide-margin: {(out['stock_conf'] == 'low').sum():,}")
    for c in ["share_pre_1940", "share_y1940_1979", "share_y1980_1999", "share_y2000_plus"]:
        s = ok[c].dropna()
        print(f"  {c:<20} p25={s.quantile(.25):.2f}  med={s.median():.2f}  p75={s.quantile(.75):.2f}")

    probes = {
        "Jersey City (Hudson 017)": out["geoid"].str.startswith("3401700"),
        "Manhattan": out["geoid"].str.startswith("36061"),
        "Westchester": out["geoid"].str.startswith("36119"),
        "Nassau": out["geoid"].str.startswith("36059"),
        "Suffolk": out["geoid"].str.startswith("36103"),
        "Bronx": out["geoid"].str.startswith("36005"),
    }
    print("\nUnits-weighted stock mix by area (2000+ / 1980-99 / 1940-79 / pre-1940):")
    for label, m in probes.items():
        sub = out[m]
        u = sub["units_total"].fillna(0)
        if u.sum() == 0:
            continue
        sh = lambda b: (sub[f"units_{b}"].fillna(0) * (u > 0)).sum() / u.sum()
        print(f"  {label:<28} {sh('y2000_plus'):.0%}  {sh('y1980_1999'):.0%}  "
              f"{sh('y1940_1979'):.0%}  {sh('pre_1940'):.0%}   "
              f"median year {np.nansum(sub['med_year'] * u) / u.sum():.0f}")


if __name__ == "__main__":
    main()
