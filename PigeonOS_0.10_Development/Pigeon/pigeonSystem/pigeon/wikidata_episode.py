"""
Resolve an episode-only title to its parent TV series via Wikidata.

Used when Apple TV / streamers (Peacock, etc.) report an episode line with no
series name, and the local kids episode index / hints do not cover it.

Requires an unambiguous Wikidata hit: exact episode label match and exactly one
``episode of …`` candidate (so generic titles like ``Pilot`` stay unresolved).
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_UA = "Pigeon/0.9 (episode-series; local media display)"
_API = "https://www.wikidata.org/w/api.php"
_EPISODE_DESC_RE = re.compile(
    r"(?i)^(?:tv |television )?episode of\b"
)
_SERIES_FROM_DESC_RE = re.compile(
    r"(?i)^(?:tv |television )?episode of (.+?)(?:\s*\(|$)"
)

# In-process cache: normalized episode title → series name or None (negative).
_cache: dict[str, str | None] = {}


def _norm(s: str) -> str:
    try:
        from pigeon.tmdb_poster import _norm_query

        return _norm_query(s)
    except Exception:
        t = re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
        return re.sub(r"\s+", " ", t)


def _http_json(params: dict[str, str], *, timeout_s: float = 12.0) -> dict[str, Any]:
    url = _API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _series_from_episode_description(desc: str) -> str | None:
    m = _SERIES_FROM_DESC_RE.match((desc or "").strip())
    if not m:
        return None
    name = m.group(1).strip().rstrip(".")
    # Reject descriptions that glued extra prose onto the series name.
    if not name or re.search(r"(?i)\bpublished on\b", name) or len(name) > 80:
        return None
    return name


def _search_exact_episode_series(episode_title: str) -> list[str]:
    """Return candidate series names for exact-label episode hits."""
    data = _http_json(
        {
            "action": "wbsearchentities",
            "search": episode_title,
            "language": "en",
            "format": "json",
            "limit": "10",
            "type": "item",
        }
    )
    want = _norm(episode_title)
    out: list[str] = []
    seen: set[str] = set()
    for row in data.get("search") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or "")
        if _norm(label) != want:
            continue
        desc = str(row.get("description") or "").strip()
        if not _EPISODE_DESC_RE.match(desc):
            continue
        series = _series_from_episode_description(desc)
        if not series:
            continue
        key = _norm(series)
        if key in seen:
            continue
        seen.add(key)
        out.append(series)
    return out


def series_name_from_wikidata_episode_title(episode_title: str | None) -> str | None:
    """
    Return the parent series name for an unambiguous episode title, or ``None``.

    Ambiguous titles (multiple Wikidata TV episodes with the same name) return
    ``None`` rather than guessing.
    """
    t = (episode_title or "").strip()
    if not t or len(t) < 2:
        return None
    key = _norm(t)
    if not key:
        return None
    if key in _cache:
        return _cache[key]

    try:
        candidates = _search_exact_episode_series(t)
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        KeyError,
    ):
        # Transient network/rate-limit failures: do not cache a negative.
        return None

    # Exactly one distinct series → use it; 0 or 2+ → unresolved (cache either way).
    series = candidates[0] if len(candidates) == 1 else None
    _cache[key] = series
    return series


def clear_wikidata_episode_cache() -> None:
    """For tests: drop in-process Wikidata episode→series cache."""
    _cache.clear()
