"""Local weather for the clock saver (high / low by ZIP).

Default test ZIP is Frederick, MD ``21704``. Uses Zippopotam for lat/lon and
Open-Meteo for daily highs/lows (no API key). Soft-fails offline.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_WEATHER_ZIP = "21704"
_CACHE_TTL_S = 30 * 60.0
_FETCH_TIMEOUT_S = 8.0

_lock = threading.Lock()
_cache_zip: str = ""
_cache_mono: float = 0.0
_cache_high: int | None = None
_cache_low: int | None = None
_fetch_in_flight: bool = False


@dataclass(frozen=True)
class WeatherTemps:
    high_f: int
    low_f: int
    zip_code: str


def _http_json(url: str) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PigeonClockSaver/0.9 (local weather)"},
    )
    with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _geocode_zip(zip_code: str) -> tuple[float, float] | None:
    z = str(zip_code or "").strip()
    if not z:
        return None
    try:
        data = _http_json(f"https://api.zippopotam.us/us/{z}")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    places = data.get("places") if isinstance(data, dict) else None
    if not isinstance(places, list) or not places:
        return None
    place = places[0] if isinstance(places[0], dict) else {}
    try:
        lat = float(place.get("latitude"))
        lon = float(place.get("longitude"))
    except (TypeError, ValueError):
        return None
    return lat, lon


def _fetch_high_low(zip_code: str) -> WeatherTemps | None:
    coords = _geocode_zip(zip_code)
    if coords is None:
        return None
    lat, lon = coords
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.4f}&longitude={lon:.4f}"
        "&daily=temperature_2m_max,temperature_2m_min"
        "&temperature_unit=fahrenheit"
        "&forecast_days=1"
        "&timezone=auto"
    )
    try:
        data = _http_json(url)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(daily, dict):
        return None
    highs = daily.get("temperature_2m_max")
    lows = daily.get("temperature_2m_min")
    if not isinstance(highs, list) or not highs:
        return None
    if not isinstance(lows, list) or not lows:
        return None
    try:
        high = int(round(float(highs[0])))
        low = int(round(float(lows[0])))
    except (TypeError, ValueError):
        return None
    return WeatherTemps(high_f=high, low_f=low, zip_code=str(zip_code).strip())


def cached_weather_temps() -> WeatherTemps | None:
    with _lock:
        if _cache_high is None or _cache_low is None:
            return None
        return WeatherTemps(
            high_f=int(_cache_high),
            low_f=int(_cache_low),
            zip_code=_cache_zip or DEFAULT_WEATHER_ZIP,
        )


def _store(temps: WeatherTemps) -> None:
    global _cache_zip, _cache_mono, _cache_high, _cache_low
    with _lock:
        _cache_zip = temps.zip_code
        _cache_mono = time.monotonic()
        _cache_high = int(temps.high_f)
        _cache_low = int(temps.low_f)


def refresh_weather(*, zip_code: str = DEFAULT_WEATHER_ZIP, force: bool = False) -> bool:
    """Fetch (or reuse cache). Returns True when a background fetch was started."""
    global _fetch_in_flight, _cache_mono
    z = str(zip_code or DEFAULT_WEATHER_ZIP).strip() or DEFAULT_WEATHER_ZIP
    now = time.monotonic()
    with _lock:
        fresh = (
            not force
            and _cache_high is not None
            and _cache_zip == z
            and (now - _cache_mono) < _CACHE_TTL_S
        )
        if fresh or _fetch_in_flight:
            return False
        _fetch_in_flight = True

    def worker() -> None:
        global _fetch_in_flight
        try:
            temps = _fetch_high_low(z)
            if temps is not None:
                _store(temps)
        finally:
            with _lock:
                _fetch_in_flight = False

    threading.Thread(target=worker, name="pigeon-weather", daemon=True).start()
    return True


def ensure_weather(*, zip_code: str = DEFAULT_WEATHER_ZIP) -> WeatherTemps | None:
    """Return cache; kick a refresh when stale/empty."""
    refresh_weather(zip_code=zip_code, force=False)
    return cached_weather_temps()
