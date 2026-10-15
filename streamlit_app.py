"""Kalimantan Fire Watch v1.4.

Default: NASA FIRMS live 24h thermal points + province boundaries.
Independent map-layer radio options: 1, 2, 3, 5, 7 rolling days.
Land cover 2021 and peatland visual overlays are off by default.
Village names are assigned from original user village polygons, not display simplification.
"""
from __future__ import annotations
import gzip
import json
import os
import logging
from datetime import datetime,timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from nasa_live import (SOURCES,PROVINCES,DataUnavailable,fetch_nasa,normalize_nasa,
                       fetch_provinces,local_province_backup,assign_provinces)
from village_lookup import annotate_villages,load_visual_geojson,inspect_village_assets
from view_v14 import build_live_map

BASE=Path(__file__).resolve().parent
DATA=BASE/'data'
st.set_page_config(page_title='Kalimantan Fire Watch | Near-Real-Time',page_icon='🔥',layout='wide',initial_sidebar_state='expanded')

st.markdown('''<style>
html,body,[class*="css"]{font-family:Inter,system-ui,-apple-system,"Segoe UI",sans-serif}
.block-container{max-width:1550px;padding-top:1.05rem;padding-bottom:2.3rem}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#f5f9fa 0%,#eef4f5 100%)}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.6rem}
.kfw-head{background:linear-gradient(116deg,#102c3d 0%,#164d5d 53%,#187b80 100%);border-radius:16px;padding:23px 27px;color:#fff;box-sizing:border-box;width:100%;overflow-wrap:anywhere;box-shadow:0 8px 24px #17304b17}
.kfw-head .kicker{color:#aee7d9;font-size:.68rem;font-weight:800;letter-spacing:.15em}
.kfw-head .main-title{color:#fff;font-size:clamp(1.4rem,2.25vw,2.45rem);line-height:1.18;font-weight:790;margin:9px 0 7px}
.kfw-head .subtext{color:#d9edf1;font-size:.86rem;line-height:1.6;max-width:900px}
.kfw-note{border:1px solid #d9e7ea;background:#f6fafb;border-left:4px solid #167a88;border-radius:12px;padding:12px 16px;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere;color:#28475a;font-size:.86rem;line-height:1.55;margin:8px 0 12px}
.kfw-note.warning{background:#fff9ed;border-color:#f3dfb2;border-left-color:#c8851f;color:#684713}
.kfw-note.error{background:#fff1ef;border-color:#f6cec9;border-left-color:#cc483f;color:#782e2b}
.kfw-metric{border:1px solid #dce8eb;background:#fff;border-radius:12px;padding:12px 15px;min-height:90px;box-sizing:border-box;max-width:100%;overflow-wrap:anywhere}
.kfw-metric small{display:block;color:#617b85;font-weight:600;letter-spacing:.02em;font-size:.75rem}
.kfw-metric strong{display:block;font-weight:800;font-size:clamp(1.3rem,2.2vw,2rem);color:#163e4e;margin:6px 0 0}
.map-help{background:#edf4f6;border-radius:9px;padding:9px 13px;color:#486673;font-size:.79rem;overflow-wrap:anywhere;margin-bottom:6px}
@media(max-width:740px){.kfw-head{padding:17px}.kfw-head .subtext{font-size:.8rem}.kfw-metric{min-height:70px}}
</style>''',unsafe_allow_html=True)

@st.cache_data(ttl=600,show_spinner=False)
def cached_live(key,sensors,today):
    return fetch_nasa(key,tuple(sensors),today)

@st.cache_data(ttl=86400,show_spinner=False)
def cached_provinces():
    return fetch_provinces()

@st.cache_data(show_spinner=False)
def cached_local_provinces():
    return local_province_backup(DATA/'province_backup_from_villages.geojson')

@st.cache_data(show_spinner=False)
def cached_peat():
    path=DATA/'peat_visual.geojson.gz'
    if not path.is_file(): return None
    with gzip.open(path,'rt',encoding='utf8') as f:return json.load(f)

@st.cache_data(show_spinner=False)
def cached_village_visual(tuple_provs):
    return load_visual_geojson(DATA,list(tuple_provs))


