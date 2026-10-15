"""Kalimantan Fire Watch | 1km presentation dashboard.

Snapshot is historically frozen and explicitly separate from NASA API live mode.
All 1km polygons are aggregation cells, NOT burned area boundaries.
"""
from __future__ import annotations
import io
import json
import os
from datetime import datetime,timezone
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from grid_1km import aggregate_1km,attach_worldcover,PROVINCES,CLASS_LABELS,read_worldcover_csv
from view_1km import make_screening_map
from fire_core import (SATELLITE_SOURCES,SourceError,fetch_firms,fetch_province_shapes,
                       normalize_firms,assign_provinces,read_provinces_geojson,temporal_filter)
from land_context import load_peat_units,annotate_peat_intersections,ContextError

APP=Path(__file__).parent
DATA=APP/'data'
st.set_page_config(page_title='Kalimantan Fire Watch | Grid 1 km',page_icon='\U0001f525',layout='wide')
st.markdown("""<style>
.block-container{max-width:1600px;padding-top:1rem} .hero{background:linear-gradient(120deg,#112940,#175567);color:white;padding:22px 26px;border-radius:13px;margin-bottom:12px}
.kicker{color:#8AD5DA;font-size:11px;letter-spacing:1.5px;font-weight:700}
.big{font-size:28px;font-weight:780;margin:6px 0} .sm{color:#D2E8EE;font-size:13px}
.caution{background:#FFF7E9;border-left:4px solid #F59E0B;padding:12px;border-radius:7px;color:#69410E;margin:12px 0}
</style>""",unsafe_allow_html=True)

@st.cache_data(show_spinner=False)
def load_snapshot():
    g=pd.read_csv(DATA/'snapshot_grid_1km_20261007_13.csv')
    g['candidate']=g.candidate.astype(bool)
    g['wc_available']=g.wc_available.astype(bool)
    return (g,pd.read_csv(DATA/'snapshot_trend_province_day.csv'),
            pd.read_csv(DATA/'snapshot_province_summary.csv'),
            json.loads((DATA/'snapshot_metadata.json').read_text(encoding='utf8')))

@st.cache_data(ttl=1800,show_spinner=False)
def load_api(key, satellites,days,end_date):
    return fetch_firms(key,list(satellites),calendar_days=days,end_utc=end_date)

@st.cache_data(ttl=86400,show_spinner=False)
def load_shapes():
    return fetch_province_shapes()


def get_key():
    try:return st.secrets.get('FIRMS_MAP_KEY','') or os.environ.get('FIRMS_MAP_KEY','')
    except Exception:return os.environ.get('FIRMS_MAP_KEY','')

st.markdown("""<div class='hero'><div class='kicker'>OPEN SATELLITE DATA | RESEARCH SCREENING | VERSION 1.3</div>
<div class='big'>Kalimantan Fire Watch / 1 km Grid</div>
<div class='sm'>Multi-day VIIRS monitoring + peat-soil map context + ESA WorldCover 2021 baseline (verified pilot only).<br>
For situational research support, not confirmed fire incidents, burned area or an official hazard classification.</div></div>""",unsafe_allow_html=True)
with st.sidebar:
    st.header('Kendali dashboard')
    mode=st.radio('Sumber data', ['Snapshot audit 7-13 Okt 2026','NASA FIRMS terbaru (API)'],index=0)
    st.caption('Mode snapshot adalah dataset arsip yang dibekukan untuk presentasi. Pilih API untuk pengamatan terkini.')
    wc_file=st.file_uploader('CSV WorldCover 2021 tambahan (1 km)',type='csv',help='Boleh memakai hasil batch berikutnya; tanpa file baru hanya 11 sel pilot memiliki konteks WorldCover.')
    show_wc_wms=st.checkbox('Layer visual WorldCover 2021 WMS (opsional)',False)
    peat_file=st.file_uploader('GeoPackage gambut privat (opsional, hanya mode API)',type=['gpkg','geojson','json'])
    st.caption('Poligon gambut tidak disertakan dalam repository. Untuk snapshot, atribut centroid gambut dari audit tersedia.')

