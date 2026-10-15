"""Fast, presentation-first Folium map with single-copy NASA FIRMS data.

The map initially shows only 24h thermal anomaly detection points and five
province outlines. Other time windows are exclusive layer-control options;
WorldCover 2021, peat mapping units and village boundaries are optional overlays.
"""
from __future__ import annotations
import html
import json
import math
from typing import Any
import folium
import numpy as np
import pandas as pd
from branca.element import MacroElement, Template
from folium.plugins import Fullscreen
from shapely.geometry import mapping

WORLDCOVER_WMS_URL='https://mapproxy.terrascope.be/mapproxy/service'
WORLDCOVER_WMS_LAYER='esa-worldcover-map-10m-2021-v2_map'


def js_json(value: Any) -> str:
    """JSON embedded inside HTML <script>; neutralize tag termination."""
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).replace('</','<\\/')


def _string(value) -> str:
    if value is None or pd.isna(value): return ''
    return str(value).strip()


def _build_points(records: pd.DataFrame) -> tuple[list,list]:
    """Encode village label once per location rather than repeat per marker."""
    village_map={}
    villages=[]
    dots=[]
    sensor_codes={ 'S-NPP':0,'NOAA-20':1,'NOAA-21':2,'MODIS Terra/Aqua':3 }
    for row in records.itertuples(index=False):
        village=_string(getattr(row,'desa',''))
        kec=_string(getattr(row,'kecamatan',''))
        kab=_string(getattr(row,'kabupaten_desa',''))
        prov=_string(getattr(row,'province',''))
        status=_string(getattr(row,'desa_status',''))
        key=(village,kec,kab,prov,status)
        if key not in village_map:
            village_map[key]=len(villages)
            villages.append(list(key))
        age=float(row.age_h)
        if not math.isfinite(age) or age < -1 or age > 169: continue
        frp=getattr(row,'frp',float('nan'))
        frp=round(float(frp),2) if pd.notna(frp) and math.isfinite(float(frp)) else None
        conf=_string(getattr(row,'confidence_group','Tidak tersedia'))
        if conf=='MODIS numerik':
            conf='MODIS '+_string(getattr(row,'confidence_raw','?'))+' / 100'
        src=_string(getattr(row,'source_name',''))
        # Source names are kept per point: adding optional sensors will work.
        ts=row.acq_utc
        tstr=ts.strftime('%d %b %Y %H:%M UTC') if hasattr(ts,'strftime') else str(ts)
        dots.append([round(float(row.latitude),5),round(float(row.longitude),5),
                     round(age,3),tstr,src,conf,frp,village_map[key]])
    return dots,villages


