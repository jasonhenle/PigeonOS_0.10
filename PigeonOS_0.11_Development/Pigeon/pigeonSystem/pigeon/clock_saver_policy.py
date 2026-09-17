"""Pure rules for when the idle clock saver should arm."""

from __future__ import annotations

# Paused title holds the paused plate until this span, then the saver takes over.
CLOCK_SAVER_PAUSED_AFTER_S = 30.0


def next_paused_since_mono(paused: bool, now: float, paused_since_mono: float) -> float:
    """Keep the pause-start stamp while paused; clear it on play / idle."""
    if not paused:
        return 0.0
    if float(paused_since_mono) <= 0.0:
        return float(now)
    return float(paused_since_mono)


def pause_hold_s(paused: bool, now: float, paused_since_mono: float) -> float:
    started = next_paused_since_mono(paused, now, paused_since_mono)
    if started <= 0.0:
        return 0.0
    return max(0.0, float(now) - started)


def clock_saver_due_for_pause(
    paused: bool,
    paused_for_s: float,
    *,
    after_s: float = CLOCK_SAVER_PAUSED_AFTER_S,
) -> bool:
    """True when loaded content has stayed paused long enough to show the saver."""
    return bool(paused) and float(paused_for_s) >= float(after_s)


def clock_saver_due_for_no_content(
    *,
    playing: bool,
    paused_with_content: bool,
    live: bool = False,
    content_idle: bool = True,
    incoming_audio: bool = False,
) -> bool:
    """True when nothing is playing and no paused title is holding now-playing."""
    if playing or paused_with_content or live or incoming_audio:
        return False
    return bool(content_idle)


def should_hold_paused_screen(
    *,
    paused_with_content: bool,
    has_backdrop: bool,
    clock_saver_active: bool,
) -> bool:
    """The paused plate yields once the clock saver owns the frame."""
    return bool(paused_with_content and has_backdrop and not clock_saver_active)


def tmdb_should_skip_refetch_on_resume(
    *,
    content_key: object | None,
    prev_content_key: object | None,
    has_tmdb_identity: bool,
) -> bool:
    """Same now-playing identity after pause/resume must not start a new TMDb worker."""
    ck = str(content_key or "").strip()
    prev = str(prev_content_key or "").strip()
    return bool(ck and prev and ck == prev and has_tmdb_identity)
