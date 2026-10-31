#!/usr/bin/env python3
"""
Phase 4: build the deliverable — self-contained Leaflet HTML with embedded GeoJSON.
Adds reliability gates so the shortlist only shows tracts the data can actually support.
"""
import pandas as pd, numpy as np, geopandas as gpd, json, os, sys, shapely
from pathlib import Path
from shapely.geometry import mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C
import geo_prep

OUT = str(C.DATA)
TOPCAP = C.TOPCAP
MAX_REL_MOE = C.MAX_REL_MOE
WALK_LIMIT = C.MAX_WALK_MIN


def main():
    df = pd.read_csv(C.require(C.OUT_TRACT_JOINED, "run scripts/join_all.py first"),
                     dtype={"geoid": str})
    df["geoid"] = df.geoid.str.zfill(11)

    # ---- reliability gates ----
    df["rel_moe_1br"] = df.rent_1br_moe / df.rent_1br
    df["rel_moe_studio"] = df.rent_studio_moe / df.rent_studio
    df["reliable_1br"] = (df.rel_moe_1br <= MAX_REL_MOE) & df.rent_1br.notna()
    df["reliable_studio"] = (df.rel_moe_studio <= MAX_REL_MOE) & df.rent_studio.notna()
    # coherence: a 1BR must not price below a studio
    df["coherent"] = ~(
        df.rent_1br.notna() & df.rent_studio.notna() & (df.rent_1br < df.rent_studio * 0.9)
    )
    df["trustworthy"] = df.coherent & (df.reliable_1br | df.reliable_studio)

    LO, HI = 2000, 3000
    df["in_budget"] = (
        (df.rent_studio.between(LO, HI) & df.reliable_studio & ~df.rent_studio_capped)
        | (df.rent_1br.between(LO, HI) & df.reliable_1br & ~df.rent_1br_capped)
    )
    df["commutable"] = df.commute_min.notna() & (df.commute_min <= 60)

    short = df[df.in_budget & df.commutable & df.trustworthy].copy()
    print(f"in_budget: {df.in_budget.sum()}")
    print(f"  AND <=60min: {(df.in_budget & df.commutable).sum()}")
    print(f"  AND trustworthy: {len(short)}")

    # ---- geometry for the map ----
    print("loading geometry...")
    keep = df[df[["commute_min", "walk_mode_min", "cycle_mode_min"]].notna().any(axis=1)].copy()
    m, n_water = geo_prep.build(keep, log=print)
    geo_rows = keep.copy()   # `keep` is reused for station bookkeeping further down

    def _props(r):
        return {
            "geoid": r.geoid,
            "station": r.station,
            "net": r.station_net,
            "commute": None if pd.isna(r.commute_min) else round(float(r.commute_min), 1),
            # door-to-door times from the Valhalla street router; null when the
            # tract is outside the routed radius or unreachable on foot/bike
            "walk": None if pd.isna(r.walk_mode_min) else round(float(r.walk_mode_min), 1),
            "cycle": None if pd.isna(r.cycle_mode_min) else round(float(r.cycle_mode_min), 1),
            "ride": None if pd.isna(r.ride_min) else round(float(r.ride_min), 1),
            "walkm": None if pd.isna(r.station_walk_min) else round(float(r.station_walk_min), 1),
            "stu": None if pd.isna(r.rent_studio) else int(r.rent_studio),
            "one": None if pd.isna(r.rent_1br) else int(r.rent_1br),
            "allr": None if pd.isna(r.rent_all) else int(r.rent_all),
            "stu_cap": bool(r.rent_studio_capped) if pd.notna(r.rent_studio_capped) else False,
            "one_cap": bool(r.rent_1br_capped) if pd.notna(r.rent_1br_capped) else False,
            "stu_rel": None if pd.isna(r.rel_moe_studio) else round(float(r.rel_moe_studio), 2),
            "one_rel": None if pd.isna(r.rel_moe_1br) else round(float(r.rel_moe_1br), 2),
            "ok": bool(r.trustworthy and r.in_budget and r.commutable),
            "trust": bool(r.trustworthy),
            "budget": bool(r.in_budget),
            # asking rents: ZORI allocated down from ZIP codes. prem is null
            # where the ACS paid rent is too small or too noisy to divide by.
            "ask": None if pd.isna(r.get("ask")) else int(r.ask),
            "askz": None if pd.isna(r.get("ask_nzips")) else int(r.ask_nzips),
            "prem": None if pd.isna(r.get("prem")) else round(float(r.prem), 2),
            "premlo": bool(pd.notna(r.get("prem_relmoe")) and r.prem_relmoe > C.PREM_MOE_LIMIT),
        }

    gj = geo_prep.feature_collection(m, _props)
    gp = str(C.OUT_TRACT_GEOJSON)
    json.dump(gj, open(gp, "w"), separators=(",", ":"))
    print(f"  wrote {gp}  ({os.path.getsize(gp)/1e6:.1f} MB)")

    # ---- transit lines (from GTFS shapes) ----
    print("building transit lines...")
    lines = []
    NET_COLOR = {"subway": "#1f6feb", "mnr": "#c1121f", "lirr": "#b5179e", "path": "#0f766e"}
    NET_LABEL = {"subway": "Subway", "mnr": "Metro-North", "lirr": "LIRR", "path": "PATH"}
    SUBWAY_MAP = {  # the MTA printed-map palette, keyed by route bullet
        "1": "EE352E", "2": "EE352E", "3": "EE352E",
        "4": "00933C", "5": "00933C", "6": "00933C", "6X": "00933C",
        "7": "B933AD", "7X": "B933AD",
        "A": "0039A6", "C": "0039A6", "E": "0039A6",
        "B": "FF6319", "D": "FF6319", "F": "FF6319", "FX": "FF6319", "M": "FF6319",
        "G": "6CBE45", "J": "996633", "Z": "996633", "L": "A7A9AC",
        "N": "FCCC0A", "Q": "FCCC0A", "R": "FCCC0A", "W": "FCCC0A",
        "S": "808183", "SS": "808183",
    }
    FEEDS = {k: str(v) for k, v in C.FEEDS.items()}
    routes = {"subway": "routes.txt", "mnr": "routes.txt",
              "lirr": "routes.txt", "path": "routes.txt"}
    # Official MTA line colours come straight out of the feed: routes.txt
    # carries route_color (EE352E for the 1/2/3, 0039A6 for the A/C/E, ...), so
    # there is no hand-maintained palette to drift. NET_COLOR is only the
    # fallback for feeds that ship no colour, and the marker for MNR/LIRR/PATH.
    def _hex(c):
        c = str(c or "").strip().lstrip("#")
        return f"#{c}" if len(c) == 6 else None

    route_info = {}
    for net_name, d in FEEDS.items():
        try:
            rt = pd.read_csv(f"{d}/routes.txt", dtype=str).fillna("")
        except Exception:
            continue
        for r in rt.itertuples():
            route_info[(net_name, str(r.route_id))] = {
                "short": str(getattr(r, "route_short_name", "") or "").strip(),
                "color": _hex(getattr(r, "route_color", "")) or NET_COLOR[net_name],
            }

    for net_name, d in FEEDS.items():
        try:
            sh = pd.read_csv(f"{d}/shapes.txt", dtype={"shape_id": str})
            tr = pd.read_csv(f"{d}/trips.txt", dtype={"shape_id": str})
        except Exception:
            continue
        # one shape per route to keep the payload small
        seen = set()
        for rid, grp in tr.groupby("route_id"):
            sid = grp.shape_id.dropna().iloc[0] if len(grp.shape_id.dropna()) else None
            if not sid or (net_name, rid) in seen:
                continue
            seen.add((net_name, rid))
            pts = sh[sh.shape_id == sid].sort_values("shape_pt_sequence")
            if len(pts) < 2:
                continue
            coords = [[round(float(y), 5), round(float(x), 5)]
                      for x, y in zip(pts.shape_pt_lon, pts.shape_pt_lat)][::3]
            info = route_info.get((net_name, str(rid)), {})
            label = info.get("short") or str(rid)
            hexc = SUBWAY_MAP.get(label) if net_name == "subway" else None
            lines.append({"net": net_name, "route": str(rid), "label": label,
                          "color": (f"#{hexc}" if hexc
                                    else info.get("color", NET_COLOR[net_name])),
                          "coords": coords})
    print(f"  {len(lines)} route lines")

    # ---- stations within 60 min ----
    st = pd.read_csv(C.require(C.OUT_STATION_TIMES, "run scripts/build_graph.py first"))
    st = st[st["min"] <= 60]

    # ---- line membership and station symbols ----
    # station_times.csv is keyed by GTFS stop_id, and for the subway that means
    # northbound and southbound *platforms* (127N, 127S) rather than the station
    # (127) — so the same stop arrived twice, at the same coordinates. Roll
    # platforms up to their parent station, keep the best platform time, and ask
    # stop_times which routes actually call there.
    line_at, parent = {}, {}
    try:
        sd = FEEDS["subway"]
        stops = pd.read_csv(f"{sd}/stops.txt", dtype=str).fillna("")
        stx = pd.read_csv(f"{sd}/stop_times.txt", dtype=str, usecols=["trip_id", "stop_id"])
        trp = pd.read_csv(f"{sd}/trips.txt", dtype=str, usecols=["trip_id", "route_id"])
        parent = dict(zip(stops.stop_id, stops.parent_station.replace("", np.nan)))
        stx["route_id"] = stx.trip_id.map(dict(zip(trp.trip_id, trp.route_id)))
        stx["station"] = [parent.get(s) if isinstance(parent.get(s), str) else s
                          for s in stx.stop_id]
        for sid, grp in stx.groupby("station"):
            line_at[sid] = sorted({str(r) for r in grp.route_id.dropna()})
        st = st.copy()
        st["station_id"] = [
            (parent.get(s) if isinstance(parent.get(s), str) else s) if n == "subway" else None
            for s, n in zip(st.stop_id.astype(str), st.net)
        ]
    except Exception as exc:
        print(f"  station line lookup unavailable: {exc}")

    def _color_for(rid):
        short = route_info.get(("subway", rid), {}).get("short") or rid
        hexc = SUBWAY_MAP.get(short) or route_info.get(("subway", rid), {}).get("color")
        return (f"#{hexc}" if hexc else NET_COLOR["subway"]), short

    def _symbol(row):
        if row["net"] != "subway":
            return [NET_COLOR[row["net"]]], NET_LABEL[row["net"]], False
        rids = line_at.get(row["station_id"], []) if row.get("station_id") else []
        cols, shorts = [], []
        for rid in rids:
            c, s = _color_for(rid)
            cols.append(c); shorts.append(s)
        if not cols:
            return [NET_COLOR["subway"]], "", False
        uniq = list(dict.fromkeys(cols))
        return uniq, " · ".join(shorts), len(uniq) > 1

    stations = []
    seen = set()
    for _, r in st.sort_values("min").iterrows():  # best time first: keeps it on collapse
        if not r["name"] or str(r["name"]) == "nan":
            continue
        key = (r["net"], r.get("station_id") or f'{r["name"]}@{round(float(r.lat),4)},{round(float(r.lon),4)}')
        if key in seen:
            continue
        seen.add(key)
        cols, routes_txt, xfer = _symbol(r)
        stations.append({"name": str(r["name"]).strip(), "net": r.net,
                         "lat": round(float(r.lat), 5), "lon": round(float(r.lon), 5),
                         "min": round(float(r["min"]), 1),
                         "colors": cols, "routes": routes_txt, "xfer": bool(xfer)})
    # The feed splits a station complex into separate stations — Times Sq is
    # four of them (1/2/3, 7, S, N/Q/R/W), Fulton St five — but the MTA map
    # draws one interchange dot per complex. Cluster subway symbols within
    # 200 m and merge their line sets; anything further apart stays separate.
    ORDER = ["1", "2", "3", "4", "5", "6", "6X", "7", "7X", "A", "C", "E",
             "B", "D", "F", "FX", "M", "G", "J", "Z", "L", "N", "Q", "R", "W",
             "S", "SIR"]
    sub = [i for i, s in enumerate(stations) if s["net"] == "subway"]
    if sub:
        pts = gpd.GeoSeries(
            shapely.points([stations[i]["lon"] for i in sub],
                           [stations[i]["lat"] for i in sub]), crs=4326).to_crs(32618)
        pairs = shapely.STRtree(pts.values).query(pts.values, predicate="dwithin",
                                                  distance=200)
        boss = {i: i for i in sub}

        def find(i):
            while boss[i] != i:
                boss[i] = boss[boss[i]]
                i = boss[i]
            return i

        # Two platforms 150 m apart are not necessarily one complex: 14 St on
        # the 1/2/3, the A/C/E, the F/M and the L are four separate stations.
        # The MTA map merges a complex only where the stations carry the same
        # name (or a name the other extends), so gate the merge on that too.
        def same_complex(a, b):
            na, nb = stations[sub[a]]["name"].lower(), stations[sub[b]]["name"].lower()
            return na == nb or na.startswith(nb) or nb.startswith(na)

        for a, b in zip(pairs[0], pairs[1]):
            if not same_complex(a, b):
                continue
            ra, rb = find(sub[a]), find(sub[b])
            if ra != rb:
                boss[rb] = ra
        groups = {}
        for i in sub:
            groups.setdefault(find(i), []).append(i)

        merged, dropped = [], set()
        for root, members in groups.items():
            keep = stations[root]
            dropped.update(i for i in members if i != root)
            cols, rts = list(keep["colors"]), keep["routes"].split(" · ") if keep["routes"] else []
            best = keep["min"]
            for i in members:
                if i == root:
                    continue
                s2 = stations[i]
                best = min(best, s2["min"])
                cols += [c for c in s2["colors"] if c not in cols]
                if s2["routes"]:
                    rts += [x for x in s2["routes"].split(" · ") if x not in rts]
            def rank(x):
                return ORDER.index(x) if x in ORDER else 99
            rts = sorted(rts, key=rank)
            keep.update(colors=cols, routes=" · ".join(rts),
                        xfer=len(cols) > 1, min=best)
        stations = [s for i, s in enumerate(stations) if i not in dropped]

    print(f"  {len(stations)} station symbols "
          f"({sum(1 for s in stations if s['xfer'])} interchanges)")

    # Asking-rent context travels in meta so the Sources pane states measured
    # facts (month, coverage, median premium) instead of hard-coded folklore.
    zm = geo_rows.zori_month.dropna().astype(str)
    ask_ctx = {
        "month": zm.iloc[0][:7] if len(zm) else None,
        "pct": round(100 * geo_rows.ask.notna().mean(), 0) if "ask" in geo_rows else None,
        "prem_med": round(float(geo_rows.prem.median()), 2) if geo_rows.prem.notna().any() else None,
        "prem_moe_limit": C.PREM_MOE_LIMIT,
        "n_lowconf": int(geo_rows.prem_relmoe.gt(C.PREM_MOE_LIMIT).sum()) if geo_rows.prem.notna().any() else 0,
    }

    meta = {"zori_month": (zm.iloc[0][:10] if len(zm) else "2026-09-30"),
            "acs_vintage": "2017-2021..2020-2024 (5yr 2024)",
            "topcap": TOPCAP, "max_rel_moe": MAX_REL_MOE,
            "water_share_max": C.WATER_SHARE_MAX, "water_tracts_removed": n_water,
            "ask": ask_ctx}

    html = render(gj, lines, stations, meta)
    C.ensure_dirs()
    open(C.OUT_MAP_HTML, "w").write(html)
    print(f"  wrote {C.OUT_MAP_HTML} ({len(html)/1e6:.1f} MB)")

    # ---- shortlist table ----
    short = short.sort_values(["commute_min", "rent_1br"], na_position="last")
    cols = ["geoid", "station", "station_net", "commute_min", "rent_studio", "rent_1br",
            "rel_moe_1br", "rent_all"]
    short[cols].to_csv(C.OUT_SHORTLIST, index=False)
    print(f"\nshortlist n={len(short)} -> {C.OUT_SHORTLIST}")
    print(short[["station", "commute_min", "rent_studio", "rent_1br"]].head(20).to_string(index=False))


