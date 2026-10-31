#!/usr/bin/env python3
"""
Phase 4: build the deliverable — self-contained Leaflet HTML with embedded GeoJSON.
Adds reliability gates so the shortlist only shows tracts the data can actually support.
"""
import pandas as pd, numpy as np, geopandas as gpd, json, os, sys
from pathlib import Path
from shapely.geometry import mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config as C

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
    g = gpd.read_file(C.require(C.CB_TRACT_NY, "run scripts/fetch_data.py"))
    nj = gpd.read_file(C.require(C.CB_TRACT_NJ, "run scripts/fetch_data.py"))
    geo = pd.concat([g[["GEOID", "geometry"]], nj[["GEOID", "geometry"]]], ignore_index=True)
    geo = gpd.GeoDataFrame(geo, geometry="geometry", crs=4326)
    geo["GEOID"] = geo.GEOID.astype(str)

    keep = df[df.commute_min.notna()].copy()
    m = geo.merge(keep, left_on="GEOID", right_on="geoid", how="inner")
    print(f"  map features: {len(m)}")

    # simplify hard to keep the file small
    m["geometry"] = m.geometry.simplify(0.0012, preserve_topology=True)

    feats = []
    for _, r in m.iterrows():
        props = {
            "geoid": r.geoid,
            "station": r.station,
            "net": r.station_net,
            "commute": None if pd.isna(r.commute_min) else round(float(r.commute_min), 1),
            "ride": None if pd.isna(r.ride_min) else round(float(r.ride_min), 1),
            "walkm": None if pd.isna(r.walk_min) else round(float(r.walk_min), 1),
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
        }
        feats.append({"type": "Feature", "properties": props, "geometry": mapping(r.geometry)})

    gj = {"type": "FeatureCollection", "features": feats}
    gp = str(C.OUT_TRACT_GEOJSON)
    json.dump(gj, open(gp, "w"), separators=(",", ":"))
    print(f"  wrote {gp}  ({os.path.getsize(gp)/1e6:.1f} MB)")

    # ---- transit lines (from GTFS shapes) ----
    print("building transit lines...")
    lines = []
    NET_COLOR = {"subway": "#1f6feb", "mnr": "#c1121f", "lirr": "#b5179e", "path": "#0f766e"}
    NET_LABEL = {"subway": "Subway", "mnr": "Metro-North", "lirr": "LIRR", "path": "PATH"}
    FEEDS = {k: str(v) for k, v in C.FEEDS.items()}
    routes = {"subway": "routes.txt", "mnr": "routes.txt",
              "lirr": "routes.txt", "path": "routes.txt"}
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
            lines.append({"net": net_name, "route": str(rid),
                          "color": NET_COLOR[net_name], "coords": coords})
    print(f"  {len(lines)} route lines")

    # ---- stations within 60 min ----
    st = pd.read_csv(C.require(C.OUT_STATION_TIMES, "run scripts/build_graph.py first"))
    st = st[st["min"] <= 60]
    stations = [{"name": str(r["name"]).strip(), "net": r.net,
                 "lat": round(float(r.lat), 5), "lon": round(float(r.lon), 5),
                 "min": round(float(r["min"]), 1)}
                for _, r in st.iterrows() if r["name"] and str(r["name"]) != "nan"]

    meta = {"zori_month": "2026-09-30", "acs_vintage": "2017-2021..2020-2024 (5yr 2024)",
            "topcap": TOPCAP, "max_rel_moe": MAX_REL_MOE}

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
    return TEMPLATE.replace("__DATA__", data)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Penn 1 Apartment Explorer — 60 min</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
 :root{--bg:#0f1115;--panel:#171a21;--ink:#e8eaed;--dim:#9aa4b2;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444}
 *{box-sizing:border-box}
 body{margin:0;font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--ink)}
 #wrap{display:flex;height:100vh}
 #map{flex:1;min-width:0}
 #side{width:390px;background:var(--panel);border-left:1px solid #262b36;display:flex;flex-direction:column}
 #head{padding:14px 16px 10px;border-bottom:1px solid #262b36}
 h1{font-size:15px;margin:0 0 2px;letter-spacing:.2px}
 .sub{color:var(--dim);font-size:11.5px}
 #controls{padding:10px 16px;border-bottom:1px solid #262b36;display:grid;gap:8px}
 .row{display:flex;align-items:center;gap:8px;font-size:12.5px}
 .row label{flex:1;color:var(--dim)}
 input[type=range]{width:118px}
 #list{flex:1;overflow:auto}
 table{width:100%;border-collapse:collapse;font-size:12.5px}
 th{position:sticky;top:0;background:#1d222b;text-align:left;padding:7px 10px;font-weight:600;
    color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.4px;z-index:2}
 td{padding:7px 10px;border-bottom:1px solid #1f2530}
 tr:hover{background:#1d222b;cursor:pointer}
 .pill{display:inline-block;padding:1px 6px;border-radius:9px;font-size:10.5px;font-weight:600}
 .p-stu{background:#1e3a5f;color:#8ab4f8}
 .p-one{background:#14532d;color:#86efac}
 .p-cap{background:#4a2c00;color:#fbbf24}
 #legend{padding:10px 16px;border-top:1px solid #262b36;font-size:11.5px;color:var(--dim)}
 .lg{display:flex;align-items:center;gap:7px;margin:3px 0}
 .sw{width:13px;height:13px;border-radius:3px;flex:none}
 #detail{padding:12px 16px;border-top:1px solid #262b36;font-size:12.5px;display:none}
 #detail .t{font-weight:600;margin-bottom:6px}
 .kv{display:flex;justify-content:space-between;padding:2px 0}
 .kv span:first-child{color:var(--dim)}
 .note{color:var(--dim);font-size:11px;margin-top:8px;line-height:1.4}
 .leaflet-popup-content{font-size:12.5px;margin:9px 12px}
 .leaflet-popup-content b{font-size:13px}
</style></head><body>
<div id="wrap">
  <div id="map"></div>
  <div id="side">
    <div id="head">
      <h1>PENN 1 → apartment explorer</h1>
      <div class="sub">Census tracts · commute ≤ <b id="cmaxlbl">60</b> min · rent $<span id="rminlbl">2000</span>–$<span id="rmaxlbl">3000</span></div>
    </div>
    <div id="controls">
      <div class="row"><label for="cmax">Max commute</label>
        <input id="cmax" type="range" min="10" max="60" step="5" value="60"><b id="cmaxv">60m</b></div>
      <div class="row"><label for="rmin">Rent floor</label>
        <input id="rmin" type="range" min="1000" max="4500" step="100" value="2000"><b id="rminv">$2k</b></div>
      <div class="row"><label for="rmax">Rent ceiling</label>
        <input id="rmax" type="range" min="1200" max="6000" step="100" value="3000"><b id="rmaxv">$3k</b></div>
      <div class="row"><label><input type="checkbox" id="onlyBudget" checked> In-budget only</label></div>
      <div class="row"><label><input type="checkbox" id="showLines" checked> Transit lines</label>
        <label><input type="checkbox" id="showStations" checked> Stations</label></div>
    </div>
    <div id="list">
      <table><thead><tr><th>Tract</th><th>Commute</th><th>Studio</th><th>1BR</th></tr></thead>
      <tbody id="tb"></tbody></table>
    </div>
    <div id="detail"></div>
    <div id="legend">
      <div class="lg"><span class="sw" style="background:#22c55e"></span>Tract median in budget (reliable)</div>
      <div class="lg"><span class="sw" style="background:#f59e0b"></span>Above ceiling / unreliable estimate</div>
      <div class="lg"><span class="sw" style="background:#ef4444"></span>Over budget</div>
      <div class="lg" style="margin-top:6px">— <span style="color:#1f6feb">Subway</span> · <span style="color:#c1121f">Metro-North</span> · <span style="color:#b5179e">LIRR</span> · <span style="color:#0f766e">PATH</span></div>
    </div>
  </div>
</div>
<script>
const D = __DATA__;
const meta = D.meta;

const map = L.map('map',{zoomControl:true,preferCanvas:true}).setView([40.78,-73.95],10);
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {maxZoom:19,attribution:'&copy; OpenStreetMap &copy; CARTO'}).addTo(map);

// Penn 1 marker
L.marker([40.75056,-73.99386]).addTo(map).bindPopup('<b>PENN 1</b><br>1 Penn Plaza, 34th St').openPopup();

const lineLayer = L.layerGroup().addTo(map);
D.lines.forEach(l=>{
  if(!l.coords || l.coords.length<2) return;
  L.polyline(l.coords,{color:l.color,weight:2.2,opacity:.75,interactive:false}).addTo(lineLayer);
});

const stationLayer = L.layerGroup().addTo(map);
const NETC={subway:'#1f6feb',mnr:'#c1121f',lirr:'#b5179e',path:'#0f766e'};
D.stations.forEach(s=>{
  L.circleMarker([s.lat,s.lon],{radius:2.6,color:'#0b0e13',weight:.6,
    fillColor:NETC[s.net]||'#888',fillOpacity:.95})
   .bindTooltip(`${s.name} — ${s.min} min`,{direction:'top'})
   .addTo(stationLayer);
});

function color(p,cmax,rmin,rmax){
  if(p.commute==null||p.commute>cmax) return null;
  const vals=[];
  if(p.stu!=null&&!p.stu_cap) vals.push({v:p.stu,rel:p.stu_rel});
  if(p.one!=null&&!p.one_cap) vals.push({v:p.one,rel:p.one_rel});
  const inb=vals.filter(x=>x.v>=rmin&&x.v<=rmax&&x.rel!=null&&x.rel<=meta.max_rel_moe);
  if(inb.length) return '#22c55e';
  if((p.stu_cap||p.one_cap)&&vals.length===0) return '#f59e0b';
  const any=vals.length?Math.min(...vals.map(x=>x.v)):null;
  if(any!=null&&any>rmax) return '#ef4444';
  if(any!=null&&any<rmin) return '#3b4351';
  if(vals.some(x=>x.rel==null||x.rel>meta.max_rel_moe)) return '#f59e0b';
  return '#3b4351';
}

let tractLayer;
function money(v){return v==null?'—':'$'+v.toLocaleString()}
function draw(){
  const cmax=+document.getElementById('cmax').value;
  const rmin=+document.getElementById('rmin').value;
  const rmax=+document.getElementById('rmax').value;
  const onlyB=document.getElementById('onlyBudget').checked;
  document.getElementById('cmaxv').textContent=cmax+'m';
  document.getElementById('rminv').textContent='$'+(rmin/1000).toFixed(1)+'k';
  document.getElementById('rmaxv').textContent='$'+(rmax/1000).toFixed(1)+'k';
  document.getElementById('cmaxlbl').textContent=cmax;
  document.getElementById('rminlbl').textContent=rmin;
  document.getElementById('rmaxlbl').textContent=rmax;

  if(tractLayer) map.removeLayer(tractLayer);
  tractLayer=L.geoJSON(D.tracts,{
    style:f=>{
      const p=f.properties;
      const c=color(p,cmax,rmin,rmax);
      return {color:c?'#0b0e13':'transparent',weight:c?0.4:0,
              fillColor:c||'#000',fillOpacity:c?0.55:0};
    },
    onEachFeature:(f,l)=>{
      const p=f.properties;
      const capTxt=(x,c)=>c?'<span style="color:#fbbf24">≥$3,501</span>':money(x);
      l.bindPopup(`<b>${p.station||'—'}</b> <span style="color:#888">(${p.net||''})</span><br>
        commute <b>${p.commute==null?'—':p.commute+' min'}</b>
        <span style="color:#888">(ride ${p.ride}m + walk ${p.walkm}m)</span><br>
        studio ${capTxt(p.stu,p.stu_cap)}<br>
        1BR ${capTxt(p.one,p.one_cap)}<br>
        all units ${capTxt(p.allr,p.allr_cap)}<br>
        <span style="color:#888;font-size:11px">tract ${p.geoid}${p.trust?'':'<br>⚠ estimate low-confidence'}</span>`);
      l.on('click',()=>{
        const d=document.getElementById('detail');
        d.style.display='block';
        d.innerHTML=`<div class="t">${p.station||'—'} <span style="color:#888;font-weight:400">${p.net||''}</span></div>
          <div class="kv"><span>Commute</span><b>${p.commute==null?'—':p.commute+' min'}</b></div>
          <div class="kv"><span>Ride / walk</span><span>${p.ride==null?'—':p.ride}m + ${p.walkm==null?'—':p.walkm}m</span></div>
          <div class="kv"><span>Studio median</span><span>${capTxt(p.stu,p.stu_cap)}</span></div>
          <div class="kv"><span>1BR median</span><span>${capTxt(p.one,p.one_cap)}</span></div>
          <div class="kv"><span>All-units median</span><span>${capTxt(p.allr,p.allr_cap)}</span></div>
          <div class="note">Tract ${p.geoid}. ACS 5-yr 2020–2024, topcoded at $3,501.
          ${p.trust?'':'⚠ relative MOE too high to trust.'}</div>`;
      });
    }
  }).addTo(map);

  // table
  const rows=D.tracts.features.map(f=>f.properties)
    .filter(p=>p.commute!=null&&p.commute<=cmax)
    .filter(p=>{
      if(!onlyB) return true;
      const vals=[];
      if(p.stu!=null&&!p.stu_cap) vals.push({v:p.stu,rel:p.stu_rel});
      if(p.one!=null&&!p.one_cap) vals.push({v:p.one,rel:p.one_rel});
      return vals.some(x=>x.v>=rmin&&x.v<=rmax&&x.rel!=null&&x.rel<=meta.max_rel_moe);
    })
    .sort((a,b)=>a.commute-b.commute);
  const tb=document.getElementById('tb');
  tb.innerHTML=rows.slice(0,400).map(p=>{
    const st=p.stu_cap?'<span class="p-cap">≥3.5k</span>':money(p.stu);
    const on=p.one_cap?'<span class="p-cap">≥3.5k</span>':money(p.one);
    return `<tr data-g="${p.geoid}"><td>${p.station||'—'}</td>
      <td>${p.commute}m</td><td>${st}</td><td>${on}</td></tr>`;
  }).join('');
  [...tb.querySelectorAll('tr')].forEach(tr=>{
    tr.onclick=()=>{
      const g=tr.dataset.g;
      const f=D.tracts.features.find(x=>x.properties.geoid===g);
      if(!f) return;
      const c=f.geometry.coordinates;
      let xs=[],ys=[];
      const walk=arr=>arr.forEach(pt=>{
        if(typeof pt[0]==='number'){xs.push(pt[0]);ys.push(pt[1]);} else walk(pt);
      });
      walk(c);
      if(xs.length){
        map.fitBounds([[Math.min(...ys),Math.min(...xs)],[Math.max(...ys),Math.max(...xs)]],{maxZoom:14});
      }
    };
  });
}

['cmax','rmin','rmax'].forEach(id=>document.getElementById(id).oninput=draw);
['onlyBudget','showLines','showStations'].forEach(id=>{
  document.getElementById(id).onchange=e=>{
    if(e.target.id==='showLines'){ e.target.checked?map.addLayer(lineLayer):map.removeLayer(lineLayer); }
    else if(e.target.id==='showStations'){ e.target.checked?map.addLayer(stationLayer):map.removeLayer(stationLayer); }
    else draw();
  };
});
draw();
</script></body></html>"""


if __name__ == "__main__":
    main()
