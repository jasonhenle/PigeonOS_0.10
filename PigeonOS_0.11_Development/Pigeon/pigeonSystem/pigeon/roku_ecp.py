"""Roku ECP: resolve base URL from Pigeon state / saved Player row; now-playing line for overlay.

Documented ECP has no official **read** for TV master volume (only VolumeUp/Down/Mute keypresses).
Some Roku TV firmware still includes a numeric level inside ``query/device-info`` XML; we parse
``*volume*`` elements (skipping sound-effects volume when tagged that way) and append ``vol N``
to the overlay line when found.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

ROKU_ECP_PORT = 8060


def _strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def finalize_roku_base_url(base_url: str) -> str:
    """Normalize scheme, default port 8060."""
    u = (base_url or "").strip().rstrip("/")
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"http://{u}"
    parsed = urlparse(u)
    if not parsed.hostname:
        return ""
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "http"
    port = parsed.port or ROKU_ECP_PORT
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        netloc = f"[{host}]:{port}"
    else:
        netloc = f"{host}:{port}"
    return f"{scheme}://{netloc}"


def normalize_roku_ecp_base(host_or_url: str) -> str:
    """Accept host, host:port, or full URL; return ``http://host:8060`` style."""
    h = (host_or_url or "").strip().rstrip("/")
    if not h:
        return ""
    if not h.startswith(("http://", "https://")):
        if ":" in h and not h.startswith("["):
            parts = h.rsplit(":", 1)
            if len(parts) == 2 and parts[-1].isdigit():
                h = f"http://{h}"
            else:
                h = f"http://{h}:{ROKU_ECP_PORT}"
        else:
            h = f"http://{h}:{ROKU_ECP_PORT}"
    return finalize_roku_base_url(h)


def resolve_roku_ecp_base_url() -> str:
    """
    Optional manual ``roku_ecp_base_url`` in state.json, else saved **Player** row whose
    name/label/identifier suggests a Roku (uses the row's IP / hostname).
    """
    try:
        from pigeon.app_state import read_roku_ecp_base_url, read_saved_streaming_devices_all
    except ImportError:
        return ""

    manual = read_roku_ecp_base_url()
    if manual:
        return normalize_roku_ecp_base(manual)

    for st in read_saved_streaming_devices_all():
        if not st:
            continue
        blob = " ".join(
            [
                str(st.get("identifier") or ""),
                str(st.get("name") or ""),
                str(st.get("label") or ""),
                str(st.get("device_role") or ""),
                str(st.get("capability_profile") or ""),
            ]
        ).lower()
        dr = str(st.get("device_role") or "").strip().lower()
        cp = str(st.get("capability_profile") or "").strip().lower()
        ident_l = str(st.get("identifier") or "").strip().lower()
        if "roku" not in blob and dr != "roku" and cp != "roku" and not ident_l.startswith("roku:"):
            continue
        addr = str(st.get("address") or "").strip()
        if not addr:
            continue
        if "%" in addr and "://" not in addr:
            addr = addr.split("%", 1)[0].strip()
        return normalize_roku_ecp_base(addr)
    return ""


def _saved_row_hints_roku(st: dict[str, str]) -> bool:
    blob = " ".join(
        [
            str(st.get("identifier") or ""),
            str(st.get("name") or ""),
            str(st.get("label") or ""),
            str(st.get("device_role") or ""),
            str(st.get("capability_profile") or ""),
        ]
    ).lower()
    dr = str(st.get("device_role") or "").strip().lower()
    cp = str(st.get("capability_profile") or "").strip().lower()
    ident_l = str(st.get("identifier") or "").strip().lower()
    return (
        "roku" in blob
        or dr == "roku"
        or cp == "roku"
        or ident_l.startswith("roku:")
    )