def render(gj, lines, stations, meta):
    data = json.dumps({"tracts": gj, "lines": lines, "stations": stations, "meta": meta},
                      separators=(",", ":"))
    a = meta.get("ask") or {}
    if a.get("prem_med"):
        pm = a["prem_med"]
        warn = (f"Across the mapped tracts the market asks <b>{pm:.2f}×</b> the paid "
                f"median (Zillow ZORI, {a.get('month') or meta['zori_month']}), so a tract "
                f"showing $2,400 here is asking about ${2400*pm:,.0f}. The gap is widest "
                f"where turnover is high and rent control holds the sitting stock down.")
        method = (f"Zillow Observed Rent Index, {a.get('month') or meta['zori_month']}, published "
                  f"per ZIP code and allocated to tracts by area-weighting the ZIP Code "
                  f"Tabulation Area overlaps (TIGERweb 2024 boundaries, areas measured in "
                  f"UTM 18N). {a.get('pct') or 0:.0f}% of mapped tracts fall in a ZCTA that "
                  f"carries an index; the rest are left unpainted rather than extrapolated. "
                  f"ZORI is a constant-quality index of what is advertised, not a survey of "
                  f"what this tract actually pays, and a tract straddling several ZIPs is a "
                  f"blend (the detail card names how many). Ratios built on an ACS margin of "
                  f"error above {100*(a.get('prem_moe_limit') or 0):.0f}% are drawn faded "
                  f"({a.get('n_lowconf') or 0} tracts).")
    else:
        warn = "An asking-rent comparison was not built for this run."
        method = "Run <code>scripts/build_asking.py</code> to add Zillow ZORI."
    return (TEMPLATE.replace("__DATA__", data)
                    .replace("__ASKWARN__", warn)
                    .replace("__ASKMETHOD__", method))


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Housing Explorer — an open data rent map</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%231f4d3d'/%3E%3Cpath d='M2 11h12M4 8h8M6 5h4' stroke='%23fdfcf6' stroke-width='1.4'/%3E%3C/svg%3E">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Public+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
<link rel="stylesheet" href="./vendor/maplibre-gl.css"/>
<style>
 :root{
   --paper:#edf0e5; --card:#fdfcf6; --card-2:#f6f7ef; --line:#dfe3d6;
   --ink:#254c47; --ink-soft:#3d5f58; --muted:#6b857c; --muted-2:#8d9c93;
   --accent:#247d70; --accent-soft:#e3eddd;
   --ok:#2f8f6f; --warn:#d29a3a; --bad:#c25b52; --flat:#c9d2c3;
   --r:16px; --r-lg:22px;
   --shadow:0 1px 2px rgba(30,50,45,.06),0 12px 32px -12px rgba(30,50,45,.22);
   --font:"Public Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
   --serif:Georgia,"Iowan Old Style","Times New Roman",serif;
 }
 *{box-sizing:border-box}
 html,body{height:100%}
 body{margin:0;font-family:var(--font);font-size:14px;line-height:1.45;color:var(--ink);background:var(--paper)}
 button{font-family:inherit;font-size:inherit;color:inherit;cursor:pointer}
 a{color:var(--accent)}

 /* ---------- top bar ---------- */
 #top{position:fixed;inset:0 0 auto 0;height:56px;display:flex;align-items:center;
      justify-content:space-between;padding:0 22px;background:var(--card);
      border-bottom:1px solid var(--line);z-index:30}
 #brand{display:flex;align-items:center;gap:10px;font-weight:600;letter-spacing:-.1px}
 #mark{width:22px;height:22px;flex:none}
 #top .right{display:flex;align-items:center;gap:18px}
 #top .right a{font-size:13px;color:var(--ink-soft);text-decoration:none}
 #top .ghost{border:1px solid #b9c4b6;background:transparent;border-radius:8px;
      padding:6px 12px;font-size:13px;font-weight:600;color:var(--ink-soft)}
 #top .ghost:hover{background:var(--accent-soft);border-color:var(--accent)}

 /* ---------- map ---------- */
 #map{position:fixed;inset:56px 0 0 0}

 /* ---------- left column ---------- */
 #left{position:fixed;top:76px;left:22px;width:352px;max-height:calc(100vh - 156px);
       display:flex;flex-direction:column;gap:12px;z-index:20}
 .card{background:var(--card);border-radius:var(--r);box-shadow:var(--shadow)}

 #title{padding:20px 22px 18px}
 .eyebrow{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);
          font-weight:600}
 #title h1{margin:6px 0 0;font-family:var(--serif);font-weight:400;font-size:46px;
            letter-spacing:-.02em;line-height:1;color:var(--ink)}
 #title h1 .of{font-family:var(--font);font-size:15px;font-weight:500;letter-spacing:0;
            color:var(--muted);vertical-align:super;margin-left:2px}
 #title p{margin:10px 0 0;color:var(--muted);font-size:13.5px;line-height:1.5}

 /* tabs */
 #tabs{display:flex;gap:4px;padding:5px;background:var(--card);border-radius:var(--r)}
 #tabs button{flex:1;border:0;background:transparent;border-radius:11px;padding:8px 6px;
              font-size:13px;font-weight:600;color:var(--muted)}
 #tabs button.on{background:var(--accent);color:#fff}

 /* panes */
 #panes{flex:1;min-height:0;overflow:hidden;display:flex}
 .pane{display:none;flex-direction:column;min-height:0;width:100%}
 .pane.on{display:flex}

 /* explore */
 #pane-explore{padding:16px 18px 18px;gap:14px;overflow:auto}
 .lbl{font-size:10.5px;letter-spacing:.13em;text-transform:uppercase;color:var(--muted);font-weight:600}
 .seg{display:flex;background:var(--card-2);border-radius:11px;padding:3px;gap:3px}
 .seg button{flex:1;border:0;background:transparent;border-radius:9px;padding:7px 4px;
             font-size:12.5px;font-weight:600;color:var(--muted)}
 .seg button.on{background:#fff;color:var(--ink);box-shadow:0 1px 3px rgba(30,50,45,.12)}
 .slider{display:grid;gap:6px}
 .slider .top{display:flex;justify-content:space-between;align-items:baseline}
 .slider .val{font-weight:600;font-size:13px;color:var(--ink);font-variant-numeric:tabular-nums}
 input[type=range]{-webkit-appearance:none;appearance:none;width:100%;height:4px;border-radius:4px;
   background:#dde3d6;outline:none}
 input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;
   border-radius:50%;background:var(--accent);border:2px solid #fff;box-shadow:0 1px 3px rgba(0,0,0,.25)}
 input[type=range]::-moz-range-thumb{width:14px;height:14px;border-radius:50%;background:var(--accent);
   border:2px solid #fff}
 .swrow{display:flex;align-items:center;justify-content:space-between;padding:2px 0;font-size:13px}
 .swrow span{color:var(--ink-soft)}
 .switch{position:relative;width:38px;height:21px;flex:none}
 .switch input{opacity:0;width:100%;height:100%;margin:0;cursor:pointer;position:relative;z-index:2}
 .switch .track{position:absolute;inset:0;border-radius:11px;background:#d5ddd1;transition:.15s}
 .switch .knob{position:absolute;top:2px;left:2px;width:17px;height:17px;border-radius:50%;
   background:#fff;box-shadow:0 1px 2px rgba(0,0,0,.2);transition:.15s;pointer-events:none}
 .switch input:checked ~ .track{background:var(--accent)}
 .switch input:checked ~ .knob{left:19px}
 .keys{display:grid;gap:7px;padding-top:4px;border-top:1px solid var(--line)}
 /* `[hidden]` loses to `display:grid`, so the mode switch needs this to hide a legend. */
 .keys[hidden]{display:none}
 .kv b.dim{color:var(--muted);font-weight:500;font-size:12px;letter-spacing:.02em}
 .krow{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink-soft)}
 .sw{width:13px;height:13px;border-radius:4px;flex:none}
 .sw.dot{border-radius:50%}
.ramp{height:9px;border-radius:5px;margin:0 0 4px}
.rampend{display:flex;justify-content:space-between;font-size:11px;color:var(--ink-soft);
  font-variant-numeric:tabular-nums;margin-bottom:6px}
 .hint{font-size:11.5px;color:var(--muted);line-height:1.45}

 /* shortlist */
 #pane-list{min-height:0}
 .thead{display:grid;grid-template-columns:1fr 58px 62px 62px;gap:4px;padding:12px 16px 8px;
        font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
        font-weight:600;border-bottom:1px solid var(--line)}
 .thead span:nth-child(n+2),.trow span:nth-child(n+2){text-align:right}
 #list{overflow:auto;flex:1;padding:2px 0 6px}
 .trow{display:grid;grid-template-columns:1fr 58px 62px 62px;gap:4px;padding:8px 16px;
       font-size:12.5px;border-bottom:1px solid #eef1e7;cursor:pointer;
       font-variant-numeric:tabular-nums}
 .trow:hover{background:var(--accent-soft)}
 .trow b{font-weight:600;color:var(--ink)}
 .trow .m{color:var(--muted)}
 #count{padding:9px 16px;border-top:1px solid var(--line);font-size:11.5px;color:var(--muted)}

 /* sources */
 #pane-src{padding:16px 18px;gap:14px;overflow:auto}
 #pane-src h4{margin:0 0 4px;font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
 #pane-src p{margin:0;font-size:12.5px;line-height:1.55;color:var(--ink-soft)}
 #pane-src .warn{background:#fbf3e2;border:1px solid #eddcbb;border-radius:12px;padding:11px 13px;
   font-size:12.5px;line-height:1.5;color:#7a5a1f}

 /* ---------- right rail ---------- */
 /* The rail anchors bottom-right and the detail card sits to its left, so the
   two never collide however tall the card grows or short the window is. */
#rail{position:fixed;right:22px;bottom:22px;display:grid;gap:8px;z-index:22}
 #rail button{width:44px;height:44px;border-radius:50%;border:1px solid var(--line);
   background:var(--card);box-shadow:var(--shadow);display:grid;place-items:center;
   font-size:17px;color:var(--ink-soft);line-height:1}
 #rail button:hover{background:var(--accent-soft);color:var(--accent);border-color:#c9d7c3}
 #rail button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
 #rail svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.7;
   stroke-linecap:round;stroke-linejoin:round}

 /* ---------- detail card (bottom right) ---------- */
 #detail{position:fixed;right:80px;bottom:22px;width:322px;padding:18px 20px 20px;z-index:21;
   display:none}
 #detail.on{display:block}
 #detail .x{position:absolute;top:12px;right:12px;border:0;background:transparent;font-size:18px;
   color:var(--muted);line-height:1;padding:2px 4px}
 #detail h3{margin:6px 0 2px;font-family:var(--serif);font-weight:400;font-size:26px;
   letter-spacing:-.01em;color:var(--ink);line-height:1.15}
 #detail .net{font-size:12px;color:var(--muted);margin-bottom:12px}
 #detail .big{display:flex;align-items:baseline;gap:8px;margin:10px 0 12px}
 #detail .big b{font-size:27px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
 #detail .big span{font-size:12px;color:var(--muted)}
 #detail .kv{display:flex;justify-content:space-between;padding:4px 0;font-size:12.5px;
   border-top:1px solid #eef1e7}
 #detail .kv span:first-child{color:var(--muted)}
 #detail .kv b{font-weight:600;font-variant-numeric:tabular-nums}
 #detail .note{margin-top:10px;font-size:11px;color:var(--muted);line-height:1.45}

 /* ---------- attribution ---------- */
 #attr{position:fixed;left:22px;bottom:22px;z-index:20;background:rgba(253,252,246,.92);
   border:1px solid var(--line);border-radius:10px;padding:6px 12px;font-size:11px;
   color:var(--muted);backdrop-filter:blur(4px)}
 #attr a{color:var(--muted);text-decoration:none;border-bottom:1px solid #cfd8c9}

 /* maplibre overrides */
 .maplibregl-ctrl-attrib{display:none!important}
 .maplibregl-popup-content{background:var(--card);color:var(--ink);font-family:var(--font);
   font-size:12.5px;padding:11px 13px;border-radius:12px;border:1px solid var(--line);
   box-shadow:var(--shadow)}
 .maplibregl-popup-content b{font-size:13px}
 .maplibregl-popup-tip{border-top-color:var(--card)!important;border-bottom-color:var(--card)!important}
 #pin{width:14px;height:14px;border-radius:50%;background:var(--accent);border:2.5px solid #fff;
   box-shadow:0 0 0 4px rgba(36,125,112,.22)}

 @media (max-width:900px){
   #left{left:10px;right:10px;width:auto;top:66px;max-height:52vh}
   #title h1{font-size:34px}
   #rail{right:10px;bottom:10px}
   #detail{left:10px;right:70px;width:auto;bottom:10px}
   #attr{display:none}
 }
