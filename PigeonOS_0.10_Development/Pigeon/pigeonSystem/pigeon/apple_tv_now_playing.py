"""Discover Apple TVs on the LAN and read now-playing metadata for TMDb lookup.

Uses `pyatv` (async). Credentials are stored in ``~/.pigeon_0_6/pyatv_credentials``
(see `pyatv` FileStorage). If connections fail, pair once from a terminal, e.g.::

    atvremote --scan
    atvremote --id <identifier> --protocol mrp pair
    atvremote --id <identifier> --protocol companion pair

Then retry from Pigeon. tvOS 15+ often needs MRP tunneled via AirPlay; follow current
pyatv docs if metadata is empty.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import random
import threading
import time
from pathlib import Path
import plistlib

from pigeon.runtime_paths import pigeon_state_dir

_PAIRING_SESSIONS: dict[str, dict[str, object]] = {}


def _credentials_file() -> Path:
    return pigeon_state_dir() / "pyatv_credentials"


# Background Apple TV remote: one thread + one asyncio loop reuses ``pyatv`` after the first
# scan/connect (UI remotes no longer pay scan+handshake per keypress).
_ATV_REMOTE_QUEUE: queue.Queue[tuple[str, str, str, int]] | None = None
_ATV_REMOTE_WORKER_THREAD: threading.Thread | None = None
_ATV_REMOTE_WORKER_LOCK = threading.Lock()
_ATV_REMOTE_STATE: dict[str, object] = {}
_ATV_REMOTE_LAST_USE_MONO: list[float] = [0.0]
_ATV_REMOTE_IDLE_DISCONNECT_S = 120.0


def _tmdb_query_from_playing_impl(playing) -> str | None:
    """Pick a short search string for TMDb from pyatv ``Playing`` state (before global refine)."""
    from pyatv.const import DeviceState, MediaType

    try:
        from pigeon.tmdb_poster import colon_prefix_show_query, is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_s: str) -> bool:  # type: ignore[misc]
            return False

        def colon_prefix_show_query(_s: str) -> str | None:  # type: ignore[misc]
            return None

    def _text(name: str) -> str | None:
        value = getattr(playing, name, None)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _tmdb_show_from_series_or_compound_title() -> str | None:
        """
        Prefer ``series_name`` for episodic rows, but Disney+ can send a **stale** ``series_name``
        while ``title`` is still ``Show - Episode``. If the show side parsed from ``title`` disagrees
        and ``series_name`` does not appear in ``title``, trust the compound ``title``.
        """
        if not series_name or is_degenerate_tmdb_query(series_name):
            return None
        if not title:
            return series_name
        inf = colon_prefix_show_query(title)
        if not inf:
            return series_name
        il = inf.strip().lower()
        sl = series_name.strip().lower()
        tl = title.lower()
        if il != sl and sl not in tl:
            return inf.strip()
        return series_name

    title = _text("title")
    series_name = _text("series_name")
    album = _text("album")
    artist = _text("artist")
    episode_title_txt = _text("episode_title")
    season_number = getattr(playing, "season_number", None)
    episode_number = getattr(playing, "episode_number", None)

    # Some apps (e.g. certain streaming players) may report Idle/Stopped even while video is
    # visually playing, but still include a valid title/series name. Prefer any usable metadata
    # over the reported device state.
    mt = getattr(playing, "media_type", None)
    is_tv = mt == MediaType.TV
    # Heuristic: only swap title→artist for “episode-like” contexts.
    # (For movies, artist can be app/platform name and would break TMDb matching.)
    episode_like = season_number is not None or episode_number is not None
    title_embeds_se = False
    artist_embeds_se = False
    try:
        from pigeon.raw_title import _strip_season_episode_from_text

        if title:
            _te, _s_lab, _e_lab = _strip_season_episode_from_text(title)
            title_embeds_se = bool(_s_lab or _e_lab)
        if artist:
            _ae, _a_s_lab, _a_e_lab = _strip_season_episode_from_text(artist)
            artist_embeds_se = bool(_a_s_lab or _a_e_lab)
    except ImportError:
        pass
    # Prime Video often reports episodes as ``Video`` with S01 E01 inside ``title`` only (no pyatv S/E).
    # Disney+ packs ``S3:E21 Episode Title`` into ``artist`` while ``title`` is the bare show name —
    # either placement is a reliable hint that this is episodic content.
    episodic_hint = bool(
        is_tv or episode_like or episode_title_txt or title_embeds_se or artist_embeds_se
    )

    try:
        from pigeon.tmdb_poster import compound_title_streaming_series_fix
    except ImportError:
        compound_title_streaming_series_fix = None  # type: ignore[misc, assignment]
    if compound_title_streaming_series_fix is not None:
        d_fix = compound_title_streaming_series_fix(title, series_name)
        if d_fix:
            return d_fix

    # Apple TV app / TV+: ``series_name`` is often empty while ``title`` is ``Series — Episode`` or ``: Episode``.
    if not series_name and title and episodic_hint:
        cp_solo = colon_prefix_show_query(title)
        if cp_solo:
            return cp_solo.strip()

    # HBO Max and similar often report episodes as Video, not TV — still prefer series for TMDb.
    if series_name and (is_tv or episode_like) and not is_degenerate_tmdb_query(series_name):
        return _tmdb_show_from_series_or_compound_title() or series_name

    if is_tv:
        if artist and not is_degenerate_tmdb_query(artist):
            return artist
        if title:
            cp_tv = colon_prefix_show_query(title)
            if cp_tv:
                return cp_tv.strip()
            if not is_degenerate_tmdb_query(title):
                return title
            if album and not is_degenerate_tmdb_query(album):
                return album
        return None

    # Some Apple TV apps expose episode title as "title" and show title as "artist"
    # while still reporting media type Unknown. Prefer the show name in that case.
    if artist and title and artist != title:
        if episode_like:
            if not is_degenerate_tmdb_query(artist):
                return artist
            if album and not is_degenerate_tmdb_query(album) and album.lower() != title.lower():
                return album
            if series_name and not is_degenerate_tmdb_query(series_name):
                return _tmdb_show_from_series_or_compound_title() or series_name
            if not is_degenerate_tmdb_query(title):
                return title
            if album and not is_degenerate_tmdb_query(album):
                return album
            return title
        # HBO Max et al. often omit season/episode numbers but still send show=artist, episode=title.
        # Do not use len() here — episode titles are often longer than the series name.
        if mt in (MediaType.Video, MediaType.Unknown):
            # Prefer series over artist when both title and series differ: artist may be a guest,
            # channel, or wrong field while title holds a sketch/segment name (e.g. Apple TV+).
            if (
                series_name
                and not is_degenerate_tmdb_query(series_name)
                and series_name.strip().lower() != (title or "").strip().lower()
            ):
                return _tmdb_show_from_series_or_compound_title() or series_name
            # Disney+ convention: ``artist = 'S3:E21 Episode Title'`` is the episode descriptor,
            # while ``title = 'Muppet Babies'`` is the show. An artist that starts with an S/E
            # token is a near-certain episode-line signal — prefer the bare title in that case.
            if (
                artist_embeds_se
                and title
                and not is_degenerate_tmdb_query(title)
                and not title_embeds_se
            ):
                return title
            if not is_degenerate_tmdb_query(artist):
                return artist
            # Artist is the app (Peacock, etc.): series_name is usually the real show (e.g. SNL), not the sketch in title.
            if series_name and not is_degenerate_tmdb_query(series_name):
                return _tmdb_show_from_series_or_compound_title() or series_name
            cp = colon_prefix_show_query(title or "")
            if cp:
                return cp
            # Artist is often the app name "Max" (degenerate) while album still holds the series.
            if (
                album
                and not is_degenerate_tmdb_query(album)
                and title
                and album.strip().lower() != title.strip().lower()
            ):
                return album
            # Episode titles are usually longer than "Max"; never pick the app via len(artist).
            if not is_degenerate_tmdb_query(title):
                return title
            if album and not is_degenerate_tmdb_query(album):
                return album
            return title
        # Live/continuous content often swaps fields; prefer the longest “title-like” string.
        if series_name:
            return max((title, artist, series_name), key=len)
        if (
            album
            and not is_degenerate_tmdb_query(album)
            and title
            and album.strip().lower() != title.strip().lower()
        ):
            return album
        if title and not is_degenerate_tmdb_query(title):
            return title
        if album and not is_degenerate_tmdb_query(album):
            return album
        if artist and not is_degenerate_tmdb_query(artist):
            return artist
        return title if title else artist
    # Video with sketch/segment in title but no usable artist (Peacock often omits it): still prefer series_name.
    if (
        title
        and series_name
        and not is_degenerate_tmdb_query(series_name)
        and mt in (MediaType.Video, MediaType.Unknown)
        and series_name.strip().lower() != title.strip().lower()
        and (not artist or is_degenerate_tmdb_query(artist))
    ):
        return _tmdb_show_from_series_or_compound_title() or series_name
    if title and mt in (MediaType.Video, MediaType.Unknown):
        cp = colon_prefix_show_query(title)
        if cp:
            return cp
    # Prime Video sometimes uses app branding as ``title`` when ``artist`` matches or is absent.
    if title and is_degenerate_tmdb_query(title):
        if album and not is_degenerate_tmdb_query(album):
            return album
        if series_name and not is_degenerate_tmdb_query(series_name):
            return series_name
    if title and not is_degenerate_tmdb_query(title):
        return title
    if series_name and not is_degenerate_tmdb_query(series_name):
        return series_name
    # Some apps put the most useful "title" into album.
    if album and not is_degenerate_tmdb_query(album):
        return album
    if title:
        return title
    if series_name:
        return series_name
    if album:
        return album

    # Only consider state as a reason to give up after we've tried to extract a title.
    if getattr(playing, "device_state", None) in (DeviceState.Idle, DeviceState.Stopped):
        return None
    return None


def _tmdb_query_from_playing(playing) -> str | None:
    """Pick TMDb query from ``Playing``; align with UI canonical series names (e.g. SNL → full title)."""
    q = _tmdb_query_from_playing_impl(playing)
    try:
        from pigeon.raw_title import raw_title_from_pyatv_playing, tmdb_query_from_raw_title
    except ImportError:
        raw_title_from_pyatv_playing = None  # type: ignore[misc, assignment]
        tmdb_query_from_raw_title = None  # type: ignore[misc, assignment]
    if raw_title_from_pyatv_playing is not None and tmdb_query_from_raw_title is not None:
        rt = raw_title_from_pyatv_playing(playing)
        return tmdb_query_from_raw_title(rt, base_query=q)
    try:
        from pigeon.tmdb_poster import resolve_tmdb_query_from_now_playing_fields
    except ImportError:
        try:
            from pigeon.tmdb_poster import refine_tmdb_search_query as _ref
        except ImportError:
            return q
        return _ref(q)
    return resolve_tmdb_query_from_now_playing_fields(
        base_query=q,
        title=getattr(playing, "title", None),
        series_name=getattr(playing, "series_name", None),
        artist=getattr(playing, "artist", None),
        album=getattr(playing, "album", None),
        episode_title=getattr(playing, "episode_title", None),
    )


def _prefer_pyatv_media_type_only(playing) -> str:
    """
    TMDb axis implied only by pyatv ``media_type`` (no episode/series heuristics).

    Used for debug: e.g. ``Video`` → ``movie`` while fetch ``prefer`` may be ``auto`` to search both endpoints.
    """
    from pyatv.const import MediaType

    media_type = getattr(playing, "media_type", None)
    if media_type == MediaType.TV:
        return "tv"
    if media_type == MediaType.Video:
        return "movie"
    return "auto"


def _query_preference_from_playing(playing) -> str:
    from pyatv.const import MediaType

    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(_s: str) -> bool:  # type: ignore[misc]
            return False

    def _tx(name: str) -> str | None:
        v = getattr(playing, name, None)
        if v is None:
            return None
        t = str(v).strip()
        return t or None

    media_type = getattr(playing, "media_type", None)
    sn = getattr(playing, "season_number", None)
    en = getattr(playing, "episode_number", None)
    episode_title_txt = _tx("episode_title")
    episode_like = sn is not None or en is not None or bool(episode_title_txt)
    t_t = _tx("title")
    t_a = _tx("artist")
    title_embeds_se = False
    try:
        from pigeon.raw_title import _strip_season_episode_from_text

        if t_t:
            _, _sl, _el = _strip_season_episode_from_text(t_t)
            title_embeds_se = bool(_sl or _el)
    except ImportError:
        pass

    if media_type == MediaType.TV:
        return "tv"
    # Streamers often tag TV episodes as Video; still search TMDb as TV when episodic.
    # Prime Video often embeds ``S01 E01`` only inside ``title`` (no pyatv season/episode fields).
    # Peacock / others often send only ``episode_title`` with an empty series_name.
    if episode_like or title_embeds_se:
        return "tv"
    t_sn = _tx("series_name")
    # Apple TV+ often uses Video + sketch/segment ``title`` + real show in ``series_name`` (no S/E).
    if (
        media_type in (MediaType.Video, MediaType.Unknown)
        and t_sn
        and t_t
        and t_sn.lower() != t_t.lower()
        and not is_degenerate_tmdb_query(t_sn)
    ):
        return "tv"
    # HBO-style: Video + distinct title/artist (episode vs series) with no S/E numbers.
    if (
        media_type in (MediaType.Video, MediaType.Unknown)
        and t_t
        and t_a
        and t_t.lower() != t_a.lower()
        and not is_degenerate_tmdb_query(t_a)
    ):
        return "tv"
    # Episode-only metadata + hints / series resolution rewrites the search string to the real show
    # name, but raw ``media_type`` may still be Video — TMDb then needs ``tv``, not ``movie``.
    try:
        from pigeon.tmdb_poster import resolve_tmdb_query_from_now_playing_fields
    except ImportError:
        pass
    else:
        q_impl = _tmdb_query_from_playing_impl(playing)
        q_res = resolve_tmdb_query_from_now_playing_fields(
            base_query=q_impl,
            title=getattr(playing, "title", None),
            series_name=getattr(playing, "series_name", None),
            artist=getattr(playing, "artist", None),
            album=getattr(playing, "album", None),
            episode_title=getattr(playing, "episode_title", None),
        )
        if (
            q_impl
            and q_res
            and q_impl.strip().lower() != q_res.strip().lower()
            and not is_degenerate_tmdb_query(q_res)
        ):
            return "tv"
    # Video / Unknown without TV signals: search both TMDb movie and TV; popularity picks the hit.
    return "auto"


def _media_type_is_music(media_type: object) -> bool:
    """True when pyatv (or stringified) media type is Music."""
    mt = str(media_type or "").strip().lower()
    if not mt:
        return False
    return mt == "music" or mt.endswith(".music")


async def _attach_music_artwork_bytes(atv, metadata: dict[str, object]) -> dict[str, object]:
    """Best-effort: attach ``artwork_bytes`` when metadata is Music.

    Uses ``await atv.metadata.artwork()``. Failures are swallowed — caller still
    gets title/artist/album. Artwork bytes are JPEG/PNG raw; decode at the host.
    """
    if not isinstance(metadata, dict):
        return metadata
    if not _media_type_is_music(metadata.get("media_type")):
        return metadata
    if metadata.get("artwork_bytes"):
        return metadata
    try:
        art = await atv.metadata.artwork(width=400, height=400)
    except Exception:
        return metadata
    if art is None:
        return metadata
    raw = getattr(art, "bytes", None)
    if not raw:
        return metadata
    out = dict(metadata)
    try:
        out["artwork_bytes"] = bytes(raw)
    except Exception:
        return metadata
    try:
        aid = getattr(atv.metadata, "artwork_id", None)
        if aid is not None and str(aid).strip():
            out["artwork_id"] = str(aid).strip()
    except Exception:
        pass
    return out


def _playing_metadata(playing) -> dict[str, object]:
    meta: dict[str, object] = {
        "query": _tmdb_query_from_playing(playing),
        "prefer": _query_preference_from_playing(playing),
        "prefer_pyatv_media": _prefer_pyatv_media_type_only(playing),
        "title": getattr(playing, "title", None),
        "series_name": getattr(playing, "series_name", None),
        "artist": getattr(playing, "artist", None),
        "album": getattr(playing, "album", None),
        "position": getattr(playing, "position", None),
        "total_time": getattr(playing, "total_time", None),
        "device_state": str(getattr(playing, "device_state", "")),
        "media_type": str(getattr(playing, "media_type", "")),
        "hash": getattr(playing, "hash", None),
    }
    # Best-effort extras for diagnostics (availability varies by tvOS / app / pyatv version).
    for name in (
        "genre",
        "episode_number",
        "season_number",
        "episode_title",
        "shuffle",
        "repeat",
        "content_identifier",
        "playback_rate",
        "itunes_store_identifier",
    ):
        try:
            v = getattr(playing, name, None)
        except Exception:
            v = None
        if v is not None and str(v).strip():
            meta[name] = v
    return meta


def _metadata_with_app(atv, metadata: dict[str, object]) -> dict[str, object]:
    """Attach ``app_name`` / ``app_id`` (bundle) when the protocol exposes :attr:`pyatv.interface.Metadata.app`."""
    out = dict(metadata)
    app = None
    try:
        app = atv.metadata.app
    except Exception:
        app = None
    if app is not None:
        nm = getattr(app, "name", None)
        out["app_name"] = nm.strip() if isinstance(nm, str) else ""
        out["app_id"] = str(getattr(app, "identifier", "") or "").strip()
    else:
        out["app_name"] = ""
        out["app_id"] = ""
    # Kids-primary apps (PBS Kids, …): bump TMDb prefer from auto → tv so movie-first
    # ``auto`` cannot win adult documentaries that share short title tokens.
    try:
        from pigeon.tmdb_poster import prefer_media_for_streaming_service

        out["prefer"] = prefer_media_for_streaming_service(
            str(out.get("prefer") or "auto"),
            app_name=str(out.get("app_name") or "") or None,
            app_id=str(out.get("app_id") or "") or None,
        )
    except Exception:
        pass
    # System output level when the protocol exposes it (often 0.0–100.0; not all devices).
    try:
        audio = atv.audio
        vol = getattr(audio, "volume", None)
        if vol is not None:
            vf = float(vol)  # type: ignore[arg-type]
            if vf == vf:
                out["volume_percent"] = max(0.0, min(100.0, vf))
    except Exception:
        pass
    return out


def _playing_debug_summary(playing) -> str:
    """Best-effort one-line dump of relevant now-playing fields (for UI error messages)."""
    def g(name: str) -> str | None:
        v = getattr(playing, name, None)
        if v is None:
            return None
        s = str(v).strip()
        return s if s else None

    parts: list[str] = []
    for k in ("device_state", "media_type", "app", "title", "series_name", "album", "artist"):
        v = g(k)
        if v is not None:
            parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "no fields"


def _protocol_name(protocol) -> str:
    if protocol is None:
        return "Automatic"
    try:
        return str(getattr(protocol, "name", protocol))
    except Exception:
        return str(protocol)


def _playing_field_lines(playing) -> list[str]:
    fields = (
        "device_state",
        "media_type",
        "app",
        "title",
        "series_name",
        "season_number",
        "episode_number",
        "album",
        "artist",
        "genre",
        "position",
        "total_time",
        "shuffle",
        "repeat",
        "hash",
    )
    lines: list[str] = []
    for field in fields:
        value = getattr(playing, field, None)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        lines.append(f"{field}: {text}")
    return lines


def _has_session_hash(playing) -> bool:
    value = getattr(playing, "hash", None)
    if value is None:
        return False
    return bool(str(value).strip())


def _resolve_archived_value(value, objects):
    if isinstance(value, plistlib.UID):
        try:
            return _resolve_archived_value(objects[value.data], objects)
        except Exception:
            return None
    if isinstance(value, dict):
        return {key: _resolve_archived_value(val, objects) for key, val in value.items()}
    if isinstance(value, list):
        return [_resolve_archived_value(item, objects) for item in value]
    return value


def _decode_nskeyed_root(archive: bytes):
    data = plistlib.loads(archive)
    objects = data.get("$objects", [])
    top = data.get("$top", {})
    root = top.get("root")
    return _resolve_archived_value(root, objects)


def _title_from_now_playing_archive(archive: bytes) -> str | None:
    try:
        root = _decode_nskeyed_root(archive)
    except Exception:
        return None
    if not isinstance(root, dict):
        return None
    try:
        from pigeon.tmdb_poster import (
            refine_tmdb_search_query,
            resolve_tmdb_query_from_now_playing_fields,
            tmdb_match_forgiving,
        )
    except ImportError:

        def refine_tmdb_search_query(x: str | None) -> str | None:  # type: ignore[misc]
            return (x or "").strip() or None

        def resolve_tmdb_query_from_now_playing_fields(*, base_query, **kwargs):  # type: ignore[misc]
            return refine_tmdb_search_query(base_query) if base_query else None

        def tmdb_match_forgiving(*, override=None):  # type: ignore[misc]
            return bool(override) if override is not None else False

    def _pick_refined(*candidates: object) -> str | None:
        fg = tmdb_match_forgiving()
        for c in candidates:
            if isinstance(c, str) and c.strip():
                s = c.strip()
                if fg:
                    r = refine_tmdb_search_query(s)
                    if r:
                        return r
                else:
                    return s
        return None

    metadata = root.get("metadata")
    sn: object | None
    ti: object | None
    ep: object | None
    if isinstance(metadata, dict):
        sn = metadata.get("seriesName")
        ti = metadata.get("title")
        ep = metadata.get("episodeTitle")
        base = _pick_refined(sn, ti, ep)
    else:
        sn = root.get("seriesName")
        ti = root.get("title")
        ep = root.get("episodeTitle")
        base = _pick_refined(sn, ti, ep)
    return resolve_tmdb_query_from_now_playing_fields(
        base_query=base,
        title=ti,
        series_name=sn,
        artist=None,
        album=None,
        episode_title=ep,
    )


def _conf_services_summary(conf) -> str:
    """Best-effort summary of discovered services (protocol + pairing requirement)."""
    parts: list[str] = []
    services = getattr(conf, "services", None)
    if not services:
        return "services=none"
    for svc in services:
        proto = getattr(svc, "protocol", None)
        pairing = getattr(svc, "pairing", None)
        enabled = getattr(svc, "enabled", None)
        parts.append(f"{proto}(pairing={pairing},enabled={enabled})")
    return "services=" + "; ".join(parts)


def _conf_looks_like_apple_tv(conf) -> bool:
    """True if discovery includes tvOS-style control (MRP or Companion), not AirPlay-only speakers."""
    from pyatv.const import Protocol

    for service in getattr(conf, "services", []) or []:
        if not getattr(service, "enabled", True):
            continue
        p = getattr(service, "protocol", None)
        if p in (Protocol.MRP, Protocol.Companion):
            return True
    return False


def _format_device_label(*, name: str, addr: str, ident: str, looks_like_atv: bool) -> str:
    """
    Human-visible line for lists. Same mDNS name can appear on Apple TV vs AirPlay receiver—
    always show address, capability, and a short id so rows stay distinct.
    """
    capability = "Apple TV / tvOS" if looks_like_atv else "AirPlay / other"
    i = str(ident or "").strip() or str(addr)
    if len(i) > 14:
        ident_short = f"{i[:6]}…{i[-4:]}"
    else:
        ident_short = i
    return f"{name} — {addr} — {capability} — id {ident_short}"


def _device_row(conf) -> dict[str, str]:
    addr = str(conf.address)
    ident = conf.identifier
    if not ident:
        ident = addr
    name = getattr(conf, "name", None) or "Apple TV"
    looks = _conf_looks_like_apple_tv(conf)
    return {
        "identifier": ident,
        "address": addr,
        "name": name,
        "label": _format_device_label(
            name=str(name), addr=addr, ident=str(ident), looks_like_atv=looks
        ),
        "services": _conf_services_summary(conf),
        "looks_like_apple_tv": "true" if looks else "false",
    }


async def _fetch_title_with_connection(
    atv,
    *,
    protocol_label: str,
) -> tuple[bool, str, str | None, bool]:
    last_playing = None
    for _ in range(6):  # ~3s total
        playing = await atv.metadata.playing()
        last_playing = playing
        q = _tmdb_query_from_playing(playing)
        if q:
            return True, f"{protocol_label}: {q}", q, False
        await asyncio.sleep(0.5)

    playing = last_playing
    from pyatv.const import DeviceState

    if playing is None:
        return False, f"{protocol_label}: metadata unavailable", None, False
    session_detected = _has_session_hash(playing)
    if playing.device_state in (DeviceState.Idle, DeviceState.Stopped):
        return (
            False,
            f"{protocol_label}: idle/stopped ({_playing_debug_summary(playing)})",
            None,
            session_detected,
        )
    return (
        False,
        f"{protocol_label}: no usable title ({_playing_debug_summary(playing)})",
        None,
        session_detected,
    )


async def _connect_with_protocol(pyatv, conf, loop, storage, protocol):
    if protocol is None:
        return await pyatv.connect(conf, loop, storage=storage)
    return await pyatv.connect(conf, loop, protocol=protocol, storage=storage)


async def _try_raw_companion_now_playing(conf, settings, loop) -> tuple[bool, str, str | None]:
    from pyatv.const import Protocol
    from pyatv.core import CoreStateDispatcher, create_core
    from pyatv.protocols.companion.api import CompanionAPI
    from pyatv.support.http import create_session
    from pyatv.support.state_producer import StateProducer

    service = conf.get_service(Protocol.Companion)
    if service is None or not service.credentials:
        return False, "Raw Companion: missing paired Companion credentials", None

    session_manager = await create_session()
    core = await create_core(
        conf,
        service,
        settings=settings,
        device_listener=StateProducer(),
        session_manager=session_manager,
        core_dispatcher=CoreStateDispatcher(),
        loop=loop,
    )
    api = CompanionAPI(core)
    received = asyncio.Event()
    payload_holder: dict[str, object] = {}

    async def _handle_now_playing(data):
        payload_holder["data"] = data
        received.set()

    api.listen_to("NowPlayingInfo", _handle_now_playing)
    try:
        await api.connect()
        await api.subscribe_event("NowPlayingInfo")
        try:
            await api._send_command("FetchCurrentNowPlayingInfoEvent", {})
        except Exception as ex:
            return False, f"Raw Companion fetch failed: {ex}", None
        try:
            await asyncio.wait_for(received.wait(), timeout=4.0)
        except asyncio.TimeoutError:
            return False, "Raw Companion: no NowPlayingInfo event received", None
        data = payload_holder.get("data")
        if not isinstance(data, dict):
            return False, "Raw Companion: invalid NowPlayingInfo payload", None
        archive = data.get("NowPlayingInfoKey")
        if not isinstance(archive, (bytes, bytearray)):
            return False, "Raw Companion: missing NowPlayingInfoKey archive", None
        title = _title_from_now_playing_archive(bytes(archive))
        if title:
            return True, f"Raw Companion: {title}", title
        return False, "Raw Companion: event received but no title metadata in archive", None
    finally:
        try:
            await api.disconnect()
        except Exception:
            pass
        try:
            await session_manager.close()
        except Exception:
            pass


def _candidate_protocols(conf) -> list:
    from pyatv.const import Protocol

    services = getattr(conf, "services", []) or []
    available = {
        getattr(service, "protocol", None)
        for service in services
        if getattr(service, "enabled", True)
    }
    has_airplay = Protocol.AirPlay in available
    order = []
    for protocol in (Protocol.MRP, Protocol.Companion, None):
        if protocol is None or protocol in available or (protocol == Protocol.MRP and has_airplay):
            order.append(protocol)
    if not order:
        order.append(None)
    deduped = []
    for protocol in order:
        if protocol not in deduped:
            deduped.append(protocol)
    return deduped


def _ensure_pyatv() -> None:
    import pyatv  # noqa: F401


async def _create_storage(loop):
    from pyatv.settings import MrpTunnel
    from pyatv.storage.file_storage import FileStorage

    storage = FileStorage(str(_credentials_file()), loop)
    try:
        await storage.load()
    except Exception:
        pass
    settings = getattr(storage, "settings", [])
    for entry in settings:
        try:
            entry.protocols.airplay.mrp_tunnel = MrpTunnel.Force
        except Exception:
            pass
    return storage


async def _clear_companion_credentials(storage, conf) -> None:
    from pyatv.const import Protocol

    try:
        settings = await storage.get_settings(conf)
        settings.protocols.companion.credentials = None
        service = conf.get_service(Protocol.Companion)
        if service is not None:
            service.credentials = None
        await storage.save()
    except Exception:
        pass


async def _clear_airplay_credentials(storage, conf) -> None:
    from pyatv.const import Protocol

    try:
        settings = await storage.get_settings(conf)
        settings.protocols.airplay.credentials = None
        service = conf.get_service(Protocol.AirPlay)
        if service is not None:
            service.credentials = None
        await storage.save()
    except Exception:
        pass


async def _close_atv(atv) -> None:
    """Close a pyatv session and await its teardown tasks.

    pyatv's ``AppleTV.close()`` is synchronous and returns a set of pending
    disconnect tasks (session manager + per-protocol teardown). ``await
    atv.close()`` therefore raises TypeError and leaves those tasks pending —
    when the caller's short-lived event loop closes they are destroyed and
    every poll logs "Event loop is closed" / "Task was destroyed".
    """
    if atv is None:
        return
    try:
        result = atv.close()
    except Exception:
        return
    try:
        if asyncio.iscoroutine(result):
            await result
        elif isinstance(result, (set, list, tuple)) and result:
            await asyncio.wait(set(result), timeout=3.0)
    except Exception:
        pass


def _new_loop_run(coro):
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        try:
            # Let straggler teardown tasks (pyatv disconnects, transport
            # close callbacks) finish before the loop closes; cancel and
            # reap whatever survives the grace period.
            pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
            if pending:
                loop.run_until_complete(asyncio.wait(pending, timeout=2.0))
                leftover = [t for t in pending if not t.done()]
                for t in leftover:
                    t.cancel()
                if leftover:
                    loop.run_until_complete(
                        asyncio.gather(*leftover, return_exceptions=True)
                    )
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.stop()
        except Exception:
            pass
        loop.close()


async def _async_scan_devices(*, scan_timeout_s: int) -> tuple[bool, str, list[dict[str, str]]]:
    import pyatv

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)
    atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage)
    rows: list[dict[str, str]] = []
    for conf in atvs:
        rows.append(_device_row(conf))
    if not rows:
        return False, "No devices found on the network. Check Wi‑Fi and try again.", []
    return True, f"Found {len(rows)} device(s).", rows


def scan_apple_tv_devices(*, scan_timeout_s: int = 10) -> tuple[bool, str, list[dict[str, str]]]:
    """
    Blocking: scan the LAN for Apple TVs.

    Returns rows with keys ``identifier``, ``address``, ``label`` (for UI).
    If ``identifier`` was missing from discovery, it equals ``address`` (connect by host).
    """
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv", []
    return _new_loop_run(_async_scan_devices(scan_timeout_s=scan_timeout_s))


async def _async_probe_pyatv_host(
    host: str, *, scan_timeout_s: int = 6
) -> tuple[bool, str, dict[str, str] | None, bool]:
    """
    Scan a single IP/hostname. Returns (ok, message, row, looks_like_apple_tv).

    AirPlay receivers (e.g. Denon) often share a network with Apple TV but have their *own* IP.
    If the user pastes the wrong address, pyatv will happily show the receiver's name.
    """
    import pyatv

    h = str(host or "").strip()
    if not h:
        return False, "Empty host.", None, False
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)
    atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[h])
    if not atvs:
        return False, f"No pyatv-supported device responded at {h}.", None, False
    conf = atvs[0]
    row = _device_row(conf)
    looks = _conf_looks_like_apple_tv(conf)
    summ = row.get("services", "")
    msg = f"Found “{row['name']}” at {row['address']}. {summ}"
    return True, msg, row, looks


def probe_pyatv_host(host: str, *, scan_timeout_s: int = 6) -> tuple[bool, str, dict[str, str] | None, bool]:
    """Blocking: inspect one IP/host; use before trusting manual entry."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed.", None, False
    return _new_loop_run(_async_probe_pyatv_host(host, scan_timeout_s=scan_timeout_s))