def nasa_key():
    try: val=st.secrets.get('FIRMS_MAP_KEY','')
    except Exception: val=''
    return str(val or os.environ.get('FIRMS_MAP_KEY','')).strip()


def notice(text,kind='info'):
    st.markdown(f'<div class="kfw-note {kind}">{text}</div>',unsafe_allow_html=True)


def metric_box(label,value):
    return f'<div class="kfw-metric"><small>{label}</small><strong>{value}</strong></div>'

st.markdown('''<div class="kfw-head">
 <div class="kicker">REMOTE SENSING · NEAR-REAL-TIME SITUATIONAL MONITORING</div>
 <div class="main-title">🔥 Kalimantan Fire Watch</div>
 <div class="subtext">Titik anomali termal NASA FIRMS • Batas provinsi dan desa • ESA WorldCover 2021 • Satuan peta gambut<br>
 Penapisan berbasis citra satelit untuk riset dan kewaspadaan situasional — bukan laporan insiden kebakaran terkonfirmasi.</div>
 </div>''',unsafe_allow_html=True)

with st.sidebar:
    st.markdown('### <span style="color:#164c5e">Kendali peta</span>',unsafe_allow_html=True)
    st.caption('Peta awal: hotspot 24 jam dan batas provinsi. Pilih jendela waktu dan layer lainnya dari menu Layers pada peta.')
    selected=st.multiselect('Provinsi',PROVINCES,default=PROVINCES)
    sensor=st.multiselect('Satelit NASA FIRMS',list(SOURCES),default=['S-NPP','NOAA-20','NOAA-21'])
    show_village_overlay=st.checkbox('Siapkan layer batas desa (lebih berat)',value=False,
          help='Nama desa dalam popup hotspot tetap tersedia meskipun overlay batas desa tidak diaktifkan.')
    if st.button('↻ Perbarui NASA FIRMS',use_container_width=True):
        cached_live.clear()
        st.rerun()
    st.divider()
    st.caption('**Keterangan peta**')
    st.caption('• Periode 1/2/3/5/7 hari adalah jendela waktu bergulir dari UTC saat pemuatan data. Pilih satu periode pada panel Layers.')
    st.caption('• WorldCover 2021 merupakan data historis, bukan kondisi 2026.')
    st.caption('• Satuan peta gambut tidak menunjukkan api gambut terkonfirmasi.')
    st.caption('• Nama desa berdasar batas pengguna; tanggal dan otoritas sumber perlu diverifikasi.')
    st.caption('• Nama desa dibaca dari atribut NAMOBJ pada shapefile; nama kosong/undefined tidak ditebak.')
    village_files=inspect_village_assets(DATA)
    if village_files['ready_for_all_provinces']:
        st.success('Referensi nama desa: 5/5 provinsi tersedia.')
    else:
        st.warning(f"Referensi desa: {village_files['found_count']}/5 file tersedia di GitHub.")
        with st.expander('Daftar file desa yang perlu diunggah'):
            st.code('\n'.join('data/'+name for name in village_files['missing_files']))
            st.caption('Unggah isi ZIP ke folder data/ pada branch yang digunakan Streamlit, bukan ZIP-nya.')

now=datetime.now(timezone.utc)
shapes=None
using_backup=False
try:
    shapes=cached_provinces()
except DataUnavailable:
    try:
        shapes=cached_local_provinces()
        using_backup=True
    except Exception:
        shapes=None

key=nasa_key()
records=pd.DataFrame()
api_warnings=[]
api_error=None
village_exception=None
if not key:
    api_error='MAP_KEY NASA belum terpasang dalam Streamlit Secrets (FIRMS_MAP_KEY).'
elif not sensor:
    api_error='Pilih minimal satu satelit NASA FIRMS.'