</style></head><body>

<div id="top">
  <div id="brand">
    <svg id="mark" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="2" y="4" width="20" height="16" rx="3" fill="#247d70"/>
      <path d="M6 9h6M6 13h9M6 17h5" stroke="#fdfcf6" stroke-width="1.6" stroke-linecap="round"/>
      <circle cx="17.5" cy="8.5" r="2.4" fill="#fdfcf6"/>
    </svg>
    Housing Explorer
  </div>
  <div class="right">
    <a href="#" id="toSources">Method &amp; sources</a>
    <button class="ghost" id="resetTop">Reset view</button>
  </div>
</div>

<div id="map"></div>

<div id="left">
  <div class="card" id="title">
    <div class="eyebrow">An open data housing map</div>
    <h1>New York<span class="of">/ 60 min</span></h1>
    <p>Census tracts within an hour of Penn Station, coloured by what they
       actually cost. Choose a mode, set a rent band, read the map.</p>
  </div>

  <div id="tabs">
    <button data-pane="explore" class="on">Explore</button>
    <button data-pane="list">Shortlist</button>
    <button data-pane="src">Sources</button>
  </div>

  <div class="card" id="panes">
    <div class="pane on" id="pane-explore">
      <div class="lbl">Travel mode</div>
      <div class="seg" id="mode">
        <button data-v="transit" class="on">Transit</button>
        <button data-v="cycle">Bike</button>
        <button data-v="walk">Walk</button>
      </div>

      <div class="lbl">Colour by</div>
      <div class="seg" id="cmode">
        <button data-v="fit" class="on">Budget fit</button>
        <button data-v="ask">Asking</button>
        <button data-v="prem">Premium</button>
      </div>

      <div class="slider">
        <div class="top"><span class="lbl">Max commute</span><span class="val"><b id="cmaxlbl">60</b> min</span></div>
        <input id="cmax" type="range" min="10" max="60" step="5" value="60">
        <div class="hint" id="hintC">Door to door, including the walk at both ends.</div>
      </div>

      <div class="slider">
        <div class="top"><span class="lbl">Rent floor</span><span class="val" id="rminv">$2,000</span></div>
        <input id="rmin" type="range" min="1000" max="4500" step="100" value="2000">
      </div>

      <div class="slider">
        <div class="top"><span class="lbl">Rent ceiling</span><span class="val" id="rmaxv">$3,000</span></div>
        <input id="rmax" type="range" min="1200" max="6000" step="100" value="3000">
      </div>

      <div class="swrow"><span>In-budget tracts only</span>
        <label class="switch"><input type="checkbox" id="onlyBudget" checked><span class="track"></span><span class="knob"></span></label></div>
      <div class="swrow"><span>Transit lines</span>
        <label class="switch"><input type="checkbox" id="showLines" checked><span class="track"></span><span class="knob"></span></label></div>
      <div class="swrow"><span>Stations</span>
        <label class="switch"><input type="checkbox" id="showStations" checked><span class="track"></span><span class="knob"></span></label></div>

      <div class="keys" id="keysFit">
        <div class="krow"><span class="sw" style="background:var(--ok)"></span>Median rent in your band (reliable)</div>
        <div class="krow"><span class="sw" style="background:var(--warn)"></span>Above ceiling, or estimate too noisy</div>
        <div class="krow"><span class="sw" style="background:var(--bad)"></span>Over budget</div>
        <div class="krow"><span class="sw" style="background:var(--flat)"></span>Cheaper than your floor</div>
        <div class="krow"><span class="sw dot" style="background:#fff;border:1.5px solid #254c47"></span>Subway interchange</div>
      </div>

      <div class="keys" id="keysAsk" hidden>
        <div class="lbl" style="margin-bottom:8px">Asking rent, all units</div>
        <div class="ramp" style="background:linear-gradient(90deg,#e8eeea,#93bcb3,#33786c,#1d5750)"></div>
        <div class="rampend"><span>$1,800</span><span>$4,800+</span></div>
        <div class="krow"><span class="sw" style="background:var(--flat)"></span>No ZIP matched this tract</div>
      </div>

      <div class="keys" id="keysPrem" hidden>
        <div class="lbl" style="margin-bottom:8px">Asking ÷ paid rent</div>
        <div class="ramp" style="background:linear-gradient(90deg,#eae3d2,#d6ab72,#c2824d,#a2532f)"></div>
        <div class="rampend"><span>1.0×</span><span>2.0×+</span></div>
        <div class="krow"><span class="sw" style="background:#cfc9b6"></span>Paid rent too noisy to divide by (faded)</div>
      </div>
    </div>

    <div class="pane" id="pane-list">
      <div class="thead"><span>Tract / station</span><span>Min</span><span>Studio</span><span>1BR</span></div>
      <div id="list"><div id="rows"></div></div>
      <div id="count"></div>
    </div>

    <div class="pane" id="pane-src">
      <div>
        <h4>Rents</h4>
        <p>ACS 5-year 2020–2024, table B25031: median gross rent by unit size, per
           census tract. Topcoded at $3,501.</p>
      </div>
      <div class="warn"><b>These are paid rents, not asking rents.</b> __ASKWARN__</div>
      <div>
        <h4>Asking rents</h4>
        <p>__ASKMETHOD__</p>
      </div>
      <div>
        <h4>Commute</h4>
        <p>Transit times are a GTFS graph built from MTA, LIRR, Metro-North and PATH
          timetables. Bike and walk times come from the OpenStreetMap street network
          (Valhalla), computed for the 3,274 tracts within 30 km of Penn 1. A tract with
          no routed time stays unshaded under those modes — nothing is inferred.</p>
      </div>
      <div>
        <h4>Buildings, trees, streets</h4>
        <p>Buildings are extruded live from OpenFreeMap's OpenMapTiles vector tiles,
          which carry modelled heights; nothing is downloaded per building. Street
          trees are the 2015 NYC Street Tree Census (every 4th of 683,788, for
          payload size). Streets are re-drawn over the 3D mass so the grid stays
          legible. Buildings are deliberately 42% opaque — solid enough to read as a
          city, translucent enough to keep the tract colour underneath.</p>
      </div>
      <div>
        <h4>Geometry</h4>
        <p>Census tracts from TIGER/Line 2024, coast-clipped against TIGER water
          polygons and simplified as a coverage at 15 m, so neighbouring tracts keep
          identical edges. Tracts that are ≥ 90% water are dropped.</p>
      </div>
      <div>
        <h4>Data</h4>
        <p>U.S. Census Bureau ACS · TIGER/Line · NYC Open Data ZORI · MTA, LIRR,
          Metro-North, PATH GTFS · OpenStreetMap contributors · OpenFreeMap tiles.</p>
      </div>
    </div>
  </div>
