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
    cols = ["GEOID", "ALAND", "AWATER", "geometry"]
    geo = pd.concat([g[cols], nj[cols]], ignore_index=True)
    geo = gpd.GeoDataFrame(geo, geometry="geometry", crs=4326)
    geo["GEOID"] = geo.GEOID.astype(str)

    # Census tracts that are essentially water (harbour, bay, sound) carry no
    # housing, yet ACS still tabulates a rent median for them and the routing
    # stage still returns a commute from their centroid. Shading one puts a
    # neighbourhood on open water, so they are removed from the selection.
    # Tracts that merely *contain* a lot of water (Bay Ridge, Sunset Park,
    # Broad Channel) stay: those are real neighbourhoods with a shoreline.
    tot = geo.ALAND + geo.AWATER
    geo["water_share"] = (geo.AWATER / tot.replace(0, np.nan)).fillna(0.0)
    water_body = geo.water_share >= C.WATER_SHARE_MAX
    dropped = set(geo.GEOID[water_body])
    geo = geo[~water_body]

    # Keep every tract with at least one way of reaching Penn 1; walk/cycle
    # alone is a valid answer for tracts the transit graph cannot serve.
    keep = df[df[["commute_min", "walk_mode_min", "cycle_mode_min"]].notna().any(axis=1)].copy()
    m = geo.merge(keep, left_on="GEOID", right_on="geoid", how="inner")
    n_water = len(dropped & set(keep.geoid))
    print(f"  map features: {len(m)} (water-body tracts removed: {n_water}, "
          f">= {C.WATER_SHARE_MAX:.0%} water)")

    # simplify hard to keep the file small
    m["geometry"] = m.geometry.simplify(0.0012, preserve_topology=True)

    feats = []
    for _, r in m.iterrows():
        props = {
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
            "topcap": TOPCAP, "max_rel_moe": MAX_REL_MOE,
            "water_share_max": C.WATER_SHARE_MAX, "water_tracts_removed": n_water}

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
<link rel="stylesheet" href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"/>
<script src="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"></script>
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
 .maplibregl-popup-content{background:#171a21;color:#e8eaed;font-size:12.5px;
   padding:9px 12px;border-radius:6px;border:1px solid #262b36;font-family:inherit}
 .maplibregl-popup-content b{font-size:13px}
 .maplibregl-popup-tip{border-top-color:#171a21!important;border-bottom-color:#171a21!important}
 .maplibregl-ctrl-attrib{background:rgba(15,17,21,.82)!important}
 .maplibregl-ctrl-attrib a{color:#9aa4b2}
 .maplibregl-ctrl-group{background:#171a21!important;border:1px solid #262b36!important}
 .maplibregl-ctrl-group button+button{border-top:1px solid #262b36!important}
 .maplibregl-ctrl-icon{filter:invert(1) brightness(1.6)}
</style></head><body>
<div id="wrap">
  <div id="map"></div>
  <div id="side">
    <div id="head">
      <h1>PENN 1 → apartment explorer</h1>
      <div class="sub">Census tracts · commute ≤ <b id="cmaxlbl">60</b> min · rent $<span id="rminlbl">2000</span>–$<span id="rmaxlbl">3000</span></div>
      <div class="sub" style="margin-top:5px;color:#c98a3a">Rents are ACS 5-yr <b>paid</b> medians, not asking rents. Asking runs 1.4× (Queens) to 2.2× (Manhattan) higher — measured against Zillow ZORI. A tract showing $2,400 here is asking ~$3,400–5,300.</div>
      <div class="sub" style="margin-top:5px;color:#8b98a8">Bike and walk times come from the OpenStreetMap street network (Valhalla), computed for the <b>3,274 tracts within 30 km</b> of Penn 1. Farther tracts stay unshaded under those modes — no estimate is inferred.</div>
    </div>
    <div id="controls">
      <div class="row"><label for="mode">Travel mode</label>
        <select id="mode">
          <option value="transit" selected>Transit (rail + walk)</option>
          <option value="cycle">Bike — door to door</option>
          <option value="walk">Walk — door to door</option>
        </select></div>
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
const NETC={subway:'#1f6feb',mnr:'#c1121f',lirr:'#b5179e',path:'#0f766e'};

// Basemap: OpenFreeMap vector tiles ("dark" style) rendered by MapLibre GL.
// Attribution (OpenFreeMap / © OpenMapTiles / © OpenStreetMap) is supplied by
// the style itself, so it stays correct without hand-maintained credits.
const map = new maplibregl.Map({
  container:'map',
  style:'https://tiles.openfreemap.org/styles/dark',
  center:[-73.95,40.78],
  zoom:10,
  attributionControl:{compact:true}
});
map.addControl(new maplibregl.NavigationControl({showCompass:false}),'top-left');

new maplibregl.Marker({color:'#38bdf8'}).setLngLat([-73.99386,40.75056])
  .setPopup(new maplibregl.Popup({offset:26}).setHTML('<b>PENN 1</b><br>1 Penn Plaza, 34th St'))
  .addTo(map);

// D.lines coords are [lat,lon] (Leaflet order); GeoJSON wants [lon,lat].
const lineFC={type:'FeatureCollection',features:D.lines
  .filter(l=>l.coords&&l.coords.length>1)
  .map(l=>({type:'Feature',properties:{color:l.color,net:l.net,route:l.route},
            geometry:{type:'LineString',coordinates:l.coords.map(c=>[c[1],c[0]])}}))};

const stnFC={type:'FeatureCollection',features:D.stations.map(s=>({
  type:'Feature',properties:{name:s.name,min:s.min,color:NETC[s.net]||'#888'},
  geometry:{type:'Point',coordinates:[s.lon,s.lat]}}))};

const NONE='#000000';
let ready=false;

function money(v){return v==null?'—':'$'+v.toLocaleString()}
function capTxt(v,c){return c?'<span style="color:#fbbf24">≥$3,501</span>':money(v)}

// Effective door-to-door minutes under the selected mode. Bike and walk come
// from the street router; a tract with no routed time stays unshaded rather
// than falling back to a transit figure.
function modeMin(p){
  const m=document.getElementById('mode').value;
  return m==='cycle'?p.cycle:(m==='walk'?p.walk:p.commute);
}

function color(p,cmax,rmin,rmax){
  const t=modeMin(p);
  if(t==null||t>cmax) return null;
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

function ctl(){return{
  cmax:+document.getElementById('cmax').value,
  rmin:+document.getElementById('rmin').value,
  rmax:+document.getElementById('rmax').value
};}

// The fill colour is precomputed per feature and read by the layer expression,
// which keeps one data-driven fill layer instead of re-adding layers per move.
function tractFC(){
  const {cmax,rmin,rmax}=ctl();
  return {type:'FeatureCollection',features:D.tracts.features.map(f=>({
    type:'Feature',
    properties:Object.assign({},f.properties,{fill:color(f.properties,cmax,rmin,rmax)||NONE}),
    geometry:f.geometry
  }))};
}

function popupHTML(p){
  const t=modeMin(p);
  return `<b>${p.station||'—'}</b> <span style="color:#888">(${p.net||''})</span><br>
    ${document.getElementById('mode').value} <b>${t==null?'—':t+' min'}</b><br>
    <span style="color:#888">transit ${p.commute==null?'—':p.commute+'m'} (ride ${p.ride==null?'—':p.ride}m + walk ${p.walkm==null?'—':p.walkm}m)
    · bike ${p.cycle==null?'—':p.cycle+'m'} · walk ${p.walk==null?'—':p.walk+'m'}</span><br>
    studio ${capTxt(p.stu,p.stu_cap)}<br>
    1BR ${capTxt(p.one,p.one_cap)}<br>
    all units ${capTxt(p.allr,p.allr_cap)}<br>
    <span style="color:#888;font-size:11px">tract ${p.geoid}${p.trust?'':'<br>⚠ estimate low-confidence'}</span>`;
}

function detailHTML(p){
  return `<div class="t">${p.station||'—'} <span style="color:#888;font-weight:400">${p.net||''}</span></div>
    <div class="kv"><span>Transit</span><b>${p.commute==null?'—':p.commute+' min'}</b></div>
    <div class="kv"><span>Ride / walk</span><span>${p.ride==null?'—':p.ride}m + ${p.walkm==null?'—':p.walkm}m</span></div>
    <div class="kv"><span>Bike door-to-door</span><span>${p.cycle==null?'—':p.cycle+' min'}</span></div>
    <div class="kv"><span>Walk door-to-door</span><span>${p.walk==null?'—':p.walk+' min'}</span></div>
    <div class="kv"><span>Studio median</span><span>${capTxt(p.stu,p.stu_cap)}</span></div>
    <div class="kv"><span>1BR median</span><span>${capTxt(p.one,p.one_cap)}</span></div>
    <div class="kv"><span>All-units median</span><span>${capTxt(p.allr,p.allr_cap)}</span></div>
    <div class="note">Tract ${p.geoid}. ACS 5-yr 2020–2024, topcoded at $3,501.
    ${p.trust?'':'⚠ relative MOE too high to trust.'}</div>`;
}

map.on('load',()=>{
  map.addSource('tracts',{type:'geojson',data:tractFC()});
  map.addLayer({id:'tracts-fill',type:'fill',source:'tracts',
    paint:{'fill-color':['get','fill'],
           'fill-opacity':['case',['==',['get','fill'],NONE],0,0.55]}});
  map.addLayer({id:'tracts-line',type:'line',source:'tracts',
    filter:['!=',['get','fill'],NONE],
    paint:{'line-color':'#0b0e13','line-width':0.4}});

  map.addSource('lines',{type:'geojson',data:lineFC});
  map.addLayer({id:'lines',type:'line',source:'lines',
    paint:{'line-color':['get','color'],'line-width':2,'line-opacity':0.75}});

  map.addSource('stations',{type:'geojson',data:stnFC});
  map.addLayer({id:'stations',type:'circle',source:'stations',
    paint:{'circle-radius':2.6,'circle-color':['get','color'],
           'circle-stroke-width':0.6,'circle-stroke-color':'#0b0e13'}});

  map.on('click','tracts-fill',e=>{
    const p=e.features[0].properties;
    new maplibregl.Popup({maxWidth:'300px'}).setLngLat(e.lngLat).setHTML(popupHTML(p)).addTo(map);
    const d=document.getElementById('detail');
    d.style.display='block'; d.innerHTML=detailHTML(p);
  });
  map.on('click','stations',e=>{
    const p=e.features[0].properties;
    new maplibregl.Popup({offset:8,maxWidth:'240px'}).setLngLat(e.lngLat)
      .setHTML(`<b>${p.name}</b><br><span style="color:#888">${p.min} min to Penn 1</span>`).addTo(map);
  });
  ['tracts-fill','stations'].forEach(id=>{
    map.on('mouseenter',id,()=>{map.getCanvas().style.cursor='pointer';});
    map.on('mouseleave',id,()=>{map.getCanvas().style.cursor='';});
  });

  ready=true;
  draw();
});

function draw(){
  const {cmax,rmin,rmax}=ctl();
  const onlyB=document.getElementById('onlyBudget').checked;
  document.getElementById('cmaxv').textContent=cmax+'m';
  document.getElementById('rminv').textContent='$'+(rmin/1000).toFixed(1)+'k';
  document.getElementById('rmaxv').textContent='$'+(rmax/1000).toFixed(1)+'k';
  document.getElementById('cmaxlbl').textContent=cmax;
  document.getElementById('rminlbl').textContent=rmin;
  document.getElementById('rmaxlbl').textContent=rmax;

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
  const tb=document.getElementById('tb');
  tb.innerHTML=rows.slice(0,400).map(p=>{
    const st=p.stu_cap?'<span class="p-cap">≥3.5k</span>':money(p.stu);
    const on=p.one_cap?'<span class="p-cap">≥3.5k</span>':money(p.one);
    return `<tr data-g="${p.geoid}"><td>${p.station||'—'}</td>
      <td>${modeMin(p)}m</td><td>${st}</td><td>${on}</td></tr>`;
  }).join('');
  [...tb.querySelectorAll('tr')].forEach(tr=>{
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
                      {maxZoom:14,padding:40});
      }
    };
  });
}

['cmax','rmin','rmax'].forEach(id=>document.getElementById(id).oninput=draw);
document.getElementById('mode').onchange=draw;
document.getElementById('onlyBudget').onchange=draw;
document.getElementById('showLines').onchange=e=>{
  if(ready) map.setLayoutProperty('lines','visibility',e.target.checked?'visible':'none');
};
document.getElementById('showStations').onchange=e=>{
  if(ready) map.setLayoutProperty('stations','visibility',e.target.checked?'visible':'none');
};

</script></body></html>"""


if __name__ == "__main__":
    main()
