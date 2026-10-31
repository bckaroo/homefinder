#!/usr/bin/env python3
"""
Phase 3: join transit time + tract rent -> tract-level dataset + Andrew's shortlist.
Handles ACS $3,501 topcoding explicitly rather than silently rendering a fake number.
"""
import pandas as pd, numpy as np, geopandas as gpd, json, math, os, sys
from pathlib import Path
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

OUT = str(C.DATA)
PENN1 = C.PENN1
TOPCAP = C.TOPCAP
WALK_MPS = C.WALK_MPS
MAX_WALK_MIN = C.MAX_WALK_MIN


def haversine(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2*R*math.asin(math.sqrt(min(1.0, a)))


def main():
    stations = pd.read_csv(C.require(C.OUT_STATION_TIMES, "run scripts/build_graph.py first"))
    stations["net"] = stations["net"].astype(str)
    stations["stop_id"] = stations["stop_id"].astype(str)
    print(f"stations: {len(stations)}  (<=60min: {(stations['min']<=60).sum()})")

    # ---- tracts: NY + NJ counties inside the 60-min envelope ----
    g = gpd.read_file(C.require(C.CB_TRACT_NY, "run scripts/fetch_data.py")).to_crs(32618)

    # NJ tracts (Hudson, Essex, Union, Bergen, Middlesex, Passaic, Morris)
    print("loading NJ tracts...")
    nj = gpd.read_file(C.require(C.CB_TRACT_NJ, "run scripts/fetch_data.py")).to_crs(32618)
    print(f"  NY tracts {len(g)}, NJ tracts {len(nj)}")

    frames = []
    for frame in (g, nj):
        f = frame[["GEOID", "STATEFP", "COUNTYFP", "ALAND", "geometry"]].copy()
        f["GEOID"] = f["GEOID"].astype(str)
        frames.append(f)
    tracts = pd.concat(frames, ignore_index=True)
    tracts = gpd.GeoDataFrame(tracts, geometry="geometry", crs=32618)

    stations_g = gpd.GeoDataFrame(
        stations, geometry=gpd.points_from_xy(stations.lon, stations.lat), crs=4326
    ).to_crs(32618)

    # ---- nearest reachable station per tract centroid ----
    cents = tracts.copy()
    cents["geometry"] = cents.geometry.representative_point()
    reach = stations_g[(stations_g["min"] <= 60) & stations_g["min"].notna()].copy()
    print(f"reachable stations <=60min: {len(reach)}")

    print("nearest station join...")
    j = gpd.sjoin_nearest(
        cents, reach[["net", "stop_id", "name", "min", "lat", "lon", "geometry"]],
        how="left", distance_col="dist_m",
    )
    j = j.drop_duplicates(subset=["GEOID"])

    # `station_walk_min` is access to the station, NOT a commute mode. Walking
    # as a mode is `walk_mode_min`, routed over the street network in
    # build_modes.py. The names are deliberately distinct: the two numbers
    # differ by an order of magnitude and confusing them is easy.
    j["station_walk_min"] = (j["dist_m"] / WALK_MPS / 60).round(1)
    j["commute_min"] = (j["min"] + j["station_walk_min"]).round(1)
    j["station"] = j["name"]
    j["station_net"] = j["net"]
    # honest travel time: riding only reachable if the walk is plausible
    j.loc[j["station_walk_min"] > MAX_WALK_MIN, "commute_min"] = np.nan
    if isinstance(j, gpd.GeoDataFrame):
        j = j.drop(columns=["geometry"], errors="ignore")

    out = pd.DataFrame(j[[
        "GEOID", "STATEFP", "COUNTYFP", "ALAND", "station", "station_net",
        "min", "station_walk_min", "commute_min",
    ]]).rename(columns={"GEOID": "geoid", "min": "ride_min"})
    out["geoid"] = out["geoid"].astype(str)
    print(f"  tracts with commute: {out.commute_min.notna().sum():,} / {len(out):,}")

    # ---- rent ----
    rent = pd.read_csv(C.require(C.OUT_TRACT_RENT, "run scripts/build_rent.py first"),
                       dtype={"geoid": str})
    rent["geoid"] = rent.geoid.str.zfill(11)
    df = out.merge(rent, on="geoid", how="left", suffixes=("", "_r"))
    print(f"  tracts w/ rent: {df.rent_all.notna().sum():,}")

    # ---- walking and cycling commute modes (optional stage) ----
    # Absent router output is a legitimate state: the transit artifacts must
    # reproduce on a machine with no Valhalla running. Nulls, plus a loud hint.
    if Path(C.OUT_COMMUTE_MODES).exists():
        modes = pd.read_csv(C.OUT_COMMUTE_MODES, dtype={"geoid": str})
        modes["geoid"] = modes.geoid.str.zfill(11)
        df = df.merge(modes, on="geoid", how="left")
        for col in ("walk_mode_min", "cycle_mode_min"):
            if col in df.columns:
                print(f"  tracts w/ {col}: {df[col].notna().sum():,}"
                      f"  (median {df[col].median():.0f} min)")
    else:
        df["walk_mode_min"] = np.nan
        df["cycle_mode_min"] = np.nan
        print(f"  no {C.OUT_COMMUTE_MODES.name}: walking/cycling left null")
        print("    -> run scripts/build_modes.py with Valhalla up to populate them")

    # ---- ZORI (uncapped, current) for censor resolution ----
    z = pd.read_csv(C.require(C.ZORI_ZIP, "run scripts/fetch_data.py"))
    zcols = [c for c in z.columns if c.startswith("20")]
    z["zip"] = z.RegionName.astype(str).str.zfill(5)
    zori = z[["zip", zcols[-1]]].rename(columns={zcols[-1]: "zori"})
    zori_month = zcols[-1]

    # ---- censoring flag ----
    df["rent_all_capped"] = df["rent_all"] >= TOPCAP
    df["rent_studio_capped"] = df["rent_studio"] >= TOPCAP
    df["rent_1br_capped"] = df["rent_1br"] >= TOPCAP

    # ---- budget logic ----
    LO, HI = 2000, 3000

    def verdict(row):
        st, one = row["rent_studio"], row["rent_1br"]
        st_cap, one_cap = row["rent_studio_capped"], row["rent_1br_capped"]
        # any uncapped value in budget?
        ok = False
        if pd.notna(st) and not st_cap and LO <= st <= HI:
            ok = True
        if pd.notna(one) and not one_cap and LO <= one <= HI:
            ok = True
        if ok:
            return "in_budget"
        if (st_cap or one_cap):
            return "over_budget_capped"
        if pd.notna(st) and pd.notna(one) and st < LO and one < LO:
            return "under_budget"
        if pd.notna(st) and pd.notna(one) and st > HI and one > HI:
            return "over_budget"
        return "mixed"

    df["verdict"] = df.apply(verdict, axis=1)

    # Asking rents arrive from a separate stage (ZIP -> tract allocation), and
    # the pipeline must still run without them: the columns are optional and
    # every consumer treats them as nullable.
    if C.OUT_TRACT_ASKING.exists():
        ask = pd.read_csv(C.OUT_TRACT_ASKING, dtype={"geoid": str,
                                                     "zori_month": str})
        ask["geoid"] = ask.geoid.str.zfill(11)
        df["geoid"] = df.geoid.str.zfill(11)
        df = df.merge(
            ask.rename(columns={"zori": "ask", "ask_nzips": "ask_nzips",
                                "premium": "prem", "prem_relmoe": "prem_relmoe"}),
            on="geoid", how="left")
        print(f"asking rents merged: {df.ask.notna().sum():,} tracts")
    else:
        for c in ("ask", "zori_month", "ask_nzips", "ask_share", "prem", "prem_relmoe"):
            df[c] = np.nan
        print("asking rents: not built (run scripts/build_asking.py)")

    df.to_csv(C.OUT_TRACT_JOINED, index=False)
    print(f"\nwrote {C.OUT_TRACT_JOINED}  ({len(df):,} rows)")

    print("\n=== VERDICT (all tracts with commute) ===")
    c = df[df.commute_min.notna()]
    print(c.verdict.value_counts().to_string())

    # NOTE: shortlist.csv is owned by build_html.py, which applies the reliability
    # gates (coherence + relative MOE). Writing an ungated shortlist here produced a
    # second, contradictory file on disk. Deliberately not written from this stage —
    # one writer per artifact. This stage emits tract_joined.csv only.
    print("\n=== in-budget candidates (pre-gate; build_html.py writes the gated shortlist) ===")
    cand = c[(c.verdict == "in_budget")].copy()
    cand = cand.sort_values(["rent_1br", "commute_min"], na_position="last")
    print(f"n = {len(cand)}  (ungated — expect fewer after reliability gates)")
    show = cand[["geoid", "station", "commute_min", "rent_studio", "rent_1br", "rent_all"]].head(15)
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
