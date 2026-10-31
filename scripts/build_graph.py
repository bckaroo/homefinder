#!/usr/bin/env python3
"""
Phase 1 (v2): multi-modal transit reachability from Penn 1 to every GTFS stop.

Fixes vs v1:
 - MNR/LIRR feed stop_ids are station ids; subway stop_ids are route-specific platforms.
   v1 built MNR edges but the Dijkstra origin set never bridged into the MNR graph because
   the subway GCT stop (631/723/901) was itself unreachable in the peak-window filter.
   Fix: seed origins from ALL stops within walk radius regardless of feed, and allow
   cross-feed walking transfers at <=600m between HUB stations (GCT<->42St, Penn<->PATH).
 - Reject negative/invalid times; clamp absurd ride times.
"""
import pandas as pd, numpy as np, heapq, json, math, os, sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

FEEDS = {k: str(v) for k, v in C.FEEDS.items()}
PENN1 = C.PENN1
PEAK_START, PEAK_END = C.PEAK_START, C.PEAK_END
OUT = str(C.DATA)
ORIGIN_RADIUS_M = C.ORIGIN_RADIUS_M
CROSS_FEED_RADIUS_M = C.CROSS_FEED_RADIUS_M
WALK_MPS = C.WALK_MPS
PARENT_LINK_SECONDS = C.PARENT_LINK_SECONDS

R_EARTH = 6371000.0

def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = p2 - p1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2
    return 2*R_EARTH*math.asin(math.sqrt(min(1.0, a)))


def hms_to_sec(s):
    try:
        h, m, sec = str(s).split(":")
        return int(h)*3600 + int(m)*60 + int(sec)
    except Exception:
        return None


def load_stops():
    rows = []
    for net, d in FEEDS.items():
        st = pd.read_csv(f"{d}/stops.txt", dtype=str)
        st["stop_lat"] = pd.to_numeric(st["stop_lat"], errors="coerce")
        st["stop_lon"] = pd.to_numeric(st["stop_lon"], errors="coerce")
        st = st.dropna(subset=["stop_lat", "stop_lon"])
        for _, r in st.iterrows():
            rows.append({"net": net, "stop_id": str(r["stop_id"]),
                         "name": str(r.get("stop_name", "") or ""),
                         "lat": float(r["stop_lat"]), "lon": float(r["stop_lon"])})
    return rows


def build_edges():
    adj = defaultdict(dict)
    stats = {}
    # Parent-station links: platform ids (e.g. subway 631N/631S) are what trips reference,
    # while the location_type=1 station (631) is a container. Without linking them, each
    # parent becomes an isolated island with only the fake 180s parent<->parent edges.
    n_parent = 0
    for net, d in FEEDS.items():
        st = pd.read_csv(f"{d}/stops.txt", dtype=str)
        if "parent_station" not in st.columns:
            continue
        for _, r in st.iterrows():
            p = r.get("parent_station")
            if pd.isna(p) or not str(p).strip():
                continue
            sid, par = str(r["stop_id"]), str(p).strip()
            u, v = (net, sid), (net, par)
            adj[u][v] = min(adj[u].get(v, 10**9), PARENT_LINK_SECONDS)
            adj[v][u] = min(adj[v].get(u, 10**9), PARENT_LINK_SECONDS)
            n_parent += 1
    print(f"  parent-station links: {n_parent}")
    for net, d in FEEDS.items():
        stt = pd.read_csv(f"{d}/stop_times.txt", dtype=str,
                          usecols=lambda c: c in ("trip_id", "arrival_time", "departure_time",
                                                  "stop_id", "stop_sequence"))
        stt["seq"] = pd.to_numeric(stt["stop_sequence"], errors="coerce")
        stt["arr_s"] = stt["arrival_time"].map(hms_to_sec)
        stt["dep_s"] = stt["departure_time"].map(hms_to_sec)
        stt["arr_s"] = stt["arr_s"].fillna(stt["dep_s"])
        stt["dep_s"] = stt["dep_s"].fillna(stt["arr_s"])
        stt = stt.dropna(subset=["arr_s", "dep_s", "seq"])
        stt["stop_id"] = stt["stop_id"].astype(str)

        # peak trips = any departure inside AM window
        peaks = stt[stt["dep_s"].between(PEAK_START, PEAK_END)]["trip_id"].unique()
        stt = stt[stt["trip_id"].isin(peaks)]
        n_edges = 0
        for tid, g in stt.sort_values(["trip_id", "seq"]).groupby("trip_id"):
            ids = g["stop_id"].tolist()
            arr = g["arr_s"].tolist()
            dep = g["dep_s"].tolist()
            for i in range(len(ids) - 1):
                dt = arr[i+1] - dep[i]
                if dt <= 0 or dt > 3600:
                    continue
                # Build BOTH directions. A peak trip listed inbound (X->GCT) still implies
                # an identical-magnitude outbound ride GCT->X; MNR's AM schedule is almost
                # entirely inbound, so one-way edges leave its whole network unreachable.
                k_fwd = (net, ids[i])
                k_rev = (net, ids[i+1])
                v_fwd = (net, ids[i+1])
                v_rev = (net, ids[i])
                if v_fwd not in adj[k_fwd] or dt < adj[k_fwd][v_fwd]:
                    adj[k_fwd][v_fwd] = int(dt)
                if v_rev not in adj[k_rev] or dt < adj[k_rev][v_rev]:
                    adj[k_rev][v_rev] = int(dt)
                n_edges += 1
        try:
            tf = pd.read_csv(f"{d}/transfers.txt", dtype=str)
            n_tf = 0
            for _, r in tf.iterrows():
                f, t = str(r["from_stop_id"]), str(r["to_stop_id"])
                if f == t:
                    continue
                mt = pd.to_numeric(r.get("min_transfer_time"), errors="coerce")
                # transfer_type 2 = requires min time; 1 = timed; 3 = not possible; 0 = recommended
                tt = str(r.get("transfer_type", "")).strip()
                if tt == "3":
                    continue
                mt = int(mt) if pd.notna(mt) and mt > 0 else 180
                uf, ut = (net, f), (net, t)
                adj[uf][ut] = min(adj[uf].get(ut, 10**9), mt)
                adj[ut][uf] = min(adj[ut].get(uf, 10**9), mt)
                n_tf += 1
        except Exception:
            n_tf = 0
        stats[net] = {"peak_trips": len(peaks), "edges": n_edges, "transfers": n_tf}
        print(f"  {net}: {len(peaks)} peak trips, {n_edges} edges, {n_tf} transfers")
    return adj, stats


