"""Fetch the NYC street tree census and write a compact point file for the map.

The 2015 Street Tree Census (NYC Open Data, uvpi-gqnh) has 683,788 street trees
with real latitude/longitude — the nearest thing NYC has to the tree cover the
Montréal case study models. Every 4th tree is kept: at map scale the extra
points are invisible, and it keeps the payload under 4 MB.

Output: web/trees.json  {"step":4,"n":N,"xy":[latE5,lonE5,latE5,lonE5,...]}
Lat/lon are integers at 1e-5 deg (≈1.1 m), which is well inside the 10 m
accuracy of the source and keeps the JSON small.
"""

from __future__ import annotations

import json
import pathlib
import time

import requests

BASE = "https://data.cityofnewyork.us/resource/uvpi-gqnh.json"
STEP = 4
PAGE = 50000
OUT = pathlib.Path(__file__).resolve().parent.parent / "web" / "trees.json"


def main() -> None:
    pts: list[int] = []
    offset = 0
    seen = 0
    t0 = time.time()
    while True:
        r = requests.get(
            BASE,
            params={
                "$select": "latitude,longitude",
                "$order": "tree_id",
                "$limit": PAGE,
                "$offset": offset,
            },
            timeout=120,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break
        for k, row in enumerate(rows):
            seen += 1
            if (offset + k) % STEP:
                continue
            try:
                lat = float(row["latitude"])
                lon = float(row["longitude"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (40.4 < lat < 41.0 and -74.3 < lon < -73.6):
                continue
            pts.append(round(lat * 1e5))
            pts.append(round(lon * 1e5))
        offset += PAGE
        print(f"  {offset:>7,} fetched, {len(pts)//2:>6,} kept", flush=True)
        if len(rows) < PAGE:
            break

    OUT.write_text(json.dumps({"step": STEP, "n": len(pts) // 2, "xy": pts},
                              separators=(",", ":")))
    size = OUT.stat().st_size
    print(f"wrote {OUT} — {len(pts)//2:,} trees from {seen:,} rows, "
          f"{size/1e6:.2f} MB, {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
