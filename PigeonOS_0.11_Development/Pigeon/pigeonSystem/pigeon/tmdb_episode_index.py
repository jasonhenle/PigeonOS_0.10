"""
Episode-title → series index for kids streaming apps (PBS Kids, Nick Jr, …).

Apple TV often reports only an episode line (e.g. ``Toy Maker``) with the app
set to PBS Kids. Bare TMDb ``/search/tv`` misses those rows. This module keeps a
curated list of active kids-network series (TMDb TV ids), builds a disk-cached
map of normalized episode names → series, and resolves the parent series for
poster / logo pulls.

Cache file: ``~/.pigeon_0_6/tmdb_kids_episode_index.json``
  (override with ``PIGEON_TMDB_EPISODE_INDEX_PATH``).
TTL: 14 days by default (``PIGEON_TMDB_EPISODE_INDEX_TTL_DAYS``).

Episode names are sourced from TMDb season endpoints and enriched with TVMaze
(free, no key) so recent PBS airings that lag on TMDb still resolve.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from pigeon.runtime_paths import pigeon_state_dir

# Curated active PBS Kids / kids-network series → TMDb TV id (verified via API).
KIDS_NETWORK_SERIES: tuple[tuple[str, int], ...] = (
    ("Work It Out Wombats!", 206935),
    ("Daniel Tiger's Neighborhood", 40050),
    ("Wild Kratts", 35094),
    ("Alma's Way", 135926),
    ("Rosie's Rules", 211185),
    ("Elinor Wonders Why", 124747),
    ("Molly of Denali", 93548),
    ("Curious George", 656),
    ("Sesame Street", 502),
    ("Nature Cat", 68046),
    ("Arthur", 2153),
    ("Odd Squad", 73549),
    ("Xavier Riddle and the Secret Museum", 96504),
    ("Donkey Hodie", 125605),
    ("Peg + Cat", 62694),
    ("Super Why!", 14766),
    ("Clifford the Big Red Dog", 8379),
    ("Cyberchase", 2979),
    ("Splash and Bubbles", 73371),
    ("Pinkalicious & Peterrific", 77379),
)

_INDEX_VERSION = 1
_DEFAULT_TTL_DAYS = 14
_UA = "Pigeon0.9/1.0 (local; kids episode index)"
_TVMAZE_BASE = "https://api.tvmaze.com"

# In-process cache of the parsed index payload.
_mem_index: dict[str, Any] | None = None
_mem_path: Path | None = None
_mem_mtime: float | None = None
_build_lock_attempted = False


def _norm_episode_title(s: str) -> str:
    """Same folding as ``tmdb_poster._norm_query`` (local copy avoids import cycles)."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    out: list[str] = []
    prev_space = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        else:
            if not prev_space:
                out.append(" ")
                prev_space = True
    return " ".join("".join(out).split())


def _index_path() -> Path:
    raw = (os.environ.get("PIGEON_TMDB_EPISODE_INDEX_PATH") or "").strip()
    return Path(raw).expanduser() if raw else pigeon_state_dir() / "tmdb_kids_episode_index.json"


def _ttl_seconds() -> float:
    raw = (os.environ.get("PIGEON_TMDB_EPISODE_INDEX_TTL_DAYS") or "").strip()
    try:
        days = float(raw) if raw else float(_DEFAULT_TTL_DAYS)
    except ValueError:
        days = float(_DEFAULT_TTL_DAYS)
    if days < 1:
        days = 1.0
    return days * 86400.0


def _http_get_json(url: str, *, headers: dict[str, str] | None = None, timeout_s: float = 45.0) -> dict | list | None:
    hdrs = {"User-Agent": _UA, "Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def _tmdb_headers_and_auth_url(url: str) -> tuple[str, dict[str, str]]:
    """Attach TMDb Bearer token or api_key using the same credentials as ``tmdb_poster``."""
    from pigeon.tmdb_poster import load_tmdb_api_key, load_tmdb_read_token

    headers: dict[str, str] = {}
    token = load_tmdb_read_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
        return url, headers
    api_key = load_tmdb_api_key()
    if not api_key:
        raise RuntimeError("TMDb not configured for kids episode index")
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urllib.parse.urlencode({'api_key': api_key})}", headers


def _tmdb_get(path: str) -> dict | None:
    from pigeon.tmdb_poster import TMDB_API_BASE

    url = f"{TMDB_API_BASE}{path}"
    url, headers = _tmdb_headers_and_auth_url(url)
    data = _http_get_json(url, headers=headers)
    return data if isinstance(data, dict) else None


def episode_title_lookup_keys(raw: str) -> list[str]:
    """
    Normalized keys to try for an Apple TV / now-playing title.

    Handles compound PBS titles like ``Toy Maker/The Mysterious Ruckus`` by
    also yielding each ``/``-separated segment.
    """
    s = (raw or "").strip()
    if not s:
        return []
    keys: list[str] = []
    seen: set[str] = set()

    def add(part: str) -> None:
        n = _norm_episode_title(part)
        if n and n not in seen:
            seen.add(n)
            keys.append(n)

    add(s)
    # Split compound segment titles (PBS often uses ``A/B``).
    if "/" in s:
        for piece in re.split(r"[/\\|]+", s):
            add(piece)
    # Also peel a trailing parenthetical year if present.
    no_year = re.sub(r"\s*[\(\[]\s*(?:19|20)\d{2}\s*[\)\]]\s*$", "", s).strip()
    if no_year and no_year != s:
        add(no_year)
    return keys


