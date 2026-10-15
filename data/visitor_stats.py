"""Privacy-minimal, optional visitor session metrics for Kalimantan Fire Watch.

Records one *browser session* at most, not one unique human. Does not store
IP addresses, full User-Agent strings, cookie IDs, or location. Redis keys and
aggregated counts only. External service is optional and fail-safe.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone, date
from uuid import uuid4
import time
from typing import MutableMapping, Mapping

import requests

DEVICE_TYPES = ('HP', 'Tablet', 'Laptop/Desktop', 'Tidak diketahui')
REDIS_ROOT = 'kfw:visitors:v1'

# Idempotent single-command Lua operation: retries with the same session UUID
# must not count twice, including when an HTTP request times out after commit.
_ATOMIC_REGISTER = """
local new_session = redis.call('SET', KEYS[1], '1', 'NX', 'EX', 604800)
if new_session then
  redis.call('HINCRBY', KEYS[2], ARGV[1], 1)
  redis.call('HINCRBY', KEYS[3], ARGV[1], 1)
  return 1
end
return 0
""".strip()


class AnalyticsUnavailable(RuntimeError):
    """A safe-to-display error without backend credentials."""


def classify_device(headers: Mapping[str, str] | None) -> str:
    """Best-effort visitor device type; no raw HTTP header is persisted."""
    if not headers:
        return 'Tidak diketahui'
    ua = str(headers.get('user-agent', headers.get('User-Agent', '')) or '').lower()
    client_hint = str(headers.get('sec-ch-ua-mobile', headers.get('Sec-CH-UA-Mobile', '')) or '').strip()

    if any(k in ua for k in ('ipad', 'tablet', 'kindle', 'silk/', 'playbook')):
        return 'Tablet'
    if 'android' in ua and 'mobile' not in ua:
        return 'Tablet'
    if client_hint == '?1' or any(k in ua for k in ('iphone', 'ipod', 'windows phone', 'mobile')):
        return 'HP'
    if any(k in ua for k in ('windows nt', 'macintosh', 'x11', 'linux x86_64', 'cros')):
        return 'Laptop/Desktop'
    if ua:
        return 'Tidak diketahui'
    return 'Tidak diketahui'


def tracking_omitted_by_browser(headers: Mapping[str, str] | None) -> bool:
    if not headers:
        return False
    return (str(headers.get('dnt', headers.get('DNT', ''))).strip() == '1'
            or str(headers.get('sec-gpc', headers.get('Sec-GPC', ''))).strip() == '1')


def valid_upstash_url(url: str) -> bool:
    """Prevent sending backend credentials to a non-Upstash endpoint."""
    from urllib.parse import urlparse
    parsed = urlparse(url.strip())
    host = parsed.hostname or ''
    return (parsed.scheme == 'https' and host.endswith('.upstash.io')
            and not parsed.username and not parsed.password and not parsed.port
            and parsed.path in ('', '/') and not parsed.query)


def _post_rest(url: str, token: str, payload, *, endpoint: str = ''):
    if not valid_upstash_url(url):
        raise AnalyticsUnavailable('URL database statistik tidak valid (harus HTTPS Upstash).')
    if not token:
        raise AnalyticsUnavailable('Token database statistik belum tersedia.')
    try:
        response = requests.post(
            url.rstrip('/') + endpoint,
            json=payload,
            headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
            timeout=5,
        )
        response.raise_for_status()
        result = response.json()
    except (requests.RequestException, ValueError):
        # Never show exception strings: requests exceptions may include URLs.
        raise AnalyticsUnavailable('Layanan statistik sementara tidak dapat dihubungi.') from None
    return result


def register_one_session(
    session_state: MutableMapping, headers: Mapping[str, str] | None,
    backend_url: str, backend_token: str, *, now: datetime | None = None
) -> str:
    """Record one counted app visit per Streamlit browser session.

    Returns: disabled, private_browser, recorded, duplicate, cooldown or error.
    """
    if not backend_url or not backend_token:
        return 'disabled'
    if tracking_omitted_by_browser(headers):
        return 'private_browser'
    if session_state.get('_kfw_visitor_recorded'):
        return 'recorded'
    if float(session_state.get('_kfw_visitor_retry_after', 0)) > time.time():
        return 'cooldown'

    if '_kfw_visitor_uuid' not in session_state:
        session_state['_kfw_visitor_uuid'] = uuid4().hex
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    day = moment.strftime('%Y-%m-%d')
    device = classify_device(headers)
    unique_key = f'{REDIS_ROOT}:seen:{session_state["_kfw_visitor_uuid"]}'
    overall_key = f'{REDIS_ROOT}:overall'
    daily_key = f'{REDIS_ROOT}:day:{day}'
    request = ['EVAL', _ATOMIC_REGISTER, 3, unique_key, overall_key, daily_key, device]
    try:
        result = _post_rest(backend_url, backend_token, request)
        if not isinstance(result, dict) or 'error' in result or str(result.get('result')) not in ('0', '1'):
            raise AnalyticsUnavailable('Respons layanan statistik tidak valid.')
    except AnalyticsUnavailable:
        session_state['_kfw_visitor_retry_after'] = time.time() + 300
        return 'error'
    session_state['_kfw_visitor_recorded'] = True
    return 'recorded' if int(result['result']) == 1 else 'duplicate'


def _parse_hash(raw) -> dict[str, int]:
    """Upstash REST HGETALL returns a flat array of alternating field/value."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list) and len(raw) % 2 == 0:
        items = zip(raw[::2], raw[1::2])
    else:
        raise AnalyticsUnavailable('Format hasil statistik tidak dikenal.')
    result = {}
    for k, v in items:
        name = str(k)
        if name in DEVICE_TYPES:
            try:
                value = int(v)
            except (TypeError, ValueError):
                raise AnalyticsUnavailable('Nilai hasil statistik tidak valid.') from None
            result[name] = max(0, value)
    return result