</div>

<div id="rail">
  <button id="btnHome" title="Centre on Penn 1" aria-label="Centre on Penn 1">
    <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="7"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="2.2" fill="currentColor" stroke="none"/></svg>
  </button>
  <button id="btn3d" title="Toggle 3D buildings" aria-label="Toggle 3D buildings">
    <svg viewBox="0 0 24 24"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9L12 3z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/></svg>
  </button>
  <button id="btnIn" title="Zoom in" aria-label="Zoom in">
    <svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>
  </button>
  <button id="btnOut" title="Zoom out" aria-label="Zoom out">
    <svg viewBox="0 0 24 24"><path d="M5 12h14"/></svg>
  </button>
  <button id="btnReset" title="Reset" aria-label="Reset view">
    <svg viewBox="0 0 24 24"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>
  </button>
</div>

<div class="card" id="detail"></div>

<div id="attr">
  © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> ·
  <a href="https://openfreemap.org" target="_blank" rel="noopener">OpenFreeMap</a> ·
  Census ACS 2020–2024 · ZORI
</div>

<script type="module">
import * as maplibregl from './vendor/maplibre-gl.mjs';
const D = __DATA__;
const meta = D.meta;
const NETC={subway:'#1f6feb',mnr:'#c1121f',lirr:'#b5179e',path:'#0f766e'};

