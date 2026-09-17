"""In-process live Pigeon Core runtime state (local-first Core/Interface seam).

Persisted configuration stays in ``app_state``. This module holds serializable
live observations that the Interface can render without owning hardware logic.

Do not add networking here — keep a clean boundary for a future transport.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class PlaybackRuntimeState:
    playing: bool = False
    paused: bool = False
    progress: float = 0.0
    elapsed_text: str = ""
    remaining_text: str = ""
    service_name: str = ""
    content_mode: str = "video"  # video | music
    title: str = ""
    album: str = ""
    artist: str = ""


@dataclass
class ReceiverRuntimeState:
    host: str = ""
    reachable: bool = False
    standby: bool = False
    incoming: str = ""
    config: str = ""
    volume: str = ""
    volume_fraction: float = 0.0
    muted: bool = False


@dataclass
class ContentRuntimeState:
    title_key: str = ""
    display_title: str = ""
    searching: bool = False
    missing_art: bool = False


@dataclass
class SystemRuntimeState:
    display_view: int = 1
    clock_saver_active: bool = False
    device_id: str = ""


@dataclass
class PigeonCoreState:
    """Structured live Core state. Prefer sections over one flat dict."""

    playback: PlaybackRuntimeState = field(default_factory=PlaybackRuntimeState)
    receiver: ReceiverRuntimeState = field(default_factory=ReceiverRuntimeState)
    content: ContentRuntimeState = field(default_factory=ContentRuntimeState)
    system: SystemRuntimeState = field(default_factory=SystemRuntimeState)

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


# Process-local singleton — Interface reads; Core/services write.
_CORE_STATE = PigeonCoreState()


def core_state() -> PigeonCoreState:
    return _CORE_STATE


def update_receiver_runtime(
    *,
    host: str | None = None,
    reachable: bool | None = None,
    standby: bool | None = None,
    incoming: str | None = None,
    config: str | None = None,
    volume: str | None = None,
    volume_fraction: float | None = None,
    muted: bool | None = None,
) -> ReceiverRuntimeState:
    """Patch receiver live state (AVR poll / compose path)."""
    rx = _CORE_STATE.receiver
    if host is not None:
        rx.host = str(host or "").strip()
    if reachable is not None:
        rx.reachable = bool(reachable)
    if standby is not None:
        rx.standby = bool(standby)
    if incoming is not None:
        rx.incoming = str(incoming or "").strip()
    if config is not None:
        rx.config = str(config or "").strip()
    if volume is not None:
        rx.volume = str(volume or "").strip()
    if volume_fraction is not None:
        try:
            rx.volume_fraction = max(0.0, min(1.0, float(volume_fraction)))
        except (TypeError, ValueError):
            rx.volume_fraction = 0.0
    if muted is not None:
        rx.muted = bool(muted)
    return rx
