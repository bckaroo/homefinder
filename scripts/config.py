"""
Repo-relative configuration for the transit-housing-analysis-map pipeline.

Every stage reads inputs from, and writes outputs to, paths derived from the
repo root — never from /tmp. Raw downloads live in data/raw/ (gitignored);
derived outputs live in data/ (committed).

Override the repo root with TRANSTH_MAP_ROOT if running from elsewhere.
"""
import os
from pathlib import Path

# scripts/config.py -> repo root is two levels up
ROOT = Path(os.environ.get("TRANSTH_MAP_ROOT", Path(__file__).resolve().parent.parent)).resolve()

# --- directories ---
DATA = ROOT / "data"
RAW = DATA / "raw"          # gitignored: GTFS zips, ACS .dat, shapefiles, ZORI
GTFS = RAW / "gtfs"         # extracted GTFS feeds
DOCS = ROOT / "docs"
WEB = ROOT / "web"

# --- feed directories (extracted) ---
FEEDS = {
    "subway": GTFS / "mta_subway",
    "mnr":    GTFS / "mta_mnr",
    "lirr":   GTFS / "mta_lirr",
    "path":   GTFS / "path",
}

# --- raw input files ---
ACS_5YR_2024_B25064 = RAW / "acsdt5y2024-b25064.dat"
ACS_5YR_2024_B25031 = RAW / "acsdt5y2024-b25031.dat"
GEOS_2023_5YR = RAW / "Geos20235YR.txt"
ZORI_ZIP = RAW / "Zip_zori_uc_sfrcondomfr_sm_month.csv"
CB_TRACT_NY = RAW / "cb_2023_36_tract_500k.zip"
CB_TRACT_NJ = RAW / "cb_2023_34_tract_500k.zip"
# Full-resolution TIGER/Line tracts for the map itself. The cartographic (CB)
# files are generalised to 1:500k and look like janky quadrilaterals at street
# zoom; CB stays in use for centroid maths because it is coast-clipped.
TIGER_TRACT_NY = RAW / "tl_2024_36_tract.zip"
TIGER_TRACT_NJ = RAW / "tl_2024_34_tract.zip"
AREAWATER_DIR = RAW / "areawater"   # per-county TIGER AREAWATER, fetched on demand
SIMPLIFY_M = 15                    # metres; street-scale simplification for the web map
WATER_BUFFER_M = 25                # cut water outward so no hairline of colour sits on it
MIN_PART_M2 = 5000                 # drop speck islands from clipped tracts
MIN_STREAM_M2 = 200_000            # H3010 polygons above this are rivers, below it drains

# --- derived outputs (committed) ---
OUT_STATION_TIMES = DATA / "station_times.csv"
OUT_GRAPH_STATS = DATA / "graph_stats.json"
OUT_TRACT_RENT = DATA / "tract_rent.csv"
OUT_TRACT_JOINED = DATA / "tract_joined.csv"
OUT_SHORTLIST = DATA / "shortlist.csv"
OUT_TRACT_GEOJSON = DATA / "tracts.geojson"
OUT_MAP_HTML = WEB / "index.html"
OUT_COMMUTE_MODES = DATA / "commute_modes.csv"
OUT_COMMUTE_META = DATA / "commute_modes.meta.json"

# --- asking rents: ZORI allocated from ZIP codes to tracts ---
ZCTA_METRO = RAW / "geo" / "zcta520_metro.geojson"   # TIGERweb pull, cached
ZCTA_BBOX = "-74.60,40.30,-73.30,41.40"              # W,S,E,N around study area
OUT_TRACT_ASKING = DATA / "tract_asking.csv"
MIN_RENT_FOR_PREMIUM = 500.0      # below this the ACS median is not a market rent
PREM_MOE_LIMIT = 0.30             # relative MOE above this: ratio shown as low-confidence
PREM_CAP = 3.0                   # display buckets top out here ("3x or more")

# --- street-network router (optional stage: walking and cycling modes) ---
# Port 3204 is registered in ~/.openclaw/config/ports.json as the `valhalla`
# service entry. The core pipeline must still reproduce without a running
# router: build_modes.py is optional and join_all.py degrades to nulls.
VALHALLA_URL = os.environ.get("VALHALLA_URL", "http://127.0.0.1:3204")
OSM_DIR = RAW / "osm"

# --- analysis constants ---
PENN1 = (40.75056, -73.99386)   # 1 Penn Plaza, lat/lon
PEAK_START, PEAK_END = 7 * 3600, 9 * 3600
ORIGIN_RADIUS_M = 900           # stops within this of Penn 1 seed Dijkstra
CROSS_FEED_RADIUS_M = 600       # walking transfer between different feeds
WALK_MPS = 1.35                 # ~4.9 km/h
PARENT_LINK_SECONDS = 30        # platform <-> station container
MAX_WALK_MIN = 22               # beyond this a station does not serve the tract
TOPCAP = 3501.0                 # ACS rent topcode; never render as a real number
MAX_REL_MOE = 0.30              # ACS usability threshold (relative MOE)
WATER_SHARE_MAX = 0.90          # tracts >= this share water are water bodies, not neighbourhoods
BUDGET_LO, BUDGET_HI = 2000, 3000


def ensure_dirs():
    """Create output directories; safe to call repeatedly."""
    for d in (DATA, RAW, GTFS, WEB):
        d.mkdir(parents=True, exist_ok=True)


def require(path, hint=""):
    """Fail loudly with a helpful message when a required input is missing."""
    if not Path(path).exists():
        raise FileNotFoundError(
            f"required input missing: {path}"
            + (f"\n  {hint}" if hint else "")
            + f"\n  run: python3 scripts/fetch_data.py"
        )
    return Path(path)