def cross_feed_links(stops, radius=None):
    """Walking transfers between DIFFERENT feeds (hub connections)."""
    radius = CROSS_FEED_RADIUS_M if radius is None else radius
    n = len(stops)
    la = np.radians([s["lat"] for s in stops])
    lo = np.radians([s["lon"] for s in stops])
    nets = [s["net"] for s in stops]
    added = 0
    for i in range(n):
        dlat = la - la[i]
        dlon = lo - lo[i]
        a = np.sin(dlat/2)**2 + math.cos(la[i])*np.cos(la)*np.sin(dlon/2)**2
        dist = 2*R_EARTH*np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        for j in np.where(dist <= radius)[0]:
            if j == i or nets[j] == nets[i]:
                continue
            added += 1
    return added


def main():
    os.makedirs(OUT, exist_ok=True)
    print("Loading stops...")
    stops = load_stops()
    print(f"  {len(stops)} stops")
    for net in FEEDS:
        print(f"    {net}: {sum(1 for s in stops if s['net']==net)}")

    print("Building peak edges...")
    adj, stats = build_edges()

    # cross-feed transfers
    print(f"Cross-feed walking transfers (<={CROSS_FEED_RADIUS_M}m)...")
    idx = {(s["net"], s["stop_id"]): i for i, s in enumerate(stops)}
    nets = [s["net"] for s in stops]
    la = np.radians([s["lat"] for s in stops])
    lo = np.radians([s["lon"] for s in stops])
    added = 0
    for i, s in enumerate(stops):
        dlat = la - la[i]; dlon = lo - lo[i]
        a = np.sin(dlat/2)**2 + math.cos(la[i])*np.cos(la)*np.sin(dlon/2)**2
        dist = 2*R_EARTH*np.arcsin(np.sqrt(np.clip(a, 0, 1)))
        for j in np.where(dist <= CROSS_FEED_RADIUS_M)[0]:
            if j == i or nets[j] == nets[i]:
                continue
            walk = max(60, int(dist[j] / WALK_MPS))
            t = stops[j]
            # CRITICAL: edge targets must be fully-qualified (net, stop_id). Feed stop_id
            # namespaces overlap (23 of MNR's 114 ids are also valid subway ids), so storing
            # a bare stop_id here silently writes subway->subway phantom edges and MNR never links.
            u = (s["net"], s["stop_id"])
            v = (t["net"], t["stop_id"])
            adj[u][v] = min(adj[u].get(v, 10**9), walk)
            adj[v][u] = min(adj[v].get(u, 10**9), walk)
            added += 2
    print(f"  {added} directed cross-feed edges")

    # origins: everything within ORIGIN_RADIUS_M of Penn 1 (Penn is a huge complex)
    origins = []
    for s in stops:
        d = haversine(PENN1[0], PENN1[1], s["lat"], s["lon"])
        if d <= ORIGIN_RADIUS_M:
            origins.append(((s["net"], s["stop_id"]), max(60, int(d/WALK_MPS))))
    print(f"  {len(origins)} origin stops within {ORIGIN_RADIUS_M}m of Penn 1")
    from collections import Counter
    print("   by feed:", dict(Counter(k[0] for k, _ in origins)))

    print("Dijkstra...")
    dist = {}
    pq = []
    for o, w in origins:
        if w < dist.get(o, 1e18):
            dist[o] = w
            heapq.heappush(pq, (w, o))
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist.get(u, 1e18):
            continue
        for v, w in adj.get(u, {}).items():
            nd = d + w
            if nd < dist.get(v, 1e18):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))

    out = []
    for s in stops:
        k = (s["net"], s["stop_id"])
        if k in dist:
            out.append({"net": s["net"], "stop_id": s["stop_id"], "name": s["name"],
                        "lat": s["lat"], "lon": s["lon"], "sec": dist[k]})
    df = pd.DataFrame(out)
    df["min"] = (df["sec"]/60).round(1)
    print(f"\nReachable: {len(df)}/{len(stops)}")
    print(df.groupby("net").agg(n=("stop_id","size"), med_min=("min","median"),
                                max_min=("min","max"), min_min=("min","min")).to_string())
    r60 = df[df["min"] <= 60]
    print(f"\nWithin 60 min: {len(r60)}")
    print(r60.groupby("net").size().to_string())
    df.to_csv(C.OUT_STATION_TIMES, index=False)
    json.dump(stats, open(C.OUT_GRAPH_STATS, "w"), indent=2)
    print(f"\nwrote {C.OUT_STATION_TIMES}")

if __name__ == "__main__":
    main()
