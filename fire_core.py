"""Kalimantan Fire Watch — testable scientific data processing.

NASA FIRMS API detections are thermal anomalies, NOT incident counts or burned area.
No official emergency decision, spread forecast, or land-cover risk is derived here.
"""
from __future__ import annotations

import io
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import numpy as np
import pandas as pd
import requests
from shapely.geometry import Point, shape, mapping
from shapely.ops import unary_union
from shapely.prepared import prep
from sklearn.cluster import DBSCAN

FIRMS_ROOT = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
GEOBOUNDARIES_META = "https://www.geoboundaries.org/api/current/gbOpen/IDN/ADM1/"
BBOX_KALIMANTAN = "108,-4.6,119.4,4.5"  # preliminary download only; ADM1 polygons decide inclusion.
SATELLITE_SOURCES = {
    "S-NPP": "VIIRS_SNPP_NRT",
    "NOAA-20": "VIIRS_NOAA20_NRT",
    "NOAA-21": "VIIRS_NOAA21_NRT",
}
PROVINCES = (
    "Kalimantan Barat", "Kalimantan Tengah", "Kalimantan Selatan",
    "Kalimantan Timur", "Kalimantan Utara",
)
PROVINCE_EN = {
    "West Kalimantan": "Kalimantan Barat",
    "Central Kalimantan": "Kalimantan Tengah",
    "South Kalimantan": "Kalimantan Selatan",
    "East Kalimantan": "Kalimantan Timur",
    "North Kalimantan": "Kalimantan Utara",
    "West Borneo": "Kalimantan Barat",
    "Central Borneo": "Kalimantan Tengah",
    "South Borneo": "Kalimantan Selatan",
    "East Borneo": "Kalimantan Timur",
    "North Borneo": "Kalimantan Utara",
}
EXPECTED_COLUMNS = ("latitude", "longitude", "acq_date", "acq_time")


class SourceError(RuntimeError):
    """A user-safe error that never contains API key."""


def clean_key(raw: str) -> str:
    key = (raw or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,120}", key):
        raise SourceError("NASA MAP_KEY kosong atau berformat tidak dikenal. Ambil MAP_KEY gratis dari NASA FIRMS.")
    return key


def _http_get(url: str, *, timeout: tuple[int, int] = (10, 40), getter: Callable[..., Any] | None = None) -> Any:
    try:
        response = (getter or requests.get)(url, timeout=timeout, headers={"User-Agent": "Kalimantan-Fire-Watch/1.0 research situational dashboard"})
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        # Intentionally strip the URL and any response payload, which might contain secrets.
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status:
            raise SourceError(f"Sumber data tidak dapat diakses (HTTP {status}). Periksa koneksi/MAP_KEY dan coba lagi.") from None
        raise SourceError("Tidak dapat terhubung ke server data. Periksa koneksi atau coba lagi.") from None


def period_chunks(end_utc: date, calendar_days: int) -> list[tuple[date, int]]:
    """Split an inclusive calendar interval into FIRMS API requests of 1..5 days."""
    if not 1 <= calendar_days <= 10:
        raise ValueError("calendar_days harus antara 1 dan 10")
    start = end_utc - timedelta(days=calendar_days - 1)
    chunks = []
    current = start
    while current <= end_utc:
        n = min(5, (end_utc - current).days + 1)
        chunks.append((current, n))
        current += timedelta(days=n)
    return chunks


def _fetch_sensor_chunk(key: str, satellite: str, start: date, n: int,
                        getter: Callable[..., Any] | None = None) -> pd.DataFrame:
    if satellite not in SATELLITE_SOURCES:
        raise SourceError("Sensor yang diminta tidak dikenali.")
    source = SATELLITE_SOURCES[satellite]
    url = f"{FIRMS_ROOT}/{key}/{source}/{BBOX_KALIMANTAN}/{n}/{start.isoformat()}"
    response = _http_get(url, getter=getter)
    sample = (response.text or "").strip()
    if not sample or sample.lower().startswith(("no data available", "no fires detected", "no records found")):
        return pd.DataFrame(columns=EXPECTED_COLUMNS)
    if sample.lstrip().lower().startswith(("invalid", "error", "request rejected", "transaction limit", "<!doctype", "<html")):
        raise SourceError(f"NASA FIRMS mengembalikan respons bukan CSV untuk {satellite}. Periksa MAP_KEY atau kuota transaksi.")
    try:
        frame = pd.read_csv(io.StringIO(sample), dtype={"acq_date": str, "acq_time": str}, low_memory=False)
    except (ValueError, pd.errors.ParserError, UnicodeError):
        raise SourceError(f"Format respons NASA FIRMS untuk {satellite} tidak dapat dibaca sebagai CSV.") from None
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    absent = [c for c in EXPECTED_COLUMNS if c not in frame.columns]
    if absent:
        raise SourceError(f"CSV {satellite} tidak memiliki kolom wajib: {', '.join(absent)}.")
    frame["source_name"] = satellite
    return frame