async def _async_pairing_credentials_status(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 5,
) -> tuple[bool, bool]:
    """Return (companion_credentials_present, airplay_credentials_present) for a device."""
    import pyatv

    ident = str(device_identifier or "").strip()
    addr = str(device_address or "").strip()
    if not ident and not addr:
        return False, False
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)
    use_hosts_only = ident == addr
    if use_hosts_only and addr:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[addr])
    elif ident:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, identifier=ident)
    elif addr:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[addr])
    else:
        return False, False
    if not atvs:
        return False, False
    conf = atvs[0]
    try:
        settings = await storage.get_settings(conf)
        comp = getattr(getattr(settings.protocols, "companion", None), "credentials", None)
        ap = getattr(getattr(settings.protocols, "airplay", None), "credentials", None)
        return bool(comp), bool(ap)
    except Exception:
        return False, False


def apple_tv_pairing_credentials_status(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 5,
) -> tuple[bool, bool]:
    """Blocking: whether pyatv FileStorage has Companion / AirPlay credentials for this device."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, False
    return _new_loop_run(
        _async_pairing_credentials_status(
            device_identifier=device_identifier,
            device_address=device_address,
            scan_timeout_s=scan_timeout_s,
        )
    )


async def _async_fetch_title_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
) -> tuple[bool, str, str | None]:
    import pyatv

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    # When discovery fell back to address-only, connect via hosts=...
    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address]
        )
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan.", None

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    services_summary = _conf_services_summary(conf)
    attempt_notes: list[str] = []
    session_detected = False
    try:
        settings = await storage.get_settings(conf)
        airplay_credentials = getattr(settings.protocols.airplay, "credentials", None)
    except Exception:
        airplay_credentials = None
    for protocol in _candidate_protocols(conf):
        protocol_label = _protocol_name(protocol)
        atv = None
        try:
            atv = await _connect_with_protocol(pyatv, conf, loop, storage, protocol)
            ok_playing, msg_playing, query, saw_session = await _fetch_title_with_connection(
                atv, protocol_label=protocol_label
            )
            session_detected = session_detected or saw_session
            if ok_playing and query:
                return True, f'Now playing on "{name}" via {protocol_label}: {query}', query
            attempt_notes.append(msg_playing)
        except Exception as e:
            attempt_notes.append(f"{protocol_label}: {e}")
        finally:
            if atv is not None:
                try:
                    await _close_atv(atv)
                except Exception:
                    pass

    try:
        raw_ok, raw_msg, raw_query = await _try_raw_companion_now_playing(conf, settings, loop)
        attempt_notes.append(raw_msg)
        if raw_ok and raw_query:
            return True, f'Now playing on "{name}" via raw Companion: {raw_query}', raw_query
    except Exception as e:
        attempt_notes.append(f"Raw Companion: {e}")

    attempts = " | ".join(attempt_notes) if attempt_notes else "no protocol attempts completed"
    if session_detected:
        return (
            False,
            f'"{name}": Apple TV playback session detected, but tvOS did not expose title metadata '
            f"through available protocols. Use the command bar to type the title manually for TMDb. "
            f"({services_summary}) Attempts: {attempts}",
            None,
        )
    if not airplay_credentials:
        return (
            False,
            f'"{name}": no usable metadata was returned, and no stored AirPlay credentials were found '
            f"for forcing the AirPlay MRP tunnel. Pair AirPlay for this Apple TV, then retry. "
            f"({services_summary}) Attempts: {attempts}",
            None,
        )
    return (
        False,
        f'"{name}": unable to read now playing from available protocols. '
        f"({services_summary}) Attempts: {attempts}",
        None,
    )


def fetch_now_playing_title_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 10,
) -> tuple[bool, str, str | None]:
    """Blocking: connect to one Apple TV (by identifier or address) and return a TMDb title string."""
    try:
        _ensure_pyatv()
    except ImportError:
        return (
            False,
            "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv",
            None,
        )
    return _new_loop_run(
        _async_fetch_title_for_device(
            device_identifier=device_identifier,
            device_address=device_address,
            scan_timeout_s=scan_timeout_s,
        )
    )


async def _async_fetch_now_playing_info_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
) -> tuple[bool, str, dict[str, object] | None]:
    import pyatv

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address]
        )
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan.", None

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    last_metadata: dict[str, object] | None = None
    attempt_notes: list[str] = []
    best_metadata: dict[str, object] | None = None
    best_score = 0

    def _is_floatish(v: object) -> bool:
        if v is None:
            return False
        try:
            float(v)  # type: ignore[arg-type]
            return True
        except (TypeError, ValueError):
            return False

    def _score_metadata(m: dict[str, object]) -> int:
        # Prefer metadata that includes both position and total_time,
        # because Pigeon’s timecode/progress and “Content” dashboard depend on it.
        from pigeon.raw_title import metadata_has_playback_title

        score = 0
        if m.get("query"):
            score += 2
        elif metadata_has_playback_title(m):
            score += 1
        else:
            return 0
        if _is_floatish(m.get("position")):
            score += 4
        if _is_floatish(m.get("total_time")):
            score += 2
        ds = str(m.get("device_state") or "")
        if "Playing" in ds:
            score += 1
        return score
    for protocol in _candidate_protocols(conf):
        protocol_label = _protocol_name(protocol)
        atv = None
        try:
            atv = await _connect_with_protocol(pyatv, conf, loop, storage, protocol)
            for _ in range(3):
                playing = await atv.metadata.playing()
                metadata = _metadata_with_app(atv, _playing_metadata(playing))
                try:
                    from pigeon.raw_title import resolve_metadata_tmdb_query

                    pyatv_q = str(metadata.get("query") or "").strip()
                    resolved_q = resolve_metadata_tmdb_query(metadata)
                    merged_q = pyatv_q or resolved_q
                    if merged_q:
                        metadata = dict(metadata)
                        metadata["query"] = merged_q
                except Exception:
                    pass
                last_metadata = metadata
                score = _score_metadata(metadata)
                if score > best_score:
                    metadata_copy = dict(metadata)
                    metadata_copy["protocol"] = protocol_label
                    best_metadata = metadata_copy
                    best_score = score
                # Perfect enough: title + position + total + playing-ish.
                if score >= 7:
                    assert best_metadata is not None
                    best_metadata = await _attach_music_artwork_bytes(atv, best_metadata)
                    return True, f'Now playing on "{name}" via {protocol_label}', best_metadata
                await asyncio.sleep(0.4)
            # Music artwork while still connected (lower scores never hit the early return).
            if (
                best_metadata is not None
                and best_metadata.get("protocol") == protocol_label
            ):
                best_metadata = await _attach_music_artwork_bytes(atv, best_metadata)
            if last_metadata is not None:
                attempt_notes.append(
                    f"{protocol_label}: {_playing_debug_summary(playing)}"
                )
        except Exception as e:
            attempt_notes.append(f"{protocol_label}: {e}")
        finally:
            if atv is not None:
                try:
                    await _close_atv(atv)
                except Exception:
                    pass

    if best_metadata is not None:
        return True, f'Now playing on "{name}" via {best_metadata.get("protocol") or "Automatic"}', best_metadata
    if last_metadata is not None:
        try:
            from pigeon.raw_title import resolve_metadata_tmdb_query

            if resolve_metadata_tmdb_query(last_metadata):
                return (
                    True,
                    f'Now playing on "{name}" (partial metadata)',
                    last_metadata,
                )
        except Exception:
            pass
        return False, " | ".join(attempt_notes) if attempt_notes else "No usable title", last_metadata
    return False, "No now playing metadata available.", None


def fetch_now_playing_info_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 10,
) -> tuple[bool, str, dict[str, object] | None]:
    """Blocking helper returning query + playback timing metadata for a selected Apple TV."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv", None
    return _new_loop_run(
        _async_fetch_now_playing_info_for_device(
            device_identifier=device_identifier,
            device_address=device_address,
            scan_timeout_s=scan_timeout_s,
        )
    )


