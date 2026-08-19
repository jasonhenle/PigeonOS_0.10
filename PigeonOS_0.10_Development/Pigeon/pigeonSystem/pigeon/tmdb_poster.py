"""
TMDb: search movie and/or TV by title, download poster into pigeonPulledMedia, then poster pipeline.

Credentials (never commit real keys):
  - PIGEON_TMDB_READ_TOKEN  — JWT read access token (Bearer), preferred
  - PIGEON_TMDB_API_KEY     — v3 API key (query param)
  Or files in ~/.pigeon_0_6/: tmdb_read_token, tmdb_api_key (single line each)

Query hints (optional):
  - Prefix ``tv `` to search TV only (e.g. ``tv Breaking Bad``).
  - Prefix ``movie `` to search movies only.
  - Pass pyatv ``app_name`` / ``app_id`` into :func:`apply_tmdb_movie_query` /
    :func:`search_best_media` so kids apps (PBS Kids, …) prefer TV, try
    ``Title Service`` queries, demote adult substring matches, and fall back to a
    cached episode-title → series index when Apple TV sends only an episode line.
    That episode→series fallback also runs system-wide (Peacock, etc.) whenever
    normal TMDb title search is missing or weakly aligned.

Title matching (env):
  - ``PIGEON_TMDB_MATCH_MODE=literal`` (default) — use now-playing strings as-is where possible; only
    accept TMDb hits with strong title alignment (exact norm for single-word queries, consecutive
    tokens for multi-word). Skips query variants, forced movie/TV shortcuts, and fuzzy tiers.
  - ``PIGEON_TMDB_MATCH_MODE=forgiving`` — previous heuristic behavior (variants, substring tiers, etc.).

Runtime: in the app, **+** (Shift+= on US keyboards; numpad +) toggles literal ↔ forgiving until quit;
that overrides the env default for the current session.

This product uses the TMDb API but is not endorsed or certified by TMDb.

Language:
  - Requests use ``language=en-US``. Search ranking prefers ``original_language=en`` on
    title-tier ties (non-English is never excluded). Poster art prefers English, then
    language-neutral, then any other language only when English is unavailable. Logos
    remain English-only (no non-English fallback).
"""

from __future__ import annotations

import json
import os
import random
import re
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Literal

import numpy as np

from pigeon.app_state import auto_delete_pulled_media
from pigeon.image_ui_protocol import backdrop_master_bgr_from_file, pulled_path_is_under_pulled_dir
from pigeon.media_cache import (
    ASSET_BACKDROP,
    ASSET_LOGO,
    ASSET_LOGO_EN,
    ASSET_POSTER_ART,
    copy_pulled_to_reformatted,
    find_cached_reformatted_asset,
    title_key,
)
from pigeon.media_folders import (
    ensure_tmdb_media_dirs,
    pigeon_pulled_media_dir,
    purge_directory_contents,
    trim_pulled_media_dir,
)
from pigeon.runtime_paths import pigeon_state_dir as _pigeon_state_dir

TMDB_API_BASE = "https://api.themoviedb.org/3"
# Primary product language: request English metadata/artwork when TMDb has it.
# Non-English originals and assets remain available as fallbacks (never hard-excluded).
TMDB_UI_LANGUAGE = "en-US"
TMDB_PRIMARY_ISO_639 = "en"
POSTER_SIZE = "w780"  # good balance before local 1800-wide pipeline
IMG_BASE = f"https://image.tmdb.org/t/p/{POSTER_SIZE}"
# English logos: pull at w1280 for sharp logo-only / downscaled title art (see ``LogoEn`` cache).
LOGO_SIZE = "w1280"
BACKDROP_SIZE = "w1280"
IMG_LOGO_BASE = f"https://image.tmdb.org/t/p/{LOGO_SIZE}"
IMG_BACKDROP_BASE = f"https://image.tmdb.org/t/p/{BACKDROP_SIZE}"

MediaKind = Literal["movie", "tv"]
Prefer = Literal["auto", "movie", "tv"]

# Session override set by :func:`toggle_tmdb_match_mode` (``None`` = follow env only).
_tmdb_match_runtime_forgiving: bool | None = None

_FORGIVING_ENV_TOKENS = frozenset(("forgiving", "loose", "legacy", "1", "true", "yes", "on"))


def _env_wants_forgiving() -> bool:
    v = (os.environ.get("PIGEON_TMDB_MATCH_MODE") or "literal").strip().lower()
    return v in _FORGIVING_ENV_TOKENS


def tmdb_match_forgiving(*, override: bool | None = None) -> bool:
    """
    False = literal/strict matching (default from env). True = legacy forgiving heuristics.

    ``override`` forces the mode regardless of env and runtime toggle.

    After :func:`toggle_tmdb_match_mode`, the runtime choice wins over ``PIGEON_TMDB_MATCH_MODE``
    until the process exits.
    """
    if override is not None:
        return override
    if _tmdb_match_runtime_forgiving is not None:
        return _tmdb_match_runtime_forgiving
    return _env_wants_forgiving()


def toggle_tmdb_match_mode() -> str:
    """
    Flip literal ↔ forgiving for this session.

    Returns ``literal`` or ``forgiving`` (the mode after the toggle).
    """
    global _tmdb_match_runtime_forgiving
    if _tmdb_match_runtime_forgiving is None:
        current = _env_wants_forgiving()
    else:
        current = _tmdb_match_runtime_forgiving
    _tmdb_match_runtime_forgiving = not current
    return "forgiving" if _tmdb_match_runtime_forgiving else "literal"


def _literal_min_acceptable_tier(query: str) -> int:
    """Single-token query → require exact normalized title (tier 5). Multi-word → consecutive tokens (tier 4)."""
    q_tokens = [t for t in _norm_query(query).split() if t]
    if not q_tokens:
        return 5
    return 5 if len(q_tokens) <= 1 else 4
_UA = "Pigeon0.5/1.0 (local; +https://www.themoviedb.org/documentation/api)"


# Trailing ``(YYYY)`` / ``[YYYY]`` years attached to titles by Apple TV / some TMDb clients.
# We accept 1900–2099 so bare numeric titles (``1984``, ``2012``, ``2046``) aren't year-stripped.
# Matches only at end-of-string so mid-title years (``Blade Runner 2049`` — not parenthesized) stay
# intact. Also catches a comma/space-separated year suffix (``Title, 2025``) because Apple TV
# occasionally emits that variant for theatrical titles imported into the TV app.
_TMDB_TRAILING_YEAR_RE = re.compile(
    r"\s*(?:[\(\[]\s*((?:19|20)\d{2})\s*[\)\]]|,\s*((?:19|20)\d{2}))\s*$"
)


def split_query_and_year(raw: str | None) -> tuple[str, int | None]:
    """Peel a trailing ``(YYYY)`` / ``[YYYY]`` / ``, YYYY`` off ``raw``.

    Returns ``(query_without_year, year)``. TMDb's ``/search/movie`` and ``/search/tv`` endpoints
    return zero hits when the query contains a parenthetical year (TMDb titles are stored without
    the year — the year belongs in the ``primary_release_year`` / ``first_air_date_year`` filter).
    This splitter lets the four ``search_*_best*`` helpers forward a clean title to TMDb while
    still pinning the year for disambiguation + filtering.

    Only the trailing token is peeled, and only when the remaining title is non-empty, so bare
    numeric titles (``1984``) and mid-title years (``Blade Runner 2049``) are left intact.
    """
    s = (raw or "").strip()
    if not s:
        return s, None
    m = _TMDB_TRAILING_YEAR_RE.search(s)
    if not m:
        return s, None
    grp = m.group(1) or m.group(2)
    try:
        y = int(grp)
    except (TypeError, ValueError):
        return s, None
    cleaned = s[: m.start()].rstrip(" ,;:-\u2013\u2014")
    cleaned = cleaned.strip()
    if not cleaned:
        # Year-only input (``(2025)``) — don't strip, leave TMDb to decide.
        return s, None
    return cleaned, y


def _norm_query(s: str) -> str:
    """Normalize a query/title for substring + token matching (ASCII-ish, punctuation-insensitive)."""
    s = (s or "").strip().lower()
    if not s:
        return ""
    out: list[str] = []
    prev_space = False
    for ch in s:
        # Keep letters/digits; treat everything else as a space.
        if ch.isalnum():
            out.append(ch)
            prev_space = False
        else:
            if not prev_space:
                out.append(" ")
                prev_space = True
    return " ".join("".join(out).split())


def _title_norm_matches_exact_tv_series_filter(must_norm: str, title_norm: str) -> bool:
    """
    For short-show canonical queries: keep TMDb rows whose name equals the series **or** adds only
    numeric tokens after the series (e.g. ``Saturday Night Live (1975)`` → ``saturday night live 1975``).

    Rejects spin-offs with word suffixes (``Saturday Night Live: Christmas`` → ``… christmas``).
    """
    if not must_norm or not title_norm:
        return False
    if title_norm == must_norm:
        return True
    prefix = must_norm + " "
    if not title_norm.startswith(prefix):
        return False
    rest = title_norm[len(prefix) :].strip()
    if not rest:
        return False
    for tok in rest.split():
        if not tok.isdigit():
            return False
    return True


# App / channel branding — not a movie or episode title (TMDb search yields wrong hits).
_DEGENERATE_TMDB_QUERIES = frozenset(
    {
        "disney",
        "disney+",
        "disney plus",
        "disney+ 365",
        "netflix",
        "hulu",
        "max",
        "peacock",
        "apple tv",
        "youtube",
        "paramount+",
        "paramount plus",
        "prime video",
        "amazon video",
        "amazon prime video",
        "amazon prime",
        "roku",
        "home",
        "settings",
        "hbo max",
        "hbomax",
        "nbc",
        "pbs",
        "pbs kids",
        "pbskids",
        "nick jr",
        "nickjr",
        "noggin",
    }
)

# TMDb genre ids used to keep kids-app matches on age-appropriate rows.
# 16=Animation, 10751=Family, 10762=Kids (TV).
_KIDS_GENRE_IDS = frozenset({16, 10751, 10762})

# Normalized app / bundle needles for kids-only (or kids-primary) streaming apps.
# When these match, TMDb search prefers TV and demotes adult substring hits
# (e.g. PBS Kids title "Secret Tunnels" must not become "Hitler's Secret Tunnels").
_KIDS_STREAMING_APP_NEEDLES: tuple[str, ...] = (
    "pbs kids",
    "pbskids",
    "nick jr",
    "nickjr",
    "noggin",
    "disney junior",
    "disneyjunior",
)
_KIDS_STREAMING_BUNDLE_NEEDLES: tuple[str, ...] = (
    "pbskids",
    "pbs.kids",
    "nickjr",
    "noggin",
    "disneyjunior",
)


def is_degenerate_tmdb_query(q: str) -> bool:
    """
    True if ``q`` should not be sent to TMDb alone (streaming app name, splash branding, etc.).
    """
    raw = (q or "").strip()
    if not raw or len(raw) < 2:
        return True
    n = _norm_query(raw)
    if not n:
        return True
    if n in _DEGENERATE_TMDB_QUERIES:
        return True
    # "disney+ originals", "disney+ 365", etc.
    if "disney" in n and ("365" in n or n.endswith(" original") or n.endswith(" originals")):
        return True
    if n.replace(" ", "").isdigit():
        return True
    try:
        from pigeon.ocr_clues import looks_like_ocr_junk

        if looks_like_ocr_junk(raw):
            return True
    except ImportError:
        pass
    return False