if mode.startswith('Snapshot'):
    all_grid,daily,prov_summary,metadata=load_snapshot()
    data_label='SNAPSHOT ARSIP (BUKAN LIVE)'
    freshness=metadata['last_observation_utc']
    status_note='Data FIRMS 7-13 Oktober 2026; waktu analisis 14 Okt 2026 03:14 UTC. Tidak diperbarui otomatis.'
    if wc_file is not None:
        try:
            wc=read_worldcover_csv(wc_file.getvalue())
            all_grid=all_grid.drop(columns=[c for c in all_grid.columns if c.startswith('wc_')],errors='ignore')
            all_grid,matched=attach_worldcover(all_grid,wc)
        except Exception as exc:
            st.error(f'WorldCover CSV tidak sesuai format: {exc}')
            st.stop()
else:
    st.info('Mode API mengambil data terbaru dari NASA. Hasil agregasi 1 km dihitung ulang. Konteks gambut hanya muncul jika GeoPackage privat diunggah.')
    with st.sidebar:
        api_key=get_key()
        if not api_key:
            api_key=st.text_input('NASA FIRMS MAP_KEY',type='password')
        sat=st.multiselect('Sensor',list(SATELLITE_SOURCES.keys()),default=['S-NPP','NOAA-20','NOAA-21'])
        hours=st.select_slider('Jendela waktu (jam)',options=[24,48,72,168],value=168)
        boundary_file=st.file_uploader('ADM1 Kalimantan GeoJSON cadangan (jika API batas gagal)',type=['geojson','json'])
    if not api_key or not sat:
        st.warning('Aktifkan MAP_KEY dalam Streamlit Secrets dan pilih sensor.')
        st.stop()
    now=datetime.now(timezone.utc)
    try:
        with st.spinner('Mengambil deteksi NASA FIRMS dan memeriksa batas provinsi ...'):
            raw,warnings=load_api(api_key,tuple(sat),min(10,(hours+23)//24+1),now.date())
            norm,qc=normalize_firms(raw,now=now)
            if boundary_file is not None:
                shapes=read_provinces_geojson(json.loads(boundary_file.getvalue().decode('utf8')))
            else:
                shapes=load_shapes()
            norm=assign_provinces(norm,shapes)
            norm=temporal_filter(norm,hours,now=now)
    except Exception as exc:
        st.error(f'Sumber tidak berhasil dibaca: {type(exc).__name__}. Silakan cek koneksi, autentikasi, dan format data.')
        st.stop()
    if norm.empty:
        st.warning('Tidak ada rekaman dalam jendela pengamatan; tidak berarti bebas kebakaran.')
        st.stop()
    if warnings:
        st.warning('Sumber parsial: '+ ' | '.join(warnings[:5]))
    if peat_file is not None:
        try:
            peat=load_peat_units(peat_file.getvalue(),peat_file.name)
            norm=annotate_peat_intersections(norm,peat)
            norm['peat_flag']=norm.peat_unit_status.eq('Di dalam satuan peta')
        except ContextError as exc:
            st.warning('Gambut tidak tersedia: '+str(exc))
    all_grid=aggregate_1km(norm,now)
    if wc_file is not None:
        try:
            wc=read_worldcover_csv(wc_file.getvalue())
            all_grid,matched=attach_worldcover(all_grid,wc)
        except Exception as exc:
            st.warning(f'WorldCover tambahan tidak dapat dicocokkan: {exc}')
    if 'wc_available' not in all_grid:
        all_grid['wc_available']=False
        all_grid['wc_dominant_class']=np.nan
    prov_summary=(norm.groupby('province').agg(detections=('latitude','size'),
            high=('confidence_group',lambda v: int(((v=='Tinggi') & (norm.loc[v.index,'source_family']=='VIIRS')).sum())))
             .reset_index())
    daily=(norm.assign(utc_day=norm.acq_utc.dt.strftime('%Y-%m-%d'))
           .groupby(['province','utc_day']).size().rename('detections').reset_index())
    data_label='NASA FIRMS API | OBSERVASI TERBARU'
    freshness=str(norm.acq_utc.max())
    status_note='Berdasarkan waktu UTC saat membuka aplikasi. Setiap sensor memiliki interval pengamatan berbeda.'

with st.sidebar:
    provinces=st.multiselect('Provinsi',PROVINCES,default=PROVINCES)
    only_candidate=st.checkbox('Hanya kandidat peninjauan',True)
    only_peat=st.checkbox('Hanya grid yang memiliki deteksi pada satuan gambut',False)
    min_days=st.slider('Minimum hari dengan deteksi (UTC)',1,7,1)

subset=all_grid[all_grid.province.isin(provinces)].copy()
subset=subset.loc[subset.n_days>=min_days]
if only_candidate:
    subset=subset[subset.candidate]
if only_peat:
    if subset['n_peat_detections'].notna().any():
        subset=subset.loc[subset.n_peat_detections.fillna(0)>0]
    else:
        st.warning('Overlay gambut belum tersedia di mode ini; filter gambut tidak dapat diterapkan.')
        subset=subset.iloc[:0]

st.caption(data_label+' | Observasi terbaru: '+str(freshness))
st.info(status_note)
if subset.empty:
    st.warning('Filter tidak menghasilkan sel. Kurangi kriteria atau tambah provinsi.')
    st.stop()

r1,r2,r3,r4=st.columns(4)
r1.metric('Sel 1 km (cakupan data)',f'{len(all_grid):,}',help='Jumlah sel grid dengan deteksi, bukan jumlah api')
r2.metric('Kandidat peninjauan 1 km',f'{int(all_grid.candidate.sum()):,}',help='Kriteria eksploratif 3 hari, 2 sensor, >=1 high, terbaru <=72h')
r3.metric('Sel kandidat dalam filter',f'{len(subset):,}')
r4.metric('WorldCover 2021 tersedia',f'{int(all_grid.wc_available.fillna(False).sum()):,}',help='Hanya sel pilot atau data CSV tambahan yang sudah diproses')

st.markdown("<div class='caution'><b>Interpretasi ilmiah:</b> hotspot adalah anomali termal, bukan jumlah kebakaran unik. Grid 1 km adalah unit analisis, bukan jejak api. Deteksi pada peta gambut bukan bukti kebakaran gambut. WorldCover 2021 adalah baseline historis; sel yang belum diproses ditandai sebagai tidak tersedia.</div>",unsafe_allow_html=True)

maptab,statstab,wctab,exporttab=st.tabs(['Peta grid 1 km','Tren & provinsi','Gambut dan tutupan lahan','Ekspor & catatan presentasi'])
with maptab:
    st.subheader('Lokasi kandidat peninjauan berbasis deteksi berulang')
    if len(subset)>800:
        st.caption('Peta membatasi poligon pada 800 sel teratas menurut dukungan deteksi agar tetap responsif. Statistik dan ekspor mencakup seluruh sel tersaring.')
    top_candidate = subset[subset.candidate].head(800)
    context_only = subset.head(800) if not only_candidate else top_candidate
    m=make_screening_map(context_only,show_worldcover=show_wc_wms,include_context=True)
    st_folium(m,height=620,use_container_width=True,returned_objects=[],key='screening-map')
    st.caption('Poligon hanya ditampilkan untuk sel yang memenuhi kriteria kandidat. Ungu = ada pusat deteksi pada unit peta gambut. Jingga = tidak ada / belum diperiksa. Bukan perimeter kebakaran.')
    showcols=['grid_id','province','n_detections','n_days','n_sensors','n_high','n_peat_detections','wc_dominant_class','last_seen_utc']
    st.dataframe(subset[[c for c in showcols if c in subset]].head(100),use_container_width=True,hide_index=True)
with statstab:
    l,r=st.columns(2)
    v=prov_summary[prov_summary.province.isin(provinces)]
    with l:
        st.plotly_chart(px.bar(v.sort_values('detections'),x='detections',y='province',orientation='h',
             title='Rekaman deteksi per provinsi (bukan insiden)',labels={'province':'','detections':'Rekaman'}),use_container_width=True)
    with r:
        byp=(all_grid[all_grid.province.isin(provinces)].groupby('province').candidate.sum()
                .reset_index(name='candidate_cells'))
        st.plotly_chart(px.bar(byp.sort_values('candidate_cells'),x='candidate_cells',y='province',orientation='h',
             title='Kandidat grid 1 km menurut provinsi',labels={'province':'','candidate_cells':'Sel'}),use_container_width=True)
    if len(daily):
        d=daily[daily.province.isin(provinces)].groupby('utc_day',as_index=False).detections.sum()
        st.plotly_chart(px.line(d,x='utc_day',y='detections',markers=True,
            title='Tren harian menurut akuisisi UTC',labels={'utc_day':'Tanggal UTC','detections':'Jumlah deteksi'}),use_container_width=True)
        st.caption('Tren belum dikoreksi menurut tutupan awan, lintasan satelit dan jumlah overpass. Tidak dapat langsung diartikan sebagai kenaikan luas kebakaran.')
with wctab:
    st.subheader('Konteks lahan: gambut dan WorldCover 2021')
    if all_grid['n_peat_detections'].notna().any():
        st.metric('Kandidat dengan >=1 deteksi pada unit peta gambut',int((all_grid.candidate & all_grid.n_peat_detections.fillna(0).gt(0)).sum()))
    else:
        st.warning('Tidak ada peta gambut yang diunggah untuk analisis ini.')
    known=all_grid[all_grid.wc_available.fillna(False)]
    st.info(f'Statistik WorldCover 2021 lengkap hanya pada {len(known):,} dari {len(all_grid):,} sel 1 km dengan deteksi. Tidak ada extrapolasi ke sel lain.')
    if not known.empty:
        w=known[['grid_id','province','wc_dominant_class','wc_dominant_pct_valid','wc_coverage_pct','n_detections']].copy()
        w.wc_dominant_class=w.wc_dominant_class.map(CLASS_LABELS).fillna(w.wc_dominant_class)
        st.dataframe(w,use_container_width=True,hide_index=True)
    st.caption('Sumber ESA WorldCover v200 (2021) 10 m. Persentase adalah fraksi luasan kelas menurut piksel peta 2021, bukan peluang terjadinya kebakaran. Sumber gambut: peta satuan tanah gambut skala 1:50.000 dari pengguna; metadatanya harus dipertahankan.')
with exporttab:
    st.subheader('Data dan provenance untuk presentasi')
    st.download_button('Unduh CSV grid 1 km (filter aktif)',subset.to_csv(index=False).encode('utf-8'),
        file_name='Kalimantan_FireWatch_Grid1km_Filtered.csv',mime='text/csv',use_container_width=True)
    st.download_button('Unduh GeoJSON kandidat peninjauan (maks. 800 sel)',
        json.dumps(__import__('grid_1km').candidate_geojson(subset[subset.candidate].head(800)),ensure_ascii=False).encode('utf8'),
        file_name='Kalimantan_FireWatch_Grid1km_Screening_20261014.geojson',mime='application/geo+json',use_container_width=True)
    st.markdown('**Batasan yang wajib dibacakan dalam presentasi:**')
    st.markdown('''1. Periode snapshot 7-13 Oktober 2026 menggunakan rekaman NASA FIRMS VIIRS yang diperoleh pada 14 Oktober 2026, bukan data saat ini.
2. Deteksi VIIRS/MODIS adalah anomali termal; angka tidak boleh disebut jumlah kebakaran, luas terbakar atau tingkat bahaya resmi.
3. Kriteria kandidat grid 1 km bersifat eksploratif: >=3 hari berbeda, >=2 sensor, >=1 VIIRS high, terbaru <=72 jam.
4. Overlay gambut berdasarkan posisi pusat deteksi dan peta satuan tanah, bukan verifikasi kebakaran gambut.
5. ESA WorldCover 2021 hanya baseline historis; data kuantitatif belum tersedia untuk semua sel.
6. Ambang dan ukuran grid belum dikalibrasi dengan ground-check, perubahan titik asal grid atau uji sensitivitas lengkap.''')
    st.caption('Sumber: NASA FIRMS, ESA WorldCover 2021 (CC BY 4.0), peta satuan tanah gambut dari pengguna, geoBoundaries untuk mode API.')