async def _async_atv_remote_close_session() -> None:
    atv = _ATV_REMOTE_STATE.get("atv")
    if atv is not None:
        try:
            await _close_atv(atv)
        except Exception:
            pass
    _ATV_REMOTE_STATE.clear()


async def _async_atv_remote_worker_one(
    device_identifier: str,
    device_address: str,
    method_name: str,
    scan_timeout_s: int,
) -> None:
    import pyatv

    m = (method_name or "").strip()
    if not m:
        return
    ident = (device_identifier or "").strip()
    addr = (device_address or ident).strip()
    if not ident:
        return
    key = (ident, addr)

    # The worker thread calls ``loop.run_until_complete(_async_atv_remote_worker_one(...))``,
    # so we're inside that loop and can fetch it here. (Previously ``loop`` was referenced
    # below as a free variable, which raised ``NameError`` on every reconnect — silently
    # swallowed by the worker's ``except Exception`` and surfaced as "remote hotkeys don't
    # work" once the cached session went stale.)
    loop = asyncio.get_running_loop()

    now = time.monotonic()
    if (
        _ATV_REMOTE_STATE.get("atv") is not None
        and (_ATV_REMOTE_LAST_USE_MONO[0] > 0.0)
        and (now - float(_ATV_REMOTE_LAST_USE_MONO[0])) > float(_ATV_REMOTE_IDLE_DISCONNECT_S)
    ):
        await _async_atv_remote_close_session()

    st_atv = _ATV_REMOTE_STATE.get("atv")
    if (
        st_atv is not None
        and _ATV_REMOTE_STATE.get("ident") == ident
        and _ATV_REMOTE_STATE.get("addr") == addr
    ):
        try:
            fn = getattr(st_atv.remote_control, m, None)
            if fn is None or not callable(fn):
                await _async_atv_remote_close_session()
            else:
                out = fn()
                if asyncio.iscoroutine(out):
                    await out
                _ATV_REMOTE_LAST_USE_MONO[0] = time.monotonic()
                return
        except Exception:
            await _async_atv_remote_close_session()

    await _async_atv_remote_close_session()

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    storage = await _create_storage(loop)
    use_hosts_only = ident == addr
    if use_hosts_only:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, hosts=[addr]
        )
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=ident
        )
    if not atvs:
        return

    conf = atvs[0]
    for protocol in _candidate_protocols(conf):
        atv = None
        try:
            atv = await _connect_with_protocol(pyatv, conf, loop, storage, protocol)
            fn = getattr(atv.remote_control, m, None)
            if fn is None or not callable(fn):
                try:
                    await _close_atv(atv)
                except Exception:
                    pass
                atv = None
                continue
            out = fn()
            if asyncio.iscoroutine(out):
                await out
            _ATV_REMOTE_STATE["atv"] = atv
            _ATV_REMOTE_STATE["ident"] = ident
            _ATV_REMOTE_STATE["addr"] = addr
            _ATV_REMOTE_STATE["storage"] = storage
            _ATV_REMOTE_LAST_USE_MONO[0] = time.monotonic()
            return
        except Exception:
            if atv is not None:
                try:
                    await _close_atv(atv)
                except Exception:
                    pass