def streaming_service_display_name(
    app_name: str | None = None, app_id: str | None = None
) -> str | None:
    """
    Human streaming-app label from pyatv ``app_name`` / bundle ``app_id`` (e.g. ``PBS Kids``).

    Uses :func:`pigeon.streaming_service_badges.resolve_streaming_badge_media` so badge rules
    and TMDb bias share one mapping. Returns ``None`` when nothing useful is known.
    """
    an = str(app_name or "").strip()
    aid = str(app_id or "").strip()
    if not an and not aid:
        return None
    try:
        from pigeon.streaming_service_badges import resolve_streaming_badge_media

        _rel, display = resolve_streaming_badge_media(
            ".", app_name=an, app_id=aid
        )
    except Exception:
        display = an or ""
    d = str(display or "").strip()
    if not d or d.casefold() == "playing":
        d = an
    d = d.strip()
    if not d or is_degenerate_tmdb_query(d):
        # Degenerate alone is fine as a *hint* appended to a real title (PBS Kids, Netflix…).
        # Only reject empty / trivial.
        if not d or len(d) < 2:
            return None
    return d


def is_kids_streaming_service(
    app_name: str | None = None, app_id: str | None = None
) -> bool:
    """True when the foreground app is a kids-primary service (PBS Kids, Nick Jr, …)."""
    an = _norm_query(str(app_name or ""))
    bid = str(app_id or "").strip().lower()
    for needle in _KIDS_STREAMING_APP_NEEDLES:
        if needle in an:
            return True
    # Bare "pbs" app name / bundle (badge maps PBS → PBS Kids).
    if an == "pbs" or an.endswith(" pbs") or an.startswith("pbs "):
        return True
    for needle in _KIDS_STREAMING_BUNDLE_NEEDLES:
        if needle in bid:
            return True
    if "pbs" in bid and "kids" in bid:
        return True
    # Bundle fragment used by streaming_service_badges for PBS Kids.
    if bid and "pbs" in bid and "disney" not in bid and "passport" not in bid:
        # ``("pbs", None, …)`` badge rule — treat generic PBS apps as kids-leaning on tvOS.
        if re.search(r"(^|[._-])pbs([._-]|$)", bid):
            return True
    return False


def prefer_media_for_streaming_service(
    prefer: Prefer | str,
    *,
    app_name: str | None = None,
    app_id: str | None = None,
) -> Prefer:
    """
    When ``prefer`` is ``auto``, bump kids-primary apps to ``tv``.

    Prevents movie-first ``auto`` from winning adult documentaries that merely contain
    the same short title tokens (PBS Kids ``Secret Tunnels`` → movie *Hitler's Secret Tunnels*).
    """
    p = str(prefer or "auto").strip().lower()
    if p not in ("auto", "movie", "tv"):
        p = "auto"
    if p == "auto" and is_kids_streaming_service(app_name, app_id):
        return "tv"
    return p  # type: ignore[return-value]


def _item_has_kids_genre(item: dict) -> bool:
    ids = item.get("genre_ids")
    if not isinstance(ids, list):
        return False
    for g in ids:
        try:
            if int(g) in _KIDS_GENRE_IDS:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _apply_kids_service_result_bias(
    scored: list[tuple[dict, tuple[int, int]]],
    *,
    kids_bias: bool,
) -> list[tuple[dict, tuple[int, int]]]:
    """
    For kids streaming apps: keep exact title hits and kids/family/animation genres;
    drop loose adult substring matches (tier &lt; 5 without kids genres).

    An empty result means “no acceptable kids match for this query” — callers try the
    service-augmented variant or report no match rather than falling back to adult hits.
    """
    if not kids_bias or not scored:
        return scored
    return [
        (r, rk)
        for r, rk in scored
        if rk[0] >= 5 or _item_has_kids_genre(r)
    ]


# Keys match ``_norm_query()`` form (e.g. ``snl`` for ``SNL``, full title for specials).
_SHORT_SHOW_CANONICAL_QUERIES: dict[str, str] = {
    "snl": "Saturday Night Live",
    "saturday night live": "Saturday Night Live",
}

# When TMDb lists compilation specials as **movies** (e.g. ``Saturday Night Live: Christmas``), inferred
# ``prefer=movie`` from pyatv ``Video`` must still resolve the **series** for artwork. Keys = ``_norm_query``
# form of the canonical display title (same namespace as ``_exact_tv_title_norm_for_known_series_query``).
_SHORT_SHOW_TMDB_TV_ID_BY_NORM: dict[str, int] = {
    "saturday night live": 1667,
}

# Peacock often sends guest/segment lines with no ``series_name``. Longer needles first.
_NBC_LATE_NIGHT_SUBSTRING_TO_SERIES: tuple[tuple[str, str], ...] = (
    ("the tonight show starring jimmy fallon", "The Tonight Show Starring Jimmy Fallon"),
    ("last week tonight with john oliver", "Last Week Tonight with John Oliver"),
    ("the late show with stephen colbert", "The Late Show with Stephen Colbert"),
    ("late night with seth meyers", "Late Night with Seth Meyers"),
    ("jimmy fallon", "The Tonight Show Starring Jimmy Fallon"),
    ("seth meyers", "Late Night with Seth Meyers"),
    ("john oliver", "Last Week Tonight with John Oliver"),
    ("stephen colbert", "The Late Show with Stephen Colbert"),
)


def _compact_norm_for_acronym(s: str) -> str:
    return "".join(ch for ch in _norm_query(s) if ch.isalnum())


def _canonical_series_from_dash_pair(left: str, right: str) -> str | None:
    """
    Peacock / tvOS sometimes send ``SNL - Sketch`` or ``Sketch - SNL``.
    If either side is a known acronym, return the canonical series search string.
    """
    le, ri = left.strip(), right.strip()
    if not le or not ri:
        return None
    lk = _compact_norm_for_acronym(le)
    rk = _compact_norm_for_acronym(ri)
    if rk in _SHORT_SHOW_CANONICAL_QUERIES:
        return _SHORT_SHOW_CANONICAL_QUERIES[rk]
    if lk in _SHORT_SHOW_CANONICAL_QUERIES:
        return _SHORT_SHOW_CANONICAL_QUERIES[lk]
    return None


def _norm_blob_suggests_snl(norm_blob: str) -> bool:
    """True when combined metadata is clearly Saturday Night Live (not The Tonight Show)."""
    if not norm_blob:
        return False
    if "saturday night live" in norm_blob:
        return True
    return "snl" in frozenset(norm_blob.split())


def _canonical_series_from_nbc_late_night_blob(norm_blob: str) -> str | None:
    """
    Peacock / NBCUniversal apps often expose a guest or segment line without ``series_name``.
    Map obvious substrings to the TMDb series title. ``jimmy fallon`` is ignored when the blob looks
    like SNL (Fallon as host/guest).
    """
    if not norm_blob:
        return None
    for needle, canon in _NBC_LATE_NIGHT_SUBSTRING_TO_SERIES:
        if needle not in norm_blob:
            continue
        if needle == "jimmy fallon" and _norm_blob_suggests_snl(norm_blob):
            continue
        return canon
    return None


def canonical_tv_title_if_sketch_show_compound(display_title: str) -> str | None:
    """
    TMDb sometimes returns a TV row whose ``name`` is ``Sketch - SNL``, ``SNL - Sketch``,
    or ``Saturday Night Live: Christmas``-style episode/special labels.
    Replace with the canonical series title when a known show appears on the left (dash or colon).
    """
    q0 = _normalize_title_for_show_split(display_title or "")
    if not q0:
        return None
    pair = _sketch_show_dash_pair(q0)
    if pair is not None:
        c = _canonical_series_from_dash_pair(pair[0], pair[1])
        if c:
            return c
    for sep in (":", "\uff1a"):
        if sep in q0:
            a, b = q0.split(sep, 1)
            a_s, b_s = a.strip(), b.strip()
            if not a_s or not b_s:
                continue
            c = _canonical_series_from_dash_pair(a_s, b_s)
            if c:
                return c
            full = _SHORT_SHOW_CANONICAL_QUERIES.get(_norm_query(a_s))
            if full:
                return full
    return None


_UNICODE_DASH_CHARS = frozenset(
    "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d"  # hyphen, dashes, minus (not ASCII -)
)

# NBSP and other spaces that break naive ``" - "`` substring checks (Peacock / tvOS metadata).
_SPACE_LIKE_RE = re.compile(r"[\u00a0\u2000-\u200a\u202f\u205f\u3000]+")
# Middle dot / bullet separators (Apple TV ``WALL·E``) — TMDb indexes ``WALL·E`` with the dot;
# hyphen queries (``WALL-E``) return unrelated ``wall*`` titles.
_INTERPUNCT_CHAR = "\u00b7"
_INTERPUNCT_LIKE_CHARS = frozenset("\u00b7\u2022\u2024\u2219\u22c5\u30fb\uff65")
# ``WALL E`` / ``WALL·E`` → ``WALL-E`` (short suffix token only, e.g. not ``STAR WARS``).
_ACRONYM_HYPHEN_SUFFIX_RE = re.compile(r"^([\w]{2,})\s+([\w]{1,2})$")
# ``Show - sketch`` with flexible space and any common dash (ASCII or unicode).
_SHOW_EPISODE_SEP_RE = re.compile(
    r"\s+[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d]\s+"
)
# Metadata sometimes omits spaces around the hyphen (``Papryus-SNL``, ``Papryus -SNL``).
_LOOSE_SHOW_EPISODE_SEP_RE = re.compile(
    r"\s*[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uff0d]\s*"
)


def _sketch_show_dash_pair(q0: str) -> tuple[str, str] | None:
    """Return ``(left, right)`` for ``Sketch - Show`` / tight-hyphen variants, or None."""
    m = _SHOW_EPISODE_SEP_RE.split(q0, maxsplit=1)
    if len(m) == 2:
        a, b = m[0].strip(), m[1].strip()
        if a and b:
            return a, b
    parts = _LOOSE_SHOW_EPISODE_SEP_RE.split(q0, maxsplit=1)
    if len(parts) == 2:
        a, b = parts[0].strip(), parts[1].strip()
        if a and b:
            return a, b
    return None


def _normalize_unicode_dashes_for_episode_titles(s: str) -> str:
    """Map unicode dashes to `` - `` so ``SNL–Sketch`` (en dash) splits like ``SNL - Sketch``."""
    if not s:
        return s
    parts: list[str] = []
    for ch in s:
        if ch in _UNICODE_DASH_CHARS:
            parts.append(" - ")
        else:
            parts.append(ch)
    t = "".join(parts)
    while "   " in t:
        t = t.replace("   ", " ")
    while "  " in t:
        t = t.replace("  ", " ")
    return t.strip()


def _normalize_interpunct_for_tmdb(s: str) -> str:
    """Preserve middle-dot titles for TMDb search (API indexes ``WALL·E`` with the dot).

    Hyphen/compact variants are attempted separately in :func:`_tmdb_query_variants`; mapping
    ``·`` → ``-`` breaks the movie search (``WALL-E`` returns unrelated ``wall`` titles).
    """
    return s