// Basemap: OpenFreeMap "positron" — a pale, low-label style, so the choropleth
// and the MTA line colours carry the colour and the streets stay legible.
const map = new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/positron',
  center:[-73.95,40.78],
  zoom:10,
  pitch:0,
  bearing:0,
  attributionControl:false
});
window.__map = map;   // QA hook: pitch/zoom assertions from scripts/check_render.py

const HOME={center:[-73.99386,40.75056],zoom:12.4};

new maplibregl.Marker({element:(()=>{const d=document.createElement('div');d.id='pin';return d;})()})
  .setLngLat([-73.99386,40.75056]).addTo(map);

// D.lines coords are [lat,lon] (Leaflet order); GeoJSON wants [lon,lat].
const lineFC={type:'FeatureCollection',features:D.lines
  .filter(l=>l.coords&&l.coords.length>1)
  .map(l=>({type:'Feature',properties:{color:l.color,net:l.net,route:l.route},
            geometry:{type:'LineString',coordinates:l.coords.map(c=>[c[1],c[0]])}}))};

const stnFC={type:'FeatureCollection',features:D.stations.map(s=>({
  type:'Feature',
  properties:{name:s.name,min:s.min,net:s.net,routes:s.routes||'',
              xfer:!!s.xfer, color:(s.colors&&s.colors[0])||NETC[s.net]||'#888'},
  geometry:{type:'Point',coordinates:[s.lon,s.lat]}}))};