def _atv_remote_worker_thread_main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    q = _ATV_REMOTE_QUEUE
    assert q is not None
    while True:
        ident, addr, method, scan_t = q.get()
        try:
            loop.run_until_complete(
                _async_atv_remote_worker_one(ident, addr, method, scan_t)
            )
        except Exception:
            try:
                loop.run_until_complete(_async_atv_remote_close_session())
            except Exception:
                pass


def _ensure_atv_remote_worker_started() -> None:
    global _ATV_REMOTE_QUEUE, _ATV_REMOTE_WORKER_THREAD
    with _ATV_REMOTE_WORKER_LOCK:
        if _ATV_REMOTE_WORKER_THREAD is not None and _ATV_REMOTE_WORKER_THREAD.is_alive():
            return
        _ATV_REMOTE_QUEUE = queue.Queue()
        _ATV_REMOTE_WORKER_THREAD = threading.Thread(
            target=_atv_remote_worker_thread_main,
            name="pigeon-atv-remote",
            daemon=True,
        )
        _ATV_REMOTE_WORKER_THREAD.start()


def enqueue_apple_tv_remote_command(
    *,
    device_identifier: str,
    device_address: str,
    method_name: str,
    scan_timeout_s: int = 3,
) -> bool:
    """
    Queue a ``RemoteControl`` method for the selected Apple TV (non-blocking).

    Uses a single background worker that **reuses one pyatv session**, so repeated volume /
    d-pad keys avoid a full ``scan`` + ``connect`` every time. Pairing and blocking helpers keep
    using ``send_remote_method_to_device`` / ``_new_loop_run``.
    """
    try:
        _ensure_pyatv()
    except ImportError:
        return False
    ident = (device_identifier or "").strip()
    if not ident:
        return False
    addr = (device_address or ident).strip()
    m = (method_name or "").strip()
    if not m:
        return False
    _ensure_atv_remote_worker_started()
    assert _ATV_REMOTE_QUEUE is not None
    _ATV_REMOTE_QUEUE.put((ident, addr, m, max(1, int(scan_timeout_s))))
    return True


