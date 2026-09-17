# device_capability_matrix.py — lives next to pigeon_0_9.py (avoids importing pigeon package).
# Static capability levels for Settings → Advanced matrix (replace with live probes later).

from __future__ import annotations

from typing import Dict, List, Literal, Tuple

Level = Literal["none", "partial", "full"]

FEATURES: List[Tuple[str, str]] = [
    ("Title", "title"),
    ("Service (app)", "service"),
    ("Playback position", "playback_position"),
    ("TRT", "trt"),
    ("Play state", "play_state"),
    ("Current volume", "volume"),
    ("Video config", "video_config"),
    ("Audio config", "audio_config"),
]

DEVICES: List[Tuple[str, str]] = [
    ("Apple TV", "appletv"),
    ("Roku", "roku"),
    ("Receiver", "receiver"),
    ("TV", "tv"),
    ("Projector", "projector"),
    ("Game", "game"),
    ("Other", "other"),
]

_MATRIX: Dict[Tuple[str, str], Level] = {
    ("appletv", "title"): "full",
    ("appletv", "service"): "full",
    ("appletv", "playback_position"): "full",
    ("appletv", "trt"): "partial",
    ("appletv", "play_state"): "full",
    ("appletv", "volume"): "full",
    ("appletv", "video_config"): "full",
    ("appletv", "audio_config"): "partial",
    ("roku", "title"): "partial",
    ("roku", "service"): "full",
    ("roku", "playback_position"): "partial",
    ("roku", "trt"): "none",
    ("roku", "play_state"): "partial",
    ("roku", "volume"): "none",
    ("roku", "video_config"): "partial",
    ("roku", "audio_config"): "partial",
    ("receiver", "title"): "none",
    ("receiver", "service"): "none",
    ("receiver", "playback_position"): "none",
    ("receiver", "trt"): "none",
    ("receiver", "play_state"): "partial",
    ("receiver", "volume"): "full",
    ("receiver", "video_config"): "partial",
    ("receiver", "audio_config"): "full",
    ("tv", "title"): "partial",
    ("tv", "service"): "partial",
    ("tv", "playback_position"): "partial",
    ("tv", "trt"): "partial",
    ("tv", "play_state"): "partial",
    ("tv", "volume"): "full",
    ("tv", "video_config"): "full",
    ("tv", "audio_config"): "partial",
    ("projector", "title"): "none",
    ("projector", "service"): "none",
    ("projector", "playback_position"): "none",
    ("projector", "trt"): "none",
    ("projector", "play_state"): "none",
    ("projector", "volume"): "partial",
    ("projector", "video_config"): "full",
    ("projector", "audio_config"): "none",
    # Conservative defaults until we add real probes.
    ("game", "title"): "none",
    ("game", "service"): "none",
    ("game", "playback_position"): "none",
    ("game", "trt"): "none",
    ("game", "play_state"): "none",
    ("game", "volume"): "partial",
    ("game", "video_config"): "partial",
    ("game", "audio_config"): "partial",
    ("other", "title"): "partial",
    ("other", "service"): "partial",
    ("other", "playback_position"): "partial",
    ("other", "trt"): "partial",
    ("other", "play_state"): "partial",
    ("other", "volume"): "partial",
    ("other", "video_config"): "partial",
    ("other", "audio_config"): "partial",
}


def level_for(device_id: str, feature_id: str) -> Level:
    return _MATRIX.get((device_id, feature_id), "none")


def device_row_display_name(row: dict[str, str]) -> str:
    nick = str(row.get("nickname") or "").strip()
    if nick:
        return nick
    nm = str(row.get("name") or "").strip()
    if nm:
        return nm
    return str(row.get("label") or "").strip() or "Device"


def device_row_stable_key(row: dict[str, str]) -> str:
    u = str(row.get("device_uid") or "").strip()
    if u:
        return u
    ident = str(row.get("identifier") or "").strip()
    adr = str(row.get("address") or "").strip()
    return f"legacy::{ident}::{adr}"