const NONE='#000000';
let ready=false;

function money(v){return v==null?'—':'$'+v.toLocaleString()}
function capTxt(v,c){return c?'≥$3,501':money(v)}

function modeMin(p){
  const m=document.querySelector('#mode button.on').dataset.v;
  return m==='cycle'?p.cycle:(m==='walk'?p.walk:p.commute);
}

// Band test, studio and 1BR first. Most tracts report no studio or 1BR median —
// 462 of 3,457 have neither — so fall back to the all-units median before
// giving up, otherwise half the city paints as an undifferentiated grey.
function rentVals(p){
  const v=[];
  if(p.stu!=null&&!p.stu_cap) v.push({v:p.stu,rel:p.stu_rel});
  if(p.one!=null&&!p.one_cap) v.push({v:p.one,rel:p.one_rel});
  if(!v.length&&p.allr!=null&&!p.allr_cap) v.push({v:p.allr,rel:null,alt:true});
  return v;
}

function color(p,cmax,rmin,rmax){
  const t=modeMin(p);
  if(t==null||t>cmax) return null;
  const vals=rentVals(p);
  if(!vals.length) return (p.stu_cap||p.one_cap)?'#d29a3a':null;
  const inb=vals.filter(x=>x.v>=rmin&&x.v<=rmax&&
      (x.alt||(x.rel!=null&&x.rel<=meta.max_rel_moe)));
  if(inb.length) return '#2f8f6f';
  const any=Math.min(...vals.map(x=>x.v));
  if(any>rmax) return '#c25b52';
  if(any<rmin) return '#c9d2c3';
  if(vals.some(x=>!x.alt&&(x.rel==null||x.rel>meta.max_rel_moe))) return '#d29a3a';
  return '#c9d2c3';
}

function ctl(){return{
  cmax:+document.getElementById('cmax').value,
  rmin:+document.getElementById('rmin').value,
  rmax:+document.getElementById('rmax').value
};}

/* ---------- asking rents ---------- */
// ZORI is a market index, not a survey of this tract: it arrives allocated from
// ZIP codes, so a tract split between ZCTAs gets a blend (p.askz > 1 tells you
// so in the detail card). Two extra colour modes keep the same shell: a
// continuous teal ramp for the asking level, a warm ramp for the multiple over
// ACS paid rent. Anything the ACS cannot support stays unpainted rather than
// being invented.
const ASK_STOPS=[[1800,'#e8eeea'],[2400,'#c3d8d2'],[3000,'#93bcb3'],
                 [3600,'#5d9a8f'],[4200,'#33786c'],[4800,'#1d5750']];
const PREM_STOPS=[[1.0,'#eae3d2'],[1.3,'#dcc39b'],[1.5,'#d6ab72'],
                  [1.7,'#c2824d'],[2.0,'#a2532f']];
const PAPER='#f6f5ea';

function hex2rgb(h){const n=parseInt(h.slice(1),16);return[n>>16,(n>>8)&255,n&255]}
function rgb2hex(r){return '#'+r.map(v=>Math.round(v).toString(16).padStart(2,'0')).join('')}
function mix(a,b,t){const x=hex2rgb(a),y=hex2rgb(b);
  return rgb2hex([0,1,2].map(i=>x[i]+(y[i]-x[i])*t))}
function ramp(stops,v){
  if(v<=stops[0][0]) return stops[0][1];
  for(let i=1;i<stops.length;i++){
    if(v<=stops[i][0]){
      const [v0,c0]=stops[i-1],[v1,c1]=stops[i];
      return mix(c0,c1,(v-v0)/(v1-v0));
    }
  }
  return stops[stops.length-1][1];
}

function colorMode(){
  const b=document.querySelector('#cmode button.on');
  return b?b.dataset.v:'fit';
}

function askColor(p){
  if(p.ask==null) return null;
  return ramp(ASK_STOPS,p.ask);
}

function premColor(p){
  if(p.prem==null) return null;
  const c=ramp(PREM_STOPS,Math.min(p.prem,2.0));
  // A ratio computed off a high-MOE paid rent is still shown — it is the only
  // read on that tract — but washed toward paper so it never competes with a
  // number the survey actually supports.
  return p.premlo? mix(c,PAPER,0.55) : c;
}

function fillFor(p,cmax,rmin,rmax){
  const t=modeMin(p);
  if(t==null||t>cmax) return null;
  const m=colorMode();
  if(m==='ask') return askColor(p);
  if(m==='prem') return premColor(p);
  return color(p,cmax,rmin,rmax);
}

function tractFC(){
  const {cmax,rmin,rmax}=ctl();
  return {type:'FeatureCollection',features:D.tracts.features.map(f=>({
    type:'Feature',
    properties:Object.assign({},f.properties,{fill:fillFor(f.properties,cmax,rmin,rmax)||NONE}),
    geometry:f.geometry
  }))};
}

function popupHTML(p){
  const t=modeMin(p);
  return `<b>${p.station||'—'}</b> <span style="color:#6b857c">${p.net||''}</span><br>
    ${document.querySelector('#mode button.on').textContent.toLowerCase()}
    <b>${t==null?'—':t+' min'}</b><br>
    <span style="color:#6b857c">transit ${p.commute==null?'—':p.commute+'m'}
    · bike ${p.cycle==null?'—':p.cycle+'m'} · walk ${p.walk==null?'—':p.walk+'m'}</span><br>
    studio ${capTxt(p.stu,p.stu_cap)} · 1BR ${capTxt(p.one,p.one_cap)}`;
}

function detailHTML(p){
  const lead = p.one_cap? p.stu : (p.one!=null? p.one : p.stu);
  const leadLbl = p.one_cap? 'studio' : (p.one!=null? '1BR median' : 'studio median');
  return `<button class="x" id="dx" aria-label="Close">×</button>
    <div class="eyebrow">Tract ${p.geoid}</div>
    <h3>${p.station||'Select a tract'}</h3>
    <div class="net">${p.net?p.net+' · ':''}${modeMin(p)==null?'no route':modeMin(p)+' min door to door'}</div>
    <div class="big"><b>${capTxt(lead, p.one_cap||p.stu_cap)}</b><span>${leadLbl}</span></div>
    <div class="kv"><span>Transit</span><b>${p.commute==null?'—':p.commute+' min'}</b></div>
    <div class="kv"><span>Ride / walk</span><b>${p.ride==null?'—':p.ride+'m + '+p.walkm+'m'}</b></div>
    <div class="kv"><span>Studio</span><b>${capTxt(p.stu,p.stu_cap)}</b></div>
    <div class="kv"><span>1BR</span><b>${capTxt(p.one,p.one_cap)}</b></div>
    <div class="kv"><span>All units</span><b>${capTxt(p.allr,p.allr_cap)}</b></div>
    ${p.ask==null?
      `<div class="kv"><span>Asking, all units</span><b class="dim">no ZIP index</b></div>`:
      `<div class="kv"><span>Asking, all units</span><b>$${p.ask.toLocaleString()}</b></div>
       <div class="kv"><span>Asking ÷ paid</span><b>${p.prem==null
         ?(p.allr_cap?'— topcoded':'— too noisy'):p.prem.toFixed(2)+'×'}</b></div>`}
    <div class="note">ACS 2020–2024 gross rent, median by unit size. Paid, not asking.${
      p.ask==null?`<br>This tract sits in a ZCTA that carries no ZORI index, so no
      asking figure is estimated rather than borrowed from a neighbour.`:
      `<br>Zillow ZORI ${meta.ask&&meta.ask.month?meta.ask.month:meta.zori_month}, allocated from
      ${p.askz} ZIP${p.askz===1?'':'s'}${p.premlo?' · ratio low-confidence (wide ACS margin)':''}.`}
      ${p.trust?'':' ⚠ Too few sample units to trust this estimate.'}</div>`;
}

