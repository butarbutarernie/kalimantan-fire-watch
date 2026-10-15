"""Optional land-context layers, with explicit provenance and non-inference limits.

A soil-map unit overlay is NOT evidence of an ongoing peat fire, and the
WorldCover WMS is visualization ONLY; quantitative classes require raster data.
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point
from shapely.strtree import STRtree

ESA_WMS_URL = "https://titiler.terrascope.be/wms"
ESA_WMS_LAYER = "WORLDCOVER_2021_MAP"
ESA_ATTRIBUTION = (
    "© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel "
    "data (2021) processed by ESA WorldCover consortium (CC BY 4.0)"
)
ESA_CLASSES = {
    10: 'Tutupan pohon', 20: 'Semak belukar', 30: 'Padang rumput',
    40: 'Lahan pertanian', 50: 'Area terbangun', 60: 'Vegetasi jarang/tanah terbuka',
    70: 'Salju/es', 80: 'Perairan permanen', 90: 'Lahan basah herba',
    95: 'Mangrove', 100: 'Lumut/lichen',
}


class ContextError(ValueError):
    """User-facing invalid geospatial context message."""


def load_peat_units(blob: bytes, filename: str) -> gpd.GeoDataFrame:
    """Read a private user's GPKG or GeoJSON, normalize CRS; never publish it.

    Geometry source and licensing are user managed; no cloud copy is created.
    """
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.gpkg', '.geojson', '.json'}:
        raise ContextError('Pilih GeoPackage (.gpkg) atau GeoJSON (.geojson).')
    if not blob or len(blob) > 100_000_000:
        raise ContextError('File gambut kosong atau melebihi batas 100 MB.')
    try:
        if suffix == '.gpkg':
            with tempfile.TemporaryDirectory(prefix='peat_upload_') as td:
                path = Path(td) / 'peat_units.gpkg'
                path.write_bytes(blob)
                gdf = gpd.read_file(path)
        else:
            gdf = gpd.read_file(io.BytesIO(blob))
    except Exception as exc:
        raise ContextError('File gambut gagal dibaca sebagai data vektor GIS.') from exc
    if gdf.empty or 'geometry' not in gdf or gdf.crs is None:
        raise ContextError('Layer gambut tidak memiliki geometri atau CRS yang jelas.')
    gdf = gdf[gdf.geometry.notna() & (~gdf.geometry.is_empty)].copy()
    if gdf.empty or not gdf.geom_type.isin(['Polygon', 'MultiPolygon']).all():
        raise ContextError('Peta gambut harus seluruhnya bergeometri Polygon/MultiPolygon.')
    if not gdf.is_valid.all():
        raise ContextError('Ada geometri gambut tidak valid. Perbaiki di QGIS sebelum overlay.')
    if 'SPT' not in gdf.columns:
        raise ContextError('Dataset tidak memiliki kolom SPT; gunakan hasil konversi peta gambut yang sudah diaudit.')
    if 'LANDFORM' not in gdf.columns:
        raise ContextError('Dataset tidak memiliki kolom LANDFORM, sehingga kelas gambut tidak dapat ditafsirkan.')
    try:
        gdf = gdf.to_crs(epsg=4326)
    except Exception as exc:
        raise ContextError('CRS layer gambut tidak dapat ditransformasi ke WGS84.') from exc
    return gdf.reset_index(drop=True)


def annotate_peat_intersections(points: pd.DataFrame, peat_units: gpd.GeoDataFrame) -> pd.DataFrame:
    """Exact centroid-to-soil-unit intersection in EPSG:4326 (boundary inclusive).

    Preserve all observations and assign explicit UNKNOWN if no source was loaded.
    Different soil polygons should be non-overlapping. Multiple matches are flagged.
    """
    cols = ['peat_unit_status', 'peat_spt', 'peat_landform', 'peat_thickness_class',
            'peat_min_cm', 'peat_max_exclusive_cm', 'peat_multiple_units']
    data = points.copy()
    for k in cols:
        data[k] = pd.NA
    if len(points) == 0:
        return data
    geom = list(peat_units.geometry)
    pt = [Point(float(lon), float(lat)) for lon, lat in zip(points.longitude, points.latitude)]
    tree = STRtree(geom)
    candidate = tree.query(np.asarray(pt, dtype=object), predicate='intersects')
    # Strict polygon-extent test only: this is not proof that organic soil burns.
    data['peat_unit_status'] = 'Di luar satuan peta'
    data['peat_multiple_units'] = False
    pairs = pd.DataFrame({'record_idx': candidate[0].astype(int),
                          'unit_idx': candidate[1].astype(int)})
    if not pairs.empty:
        multiple = pairs.record_idx.value_counts()
        idx_multi = multiple.index[multiple > 1].tolist()
        if idx_multi:
            data.iloc[idx_multi, data.columns.get_loc('peat_multiple_units')] = True
        first = pairs.sort_values(['record_idx', 'unit_idx']).drop_duplicates('record_idx')
        ridx = first.record_idx.to_numpy(dtype=int)
        matched = peat_units.iloc[first.unit_idx.to_numpy(dtype=int)]
        data.iloc[ridx, data.columns.get_loc('peat_unit_status')] = 'Di dalam satuan peta'
        source_columns = {
            'peat_spt': 'SPT', 'peat_landform': 'LANDFORM',
            'peat_thickness_class': 'kelas_ketebalan',
            'peat_min_cm': 'ketebalan_min_cm',
            'peat_max_exclusive_cm': 'ketebalan_maks_eksklusif_cm',
        }
        for dest, source in source_columns.items():
            if source in matched.columns:
                data.iloc[ridx, data.columns.get_loc(dest)] = matched[source].to_numpy()
    return data


def simplified_peat_geojson(peat_units: gpd.GeoDataFrame, tolerance_m: float = 130.0) -> dict:
    """Convert privately provided polygon layer to a simplified MAP-ONLY GeoJSON."""
    mini = peat_units.to_crs(epsg=6933).copy()
    mini['geometry'] = mini.geometry.simplify(tolerance_m, preserve_topology=True)
    mini = mini.to_crs(epsg=4326)
    columns = [c for c in ['SPT', 'LANDFORM', 'kelas_ketebalan', 'geometry'] if c in mini]
    return json.loads(mini[columns].to_json(drop_id=True))


def attach_worldcover_class(points: pd.DataFrame, frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Only attach raster-sampled ESA class when an explicit exact record ID matches.

    A public WMS color/PNG must never be used as source of class statistics.
    """
    if 'record_id' not in points or not {'record_id','worldcover_code'}.issubset(frame.columns):
        raise ContextError('WorldCover CSV wajib memiliki record_id dan worldcover_code dari ekspor GEE.')
    lookup = frame[['record_id','worldcover_code']].copy()
    lookup['record_id'] = lookup['record_id'].astype(str)
    lookup['worldcover_code'] = pd.to_numeric(lookup['worldcover_code'], errors='coerce')
    lookup = lookup.dropna(subset=['record_id']).drop_duplicates('record_id', keep='first')
    lookup.loc[~lookup.worldcover_code.isin(ESA_CLASSES), 'worldcover_code'] = np.nan
    old = points.drop(columns=['worldcover_code','worldcover_name'], errors='ignore')
    result = old.merge(lookup, on='record_id', how='left', validate='many_to_one')
    result['worldcover_name'] = result.worldcover_code.map(ESA_CLASSES)
    matched = int(result.worldcover_code.notna().sum())
    return result, {'match_count':matched,'total':len(points),
                    'unmatched':len(points)-matched,
                    'source':'Uploaded GEE raster sampling CSV; ESA WorldCover 2021 v200'}