def _infer_streaming_cap_id(row: dict[str, str], row_is_playback_apple_tv) -> str:
    blob = " ".join(
        [
            str(row.get("identifier") or ""),
            str(row.get("name") or ""),
            str(row.get("label") or ""),
        ]
    ).lower()
    if row_is_playback_apple_tv(row):
        return "appletv"
    if "roku" in blob:
        return "roku"
    return "tv"


def cap_id_for_saved_row(
    row: dict[str, str],
    *,
    slot: str,
    row_is_playback_apple_tv,
) -> str:
    """
    Map a saved device row to a matrix profile id.

    Legacy ``capability_profile`` wins if set. Otherwise ``device_role`` (from Find device)
    selects the profile; Player slot falls back to Apple TV / Roku / TV inference.
    """
    cp = str(row.get("capability_profile") or "").strip().lower()
    if cp:
        return cp
    dr = str(row.get("device_role") or "").strip().lower()
    known = frozenset({"appletv", "roku", "receiver", "tv", "projector", "game", "other"})
    slot_default = {
        "streaming": "appletv",
        "av_receiver": "receiver",
        "tv": "tv",
        "projector": "projector",
        "game": "game",
        "other": "other",
    }
    if slot == "streaming":
        if dr in known and dr != "player":
            return dr
        return _infer_streaming_cap_id(row, row_is_playback_apple_tv)
    if dr in known:
        return dr
    return str(slot_default.get(slot, "other"))


def active_device_columns() -> list[tuple[str, str, str]]:
    """[(display_label, capability_profile_id, stable_key), ...] for saved devices in the current location."""
    try:
        from pigeon.app_state import (
            read_saved_slot_rows_all,
            read_saved_streaming_devices_all,
            row_is_playback_apple_tv,
        )
    except ImportError:
        return []

    def _filled(row: dict[str, str] | None) -> bool:
        if not row:
            return False
        return bool(str(row.get("identifier") or "").strip() and str(row.get("address") or "").strip())

    out: list[tuple[str, str, str]] = []

    for st in read_saved_streaming_devices_all():
        if not _filled(st):
            continue
        disp = device_row_display_name(st) or "Player"
        cap_id = cap_id_for_saved_row(st, slot="streaming", row_is_playback_apple_tv=row_is_playback_apple_tv)
        out.append((disp, cap_id, device_row_stable_key(st)))

    for av in read_saved_slot_rows_all("av_receiver"):
        if not _filled(av):
            continue
        disp = device_row_display_name(av) or "Receiver"
        out.append(
            (
                disp,
                cap_id_for_saved_row(av, slot="av_receiver", row_is_playback_apple_tv=row_is_playback_apple_tv),
                device_row_stable_key(av),
            )
        )

    for tv in read_saved_slot_rows_all("tv"):
        if not _filled(tv):
            continue
        disp = device_row_display_name(tv) or "TV"
        out.append(
            (disp, cap_id_for_saved_row(tv, slot="tv", row_is_playback_apple_tv=row_is_playback_apple_tv), device_row_stable_key(tv))
        )

    for pj in read_saved_slot_rows_all("projector"):
        if not _filled(pj):
            continue
        disp = device_row_display_name(pj) or "Projector"
        out.append(
            (
                disp,
                cap_id_for_saved_row(pj, slot="projector", row_is_playback_apple_tv=row_is_playback_apple_tv),
                device_row_stable_key(pj),
            )
        )

    for gm in read_saved_slot_rows_all("game"):
        if not _filled(gm):
            continue
        disp = device_row_display_name(gm) or "Game"
        out.append(
            (disp, cap_id_for_saved_row(gm, slot="game", row_is_playback_apple_tv=row_is_playback_apple_tv), device_row_stable_key(gm))
        )

    for ot in read_saved_slot_rows_all("other"):
        if not _filled(ot):
            continue
        disp = device_row_display_name(ot) or "Other"
        out.append(
            (disp, cap_id_for_saved_row(ot, slot="other", row_is_playback_apple_tv=row_is_playback_apple_tv), device_row_stable_key(ot))
        )

    return out