def _compact_interpunct_acronym(s: str) -> str | None:
    """``WALL·E`` / ``WALL-E`` / ``WALL E`` → ``WALLE`` for TMDb variant search."""
    if not s:
        return None
    chars = list(s.strip())
    out: list[str] = []
    for ch in chars:
        if ch in _INTERPUNCT_LIKE_CHARS or ch in _UNICODE_DASH_CHARS or ch == "-":
            continue
        if ch.isspace():
            if out and out[-1] != " ":
                out.append(" ")
        else:
            out.append(ch)
    t = "".join(out)
    t = re.sub(r"\s+", "", t).strip()
    if len(t) < 3:
        return None
    return t


def _interpunct_from_hyphen_acronym(q: str) -> str | None:
    """``WALL-E`` → ``WALL·E`` — TMDb movie search uses the middle-dot title, not the hyphen."""
    s = (q or "").strip()
    m = re.match(r"^([A-Za-z0-9]{2,})-([A-Za-z0-9]{1,2})$", s)
    if not m:
        return None
    return f"{m.group(1)}{_INTERPUNCT_CHAR}{m.group(2)}"


def _hyphen_acronym_movie_alias(q: str) -> str | None:
    """``WALL E`` → ``WALL-E`` when the suffix is a short token (Pixar-style acronym titles)."""
    m = _ACRONYM_HYPHEN_SUFFIX_RE.match((q or "").strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2)}"


