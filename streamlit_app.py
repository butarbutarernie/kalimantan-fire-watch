"""Kalimantan Fire Watch — interactive NASA FIRMS situational dashboard.

Research / screening prototype. NEVER claim that thermal anomaly points are
confirmed fire events, unique fire incidents or measured burned area.
"""
from __future__ import annotations

import html
import io
import json
import math
import os
import zipfile
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import folium
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from streamlit_folium import st_folium

from fire_viz import make_map, create_zip
from fire_core import (
    GEOBOUNDARIES_META, PROVINCES, SATELLITE_SOURCES,
    SourceError, assign_provinces, cluster_detections, demo_detections,
    describe_age, detections_geojson, fetch_firms, fetch_province_shapes,
    normalize_firms, read_provinces_geojson, temporal_filter,
    to_feature_collection,
)

st.set_page_config(
    page_title="Kalimantan Fire Watch | Satellite Monitoring",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .block-container { padding-top: 1.1rem; padding-bottom: 3rem; max-width: 1550px; }
  [data-testid="stSidebar"] { border-right: 1px solid #E3EAF1; }
  .hero { padding: 20px 24px; border-radius: 14px;
     background: linear-gradient(120deg,#10263e 0%,#1b3e53 62%,#28595b 100%);
     color: white; margin-bottom: 16px; }
  .hero-kicker { font-size: 11px; font-weight: 650; letter-spacing: 1.8px; color: #A8D7D2; }
  .hero-title {font-size: 28px; line-height: 1.17; font-weight: 760; margin: 5px 0 8px;}
  .hero-subtitle {font-size: 13px; color: #D8E4EF; line-height: 1.55;}
  .note {background: #FFF7EC; border-left: 4px solid #F59E0B; border-radius: 5px;
     padding: 10px 13px; font-size: 13px; color: #754617; margin: 10px 0;}
  .demo {background: #7F1D1D; color: white; border-radius: 8px; padding: 10px 14px;
     font-weight: 650; letter-spacing: .3px; margin-bottom: 8px;}
  .fineprint {font-size: 11px; color: #6C7D8F;}
  h3 {padding-top: 0.45rem !important;}
</style>
""", unsafe_allow_html=True)


@st.cache_data(ttl=1800, show_spinner=False)
def load_firms_cached(api_key: str, satellites: tuple[str, ...], calendar_days: int, end_date):
    raw, issues = fetch_firms(api_key, list(satellites), calendar_days=calendar_days, end_utc=end_date)
    return raw, issues, datetime.now(timezone.utc).isoformat()


@st.cache_data(ttl=86400, show_spinner=False)
def shapes_cached():
    shapes = fetch_province_shapes()
    # Cache JSON-safe boundary only; shapely geometry can be handled via cached pickle too.
    return shapes


def secret_key():
    try:
        return st.secrets.get("FIRMS_MAP_KEY", "") or os.environ.get("FIRMS_MAP_KEY", "")
    except Exception:
        return os.environ.get("FIRMS_MAP_KEY", "")


def read_user_boundary(file) -> dict:
    try:
        return read_provinces_geojson(json.loads(file.getvalue().decode("utf-8")))
    except (ValueError, UnicodeDecodeError, KeyError) as exc:
        raise SourceError("GeoJSON unggahan tidak dapat dibaca atau tidak memiliki lima provinsi yang valid.") from exc


def read_user_firms_csv(file) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(file.getvalue()), dtype={"acq_date": str, "acq_time": str}, low_memory=False)
    except (ValueError, pd.errors.ParserError, UnicodeError):
        raise SourceError("CSV unggahan tidak terbaca. Pilih CSV berformat NASA FIRMS.") from None
    if "source_name" not in df.columns and "satellite" in df.columns:
        df["source_name"] = df["satellite"].astype(str)
    return df


# ----------------------------------------------------------------------
# Header and sidebar
# ----------------------------------------------------------------------
st.markdown("""
<div class="hero">
  <div class="hero-kicker">OPEN SATELLITE DATA · KALIMANTAN · RESEARCH PROTOTYPE V1.0</div>
  <div class="hero-title">Kalimantan Fire Watch <span style="color:#FFB767">/ Active Fire Monitoring</span></div>
  <div class="hero-subtitle">Dashboard interaktif deteksi anomali termal VIIRS (NASA FIRMS). 
  Analisis sebaran, pola temporal, dan klaster spasial untuk <i>screening</i> kandidat verifikasi — bukan laporan insiden terkonfirmasi.</div>
</div>
""", unsafe_allow_html=True)

key_available = bool(secret_key())
with st.sidebar:
    st.header("⚙️ Kendali analisis")
    st.caption("Pilih sumber data. NASA FIRMS dan geoBoundaries saja; tanpa BMKG/SiPongi.")
    modes = ["NASA FIRMS API", "Unggah CSV FIRMS", "Demo sintetis"]
    mode = st.radio("Mode sumber", modes, index=0 if key_available else 2, horizontal=False)
    raw = None
    failures = []
    retrieved_utc = None
    api_key = ""
    if mode == "NASA FIRMS API":
        if key_available:
            api_key = secret_key()
            st.success("MAP_KEY terbaca dari pengaturan rahasia aplikasi.")
        else:
            api_key = st.text_input("NASA FIRMS MAP_KEY", type="password", help="Token gratis dari NASA; jangan bagikan ke publik.")
            st.link_button("Ambil MAP_KEY gratis (NASA)", "https://firms.modaps.eosdis.nasa.gov/api/area/", use_container_width=True)
        satellites = st.multiselect("Satelit", list(SATELLITE_SOURCES),
                                     default=list(SATELLITE_SOURCES))
    elif mode == "Unggah CSV FIRMS":
        csv_file = st.file_uploader("CSV NASA FIRMS", type=["csv"],
                                    help="CSV resmi FIRMS yang berisi latitude, longitude, acq_date, acq_time.")
        satellites = []
    else:
        satellites = list(SATELLITE_SOURCES)
        st.info("Mode latihan menggunakan koordinat dan FRP yang disintesis. BUKAN kondisi karhutla aktual.")
    duration = st.select_slider("Rentang waktu bergulir", options=[24, 48, 72, 168], value=72,
                                 format_func=lambda x: "7 hari" if x == 168 else f"{x} jam")
    st.divider()
    st.subheader("Batas wilayah")
    uploaded_boundary = st.file_uploader("GeoJSON ADM1 pribadi (opsional)", type=["json", "geojson"],
                                         help="Jika sumber geoBoundaries gagal, unggah GeoJSON ADM1 dengan lima provinsi.")
    st.caption("Batas publik geoBoundaries gbOpen (CC BY 4.0). Indonesia saja, bukan seluruh Pulau Borneo.")
    st.divider()
    if st.button("🔄 Ambil ulang data (hapus cache)", use_container_width=True):
        load_firms_cached.clear()
        shapes_cached.clear()
        st.rerun()
    st.caption("Cache API: 30 menit. Pembaruan membutuhkan sesi aplikasi aktif; bukan layanan pemantauan tanpa henti.")

now_utc = datetime.now(timezone.utc)
mode_tag = "NASA_FIRMS_LIVE" if mode == "NASA FIRMS API" else ("CSV_UNGGAHAN" if mode == "Unggah CSV FIRMS" else "DEMO_SINTETIS")

if mode == "NASA FIRMS API":
    if not api_key:
        st.info("🔑 Masukkan NASA MAP_KEY gratis pada panel kiri untuk mengaktifkan pemantauan live, atau pilih 'Demo sintetis' untuk melihat antarmuka.")
        st.stop()
    if not satellites:
        st.warning("Pilih minimal satu satelit.")
        st.stop()
    calendar_days = min(10, math.ceil(duration / 24) + 1)
    try:
        with st.spinner("Mengambil deteksi VIIRS terbaru dari NASA FIRMS ..."):
            raw, failures, retrieved_utc = load_firms_cached(api_key, tuple(satellites), calendar_days, now_utc.date())
    except SourceError as exc:
        st.error(f"Pengambilan FIRMS gagal: {exc}")
        st.info("Periksa MAP_KEY dan koneksi. Untuk tetap bekerja, unggah CSV FIRMS atau coba mode demonstrasi.")
        st.stop()
elif mode == "Unggah CSV FIRMS":
    if csv_file is None:
        st.info("⬆️ Unggah CSV NASA FIRMS pada panel kiri agar analisis dimulai.")
        st.stop()
    try:
        raw = read_user_firms_csv(csv_file)
    except SourceError as exc:
        st.error(str(exc))
        st.stop()
else:
    raw = demo_detections(now=now_utc)

try:
    records, qa = normalize_firms(raw, now=now_utc)
except ValueError as exc:
    st.error(f"Struktur data tidak valid: {exc}")
    st.stop()

if records.empty:
    st.warning("Tidak ada catatan valid setelah pemeriksaan koordinat/waktu.")
    st.stop()

# Obtain actual provincial boundaries for strict geographic filtering.
# Demo is still possible if the boundary service is down, but all demo statistics
# remain explicitly synthetic and use its own known test province labels.
shapes = None
boundary_error = None
try:
    if uploaded_boundary is not None:
        shapes = read_user_boundary(uploaded_boundary)
    else:
        shapes = shapes_cached()
except SourceError as exc:
    boundary_error = str(exc)

if shapes is None:
    if mode_tag != "DEMO_SINTETIS":
        st.error("⛔ Batas wilayah Kalimantan belum dapat diverifikasi. Demi mencegah masuknya deteksi negara tetangga, peta operasional tidak dibuat. "
                 + str(boundary_error))
        st.info("Unduh GeoJSON ADM1 Indonesia dari https://www.geoboundaries.org/ dan unggah via panel kiri.")
        st.stop()
    records["province"] = records["demo_true_province"]
    st.warning("geoBoundaries sedang tidak dapat diakses; mode demo memakai label provinsi sintetis tanpa polygon.")
else:
    clipped = assign_provinces(records, shapes)
    qa["outside_adm1_removed"] = len(records) - len(clipped)
    records = clipped

if failures:
    st.warning("Sebagian sumber tidak berhasil diambil, sehingga hitungan berikut bersifat parsial: " + " | ".join(failures[:5]))

# Base observation window is applied AFTER UTC parsing, and all counts are detections.
windowed = temporal_filter(records, duration, now=now_utc)
if windowed.empty:
    st.warning("Tidak ditemukan rekaman deteksi pada rentang waktu ini. Ini TIDAK berarti bebas kebakaran. Coba periode lebih panjang.")
    with st.expander("Hasil QC / waktu observasi"):
        st.json(qa)
        st.write("Observasi terakhir tersedia:", records.acq_utc.max().isoformat() if not records.empty else "Tidak tersedia")
    st.stop()

with st.sidebar:
    st.divider()
    st.subheader("Filter peta")
    selected_provinces = st.multiselect("Provinsi", list(PROVINCES), default=list(PROVINCES))
    selected_conf = st.multiselect("Tingkat keyakinan VIIRS", ["Tinggi", "Nominal", "Rendah", "Tidak tersedia"],
                                   default=["Tinggi", "Nominal"],
                                   help="Tingkat keyakinan deteksi sensor, BUKAN prediksi keparahan kebakaran.")
    actual_sources = sorted(windowed.source_name.astype(str).unique().tolist())
    selected_sources = st.multiselect("Sensor dalam peta", actual_sources, default=actual_sources)
    st.subheader("Peta")
    show_individual = st.checkbox("Tampilkan titik terdeteksi", True)
    show_heat = st.checkbox("Heatmap kepadatan", False)
    show_clusters = st.checkbox("Klaster DBSCAN", True)
    eps_km = st.slider("Jarak DBSCAN (km)", min_value=0.5, max_value=5.0,
                       value=2.0, step=0.5, help="Eksploratif; jarak pengelompokan, bukan radius api.")
    min_records = st.slider("Minimum rekaman per klaster", 2, 10, value=3)

filtered = windowed.loc[
    windowed.province.isin(selected_provinces) &
    windowed.confidence_group.isin(selected_conf) &
    windowed.source_name.isin(selected_sources)
].copy().reset_index(drop=True)

if mode_tag == "DEMO_SINTETIS":
    st.markdown("<div class='demo'>⚠ DATA DEMONSTRASI SINTETIS — BUKAN HOTSPOT AKTUAL KALIMANTAN</div>", unsafe_allow_html=True)
else:
    st.success(f"Sumber: {('NASA FIRMS API' if mode_tag == 'NASA_FIRMS_LIVE' else 'CSV FIRMS unggahan')} · Batas: geoBoundaries Indonesia ADM1 · Semua waktu observasi disimpan dalam UTC")

if filtered.empty:
    st.warning("Tidak ada deteksi yang cocok dengan filter ini. Ubah provinsi, confidence, atau sensor pada panel kiri.")
    st.stop()

with st.spinner("Menganalisis konsentrasi deteksi secara spasial ..."):
    annotated, clusters = cluster_detections(filtered, eps_km=eps_km, min_records=min_records)

newest = filtered.acq_utc.max()
oldest = filtered.acq_utc.min()
newest_age = (pd.Timestamp(now_utc) - newest).total_seconds() / 3600

head1, head2 = st.columns([3, 1])
with head1:
    st.caption(f"WINDOW: {duration} JAM TERAKHIR (UTC) · WAKTU ANALISIS {now_utc.strftime('%d %b %Y %H:%M UTC')}")
with head2:
    if mode_tag != "DEMO_SINTETIS" and newest_age > 24:
        st.warning("Observasi terbaru >24 jam yang lalu")
    else:
        st.caption("DATA DEMO" if mode_tag == "DEMO_SINTETIS" else "Observasi terbaru tersedia")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Rekaman deteksi tersaring", f"{len(filtered):,}", help="Jumlah catatan multisensor, bukan insiden kebakaran unik.")
c2.metric("Deteksi confidence tinggi", f"{(filtered.confidence_group == 'Tinggi').sum():,}")
c3.metric("Klaster eksploratif", f"{len(clusters):,}", help="DBSCAN spasial; satu klaster tidak harus satu kejadian kebakaran.")
c4.metric("Usia observasi terbaru", describe_age(max(0, newest_age)),
          help="Selisih antara waktu UTC sekarang dan akuisisi terbaru. BUKAN latency penerbitan API.")

st.markdown("<div class='note'><b>Interpretasi:</b> titik adalah anomali termal terdeteksi satelit, bukan kejadian kebakaran yang sudah diverifikasi. Sensor berbeda dapat mendeteksi peristiwa yang sama. Ketiadaan hotspot tidak menjamin ketiadaan kebakaran. Klaster hanya membantu penapisan kandidat verifikasi.</div>", unsafe_allow_html=True)

map_tab, trend_tab, cluster_tab, export_tab = st.tabs([
    "🗺️ Peta interaktif", "📈 Statistik & tren", "🎯 Klaster kandidat", "📦 Ekspor & metodologi"
])

with map_tab:
    st.subheader("Sebaran deteksi pada lima provinsi Kalimantan")
    fire_map, plotted = make_map(annotated, clusters, shapes, show_individual, show_heat, show_clusters, mode_tag)
    st_folium(fire_map, height=560, use_container_width=True, returned_objects=[], key="fire-map")
    st.caption(f"Peta menampilkan {(min(len(annotated), 2400) if show_individual else 0):,} dari {len(annotated):,} rekaman sebagai titik (dibatasi demi performa). "
               "Statistik dan unduhan memakai seluruh rekaman tersaring. Ungu = klaster eksploratif; merah = confidence tinggi; jingga = nominal; biru = rendah.")
    st.caption("Peta administratif: geoBoundaries gbOpen CC-BY 4.0 (jika tersedia), bukan batas hukum resmi. Basemap: © OpenStreetMap contributors / CARTO.")
    with st.expander("Lihat 100 rekaman terbaru dengan waktu lokal"):
        cols = ["province", "acq_utc", "source_name", "confidence_group", "frp", "latitude", "longitude"]
        display_data = annotated[cols].head(100).copy()
        display_data["acq_utc"] = display_data["acq_utc"].dt.strftime("%Y-%m-%d %H:%M UTC")
        st.dataframe(display_data, use_container_width=True, hide_index=True)

with trend_tab:
    st.subheader("Distribusi spasial dan temporal")
    a, b = st.columns([1, 1])
    provinces_stat = annotated.groupby("province").agg(
        detections=("latitude", "size"),
        high=("confidence_group", lambda s: int((s == "Tinggi").sum())),
        sources=("source_name", "nunique"),
    ).reset_index()
    provinces_stat = pd.DataFrame({"province": list(PROVINCES)}).merge(provinces_stat, how="left", on="province")
    provinces_stat[["detections", "high", "sources"]] = provinces_stat[["detections", "high", "sources"]].fillna(0).astype(int)
    with a:
        bar = px.bar(provinces_stat.sort_values("detections", ascending=False), x="detections", y="province", orientation="h",
                     color="high", color_continuous_scale="OrRd", labels={"detections": "Rekaman", "province": "", "high": "Confidence tinggi"},
                     title="Rekaman per provinsi (filter aktif)")
        bar.update_layout(yaxis={"categoryorder": "total ascending"}, height=365, margin=dict(l=8,r=8,t=50,b=12))
        st.plotly_chart(bar, use_container_width=True)
    with b:
        source_counts = annotated.groupby(["source_name", "confidence_group"]).size().reset_index(name="n")
        source_fig = px.bar(source_counts, x="source_name", y="n", color="confidence_group", barmode="stack",
                            color_discrete_map={"Tinggi":"#D93025", "Nominal":"#F59E0B", "Rendah":"#38BDF8", "Tidak tersedia":"#94A3B8"},
                            labels={"source_name": "Sensor", "n": "Rekaman", "confidence_group": "Confidence"},
                            title="Deteksi per sensor dan confidence")
        source_fig.update_layout(height=365, margin=dict(l=8,r=8,t=50,b=12))
        st.plotly_chart(source_fig, use_container_width=True)

    st.caption("Tren berikut menggunakan tanggal/jam akuisisi dalam UTC; ketidakteraturan lintasan satelit memengaruhi frekuensi deteksi.")
    by_hour = annotated.assign(hour=annotated.acq_utc.dt.floor("3h")).groupby(["hour", "source_name"]).size().reset_index(name="n")
    timefig = px.line(by_hour, x="hour", y="n", color="source_name", markers=True,
                      labels={"hour":"Waktu akuisisi (UTC, interval 3 jam)", "n":"Rekaman", "source_name":"Sensor"},
                      title="Rekaman deteksi menurut waktu akuisisi")
    timefig.update_layout(height=350, margin=dict(l=8,r=8,t=55,b=12))
    st.plotly_chart(timefig, use_container_width=True)
    st.dataframe(provinces_stat.rename(columns={"province":"Provinsi", "detections":"Rekaman", "high":"Confidence tinggi", "sources":"Sensor"}),
                 hide_index=True, use_container_width=True)
    if annotated.frp.notna().any():
        st.caption(f"FRP maksimum satu deteksi: {annotated.frp.max():.1f} MW. Ini bukan kekuatan total semua kebakaran dan tidak menentukan luas terbakar.")

with cluster_tab:
    st.subheader("Kandidat verifikasi berbasis pengelompokan spasial")
    st.write(f"Algoritme DBSCAN Haversine · jarak maksimum {eps_km:.1f} km · minimum {min_records} catatan. "
             "Klaster dibentuk hanya oleh jarak geografis, bukan prediksi penyebaran api.")
    if clusters.empty:
        st.info("Tidak ada klaster yang memenuhi parameter. Deteksi tunggal tetap bisa penting dan tidak boleh dianggap aman.")
    else:
        screening_counts = clusters.screening_flag.value_counts()
        st.caption("Tanda 'Tinjau ulang deteksi berulang' berlaku bila ada confidence tinggi atau deteksi di beberapa hari UTC; bukan penilaian resmi keparahan kebakaran.")
        out = clusters.copy()
        for col in ["first_seen_utc", "last_seen_utc"]:
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M UTC")
        st.dataframe(out.rename(columns={
            "cluster_id":"ID", "province":"Provinsi", "latitude":"Lat pusat", "longitude":"Lon pusat",
            "detections":"Rekaman", "sensors":"Sensor", "high_confidence_records":"Conf. tinggi",
            "days_utc":"Hari berbeda", "max_frp_mw":"FRP maks. (MW)", "first_seen_utc":"Pertama",
            "last_seen_utc":"Terakhir", "span_hours":"Rentang jam", "screening_flag":"Catatan penapisan",
        }), hide_index=True, use_container_width=True)
    st.markdown("""**Panduan tindak lanjut yang aman**

1. Periksa koordinat, timestamp dan satelit untuk lokasi terindikasi.
2. Bandingkan dengan citra resolusi lebih tinggi serta informasi lapangan yang tersedia.
3. Laporkan kandidat lokasi kepada petugas berwenang untuk verifikasi, **bukan** mengarahkan masyarakat menuju titik api.
4. Jangan menginterpretasikan lingkaran klaster sebagai garis batas kebakaran, arah rambatan, atau zona aman.
""")

with export_tab:
    st.subheader("Ekspor data dan audit ilmiah")
    manifest = {
        "dashboard": "Kalimantan Fire Watch v1.0",
        "mode": mode_tag,
        "analysis_at_utc": now_utc.isoformat(),
        "api_download_at_utc": retrieved_utc,
        "observation_first_utc": oldest.isoformat(),
        "observation_last_utc": newest.isoformat(),
        "window_hours": duration,
        "selected_provinces": selected_provinces,
        "selected_confidence": selected_conf,
        "selected_sources": selected_sources,
        "qa": qa,
        "detections_after_filter": int(len(annotated)),
        "high_confidence_records": int((annotated.confidence_group == "Tinggi").sum()),
        "exploratory_clusters": int(len(clusters)),
        "dbscan_radius_km": eps_km,
        "dbscan_min_records": min_records,
        "sensor_errors_or_warnings": failures,
        "boundary_source": "uploaded-geojson" if uploaded_boundary is not None else (
            "geoBoundaries gbOpen IDN ADM1 CC BY 4.0" if shapes is not None else "demo-only synthetic labels"),
        "data_source_url": "https://firms.modaps.eosdis.nasa.gov/api/area/",
        "meaning": "Multisensor satellite thermal anomaly DETECTIONS, not confirmed unique fires or burned area",
    }
    if mode_tag == "DEMO_SINTETIS":
        manifest["WARNING"] = "ALL DATA SYNTHETIC — DO NOT USE FOR OPERATIONAL FIRE MONITORING"
    meta_col, dl_col = st.columns([1, 1])
    with meta_col:
        st.json(manifest, expanded=False)
    with dl_col:
        st.write("**Paket hasil analisis terfilter**")
        st.caption("Berisi CSV, GeoJSON, tabel klaster, peta HTML interaktif, manifest QC, dan batasan penggunaan.")
        zip_bytes = create_zip(annotated, clusters, manifest, fire_map.get_root().render())
        st.download_button("⬇️ Unduh hasil lengkap (.zip)", data=zip_bytes,
                           file_name=f"Kalimantan_FireWatch_{mode_tag}_{now_utc.strftime('%Y%m%d_%H%M')}.zip",
                           mime="application/zip", type="primary", use_container_width=True)
        csv_bytes = annotated.to_csv(index=False).encode("utf-8-sig")
        st.download_button("Unduh tabel deteksi (.csv)", csv_bytes,
                           file_name=f"Kalimantan_{mode_tag}_detections.csv", mime="text/csv", use_container_width=True)
        st.download_button("Unduh peta offline (.html)", data=fire_map.get_root().render(),
                           file_name=f"Kalimantan_{mode_tag}_map.html", mime="text/html", use_container_width=True)
    st.divider()
    st.markdown("""**Metodologi dan batasan:**

- **Sumber:** NASA FIRMS API (VIIRS Suomi-NPP, NOAA-20, NOAA-21) dan batas provinsi geoBoundaries gbOpen (CC BY 4.0). Tidak menggunakan data SiPongi ataupun BMKG.
- **Kualitas:** pemeriksaan koordinat, timestamp UTC, rekaman duplikat identik dalam sensor sama, serta penyaringan polygon administratif.
- **Interpretasi:** satu baris ialah rekaman deteksi anomali termal satelit; bukan satu kebakaran yang dikonfirmasi.
- **Ketidakpastian:** cakupan awan, waktu lintasan, keterlambatan distribusi, resolusi sensor, dan duplikasi lintas satelit dapat memengaruhi hitungan.
- **Klaster:** DBSCAN Haversine dengan ambang yang dapat disetel, belum divalidasi terhadap data insiden independen.
- **Tidak tersedia dalam versi ini:** prakiraan arah api, delineasi burn scar/hektare terbakar, indeks cuaca kebakaran, status peristiwa terkonfirmasi, sistem alarm otomatis, serta pengiriman tugas lapangan.
- **Penggunaan:** kajian situasional dan screening lokasi untuk verifikasi oleh instansi berwenang; bukan sumber tunggal keputusan keselamatan jiwa/properti.
""")

st.divider()
st.caption("Made for open-data research and rapid situational awareness · NASA FIRMS (active-fire/thermal anomalies) · geoBoundaries gbOpen CC BY 4.0 · © OSM contributors / CARTO for map tiles · Prototype 2026-10-14")