class TimeWindowLayers(MacroElement):
    """Leaflet map LayerControl; 1/2/3/5/7 day options are exclusive radio items."""
    _template=Template(r"""
{% macro script(this, kwargs) %}
(function(){
 const m={{this._parent.get_name()}};
 const sourceRows={{this.rows_js|safe}};
 const villages={{this.villages_js|safe}};
 const canvas=L.canvas({padding:0.5});
 const windows=[1,2,3,5,7];
 const layers={};
 windows.forEach(d=>{layers[d]=L.layerGroup();});
 const empty=L.layerGroup();
 const basemaps={"Hotspot NASA - 1 hari (24 jam)":layers[1],
   "Hotspot NASA - 2 hari (48 jam)":layers[2],
   "Hotspot NASA - 3 hari (72 jam)":layers[3],
   "Hotspot NASA - 5 hari (120 jam)":layers[5],
   "Hotspot NASA - 7 hari (168 jam)":layers[7],
   "Tanpa titik api":empty};
 const overlays={{this.overlays_js|safe}};
 const layerCtrl=L.control.layers(basemaps,overlays,{position:'topright',collapsed:true,sortLayers:false}).addTo(m);
 function safe(s){return String(s===null||s===undefined?'—':s).replace(/[&<>"']/g,function(x){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x];});}
 function content(r){
   const v=villages[r[7]]||['','','','','Data desa tidak tersedia'];
   const desc=(v[4]==='Ditemukan pada poligon desa') ? safe(v[0]) : safe(v[4]);
   const admin=(v[4]==='Ditemukan pada poligon desa') ?
        `<div class="fire-sub">${safe(v[1])} · ${safe(v[2])} · ${safe(v[3])}</div>` :
        `<div class="fire-sub">Tidak dapat memastikan desa dari peta yang tersedia</div>`;
   return `<div class="kfw-pop"><div class="fire-pop-kicker">NASA FIRMS · Anomali Termal</div>`+
     `<div class="fire-pop-title">${desc||'Tidak tersedia'}</div>${admin}`+
     `<div class="fire-pop-grid"><b>Waktu</b><span>${safe(r[3])}</span>`+
     `<b>Sensor</b><span>${safe(r[4])}</span>`+
     `<b>Confidence</b><span>${safe(r[5])}</span>`+
     `<b>FRP</b><span>${r[6]===null?'Tidak tersedia':safe(r[6]+' MW')}</span></div>`+
     `<div class="fire-pop-foot">Titik pusat deteksi satelit; bukan batas kebakaran atau desa terdampak.</div></div>`;
 }
 function fillLayer(d){
   // Clear all previously constructed windows to keep browser memory bounded.
   windows.forEach(k=>layers[k].clearLayers());
   const cutoff=d*24;
   const group=layers[d];
   for(let i=0;i<sourceRows.length;i++){
     const r=sourceRows[i];
     if(r[2]>cutoff || r[2]<-1)continue;
     const high=(r[5]==='Tinggi'), modis=String(r[4]).indexOf('MODIS')>=0;
     const color=high?'#C93C28':(modis?'#D99B2B':'#F07D29');
     const marker=L.circleMarker([r[0],r[1]],{renderer:canvas,radius:high?4.4:3.3,
       color:'#ffffff',weight:0.5,fillColor:color,fillOpacity:0.89});
     marker.bindPopup(function(){return content(r);},{maxWidth:310,autoPan:true});
     group.addLayer(marker);
   }
 }
 m.on('baselayerchange',function(event){
   for(const d of windows){if(event.layer===layers[d]){fillLayer(d);return;}}
   windows.forEach(k=>layers[k].clearLayers());
 });
 fillLayer(1);
 layers[1].addTo(m);
 // Leaflet map has exactly one initial hotspot window; basemap tiles are fixed.
 const css=document.createElement('style');
 css.textContent='.kfw-pop{font-family:system-ui,Segoe UI,sans-serif;min-width:220px;max-width:280px;overflow-wrap:anywhere}.fire-pop-kicker{font-size:10px;letter-spacing:1px;color:#167e85;font-weight:800}.fire-pop-title{font-size:16px;font-weight:750;color:#10394A;margin:4px 0}.fire-sub{font-size:11px;color:#486578;margin:0 0 10px}.fire-pop-grid{display:grid;grid-template-columns:88px 1fr;font-size:11px;gap:5px}.fire-pop-grid b{color:#23445a}.fire-pop-foot{margin-top:12px;font-size:10px;color:#7b8794;border-top:1px solid #e4e9ed;padding-top:7px}.leaflet-control-layers{max-height:65vh;max-width:min(260px,85vw);overflow:auto;border-radius:12px!important;box-shadow:0 4px 24px #172b3a23!important}.leaflet-popup-content-wrapper{border-radius:12px}';
 document.head.appendChild(css);
 const legend=L.control({position:'bottomleft'});
 legend.onAdd=function(){const el=L.DomUtil.create('div','kfw-legend');
  el.style.cssText='background:rgba(255,255,255,.95);padding:10px 13px;border-radius:10px;box-shadow:0 2px 9px #1b344733;font:12px system-ui;max-width:230px;';
  el.innerHTML='<b>Deteksi termal NASA FIRMS</b><div style="margin-top:4px;"><span style="color:#c93c28">●</span> VIIRS high confidence &nbsp; <span style="color:#f07d29">●</span> VIIRS lainnya</div><div><span style="color:#d99b2b">●</span> MODIS (jika dipilih)</div><small style="color:#64748b">Bukan titik kebakaran terverifikasi</small>';
  return el;};legend.addTo(m);
})();
{% endmacro %}
""")
    def __init__(self, points:pd.DataFrame, overlays:dict):
        super().__init__()
        self._name='TimeWindowLayers'
        rows,villages=_build_points(points)
        self.rows_js=js_json(rows)
        self.villages_js=js_json(villages)
        self.overlays_js='{'+','.join(js_json(name)+':'+layer.get_name() for name,layer in overlays.items())+'}'


