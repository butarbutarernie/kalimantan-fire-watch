"""Interactive 1km screening map for research/presentation, no fire polygons."""
from __future__ import annotations
import html
import folium
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen,MiniMap
from grid_1km import candidate_geojson, CLASS_LABELS

ESA_WMS='https://titiler.terrascope.be/wms'
WC_PALETTE={
 'tree_cover':'#17643B','shrubland':'#E7BC41','grassland':'#B3C95F',
 'cropland':'#ECA0D5','built_up':'#E44744','bare_sparse':'#B2B0AF',
 'snow_ice':'#F4F4F4','permanent_water':'#2578C3',
 'herbaceous_wetland':'#58BFC2','mangroves':'#31B981','moss_lichen':'#EBDEA1'}


def make_screening_map(grid:pd.DataFrame,show_worldcover:bool=False,include_context:bool=True,show_all_candidate:bool=True):
    m=folium.Map(location=[-1.05,114.4],zoom_start=6,tiles='CartoDB positron',control_scale=True,prefer_canvas=True)
    folium.TileLayer('OpenStreetMap',name='OpenStreetMap',show=False).add_to(m)
    if show_worldcover:
        folium.raster_layers.WmsTileLayer(url=ESA_WMS,layers='WORLDCOVER_2021_MAP',name='ESA WorldCover 2021 / visual WMS',
           fmt='image/png',transparent=True,show=False,opacity=.55,attr='ESA WorldCover 2021 - CC BY 4.0').add_to(m)
    candidates=grid.loc[grid.candidate.fillna(False)].copy() if 'candidate' in grid else grid.iloc[:0].copy()
    if not candidates.empty and show_all_candidate:
        geo=candidate_geojson(candidates,limit=3000)
        for f in geo['features']:
            f['properties']['peat_hint']='Yes' if (f['properties']['peat_centroid_records'] or 0)>0 else 'No / not evaluated'
        folium.GeoJson(geo,name=f'1km screening candidates ({len(candidates):,})',
          style_function=lambda feat: {'color':'#6D28D9' if feat['properties']['peat_hint']=='Yes' else '#DA7422',
                                       'fillColor':'#8B5CF6' if feat['properties']['peat_hint']=='Yes' else '#F6A83F',
                                       'fillOpacity':.28,'weight':1.0},
          highlight_function=lambda _: {'weight':2,'fillOpacity':.52},
          tooltip=folium.GeoJsonTooltip(fields=['grid_id','province','detections','days','high_confidence','peat_centroid_records','wc_2021'],
             aliases=['Grid 1 km','Provinsi','Rekaman VIIRS/MODIS','Hari dengan deteksi (UTC)','VIIRS high confidence',
                      'Rekaman pusat di poligon gambut','WorldCover 2021 (pilot)'],sticky=True)).add_to(m)
    if include_context:
        context = grid.sort_values('n_detections',ascending=False).head(450)
        lyr=folium.FeatureGroup(name='Lokasi konsentrasi tertinggi (indikator, bukan luas terbakar)',show=False)
        for _,r in context.iterrows():
            label=f"{html.escape(str(r.grid_id))}: {int(r.n_detections)} deteksi, {int(r.n_days)} hari"
            folium.CircleMarker(location=[float(r.center_lat),float(r.center_lon)],
                radius=3.4,color='#2563EB',weight=.6,fill=True,fill_opacity=.6,
                tooltip=label).add_to(lyr)
        lyr.add_to(m)
    if 'wc_available' in grid.columns:
        available=grid[grid.wc_available.fillna(False)].copy()
        if not available.empty:
            wc=folium.FeatureGroup(name=f'WorldCover 2021: {len(available)} 1km cells VERIFIED (pilot)',show=False)
            for _,r in available.iterrows():
                lc=str(r.wc_dominant_class)
                pct=float(r.wc_dominant_pct_valid)
                msg=f"{html.escape(str(r.grid_id))} | 2021 {html.escape(CLASS_LABELS.get(lc,lc))} | {pct:.1f}% of valid pixels"
                folium.CircleMarker([float(r.center_lat),float(r.center_lon)],radius=7,
                     color=WC_PALETTE.get(lc,'#64748b'),fill=True,fill_opacity=.85,
                     tooltip=msg).add_to(wc)
            wc.add_to(m)
    Fullscreen(position='topleft').add_to(m)
    folium.LayerControl(collapsed=False,position='topright').add_to(m)
    return m


def inject_notice(m:folium.Map,observed:str,include_status:bool=True):
    notice=(
      "<div style='position:fixed;bottom:18px;left:50%;transform:translateX(-50%);"
      "z-index:9999;max-width:92vw;padding:12px 20px;background:rgba(13,31,50,.96);"
      "color:white;border-radius:9px;font:600 12px sans-serif;box-shadow:0 3px 18px #0005'>"
      "KALIMANTAN FIRE WATCH | 1 km screening grid | 7-13 OCT 2026 UTC | "
      "SATELLITE DETECTIONS, NOT CONFIRMED FIRES | STATIC HISTORICAL SNAPSHOT</div>"
    )
    m.get_root().html.add_child(folium.Element(notice))
    return m
