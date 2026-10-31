#!/usr/bin/env python3
"""
Optional stage: walking and cycling commute times from a street-network router.

Transit times come from a GTFS timetable graph (build_graph.py). Walking and
cycling cannot: they are properties of the street network, so they come from
Valhalla running over a NY-metro OSM extract. This stage routes every tract
centroid to the analysis destination under each costing and writes
data/commute_modes.csv.

Deliberately separate from the transit graph, and deliberately optional:
a fresh clone reproduces the committed transit artifacts without a router.
join_all.py left-merges this file when present and writes nulls when it is not.

The destination is C.PENN1 for now (DEV-323 will make it an input).

Usage:
    python3 scripts/build_modes.py                  # all tracts, walk + cycle
    python3 scripts/build_modes.py --limit 25       # smoke test
    python3 scripts/build_modes.py --costings pedestrian
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import geopandas as gpd
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

# Valhalla costing -> output column. Names are prefixed with `mode` because
# `walk_min` already means walk-to-station access in tract_joined.csv.
COSTING_COLUMNS = {
    "pedestrian": "walk_mode_min",
    "bicycle": "cycle_mode_min",
}


def valhalla(path, payload=None, timeout=600):
    """POST (or GET) to the router, with a small retry ladder."""
    url = f"{C.VALHALLA_URL.rstrip('/')}{path}"
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if data else {}
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"router call failed after 3 attempts: {url}: {last}")


def require_router():
    """Fail with an actionable message, not a stack trace about sockets."""
    try:
        status = valhalla("/status", timeout=20)
    except RuntimeError as exc:
        raise SystemExit(
            f"no Valhalla answering at {C.VALHALLA_URL}\n"
            f"  {exc}\n"
            "  start it: docker start valhalla-housing  "
            "(tiles build on first start; see notes/DEVLOG.md)\n"
            "  or skip this stage: the transit artifacts do not depend on it"
        )
    return status


def tract_centroids():
    """Representative point per tract, NY + NJ — the same tracts join_all uses."""
    frames = []
    for path, label in ((C.CB_TRACT_NY, "NY"), (C.CB_TRACT_NJ, "NJ")):
        g = gpd.read_file(C.require(path, "run scripts/fetch_data.py")).to_crs(32618)
        g = g[["GEOID", "geometry"]].copy()
        g["GEOID"] = g.GEOID.astype(str)
        frames.append(g)
        print(f"  {label}: {len(g):,} tracts")
    tracts = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True),
                              geometry="geometry", crs=32618)
    cents = tracts.copy()
    cents["geometry"] = cents.geometry.representative_point()
    cents = cents.to_crs(4326)
    cents["lat"] = cents.geometry.y
    cents["lon"] = cents.geometry.x
    return cents[["GEOID", "lat", "lon"]].rename(columns={"GEOID": "geoid"})


def route_batch(frame, costing, dest, batch_size):
    """One sources_to_targets call per batch; returns minutes parallel to frame."""
    out = []
    total = len(frame)
    for start in range(0, total, batch_size):
        chunk = frame.iloc[start:start + batch_size]
        payload = {
            "sources": [{"lat": float(r.lat), "lon": float(r.lon)} for r in chunk.itertuples()],
            "targets": [{"lat": dest[0], "lon": dest[1]}],
            "costing": costing,
            "units": "minutes",
        }
        try:
            resp = valhalla("/sources_to_targets", payload)
            rows = resp.get("sources_to_targets", [[]])[0]
        except RuntimeError as exc:
            print(f"  ! batch {start}-{start + len(chunk)} failed: {exc}")
            rows = []
        for i in range(len(chunk)):
            t = None
            if i < len(rows) and isinstance(rows[i], dict):
                # unreachable pairs return a null time; keep it null rather than
                # implying a fast trip
                t = rows[i].get("time")
            out.append(None if t is None else round(float(t), 1))
        done = min(start + batch_size, total)
        print(f"  {costing}: {done:,}/{total:,}", end="\r", flush=True)
    print()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--costings", default="pedestrian,bicycle",
                    help="comma-separated Valhalla costings (pedestrian,bicycle)")
    ap.add_argument("--batch", type=int, default=100, help="sources per matrix call")
    ap.add_argument("--limit", type=int, default=None, help="route only the first N tracts")
    args = ap.parse_args()

    costings = [c.strip() for c in args.costings.split(",") if c.strip()]
    unknown = [c for c in costings if c not in COSTING_COLUMNS]
    if unknown:
        raise SystemExit(f"unsupported costing(s): {unknown}; known: {list(COSTING_COLUMNS)}")

    status = require_router()
    print(f"router: {status.get('version', '?')} at {C.VALHALLA_URL}")

    print("tract centroids:")
    cents = tract_centroids()
    if args.limit:
        cents = cents.head(args.limit)
    print(f"  routing {len(cents):,} tracts to PENN1 {C.PENN1}")

    result = cents.copy()
    for costing in costings:
        col = COSTING_COLUMNS[costing]
        result[col] = route_batch(result, costing, C.PENN1, args.batch)
        n = result[col].notna().sum()
        med = result[col].median()
        print(f"  {col}: {n:,} routed, median {med:.1f} min"
              if n else f"  {col}: nothing routed")

    keep = ["geoid"] + [COSTING_COLUMNS[c] for c in costings]
    result[keep].to_csv(C.OUT_COMMUTE_MODES, index=False)
    print(f"\nwrote {C.OUT_COMMUTE_MODES}  ({len(result):,} rows)")

    meta = {
        "router": "valhalla",
        "version": status.get("version"),
        "costings": costings,
        "destination": {"name": "1 Penn Plaza", "lat": C.PENN1[0], "lon": C.PENN1[1]},
        "extracts": sorted(p.name for p in C.OSM_DIR.glob("*.pbf")),
        "tracts_routed": int(len(result)),
        "generated_by": "scripts/build_modes.py",
    }
    C.OUT_COMMUTE_META.write_text(json.dumps(meta, indent=2) + "\n")
    print(f"wrote {C.OUT_COMMUTE_META}")


if __name__ == "__main__":
    main()
