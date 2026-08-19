"""
Map episode-only titles (common from Apple TV / iOS Now Playing) to a series name for TMDb.

There is no reliable way to infer the show from an arbitrary episode string without an external
episode index; public APIs like TVMaze no longer expose episode-title search. This module
supports a small **local** JSON map you maintain for titles you care about.

File: ``~/.pigeon_0_6/episode_series_hints.json`` (override with env ``PIGEON_EPISODE_SERIES_HINTS_PATH``).

Example::

    {
      "lady tomato and mr f tomato head": "The Miniature Wife"
    }

Keys are matched using the same normalization as TMDb queries (see :func:`_norm_query` in
``tmdb_poster``): lowercase, punctuation folded to spaces.

Built-in defaults (Apple TV / iOS often report only the episode line) are merged with your file;
entries in ``episode_series_hints.json`` override built-ins for the same key.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pigeon.runtime_paths import pigeon_state_dir

# Keys are already :func:`pigeon.tmdb_poster._norm_query` form.
_BUILTIN_EPISODE_SERIES_HINTS: dict[str, str] = {
    "lady tomato and mr f tomato head": "The Miniature Wife",
}

_cache: dict[str, str] | None = None
_cache_file: Path | None = None
_cache_mtime: float | None = None


def _hints_path() -> Path:
    raw = (os.environ.get("PIGEON_EPISODE_SERIES_HINTS_PATH") or "").strip()
    return Path(raw) if raw else pigeon_state_dir() / "episode_series_hints.json"


def _load_raw_hints() -> dict[str, str]:
    global _cache, _cache_file, _cache_mtime
    p = _hints_path()
    try:
        st = p.stat()
    except OSError:
        _cache = {}
        _cache_file = p
        _cache_mtime = None
        return _cache

    if _cache is not None and _cache_file == p and _cache_mtime == st.st_mtime:
        return _cache

    from pigeon.tmdb_poster import _norm_query

    out: dict[str, str] = {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _cache = {}
        _cache_file = p
        _cache_mtime = st.st_mtime
        return _cache

    if isinstance(data, dict):
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            ks, vs = k.strip(), v.strip()
            if not ks or not vs:
                continue
            out[_norm_query(ks)] = vs

    _cache = out
    _cache_file = p
    _cache_mtime = st.st_mtime
    return _cache


def series_name_for_episode_title_hint(episode_title: str | None) -> str | None:
    """Return series name from file hints, else built-in defaults, or ``None``."""
    t = (episode_title or "").strip()
    if not t:
        return None
    from pigeon.tmdb_poster import _norm_query

    k = _norm_query(t)
    file_hints = _load_raw_hints()
    if k in file_hints:
        v = file_hints[k].strip()
        return v or None
    return _BUILTIN_EPISODE_SERIES_HINTS.get(k)


def clear_episode_series_hints_cache() -> None:
    """For tests: drop cached hints so the next lookup re-reads disk."""
    global _cache, _cache_file, _cache_mtime
    _cache = None
    _cache_file = None
    _cache_mtime = None
