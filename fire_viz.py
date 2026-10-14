"""Folium visualization and reproducible export functions without Streamlit dependency."""
from __future__ import annotations
import html
import io
import json
import math
import zipfile

import folium
import numpy as np
import pandas as pd
from folium.plugins import Fullscreen, HeatMap, MiniMap
from fire_core import detections_geojson, to_feature_collection

def status_local(row):
    tz = "Asia/Jakarta" if row.get("province") in ("Kalimantan Barat", "Kalimantan Tengah") else "Asia/Makassar"
    return row["acq_utc"].tz_convert(tz).strftime("%d %b %Y, %H:%M") + (" WIB" if tz.endswith("Jakarta") else " WITA")


def make_map(points: pd.DataFrame, clusters: pd.DataFrame, shapes: dict | None,
             show_points: bool, show_heat: bool, show_clusters: bool, mode: str):
    m = folium.Map(location=[-0.1, 114.2], zoom_start=6, tiles="CartoDB positron", control_scale=True,
                   prefer_canvas=True)
    folium.TileLayer("OpenStreetMap", name="OSM basemap", show=False).add_to(m)
    if shapes is not None:
        geo = to_feature_collection(shapes)
        folium.GeoJson(geo, name="Batas provinsi (geoBoundaries)",
                       style_function=lambda _: {"color": "#54738a", "weight": 1.4,
                                                 "fillColor": "#E2EDF6", "fillOpacity": 0.06},
                       tooltip=folium.GeoJsonTooltip(fields=["name"], aliases=["Provinsi"])).add_to(m)
    if show_heat and not points.empty:
        heat_layer = folium.FeatureGroup(name="Kepadatan rekaman deteksi (indikatif)", show=True)
        HeatMap(points[["latitude", "longitude"]].values.tolist(), radius=14, blur=15,
                min_opacity=.35).add_to(heat_layer)
        heat_layer.add_to(m)
    rendered = min(len(points), 2400)
    if show_points and not points.empty:
        marker_layer = folium.FeatureGroup(name="Lokasi deteksi (dapat diklik)", show=True)
        if len(points) <= 2400:
            displayed = points
        else:
            latest = points.sort_values("acq_utc", ascending=False).head(700)
            rest = points.loc[~points.index.isin(latest.index)]
            other = rest.sample(n=min(1700, len(rest)), random_state=20261014)
            displayed = pd.concat([latest, other], ignore_index=True)
        colors = {"Tinggi": "#DC2626", "Nominal": "#F59E0B", "Rendah": "#0EA5E9", "Tidak tersedia": "#64748B"}
        for _, row in displayed.iterrows():
            col = colors.get(row["confidence_group"], "#64748B")
            frp = f"{row['frp']:.1f} MW" if pd.notna(row["frp"]) else "tidak tersedia"
            popup_html = ("<div style='width:220px;font-family:sans-serif;font-size:12px'>"
                          f"<strong>{html.escape(str(row.get('province', '-')))}</strong><hr style='margin:6px 0'>"
                          f"<b>Observasi:</b> {html.escape(status_local(row))}<br>"
                          f"<b>UTC:</b> {row['acq_utc'].strftime('%Y-%m-%d %H:%M')}<br>"
                          f"<b>Satelit:</b> {html.escape(str(row['source_name']))}<br>"
                          f"<b>Confidence:</b> {html.escape(str(row['confidence_group']))}<br>"
                          f"<b>FRP:</b> {frp}<br>"
                          f"<b>Koordinat:</b> {row['latitude']:.5f}, {row['longitude']:.5f}<br>"
                          f"<small>{'DEMO SINTETIS' if mode == 'DEMO_SINTETIS' else 'Anomali termal (belum terverifikasi)'}</small></div>")
            folium.CircleMarker(location=[row.latitude, row.longitude], radius=4.8,
                                color=col, weight=1, fill=True, fill_color=col,
                                fill_opacity=.83, popup=folium.Popup(popup_html, max_width=260)).add_to(marker_layer)
        marker_layer.add_to(m)
    if show_clusters and not clusters.empty:
        cluster_layer = folium.FeatureGroup(name="Klaster spasial DBSCAN — eksploratif", show=True)
        for _, cl in clusters.head(600).iterrows():
            popup_txt = (f"<b>Klaster {cl.cluster_id}</b><br>"
                         f"Provinsi: {html.escape(str(cl.province))}<br>"
                         f"Rekaman: {int(cl.detections)}<br>"
                         f"Hari pengamatan (UTC): {int(cl.days_utc)}<br>"
                         f"Sensor: {int(cl.sensors)}<br>"
                         f"Deteksi high: {int(cl.high_confidence_records)}<br>"
                         "Bukan perimeter kebakaran atau prioritas darurat terverifikasi")
            folium.CircleMarker(location=[cl.latitude, cl.longitude],
                                radius=min(25, 7 + math.sqrt(float(cl.detections))),
                                color="#7C3AED", weight=2, fill=True, fill_color="#7C3AED",
                                fill_opacity=.22, popup=folium.Popup(popup_txt, max_width=270)).add_to(cluster_layer)
        cluster_layer.add_to(m)
    Fullscreen(position="topright").add_to(m)
    MiniMap(toggle_display=True, position="bottomright").add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)
    if mode == "DEMO_SINTETIS":
        # Persistent watermark in the self-contained HTML export, not only Streamlit.
        m.get_root().html.add_child(folium.Element(
            '<div style="position:fixed;top:10px;left:50%;transform:translateX(-50%);'
            'z-index:99999;background:#991b1b;color:white;padding:9px 15px;'
            'border-radius:8px;font:700 13px sans-serif;box-shadow:0 3px 14px #0005">'
            'DATA SINTETIS · DEMO SAJA · BUKAN HOTSPOT AKTUAL</div>'))
    else:
        m.get_root().html.add_child(folium.Element(
            '<div style="position:fixed;bottom:12px;left:50%;transform:translateX(-50%);'
            'z-index:99999;background:#ffffffed;color:#334155;padding:7px 12px;'
            'border:1px solid #e2e8f0;border-radius:7px;font:600 11px sans-serif">'
            'NASA FIRMS thermal anomalies · belum diverifikasi lapangan</div>'))
    m.fit_bounds([[-4.55, 108], [4.35, 119.5]])
    return m, rendered