def get_statistics(backend_url: str, backend_token: str, days: int = 30,
                   *, today: date | None = None) -> dict:
    """Retrieve all-time device aggregates and daily visit counts in UTC."""
    if days not in (7, 14, 30, 90):
        raise ValueError('Jangka waktu harus 7, 14, 30, atau 90 hari')
    today = today or datetime.now(timezone.utc).date()
    dates = [today - timedelta(days=i) for i in range(days - 1, -1, -1)]
    keys = [f'{REDIS_ROOT}:overall'] + [f'{REDIS_ROOT}:day:{dt.isoformat()}' for dt in dates]
    commands = [['HGETALL', key] for key in keys]
    raw = _post_rest(backend_url, backend_token, commands, endpoint='/pipeline')
    if not isinstance(raw, list) or len(raw) != len(keys):
        raise AnalyticsUnavailable('Respons statistik tidak lengkap.')
    parsed = []
    for item in raw:
        if not isinstance(item, dict) or item.get('error'):
            raise AnalyticsUnavailable('Statistik belum dapat dibaca dari database.')
        parsed.append(_parse_hash(item.get('result')))
    overall = {name: parsed[0].get(name, 0) for name in DEVICE_TYPES}
    daily = []
    for when, values in zip(dates, parsed[1:]):
        row = {'tanggal_utc': when.isoformat()}
        for name in DEVICE_TYPES:
            row[name] = values.get(name, 0)
        row['total_sesi'] = sum(row[name] for name in DEVICE_TYPES)
        daily.append(row)
    return {
        'total_sejak_aktif': sum(overall.values()),
        'total_per_perangkat': overall,
        'periode_sesi': sum(d['total_sesi'] for d in daily),
        'harian': daily,
        'hari_ini': daily[-1]['total_sesi'],
    }