async def _async_send_remote_method_to_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
    method_name: str,
) -> tuple[bool, str]:
    """Connect like metadata fetch and call ``remote_control.<method_name>()`` once."""
    import pyatv

    m = (method_name or "").strip()
    if not m:
        return False, "No remote method."

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address]
        )
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan."

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    last_err = ""
    for protocol in _candidate_protocols(conf):
        atv = None
        try:
            atv = await _connect_with_protocol(pyatv, conf, loop, storage, protocol)
            fn = getattr(atv.remote_control, m, None)
            if fn is None or not callable(fn):
                return False, f'Remote has no "{m}" on "{name}".'
            out = fn()
            if asyncio.iscoroutine(out):
                await out
            return True, f'"{m}" sent to "{name}".'
        except Exception as e:
            last_err = str(e)
        finally:
            if atv is not None:
                try:
                    await _close_atv(atv)
                except Exception:
                    pass

    return False, last_err or f'Could not send "{m}" to Apple TV.'


async def _async_send_play_pause_to_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
) -> tuple[bool, str]:
    return await _async_send_remote_method_to_device(
        device_identifier=device_identifier,
        device_address=device_address,
        scan_timeout_s=scan_timeout_s,
        method_name="play_pause",
    )


def send_remote_method_to_device(
    *,
    device_identifier: str,
    device_address: str,
    method_name: str,
    scan_timeout_s: int = 8,
) -> tuple[bool, str]:
    """Blocking: one pyatv ``RemoteControl`` method (full scan + connect each call).

    UI hot paths use ``enqueue_apple_tv_remote_command`` instead (reused session, lower latency).
    """
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv"
    if not (device_identifier or "").strip():
        return False, "No Apple TV selected."
    return _new_loop_run(
        _async_send_remote_method_to_device(
            device_identifier=device_identifier.strip(),
            device_address=(device_address or device_identifier).strip(),
            scan_timeout_s=scan_timeout_s,
            method_name=method_name.strip(),
        )
    )