def build_live_map(points:pd.DataFrame, provinces:dict, peat_geojson:dict|None=None,
                   village_visual:dict|None=None) -> folium.Map:
    # User chose a hotspot map (not 1 km screening polygons) as the main display.
    m=folium.Map(location=[-0.65,114.5],zoom_start=6,tiles=None,
                 control_scale=True,prefer_canvas=True)
    folium.TileLayer(tiles='CartoDB positron',name='Peta dasar',control=False,
                      attr='CartoDB / OpenStreetMap').add_to(m)
    borders={'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'provinsi':key},'geometry':mapping(poly)}
        for key,poly in provinces.items()]}
    prov=folium.GeoJson(borders,name='Batas provinsi',show=True,control=False,
          style_function=lambda f:{'color':'#126d78','weight':2.0,'fillOpacity':0,'opacity':0.95},
          highlight_function=lambda f:{'color':'#0a4e5c','weight':3}).add_to(m)
    folium.GeoJsonTooltip(fields=['provinsi'],aliases=['Provinsi'],sticky=True).add_to(prov)
    wc=folium.raster_layers.WmsTileLayer(url=WORLDCOVER_WMS_URL,
             layers=WORLDCOVER_WMS_LAYER,fmt='image/png',transparent=True,
             version='1.1.1',overlay=True,control=False,show=False,
             name='ESA WorldCover 2021',attr='ESA WorldCover 2021 (CC BY 4.0)',
             opacity=0.65).add_to(m)
    overlays={'Batas provinsi':prov,'ESA WorldCover 2021':wc}
    if peat_geojson and peat_geojson.get('features'):
        peat=folium.GeoJson(peat_geojson,name='Peta gambut (satuan peta)',show=False,control=False,
           style_function=lambda f:{'color':'#7446A0','weight':0.8,'fillColor':'#9168B1','fillOpacity':0.18},
           highlight_function=lambda f:{'color':'#5B2487','weight':2,'fillOpacity':0.35}).add_to(m)
        folium.GeoJsonTooltip(fields=['LANDFORM','kelas_ketebalan'],aliases=['Landform','Kelas ketebalan'],
                 labels=True,sticky=False).add_to(peat)
        overlays['Peta gambut (bukan api gambut)']=peat
    if village_visual and village_visual.get('features'):
        villages=folium.GeoJson(village_visual,name='Batas desa (visual sederhana)',show=False,control=False,
             style_function=lambda f:{'color':'#475569','weight':0.55,'fillOpacity':0},
             highlight_function=lambda f:{'color':'#254a6d','weight':2,'fillOpacity':0.12}).add_to(m)
        folium.GeoJsonTooltip(fields=['desa','kecamatan','kabupaten','provinsi'],
                      aliases=['Desa','Kecamatan','Kabupaten/Kota','Provinsi'],sticky=False).add_to(villages)
        overlays['Batas desa (opsional)']=villages
    m.add_child(TimeWindowLayers(points,overlays))
    Fullscreen(position='topleft').add_to(m)
    return m
