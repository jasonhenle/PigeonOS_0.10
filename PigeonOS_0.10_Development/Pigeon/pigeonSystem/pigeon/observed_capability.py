"""Live capability hints from player polls, merged with the static matrix (min of static vs observed)."""

from __future__ import annotations

from typing import Literal

from device_capability_matrix import FEATURES, device_row_stable_key, level_for

Level = Literal["none", "partial", "full"]

_LEVEL_ORDER = {"none": 0, "partial": 1, "full": 2}


def _min_level(a: Level, b: Level) -> Level:
    return a if _LEVEL_ORDER[a] <= _LEVEL_ORDER[b] else b


def _observed_state_key(loc_id: str) -> str:
    return f"observed_capability_live_v1.{str(loc_id or '').strip()}"


def _read_observed_store_for_location(loc_id: str) -> dict[str, dict[str, str]]:
    try:
        from pigeon.app_state import read_app_state
    except ImportError:
        return {}
    lid = str(loc_id or "").strip()
    if not lid:
        return {}
    raw = read_app_state()
    blob = raw.get(_observed_state_key(lid))
    if not isinstance(blob, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for sk, feats in blob.items():
        if not isinstance(sk, str) or not isinstance(feats, dict):
            continue
        fd: dict[str, str] = {}
        for fid, lvl in feats.items():
            if isinstance(fid, str) and isinstance(lvl, str) and lvl in _LEVEL_ORDER:
                fd[fid.strip()] = lvl  # type: ignore[assignment]
        if fd:
            out[sk.strip()] = fd
    return out


def effective_level_for(
    loc_id: str,
    device_cap_id: str,
    feature_id: str,
    stable_key: str,
) -> Level:
    static: Level = level_for(device_cap_id, feature_id)
    lid = str(loc_id or "").strip()
    if not lid:
        return static
    obs = _read_observed_store_for_location(lid)
    live = obs.get(str(stable_key), {}).get(str(feature_id))
    if live not in _LEVEL_ORDER:
        return static
    return _min_level(static, live)  # type: ignore[arg-type]


def clear_observed_capabilities_for_location(loc_id: str) -> None:
    try:
        from pigeon.app_state import pop_app_state_keys
    except ImportError:
        return
    lid = str(loc_id or "").strip()
    if not lid:
        return
    pop_app_state_keys(_observed_state_key(lid))


def _metadata_idle(metadata: dict[str, object]) -> bool:
    ds = str(metadata.get("device_state") or "")
    if "Idle" in ds or "Stopped" in ds:
        return True
    playing_now = "Playing" in ds
    q = str(metadata.get("query") or "").strip()
    return not playing_now and not q


def _infer_levels(
    ok: bool,
    metadata: dict[str, object] | None,
    *,
    idle: bool,
) -> dict[str, Level]:
    fids = [fid for _fn, fid in FEATURES]
    if not ok or metadata is None:
        return {fid: "none" for fid in fids}
    if idle:
        out: dict[str, Level] = {fid: "none" for fid in fids}
        out["play_state"] = "partial"
        return out

    md = metadata
    has_titleish = any(
        str(md.get(k) or "").strip()
        for k in ("query", "title", "series_name", "artist", "album")
    )
    playing = "Playing" in str(md.get("device_state") or "")
    pos_ok = False
    try:
        pos_raw = md.get("position")
        if pos_raw is not None and playing:
            float(pos_raw)
            pos_ok = True
    except (TypeError, ValueError):
        pass
    trt_ok = False
    try:
        tt = md.get("total_time")
        if tt is not None and float(tt) > 0:
            trt_ok = True
    except (TypeError, ValueError):
        pass

    inferred: dict[str, Level] = {}
    for _fn, fid in FEATURES:
        if fid == "title":
            inferred[fid] = "full" if has_titleish else "partial"
        elif fid == "service":
            inferred[fid] = "full" if str(md.get("app_name") or "").strip() else "partial"
        elif fid == "playback_position":
            if pos_ok:
                inferred[fid] = "full"
            elif playing:
                inferred[fid] = "partial"
            else:
                inferred[fid] = "none"
        elif fid == "trt":
            inferred[fid] = "partial" if trt_ok else "none"
        elif fid == "play_state":
            inferred[fid] = "full" if str(md.get("device_state") or "").strip() else "partial"
        elif fid in ("volume", "video_config", "audio_config"):
            inferred[fid] = "partial"
        else:
            inferred[fid] = "partial"
    return inferred


def update_observed_capabilities_from_receiver_poll(
    loc_id: str,
    receiver_row: dict[str, str] | None,
    *,
    denon_reachable: bool,
    denon_volume_usable: bool,
    denon_has_incoming: bool,
    denon_has_config: bool,
) -> None:
    """
    Reflect Denon/Marantz HTTP poll results in the Advanced matrix for the AVR row.

    Without this, receiver tiles used only the static matrix (e.g. volume=full) even when
    the receiver LED was red because the poll failed or volume was sourced from Roku instead.
    """
    try:
        from pigeon.app_state import write_app_state
    except ImportError:
        return
    lid = str(loc_id or "").strip()
    if not lid or not receiver_row:
        return
    sk = device_row_stable_key(dict(receiver_row))
    if not sk:
        return
    store = _read_observed_store_for_location(lid)
    if denon_reachable:
        feats: dict[str, str] = {}
        for _label, fid in FEATURES:
            if fid in ("title", "service", "playback_position", "trt"):
                feats[fid] = "none"
            elif fid == "volume":
                feats[fid] = "full" if denon_volume_usable else "partial"
            elif fid == "audio_config":
                feats[fid] = "full" if denon_has_incoming else "partial"
            elif fid == "video_config":
                feats[fid] = "full" if denon_has_config else "partial"
            elif fid == "play_state":
                feats[fid] = "partial"
            else:
                feats[fid] = "none"
        store[sk] = feats
    else:
        store[sk] = {fid: "none" for _label, fid in FEATURES}
    write_app_state(**{_observed_state_key(lid): store})


def update_observed_capabilities_from_player_poll(
    loc_id: str,
    player_row: dict[str, str],
    *,
    ok: bool,
    metadata: dict[str, object] | None,
) -> None:
    """On successful poll, merge inferred levels. On failure, drop live hints for this device so UI falls back to static matrix (Roku etc. are not "all none")."""
    try:
        from pigeon.app_state import write_app_state
    except ImportError:
        return
    lid = str(loc_id or "").strip()
    if not lid:
        return
    sk = device_row_stable_key(dict(player_row))
    if not sk:
        return
    store = _read_observed_store_for_location(lid)
    if not ok:
        store.pop(sk, None)
        write_app_state(**{_observed_state_key(lid): store})
        return
    if not isinstance(metadata, dict):
        store.pop(sk, None)
        write_app_state(**{_observed_state_key(lid): store})
        return
    idle = bool(_metadata_idle(metadata))
    inferred = _infer_levels(True, metadata, idle=idle)
    store[sk] = {fid: inferred[fid] for fid in inferred}
    write_app_state(**{_observed_state_key(lid): store})
