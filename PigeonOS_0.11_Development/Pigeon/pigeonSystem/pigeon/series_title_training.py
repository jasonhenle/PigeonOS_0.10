"""
Developer/local hints: map a **normalized playback signature** → series title for TMDb.

Populated via the dev-mode ``+`` hotkey (see ``pigeon_0_5``) or by editing::

    ~/.pigeon_0_6/series_title_training_hints.json

Override path with ``PIGEON_SERIES_TITLE_TRAINING_PATH``.

Keys are produced by :func:`pigeon.raw_title.training_signature_normalized` (order-independent).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pigeon.runtime_paths import pigeon_state_dir

_cache: dict[str, str] | None = None
_cache_file: Path | None = None
_cache_mtime: float | None = None


def _hints_path() -> Path:
    raw = (os.environ.get("PIGEON_SERIES_TITLE_TRAINING_PATH") or "").strip()
    return Path(raw) if raw else pigeon_state_dir() / "series_title_training_hints.json"


def _load_hints() -> dict[str, str]:
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


def lookup_training_series_title(signature_norm: str) -> str | None:
    """Return trained series title for normalized signature, or ``None``."""
    k = (signature_norm or "").strip()
    if not k:
        return None
    from pigeon.tmdb_poster import _norm_query

    nk = _norm_query(k)
    hints = _load_hints()
    v = hints.get(nk)
    return v.strip() if isinstance(v, str) and v.strip() else None


def add_training_mapping(signature_norm: str, series_title: str) -> tuple[bool, str]:
    """
    Persist ``signature_norm → series_title``. Creates the state dir if needed.

    Returns ``(ok, message)``.
    """
    from pigeon.tmdb_poster import _norm_query

    ks = _norm_query((signature_norm or "").strip())
    vs = (series_title or "").strip()
    if not ks:
        return False, "Empty signature (no metadata to key this hint)."
    if not vs:
        return False, "Empty series title."

    p = _hints_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return False, f"Cannot create directory {p.parent}: {e}"

    merged = dict(_load_hints())
    merged[ks] = vs
    try:
        p.write_text(
            json.dumps(merged, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    except OSError as e:
        return False, f"Cannot write {p}: {e}"

    global _cache, _cache_file, _cache_mtime
    _cache = None
    _cache_file = None
    _cache_mtime = None
    return True, f"Saved training hint → {vs!r} (key {ks[:48]}{'…' if len(ks) > 48 else ''})"


def clear_series_title_training_cache() -> None:
    """Tests: drop cache so the next lookup re-reads disk."""
    global _cache, _cache_file, _cache_mtime
    _cache = None
    _cache_file = None
    _cache_mtime = None
