"""Lean NASA FIRMS Area API ingestion for Streamlit (no scientific ranking).

Never log URLs because they contain NASA MAP_KEY. Calendar extraction uses 8 UTC
calendar dates to cover a full rolling 7*24h window.
"""
from __future__ import annotations
import io
import json
from datetime import date,datetime,timedelta,timezone
from typing import Any
import numpy as np
import pandas as pd
import requests
from shapely import intersects_xy
from shapely.geometry import shape
from shapely.ops import unary_union

API='https://firms.modaps.eosdis.nasa.gov/api/area/csv'
BBOX='108,-5.1,119.3,4.6'
SOURCES={
    'S-NPP':'VIIRS_SNPP_NRT',
    'NOAA-20':'VIIRS_NOAA20_NRT',
    'NOAA-21':'VIIRS_NOAA21_NRT',
    'MODIS Terra/Aqua':'MODIS_NRT',
}
PROVINCES=['Kalimantan Barat','Kalimantan Tengah','Kalimantan Selatan',
           'Kalimantan Timur','Kalimantan Utara']
PROV_EN={'West Kalimantan':'Kalimantan Barat','Central Kalimantan':'Kalimantan Tengah',
         'South Kalimantan':'Kalimantan Selatan','East Kalimantan':'Kalimantan Timur',
         'North Kalimantan':'Kalimantan Utara'}
META_URL='https://www.geoboundaries.org/api/current/gbOpen/IDN/ADM1/'

class DataUnavailable(Exception):
    pass


def fetch_nasa(key: str, sensor_names: tuple[str,...],end_date: date):
    key=(key or '').strip()
    if not key or not all(x in SOURCES for x in sensor_names):
        raise DataUnavailable('Konfigurasi MAP_KEY atau sensor tidak lengkap.')
    start=end_date-timedelta(days=7)
    dates=[(start,5),(start+timedelta(days=5),3)]
    frames=[];warnings=[];empty_responses=0
    for name in sensor_names:
        for dt,n in dates:
            # NASA API permits 1..5 days per request; split into 5 + 3 days.
            url=f'{API}/{key}/{SOURCES[name]}/{BBOX}/{n}/{dt.isoformat()}'
            try:
                r=requests.get(url,timeout=(10,50),headers={'User-Agent':'KalimantanFireWatch/1.4 research'})
                r.raise_for_status()
                msg=r.text.strip()
                if not msg or msg.lower().startswith(('no data available','no fires detected','no records found')):
                    empty_responses+=1
                    continue
                if msg.lower().startswith(('invalid','error','transaction limit','<!doctype','<html')):
                    raise DataUnavailable('Respons API tidak valid, periksa kuota atau sumber.')
                d=pd.read_csv(io.StringIO(msg),dtype={'acq_date':str,'acq_time':str},low_memory=False)
                d.columns=d.columns.str.lower().str.strip()
                if not {'latitude','longitude','acq_date','acq_time'}.issubset(d.columns):
                    raise DataUnavailable('Skema CSV tidak sesuai.')
                d['source_name']=name
                frames.append(d)
            except (requests.RequestException,DataUnavailable,ValueError,pd.errors.ParserError):
                # Never include server response URL, which contains MAP_KEY.
                warnings.append(f'{name} {dt.isoformat()}: respons tidak tersedia')
    if not frames:
        if empty_responses:
            return pd.DataFrame(columns=['latitude','longitude','acq_date','acq_time','source_name','confidence','frp']),warnings
        raise DataUnavailable('Tidak ada data valid dari NASA FIRMS. Periksa MAP_KEY, kuota, dan akses jaringan.')
    return pd.concat(frames,ignore_index=True),warnings


def normalize_nasa(raw:pd.DataFrame,now:datetime) -> pd.DataFrame:
    d=raw.copy()
    d['latitude']=pd.to_numeric(d['latitude'],errors='coerce')
    d['longitude']=pd.to_numeric(d['longitude'],errors='coerce')
    d['frp']=pd.to_numeric(d.get('frp',pd.Series(np.nan,index=d.index)),errors='coerce')
    d.loc[d.frp<0,'frp']=np.nan
    hhmm=d.acq_time.astype(str).str.strip().str.replace(r'\.0$','',regex=True).str.zfill(4)
    d['acq_utc']=pd.to_datetime(d.acq_date.astype(str).str.strip()+' '+hhmm,
          format='%Y-%m-%d %H%M',utc=True,errors='coerce')
    d=d.loc[d.latitude.between(-90,90) & d.longitude.between(-180,180) & d.acq_utc.notna()].copy()
    source=d.source_name.astype(str)
    modis=source.str.contains('MODIS',case=False)
    conf=d.get('confidence',pd.Series('',index=d.index)).astype(str).str.strip().str.lower()
    d['confidence_group']=np.select([modis,conf.isin(['h','high']),conf.isin(['n','nominal']),conf.isin(['l','low'])],
        ['MODIS numerik','Tinggi','Nominal','Rendah'],default='Tidak tersedia')
    d['confidence_raw']=conf
    d['source_family']=np.where(modis,'MODIS','VIIRS')
    d=d.drop_duplicates(subset=['latitude','longitude','acq_utc','source_name','frp','confidence_raw']).copy()
    d['age_h']=(pd.Timestamp(now)-d.acq_utc).dt.total_seconds()/3600
    d=d.loc[(d.age_h>=-1)&(d.age_h<=168)].copy()
    return d.sort_values('acq_utc',ascending=False).reset_index(drop=True)


def parse_provinces_fc(fc:dict) -> dict:
    found={}
    for feature in fc.get('features',[]):
        props=feature.get('properties',{})
        name=next((props[k] for k in ['shapeName','ADM1_NAME','NAME_1','name','provinsi']
          if props.get(k)),None)
        name=PROV_EN.get(name,name)
        if name not in PROVINCES: continue
        try:
            geometry=shape(feature['geometry'])
            if not geometry.is_valid:
                import shapely
                geometry=shapely.make_valid(geometry)
            if geometry.is_empty: continue
        except (KeyError,TypeError,ValueError):continue
        if name in found: found[name]=unary_union([found[name],geometry])
        else: found[name]=geometry
    if set(found)!=set(PROVINCES):
        raise DataUnavailable('Batas administrasi tidak mencakup lima provinsi Kalimantan.')
    return found


def fetch_provinces():
    try:
        resp=requests.get(META_URL,timeout=(10,30))
        resp.raise_for_status()
        meta=resp.json()
        url=meta.get('simplifiedGeometryGeoJSON') or meta.get('gjDownloadURL')
        if not isinstance(url,str) or not url.startswith('https://'):
            raise ValueError('Missing geojson url')
        data=requests.get(url,timeout=(10,45))
        data.raise_for_status()
        return parse_provinces_fc(data.json())
    except Exception:
        raise DataUnavailable('Layanan batas provinsi eksternal tidak dapat diakses.') from None


def local_province_backup(path):
    from pathlib import Path
    return parse_provinces_fc(json.loads(Path(path).read_text(encoding='utf8')))


def assign_provinces(df:pd.DataFrame,provinces:dict) -> pd.DataFrame:
    d=df.copy()
    d['province']=''
    xs=d.longitude.to_numpy(dtype=float)
    ys=d.latitude.to_numpy(dtype=float)
    for prov in PROVINCES:
        cover=intersects_xy(provinces[prov],xs,ys)
        d.loc[cover,'province']=prov
    return d[d.province.ne('')].copy().reset_index(drop=True)