def _add_episode_entry(
    episodes: dict[str, list[dict[str, Any]]],
    *,
    episode_name: str,
    series_id: int,
    series_name: str,
    season: int,
    episode: int,
    air_date: str,
    popularity: float,
) -> None:
    for key in episode_title_lookup_keys(episode_name):
        row = {
            "series_id": int(series_id),
            "series_name": series_name,
            "season": int(season),
            "episode": int(episode),
            "air_date": air_date or "",
            "popularity": float(popularity or 0.0),
            "episode_name": episode_name,
        }
        bucket = episodes.setdefault(key, [])
        # De-dupe same series+season+episode.
        sig = (row["series_id"], row["season"], row["episode"])
        if any((b.get("series_id"), b.get("season"), b.get("episode")) == sig for b in bucket):
            continue
        bucket.append(row)


def _ingest_tmdb_series(series_id: int, series_name: str, episodes: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    detail = _tmdb_get(f"/tv/{int(series_id)}")
    meta = {
        "id": int(series_id),
        "name": series_name,
        "popularity": 0.0,
        "first_air_date": "",
    }
    if not isinstance(detail, dict):
        return meta
    meta["name"] = str(detail.get("name") or series_name)
    try:
        meta["popularity"] = float(detail.get("popularity") or 0.0)
    except (TypeError, ValueError):
        meta["popularity"] = 0.0
    meta["first_air_date"] = str(detail.get("first_air_date") or "")
    seasons = detail.get("seasons") if isinstance(detail.get("seasons"), list) else []
    for s in seasons:
        if not isinstance(s, dict):
            continue
        try:
            sn = int(s.get("season_number"))
        except (TypeError, ValueError):
            continue
        if sn < 0:
            continue
        season = _tmdb_get(f"/tv/{int(series_id)}/season/{sn}")
        if not isinstance(season, dict):
            continue
        for ep in season.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            ename = str(ep.get("name") or "").strip()
            if not ename:
                continue
            try:
                enum = int(ep.get("episode_number") or 0)
            except (TypeError, ValueError):
                enum = 0
            _add_episode_entry(
                episodes,
                episode_name=ename,
                series_id=series_id,
                series_name=meta["name"],
                season=sn,
                episode=enum,
                air_date=str(ep.get("air_date") or ""),
                popularity=meta["popularity"],
            )
    return meta


def _ingest_tvmaze_series(series_name: str, series_id: int, popularity: float, episodes: dict[str, list[dict[str, Any]]]) -> None:
    """Merge TVMaze episode titles for the same show (fills gaps when TMDb lags)."""
    q = urllib.parse.urlencode({"q": series_name})
    show = _http_get_json(f"{_TVMAZE_BASE}/singlesearch/shows?{q}", timeout_s=30.0)
    if not isinstance(show, dict) or show.get("id") is None:
        return
    maze_id = int(show["id"])
    eps = _http_get_json(f"{_TVMAZE_BASE}/shows/{maze_id}/episodes", timeout_s=45.0)
    if not isinstance(eps, list):
        return
    display = str(show.get("name") or series_name)
    for ep in eps:
        if not isinstance(ep, dict):
            continue
        ename = str(ep.get("name") or "").strip()
        if not ename:
            continue
        try:
            sn = int(ep.get("season") or 0)
            enum = int(ep.get("number") or 0)
        except (TypeError, ValueError):
            continue
        _add_episode_entry(
            episodes,
            episode_name=ename,
            series_id=series_id,
            series_name=display,
            season=sn,
            episode=enum,
            air_date=str(ep.get("airdate") or ""),
            popularity=popularity,
        )


def build_kids_episode_index(*, force: bool = False) -> dict[str, Any]:
    """
    Build (or rebuild) the episode → series index and write it to disk.

    Returns the index payload. Raises ``RuntimeError`` if TMDb credentials are missing.
    """
    global _mem_index, _mem_path, _mem_mtime
    path = _index_path()
    if not force:
        existing = _load_index_from_disk(path)
        if existing is not None and not _index_is_stale(existing):
            _mem_index = existing
            _mem_path = path
            try:
                _mem_mtime = path.stat().st_mtime
            except OSError:
                _mem_mtime = None
            return existing

    episodes: dict[str, list[dict[str, Any]]] = {}
    series_meta: list[dict[str, Any]] = []
    for name, sid in KIDS_NETWORK_SERIES:
        meta = _ingest_tmdb_series(sid, name, episodes)
        series_meta.append(meta)
        try:
            _ingest_tvmaze_series(meta["name"], sid, float(meta.get("popularity") or 0.0), episodes)
        except Exception:
            pass

    payload: dict[str, Any] = {
        "version": _INDEX_VERSION,
        "built_at": time.time(),
        "ttl_days": _ttl_seconds() / 86400.0,
        "series": series_meta,
        "episodes": episodes,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    _mem_index = payload
    _mem_path = path
    try:
        _mem_mtime = path.stat().st_mtime
    except OSError:
        _mem_mtime = None
    return payload


def _index_is_stale(payload: dict[str, Any]) -> bool:
    try:
        built = float(payload.get("built_at") or 0.0)
    except (TypeError, ValueError):
        return True
    if built <= 0:
        return True
    if int(payload.get("version") or 0) != _INDEX_VERSION:
        return True
    return (time.time() - built) > _ttl_seconds()


def _load_index_from_disk(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("episodes"), dict) else None


def _ensure_index() -> dict[str, Any] | None:
    """Return a usable index, rebuilding when missing/stale. ``None`` if TMDb unavailable."""
    global _mem_index, _mem_path, _mem_mtime, _build_lock_attempted
    path = _index_path()
    try:
        st = path.stat()
        mtime = st.st_mtime
    except OSError:
        st = None
        mtime = None

    if (
        _mem_index is not None
        and _mem_path == path
        and _mem_mtime == mtime
        and not _index_is_stale(_mem_index)
    ):
        return _mem_index

    if st is not None:
        disk = _load_index_from_disk(path)
        if disk is not None and not _index_is_stale(disk):
            _mem_index = disk
            _mem_path = path
            _mem_mtime = mtime
            return disk

    # Rebuild (may take a while on first launch). Avoid tight rebuild loops on failure.
    if _build_lock_attempted and _mem_index is not None:
        return _mem_index
    _build_lock_attempted = True
    try:
        from pigeon.tmdb_poster import tmdb_is_configured

        if not tmdb_is_configured():
            return _mem_index
        return build_kids_episode_index(force=True)
    except Exception:
        return _mem_index


def clear_kids_episode_index_cache() -> None:
    """Drop in-memory index (tests / forced rebuild)."""
    global _mem_index, _mem_path, _mem_mtime, _build_lock_attempted
    _mem_index = None
    _mem_path = None
    _mem_mtime = None
    _build_lock_attempted = False


def _pick_best_match(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Unique series wins; else prefer higher popularity, then newer air_date."""
    if not candidates:
        return None
    by_series: dict[int, dict[str, Any]] = {}
    for c in candidates:
        try:
            sid = int(c["series_id"])
        except (KeyError, TypeError, ValueError):
            continue
        prev = by_series.get(sid)
        if prev is None:
            by_series[sid] = c
            continue
        # Keep the more recently aired episode row for the same series.
        if str(c.get("air_date") or "") > str(prev.get("air_date") or ""):
            by_series[sid] = c
    if not by_series:
        return None
    if len(by_series) == 1:
        return next(iter(by_series.values()))

    def sort_key(row: dict[str, Any]) -> tuple[float, str]:
        try:
            pop = float(row.get("popularity") or 0.0)
        except (TypeError, ValueError):
            pop = 0.0
        return (pop, str(row.get("air_date") or ""))

    return max(by_series.values(), key=sort_key)


def lookup_series_for_episode_title(episode_title: str | None) -> dict[str, Any] | None:
    """
    Resolve an episode-only title to ``{series_id, series_name, season, episode, ...}``.

    Prefers an exact normalized episode-name match. Compound titles are split on ``/``.
    When multiple series share the title, picks the strongest popularity / newest air date.
    """
    keys = episode_title_lookup_keys(episode_title or "")
    if not keys:
        return None
    index = _ensure_index()
    if not index:
        return None
    episodes = index.get("episodes")
    if not isinstance(episodes, dict):
        return None
    for key in keys:
        bucket = episodes.get(key)
        if not isinstance(bucket, list) or not bucket:
            continue
        rows = [r for r in bucket if isinstance(r, dict)]
        hit = _pick_best_match(rows)
        if hit is not None:
            return hit
    return None


def resolve_kids_series_from_episode_title(episode_title: str | None) -> dict | None:
    """
    Look up episode title in the kids index and return a TMDb ``/tv/{id}`` detail dict.

    Returns ``None`` when no unique/best series match is found or TMDb detail fetch fails.
    """
    hit = lookup_series_for_episode_title(episode_title)
    if hit is None:
        return None
    try:
        sid = int(hit["series_id"])
    except (KeyError, TypeError, ValueError):
        return None
    from pigeon.tmdb_poster import _tmdb_tv_detail

    detail = _tmdb_tv_detail(sid)
    if detail is None:
        # Minimal search-shaped row so callers can still use id/name/poster_path if detail fails.
        return {
            "id": sid,
            "name": hit.get("series_name") or "",
            "original_name": hit.get("series_name") or "",
            "poster_path": None,
            "popularity": hit.get("popularity") or 0.0,
            "first_air_date": hit.get("air_date") or "",
        }
    return detail
