"""How sure we are about each now-playing field.

The player (pyatv / Roku) is trusted when it gives a real title. HDMI OCR
takes over when that title is missing or is only a service name. After an
app change, leftover identity is stale until a new source confirms it.

The UI should only put up fields at or above ``DISPLAY_MIN``.
"""

from __future__ import annotations

from typing import Any, Mapping

DISPLAY_MIN = 0.50
PYATV_IDENTITY = 0.90
OCR_IDENTITY = 0.65
OCR_PENDING = 0.45
STALE = 0.20
POSITION_LIVE = 0.90
ART_MATCHED = 0.80
APP_BADGE = 0.85


def is_placeholder_identity(value: str) -> bool:
    """True when a string is empty, a service name, or OCR junk."""
    text = str(value or "").strip()
    if not text:
        return True
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query

        if is_degenerate_tmdb_query(text):
            return True
    except ImportError:
        pass
    try:
        from pigeon.ocr_clues import looks_like_ocr_junk

        if looks_like_ocr_junk(text):
            return True
    except ImportError:
        pass
    return False


def parse_position(metadata: Mapping[str, Any] | None) -> float | None:
    md = metadata if isinstance(metadata, dict) else {}
    raw = md.get("position")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def playback_detected(metadata: Mapping[str, Any] | None) -> bool:
    """True when the player reports active playback."""
    md = metadata if isinstance(metadata, dict) else {}
    ds = str(md.get("device_state") or "").lower()
    return "playing" in ds


def has_foreground_app(metadata: Mapping[str, Any] | None) -> bool:
    md = metadata if isinstance(metadata, dict) else {}
    return bool(str(md.get("app_id") or md.get("app_name") or "").strip())


def player_metadata_adequate(metadata: Mapping[str, Any] | None) -> bool:
    """True when the player itself supplied a TMDb-ready title.

    An OCR-filled ``query`` does not count — HDMI is then in charge.
    """
    md = metadata if isinstance(metadata, dict) else {}
    source = str(md.get("identity_source") or "").strip().lower()
    if source in ("ocr", "stale"):
        return False
    query = str(md.get("query") or "").strip()
    if is_placeholder_identity(query):
        return False
    if source == "pyatv":
        return True
    ocr = str(md.get("ocr_title") or "").strip()
    if ocr and query.casefold() == ocr.casefold():
        return False
    return True


def ocr_is_in_charge(
    metadata: Mapping[str, Any] | None,
    *,
    hdmi_on: bool = True,
    hdmi_present: bool = True,
) -> bool:
    """Upcoming / no-meta services: HDMI owns identity when the player does not."""
    if not hdmi_on or not hdmi_present:
        return False
    return not player_metadata_adequate(metadata)


def identity_confidence(metadata: Mapping[str, Any] | None) -> float:
    md = metadata if isinstance(metadata, dict) else {}
    raw = md.get("identity_confidence")
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            pass
    source = str(md.get("identity_source") or "").strip().lower()
    if source == "pyatv" and player_metadata_adequate(md):
        return PYATV_IDENTITY
    if source == "ocr" and not is_placeholder_identity(str(md.get("query") or md.get("ocr_title") or "")):
        return OCR_IDENTITY
    if source == "stale":
        return STALE
    if player_metadata_adequate(md):
        return PYATV_IDENTITY
    if not is_placeholder_identity(str(md.get("ocr_title") or "")):
        return OCR_IDENTITY
    return 0.0


def identity_displayable(metadata: Mapping[str, Any] | None) -> bool:
    md = metadata if isinstance(metadata, dict) else {}
    query = str(md.get("query") or md.get("ocr_title") or "").strip()
    if is_placeholder_identity(query):
        return False
    return identity_confidence(md) >= DISPLAY_MIN


def position_confidence(*, advancing: bool, has_position: bool) -> float:
    if advancing and has_position:
        return POSITION_LIVE
    return 0.0


def art_confidence(*, identity_ok: bool, tmdb_matches: bool) -> float:
    if identity_ok and tmdb_matches:
        return ART_MATCHED
    return 0.0


def app_confidence(metadata: Mapping[str, Any] | None) -> float:
    return APP_BADGE if has_foreground_app(metadata) else 0.0


def content_should_stay_active(
    metadata: Mapping[str, Any] | None,
    *,
    hdmi_on: bool = True,
    hdmi_present: bool = True,
) -> bool:
    """Keep now-playing chrome up when we have something we can show or watch."""
    if identity_displayable(metadata):
        return True
    if playback_detected(metadata):
        return True
    if has_foreground_app(metadata) and ocr_is_in_charge(
        metadata, hdmi_on=hdmi_on, hdmi_present=hdmi_present
    ):
        return True
    return False


def scores_for_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    position_advancing: bool = False,
    tmdb_matches: bool = False,
    hdmi_on: bool = True,
    hdmi_present: bool = True,
) -> dict[str, float]:
    md = metadata if isinstance(metadata, dict) else {}
    ident = identity_confidence(md)
    has_pos = parse_position(md) is not None
    return {
        "identity": ident,
        "position": position_confidence(advancing=position_advancing, has_position=has_pos),
        "art": art_confidence(identity_ok=ident >= DISPLAY_MIN, tmdb_matches=tmdb_matches),
        "app": app_confidence(md),
        "ocr_charge": 1.0
        if ocr_is_in_charge(md, hdmi_on=hdmi_on, hdmi_present=hdmi_present)
        else 0.0,
    }


def mark_identity(
    metadata: dict[str, Any],
    *,
    source: str,
    confidence: float,
) -> None:
    metadata["identity_source"] = str(source or "").strip().lower()
    metadata["identity_confidence"] = max(0.0, min(1.0, float(confidence)))


def mark_stale(metadata: dict[str, Any]) -> None:
    mark_identity(metadata, source="stale", confidence=STALE)
