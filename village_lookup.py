"""Precise administrative-village lookup using NAMOBJ names for NASA FIRMS pixel centres.

The five user-provided source polygon partitions are retained without display
simplification. They are stored as compressed NumPy archives under ``data/``.
The lookup never assumes that a missing archive means an unlabeled village.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import gzip
import json
import logging

import numpy as np
import pandas as pd
import shapely
from shapely.strtree import STRtree

_LOG = logging.getLogger(__name__)

# Fixed, auditable mapping: an omitted/old manifest cannot prevent detection.
VILLAGE_ARCHIVES = {
    'Kalimantan Barat': 'villages_lookup_kalbar.npz',
    'Kalimantan Tengah': 'villages_lookup_kalteng.npz',
    'Kalimantan Selatan': 'villages_lookup_kalsel.npz',
    'Kalimantan Timur': 'villages_lookup_kaltim.npz',
    'Kalimantan Utara': 'villages_lookup_kaltara.npz',
}
VILLAGE_COLUMNS = ('desa', 'kecamatan', 'kabupaten_desa',
                   'desa_status', 'desa_kode_pum')


def inspect_village_assets(data_dir: str | Path) -> dict:
    """File-level installation audit; avoids expensive geometry decompression.

    An archive is only *available*, not proven readable, until loaded. The
    data folder and file names are case-sensitive on Streamlit Cloud/Linux.
    """
    root = Path(data_dir)
    found, missing = [], []
    for province, filename in VILLAGE_ARCHIVES.items():
        path = root / filename
        if path.is_file() and path.stat().st_size > 1024:
            found.append(province)
        else:
            missing.append(filename)
    return {
        'found_count': len(found),
        'expected_count': len(VILLAGE_ARCHIVES),
        'available_provinces': found,
        'missing_files': missing,
        'ready_for_all_provinces': not missing,
    }


@lru_cache(maxsize=5)
def _load_partition(path_str: str):
    """Load original WKB polygons for a single province, once per worker."""
    path = Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(path.name)
    with np.load(path, allow_pickle=False) as dat:
        data = dat['geometry_bytes'].tobytes()
        offsets = dat['geometry_offsets'].copy()
        labels = {k: dat[k].astype(str)
                  for k in ('desa', 'kecamatan', 'kabupaten', 'provinsi', 'kode_pum')}
    if len(offsets) != len(labels['desa']) + 1:
        raise ValueError('Archive offsets do not match village counts')
    if offsets[0] != 0 or offsets[-1] != len(data) or np.any(np.diff(offsets) <= 0):
        raise ValueError('Village geometry archive has invalid WKB offsets')
    geoms = shapely.from_wkb([data[int(a):int(b)]
                              for a, b in zip(offsets[:-1], offsets[1:])])
    if bool(np.any(~shapely.is_valid(geoms))) or bool(np.any(shapely.is_empty(geoms))):
        raise ValueError('Village archive includes invalid/empty geometry')
    return STRtree(geoms), labels


def annotate_villages(df: pd.DataFrame, data_dir: str | Path) -> pd.DataFrame:
    """Attach desa/kecamatan/kabupaten by point-in-polygon, per province.

    Missing/corrupt assets affect only their own province; hotspots from other
    provinces are still annotated. Multiple intersections are left ambiguous.
    There is no inference of a confirmed wildfire or of impacted villages.
    """
    data = df.copy().reset_index(drop=True)
    for column in VILLAGE_COLUMNS:
        data[column] = ''
    if data.empty:
        return data
    required = {'latitude', 'longitude', 'province'}
    if not required.issubset(data.columns):
        raise ValueError('Missing latitude, longitude, or province columns')
    data['desa_status'] = 'Tidak tercakup dalam shapefile'
    root = Path(data_dir)
    lat = pd.to_numeric(data.latitude, errors='coerce')
    lon = pd.to_numeric(data.longitude, errors='coerce')

    for province, group in data.groupby('province', sort=False):
        archive_name = VILLAGE_ARCHIVES.get(str(province))
        if not archive_name:
            data.loc[group.index, 'desa_status'] = 'Nama provinsi belum cocok dengan referensi desa'
            continue
        path = root / archive_name
        if not path.is_file():
            data.loc[group.index, 'desa_status'] = 'Arsip batas desa belum diunggah'
            continue
        try:
            tree, labels = _load_partition(str(path.resolve()))
        except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
            _LOG.warning('Village partition unreadable: province=%s error=%s',
                         province, type(exc).__name__)
            data.loc[group.index, 'desa_status'] = 'Arsip batas desa gagal dibaca'
            continue

        is_valid = np.isfinite(lat.loc[group.index]) & np.isfinite(lon.loc[group.index])
        indices = group.index.to_numpy()[is_valid.to_numpy()]
        if not len(indices):
            data.loc[group.index, 'desa_status'] = 'Koordinat deteksi tidak valid'
            continue
        try:
            pts = shapely.points(lon.loc[indices].to_numpy(dtype=float),
                                 lat.loc[indices].to_numpy(dtype=float))
            pairs = tree.query(pts, predicate='intersects')
            if pairs.shape[1] == 0:
                continue
            loc_idx = pairs[0].astype(int)
            polygon_idx = pairs[1].astype(int)
            counts = np.bincount(loc_idx, minlength=len(indices))
            unique = counts[loc_idx] == 1
            rows = indices[loc_idx[unique]]
            matching = polygon_idx[unique]
            for dest, src in [('desa', 'desa'), ('kecamatan', 'kecamatan'),
                              ('kabupaten_desa', 'kabupaten'),
                              ('desa_kode_pum', 'kode_pum')]:
                data.loc[rows, dest] = labels[src][matching]
            data.loc[rows, 'desa_status'] = 'Ditemukan pada poligon desa'
            # A matching polygon may have NAMOBJ='Area Tidak Terdefinisi'.
            # Do not present this placeholder as a confirmed village name.
            unnamed = np.asarray([not str(v).strip() for v in labels['desa'][matching]], dtype=bool)
            if unnamed.any():
                data.loc[rows[unnamed], 'desa_status'] = 'Nama desa belum terdefinisi (NAMOBJ)'
            conflicting = indices[np.flatnonzero(counts > 1)]
            if len(conflicting):
                data.loc[conflicting, 'desa_status'] = 'Ambigu: poligon desa beririsan'
        except (ValueError, IndexError, TypeError, RuntimeError) as exc:
            _LOG.warning('Village point lookup failed: province=%s error=%s',
                         province, type(exc).__name__)
            data.loc[group.index, 'desa_status'] = 'Pencocokan koordinat desa gagal'
    return data


def lookup_coordinate(lon: float, lat: float, provinsi: str,
                      data_dir: str | Path) -> dict:
    df = pd.DataFrame({'longitude':[lon], 'latitude':[lat], 'province':[provinsi]})
    return annotate_villages(df, data_dir).iloc[0].to_dict()


def load_visual_geojson(data_dir: str | Path, provinces=None):
    """Optional, simplified village lines for map drawing only."""
    path = Path(data_dir) / 'villages_visual_130m.geojson.gz'
    with gzip.open(path, 'rt', encoding='utf8') as fh:
        collection = json.load(fh)
    if provinces is not None:
        wanted = set(provinces)
        collection['features'] = [f for f in collection['features']
                                  if f.get('properties', {}).get('provinsi') in wanted]
    return collection