else:
    try:
        with st.spinner('Mengunduh 7 hari NASA FIRMS dan menyiapkan peta interaktif ...'):
            raw,api_warnings=cached_live(key,tuple(sensor),now.date())
            records=normalize_nasa(raw,now)
            if shapes is not None: records=assign_provinces(records,shapes)
            else:
                records=records.loc[records.longitude.between(108,120)&records.latitude.between(-5.2,4.6)].copy()
                records['province']='Wilayah belum diverifikasi'
            if len(records):
                if shapes is not None:
                    records=records[records.province.isin(selected)].copy()
                try:
                    records=annotate_villages(records,DATA)
                except Exception as exc:
                    # The NASA data must survive a village archive error.
                    # Never log API URLs or the private NASA MAP_KEY.
                    logging.getLogger(__name__).exception(
                        'Unexpected village lookup failure (type=%s)', type(exc).__name__)
                    village_exception=type(exc).__name__
                    for col in ['desa','kecamatan','kabupaten_desa','desa_kode_pum']:
                        records[col]=''
                    records['desa_status']='Pencocokan desa gagal; lihat diagnostik di sidebar'

    except (DataUnavailable,ValueError,OSError) as exc:
        api_error=str(exc)
        records=pd.DataFrame()
    except Exception:
        api_error='Terjadi masalah pemrosesan NASA FIRMS. Periksa log dan koneksi. Kredensial tidak ditampilkan.'
        records=pd.DataFrame()

if village_exception:
    notice('Nama desa belum dapat dihitung karena kesalahan pembacaan referensi. Buka <b>Kendali peta</b> untuk memeriksa data desa yang terpasang; catatan rinci ada di log Streamlit.', 'warning')
if using_backup:
    notice('Batas provinsi saat ini memakai <b>cadangan hasil penggabungan shapefile desa</b>, bukan ADM1 resmi. Sebagian area tanpa poligon desa mungkin tidak tercakup.','warning')
if shapes is None:
    notice('Batas provinsi belum dapat dimuat. Peta dasar akan tetap ditampilkan, tetapi verifikasi administratif titik api terbatas.','warning')
if api_warnings:
    notice('Sebagian sumber NASA tidak merespons: '+str(len(api_warnings))+' permintaan. Statistik berasal dari data yang berhasil diterima.','warning')
if not records.empty and 'desa_status' in records.columns:
    village_failures=int(records['desa_status'].isin([
        'Arsip batas desa gagal dibaca', 'Pencocokan koordinat desa gagal'
    ]).sum())
    if village_failures:
        notice(f'<b>Perhatian:</b> {village_failures:,} rekaman NASA belum mendapatkan nama desa karena file atau operasi geometri gagal. Peta titik tetap tampil. Periksa unggahan <code>data/villages_lookup_*.npz</code> dan log Streamlit.', 'warning')
if api_error:
    notice('<b>Deteksi NASA belum dapat ditampilkan.</b> '+api_error.replace('<','&lt;').replace('>','&gt;'),'error')
else:
    if len(records):
        latest=records.acq_utc.max().strftime('%d %b %Y %H:%M UTC')
        notice(f'<b>NASA FIRMS berhasil dimuat.</b> Observasi paling baru: {latest}. Data disimpan di cache selama 10 menit; tombol Perbarui untuk memuat ulang. Pilih jendela 1–7 hari pada menu <b>Layers</b>.')
    else:
        notice('Tidak ada anomali termal yang tercatat pada wilayah dan sensor terpilih dalam jendela 7 hari. Ini bukan bukti tidak terjadi kebakaran.','warning')

if not records.empty:
    d1=records.loc[records.age_h.between(0,24,inclusive='both')]
    d7=records.loc[records.age_h.between(0,168,inclusive='both')]
    m1,m2,m3,m4=st.columns(4)
    with m1:st.markdown(metric_box('Deteksi 24 jam',f'{len(d1):,}'),unsafe_allow_html=True)
    with m2:st.markdown(metric_box('Deteksi 7 hari',f'{len(d7):,}'),unsafe_allow_html=True)
    with m3:st.markdown(metric_box('VIIRS confidence tinggi',f'{int((d1.confidence_group=="Tinggi").sum()):,}'),unsafe_allow_html=True)
    with m4:st.markdown(metric_box('Desa teridentifikasi 24 jam',f'{d1.loc[d1.desa_status.eq("Ditemukan pada poligon desa") & d1.desa_kode_pum.ne(""),"desa_kode_pum"].nunique():,}'),unsafe_allow_html=True)
