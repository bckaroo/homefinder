#!/usr/bin/env python3
"""
Fetch and extract every raw input the pipeline needs.

Idempotent: existing files are skipped unless --force is passed.
Downloads land in data/raw/ (gitignored); GTFS feeds are extracted to
data/raw/gtfs/<feed>/.

Usage:
    python3 scripts/fetch_data.py [--force]
"""
import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

GTFS_FEEDS = {
    "subway": "https://web.mta.info/developers/data/nyct/subway/google_transit.zip",
    "mnr":    "https://web.mta.info/developers/data/mnr/google_transit.zip",
    "lirr":   "https://web.mta.info/developers/data/lirr/google_transit.zip",
    "path":   "https://data.trilliumtransit.com/gtfs/path-nj-us/path-nj-us.zip",
}

DIRECT = {
    C.ACS_5YR_2024_B25064:
        "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25064.dat",
    C.ACS_5YR_2024_B25031:
        "https://www2.census.gov/programs-surveys/acs/summary_file/2024/table-based-SF/data/5YRData/acsdt5y2024-b25031.dat",
    C.GEOS_2023_5YR:
        "https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/documentation/Geos20235YR.txt",
    C.ZORI_ZIP:
        "https://files.zillowstatic.com/research/public_csvs/zori/Zip_zori_uc_sfrcondomfr_sm_month.csv",
    C.CB_TRACT_NY:
        "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_36_tract_500k.zip",
    C.CB_TRACT_NJ:
        "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_34_tract_500k.zip",
}


def download(url, dest, force=False):
    dest = Path(dest)
    if dest.exists() and not force:
        print(f"  skip (exists) {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"  get {dest.name}")
    req = urllib.request.Request(url, headers={"User-Agent": "transit-housing-analytics/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)
    print(f"      {dest.stat().st_size/1e6:.1f} MB")
    return dest


def extract_gtfs(name, zip_path, force=False):
    # extract to the path config declares for this feed (mta_subway, path, ...)
    out = C.FEEDS[name]
    marker = out / "stops.txt"
    if marker.exists() and not force:
        print(f"  skip (extracted) {out.name}")
        return out
    out.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(out)
    print(f"      extracted -> {out}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="re-download/re-extract everything")
    args = ap.parse_args()

    C.ensure_dirs()
    print(f"repo root: {C.ROOT}")
    print(f"raw dir:   {C.RAW}")

    print("\nGTFS feeds:")
    for name, url in GTFS_FEEDS.items():
        zp = C.RAW / f"gtfs_{name}.zip"
        download(url, zp, args.force)
        extract_gtfs(name, zp, args.force)

    print("\nACS / ZORI / boundaries:")
    for dest, url in DIRECT.items():
        download(url, dest, args.force)

    print("\nAll inputs present. Next:")
    print("  python3 scripts/build_graph.py")
    print("  python3 scripts/build_rent.py")
    print("  python3 scripts/join_all.py")
    print("  python3 scripts/build_html.py")


if __name__ == "__main__":
    main()