map.on('load',()=>{
  // Warm the basemap toward the page paper so the tiles do not read as a grey
  // slab against the cream chrome, and take out the noise that fights the data:
  // the residential wash that greys whole cities, street-name and POI labels,
  // and airport tags. Place names stay — they are the only labels worth keeping
  // at this scale.
  try{
    map.setPaintProperty('background','background-color','#eef0e4');
    map.setPaintProperty('landuse_residential','fill-opacity',0);
    map.setPaintProperty('park','fill-color','#e0e8d7');
    map.setPaintProperty('water','fill-color','#dfe7e3');
    // Positron paints woods a cool grey and buildings a slightly cooler one;
    // both read as dirty against cream. Warm them and thin the canopy.
    map.setPaintProperty('landcover_wood','fill-color','#dbe6d1');
    map.setPaintProperty('landcover_wood','fill-opacity',
                         ['interpolate',['linear'],['zoom'],8,0,12,0.38]);
    map.setPaintProperty('building','fill-color','#f1f0e7');
    map.setPaintProperty('road_area_pier','fill-color','#f1f0e7');
    const drop=/^(highway-name-|highway-shield|road_shield|airport|waterway_line_label|poi)/;
    for(const l of map.getStyle().layers||[]){
      if(l.type==='symbol' && drop.test(l.id)) map.setLayoutProperty(l.id,'visibility','none');
      if(l.type==='symbol' && /^label_/.test(l.id)){
        map.setPaintProperty(l.id,'text-color','#7d8a7c');
        map.setPaintProperty(l.id,'text-halo-color','rgba(253,252,246,.85)');
      }
    }
  }catch(e){}

  map.addSource('tracts',{type:'geojson',data:tractFC()});
  map.addLayer({id:'tracts-fill',type:'fill',source:'tracts',
    paint:{'fill-color':['get','fill'],
           'fill-opacity':['case',['==',['get','fill'],NONE],0,0.72]}});
  map.addLayer({id:'tracts-line',type:'line',source:'tracts',
    filter:['!=',['get','fill'],NONE],
    paint:{'line-color':'#fdfcf6','line-width':0.7,'line-opacity':0.55}});

  // 3D: the same OpenFreeMap tile source the basemap uses carries
  // render_height / render_min_height on the building layer (OpenMapTiles
  // schema), so the city can be extruded without a second tileset or any
  // footprint download. Flat building fill is hidden while extrusion is on,
  // otherwise the two z-fight.
  map.addLayer({id:'buildings-3d',type:'fill-extrusion',source:'openmaptiles',
    'source-layer':'building',minzoom:12,
    paint:{
      'fill-extrusion-color':['interpolate',['linear'],['get','render_height'],
                              0,'#e6e4d6',20,'#efeddf',80,'#f6f4e9',200,'#fbf9f1'],
      'fill-extrusion-height':['max',5,['coalesce',['get','render_height'],8]],
      'fill-extrusion-base':['coalesce',['get','render_min_height'],0],
      'fill-extrusion-opacity':0.58,
      'fill-extrusion-vertical-gradient':true}});
  // Trees go on top of the extrusion: MapLibre depth-tests 3D against 3D, so a
  // canopy layer beneath the buildings is swallowed whole. Drawn after, the
  // crowns read the way a tree in front of a facade does in a pitched view.
  map.addSource('trees',{type:'geojson',data:{type:'FeatureCollection',features:[]}});
  map.addLayer({id:'trees',type:'circle',source:'trees',minzoom:14.5,
    paint:{'circle-radius':['interpolate',['linear'],['zoom'],15,2.2,17,4.2,19,6.6],
           'circle-color':'#588a48','circle-opacity':0.8,'circle-blur':0.2}});

  try{ map.setLayoutProperty('building','visibility','none'); }catch(e){}
  try{ map.setLight({anchor:'viewport',color:'#fffdf2',intensity:0.42,
                     position:[1.4,205,32]}); }catch(e){}
  // MapLibre 6 paints an atmosphere behind the scene; v4 had no sky layer at
  // all. Kept to the paper palette so the horizon reads as haze, not weather.
  try{ map.setSky({'sky-color':'#dfe7dc','horizon-color':'#f6f5ea',
                   'fog-color':'#e9ece1','sky-horizon-blend':0.55,
                   'horizon-fog-blend':0.5,'fog-ground-blend':0.85,
                   'atmosphere-blend':['interpolate',['linear'],['zoom'],10,1,13,0.25]}); }catch(e){}

  // Streets drawn back over the extruded mass. In a 3D city the basemap's own
  // road linework is buried under the buildings, and without a street grid the
  // blocks read as a solid foam model rather than a city.
  map.addLayer({id:'streets-casing',type:'line',source:'openmaptiles',
    'source-layer':'transportation',minzoom:11,
    filter:['in',['get','class'],['literal',['motorway','trunk','primary','secondary',
                                             'tertiary','minor','street','service']]],
    paint:{'line-color':'#cfcdbb',
           'line-width':['interpolate',['linear'],['zoom'],11,0.5,14,1.5,17,4.6,19,9.5],
           'line-opacity':0.22}});
  map.addLayer({id:'streets',type:'line',source:'openmaptiles',
    'source-layer':'transportation',minzoom:11,
    filter:['in',['get','class'],['literal',['motorway','trunk','primary','secondary',
                                             'tertiary','minor','street','service']]],
    paint:{'line-color':['match',['get','class'],
                          ['motorway','trunk'],'#f6e7c8',
                          ['primary','secondary'],'#faf2e0',
                          '#fdfcf6'],
           'line-width':['interpolate',['linear'],['zoom'],11,0.35,14,1.1,17,3.6,19,7.6],
           'line-opacity':0.4}});

  map.addSource('lines',{type:'geojson',data:lineFC});
  // Pale casing under each route: on a light map this is the MTA map's own trick
  // of separating lines from whatever sits behind them.
  map.addLayer({id:'lines-casing',type:'line',source:'lines',
    paint:{'line-color':'#fdfcf6',
           'line-width':['interpolate',['linear'],['zoom'],9,
                         ['case',['==',['get','net'],'subway'],2.8,2],14,
                         ['case',['==',['get','net'],'subway'],8,4.4]],
           'line-opacity':0.9}});
  map.addLayer({id:'lines',type:'line',source:'lines',
    paint:{'line-color':['get','color'],
           'line-width':['interpolate',['linear'],['zoom'],9,
                         ['case',['==',['get','net'],'subway'],1.5,1.1],14,
                         ['case',['==',['get','net'],'subway'],4,2.4]],
           'line-opacity':['case',['==',['get','net'],'subway'],0.95,0.75]}});

  map.addSource('stations',{type:'geojson',data:stnFC});
  // Station dots are noise at city scale: below z11 the network line pattern
  // already reads, and 441 named circles collapse into a rash of white rings.
  map.addLayer({id:'stations',type:'circle',source:'stations',minzoom:11,
    paint:{'circle-radius':['interpolate',['linear'],['zoom'],
                            8,['case',['==',['get','xfer'],true],1.9,1.2],
                            11,['case',['==',['get','xfer'],true],3.4,2.2],
                            14,['case',['==',['get','xfer'],true],6.4,4.0]],
           'circle-color':['case',['==',['get','xfer'],true],'#ffffff',['get','color']],
           'circle-stroke-width':['case',['==',['get','xfer'],true],1.4,0.9],
           'circle-stroke-color':['case',['==',['get','xfer'],true],'#254c47','#ffffff']}});

  map.on('click','tracts-fill',e=>{
    const p=e.features[0].properties;
    openDetail(p);
  });
  map.on('click','stations',e=>{
    const p=e.features[0].properties;
    new maplibregl.Popup({offset:10,maxWidth:'240px'}).setLngLat(e.lngLat)
      .setHTML(`<b>${p.name}</b>`+(p.routes?`<br><span style="color:#6b857c;font-size:11px">${p.routes}</span>`:'')
               +`<br><span style="color:#6b857c">${p.min} min to Penn 1</span>`).addTo(map);
  });
  ['tracts-fill','stations'].forEach(id=>{
    map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',id,()=>{map.getCanvas().style.cursor='';});
  });

  ready=true;
  draw();
});