else:
    m1,m2,m3,m4=st.columns(4)
    for col,label in zip([m1,m2,m3,m4],['Deteksi 24 jam','Deteksi 7 hari','VIIRS confidence tinggi','Desa teridentifikasi']):
        with col:st.markdown(metric_box(label,'—'),unsafe_allow_html=True)

st.markdown('### Peta deteksi anomali termal')
st.markdown('<div class="map-help">Menu <b>Layers</b> di sudut kanan atas: pilih <b>1, 2, 3, 5 atau 7 hari</b> dan aktifkan WorldCover 2021, gambut, batas provinsi, atau desa. Tampilan awal hanya menampilkan titik NASA 24 jam dan batas provinsi. <b>Klik titik untuk melihat nama desa dan atribut deteksi.</b></div>',unsafe_allow_html=True)
peat=None
try: peat=cached_peat()
except Exception:pass
visual=None
if show_village_overlay:
    try:
        visual=cached_village_visual(tuple(selected))
    except Exception:
        st.warning('Garis batas desa belum dapat dimuat; atribut nama desa pada titik tetap tersedia.')
try:
    m=build_live_map(records,shapes or {},peat_geojson=peat,village_visual=visual)
    st_folium(m,height=645,use_container_width=True,returned_objects=[],key='kfw-live-map')
except Exception as exc:
    st.error('Peta interaktif belum berhasil dirender. Periksa data dan pustaka Folium.')
    st.code(f'{type(exc).__name__}: {str(exc)[:400]}')

st.markdown('<div class="kfw-note warning"><b>Interpretasi:</b> setiap titik merupakan deteksi anomali termal, bukan satu kejadian kebakaran independen atau luas terbakar. Lokasi titik adalah koordinat pusat piksel sensor. Nama desa mengacu pada poligon yang beririsan dengan titik tersebut, bukan desa yang dipastikan terdampak. Peta gambut adalah konteks tanah, bukan bukti kebakaran gambut.</div>',unsafe_allow_html=True)

if len(records):
    with st.expander('Ringkasan provinsi dan pengamatan satelit',expanded=False):
        a,b=st.columns(2)
        d=records[records.age_h.between(0,24)].copy()
        with a:
            if len(d):
                prov=d.groupby('province').size().sort_values().reset_index(name='deteksi')
                st.plotly_chart(px.bar(prov,x='deteksi',y='province',orientation='h',
                 title='Deteksi termal 24 jam per provinsi',labels={'province':'','deteksi':'Jumlah rekaman'}),
                 use_container_width=True)
            else: st.caption('Tidak ada deteksi 24 jam untuk grafik provinsi.')
        with b:
            days=(records.assign(tanggal=records.acq_utc.dt.strftime('%Y-%m-%d'))
                  .groupby('tanggal').size().reset_index(name='deteksi'))
            st.plotly_chart(px.bar(days,x='tanggal',y='deteksi',
                    title='Deteksi per tanggal akuisisi UTC (7 hari)'),use_container_width=True)
        st.caption('Perbandingan lintas hari dipengaruhi lintasan satelit, awan dan cakupan pengamatan; bukan tren luas kebakaran terkalibrasi.')
    with st.expander('Tabel titik NASA FIRMS dan desa',expanded=False):
        cols=['acq_utc','source_name','province','desa','kecamatan','kabupaten_desa',
              'desa_status','confidence_group','confidence_raw','frp','latitude','longitude']
        st.dataframe(records[cols].head(3000),hide_index=True,use_container_width=True,height=320)
        st.download_button('Unduh seluruh titik dan atribut desa (CSV)',
            records[cols].to_csv(index=False).encode('utf8'),
            file_name='Kalimantan_FireWatch_NASA_FIRMS_7d_Desa.csv',mime='text/csv')

st.caption('Sumber: NASA FIRMS VIIRS/MODIS NRT; geoBoundaries (atau batas cadangan terlabel); ESA WorldCover 2021 v200, CC BY 4.0; peta gambut dan batas desa dari berkas pengguna. Pemantauan mendekati waktu nyata, bukan siaran langsung tanpa jeda.')
