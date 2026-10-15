"""Reproducible 1km FIRMS aggregation (not unique fire incident counts)."""
from __future__ import annotations
import io
import re
import numpy as np
import pandas as pd
from pyproj import Transformer

PROVINCES = ['Kalimantan Barat','Kalimantan Tengah','Kalimantan Selatan','Kalimantan Timur','Kalimantan Utara']
ABBR = {'Kalimantan Barat':'KB','Kalimantan Tengah':'KT','Kalimantan Selatan':'KS','Kalimantan Timur':'KI','Kalimantan Utara':'KU'}
CLASS_LABELS = {'tree_cover':'Tutupan pohon','shrubland':'Semak belukar','grassland':'Padang rumput',
                'cropland':'Lahan pertanian','built_up':'Area terbangun','bare_sparse':'Tanah terbuka',
                'snow_ice':'Salju / es','permanent_water':'Perairan permanen','herbaceous_wetland':'Lahan basah herba',
                'mangroves':'Mangrove','moss_lichen':'Lumut / lichen'}
EARTH = Transformer.from_crs('EPSG:4326','EPSG:6933',always_xy=True)
WGS84 = Transformer.from_crs('EPSG:6933','EPSG:4326',always_xy=True)
PATTERN = re.compile(r'^(KB|KT|KS|KI|KU)_(-?\d+)_(-?\d+)_1000$')