def resolve_roku_ecp_base_url_for_row(row: dict[str, str] | None) -> str:
    """
    Roku ECP base for metadata/TMDb: manual ``roku_ecp_base_url`` first, else this Player row's
    address when the row looks like a Roku, else a quick ECP probe on the Player IP (Onn Roku TV,
    etc. saved as a generic AirPlay-only row).
    """
    try:
        from pigeon.app_state import read_roku_ecp_base_url
    except ImportError:
        return ""
    manual = read_roku_ecp_base_url()
    if manual:
        return normalize_roku_ecp_base(manual)
    if not row:
        return ""
    addr = str(row.get("address") or "").strip()
    if not addr:
        return ""
    if "%" in addr and "://" not in addr:
        addr = addr.split("%", 1)[0].strip()
    if _saved_row_hints_roku(row):
        return normalize_roku_ecp_base(addr)
    return probe_roku_ecp_at_address(addr, timeout=2.5)


def fetch_roku_title_for_metadata(base_url: str, timeout: float = 8.0) -> tuple[bool, str, str | None]:
    """
    Query Roku ECP for text suitable as a TMDb search query (show/movie/channel title).
    Returns ``(ok, message, title_or_none)``.
    """
    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return False, "No Roku ECP address (set roku_ecp_base_url in state or save a Roku Player).", None

    mp_body = _fetch(f"{base}/query/media-player", timeout)
    aa_body = _fetch(f"{base}/query/active-app", timeout)
    mp_root = None
    aa_root = None
    try:
        if mp_body and mp_body.strip():
            mp_root = ET.fromstring(mp_body)
    except ET.ParseError:
        mp_root = None
    try:
        if aa_body and aa_body.strip():
            aa_root = ET.fromstring(aa_body)
    except ET.ParseError:
        aa_root = None

    state, title, roku_fields = _parse_media_player(mp_root)
    app_name = _parse_active_app(aa_root)

    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_q: str) -> bool:  # type: ignore[misc]
            return False

    if roku_fields:
        try:
            from pigeon.raw_title import raw_title_from_roku_player_fields, tmdb_query_from_raw_title

            rt = raw_title_from_roku_player_fields(roku_fields)
            q_rt = tmdb_query_from_raw_title(rt, base_query=(title or "").strip() or None)
            if q_rt and not is_degenerate_tmdb_query(q_rt):
                return True, f"Roku @ {base}", q_rt
        except ImportError:
            pass

    t = (title or "").strip()
    if t and not is_degenerate_tmdb_query(t):
        return True, f"Roku @ {base}", t

    inactive = ("", "close", "inactive", "none", "idle")
    if state in inactive:
        tv_body = _fetch(f"{base}/query/tv-active-channel", timeout)
        tv_root = None
        try:
            if tv_body and tv_body.strip():
                tv_root = ET.fromstring(tv_body)
        except ET.ParseError:
            tv_root = None
        ch = (_parse_tv_active_channel(tv_root) or "").strip()
        if ch and not is_degenerate_tmdb_query(ch):
            return True, f"Roku @ {base}", ch
        an = (app_name or "").strip()
        if an and an.lower() not in ("roku", "home", "settings") and not is_degenerate_tmdb_query(an):
            return True, f"Roku @ {base}", an
        return False, "Roku reported no title (home screen or idle). Start playback and try again.", None

    an = (app_name or "").strip()
    if an and an.lower() not in ("roku",) and not is_degenerate_tmdb_query(an):
        return True, f"Roku @ {base}", an
    return (
        False,
        "Roku is active but did not report a usable program title (only app or channel branding).",
        None,
    )


def fetch_roku_active_app_name(base_url: str, timeout: float = 3.0) -> str:
    """Foreground app name from ``/query/active-app`` (e.g. ``Disney+``) for service-badge mapping."""
    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return ""
    aa_body = _fetch(f"{base}/query/active-app", timeout=timeout)
    aa_root = None
    try:
        if aa_body and aa_body.strip():
            aa_root = ET.fromstring(aa_body)
    except ET.ParseError:
        aa_root = None
    return (_parse_active_app(aa_root) or "").strip()