def _normalize_title_for_show_split(s: str) -> str:
    """Unicode dashes → spaced hyphen; preserve interpunct; NBSP-like → space; collapse runs."""
    if not s:
        return s
    t = _normalize_unicode_dashes_for_episode_titles(s.strip())
    t = _normalize_interpunct_for_tmdb(t)
    t = _SPACE_LIKE_RE.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _colon_prefix_show_query_normalized(q0: str) -> str | None:
    """Core split for :func:`colon_prefix_show_query`; ``q0`` must already be :func:`_normalize_title_for_show_split`."""
    if not q0:
        return None

    def _split_show(sep: str) -> str | None:
        if sep not in q0:
            return None
        left, right = q0.split(sep, 1)
        left, right = left.strip(), right.strip()
        if not left or not right:
            return None
        if len(left) < 2:
            return None
        if is_degenerate_tmdb_query(left):
            return None
        if left.lower() == q0.lower():
            return None
        return left

    parts = _SHOW_EPISODE_SEP_RE.split(q0, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        if left and right:
            canon = _canonical_series_from_dash_pair(left, right)
            if canon:
                return canon
            if (
                len(left) >= 2
                and not is_degenerate_tmdb_query(left)
                and left.lower() != q0.lower()
            ):
                return left

    for sep in (" - ", " – "):
        got = _split_show(sep)
        if got:
            return got
    for sep in ("\u2014", "\u2013"):
        got = _split_show(sep)
        if got:
            return got
    for sep in (":", "\uff1a"):
        got = _split_show(sep)
        if got:
            return got
    return None


def colon_prefix_show_query(raw: str) -> str | None:
    """
    If metadata looks like ``Show: segment`` or ``Show - sketch`` (guest, sketch, episode label),
    return the show side for TMDb when the full string would match the wrong thing or miss the series.

    **Prime / Hulu / Peacock:** titles like ``Series S01 E01 - Episode`` are split on the wrong dash if
    S/E is not removed first; we strip embedded season/episode clauses (see
    :func:`pigeon.raw_title._strip_season_episode_from_text`) and try that string before the original.
    """
    q0 = _normalize_title_for_show_split(raw or "")
    if not q0:
        return None
    candidates: list[str] = []
    try:
        from pigeon.raw_title import _strip_season_episode_from_text

        c_raw, _, _ = _strip_season_episode_from_text(q0)
        c_n = _normalize_title_for_show_split((c_raw or "").strip())
        if c_n:
            candidates.append(c_n)
    except ImportError:
        pass
    if not any(c.lower() == q0.lower() for c in candidates):
        candidates.append(q0)
    seen: set[str] = set()
    for cand in candidates:
        ck = cand.lower()
        if ck in seen:
            continue
        seen.add(ck)
        got = _colon_prefix_show_query_normalized(cand)
        if got:
            return got
    return None


def _colon_show_episode_pair(q: str) -> tuple[str, str] | None:
    """First ``Show: Episode`` split (ASCII or full-width colon); ``None`` if not a usable pair."""
    for sep in (":", "\uff1a"):
        if sep not in q:
            continue
        left, right = q.split(sep, 1)
        left, right = left.strip(), right.strip()
        if len(left) < 2 or len(right) < 2:
            continue
        if is_degenerate_tmdb_query(left):
            continue
        if left.lower() == q.lower():
            continue
        return left, right
    return None


def compound_title_streaming_series_fix(
    title: str | None, series_name: str | None
) -> str | None:
    """
    Streaming apps (Disney+, Peacock, Netflix, …) sometimes send a misleading ``series_name``:
    the full compound line duplicated in both fields, or the **episode** segment while ``title`` is
    still ``Show - Episode`` or ``Show: Episode``.
    """
    if not title or not series_name:
        return None
    t = title.strip()
    sn = series_name.strip()
    if not t or not sn:
        return None
    tl, snl = t.lower(), sn.lower()
    if tl == snl:
        cp = colon_prefix_show_query(t)
        if cp:
            return cp.strip()
        q0 = _normalize_title_for_show_split(t)
        if " - " in q0:
            left, _ = q0.split(" - ", 1)
            left = left.strip()
            if len(left) >= 2 and not is_degenerate_tmdb_query(left):
                return left
        pair_c = _colon_show_episode_pair(q0)
        if pair_c:
            le, _ri = pair_c
            return le
        return None
    q0 = _normalize_title_for_show_split(t)

    if " - " in q0:
        left, right = q0.split(" - ", 1)
        left, right = left.strip(), right.strip()
        if len(left) >= 2 and len(right) >= 2 and snl == right.lower():
            return left

    pair = _colon_show_episode_pair(q0)
    if pair:
        left, right = pair
        if snl == right.lower():
            return left

    return None


_GUEST_ON_SHOW_RE = re.compile(
    r"^(.+?)\s+on\s+(snl|saturday\s+night\s+live)\s*$",
    re.IGNORECASE,
)


def _guest_on_show_canonical_query(s: str) -> str | None:
    """``Will Ferrell on SNL`` → ``Saturday Night Live`` for TMDb TV search."""
    m = _GUEST_ON_SHOW_RE.match((s or "").strip())
    if not m:
        return None
    return _SHORT_SHOW_CANONICAL_QUERIES.get("snl") or "Saturday Night Live"


def refine_tmdb_search_query(raw: str | None) -> str | None:
    """
    Last-mile cleanup for any metadata source: unicode dashes, then ``Show - segment`` / colon stripping.
    Safe to call on strings that already went through pyatv heuristics (idempotent for plain titles).
    """
    if raw is None:
        return None
    s = _normalize_title_for_show_split(str(raw).strip())
    if not s:
        return None
    guest_show = _guest_on_show_canonical_query(s)
    if guest_show:
        return guest_show
    pick = colon_prefix_show_query(s)
    out = (pick or s).strip()
    return out or None


def canonical_tv_display_name_for_search_query(search_query: str) -> str | None:
    """
    When the user/device search resolves to a known acronym (e.g. ``SNL``), use TMDb’s full series
    name for on-screen title/logo cache even if TMDb matched a sketch row.
    """
    p = (search_query or "").strip()
    m = re.match(r"(?is)^tv\s+(.+)$", p)
    if m:
        p = m.group(1).strip()
    key = _norm_query(p)
    return _SHORT_SHOW_CANONICAL_QUERIES.get(key)


def _exact_tv_title_norm_for_known_series_query(q: str) -> str | None:
    """
    If ``q`` maps to a canonical series in ``_SHORT_SHOW_CANONICAL_QUERIES`` (via
    :func:`canonical_tv_display_name_for_search_query`, including after
    :func:`refine_tmdb_search_query`), return that series’ normalized title.

    TV search keeps only rows whose title matches the canonical series (normalized), optionally with
    **numeric-only** trailing tokens (e.g. TMDb’s ``(1975)`` in the title). When this returns
    non-``None``, media pick uses **TV** for that query (and a fixed TMDb id when configured) even if
    ``prefer`` is ``movie``, so compilation **movies** with the same words cannot beat the series.
    """
    p = (q or "").strip()
    m = re.match(r"(?is)^tv\s+(.+)$", p)
    if m:
        p = m.group(1).strip()
    canon = canonical_tv_display_name_for_search_query(p)
    if canon:
        return _norm_query(canon)
    r = refine_tmdb_search_query(p) or p
    if r.strip() != p.strip():
        canon = canonical_tv_display_name_for_search_query(r)
        if canon:
            return _norm_query(canon)
    return None


def resolve_tmdb_query_from_now_playing_fields(
    *,
    base_query: str | None,
    title: object | None = None,
    series_name: object | None = None,
    artist: object | None = None,
    album: object | None = None,
    episode_title: object | None = None,
    forgiving: bool | None = None,
) -> str | None:
    """
    Build the TMDb search string from pyatv-style fields plus the heuristic ``base_query``.

    **Literal mode** (default, ``PIGEON_TMDB_MATCH_MODE=literal``): first substantive non-degenerate
    field. Normally ``base_query`` (Apple TV heuristics) wins first so Disney+/etc. fixes beat a stale
    ``series_name``. When ``series_name`` and ``title`` disagree but ``base_query`` is just the episode
    ``title``, ``series_name`` is tried first so TV rows are not reduced to movie-style title-only
    metadata. If the chosen string is still just the episode line but combined metadata matches a
    Peacock/NBC late-night franchise (same substring map as forgiving mode), the canonical series
    title is used instead.

    **Forgiving mode** (``forgiving=True`` or env ``forgiving``): prefer canonical series titles,
    sketch–show compounds, Peacock NBC blob rules, episode hints, etc.
    """

    def _field(x: object | None) -> str | None:
        if x is None:
            return None
        t = str(x).strip()
        return t or None

    def _prefer_full_title_when_base_is_stub(
        base_q: str | None, title_q: str | None
    ) -> str | None:
        """If ``base_q`` is a short prefix of a fuller ``title_q``, return ``title_q``."""
        if not base_q or not title_q:
            return base_q
        nbq = _norm_query(base_q)
        nti = _norm_query(title_q)
        if (
            nbq
            and nti
            and len(nbq.split()) <= 2
            and len(nti.split()) >= 3
            and nti.startswith(nbq + " ")
        ):
            return title_q
        return base_q

    fg = tmdb_match_forgiving(override=forgiving)
    bq = _field(base_query)
    sn = _field(series_name)
    ti = _field(title)
    et = _field(episode_title)
    bq = _prefer_full_title_when_base_is_stub(bq, ti)
    if not fg:
        # Episode/film title in base_query while series_name names the show → search the show, not the episode string.
        bq_is_episode_like = bool(
            bq
            and (
                (ti and bq.lower() == ti.lower())
                or (et and bq.lower() == et.lower())
            )
        )
        compound_disagrees_with_sn = False
        if sn and ti:
            cp_ti = colon_prefix_show_query(ti)
            if cp_ti:
                snl = sn.lower()
                cpl = cp_ti.strip().lower()
                til = ti.lower()
                # Stale series_name (e.g. Disney+) while title is already ``NewShow - Episode``: trust base_query order.
                if cpl != snl and snl not in til:
                    compound_disagrees_with_sn = True
        prefer_series_first = bool(
            sn
            and not is_degenerate_tmdb_query(sn)
            and bq_is_episode_like
            and not compound_disagrees_with_sn
            and (
                (ti and sn.lower() != ti.lower())
                or (et and sn.lower() != et.lower())
            )
        )
        if prefer_series_first:
            ordered = (series_name, base_query, album, artist, title, episode_title)
        else:
            ordered = (base_query, series_name, album, artist, title, episode_title)
        first_pick: str | None = None
        for x in ordered:
            s = _field(x)
            if s and not is_degenerate_tmdb_query(s):
                first_pick = s
                break
        if first_pick is None:
            return None
        # Literal mode used to return before forgiving Peacock/NBC blob rules; guest-only titles still need it.
        blob_parts_lit: list[str] = []
        for x in (base_query, series_name, title, episode_title, artist, album):
            s = _field(x)
            if s and not is_degenerate_tmdb_query(s):
                blob_parts_lit.append(s)
        norm_lit = _norm_query(" ".join(blob_parts_lit)) if blob_parts_lit else ""
        nbc_lit = _canonical_series_from_nbc_late_night_blob(norm_lit)
        if nbc_lit:
            fp = first_pick.lower()
            episode_like_pick = bool(
                (ti and fp == ti.lower()) or (et and fp == et.lower())
            )
            if episode_like_pick and _norm_query(first_pick) != _norm_query(nbc_lit):
                return nbc_lit
        return first_pick

    ordered: list[str] = []
    for x in (base_query, series_name, title, episode_title, artist, album):
        s = _field(x)
        if s and s not in ordered:
            ordered.append(s)

    for s in ordered:
        compound = canonical_tv_title_if_sketch_show_compound(s)
        if compound:
            return compound
    for s in ordered:
        r = refine_tmdb_search_query(s) or s
        canon = canonical_tv_display_name_for_search_query(r)
        if canon:
            return canon

    # Peacock: guest/segment-only strings (no series_name) — e.g. Tonight Show, Late Night, LWT, Colbert.
    blob_parts: list[str] = []
    for x in (base_query, series_name, title, episode_title, artist, album):
        s = _field(x)
        if s and not is_degenerate_tmdb_query(s):
            blob_parts.append(s)
    norm_blob = _norm_query(" ".join(blob_parts)) if blob_parts else ""
    nbc_series = _canonical_series_from_nbc_late_night_blob(norm_blob)
    if nbc_series:
        return refine_tmdb_search_query(nbc_series) or nbc_series

    # Apple TV+ / streamers often put a sketch or episode label in ``title`` and the real series in
    # ``series_name``. Disney+ can send a **stale** ``series_name`` (previous show) while ``title``
    # is still ``Show - Episode`` — prefer the show parsed from ``title`` when it disagrees and the
    # reported series string does not appear inside ``title``.
        # bq/sn/ti/et were normalized above.
    if (
        sn
        and ti
        and sn.lower() != ti.lower()
        and not is_degenerate_tmdb_query(sn)
    ):
        cp = colon_prefix_show_query(ti)
        if cp:
            cpl = cp.strip().lower()
            snl = sn.lower()
            til = ti.lower()
            if cpl != snl and snl not in til:
                return refine_tmdb_search_query(cp) or cp.strip()
        return refine_tmdb_search_query(sn) or sn

    # Episode-only metadata (common on iOS Now Playing): optional ~/.pigeon_0_6/episode_series_hints.json
    if not sn:
        from pigeon.episode_series_hints import series_name_for_episode_title_hint

        for cand in (_field(episode_title), _field(title)):
            if not cand:
                continue
            g = series_name_for_episode_title_hint(cand)
            if g and not is_degenerate_tmdb_query(g):
                return refine_tmdb_search_query(g) or g

    if base_query is None:
        return None
    out = refine_tmdb_search_query(str(base_query).strip()) or str(base_query).strip()
    if not out:
        return None
    compound = canonical_tv_title_if_sketch_show_compound(out)
    if compound:
        return compound
    canon = canonical_tv_display_name_for_search_query(out)
    return canon or out


def equivalent_tmdb_search_queries(a: str, b: str) -> bool:
    """
    True when two query strings mean the same show for alternation / dedupe
    (e.g. primary ``SNL`` vs metadata title ``Papyrus - SNL``).
    """
    ra = refine_tmdb_search_query((a or "").strip()) or (a or "").strip()
    rb = refine_tmdb_search_query((b or "").strip()) or (b or "").strip()
    if ra.lower() == rb.lower():
        return True
    ca = canonical_tv_display_name_for_search_query(ra)
    cb = canonical_tv_display_name_for_search_query(rb)
    if ca and cb and ca.lower() == cb.lower():
        return True
    if ca and rb.lower() == ca.lower():
        return True
    if cb and ra.lower() == cb.lower():
        return True
    return False


def _tmdb_query_variants(raw: str) -> list[str]:
    """
    Extra query strings to try when now-playing metadata appends app names or episode titles
    (common from Apple TV), which TMDb will not match as a single search phrase.

    The full, unsimplified title is always tried first. Simplified variants
    (show-prefix, colon-left, etc.) are fallbacks only.
    """
    q0 = _normalize_title_for_show_split((raw or "").strip())
    raw0 = (raw or "").strip()
    if not q0:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = s.strip()
        if t and t not in seen:
            seen.add(t)
            out.append(t)

    # Hyphen acronym titles (``WALL-E``): TMDb indexes ``WALL·E`` — try that before the hyphen.
    interpunct = _interpunct_from_hyphen_acronym(raw0 or q0)
    if interpunct:
        add(interpunct)
    # Always try the exact/full title next; only fall back to simplifications.
    add(q0)
    if raw0 and raw0.casefold() != q0.casefold():
        add(raw0)
    compact = _compact_interpunct_acronym(raw0 or q0)
    if compact:
        add(compact)
    alias = _hyphen_acronym_movie_alias(q0)
    if alias:
        add(alias)

    prefixes: list[str] = []

    def push_left(left: str, right: str) -> None:
        le = left.strip()
        ri = right.strip()
        if not le or not ri or len(le) < 2:
            return
        if is_degenerate_tmdb_query(le):
            return
        if le.lower() == q0.lower():
            return
        prefixes.append(le)

    rx_parts = _SHOW_EPISODE_SEP_RE.split(q0, maxsplit=1)
    if len(rx_parts) == 2:
        lx, rx = rx_parts[0].strip(), rx_parts[1].strip()
        cnp = _canonical_series_from_dash_pair(lx, rx)
        if cnp:
            add(cnp)
        else:
            push_left(lx, rx)

    if "|" in q0:
        a, b = q0.split("|", 1)
        push_left(a, b)
    if " - " in q0:
        a, b = q0.split(" - ", 1)
        push_left(a, b)
    for sep in (" – ", "\u2014", "\u2013"):
        if sep in q0:
            a, b = q0.split(sep, 1)
            push_left(a, b)
    for sep in (":", "\uff1a"):
        if sep in q0:
            a, b = q0.split(sep, 1)
            push_left(a, b)

    uniq_prefix: list[str] = []
    for p in prefixes:
        if p not in uniq_prefix:
            uniq_prefix.append(p)
    # Shortest first: e.g. ``SNL`` before a longer accidental prefix.
    uniq_prefix.sort(key=len)
    for p in uniq_prefix:
        add(p)
        key = _norm_query(p)
        canon = _SHORT_SHOW_CANONICAL_QUERIES.get(key)
        if canon:
            add(canon)
    if "|" in q0:
        add(q0.split("|", 1)[0])
    for sep in ("\u2014", "\u2013", " – "):
        if sep in q0:
            add(q0.split(sep, 1)[0])
    for sep in (":", "\uff1a"):
        if sep in q0:
            left = q0.split(sep, 1)[0].strip()
            if left and not is_degenerate_tmdb_query(left):
                add(left)
    return out


def _result_titles(item: dict) -> list[str]:
    """Candidate title strings for movie/tv result dicts."""
    vals = [
        item.get("title"),
        item.get("original_title"),
        item.get("name"),
        item.get("original_name"),
    ]
    out: list[str] = []
    for v in vals:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    # de-dupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _tokens_subsequence(needle: list[str], haystack: list[str]) -> bool:
    """True if ``needle`` appears as consecutive tokens in ``haystack``."""
    if not needle:
        return True
    nlen = len(needle)
    for i in range(len(haystack) - nlen + 1):
        if haystack[i : i + nlen] == needle:
            return True
    return False


def _short_acronym_compact_norm(query: str) -> str | None:
    """Compact form for Pixar-style titles (``WALL·E``, ``WALL-E``, ``WALL E`` → ``walle``)."""
    compact = _compact_interpunct_acronym(query)
    if not compact or len(compact) > 8:
        return None
    nq = _norm_query(query)
    tokens = [t for t in nq.split() if t]
    if len(tokens) == 2 and len(tokens[1]) <= 2:
        return _compact_norm_for_acronym(compact)
    if re.fullmatch(r"^[A-Za-z0-9]{2,}-[A-Za-z0-9]{1,2}$", (query or "").strip()):
        return _compact_norm_for_acronym(compact)
    if any(ch in _INTERPUNCT_LIKE_CHARS for ch in (query or "")):
        return _compact_norm_for_acronym(compact)
    return None


def _weak_short_acronym_match(query: str, item: dict, rank: tuple[int, int]) -> bool:
    """Reject fuzzy ``wall*`` hits when the query is a short acronym like ``WALL-E``."""
    if rank[0] >= 4:
        return False
    must = _short_acronym_compact_norm(query)
    if not must:
        return False
    for title in _result_titles(item):
        if _compact_norm_for_acronym(title) == must:
            return False
    return True


def _match_rank(query: str, item: dict) -> tuple[int, int]:
    """
    Sort key (tier, tie_break) for picking the best TMDb search hit — lexicographic **max** wins.
    ``tie_break`` is ``-len(normalized_title)`` so **shorter** titles win when tier ties
    (e.g. "Luck" over "Good Luck, Have Fun, Don't Die" for query ``luck``).

    TV queries that map to ``_SHORT_SHOW_CANONICAL_QUERIES`` are pre-filtered so every candidate’s
    title **equals** the canonical series name (normalized), or that name plus **numeric-only** suffix
    tokens (TMDb’s ``(1975)`` style); those hits are not chosen on loose substring strength alone.

    Tiers (highest first):
      5 — normalized title **equals** query (exact)
      4 — multi-word query appears as **consecutive** whole tokens in the title
      3 — every query token appears as a **whole word** in the title
      2 — normalized query substring of normalized title (fuzzy)
      1 — every query token appears as substring in some title token
      0 — no match
    """
    nq = _norm_query(query)
    if not nq:
        return (0, 0)
    q_tokens = [t for t in nq.split() if t]
    if not q_tokens:
        return (0, 0)

    best: tuple[int, int] = (0, -(10**9))
    for title in _result_titles(item):
        nt = _norm_query(title)
        if not nt:
            continue
        t_tokens = [t for t in nt.split() if t]
        t_set = set(t_tokens)
        neg_len = -len(nt)

        tier = 0
        if nt == nq:
            tier = 5
        elif len(q_tokens) >= 2 and _tokens_subsequence(q_tokens, t_tokens):
            tier = 4
        elif all(qt in t_set for qt in q_tokens):
            tier = 3
        elif nq in nt:
            tier = 2
        elif all(any(qt in tw for tw in t_tokens) for qt in q_tokens):
            tier = 1

        cand = (tier, neg_len)
        if cand > best:
            best = cand
    return best


def _sanitize_api_secret(raw: str | bytes) -> str:
    """Strip BOM, whitespace (incl. U+202F), and non-ASCII from pasted API keys/tokens."""
    if not raw:
        return ""
    if isinstance(raw, bytes):
        return bytes(b for b in raw if 32 <= b < 127).decode("ascii")
    cleaned: list[str] = []
    for ch in raw.lstrip("\ufeff"):
        if ch.isspace():
            continue
        if ord(ch) < 128:
            cleaned.append(ch)
    return "".join(cleaned)


def _minimal_subprocess_env() -> dict[str, str]:
    keep = (
        "PATH",
        "HOME",
        "USER",
        "LANG",
        "LC_ALL",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    )
    out: dict[str, str] = {}
    for key in keep:
        val = os.environ.get(key)
        if val:
            out[key] = "".join(ch for ch in str(val) if 32 <= ord(ch) < 127)
    if "PATH" not in out:
        out["PATH"] = "/usr/local/bin:/usr/bin:/bin"
    if "HOME" not in out:
        try:
            out["HOME"] = str(Path.home())
        except RuntimeError:
            pass
    return out


def _http_get_bytes(url: str, *, timeout_s: float = 60.0, headers: dict[str, str] | None = None) -> bytes:
    """HTTPS GET for TMDb API and image CDN (curl on Linux for reliable Pi SSL)."""
    try:
        from pigeon.source_toggles import source_enabled

        if not source_enabled("wifi"):
            raise OSError("Pigeon is offline (Wi-Fi source is off).")
    except ImportError:
        pass
    safe_headers = {
        str(k): "".join(ch for ch in str(v) if ord(ch) < 256) for k, v in (headers or {}).items()
    }
    if sys.platform.startswith("linux"):
        curl = shutil.which("curl")
        if not curl:
            raise OSError("curl is required for TMDb downloads on Linux.")
        cmd = [curl, "-fsSL", "--max-time", str(max(1, int(timeout_s)))]
        for hk, hv in safe_headers.items():
            if hk and hv is not None:
                cmd.extend(["-H", f"{hk}: {hv}"])
        cmd.append(url)
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            env=_minimal_subprocess_env(),
        )
        if proc.returncode == 0 and proc.stdout:
            return proc.stdout
        err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace").strip()
        raise OSError(err or f"curl exited {proc.returncode}")

    req = urllib.request.Request(url, headers=safe_headers or {"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


def load_tmdb_api_key() -> str | None:
    k = _sanitize_api_secret(os.environ.get("PIGEON_TMDB_API_KEY", ""))
    if k:
        return k
    p = _pigeon_state_dir() / "tmdb_api_key"
    if p.is_file():
        try:
            return _sanitize_api_secret(p.read_bytes()) or None
        except OSError:
            return None
    return None


def load_tmdb_read_token() -> str | None:
    t = _sanitize_api_secret(os.environ.get("PIGEON_TMDB_READ_TOKEN", ""))
    if t:
        return t
    p = _pigeon_state_dir() / "tmdb_read_token"
    if p.is_file():
        try:
            return _sanitize_api_secret(p.read_bytes()) or None
        except OSError:
            return None
    return None


def tmdb_is_configured() -> bool:
    """True when a TMDb API key or read token is available."""
    return bool(load_tmdb_read_token() or load_tmdb_api_key())


def _request_json(url: str) -> dict:
    headers = {"User-Agent": _UA}
    token = load_tmdb_read_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        api_key = load_tmdb_api_key()
        if not api_key:
            raise RuntimeError(
                "TMDb not configured. Set PIGEON_TMDB_READ_TOKEN or PIGEON_TMDB_API_KEY, "
                "or create ~/.pigeon_0_6/tmdb_read_token or tmdb_api_key (single line)."
            )
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urllib.parse.urlencode({'api_key': api_key})}"

    body = _http_get_bytes(url, timeout_s=45.0, headers=headers)
    return json.loads(body.decode("utf-8"))


def _download_binary(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _http_get_bytes(url, timeout_s=60.0, headers={"User-Agent": _UA})
    dest.write_bytes(data)


def _best_with_poster_from_results(
    query: str,
    results: list,
    *,
    tv_title_must_equal_norm: str | None = None,
    forgiving: bool = True,
    kids_bias: bool = False,
) -> dict | None:
    if not isinstance(results, list) or not results:
        return None
    with_poster = [r for r in results if isinstance(r, dict) and r.get("poster_path")]
    if not with_poster:
        return None
    if tv_title_must_equal_norm:
        with_poster = [
            r
            for r in with_poster
            if any(
                _title_norm_matches_exact_tv_series_filter(tv_title_must_equal_norm, _norm_query(t))
                for t in _result_titles(r)
            )
        ]
        if not with_poster:
            return None
    # Prefer exact / whole-word title matches from the real search query; tie-break by
    # shorter title, then English original_language, then TMDb popularity. Fall back to
    # the full pool only if every rank is 0.
    scored = [(r, _match_rank(query, r)) for r in with_poster]
    scored = [(r, rk) for r, rk in scored if not _weak_short_acronym_match(query, r, rk)]
    scored = _apply_kids_service_result_bias(scored, kids_bias=kids_bias)
    if not scored:
        return None
    if forgiving:
        best_key = max(rank for _, rank in scored)
        pool = [r for r, rank in scored if rank == best_key] if best_key[0] > 0 else [r for r, _ in scored]
    else:
        min_tier = _literal_min_acceptable_tier(query)
        strict = [(r, rk) for r, rk in scored if rk[0] >= min_tier]
        if not strict:
            return None
        best_key = max(rk for _, rk in strict)
        pool = [r for r, rk in strict if rk == best_key]
    return max(pool, key=_english_prefer_popularity_key)


def _best_from_results(
    query: str,
    results: list,
    *,
    tv_title_must_equal_norm: str | None = None,
    forgiving: bool = True,
    kids_bias: bool = False,
) -> dict | None:
    """Best title match among dict results (no poster requirement); English originals preferred."""
    if not isinstance(results, list) or not results:
        return None
    items = [r for r in results if isinstance(r, dict)]
    if not items:
        return None
    if tv_title_must_equal_norm:
        items = [
            r
            for r in items
            if any(
                _title_norm_matches_exact_tv_series_filter(tv_title_must_equal_norm, _norm_query(t))
                for t in _result_titles(r)
            )
        ]
        if not items:
            return None
    scored = [(r, _match_rank(query, r)) for r in items]
    scored = [(r, rk) for r, rk in scored if not _weak_short_acronym_match(query, r, rk)]
    scored = _apply_kids_service_result_bias(scored, kids_bias=kids_bias)
    if not scored:
        return None
    if forgiving:
        best_key = max(rank for _, rank in scored)
        pool = [r for r, rank in scored if rank == best_key] if best_key[0] > 0 else [r for r, _ in scored]
    else:
        min_tier = _literal_min_acceptable_tier(query)
        strict = [(r, rk) for r, rk in scored if rk[0] >= min_tier]
        if not strict:
            return None
        best_key = max(rk for _, rk in strict)
        pool = [r for r, rk in strict if rk == best_key]
    return max(pool, key=_english_prefer_popularity_key)


def _tmdb_search_get(endpoint: str, query: str, *, year_param: str | None, year: int | None) -> list:
    """GET ``{endpoint}?query=…&{year_param}=…`` and return ``results`` (empty list on any failure).

    Centralizes the "request + pull out results" dance used by the four public search helpers so
    the two-pass "with year filter, then retry without" logic doesn't repeat the HTTP boilerplate.

    Always requests ``language=en-US`` so titles / default ``poster_path`` prefer English when
    TMDb has a translation — this does not filter out non-English originals.
    """
    params: dict[str, str] = {"query": query, "language": TMDB_UI_LANGUAGE}
    if year is not None and year_param:
        params[year_param] = str(int(year))
    url = f"{TMDB_API_BASE}/{endpoint}?{urllib.parse.urlencode(params)}"
    data = _request_json(url)
    results = data.get("results") if isinstance(data, dict) else None
    return results if isinstance(results, list) else []


def _is_english_original(item: dict) -> bool:
    return str((item or {}).get("original_language") or "").strip().lower() == TMDB_PRIMARY_ISO_639


def _english_prefer_popularity_key(item: dict) -> tuple[int, float]:
    """Tie-break: English originals first, then TMDb popularity. Never excludes non-English."""
    return (1 if _is_english_original(item) else 0, float((item or {}).get("popularity") or 0.0))


def _image_iso_639(item: dict) -> str:
    """Normalized ``iso_639_1`` (``""`` = language-neutral / missing)."""
    iso = (item or {}).get("iso_639_1")
    if iso is None:
        return ""
    return str(iso).strip().lower()


def _prefer_english_image_rows(rows: list[dict]) -> list[dict]:
    """English → language-neutral → any other language (last resort)."""
    if not rows:
        return rows
    en = [r for r in rows if _image_iso_639(r) == TMDB_PRIMARY_ISO_639]
    if en:
        return en
    neutral = [r for r in rows if _image_iso_639(r) == ""]
    if neutral:
        return neutral
    return rows


def search_movie_best_with_poster(
    query: str,
    *,
    forgiving: bool | None = None,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> dict | None:
    """Return one TMDb movie dict (has poster_path) or None.

    Trailing ``(YYYY)`` on the query is peeled off and sent as ``primary_release_year`` because
    TMDb's ``query`` param does not recognize parenthetical years — leaving them in returns zero
    hits. Falls back to an unfiltered search if the year-filtered pass comes up empty, so
    slightly-off reported years (imports, re-releases) still resolve to the real title.

    ``rank_query`` (optional) scores hits against a shorter title when ``query`` was augmented
    with a streaming-service hint (e.g. search ``Secret Tunnels PBS Kids``, rank ``Secret Tunnels``).
    """
    q = query.strip()
    if not q:
        return None
    fg = tmdb_match_forgiving(override=forgiving)
    q_clean, year = split_query_and_year(q)
    rq = (rank_query or q_clean).strip() or q_clean
    rq_clean, _ = split_query_and_year(rq)
    rq_clean = rq_clean or rq
    results = _tmdb_search_get("search/movie", q_clean, year_param="primary_release_year", year=year)
    hit = _best_with_poster_from_results(
        rq_clean, results, forgiving=fg, kids_bias=kids_bias
    )
    if hit is None and year is not None:
        results = _tmdb_search_get("search/movie", q_clean, year_param=None, year=None)
        hit = _best_with_poster_from_results(
            rq_clean, results, forgiving=fg, kids_bias=kids_bias
        )
    return hit


def search_movie_best(
    query: str,
    *,
    forgiving: bool | None = None,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> dict | None:
    q = query.strip()
    if not q:
        return None
    fg = tmdb_match_forgiving(override=forgiving)
    q_clean, year = split_query_and_year(q)
    rq = (rank_query or q_clean).strip() or q_clean
    rq_clean, _ = split_query_and_year(rq)
    rq_clean = rq_clean or rq
    results = _tmdb_search_get("search/movie", q_clean, year_param="primary_release_year", year=year)
    hit = _best_from_results(rq_clean, results, forgiving=fg, kids_bias=kids_bias)
    if hit is None and year is not None:
        results = _tmdb_search_get("search/movie", q_clean, year_param=None, year=None)
        hit = _best_from_results(rq_clean, results, forgiving=fg, kids_bias=kids_bias)
    return hit


def search_tv_best(
    query: str,
    *,
    forgiving: bool | None = None,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> dict | None:
    q = query.strip()
    if not q:
        return None
    fg = tmdb_match_forgiving(override=forgiving)
    q_clean, year = split_query_and_year(q)
    rq = (rank_query or q_clean).strip() or q_clean
    rq_clean, _ = split_query_and_year(rq)
    rq_clean = rq_clean or rq
    en = _exact_tv_title_norm_for_known_series_query(rq_clean) if fg else None
    results = _tmdb_search_get("search/tv", q_clean, year_param="first_air_date_year", year=year)
    hit = _best_from_results(
        rq_clean, results, tv_title_must_equal_norm=en, forgiving=fg, kids_bias=kids_bias
    )
    if hit is None and year is not None:
        results = _tmdb_search_get("search/tv", q_clean, year_param=None, year=None)
        hit = _best_from_results(
            rq_clean, results, tv_title_must_equal_norm=en, forgiving=fg, kids_bias=kids_bias
        )
    return hit


def search_tv_best_with_poster(
    query: str,
    *,
    forgiving: bool | None = None,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> dict | None:
    """Return one TMDb TV result (has poster_path) or None.

    See :func:`search_movie_best_with_poster` for the year-peel rationale; TV uses
    ``first_air_date_year`` as the filter param.
    """
    q = query.strip()
    if not q:
        return None
    fg = tmdb_match_forgiving(override=forgiving)
    q_clean, year = split_query_and_year(q)
    rq = (rank_query or q_clean).strip() or q_clean
    rq_clean, _ = split_query_and_year(rq)
    rq_clean = rq_clean or rq
    en = _exact_tv_title_norm_for_known_series_query(rq_clean) if fg else None
    results = _tmdb_search_get("search/tv", q_clean, year_param="first_air_date_year", year=year)
    hit = _best_with_poster_from_results(
        rq_clean, results, tv_title_must_equal_norm=en, forgiving=fg, kids_bias=kids_bias
    )
    if hit is None and year is not None:
        results = _tmdb_search_get("search/tv", q_clean, year_param=None, year=None)
        hit = _best_with_poster_from_results(
            rq_clean, results, tv_title_must_equal_norm=en, forgiving=fg, kids_bias=kids_bias
        )
    return hit


def _tmdb_tv_detail(tv_id: int) -> dict | None:
    try:
        data = _request_json(
            f"{TMDB_API_BASE}/tv/{int(tv_id)}?{urllib.parse.urlencode({'language': TMDB_UI_LANGUAGE})}"
        )
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("id") is None:
        return None
    return data


def _tmdb_movie_detail(movie_id: int) -> dict | None:
    try:
        data = _request_json(
            f"{TMDB_API_BASE}/movie/{int(movie_id)}?{urllib.parse.urlencode({'language': TMDB_UI_LANGUAGE})}"
        )
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("id") is None:
        return None
    return data


_KNOWN_TMDB_MOVIE_ID_BY_COMPACT_NORM: dict[str, int] = {
    "walle": 10681,
}


def _forced_tmdb_movie_id_for_disambiguated_query(q: str) -> int | None:
    """
    TMDb search often picks the wrong row when many titles share ``Taylor Swift`` tokens
    (e.g. *Journey to Fearless* over *The Eras Tour*). Map full queries to the known movie ids.
    """
    must = _short_acronym_compact_norm(q)
    if must:
        known = _KNOWN_TMDB_MOVIE_ID_BY_COMPACT_NORM.get(must)
        if known is not None:
            return known
    cn = _compact_norm_for_acronym(q)
    if cn:
        known = _KNOWN_TMDB_MOVIE_ID_BY_COMPACT_NORM.get(cn)
        if known is not None:
            return known
    n = _norm_query(q)
    if not n:
        return None
    if "taylor" not in n or "swift" not in n:
        return None
    if "eras" not in n or "tour" not in n:
        return None
    # The Final Show (2025, Vancouver) vs theatrical Eras Tour (2023)
    if "final" in n:
        return 1562010
    return 1160164


def _forced_tmdb_movie_item_for_disambiguated_query(q: str, *, require_poster: bool) -> dict | None:
    mid = _forced_tmdb_movie_id_for_disambiguated_query(q)
    if mid is None:
        return None
    detail = _tmdb_movie_detail(mid)
    if detail is None:
        return None
    if require_poster and not detail.get("poster_path"):
        return None
    return detail


def _forced_tmdb_tv_item_for_canonical_query(q: str, *, require_poster: bool) -> dict | None:
    """
    For queries that map to :data:`_SHORT_SHOW_CANONICAL_QUERIES`, return the fixed TMDb TV row from
    ``/tv/{id}`` when listed in :data:`_SHORT_SHOW_TMDB_TV_ID_BY_NORM`.

    Holiday / clip compilations for the same franchise are often **movies** on TMDb; Apple TV often
    reports them as ``Video`` so ``prefer`` becomes ``movie`` and would otherwise beat the series.
    """
    en = _exact_tv_title_norm_for_known_series_query(q)
    if en is None:
        return None
    tv_id = _SHORT_SHOW_TMDB_TV_ID_BY_NORM.get(en)
    if tv_id is None:
        return None
    detail = _tmdb_tv_detail(tv_id)
    if detail is None:
        return None
    if require_poster and not detail.get("poster_path"):
        return None
    return detail


def _service_augmented_search_queries(
    query: str,
    *,
    service_hint: str | None,
    forgiving: bool,
    service_first: bool = False,
) -> list[str]:
    """
    Ordered TMDb search strings.

    When ``service_first`` (kids apps), try ``Title PBS Kids`` before the bare title so the
    API surface leans toward that service. For other apps the service suffix is a fallback
    only, so normal title matching stays primary.
    """
    raw = (query or "").strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(s: str) -> None:
        t = s.strip()
        if not t:
            return
        key = t.casefold()
        if key in seen:
            return
        seen.add(key)
        out.append(t)

    sh = (service_hint or "").strip()
    svc_q = ""
    if sh and _norm_query(sh) and _norm_query(sh) not in _norm_query(raw):
        svc_q = f"{raw} {sh}"

    if service_first and svc_q:
        add(svc_q)
    if forgiving:
        for v in _tmdb_query_variants(raw):
            add(v)
    else:
        add(raw)
    if svc_q:
        add(svc_q)
    return out


def _search_best_media_with_poster_one(
    q: str,
    *,
    prefer: Prefer,
    forgiving: bool,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> tuple[dict | None, MediaKind | None]:
    if not q:
        return None, None
    rq = (rank_query or q).strip() or q
    if forgiving:
        en = _exact_tv_title_norm_for_known_series_query(rq)
        if en is not None:
            hit = _forced_tmdb_tv_item_for_canonical_query(rq, require_poster=True)
            if hit is None:
                hit = search_tv_best_with_poster(
                    q, forgiving=True, rank_query=rq, kids_bias=kids_bias
                )
            if hit is not None:
                return hit, "tv"
            return None, None
    if prefer == "movie":
        m = search_movie_best_with_poster(
            q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
        )
        return (m, "movie") if m else (None, None)
    if prefer == "tv":
        t = search_tv_best_with_poster(
            q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
        )
        return (t, "tv") if t else (None, None)

    # auto: prefer the movie catalogue when it yields a hit; if no film matches, try TV.
    m = search_movie_best_with_poster(
        q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
    )
    if m is not None:
        return m, "movie"
    t = search_tv_best_with_poster(
        q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
    )
    return (t, "tv") if t else (None, None)


def _episode_title_series_fallback(
    query: str,
    *,
    require_poster: bool,
) -> tuple[dict | None, MediaKind | None]:
    """
    Map an episode-only title to its parent series (system-wide, not kids-only).

    Order:
      1. Disk-cached kids/PBS episode index (same data PBS Kids uses)
      2. Local ``episode_series_hints.json`` / built-ins → TMDb TV search for that series
      3. Wikidata unambiguous episode → series (Peacock / general streamers)

    Used when normal TMDb title search misses or only yields a weak title match.
    """
    q = (query or "").strip()
    if not q:
        return None, None
    try:
        from pigeon.tmdb_episode_index import resolve_kids_series_from_episode_title

        detail = resolve_kids_series_from_episode_title(q)
    except Exception:
        detail = None
    if detail is not None and detail.get("id") is not None:
        if not require_poster or detail.get("poster_path"):
            return detail, "tv"

    hinted: str | None = None
    try:
        from pigeon.episode_series_hints import series_name_for_episode_title_hint

        hinted = series_name_for_episode_title_hint(q)
    except Exception:
        hinted = None
    if not hinted:
        try:
            from pigeon.wikidata_episode import series_name_from_wikidata_episode_title

            hinted = series_name_from_wikidata_episode_title(q)
        except Exception:
            hinted = None
    if hinted and not is_degenerate_tmdb_query(hinted):
        if require_poster:
            hit = search_tv_best_with_poster(hinted, forgiving=True, kids_bias=False)
        else:
            hit = search_tv_best(hinted, forgiving=True, kids_bias=False)
        if hit is not None and hit.get("id") is not None:
            if not require_poster or hit.get("poster_path"):
                return hit, "tv"
    return None, None


# Back-compat alias (older call sites / docs).
_kids_episode_index_series_fallback = _episode_title_series_fallback


def _tmdb_hit_is_weak_for_query(
    query: str, item: dict | None, *, require_poster: bool = False
) -> bool:
    """True when there is no hit, missing poster (if required), or match tier &lt; 4."""
    if item is None:
        return True
    if require_poster and not item.get("poster_path"):
        return True
    return _match_rank(query, item)[0] < 4


def search_best_media_with_poster(
    query: str,
    *,
    prefer: Prefer = "auto",
    forgiving: bool | None = None,
    service_hint: str | None = None,
    kids_bias: bool = False,
    app_name: str | None = None,
    app_id: str | None = None,
) -> tuple[dict | None, MediaKind | None]:
    """
    Pick one movie or TV hit with a poster.
    ``auto`` takes the best movie result first; only if none is found uses the TV catalogue.

    When ``app_name`` / ``app_id`` (or ``service_hint`` / ``kids_bias``) indicate a kids
    streaming app, prefer TV and bias ranking away from adult substring matches.

    When search still fails (or only a weak hit), resolve episode-only titles via the
    episode→series index (same path PBS Kids uses) — **system-wide**, not kids-only.
    """
    fg = tmdb_match_forgiving(override=forgiving)
    raw = (query or "").strip()
    hint = (service_hint or "").strip() or None
    if hint is None and (app_name or app_id):
        hint = streaming_service_display_name(app_name, app_id)
    kids = bool(kids_bias) or is_kids_streaming_service(app_name, app_id)
    pref = prefer_media_for_streaming_service(prefer, app_name=app_name, app_id=app_id)
    if raw and not kids:
        mf = _forced_tmdb_movie_item_for_disambiguated_query(raw, require_poster=True)
        if mf is not None:
            return mf, "movie"
    variants = _service_augmented_search_queries(
        raw, service_hint=hint, forgiving=fg, service_first=kids
    )
    best: tuple[dict | None, MediaKind | None] = (None, None)
    for q in variants:
        hit = _search_best_media_with_poster_one(
            q, prefer=pref, forgiving=fg, rank_query=raw, kids_bias=kids
        )
        if hit[0] is not None:
            best = hit
            break
    if _tmdb_hit_is_weak_for_query(raw, best[0], require_poster=True):
        ep = _episode_title_series_fallback(raw, require_poster=True)
        if ep[0] is not None:
            return ep
    return best


def search_best_media(
    query: str,
    *,
    prefer: Prefer = "auto",
    forgiving: bool | None = None,
    service_hint: str | None = None,
    kids_bias: bool = False,
    app_name: str | None = None,
    app_id: str | None = None,
) -> tuple[dict | None, MediaKind | None]:
    """Pick one movie or TV hit; ``auto`` tries movies first, then TV if no movie match.

    Episode-only titles fall back to the episode→series index when search misses or only
    returns a weak title alignment (system-wide; not limited to kids apps).
    """
    fg = tmdb_match_forgiving(override=forgiving)
    raw = (query or "").strip()
    hint = (service_hint or "").strip() or None
    if hint is None and (app_name or app_id):
        hint = streaming_service_display_name(app_name, app_id)
    kids = bool(kids_bias) or is_kids_streaming_service(app_name, app_id)
    pref = prefer_media_for_streaming_service(prefer, app_name=app_name, app_id=app_id)
    if raw and not kids:
        mf = _forced_tmdb_movie_item_for_disambiguated_query(raw, require_poster=False)
        if mf is not None:
            return mf, "movie"
    variants = _service_augmented_search_queries(
        raw, service_hint=hint, forgiving=fg, service_first=kids
    )
    best: tuple[dict | None, MediaKind | None] = (None, None)
    for q in variants:
        hit = _search_best_media_one(
            q, prefer=pref, forgiving=fg, rank_query=raw, kids_bias=kids
        )
        if hit[0] is not None:
            best = hit
            break
    if _tmdb_hit_is_weak_for_query(raw, best[0]):
        ep = _episode_title_series_fallback(raw, require_poster=False)
        if ep[0] is not None:
            return ep
    return best


def _search_best_media_one(
    q: str,
    *,
    prefer: Prefer,
    forgiving: bool,
    rank_query: str | None = None,
    kids_bias: bool = False,
) -> tuple[dict | None, MediaKind | None]:
    if not q:
        return None, None
    rq = (rank_query or q).strip() or q
    if forgiving:
        en = _exact_tv_title_norm_for_known_series_query(rq)
        if en is not None:
            hit = _forced_tmdb_tv_item_for_canonical_query(rq, require_poster=False)
            if hit is None:
                hit = search_tv_best(
                    q, forgiving=True, rank_query=rq, kids_bias=kids_bias
                )
            if hit is not None:
                return hit, "tv"
            return None, None
    if prefer == "movie":
        m = search_movie_best(
            q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
        )
        return (m, "movie") if m else (None, None)
    if prefer == "tv":
        t = search_tv_best(
            q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
        )
        return (t, "tv") if t else (None, None)

    # auto: movie catalogue first, then TV when no film hit (same policy as with-poster path).
    m = search_movie_best(
        q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
    )
    if m is not None:
        return m, "movie"
    t = search_tv_best(
        q, forgiving=forgiving, rank_query=rq, kids_bias=kids_bias
    )
    return (t, "tv") if t else (None, None)


def fetch_media_images(kind: MediaKind, media_id: int) -> dict:
    """Fetch image lists in all languages; callers prefer English in code.

    Do not pass ``language=en-US`` here — TMDb then returns only English-tagged images
    and drops Spanish-only posters needed as last-resort fallbacks.
    """
    if kind == "movie":
        url = f"{TMDB_API_BASE}/movie/{int(media_id)}/images"
    else:
        url = f"{TMDB_API_BASE}/tv/{int(media_id)}/images"
    return _request_json(url)


# title_key → [(actor, character), ...] for view_circles cast row.
_CAST_CACHE: dict[str, list[tuple[str, str]]] = {}


def get_cached_tmdb_cast(title_key_s: str) -> list[tuple[str, str]]:
    """Return top cast cached for a reformatted-media title key (may be empty).

    Also tries a year-stripped alias so Apple TV ``Title (YYYY)`` keys still hit
    cast cached under the clean TMDb display name.
    """
    tk = str(title_key_s or "").strip()
    if not tk:
        return []
    hit = _CAST_CACHE.get(tk)
    if hit:
        return list(hit)
    cleaned, _year = split_query_and_year(tk)
    cleaned = (cleaned or "").strip()
    if cleaned and cleaned != tk:
        hit = _CAST_CACHE.get(cleaned)
        if hit:
            return list(hit)
    return []


def clear_cached_tmdb_cast(title_key_s: str | None = None) -> None:
    if title_key_s is None:
        _CAST_CACHE.clear()
        return
    _CAST_CACHE.pop(str(title_key_s).strip(), None)


def _character_from_cast_entry(entry: dict) -> str:
    roles = entry.get("roles")
    if isinstance(roles, list) and roles:
        chars: list[str] = []
        for role in roles:
            if not isinstance(role, dict):
                continue
            ch = str(role.get("character") or "").strip()
            if ch:
                chars.append(ch)
        if chars:
            return chars[0]
    return str(entry.get("character") or "").strip()


def fetch_top_cast(kind: MediaKind, media_id: int, *, limit: int = 9) -> list[tuple[str, str]]:
    """
    Top billed cast as ``(actor_name, character_name)``.

    Movies use ``/credits``. TV prefers ``/aggregate_credits``, then ``/credits``.
    """
    lim = max(0, int(limit))
    if lim <= 0:
        return []
    mid = int(media_id)
    cast_rows: list[dict] = []
    if kind == "tv":
        try:
            data = _request_json(f"{TMDB_API_BASE}/tv/{mid}/aggregate_credits")
            rows = data.get("cast") or []
            if isinstance(rows, list):
                cast_rows = [r for r in rows if isinstance(r, dict)]
        except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
            cast_rows = []
        if not cast_rows:
            try:
                data = _request_json(f"{TMDB_API_BASE}/tv/{mid}/credits")
                rows = data.get("cast") or []
                if isinstance(rows, list):
                    cast_rows = [r for r in rows if isinstance(r, dict)]
            except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
                cast_rows = []
    else:
        try:
            data = _request_json(f"{TMDB_API_BASE}/movie/{mid}/credits")
            rows = data.get("cast") or []
            if isinstance(rows, list):
                cast_rows = [r for r in rows if isinstance(r, dict)]
        except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
            cast_rows = []

    def _order_key(row: dict) -> tuple[int, int]:
        try:
            order = int(row.get("order") if row.get("order") is not None else 10_000)
        except (TypeError, ValueError):
            order = 10_000
        roles = row.get("roles")
        role_n = len(roles) if isinstance(roles, list) else 0
        return (order, -role_n)

    cast_rows.sort(key=_order_key)
    out: list[tuple[str, str]] = []
    for row in cast_rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        character = _character_from_cast_entry(row)
        out.append((name, character))
        if len(out) >= lim:
            break
    return out


def cache_tmdb_cast_for_title(title_key_s: str, cast: list[tuple[str, str]]) -> None:
    tk = str(title_key_s or "").strip()
    if not tk:
        return
    rows = [(str(a or ""), str(c or "")) for a, c in cast[:9]]
    # Bound the cache: long sessions cycle through many titles; evict oldest.
    while len(_CAST_CACHE) >= 64:
        _CAST_CACHE.pop(next(iter(_CAST_CACHE)))
    _CAST_CACHE[tk] = rows
    cleaned, _year = split_query_and_year(tk)
    cleaned = (cleaned or "").strip()
    if cleaned and cleaned != tk:
        _CAST_CACHE[cleaned] = list(rows)


def _logo_path_from_images(images: dict) -> str | None:
    logos = images.get("logos") or []
    if not logos:
        return None

    # Always prefer English logos. If none exist, treat as no logo (do not fall back).
    en = [l for l in logos if _image_iso_639(l) == TMDB_PRIMARY_ISO_639]
    if not en:
        return None
    best = max(en, key=lambda l: float((l or {}).get("vote_average") or 0.0))
    return (best or {}).get("file_path")  # type: ignore[return-value]


def _backdrop_is_no_language(item: dict) -> bool:
    """TMDb uses ``iso_639_1: null`` (and sometimes missing/empty) for language-neutral backdrops."""
    iso = item.get("iso_639_1")
    if iso is None:
        return True
    if isinstance(iso, str) and iso.strip() == "":
        return True
    return False


def _random_backdrop_path(images: dict) -> str | None:
    backs = images.get("backdrops") or []
    neutral = [
        b
        for b in backs
        if isinstance(b, dict) and _backdrop_is_no_language(b) and b.get("file_path")
    ]
    if not neutral:
        return None
    choice = random.choice(neutral)
    return choice.get("file_path")


def _poster_path_from_item_or_images(item: dict, images: dict) -> str | None:
    """Prefer English poster art; fall back to neutral, then any language, then ``poster_path``.

    ``item.poster_path`` is TMDb's default for the request language and can still be a
    non-English asset when that is the only upload — use the images list first so an
    English alternate wins when it exists.
    """
    posters = images.get("posters") or []
    rows: list[dict] = []
    if isinstance(posters, list):
        rows = [r for r in posters if isinstance(r, dict) and r.get("file_path")]
    if rows:
        pool = _prefer_english_image_rows(rows)
        best = max(pool, key=lambda r: float((r or {}).get("vote_average") or 0.0))
        path = str((best or {}).get("file_path") or "").strip()
        if path:
            return path
    p = str(item.get("poster_path") or "").strip()
    return p or None


def _maybe_delete_pulled(path: Path) -> None:
    if pulled_path_is_under_pulled_dir(path) and auto_delete_pulled_media():
        try:
            path.unlink()
        except OSError:
            pass


def _display_title(item: dict, kind: MediaKind) -> str:
    if kind == "movie":
        return str(item.get("title") or item.get("original_title") or "movie")
    return str(item.get("name") or item.get("original_name") or "TV")


def download_poster_to_pulled(item: dict, kind: MediaKind) -> tuple[bool, str, Path | None]:
    """
    Save poster under pigeonPulledMedia as ``tmdb_m_<id>`` or ``tmdb_tv_<id>`` (IDs differ by media type).
    """
    mid = item.get("id")
    ppath = item.get("poster_path")
    title = _display_title(item, kind)
    if mid is None or not ppath:
        return False, "TMDb result missing id or poster_path.", None
    ext = Path(str(ppath)).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    tag = "m" if kind == "movie" else "tv"
    pulled = pigeon_pulled_media_dir()
    dest = pulled / f"tmdb_{tag}_{int(mid)}{ext}"
    image_url = f"{IMG_BASE}{ppath}"
    try:
        _download_binary(image_url, dest)
    except urllib.error.HTTPError as e:
        return False, f"Poster download failed ({e.code}): {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Poster download failed: {e.reason}", None
    except OSError as e:
        return False, f"Could not save poster: {e}", None
    kind_label = "movie" if kind == "movie" else "TV"
    return True, f"{title} ({kind_label}) → {dest.name}", dest


def download_logo_to_pulled(item: dict, kind: MediaKind, file_path: str) -> tuple[bool, str, Path | None]:
    mid = item.get("id")
    if mid is None or not file_path:
        return False, "TMDb logo path missing.", None
    ext = Path(str(file_path)).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".svg"):
        ext = ".png"
    tag = "m" if kind == "movie" else "tv"
    dest = pigeon_pulled_media_dir() / f"tmdb_logo_{tag}_{int(mid)}{ext}"
    image_url = f"{IMG_LOGO_BASE}{file_path}"
    try:
        _download_binary(image_url, dest)
    except urllib.error.HTTPError as e:
        return False, f"Logo download failed ({e.code}): {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Logo download failed: {e.reason}", None
    except OSError as e:
        return False, f"Could not save logo: {e}", None
    return True, dest.name, dest


def download_backdrop_to_pulled(item: dict, kind: MediaKind, file_path: str) -> tuple[bool, str, Path | None]:
    mid = item.get("id")
    if mid is None or not file_path:
        return False, "TMDb backdrop path missing.", None
    ext = Path(str(file_path)).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    tag = "m" if kind == "movie" else "tv"
    rand = secrets.token_hex(4)
    dest = pigeon_pulled_media_dir() / f"tmdb_bd_{tag}_{int(mid)}_{rand}{ext}"
    image_url = f"{IMG_BACKDROP_BASE}{file_path}"
    try:
        _download_binary(image_url, dest)
    except urllib.error.HTTPError as e:
        return False, f"Backdrop download failed ({e.code}): {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"Backdrop download failed: {e.reason}", None
    except OSError as e:
        return False, f"Could not save backdrop: {e}", None
    return True, dest.name, dest


def _resolve_preferred_poster_path(item: dict, kind: MediaKind) -> str | None:
    """English-first poster path for a search/detail hit (images when available)."""
    try:
        images = fetch_media_images(kind, int(item["id"]))
    except (
        RuntimeError,
        urllib.error.HTTPError,
        urllib.error.URLError,
        json.JSONDecodeError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ):
        images = {}
    return _poster_path_from_item_or_images(item, images if isinstance(images, dict) else {})


def fetch_tmdb_poster_to_pulled(
    query: str,
    *,
    prefer: Prefer = "auto",
    forgiving: bool | None = None,
    app_name: str | None = None,
    app_id: str | None = None,
) -> tuple[bool, str, Path | None]:
    """Search TMDb (movie and/or TV) and download best-match poster to pigeonPulledMedia."""
    fg = tmdb_match_forgiving(override=forgiving)
    try:
        item, kind = search_best_media_with_poster(
            query,
            prefer=prefer,
            forgiving=forgiving,
            app_name=app_name,
            app_id=app_id,
        )
    except RuntimeError as e:
        return False, str(e), None
    except urllib.error.HTTPError as e:
        return False, f"TMDb API error ({e.code}): {e.reason}", None
    except urllib.error.URLError as e:
        return False, f"TMDb network error: {e.reason}", None
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return False, str(e), None
    if item is None or kind is None:
        q0 = query.strip()
        variants = _tmdb_query_variants(q0) if fg else ([q0] if q0 else [])
        tried_line = (
            "Variants tried: " + ", ".join(repr(x) for x in variants) + "\n"
            if len(variants) > 1
            else ""
        )
        return (
            False,
            "No movie or TV show found with a poster for that search.\n\n"
            f"Searched: {q0!r}\n{tried_line}\n"
            "Tips: In the command bar use tv Your Show or movie Your Film; use the series or "
            "film title only. If the string included an app, episode name, or a colon "
            "(Show: guest), Pigeon already tried shortened variants.",
            None,
        )
    pp = _resolve_preferred_poster_path(item, kind)
    if pp:
        item = {**item, "poster_path": pp}
    ok_p, msg_p, path_p = download_poster_to_pulled(item, kind)
    if ok_p:
        trim_pulled_media_dir()
    return ok_p, msg_p, path_p


def apply_tmdb_movie_query(
    query: str,
    *,
    prefer: Prefer = "auto",
    forgiving: bool | None = None,
    app_name: str | None = None,
    app_id: str | None = None,
    service_hint: str | None = None,
) -> tuple[bool, str, np.ndarray | None, int]:
    """
    Search TMDb, prefer cached logo when present; pull missing assets and cache as
    ``{Title}_{Logo|Backdrop}`` in pigeonReFormattedMedia.

    Always picks a **random** backdrop from TMDb image results (not served from cache).

    ``app_name`` / ``app_id`` (pyatv) bias search toward the streaming service — kids apps
    append the service name as a query variant, prefer TV, and demote adult substring hits.

    Returns ``(ok, message, backdrop_master_bgr_or_none, match_tier)`` where master is BGR
    scaled to uniform design canvas height for the compositor, or None if no backdrop could be
    loaded. ``match_tier`` is the :func:`_match_rank` tier (0 when no hit).
    """
    q = query.strip()
    if not q:
        return False, "Empty search.", None, 0
    # Protocol: keep ORIGINAL as transient staging only; clear leftovers before each new TMDB pull.
    try:
        ensure_tmdb_media_dirs()
        purge_directory_contents(pigeon_pulled_media_dir())
    except Exception:
        pass
    fg = tmdb_match_forgiving(override=forgiving)
    hint = (service_hint or "").strip() or streaming_service_display_name(app_name, app_id)
    kids = is_kids_streaming_service(app_name, app_id)
    pref = prefer_media_for_streaming_service(prefer, app_name=app_name, app_id=app_id)

    try:
        item, kind = search_best_media(
            q,
            prefer=pref,
            forgiving=forgiving,
            service_hint=hint,
            kids_bias=kids,
            app_name=app_name,
            app_id=app_id,
        )
        if item is None and not fg:
            variants = _service_augmented_search_queries(
                q, service_hint=hint, forgiving=True, service_first=kids
            )
            for v in variants:
                if v.strip().casefold() == q.strip().casefold():
                    continue
                item, kind = search_best_media(
                    v,
                    prefer=pref,
                    forgiving=False,
                    service_hint=None,
                    kids_bias=kids,
                    app_name=app_name,
                    app_id=app_id,
                )
                if item is not None:
                    break
            if item is None:
                for v in variants:
                    item, kind = search_best_media(
                        v,
                        prefer=pref,
                        forgiving=True,
                        service_hint=None,
                        kids_bias=kids,
                        app_name=app_name,
                        app_id=app_id,
                    )
                    if item is not None:
                        break
    except RuntimeError as e:
        return False, str(e), None, 0
    except urllib.error.HTTPError as e:
        return False, f"TMDb API error ({e.code}): {e.reason}", None, 0
    except urllib.error.URLError as e:
        return False, f"TMDb network error: {e.reason}", None, 0
    except (json.JSONDecodeError, OSError, ValueError) as e:
        return False, str(e), None, 0

    if item is None or kind is None:
        variants = _service_augmented_search_queries(
            q, service_hint=hint, forgiving=fg, service_first=kids
        )
        tried_line = (
            "Variants tried: " + ", ".join(repr(x) for x in variants) + "\n"
            if len(variants) > 1
            else ""
        )
        return (
            False,
            "No movie or TV show found for that search.\n\n"
            f"Searched: {q!r}\n{tried_line}\n"
            "Tips: Use tv Your Show or movie Your Film in the command bar; try the main title "
            "only. Apple TV sometimes sends a label TMDb does not recognize (episode titles, apps, "
            "Show: guest lines, or extras). Check spelling and network — API errors show a different message.",
            None,
            0,
        )

    match_tier = int(_match_rank(q, item)[0])

    display_title = _display_title(item, kind)
    # TMDb may classify an SNL sketch row as a **movie**; still normalize the on-screen title.
    swap = canonical_tv_title_if_sketch_show_compound(display_title)
    if swap:
        display_title = swap
    if kind == "tv":
        canon = canonical_tv_display_name_for_search_query(q)
        if canon:
            display_title = canon
    tk = title_key(display_title)
    parts: list[str] = [display_title]

    # --- Cast (enough for two 3-up strips: zone4 + zone5) ---
    try:
        cast = fetch_top_cast(kind, int(item["id"]), limit=9)
        cache_tmdb_cast_for_title(tk, cast)
        if cast:
            parts.append(f"cast: {len(cast)}")
        else:
            parts.append("cast: none")
    except Exception:
        clear_cached_tmdb_cast(tk)
        parts.append("cast: fail")

    # --- Images bundle (logo + random backdrop) ---
    backdrop_master: np.ndarray | None = None
    try:
        images = fetch_media_images(kind, int(item["id"]))
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError, OSError, ValueError):
        images = {}

    # --- Poster: routinely pull/cache alongside TT + BD ---
    pp = _poster_path_from_item_or_images(item, images)
    if not pp:
        parts.append("poster: none")
    else:
        ok_p, _msg_p, p_pulled = download_poster_to_pulled(
            {"id": item.get("id"), "poster_path": pp, "title": display_title, "name": display_title},
            kind,
        )
        if ok_p and p_pulled is not None:
            try:
                copy_pulled_to_reformatted(p_pulled, tk, ASSET_POSTER_ART)
            except OSError as e:
                parts.append(f"poster: cache failed ({e})")
            else:
                parts.append(f"poster: {p_pulled.name}")
            _maybe_delete_pulled(p_pulled)
        else:
            parts.append("poster: download failed")

    # --- Logo (English-only; cache first) ---
    logo_cached = find_cached_reformatted_asset(tk, ASSET_LOGO_EN)
    if logo_cached is not None:
        parts.append("logo: en cache")
    else:
        lp = _logo_path_from_images(images)
        if lp:
            ok_l, _msg_l, logo_path = download_logo_to_pulled(item, kind, lp)
            if ok_l and logo_path is not None:
                try:
                    copy_pulled_to_reformatted(logo_path, tk, ASSET_LOGO_EN)
                    parts.append(f"logo: {logo_path.name}")
                except OSError as e:
                    parts.append(f"logo: copy failed ({e})")
                _maybe_delete_pulled(logo_path)
            else:
                parts.append("logo: skip")
        else:
            parts.append("logo: none")

    # --- Backdrop: always random from API ---
    bp = _random_backdrop_path(images)
    if not bp:
        parts.append("backdrop: none")
    else:
        ok_b, _msg_b, bd_pulled = download_backdrop_to_pulled(item, kind, bp)
        if ok_b and bd_pulled is not None:
            try:
                copy_pulled_to_reformatted(bd_pulled, tk, ASSET_BACKDROP)
            except OSError as e:
                parts.append(f"backdrop: reformatted copy failed ({e})")
            else:
                parts.append(f"backdrop: {bd_pulled.name}")
            backdrop_master = backdrop_master_bgr_from_file(bd_pulled)
            _maybe_delete_pulled(bd_pulled)
        else:
            parts.append("backdrop: download failed")

    summary = " | ".join(parts)
    trim_pulled_media_dir()
    # Prefix title_key + display_title so the UI can render a text fallback when no English logo exists.
    return True, f"{tk}::{display_title}::{summary}", backdrop_master, match_tier