def send_play_pause_to_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 8,
) -> tuple[bool, str]:
    """Blocking: send play/pause to the paired Apple TV (pyatv)."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv"
    if not (device_identifier or "").strip():
        return False, "No Apple TV selected."
    return _new_loop_run(
        _async_send_play_pause_to_device(
            device_identifier=device_identifier.strip(),
            device_address=(device_address or device_identifier).strip(),
            scan_timeout_s=scan_timeout_s,
        )
    )


async def _async_begin_companion_pairing(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
    tv_displays_pin: bool,
) -> tuple[bool, str, str | None, str | None]:
    """Start Companion pairing.

    ``tv_displays_pin``: True = user reads a 4-digit code from the TV and types it in Pigeon (the
    only mode exposed in the Pigeon settings UI). False = reverse-PIN: Pigeon generates a PIN for
    the user to type on the Apple TV — kept for ``atvremote`` / advanced use; pyatv cannot infer
    which mode the TV expects, so the caller must pass the correct value.
    """
    import pyatv
    from pyatv.const import Protocol

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address])
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan.", None, None

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    await _clear_companion_credentials(storage, conf)
    pairing = None
    try:
        pairing = await pyatv.pair(conf, Protocol.Companion, loop, storage=storage, name="Pigeon")
        await pairing.begin()
        reverse_pin: str | None = None
        if not tv_displays_pin:
            reverse_pin = f"{random.randint(0, 9999):04d}"
            pairing.pin(int(reverse_pin))
        session_key = device_identifier or device_address
        _PAIRING_SESSIONS[session_key] = {
            "loop": loop,
            "pairing": pairing,
            "storage": storage,
            "name": name,
            "reverse_pin": reverse_pin,
            "pair_label": "Companion",
        }
        pairing = None
        if reverse_pin:
            msg = (
                f'Use the Apple TV remote: enter this PIN on "{name}" '
                f'(Settings → Remotes and Devices → Remote App and Devices): {reverse_pin}'
            )
        else:
            msg = (
                f'When "{name}" shows a 4-digit code on screen, you will enter it in the next step. '
                "If no code appears, cancel and run pairing again — choose No when asked whether "
                "the TV shows a code."
            )
        return True, msg, session_key, reverse_pin
    except Exception as e:
        return False, f'Companion pairing failed for "{name}": {e}', None, None
    finally:
        if pairing is not None:
            try:
                await pairing.close()
            except Exception:
                pass


async def _async_finish_companion_pairing(session_key: str, pin_code: str) -> tuple[bool, str]:
    session = _PAIRING_SESSIONS.get(session_key)
    if not session:
        return False, "No active pairing session."
    pairing = session["pairing"]
    storage = session["storage"]
    name = session["name"]
    reverse_pin = session.get("reverse_pin")
    label = str(session.get("pair_label") or "Pairing")
    try:
        if reverse_pin is None:
            pairing.pin(int(pin_code))
        await pairing.finish()
        if pairing.has_paired:
            await storage.save()
            return True, f'{label} pairing succeeded for "{name}".'
        return False, f'{label} pairing did not complete for "{name}".'
    except Exception as e:
        return False, f'{label} pairing failed for "{name}": {e}'
    finally:
        try:
            await pairing.close()
        except Exception:
            pass
        _PAIRING_SESSIONS.pop(session_key, None)


def begin_companion_pairing_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 10,
    tv_displays_pin: bool = True,
) -> tuple[bool, str, str | None, str | None]:
    """Start Companion pairing and keep session alive until finish is called."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv", None, None

    result: concurrent.futures.Future = concurrent.futures.Future()

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ok, msg, session_key, reverse_pin = loop.run_until_complete(
                _async_begin_companion_pairing(
                    device_identifier=device_identifier,
                    device_address=device_address,
                    scan_timeout_s=scan_timeout_s,
                    tv_displays_pin=tv_displays_pin,
                )
            )
            if ok and session_key:
                session = _PAIRING_SESSIONS.get(session_key)
                if session is not None:
                    session["thread"] = threading.current_thread()
                result.set_result((ok, msg, session_key, reverse_pin))
                loop.run_forever()
            else:
                result.set_result((ok, msg, session_key, reverse_pin))
        except Exception as exc:
            result.set_result((False, str(exc), None, None))
        finally:
            try:
                loop.stop()
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return result.result(timeout=15)


