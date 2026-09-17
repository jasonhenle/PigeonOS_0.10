"""
``rawTitle`` protocol: separate **series title**, **season label**, **episode # label**, and **episode title**
from inconsistent streaming metadata (Apple TV / pyatv, Roku ECP, plist archives).

The only layer Pigeon must get right for TMDb today is **series title**; other layers help parsing
and future UI. Training hints (:mod:`pigeon.series_title_training`) override inferred queries.

**Typical provider ``title`` / compound strings** (heuristics below; see also
:func:`pigeon.tmdb_poster.colon_prefix_show_query` and ``_tmdb_query_variants``):

- **Netflix / Max:** ``Series: Episode`` (colon).
- **Apple TV+:** ``Series — Episode`` (em dash); unicode dashes are normalized for splitting.
- **Disney+ / Paramount+:** ``Series - Episode`` (ASCII hyphen + spaced variants).
- **Prime:** ``Series S01 E01 - Episode`` (:func:`_strip_season_episode_from_text` strips ``SxxEyy``).
- **Hulu:** ``Series (S1:E1) Episode`` (parenthesized season/episode stripped here).
- **Peacock:** ``Series S1 E1: Episode`` (``SxxEyy`` stripped; trailing colon cleaned with whitespace collapse).
- **YouTube:** often a single long video title; no structured split (may need training hints or manual cleanup).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# --- Season / episode patterns in a single free-text line (title or compound string) ---

# Hulu-style: "The Bear (S1:E1) System"
_HULU_PAREN_S_COLON_E = re.compile(
    r"\(\s*S\s*(\d{1,2})\s*:\s*E\s*(\d{1,4})\s*\)",
    re.IGNORECASE | re.UNICODE,
)
# Full-line compound (series name before the parenthesis).
_HULU_COMPOUND_LINE = re.compile(
    r"^(.+?)\s*\(\s*S\s*(\d{1,2})\s*:\s*E\s*(\d{1,4})\s*\)\s*(.*)$",
    re.IGNORECASE | re.UNICODE,
)

_SXXEYY = re.compile(
    # Disney+ uses ``S3:E21`` (colon separator) in ``artist`` — treat it the same as
    # ``S03E21`` / ``S3-E21`` / ``S3.E21`` when stripping from free-text lines.
    r"(?i)\bS(\d{1,2})\s*[\.\-:]?\s*E(\d{1,4})\b",
    re.UNICODE,
)
_NUM_X_NUM = re.compile(r"(?i)\b(\d{1,2})\s*x\s*(\d{1,4})\b", re.UNICODE)
_SEASON_WORD = re.compile(
    r"(?i)\bSeason\s+(\d{1,2})\b(?:\s*,\s*|\s+)(?:Episode\s+)?(\d{1,4})\b",
    re.UNICODE,
)
_EP_WORD = re.compile(r"(?i)\bEp(?:isode)?\.?\s*(\d{1,4})\b", re.UNICODE)


@dataclass
class RawTitle:
    """
    Provider-agnostic view of now-playing text.

    ``raw_*`` are verbatim strings from the device when available.
    ``layer_*`` are best-effort interpretations for display / future logic.
    """

    source: str  # "pyatv" | "roku" | "archive" | "metadata_dict" | "unknown"

    raw_title: str | None = None
    raw_series_name: str | None = None
    raw_artist: str | None = None
    raw_album: str | None = None
    raw_episode_title: str | None = None
    # Pigeon poll snapshot: resolved TMDb search string (optional fingerprint field).
    raw_query: str | None = None

    season_index: int | None = None
    episode_index: int | None = None

    layer_series_title: str | None = None
    layer_series_number: str | None = None  # e.g. "S1"
    layer_episode_number: str | None = None  # e.g. "E3" or "101"
    layer_episode_title: str | None = None

    media_type_label: str | None = None
    notes: list[str] = field(default_factory=list)

    def training_signature_parts(self) -> list[str]:
        """Distinct non-empty raw strings for a stable training key (order handled in normalized)."""
        out: list[str] = []
        seen: set[str] = set()
        for x in (
            self.raw_title,
            self.raw_series_name,
            self.raw_artist,
            self.raw_album,
            self.raw_episode_title,
            self.raw_query,
        ):
            t = (x or "").strip()
            if not t:
                continue
            low = t.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(t)
        return out

    def training_signature_normalized(self) -> str:
        """Stable normalized key for :mod:`pigeon.series_title_training`."""
        from pigeon.tmdb_poster import _norm_query

        parts = sorted(self.training_signature_parts(), key=lambda s: s.lower())
        blob = " | ".join(parts)
        return _norm_query(blob) if blob else ""


def _strip_season_episode_from_text(s: str) -> tuple[str, str | None, str | None]:
    """
    Remove common SxxEyy / 1x02 / Season n Episode m / Hulu (S1:E1) clauses; return cleaned text + labels.
    """
    if not s or not s.strip():
        return "", None, None
    t = s.strip()
    s_lab: str | None = None
    e_lab: str | None = None
    stripped_s_e = False
    m0 = _HULU_PAREN_S_COLON_E.search(t)
    if m0:
        stripped_s_e = True
        s_lab = f"S{int(m0.group(1))}"
        e_lab = f"E{int(m0.group(2))}"
        t = _HULU_PAREN_S_COLON_E.sub(" ", t)
    m = _SXXEYY.search(t)
    if m:
        stripped_s_e = True
        s_lab = f"S{int(m.group(1))}"
        e_lab = f"E{int(m.group(2))}"
        t = _SXXEYY.sub(" ", t)
    else:
        m2 = _NUM_X_NUM.search(t)
        if m2:
            stripped_s_e = True
            s_lab = f"S{int(m2.group(1))}"
            e_lab = f"E{int(m2.group(2))}"
            t = _NUM_X_NUM.sub(" ", t)
        else:
            m3 = _SEASON_WORD.search(t)
            if m3:
                stripped_s_e = True
                s_lab = f"S{int(m3.group(1))}"
                e_lab = f"E{int(m3.group(2))}"
                t = _SEASON_WORD.sub(" ", t)
    m4 = _EP_WORD.search(t)
    if m4 and e_lab is None:
        e_lab = f"E{int(m4.group(1))}"
        t = _EP_WORD.sub(" ", t)
    t = re.sub(r"\s+", " ", t).strip()
    # Peacock-style "Show S1 E1: Pilot" leaves a stray colon after stripping SxxEyy; do not touch
    # ordinary "Movie: Subtitle" titles when no S/E was removed.
    if stripped_s_e:
        t = re.sub(r"\s+:\s+", " ", t).strip()
    t = t.strip(" -–—:\uFF1a|").strip()
    return t, s_lab, e_lab


def strip_embedded_season_episode_for_tmdb_query(s: str | None) -> str | None:
    """
    Remove ``(S1:E1)``, ``S01E02``, ``1x02``, etc. from a single metadata line so TMDb gets a
    cleaner query when Hulu (and similar) glue season/episode into ``title`` or ``series_name``.
    Returns the original string when nothing was removed, stripping would be empty/degenerate, or
    the cleaned string equals the input.
    """
    if s is None:
        return None
    t = str(s).strip()
    if not t:
        return None
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_x: str) -> bool:  # type: ignore[misc]
            return False

    clean, s_lab, e_lab = _strip_season_episode_from_text(t)
    clean = (clean or "").strip()
    if not (s_lab or e_lab):
        return s
    if not clean or clean.lower() == t.lower():
        return s
    if is_degenerate_tmdb_query(clean):
        return s
    return clean


def _fill_layers_common(rt: RawTitle) -> None:
    """Infer layer_* from raw_* + embedded S/E patterns (provider-agnostic)."""
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_s: str) -> bool:  # type: ignore[misc]
            return False

    sn = (rt.raw_series_name or "").strip() or None
    ti = (rt.raw_title or "").strip() or None
    et = (rt.raw_episode_title or "").strip() or None

    hulu_compound_handled = False
    if not sn and ti:
        m_h = _HULU_COMPOUND_LINE.match(ti.strip())
        if m_h:
            ser = m_h.group(1).strip()
            ept = (m_h.group(4) or "").strip()
            if ser and not is_degenerate_tmdb_query(ser):
                hulu_compound_handled = True
                rt.layer_series_title = ser
                rt.layer_series_number = f"S{int(m_h.group(2))}"
                rt.layer_episode_number = f"E{int(m_h.group(3))}"
                if ept:
                    rt.layer_episode_title = ept
                rt.notes.append("hulu-style (S#:E#) compound in title")

    # Episode title layer: explicit episode field, else title if series_name differs
    if et:
        rt.layer_episode_title = et
    elif ti and sn and ti.lower() != sn.lower():
        clean_t, s_l, e_l = _strip_season_episode_from_text(ti)
        if clean_t and clean_t.lower() != (sn or "").lower():
            rt.layer_episode_title = clean_t
        if s_l:
            rt.layer_series_number = rt.layer_series_number or s_l
        if e_l:
            rt.layer_episode_number = rt.layer_episode_number or e_l

    # Series title: prefer non-degenerate series_name
    if sn and not is_degenerate_tmdb_query(sn):
        rt.layer_series_title = sn
    elif ti and not hulu_compound_handled:
        clean_t, s_l, e_l = _strip_season_episode_from_text(ti)
        if s_l:
            rt.layer_series_number = rt.layer_series_number or s_l
        if e_l:
            rt.layer_episode_number = rt.layer_episode_number or e_l
        if clean_t and not is_degenerate_tmdb_query(clean_t):
            # If this looks like "Show Name" only after stripping S/E, use as series candidate
            if not rt.layer_series_title:
                rt.layer_series_title = clean_t

    if rt.season_index is not None and not rt.layer_series_number:
        rt.layer_series_number = f"S{int(rt.season_index)}"
    if rt.episode_index is not None and not rt.layer_episode_number:
        rt.layer_episode_number = f"E{int(rt.episode_index)}"


def _fill_layers_pyatv(rt: RawTitle) -> None:
    """Apple TV / pyatv: often episode in ``title``, series in ``series_name`` or ``artist``."""
    _fill_layers_common(rt)
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_s: str) -> bool:  # type: ignore[misc]
            return False

    if rt.layer_series_title:
        return
    ar = (rt.raw_artist or "").strip() or None
    al = (rt.raw_album or "").strip() or None
    ti = (rt.raw_title or "").strip() or None
    # HBO-style: artist = show, title = episode
    if ar and ti and ar.lower() != ti.lower() and not is_degenerate_tmdb_query(ar):
        rt.notes.append("pyatv: series fallback from artist (HBO-style)")
        rt.layer_series_title = ar
    elif (
        al
        and ti
        and al.lower() != ti.lower()
        and not is_degenerate_tmdb_query(al)
        and (not ar or is_degenerate_tmdb_query(ar))
    ):
        rt.notes.append("pyatv: series fallback from album (Max app often sets artist to app name)")
        rt.layer_series_title = al
    elif ti:
        rt.layer_series_title = ti


def _fill_layers_roku(rt: RawTitle) -> None:
    """Roku: ``series-title`` + ``episode-title`` are usually trustworthy when both exist."""
    _fill_layers_common(rt)
    if rt.layer_series_title:
        return
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_s: str) -> bool:  # type: ignore[misc]
            return False

    series = (rt.raw_series_name or "").strip() or None
    if series and not is_degenerate_tmdb_query(series):
        rt.layer_series_title = series
        return
    artist = (rt.raw_artist or "").strip() or None
    ti = (rt.raw_title or "").strip() or None
    if artist and ti and artist.lower() != ti.lower() and not is_degenerate_tmdb_query(artist):
        rt.notes.append("roku: series fallback from artist-name")
        rt.layer_series_title = artist
    elif ti:
        rt.layer_series_title = ti


def _coerce_counting_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        f = float(str(v).strip())
        if f < 0 or f > 1_000_000:
            return None
        r = round(f)
        if abs(f - r) < 1e-6:
            return int(r)
    except (TypeError, ValueError):
        pass
    return None


def raw_title_from_pyatv_playing(playing: Any) -> RawTitle:
    """Build :class:`RawTitle` from a pyatv ``Playing`` instance."""

    def _tx(name: str) -> str | None:
        v = getattr(playing, name, None)
        if v is None:
            return None
        t = str(v).strip()
        return t or None

    sn = getattr(playing, "season_number", None)
    en = getattr(playing, "episode_number", None)
    mt = getattr(playing, "media_type", None)
    mt_label = getattr(mt, "name", None) or (str(mt) if mt is not None else None)

    rt = RawTitle(
        source="pyatv",
        raw_title=_tx("title"),
        raw_series_name=_tx("series_name"),
        raw_artist=_tx("artist"),
        raw_album=_tx("album"),
        raw_episode_title=_tx("episode_title"),
        season_index=_coerce_counting_int(sn),
        episode_index=_coerce_counting_int(en),
        media_type_label=mt_label,
    )
    _fill_layers_pyatv(rt)
    return rt


def raw_title_from_metadata_dict(d: Mapping[str, Any]) -> RawTitle:
    """Pigeon ``last_metadata`` dict (Apple TV poll snapshot or Roku shim)."""

    def _g(key: str) -> str | None:
        v = d.get(key)
        if v is None:
            return None
        t = str(v).strip()
        return t or None

    rt = RawTitle(
        source="metadata_dict",
        raw_title=_g("title"),
        raw_series_name=_g("series_name"),
        raw_artist=_g("artist"),
        raw_album=_g("album"),
        raw_episode_title=_g("episode_title"),
        raw_query=_g("query"),
        media_type_label=_g("media_type"),
    )
    _fill_layers_pyatv(rt)
    return rt


def raw_title_from_roku_player_fields(fields: Mapping[str, str]) -> RawTitle:
    """
    From Roku ECP ``<player>`` child tags (see :func:`pigeon.roku_ecp._collect_player_fields`).
    """

    def _first(*keys: str) -> str | None:
        for k in keys:
            v = (fields.get(k) or "").strip()
            if v:
                return v
        return None

    series = _first("series-title", "series_title", "seriestitle", "seriesname")
    episode = _first("episode-title", "episode_title", "episodetitle", "episodename")
    artist = _first("artist-name", "artist", "artistname")
    album = _first("album-title", "albumtitle")
    prog = _first(
        "program-title",
        "programtitle",
        "show-title",
        "showtitle",
        "movie-title",
        "movietitle",
        "title",
        "label",
    )
    rt = RawTitle(
        source="roku",
        raw_title=prog,
        raw_series_name=series,
        raw_artist=artist,
        raw_album=album,
        raw_episode_title=episode,
    )
    _fill_layers_roku(rt)
    return rt


def tmdb_query_from_raw_title(
    rt: RawTitle,
    *,
    base_query: str | None = None,
    forgiving: bool | None = None,
) -> str | None:
    """
    TMDb search string: training hint wins, else :func:`pigeon.tmdb_poster.resolve_tmdb_query_from_now_playing_fields`.

    ``base_query`` should be the legacy heuristic string (e.g. from pyatv) when available.
    """
    try:
        from pigeon.series_title_training import lookup_training_series_title
        from pigeon.tmdb_poster import (
            is_degenerate_tmdb_query,
            refine_tmdb_search_query,
            resolve_tmdb_query_from_now_playing_fields,
        )
    except ImportError:
        return (base_query or "").strip() or None

    sig = rt.training_signature_normalized()
    trained = lookup_training_series_title(sig)
    if trained and not is_degenerate_tmdb_query(trained):
        return refine_tmdb_search_query(trained) or trained

    bq = (base_query or "").strip() or None
    if bq:
        bq = strip_embedded_season_episode_for_tmdb_query(bq) or bq
    ti = (rt.raw_title or "").strip() or None
    if ti:
        ti = strip_embedded_season_episode_for_tmdb_query(ti) or ti
    raw_sn = (rt.raw_series_name or "").strip() or None
    if raw_sn:
        raw_sn = strip_embedded_season_episode_for_tmdb_query(raw_sn) or raw_sn
    layer_sn = (rt.layer_series_title or "").strip() or None
    if layer_sn and is_degenerate_tmdb_query(layer_sn):
        layer_sn = None
    merged_sn: str | None = raw_sn
    if layer_sn:
        if not merged_sn:
            merged_sn = layer_sn
        elif merged_sn.lower() == (ti or "").strip().lower():
            # Hulu often duplicates the full compound line into ``series_name``.
            merged_sn = layer_sn
        else:
            stripped_raw, _, _ = _strip_season_episode_from_text(rt.raw_series_name or "")
            stripped_raw = (stripped_raw or "").strip()
            if stripped_raw and stripped_raw.lower() == layer_sn.lower():
                merged_sn = layer_sn
            elif any("hulu-style" in str(n) for n in rt.notes) and (
                merged_sn.lower().startswith(layer_sn.lower() + " ")
                or merged_sn.lower() == layer_sn.lower()
            ):
                merged_sn = layer_sn

    return resolve_tmdb_query_from_now_playing_fields(
        base_query=bq,
        title=ti,
        series_name=merged_sn,
        artist=rt.raw_artist,
        album=rt.raw_album,
        episode_title=rt.raw_episode_title,
        forgiving=forgiving,
    )


def metadata_has_playback_title(metadata: Mapping[str, Any] | None) -> bool:
    """True when metadata carries show/movie title fields even if ``query`` is empty."""
    if not metadata:
        return False
    for key in ("query", "title", "series_name", "artist", "album"):
        if str(metadata.get(key) or "").strip():
            return True
    return False


def _title_looks_like_episode_in_metadata(
    metadata: Mapping[str, Any],
    rt: RawTitle,
) -> bool:
    """True when ``title`` is probably an episode line and another field carries the series (HBO/Max)."""
    title = (rt.raw_title or "").strip()
    if not title:
        return False
    try:
        from pigeon.tmdb_poster import _guest_on_show_canonical_query

        if _guest_on_show_canonical_query(title):
            return False
    except ImportError:
        pass
    t_cf = title.casefold()
    for key in ("series_name", "album"):
        val = str(metadata.get(key) or "").strip()
        if val and val.casefold() != t_cf:
            try:
                from pigeon.tmdb_poster import is_degenerate_tmdb_query

                if not is_degenerate_tmdb_query(val):
                    return True
            except ImportError:
                return True
    artist = str(metadata.get("artist") or "").strip()
    if artist and artist.casefold() != t_cf:
        try:
            from pigeon.tmdb_poster import is_degenerate_tmdb_query

            if not is_degenerate_tmdb_query(artist):
                return True
        except ImportError:
            return True
    return False


def resolve_metadata_tmdb_query(metadata: Mapping[str, Any] | None) -> str:
    """Canonical TMDb/playback query — pyatv ``query`` first, then rawTitle field fallbacks."""
    if not metadata:
        return ""
    q = str(metadata.get("query") or "").strip()
    if q:
        try:
            from pigeon.tmdb_poster import is_degenerate_tmdb_query

            if not is_degenerate_tmdb_query(q):
                return q
        except ImportError:
            return q
    try:
        derived = tmdb_query_from_raw_title(
            raw_title_from_metadata_dict(metadata),
            base_query=None,
        )
        if derived and str(derived).strip():
            return str(derived).strip()
    except Exception:
        pass
    try:
        from pigeon.tmdb_poster import refine_tmdb_search_query
    except ImportError:

        def refine_tmdb_search_query(x: str | None) -> str | None:  # type: ignore[misc]
            return (str(x).strip() or None) if x else None

    for key in ("series_name", "title", "artist", "album"):
        raw = str(metadata.get(key) or "").strip()
        if not raw:
            continue
        refined = refine_tmdb_search_query(raw) or raw
        if refined.strip():
            try:
                from pigeon.tmdb_poster import is_degenerate_tmdb_query

                if is_degenerate_tmdb_query(refined):
                    continue
            except ImportError:
                pass
            return refined.strip()
    return ""


def tmdb_query_candidates_from_metadata(metadata: Mapping[str, Any] | None) -> list[str]:
    """Ordered unique TMDb search strings for API retries (smart raw-title vs pyatv ordering)."""
    if not metadata:
        return []
    try:
        from pigeon.tmdb_poster import (
            equivalent_tmdb_search_queries,
            is_degenerate_tmdb_query,
            refine_tmdb_search_query,
        )
    except ImportError:
        q = resolve_metadata_tmdb_query(metadata)
        return [q] if q else []

    out: list[str] = []
    seen: set[str] = set()

    def add(cand: str | None) -> None:
        c = (cand or "").strip()
        if not c or is_degenerate_tmdb_query(c):
            return
        r = refine_tmdb_search_query(c) or c
        key = r.strip().casefold()
        if not key or key in seen:
            return
        if out and any(equivalent_tmdb_search_queries(r, prev) for prev in out):
            return
        seen.add(key)
        out.append(r.strip())

    rt = raw_title_from_metadata_dict(metadata)
    pyatv_q = str(metadata.get("query") or "").strip()
    episode_like = _title_looks_like_episode_in_metadata(metadata, rt)
    ep_title = (rt.raw_episode_title or "").strip() or str(
        metadata.get("episode_title") or ""
    ).strip()
    if episode_like:
        add(pyatv_q)
        add(rt.raw_title)
    else:
        add(rt.raw_title)
        add(pyatv_q)
    # Episode-only Peacock/etc. lines: always try the episode title for series-index fallback.
    add(ep_title)
    try:
        add(tmdb_query_from_raw_title(rt, base_query=pyatv_q or None))
    except Exception:
        pass
    for key in ("series_name", "artist", "album", "title", "ocr_title"):
        add(str(metadata.get(key) or ""))
    if not out:
        add(resolve_metadata_tmdb_query(metadata))
    return out