def _collect_player_fields(player: ET.Element) -> dict[str, str]:
    """Longest non-empty text seen per child tag under ``<player>`` (namespace-stripped)."""
    by_tag: dict[str, str] = {}
    for el in player.iter():
        tag = _strip_ns(el.tag).lower()
        tx = (el.text or "").strip()
        if not tx:
            continue
        prev = by_tag.get(tag, "")
        if len(tx) > len(prev):
            by_tag[tag] = tx
    return by_tag


def _is_junk_roku_program_title(s: str) -> bool:
    """Splash / app strings and other non-program titles from Roku ECP."""
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:
        is_degenerate_tmdb_query = None  # type: ignore[assignment]

    raw = (s or "").strip()
    if not raw or len(raw) < 2:
        return True
    if is_degenerate_tmdb_query is not None and is_degenerate_tmdb_query(raw):
        return True
    low = raw.lower()
    if "disney" in low and "365" in low:
        return True
    if low in ("null", "none", "n/a", "unknown", "title", "video"):
        return True
    return False


def _first_field(fields: dict[str, str], *keys: str) -> str:
    for k in keys:
        v = (fields.get(k) or "").strip()
        if v:
            return v
    return ""


def _best_title_from_player_fields(fields: dict[str, str]) -> str:
    """
    Prefer series + episode, then program-style tags, then generic title — skip app splash strings.
    """
    series = _first_field(fields, "series-title", "series_title", "seriestitle", "seriesname")
    episode = _first_field(fields, "episode-title", "episode_title", "episodetitle", "episodename")
    # TMDb matches the series; episode titles alone often pick the wrong show.
    if series and episode and series.strip().lower() != episode.strip().lower():
        if not _is_junk_roku_program_title(series.strip()):
            return series.strip()
        cand = f"{series.strip()} — {episode.strip()}"
        if not _is_junk_roku_program_title(cand):
            return cand
    if series and not _is_junk_roku_program_title(series):
        return series.strip()

    artist_early = _first_field(fields, "artist-name", "artist", "artistname")
    # HBO / Max on Roku often omit series-title but put the show in artist and episode in episode-title.
    if episode and not _is_junk_roku_program_title(episode):
        if (
            artist_early
            and not _is_junk_roku_program_title(artist_early)
            and artist_early.strip().lower() != episode.strip().lower()
        ):
            return artist_early.strip()

    if episode and not _is_junk_roku_program_title(episode):
        return episode.strip()

    for key in (
        "program-title",
        "programtitle",
        "show-title",
        "showtitle",
        "movie-title",
        "movietitle",
        "feature-title",
        "content-title",
        "contenttitle",
    ):
        v = _first_field(fields, key)
        if v and not _is_junk_roku_program_title(v):
            return v

    artist = _first_field(fields, "artist-name", "artist", "artistname")
    album = _first_field(fields, "album-title", "albumtitle")
    if artist and album:
        cand = f"{artist.strip()} — {album.strip()}"
        if not _is_junk_roku_program_title(cand):
            return cand
    if artist and not _is_junk_roku_program_title(artist):
        return artist.strip()

    for key in ("title", "label", "name", "heading", "subtitle"):
        v = _first_field(fields, key)
        if v and not _is_junk_roku_program_title(v):
            return v

    for key in ("description", "synopsis", "summary"):
        v = _first_field(fields, key)
        if v and len(v) > 12 and not _is_junk_roku_program_title(v):
            return v.strip()

    return ""


def _streaming_service_display_name(s: str) -> bool:
    """True if ``s`` is only a service name (duplicate of badge), not a show title."""
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:
        return False
    return bool(is_degenerate_tmdb_query(s))


def _fetch(url: str, timeout: float) -> str | None:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "Pigeon/0.5 (Roku ECP)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