async def _async_abandon_pairing_session(session_key: str) -> None:
    session = _PAIRING_SESSIONS.pop(session_key, None)
    if not session:
        return
    pairing = session.get("pairing")
    if pairing is not None:
        try:
            await pairing.close()
        except Exception:
            pass


def abandon_pairing_session(session_key: str) -> None:
    """Close and remove an in-progress pairing if the user cancels the PIN UI (Companion or AirPlay)."""
    session = _PAIRING_SESSIONS.get(session_key)
    if not session:
        return
    loop = session.get("loop")
    if loop is None:
        _PAIRING_SESSIONS.pop(session_key, None)
        return
    try:
        if loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(_async_abandon_pairing_session(session_key), loop)
            try:
                fut.result(timeout=12)
            except Exception:
                _PAIRING_SESSIONS.pop(session_key, None)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass
        else:
            _PAIRING_SESSIONS.pop(session_key, None)
    except Exception:
        _PAIRING_SESSIONS.pop(session_key, None)


def finish_companion_pairing_for_device(
    *,
    session_key: str,
    pin_code: str,
) -> tuple[bool, str]:
    """Finish a previously-started Companion or AirPlay pairing flow (same pyatv handler API)."""
    session = _PAIRING_SESSIONS.get(session_key)
    if not session:
        return False, "No active pairing session."
    cleaned_pin = "".join(ch for ch in str(pin_code) if ch.isdigit())
    if session.get("reverse_pin") is None:
        if len(cleaned_pin) != 4:
            return False, "Enter the 4-digit code shown on Apple TV."
        pin_for_async = cleaned_pin
    else:
        pin_for_async = cleaned_pin  # ignored in async when reverse_pin is set
    loop = session["loop"]
    future = asyncio.run_coroutine_threadsafe(
        _async_finish_companion_pairing(session_key, pin_for_async), loop
    )
    ok, msg = future.result(timeout=20)
    try:
        loop.call_soon_threadsafe(loop.stop)
    except Exception:
        pass
    return ok, msg