def create_zip(points: pd.DataFrame, clusters: pd.DataFrame, meta: dict, html_map: str) -> bytes:
    csv_buf = io.StringIO()
    points.to_csv(csv_buf, index=False)
    clusters_buf = io.StringIO()
    clusters.to_csv(clusters_buf, index=False)
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        prefix = "DEMO_SINTETIS_" if meta["mode"] == "DEMO_SINTETIS" else ""
        z.writestr(prefix + "detections_filtered.csv", csv_buf.getvalue())
        z.writestr(prefix + "detections_filtered.geojson", detections_geojson(points, meta["mode"]))
        z.writestr(prefix + "clusters_exploratory.csv", clusters_buf.getvalue())
        z.writestr(prefix + "interactive_map.html", html_map)
        z.writestr(prefix + "manifest.json", json.dumps(meta, ensure_ascii=False, indent=2))
        z.writestr("LIMITATIONS_README.txt",
                   "NASA FIRMS active fire points are satellite-detected thermal anomalies, NOT confirmed fires.\n"
                   "Multiple detections can refer to a single physical incident.\n"
                   "DBSCAN clusters are exploratory groupings; not burned-area outlines or hazard predictions.\n"
                   "Clouds, revisit frequency, and sensor geometry can produce missed detections.\n"
                   "Burned area cannot be estimated by multiplying nominal pixel size by detections.\n"
                   "Data status is in manifest.json. Time of acquisition is UTC.\n"
                   "Do NOT use as sole basis for life/property-critical field operations.\n"
                   "Credits: NASA FIRMS; geoBoundaries gbOpen (CC BY 4.0); map tiles per providers.\n")
    return blob.getvalue()