def add_grid_id(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy().reset_index(drop=True)
    if data.empty:
        data['grid_id'] = pd.Series(dtype='str')
        return data
    if not {'latitude','longitude','province'}.issubset(data):
        raise ValueError('Latitude, longitude, province must be available.')
    if data['province'].map(ABBR).isna().any():
        raise ValueError('Unknown province labels; check ADM1 geospatial filtering.')
    lon = pd.to_numeric(data.longitude, errors='coerce').to_numpy()
    lat = pd.to_numeric(data.latitude, errors='coerce').to_numpy()
    if not np.isfinite(lon).all() or not np.isfinite(lat).all():
        raise ValueError('Coordinates contain missing or invalid values.')
    x, y = EARTH.transform(lon,lat)
    xx = np.floor(np.asarray(x)/1000).astype(np.int64)
    yy = np.floor(np.asarray(y)/1000).astype(np.int64)
    data['grid_id'] = data.province.map(ABBR).astype(str) + '_' + pd.Series(xx).astype(str) + '_' + pd.Series(yy).astype(str) + '_1000'
    return data


def aggregate_1km(df: pd.DataFrame, as_of_utc: str | pd.Timestamp) -> pd.DataFrame:
    data = add_grid_id(df)
    if data.empty:
        return pd.DataFrame()
    data['acq_utc'] = pd.to_datetime(data.acq_utc, utc=True, errors='coerce')
    if data.acq_utc.isna().any():
        raise ValueError('Some timestamps cannot be parsed as UTC.')
    data['utc_day'] = data.acq_utc.dt.date
    data['high_flag'] = ((data.source_family=='VIIRS') & (data.confidence_group=='Tinggi')).astype(int)
    data['peat_flag'] = data.get('peat_flag',pd.Series(pd.NA,index=data.index))
    has_peat = data['peat_flag'].notna().all()
    data['peat_flag'] = data['peat_flag'].eq(True).astype(bool)
    data['modis_flag'] = (data.source_family=='MODIS').astype(int)
    data['viirs_name'] = data.source_name.where(data.source_family.eq('VIIRS'), pd.NA)
    group = data.groupby(['grid_id','province'],as_index=False).agg(
        n_detections=('grid_id','size'),n_days=('utc_day','nunique'),
        n_sensors=('source_name','nunique'),n_viirs_sensors=('viirs_name','nunique'),n_high=('high_flag','sum'),
        n_modis=('modis_flag','sum'),n_peat_detections=('peat_flag','sum'),
        last_seen_utc=('acq_utc','max'),first_seen_utc=('acq_utc','min'),
        frp_max_mw=('frp','max'),frp_median_mw=('frp','median'))
    comps = group.grid_id.str.extract(r'^[A-Z]{2}_(-?\d+)_(-?\d+)_1000$')
    xx = comps[0].astype(int).to_numpy()
    yy = comps[1].astype(int).to_numpy()
    longitude,latitude = WGS84.transform((xx+0.5)*1000,(yy+0.5)*1000)
    group['grid_ix'] = xx
    group['grid_iy'] = yy
    group['center_lat'] = np.around(latitude,6)
    group['center_lon'] = np.around(longitude,6)
    group['peat_detect_share_pct'] = (group.n_peat_detections / group.n_detections * 100).round(2) if has_peat else np.nan
    if not has_peat:
        group['n_peat_detections'] = np.nan
    reference = pd.to_datetime(as_of_utc, utc=True)
    recent = group.last_seen_utc >= reference-pd.Timedelta(hours=72)
    group['candidate'] = (group.n_days >= 3) & (group.n_viirs_sensors >= 2) & (group.n_high >= 1) & recent
    for datecol in ['first_seen_utc','last_seen_utc']:
        group[datecol] = group[datecol].dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    return group.sort_values(['candidate','n_days','n_high','n_detections'],ascending=[False,False,False,False]).reset_index(drop=True)


def attach_worldcover(grid: pd.DataFrame, wc: pd.DataFrame) -> tuple[pd.DataFrame,int]:
    if 'grid_id' not in wc or not wc.grid_id.is_unique:
        raise ValueError('WorldCover table requires unique grid_id values.')
    mandatory=['grid_id','wc_run_status','wc_coverage_pct','wc_dominant_class','wc_dominant_pct_valid']
    if not set(mandatory).issubset(wc):
        raise ValueError('WorldCover CSV must contain audited class and coverage columns.')
    cols=[c for c in wc.columns if c.startswith('wc_')] + ['grid_id']
    out=grid.drop(columns=[c for c in grid.columns if c.startswith('wc_')],errors='ignore')
    out=out.merge(wc[cols],on='grid_id',how='left',validate='one_to_one')
    out['wc_available']=(out.wc_run_status=='ok') & pd.to_numeric(out.wc_coverage_pct,errors='coerce').ge(90)
    out.loc[~out.wc_available,'wc_dominant_class']=np.nan
    return out,int(out.wc_available.sum())


def candidate_geojson(grid: pd.DataFrame, limit:int=600) -> dict:
    from shapely.geometry import box,mapping
    from shapely.ops import transform
    features=[]
    for _,r in grid.head(limit).iterrows():
        m=PATTERN.fullmatch(str(r.grid_id))
        if not m: continue
        ix,iy=int(m.group(2)),int(m.group(3))
        geom=transform(WGS84.transform,box(ix*1000,iy*1000,(ix+1)*1000,(iy+1)*1000))
        props={'grid_id':str(r.grid_id),'province':str(r.province),
               'detections':int(r.n_detections),'days':int(r.n_days),
               'high_confidence':int(r.n_high),
               'peat_centroid_records':None if pd.isna(r.get('n_peat_detections')) else int(r.n_peat_detections),
               'wc_2021':str(r.wc_dominant_class) if pd.notna(r.get('wc_dominant_class')) else 'Not yet processed'}
        features.append({'type':'Feature','properties':props,'geometry':mapping(geom)})
    return {'type':'FeatureCollection','features':features}


def read_worldcover_csv(blob: bytes) -> pd.DataFrame:
    d = pd.read_csv(io.BytesIO(blob))
    d.columns=d.columns.str.strip()
    if d.grid_id.duplicated().any() or ~d.grid_id.map(lambda a: bool(PATTERN.fullmatch(str(a)))).all():
        raise ValueError('WorldCover CSV grid IDs do not match 1km EPSG:6933 keys.')
    return d