def pair_companion_for_device(
    *,
    device_identifier: str,
    device_address: str,
    pin_code: str,
    scan_timeout_s: int = 10,
) -> tuple[bool, str]:
    """Backward-compatible one-shot helper for Companion pairing."""
    ok, msg, session_key, reverse_pin = begin_companion_pairing_for_device(
        device_identifier=device_identifier,
        device_address=device_address,
        scan_timeout_s=scan_timeout_s,
        tv_displays_pin=True,
    )
    if not ok or not session_key:
        return False, msg
    if reverse_pin:
        return (
            False,
            f"This Apple TV expects PIN {reverse_pin} to be entered on the TV "
            "(Remote App and Devices). Use the in-app Pair Apple TV flow; one-shot pairing "
            "cannot show that PIN interactively.",
        )
    return finish_companion_pairing_for_device(session_key=session_key, pin_code=pin_code)


async def _async_begin_airplay_pairing(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
    tv_displays_pin: bool,
) -> tuple[bool, str, str | None, str | None]:
    """AirPlay pairing; same ``tv_displays_pin`` semantics as Companion (pyatv cannot infer this)."""
    import pyatv
    from pyatv.const import Protocol

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address])
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan.", None, None

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    await _clear_airplay_credentials(storage, conf)
    pairing = None
    try:
        pairing = await pyatv.pair(conf, Protocol.AirPlay, loop, storage=storage, name="Pigeon")
        await pairing.begin()
        reverse_pin: str | None = None
        if not tv_displays_pin:
            reverse_pin = f"{random.randint(0, 9999):04d}"
            pairing.pin(int(reverse_pin))
        session_key = f"airplay:{device_identifier or device_address}"
        _PAIRING_SESSIONS[session_key] = {
            "loop": loop,
            "pairing": pairing,
            "storage": storage,
            "name": name,
            "reverse_pin": reverse_pin,
            "pair_label": "AirPlay",
        }
        pairing = None
        if reverse_pin:
            msg = (
                f'On "{name}", open Settings → AirPlay (or the AirPlay pairing screen) and enter this PIN: '
                f"{reverse_pin}"
            )
        else:
            msg = (
                f'If a 4-digit code appears for "{name}" (often on Settings → AirPlay), enter it next. '
                "If nothing appears: assign the Apple TV to a HomeKit “Room”, set AirPlay access to "
                '"Anyone on the Same Network", then try again — or re-run pairing and choose No '
                "when asked if the TV shows a code."
            )
        return True, msg, session_key, reverse_pin
    except Exception as e:
        return False, f'AirPlay pairing failed for "{name}": {e}', None, None
    finally:
        if pairing is not None:
            try:
                await pairing.close()
            except Exception:
                pass


def begin_airplay_pairing_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 10,
    tv_displays_pin: bool = True,
) -> tuple[bool, str, str | None, str | None]:
    """Start AirPlay pairing and keep session alive until finish is called."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv", None, None

    result: concurrent.futures.Future = concurrent.futures.Future()

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            ok, msg, session_key, reverse_pin = loop.run_until_complete(
                _async_begin_airplay_pairing(
                    device_identifier=device_identifier,
                    device_address=device_address,
                    scan_timeout_s=scan_timeout_s,
                    tv_displays_pin=tv_displays_pin,
                )
            )
            if ok and session_key:
                result.set_result((ok, msg, session_key, reverse_pin))
                loop.run_forever()
            else:
                result.set_result((ok, msg, session_key, reverse_pin))
        except Exception as exc:
            result.set_result((False, str(exc), None, None))
        finally:
            try:
                loop.stop()
            except Exception:
                pass
            loop.close()

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return result.result(timeout=15)


async def _async_debug_metadata_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int,
    tries: int,
    delay_s: float,
) -> tuple[bool, str]:
    import pyatv

    pigeon_state_dir().mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    storage = await _create_storage(loop)

    use_hosts_only = device_identifier == device_address
    if use_hosts_only:
        atvs = await pyatv.scan(loop, timeout=scan_timeout_s, storage=storage, hosts=[device_address])
    else:
        atvs = await pyatv.scan(
            loop, timeout=scan_timeout_s, storage=storage, identifier=device_identifier
        )

    if not atvs:
        return False, "Selected Apple TV did not respond to scan."

    conf = atvs[0]
    name = getattr(conf, "name", None) or "Apple TV"
    lines = [
        f'Apple TV: {name}',
        f"Address: {conf.address}",
        f"Identifier: {conf.identifier}",
        _conf_services_summary(conf),
        "",
    ]

    any_success = False
    for protocol in _candidate_protocols(conf):
        protocol_label = _protocol_name(protocol)
        lines.append(f"[{protocol_label}]")
        atv = None
        try:
            atv = await _connect_with_protocol(pyatv, conf, loop, storage, protocol)
            any_success = True
            for attempt in range(max(1, tries)):
                lines.append(f"try {attempt + 1}:")
                playing = await atv.metadata.playing()
                field_lines = _playing_field_lines(playing)
                if field_lines:
                    lines.extend([f"  {field}" for field in field_lines])
                else:
                    lines.append("  no fields")
                if attempt + 1 < max(1, tries):
                    await asyncio.sleep(max(0.0, delay_s))
        except Exception as e:
            lines.append(f"  error: {e}")
        finally:
            if atv is not None:
                try:
                    await _close_atv(atv)
                except Exception:
                    pass
        lines.append("")

    if not any_success:
        return False, "\n".join(lines).strip()
    return True, "\n".join(lines).strip()


def debug_metadata_for_device(
    *,
    device_identifier: str,
    device_address: str,
    scan_timeout_s: int = 10,
    tries: int = 4,
    delay_s: float = 0.5,
) -> tuple[bool, str]:
    """Blocking metadata dump for a selected Apple TV across available protocols."""
    try:
        _ensure_pyatv()
    except ImportError:
        return False, "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv"
    return _new_loop_run(
        _async_debug_metadata_for_device(
            device_identifier=device_identifier,
            device_address=device_address,
            scan_timeout_s=scan_timeout_s,
            tries=tries,
            delay_s=delay_s,
        )
    )


def fetch_now_playing_title_for_tmdb(*, scan_timeout_s: int = 10) -> tuple[bool, str, str | None]:
    """
    Blocking: scan all Apple TVs and return the first usable now-playing title (legacy / scripts).

    Prefer :func:`scan_apple_tv_devices` + :func:`fetch_now_playing_title_for_device` in UI.
    """
    try:
        _ensure_pyatv()
    except ImportError:
        return (
            False,
            "The `pyatv` package is not installed. From Pigeon_python: pip install pyatv",
            None,
        )

    ok, msg, rows = scan_apple_tv_devices(scan_timeout_s=scan_timeout_s)
    if not ok or not rows:
        return False, msg or "No Apple TVs found.", None

    errors: list[str] = []
    for row in rows:
        o, m, t = fetch_now_playing_title_for_device(
            device_identifier=row["identifier"],
            device_address=row["address"],
            scan_timeout_s=scan_timeout_s,
        )
        if o and t:
            return o, m, t
        errors.append(m)

    return False, "No title from any device. " + " | ".join(errors), None
