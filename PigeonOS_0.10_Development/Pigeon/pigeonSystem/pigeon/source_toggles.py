"""On/off switches for Pigeon data sources (Wi‑Fi, Apple TV metadata, HDMI OCR, audio).

Persisted in ``state.json`` as ``source_toggles``. Default is on so existing
devices keep working. Audio is reserved (no recognizer yet) but the switch
is stored so the settings tile can toggle now.
"""

from __future__ import annotations

from typing import Any

_KINDS = ("wifi", "metadata", "hdmi", "audio")
_DEFAULTS: dict[str, bool] = {
    "wifi": True,
    "metadata": True,
    "hdmi": True,
    "audio": True,
}


def read_source_toggles() -> dict[str, bool]:
    from pigeon.app_state import read_app_state

    raw = read_app_state().get("source_toggles")
    out = dict(_DEFAULTS)
    if isinstance(raw, dict):
        for kind in _KINDS:
            if kind in raw:
                out[kind] = bool(raw[kind])
    return out


def source_enabled(kind: str) -> bool:
    """True when that source is allowed to feed Pigeon."""
    key = str(kind or "").strip().lower()
    if key not in _KINDS:
        return True
    return bool(read_source_toggles().get(key, True))


def set_source_enabled(kind: str, enabled: bool) -> bool:
    key = str(kind or "").strip().lower()
    if key not in _KINDS:
        return False
    flags = read_source_toggles()
    flags[key] = bool(enabled)
    from pigeon.app_state import write_app_state

    write_app_state(source_toggles=flags)
    return flags[key]


def toggle_source(kind: str) -> bool:
    """Flip one source. Returns the new enabled value."""
    key = str(kind or "").strip().lower()
    return set_source_enabled(key, not source_enabled(key))


def apply_toggles_to_settings_state(state: Any) -> None:
    """Copy persisted flags onto ``MainSettingsState`` for the LED tiles."""
    flags = read_source_toggles()
    state.source_wifi_on = flags["wifi"]
    state.source_metadata_on = flags["metadata"]
    state.source_hdmi_on = flags["hdmi"]
    state.source_audio_on = flags["audio"]
    # HDMI LED follows a live video signal, not the toggle or a stale handle.
    try:
        from pigeon.hdmi_ocr import hdmi_capture_available

        state.pigeon_hdmi_ok = hdmi_capture_available()
    except Exception:
        pass
    state.pigeon_audio_ok = flags["audio"]


_IDENTITY_KEYS = (
    "query",
    "title",
    "artist",
    "series_name",
    "album",
    "episode_title",
)

# Extra poll fields that come from the same Apple TV / Roku metadata source.
# Used when redacting View 4; live strip keeps play/pause and lets OCR refill title.
_METADATA_DISPLAY_KEYS = _IDENTITY_KEYS + (
    "media_type",
    "prefer_pyatv_media",
    "inferred_prefer",
    "content_key",
    "position",
    "total_time",
    "season",
    "episode",
    "season_number",
    "episode_number",
    "season_index",
    "episode_index",
    "app_name",
    "app_id",
    "app",
    "bundle_identifier",
    "app_identifier",
    "bundle_id",
)


def strip_streaming_identity(metadata: dict[str, Any]) -> None:
    """Drop Apple TV / Roku title and position when the metadata source is off."""
    for key in _IDENTITY_KEYS:
        metadata[key] = ""
    metadata["position"] = None
    metadata["total_time"] = None


def redact_disabled_source_fields(metadata: dict[str, Any]) -> dict[str, Any]:
    """Copy of ``metadata`` with disabled sources removed (View 4 / debug)."""
    out = dict(metadata)
    if not source_enabled("metadata"):
        strip_streaming_identity(out)
        for key in _METADATA_DISPLAY_KEYS:
            out.pop(key, None)
    if not source_enabled("hdmi"):
        from pigeon.hdmi_ocr import clear_ocr_fields

        clear_ocr_fields(out)
    return out
