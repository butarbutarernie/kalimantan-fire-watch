"""Accurate village lookup for NASA FIRMS point centroids.

Loads user-provided original 2D village polygon geometry, split by province and
compressed in NumPy NPZ archives (allow_pickle=False). Geometry is NOT
simplified for lookup. Boundary overlaps are explicitly labelled ambiguous.
"""
from __future__ import annotations
from functools import lru_cache
from pathlib import Path
import gzip
import json
import numpy as np
import pandas as pd
import shapely
from shapely.strtree import STRtree


@lru_cache(maxsize=8)
def _load_partition(path_str: str):
    path=Path(path_str)
    if not path.is_file():
        raise FileNotFoundError(path.name)
    with np.load(path,allow_pickle=False) as dat:
        byte_blob=dat['geometry_bytes'].tobytes()
        offsets=dat['geometry_offsets'].copy()
        labels={k:dat[k].astype(str) for k in ['desa','kecamatan','kabupaten','provinsi','kode_pum']}
    if len(offsets)!=len(labels['desa'])+1:
        raise ValueError('Village archive geometry offsets incorrect')
    wbks=[byte_blob[int(offsets[i]):int(offsets[i+1])] for i in range(len(offsets)-1)]
    geom=shapely.from_wkb(wbks)
    if (~shapely.is_valid(geom)).any() or shapely.is_empty(geom).any():
        raise ValueError('Archive contains invalid geometry')
    return STRtree(geom), labels


def _load_manifest(path: Path) -> dict:
    with path.open(encoding='utf8') as f:
        manifest=json.load(f)
    return {val['province']: path.parent / val['filename'] for _,val in manifest.items()}


def annotate_villages(df: pd.DataFrame, data_dir: str | Path) -> pd.DataFrame:
    """Find administrative polygon(s) containing the center of each FIRMS pixel.

    'multiple' never silently chooses one of two overlapping village boundaries.
    'outside' means this SHAPEFILE does not cover the point, not that there is
    no administratively recognized village at the location.
    """
    data=df.copy()
    for col in ['desa','kecamatan','kabupaten_desa','desa_status','desa_kode_pum']:
        data[col]=''
    if data.empty: return data
    required={'latitude','longitude','province'}
    if not required.issubset(data):
        raise ValueError('Missing latitude, longitude or province')
    data['desa_status']='Tidak tercakup dalam shapefile'
    data_dir=Path(data_dir)
    manifest=_load_manifest(data_dir/'villages_lookup_manifest.json')
    lat=pd.to_numeric(data.latitude,errors='coerce')
    lon=pd.to_numeric(data.longitude,errors='coerce')
    for prov,group in data.groupby('province',sort=False):
        archive=manifest.get(str(prov))
        if archive is None or not archive.is_file():
            data.loc[group.index,'desa_status']='Data wilayah tidak tersedia'
            continue
        tree,labels=_load_partition(str(archive.resolve()))
        valid=np.isfinite(lat.loc[group.index]) & np.isfinite(lon.loc[group.index])
        group_indices=group.index.to_numpy()[valid.to_numpy()]
        if not len(group_indices): continue
        pts=shapely.points(lon.loc[group_indices].to_numpy(dtype=float),
                           lat.loc[group_indices].to_numpy(dtype=float))
        pairs=tree.query(pts,predicate='intersects')
        if pairs.shape[1]==0: continue
        # Keep only unambiguous single matches. Assign status 'ambiguous' for
        # coincident/overlapping polygons; precise village cannot be inferred.
        loc=pairs[0].astype(int)
        pol=pairs[1].astype(int)
        counts=np.bincount(loc,minlength=len(group_indices))
        matched_loc=loc[counts[loc]==1]
        matched_poly=pol[counts[loc]==1]
        global_idx=group_indices[matched_loc]
        for col,source in [('desa','desa'),('kecamatan','kecamatan'),
                           ('kabupaten_desa','kabupaten'),('desa_kode_pum','kode_pum')]:
            data.loc[global_idx,col]=labels[source][matched_poly]
        data.loc[global_idx,'desa_status']='Ditemukan pada poligon desa'
        ambiguous=group_indices[np.flatnonzero(counts>1)]
        if len(ambiguous): data.loc[ambiguous,'desa_status']='Ambigu: poligon beririsan'
    return data


def lookup_coordinate(lon: float,lat: float,provinsi: str, data_dir: str | Path) -> dict:
    df=pd.DataFrame({'longitude':[lon],'latitude':[lat],'province':[provinsi]})
    return annotate_villages(df,data_dir).iloc[0].to_dict()


def load_visual_geojson(data_dir: str | Path, provinces=None):
    path=Path(data_dir)/'villages_visual_130m.geojson.gz'
    with gzip.open(path,'rt',encoding='utf8') as fh: collection=json.load(fh)
    if provinces is not None:
        wanted=set(provinces)
        collection['features']=[f for f in collection['features']
                 if f.get('properties',{}).get('provinsi') in wanted]
    return collection