function openDetail(p){
  const d=document.getElementById('detail');
  d.innerHTML=detailHTML(p);
  d.classList.add('on');
  const x=document.getElementById('dx');
  if(x) x.onclick=()=>d.classList.remove('on');
}

function draw(){
  const {cmax,rmin,rmax}=ctl();
  const onlyB=document.getElementById('onlyBudget').checked;
  document.getElementById('cmaxlbl').textContent=cmax;
  document.getElementById('rminv').textContent='$'+rmin.toLocaleString();
  document.getElementById('rmaxv').textContent='$'+rmax.toLocaleString();

  if(ready) map.getSource('tracts').setData(tractFC());

  const rows=D.tracts.features.map(f=>f.properties)
    .filter(p=>{const t=modeMin(p); return t!=null&&t<=cmax;})
    .filter(p=>{
      if(!onlyB) return true;
      const vals=[];
      if(p.stu!=null&&!p.stu_cap) vals.push({v:p.stu,rel:p.stu_rel});
      if(p.one!=null&&!p.one_cap) vals.push({v:p.one,rel:p.one_rel});
      return vals.some(x=>x.v>=rmin&&x.v<=rmax&&x.rel!=null&&x.rel<=meta.max_rel_moe);
    })
    .sort((a,b)=>modeMin(a)-modeMin(b));

  document.getElementById('rows').innerHTML=rows.slice(0,400).map(p=>{
    const st=p.stu_cap?'≥3.5k':money(p.stu).replace('$','');
    const on=p.one_cap?'≥3.5k':money(p.one).replace('$','');
    return `<div class="trow" data-g="${p.geoid}">
      <span><b>${p.station||'—'}</b></span>
      <span class="m">${modeMin(p)}m</span>
      <span>${st}</span><span>${on}</span></div>`;
  }).join('');

  document.getElementById('count').textContent=
    rows.length+' tracts in budget at this setting';

  [...document.querySelectorAll('.trow')].forEach(tr=>{
    tr.onclick=()=>{
      const g=tr.dataset.g;
      const f=D.tracts.features.find(x=>x.properties.geoid===g);
      if(!f||!ready) return;
      const xs=[],ys=[];
      (function walk(a){a.forEach(pt=>{
        if(typeof pt[0]==='number'){xs.push(pt[0]);ys.push(pt[1]);} else walk(pt);
      });})(f.geometry.coordinates);
      if(xs.length){
        map.fitBounds([[Math.min(...xs),Math.min(...ys)],[Math.max(...xs),Math.max(...ys)]],
                      {maxZoom:14,padding:{top:80,bottom:120,left:420,right:80}});
        openDetail(f.properties);
      }
    };
  });
}

/* ---------- chrome ---------- */
document.querySelectorAll('#tabs button').forEach(b=>{
  b.onclick=()=>{
    document.querySelectorAll('#tabs button').forEach(x=>x.classList.toggle('on',x===b));
    document.querySelectorAll('.pane').forEach(p=>
      p.classList.toggle('on',p.id==='pane-'+b.dataset.pane));
  };
});
document.getElementById('toSources').onclick=e=>{
  e.preventDefault();
  document.querySelector('#tabs button[data-pane="src"]').click();
};
document.getElementById('mode').onclick=e=>{
  const b=e.target.closest('button'); if(!b) return;
  document.querySelectorAll('#mode button').forEach(x=>x.classList.toggle('on',x===b));
  draw();
};
document.getElementById('cmode').onclick=e=>{
  const b=e.target.closest('button'); if(!b) return;
  document.querySelectorAll('#cmode button').forEach(x=>x.classList.toggle('on',x===b));
  const m=colorMode();
  document.getElementById('keysFit').hidden=(m!=='fit');
  document.getElementById('keysAsk').hidden=(m!=='ask');
  document.getElementById('keysPrem').hidden=(m!=='prem');
  draw();
};
let is3d=false, auto3d=false, treesLoaded=false;

// Street trees are only legible as a canopy once you are at street scale, and
// they cost 2.9 MB, so the file is fetched the first time the camera gets
// close enough to draw them rather than on page load.
async function loadTrees(){
  if(treesLoaded) return;
  treesLoaded=true;
  try{
    const d=await (await fetch('trees.json')).json();
    const xy=d.xy, n=xy.length>>1, f=new Array(n);
    for(let i=0;i<n;i++) f[i]={type:'Feature',properties:null,
      geometry:{type:'Point',coordinates:[xy[2*i+1]/1e5,xy[2*i]/1e5]}};
    map.getSource('trees').setData({type:'FeatureCollection',features:f});
  }catch(e){ console.warn('trees unavailable',e); }
}
function apply3d(on){
  is3d=on;
  document.getElementById('btn3d').classList.toggle('on',on);
  if(ready){
    map.setLayoutProperty('buildings-3d','visibility',on?'visible':'none');
    map.setLayoutProperty('building','visibility',on?'none':'visible');
  }
  map.easeTo({pitch:on?40:0,duration:700});
}
document.getElementById('btn3d').onclick=()=>apply3d(!is3d);
document.getElementById('btnHome').onclick=()=>map.flyTo({center:HOME.center,zoom:HOME.zoom});
// The city reads best flat while you are comparing tracts at metro scale, and
// best extruded once you are down among the buildings. So 3D engages itself the
// first time the camera passes z14, and the rail button still overrides it.
map.on('zoomend',()=>{
  if(map.getZoom()>=13.8) loadTrees();
  if(!auto3d && map.getZoom()>=14){ auto3d=true; if(!is3d) apply3d(true); }
});
document.getElementById('btnIn').onclick=()=>map.zoomIn();
document.getElementById('btnOut').onclick=()=>map.zoomOut();
document.getElementById('btnReset').onclick=resetView;
document.getElementById('resetTop').onclick=resetView;
function resetView(){
  document.getElementById('cmax').value=60;
  document.getElementById('rmin').value=2000;
  document.getElementById('rmax').value=3000;
  document.getElementById('onlyBudget').checked=true;
  document.querySelectorAll('#mode button').forEach((x,i)=>x.classList.toggle('on',i===0));
  document.getElementById('detail').classList.remove('on');
  map.flyTo({center:[-73.95,40.78],zoom:10});
  draw();
}

['cmax','rmin','rmax'].forEach(id=>document.getElementById(id).oninput=draw);
document.getElementById('onlyBudget').onchange=draw;
document.getElementById('showLines').onchange=e=>{
  if(!ready) return;
  ['lines-casing','lines'].forEach(id=>
    map.setLayoutProperty(id,'visibility',e.target.checked?'visible':'none'));
};
document.getElementById('showStations').onchange=e=>{
  if(ready) map.setLayoutProperty('stations','visibility',e.target.checked?'visible':'none');
};

</script></body></html>"""


if __name__ == "__main__":
    main()