def fetch_firms(key: str, satellites: list[str], calendar_days: int = 7,
                end_utc: date | None = None, getter: Callable[..., Any] | None = None
                ) -> tuple[pd.DataFrame, list[str]]:
    """Fetch FI(R)MS CSV for recent UTC days; return raw concatenation + warnings."""
    key = clean_key(key)
    if not satellites:
        raise SourceError("Pilih minimal satu sensor.")
    end_utc = end_utc or datetime.now(timezone.utc).date()
    frames: list[pd.DataFrame] = []
    warnings: list[str] = []
    for sensor in satellites:
        for start, n in period_chunks(end_utc, calendar_days):
            try:
                frames.append(_fetch_sensor_chunk(key, sensor, start, n, getter=getter))
            except SourceError as exc:
                warnings.append(f"{sensor} ({start.isoformat()}, {n} hari): {exc}")
    if not frames:
        raise SourceError("Tidak ada respons valid dari API. " + (warnings[0] if warnings else "Coba kembali."))
    return pd.concat(frames, ignore_index=True), warnings


def normalize_firms(raw: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """QA: coordinates, time, provenance, confidences; DO NOT deduplicate across sensors."""
    data = raw.copy()
    data.columns = [str(c).strip().lower() for c in data.columns]
    absent = [c for c in EXPECTED_COLUMNS if c not in data.columns]
    if absent:
        raise ValueError("Kolom FIRMS wajib hilang: " + ", ".join(absent))
    total = len(data)
    data["latitude"] = pd.to_numeric(data["latitude"], errors="coerce")
    data["longitude"] = pd.to_numeric(data["longitude"], errors="coerce")
    data["frp"] = pd.to_numeric(data.get("frp", pd.Series(np.nan, index=data.index)), errors="coerce")
    data.loc[data["frp"] < 0, "frp"] = np.nan
    # FIRMS occasionally encodes 0 as integer; leading zeros must be restored.
    hhmm = data["acq_time"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).str.zfill(4)
    stamp = data["acq_date"].astype(str).str.strip() + " " + hhmm
    data["acq_utc"] = pd.to_datetime(stamp, format="%Y-%m-%d %H%M", utc=True, errors="coerce")
    valid = (data["latitude"].between(-90, 90) & data["longitude"].between(-180, 180)
             & data["acq_utc"].notna())
    data = data.loc[valid].copy()
    if "source_name" not in data.columns:
        data["source_name"] = data.get("satellite", "Unknown")
    raw_conf = data.get("confidence", pd.Series("?", index=data.index)).astype(str).str.strip().str.lower()
    data["confidence_group"] = np.select(
        [raw_conf.isin(["h", "high"]), raw_conf.isin(["n", "nominal"]), raw_conf.isin(["l", "low"])],
        ["Tinggi", "Nominal", "Rendah"], default="Tidak tersedia")
    data["confidence_raw"] = raw_conf
    if "daynight" not in data.columns:
        data["daynight"] = "?"
    if "satellite" not in data.columns:
        data["satellite"] = "?"
    # Only remove exact copies of the same source record, never co-located different sensors.
    subset = [c for c in ["latitude", "longitude", "acq_utc", "source_name", "frp", "confidence_raw"] if c in data]
    before = len(data)
    data = data.drop_duplicates(subset=subset).copy()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    data["age_h"] = ((pd.Timestamp(now) - data["acq_utc"]).dt.total_seconds() / 3600).round(2)
    data = data.sort_values("acq_utc", ascending=False).reset_index(drop=True)
    return data, {"raw_records": total, "invalid_or_missing": total - before,
                  "exact_duplicates": before - len(data), "valid_records": len(data)}


def _norm_text(text: Any) -> str:
    ascii_form = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", ascii_form.lower()).strip()


def canonical_province(name: str) -> str | None:
    normalized = _norm_text(name)
    lookups = { _norm_text(x): x for x in PROVINCES}
    lookups.update({_norm_text(k): v for k, v in PROVINCE_EN.items()})
    if normalized in lookups:
        return lookups[normalized]
    # geoBoundaries may include provincial prefixes; do not accidentally accept foreign Sabah/Sarawak.
    for alias, province in lookups.items():
        if normalized.endswith(" " + alias) or normalized.startswith(alias + " "):
            return province
    return None


def read_provinces_geojson(geojson: dict[str, Any]) -> dict[str, Any]:
    """Parse five Indonesian Kalimantan province shapes from a licensed ADM1 GeoJSON."""
    found = {}
    for f in geojson.get("features", []):
        props = f.get("properties") or {}
        options = [props.get(k) for k in [
            "shapeName", "shapeName_en", "NAME_1", "name", "ADM1_NAME", "ADM1_EN",
            "provinsi", "province", "NAME", "shapeISO"] if props.get(k)]
        province = next((x for x in map(canonical_province, options) if x), None)
        if province is None:
            continue
        try:
            geom = shape(f["geometry"])
            if not geom.is_valid:
                geom = geom.buffer(0)
            if geom.is_empty:
                continue
        except Exception:
            continue
        if province in found:
            found[province] = unary_union([found[province], geom])
        else:
            found[province] = geom
    missing = [p for p in PROVINCES if p not in found]
    if missing:
        raise SourceError("Batas ADM1 tidak mencakup seluruh lima provinsi Kalimantan; hilang: " + ", ".join(missing))
    return found


def fetch_province_shapes(getter: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Fetch open, CC-BY geoBoundaries simplified IDN ADM1; validate province coverage."""
    meta_response = _http_get(GEOBOUNDARIES_META, getter=getter)
    try:
        meta = meta_response.json()
        url = meta.get("simplifiedGeometryGeoJSON") or meta.get("gjDownloadURL")
        if not url or not str(url).startswith("https://"):
            raise ValueError("No valid data URL")
    except (ValueError, AttributeError, TypeError):
        raise SourceError("Metadata geoBoundaries tidak menyediakan URL GeoJSON yang diharapkan.") from None
    geo_response = _http_get(url, timeout=(10, 60), getter=getter)
    try:
        return read_provinces_geojson(geo_response.json())
    except ValueError:
        raise SourceError("GeoJSON dari geoBoundaries rusak atau bukan JSON.") from None


def assign_provinces(data: pd.DataFrame, shapes: dict[str, Any]) -> pd.DataFrame:
    """Clip strict geometry to five Indonesian provinces; cross-border detections excluded."""
    if any(p not in shapes for p in PROVINCES):
        raise ValueError("Polygon lima provinsi tidak lengkap. Tidak aman melakukan pemetaan lintas batas.")
    output = data.copy()
    output["province"] = None
    # Vectorized shapely 2 is preferred for 100k+ detections. Small-data fallback is safe.
    try:
        from shapely import intersects_xy
        xs = output.longitude.to_numpy(dtype=float)
        ys = output.latitude.to_numpy(dtype=float)
        for province in PROVINCES:
            within = intersects_xy(shapes[province], xs, ys)
            output.loc[within, "province"] = province
    except ImportError:
        locs = [Point(float(x), float(y)) for x, y in zip(output.longitude, output.latitude)]
        for province in PROVINCES:
            prepared = prep(shapes[province])
            inside = [prepared.intersects(pt) for pt in locs]
            output.loc[inside, "province"] = province
    return output.dropna(subset=["province"]).copy().reset_index(drop=True)


def to_feature_collection(shapes: dict[str, Any]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"name": p}, "geometry": mapping(shapes[p])} for p in PROVINCES
    ]}


def demo_detections(now: datetime | None = None, n: int = 230) -> pd.DataFrame:
    """Clearly synthetic data for UI testing, never observational ground truth."""
    now = now or datetime.now(timezone.utc)
    rng = np.random.default_rng(142026)
    centers = {
        "Kalimantan Barat": (-0.75, 111.1),
        "Kalimantan Tengah": (-2.08, 113.7),
        "Kalimantan Selatan": (-3.09, 115.2),
        "Kalimantan Timur": (0.48, 116.4),
        "Kalimantan Utara": (3.08, 116.1),
    }
    weights = [0.19, 0.40, 0.14, 0.20, 0.07]
    provinces = rng.choice(list(centers.keys()), size=n, p=weights)
    # generate spatially clustered detections within each sample province
    coords = []
    for prov in provinces:
        lat, lon = centers[prov]
        cluster = int(rng.integers(0, 4))
        dlat = (cluster - 1.5) * 0.075 + rng.normal(0, 0.009)
        dlon = (cluster - 1.5) * 0.064 + rng.normal(0, 0.009)
        coords.append((lat + dlat, lon + dlon))
    hours_old = rng.integers(1, 160, size=n)
    stamps = [now - timedelta(hours=int(h)) for h in hours_old]
    confidence = rng.choice(["h", "n", "l"], size=n, p=[0.24, 0.63, 0.13])
    satellites = rng.choice(list(SATELLITE_SOURCES), size=n)
    data = pd.DataFrame({
        "latitude": [c[0] for c in coords],
        "longitude": [c[1] for c in coords],
        "acq_date": [v.strftime("%Y-%m-%d") for v in stamps],
        "acq_time": [v.strftime("%H%M") for v in stamps],
        "source_name": satellites, "satellite": satellites,
        "confidence": confidence,
        "frp": np.round(rng.lognormal(mean=2.1, sigma=0.65, size=n), 2),
        "daynight": rng.choice(["D", "N"], size=n),
        "demo_true_province": provinces,
    })
    return data


def temporal_filter(data: pd.DataFrame, hours: int, now: datetime | None = None) -> pd.DataFrame:
    now = now or datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    threshold = pd.Timestamp(now) - pd.Timedelta(hours=hours)
    return data.loc[(data["acq_utc"] >= threshold) & (data["acq_utc"] <= pd.Timestamp(now) + pd.Timedelta(hours=1))].copy()


def cluster_detections(data: pd.DataFrame, eps_km: float = 2.0,
                       min_records: int = 3) -> tuple[pd.DataFrame, pd.DataFrame]:
    """DBSCAN spatial grouping. Not a forecast, incident count or automated field order."""
    records = data.copy().reset_index(drop=True)
    records["cluster_id"] = -1
    columns = ["cluster_id", "province", "latitude", "longitude", "detections",
               "sensors", "high_confidence_records", "days_utc", "max_frp_mw",
               "first_seen_utc", "last_seen_utc", "span_hours", "screening_flag"]
    if records.empty or len(records) < min_records:
        return records, pd.DataFrame(columns=columns)
    coords_rad = np.radians(records[["latitude", "longitude"]].astype(float).values)
    db = DBSCAN(eps=eps_km / 6371.0088, min_samples=min_records, metric="haversine",
                algorithm="ball_tree")
    records["cluster_id"] = db.fit_predict(coords_rad)
    reports = []
    for label, subset in records.loc[records.cluster_id >= 0].groupby("cluster_id"):
        latest = subset.acq_utc.max()
        first = subset.acq_utc.min()
        unique_days = subset.acq_utc.dt.strftime("%Y-%m-%d").nunique()
        high = int((subset.confidence_group == "Tinggi").sum())
        # Screening signal, NOT scientifically validated wildfire severity.
        flag = "Tinjau ulang deteksi berulang" if unique_days > 1 or high > 0 else "Klaster spasial eksploratif"
        reports.append({
            "cluster_id": int(label),
            "province": subset.province.mode().iloc[0] if "province" in subset and not subset.province.mode().empty else "-",
            "latitude": round(float(subset.latitude.mean()), 5),
            "longitude": round(float(subset.longitude.mean()), 5),
            "detections": int(len(subset)),
            "sensors": int(subset.source_name.nunique()),
            "high_confidence_records": high,
            "days_utc": int(unique_days),
            "max_frp_mw": float(subset.frp.max()) if subset.frp.notna().any() else None,
            "first_seen_utc": first,
            "last_seen_utc": latest,
            "span_hours": round((latest - first).total_seconds() / 3600, 1),
            "screening_flag": flag,
        })
    out = pd.DataFrame(reports, columns=columns)
    if not out.empty:
        out = out.sort_values(["detections", "last_seen_utc"], ascending=[False, False]).reset_index(drop=True)
    return records, out


def detections_geojson(data: pd.DataFrame, mode: str) -> str:
    cols = [c for c in ("acq_utc", "province", "source_name", "confidence_group", "frp", "cluster_id") if c in data]
    feature_list = []
    for rec in data[["latitude", "longitude"] + cols].to_dict("records"):
        lat = rec.pop("latitude")
        lon = rec.pop("longitude")
        for k, v in list(rec.items()):
            if isinstance(v, pd.Timestamp):
                rec[k] = v.isoformat()
            elif pd.isna(v):
                rec[k] = None
            elif isinstance(v, np.generic):
                rec[k] = v.item()
        rec["data_status"] = mode
        feature_list.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
                             "properties": rec})
    return json.dumps({"type": "FeatureCollection", "features": feature_list}, ensure_ascii=False)


def describe_age(age_hours: float) -> str:
    if not math.isfinite(age_hours):
        return "Tidak diketahui"
    if age_hours < 1:
        return f"{max(0, int(age_hours * 60))} menit"
    return f"{age_hours:.1f} jam"