def roku_send_play_pause(*, base_url: str, timeout: float = 3.0) -> tuple[bool, str]:
    """
    POST Roku ECP ``Play`` key — toggles play/pause in most media UIs.

    https://developer.roku.com/docs/developer-program/debugging/external-control-api.md
    """
    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return False, "No Roku ECP URL."
    url = f"{base.rstrip('/')}/keypress/Play"
    req = urllib.request.Request(
        url,
        method="POST",
        data=b"",
        headers={"User-Agent": "Pigeon/0.5 (Roku ECP)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status not in (200, 204):
                return False, f"Roku keypress failed (HTTP {resp.status})."
        return True, "Play/pause sent (Roku)."
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        return False, str(e)


def _parse_media_player(root: ET.Element | None) -> tuple[str, str, dict[str, str]]:
    """Returns ``(state_lower, best_title_or_empty, player_tag_fields)``."""
    if root is None:
        return "", "", {}
    player = None
    for el in root.iter():
        if _strip_ns(el.tag).lower() == "player":
            player = el
            break
    if player is None:
        return "", "", {}
    state = (player.get("state") or "").strip().lower()
    fields = _collect_player_fields(player)
    title = _best_title_from_player_fields(fields)
    return state, title, fields


def _parse_active_app(root: ET.Element | None) -> str:
    if root is None:
        return ""
    for el in root.iter():
        if _strip_ns(el.tag).lower() != "app":
            continue
        tx = (el.text or "").strip()
        if tx:
            return tx
        nm = (el.get("name") or "").strip()
        if nm:
            return nm
    return ""


def _tv_volume_level_ok(s: str) -> bool:
    if not s.isdigit():
        return False
    n = int(s)
    return 0 <= n <= 100


def _parse_tv_volume_from_device_info(root: ET.Element | None, raw: str | None) -> str:
    """
    Best-effort numeric TV level from ``/query/device-info`` (undocumented; varies by model).
    """
    scored: list[tuple[int, str]] = []
    if root is not None:
        for el in root.iter():
            tag = _strip_ns(el.tag).lower()
            if "volume" not in tag:
                continue
            if "sound" in tag and "effect" in tag:
                continue
            text = (el.text or "").strip()
            if text.isdigit() and _tv_volume_level_ok(text):
                pri = 5
                if "tv" in tag:
                    pri = 0
                elif "master" in tag:
                    pri = 1
                elif "display" in tag or "osd" in tag:
                    pri = 2
                scored.append((pri, text))
            for ak, av in el.attrib.items():
                ak_l = ak.lower()
                if "volume" not in ak_l:
                    continue
                a = str(av).strip()
                if a.isdigit() and _tv_volume_level_ok(a):
                    scored.append((3, a))

    if scored:
        scored.sort(key=lambda t: t[0])
        return scored[0][1]

    if raw:
        for m in re.finditer(
            r"<([a-z0-9_-]*)volume([a-z0-9_-]*)\s*>\s*(\d{1,3})\s*</",
            raw,
            re.I,
        ):
            tag = (m.group(1) + "volume" + m.group(2)).lower()
            num = m.group(3)
            if "sound" in tag and "effect" in tag:
                continue
            if _tv_volume_level_ok(num):
                return num
    return ""


def fetch_roku_tv_master_volume_string(base_url: str, timeout: float = 3.0) -> str:
    """
    TV master level from ``/query/device-info`` as a decimal string ``0``..``100``, or ``\"\"``.
    """
    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return ""
    di_body = _fetch(f"{base}/query/device-info", timeout)
    di_root = None
    try:
        if di_body and di_body.strip():
            di_root = ET.fromstring(di_body)
    except ET.ParseError:
        di_root = None
    return _parse_tv_volume_from_device_info(di_root, di_body)


def _append_volume_suffix(line: str, vol: str) -> str:
    v = (vol or "").strip()
    if not v:
        return line
    tail = f"vol {v}"
    base = (line or "").strip()
    if not base:
        return f"Roku · {tail}"
    return f"{base} · {tail}"


def _parse_tv_active_channel(root: ET.Element | None) -> str:
    """Best-effort label when antenna/cable UI is active (varies by firmware)."""
    if root is None:
        return ""
    for el in root.iter():
        tag = _strip_ns(el.tag).lower()
        if tag not in ("channel", "channel-name", "name", "title", "label"):
            continue
        tx = (el.text or "").strip()
        if tx and tx.lower() not in ("invalid", "none", "null"):
            return tx
        nm = (el.get("name") or el.get("title") or "").strip()
        if nm:
            return nm
    return ""


def fetch_roku_playback_line(base_url: str, timeout: float = 3.0) -> tuple[str, str]:
    """
    Returns ``(line, tv_volume_percent)`` where *line* is a short ``Roku · …`` status string
    (with ``· vol N`` appended when device-info exposes a TV level), and *tv_volume_percent*
    is a ``0``..``100`` digit string or ``\"\"`` when unknown.
    """
    base = normalize_roku_ecp_base(base_url) if (base_url or "").strip() else ""
    if not base:
        return "", ""

    di_body = _fetch(f"{base}/query/device-info", timeout)
    di_root = None
    try:
        if di_body and di_body.strip():
            di_root = ET.fromstring(di_body)
    except ET.ParseError:
        di_root = None
    vol_hint = _parse_tv_volume_from_device_info(di_root, di_body)

    mp_body = _fetch(f"{base}/query/media-player", timeout)
    aa_body = _fetch(f"{base}/query/active-app", timeout)
    mp_root = None
    aa_root = None
    try:
        if mp_body and mp_body.strip():
            mp_root = ET.fromstring(mp_body)
    except ET.ParseError:
        mp_root = None
    try:
        if aa_body and aa_body.strip():
            aa_root = ET.fromstring(aa_body)
    except ET.ParseError:
        aa_root = None

    state, title, _roku_fields = _parse_media_player(mp_root)
    app_name = _parse_active_app(aa_root)

    inactive = ("", "close", "inactive", "none", "idle")
    if state in inactive:
        tv_body = _fetch(f"{base}/query/tv-active-channel", timeout)
        tv_root = None
        try:
            if tv_body and tv_body.strip():
                tv_root = ET.fromstring(tv_body)
        except ET.ParseError:
            tv_root = None
        ch = _parse_tv_active_channel(tv_root)
        line = f"Roku · {ch}" if ch else ""
        out = _append_volume_suffix(line, vol_hint)
        return out, vol_hint

    parts = ["Roku"]
    if title:
        parts.append(title)
    elif app_name and app_name.lower() != "roku" and not _streaming_service_display_name(app_name):
        parts.append(app_name)
    if state == "pause":
        parts.append("(paused)")
    return _append_volume_suffix(" · ".join(parts), vol_hint), vol_hint


# Probe results per normalized address: (result_base_or_empty, mono_when_probed).
# The receiver poll calls this every ~750ms for generic Player rows; without a cache
# each poll re-spends up to 2x the probe timeout on the same non-Roku address.
_PROBE_CACHE: dict[str, tuple[str, float]] = {}
_PROBE_OK_TTL_S = 600.0
_PROBE_FAIL_TTL_S = 60.0


def probe_roku_ecp_at_address(address: str, timeout: float = 2.5) -> str:
    """
    If ``http://address:8060`` responds like Roku External Control Protocol, return the normalized
    base URL; otherwise ``""``. Results are cached (10 min hit / 60 s miss).
    """
    base = normalize_roku_ecp_base(address)
    cached = _PROBE_CACHE.get(base)
    if cached is not None:
        result, when = cached
        ttl = _PROBE_OK_TTL_S if result else _PROBE_FAIL_TTL_S
        if time.monotonic() - when < ttl:
            return result

    result = ""
    raw = _fetch(f"{base}/query/active-app", timeout=timeout)
    head = (raw or "")[:240].lower()
    if raw and "<?xml" in head and "app" in raw.lower():
        result = base
    else:
        raw2 = _fetch(f"{base}/query/media-player", timeout=timeout)
        head2 = (raw2 or "")[:240].lower()
        if raw2 and "<?xml" in head2 and "player" in raw2.lower():
            result = base
    _PROBE_CACHE[base] = (result, time.monotonic())
    return result
