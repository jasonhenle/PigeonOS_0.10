import argparse
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

# Tk must initialize before OpenCV on macOS: cv2 pulls X11/SDL dylibs that otherwise break Tcl (Tcl_InitNotifier → abort).
import tkinter as tk
import tkinter.font as tkfont
import tkinter.messagebox as messagebox
import tkinter.scrolledtext as scrolledtext
import tkinter.simpledialog as simpledialog

import numpy as np
from PIL import Image, ImageTk
import cv2

# Status bar: one black pill cols 3–17, bar-shaped mask hole + translucent bar; rows 6–8 gradient.
# Remaining label spans cols 16–17 (was 17–18) so the right edge has one extra column of margin.
TRT_DISPLAY_ROW = 8.0  # nowPlaying band bottom aligns to row 9 (baseline of canvas)
TRT_PLAYED_COL = 3  # TRTPlayed (elapsed)
TRT_PLAYED_TEXT = "0"
TRT_REMAINING_COL = 16  # TRTRemaining (countdown); 2-wide → ends at col 17 (col 19 = breathing room)
TRT_REMAINING_TEXT = "1:00:00"
TRT_LABEL_SPAN_W = 2
TRT_LABEL_SPAN_H = 1
# Apple TV auto-poll; TRT labels on a steady ~1 Hz metronome (see _playback_ui_tick).
APPLE_TV_POLL_MS = 3000
APPLE_TV_IDLE_POLL_MS = 6000
APPLE_TV_FAIL_POLL_MAX_MS = 15000
RECEIVER_POLL_MS = 750
PLAYBACK_UI_TICK_MS = 1000  # fallback first delay only; actual spacing uses monotonic deadlines
# Title / TMDb logo (views 1/3/5; not view 2 visualizer): 5×2 top-right at grid (1.5, 13); top-aligned in cell.
TMDB_LOGO_ANCHOR_ROW = 1.5
TMDB_LOGO_TOP_RIGHT_COL = 13.0
TMDB_LOGO_SPAN_W = 5
TMDB_LOGO_SPAN_H = 2
TMDB_LOGO_FIT_SCALE = 0.88
# View 6 logo constraints: grow as large as possible while staying inside rows 2..5 and cols 2..18.
TMDB_LOGO_VIEW6_ANCHOR_ROW = 2
TMDB_LOGO_VIEW6_ANCHOR_COL = 2
TMDB_LOGO_VIEW6_SPAN_W = 17
TMDB_LOGO_VIEW6_SPAN_H = 4
TMDB_LOGO_VIEW6_FIT_SCALE = 1.0
VIEW_ONE_BADGE_COL_RIGHT = 4.0  # 2-wide badge left-aligns to grid column 2
VIEW_ONE_CLOCK_COL_RIGHT = 19.0  # clock right edge aligns to the right side of column 18
_CODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

_PROJECT_DIR = _CODE_DIR
if not os.path.isdir(os.path.join(_PROJECT_DIR, "pigeonAssets")):
    _parent = os.path.dirname(_PROJECT_DIR)
    if os.path.isdir(os.path.join(_parent, "pigeonAssets")):
        _PROJECT_DIR = _parent

from pigeon.app_state import (
    LOCATION_PRESET_ROOM_NAMES,
    add_empty_location_v2,
    append_device_to_location_slot,
    clear_all_persisted_devices_and_targets,
    clear_last_apple_tv,
    clear_last_receiver,
    merge_legacy_saved_receivers_into_av_slot,
    migrate_device_slots_from_legacy_if_needed,
    read_all_locations_v2,
    delete_location_v2,
    rename_location_v2,
    read_app_state,
    read_current_location_id,
    read_current_location_name,
    read_last_apple_tv,
    read_last_receiver,
    read_saved_av_receiver,
    read_saved_streaming_device,
    read_saved_streaming_devices_all,
    remove_device_at_slot_index,
    write_saved_game,
    write_saved_other,
    write_saved_projector,
    write_saved_tv,
    row_is_playback_apple_tv,
    set_current_location_id,
    write_app_state,
    write_last_apple_tv,
    write_last_receiver,
    write_saved_av_receiver,
    write_saved_streaming_device,
    write_location_wifi,
    advance_delegation_active,
    append_delegation_log_lines,
)
from pigeon.media_folders import (
    consolidate_legacy_pigeondata_media_folders,
    pigeon_pulled_media_dir,
    pigeon_reformatted_media_dir,
    purge_directory_contents,
)
from pigeon.compositing import cv_resize_interp
from pigeon.stage_background import bgr_to_tk_hex, get_stage_bgr, set_stage_bgr
from pigeon.tmdb_tt_contrast import GRADIENT_BGR_DARK, pick_gradient_bgr
from pigeon.runtime_paths import PIGEON_STATE_DIR_TILDE, pigeon_state_dir
from pigeon.version import version_string

try:
    from pigeon.tmdb_retry_log import append_entry as _tmdb_retry_log_append
    from pigeon.tmdb_retry_log import read_tail_lines as _tmdb_retry_log_read_tail
except ImportError:

    def _tmdb_retry_log_append(_entry: dict) -> None:
        pass

    def _tmdb_retry_log_read_tail(_max_lines: int = 120) -> list[str]:
        return []


def _log_optional_import_failure(group: str, exc: BaseException) -> None:
    """Report which optional UI group failed without silencing the root cause."""
    import traceback

    name = getattr(exc, "name", None) or ""
    detail = f"{type(exc).__name__}: {exc}"
    if name:
        detail = f"{detail} (module={name})"
    msg = f"pigeon: optional import group {group!r} disabled — {detail}"
    try:
        sys.stderr.write(msg + "\n")
        sys.stderr.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        sys.stderr.flush()
    except Exception:
        pass
    try:
        from pigeon.pi_diagnostics import append_pigeon_log

        append_pigeon_log(msg)
    except Exception:
        pass


# Optional UI extension groups. A failure disables only that group.
_PIGEON_EXT = False
alpha_blend_bgra_over_bgr = None  # type: ignore[misc, assignment]
lerp_bgr_red_monochrome = None  # type: ignore[misc, assignment]
scale_bgra_rgb = None  # type: ignore[misc, assignment]
scale_height_and_center_crop = None  # type: ignore[misc, assignment]
scale_cover_center_crop = None  # type: ignore[misc, assignment]
scale_uniform_letterbox = None  # type: ignore[misc, assignment]
rect_for_span_at_cell = None  # type: ignore[misc, assignment]
rect_for_span_top_right_at_cell = None  # type: ignore[misc, assignment]
get_grid_geometry = None  # type: ignore[misc, assignment]
playback_lower_gradient_bgra = None  # type: ignore[misc, assignment]
DESIGN_W = DESIGN_H = 0
blend_overlay_bgr = None  # type: ignore[misc, assignment]
build_stage_overlay_source_bgra = None  # type: ignore[misc, assignment]
ClockCalendarWidget = None  # type: ignore[misc, assignment]
CLOCK_ANCHOR_ROW = 1.0
CLOCK_ANCHOR_COL = int(VIEW_ONE_CLOCK_COL_RIGHT)
LOCATION_TOAST_FULL_S = 15.0
LOCATION_TOAST_FADE_S = 2.0
location_toast_patch_bgra = None  # type: ignore[misc, assignment]
TmdbLogoWidget = None  # type: ignore[misc, assignment]
ViewOneVariant = None  # type: ignore[misc, assignment]
resolve_view_one_variant = None  # type: ignore[misc, assignment]
variant_has_alternate = None  # type: ignore[misc, assignment]
variant_uses_full_path = None  # type: ignore[misc, assignment]
render_ui_text_patch_bgra = None  # type: ignore[misc, assignment]
render_view_one_video_content_b_title_patch_bgra = None  # type: ignore[misc, assignment]
render_ui_music_text_patch_bgra = None  # type: ignore[misc, assignment]
load_pigeon_temp_logo_bgra = None  # type: ignore[misc, assignment]
StatusBarWidget = None  # type: ignore[misc, assignment]
clock_saver_composite_bgra = None  # type: ignore[misc, assignment]
PlaybackOverlayWidget = None  # type: ignore[misc, assignment]
compose_playback_volume_widget_line = None  # type: ignore[misc, assignment]
PATCH_LAYER_RECEIVER_AUDIO = "receiver_audio"  # type: ignore[misc, assignment]
PATCH_LAYER_STREAMING_BADGE = "streaming_badge"  # type: ignore[misc, assignment]
pigeon_wordmark_design_patch = None  # type: ignore[misc, assignment]
ViewCirclesWidget = None  # type: ignore[misc, assignment]
MainSettingsWidget = None  # type: ignore[misc, assignment]
build_info_cluster_design_patches = None  # type: ignore[misc, assignment]
INFO_CLUSTER_COL_RIGHT = 18.0
INFO_CLUSTER_CLOCK_ROW_1BASED = 1.0  # type: ignore[misc, assignment]
prepare_default_poster_at_startup = None  # type: ignore[misc, assignment]
metadata_has_playback_title = None  # type: ignore[misc, assignment]
resolve_metadata_tmdb_query = None  # type: ignore[misc, assignment]

# Splash symbols stay importable when splash group fails (call sites check paths).
FALLBACK_SPLASH_FRAME_COUNT = 0
SPLASH_FADE_OUT_FRAMES = 0
SPLASH_CLOCK_REVEAL_FRAME = 90
SPLASH_FPS = 30
SPLASH_MAX_DURATION_S = 0.0
apply_splash_global_alpha = None  # type: ignore[misc, assignment]
bgra_to_pil_rgba = None  # type: ignore[misc, assignment]
builtin_splash_bgra_frame = None  # type: ignore[misc, assignment]
composite_splash_over_bg = None  # type: ignore[misc, assignment]
find_splash_video_path = None  # type: ignore[misc, assignment]
flatten_bgra_over_bg_to_rgb = None  # type: ignore[misc, assignment]
list_splash_png_paths = None  # type: ignore[misc, assignment]
load_splash_bgra = None  # type: ignore[misc, assignment]
resolve_splash_media = None  # type: ignore[misc, assignment]
resize_bgra_if_needed = None  # type: ignore[misc, assignment]
splash_end_fade_factor = None  # type: ignore[misc, assignment]
splash_effective_frame_count = None  # type: ignore[misc, assignment]

try:
    from pigeon.compositing import (
        alpha_blend_bgra_over_bgr,
        lerp_bgr_red_monochrome,
        scale_bgra_rgb,
        scale_cover_center_crop,
        scale_height_and_center_crop,
        scale_uniform_letterbox,
    )
    from pigeon.design import (
        DESIGN_H,
        DESIGN_W,
        get_grid_geometry,
        playback_lower_gradient_bgra,
        rect_for_span_at_cell,
        rect_for_span_top_right_at_cell,
    )
    from pigeon.overlay import blend_overlay_bgr, build_stage_overlay_source_bgra

    _PIGEON_EXT = True
except ImportError as _exc:
    _log_optional_import_failure("core_compositing", _exc)

try:
    from pigeon.widgets.clock_calendar import (
        CLOCK_WIDGET_COL_RIGHT,
        CLOCK_WIDGET_ROW,
        ClockCalendarWidget,
    )

    CLOCK_ANCHOR_ROW = CLOCK_WIDGET_ROW
    CLOCK_ANCHOR_COL = int(VIEW_ONE_CLOCK_COL_RIGHT)
    from pigeon.widgets.location_toast import (
        LOCATION_TOAST_FADE_S,
        LOCATION_TOAST_FULL_S,
        location_toast_patch_bgra,
    )
    from pigeon.widgets.logo_tmdb import TmdbLogoWidget
    from pigeon.widgets.status_bar import StatusBarWidget
    from pigeon.widgets.clock_saver import clock_saver_composite_bgra
except ImportError as _exc:
    _log_optional_import_failure("clock_status_widgets", _exc)

try:
    from pigeon.view_one_variants import (
        ViewOneVariant,
        load_pigeon_temp_logo_bgra,
        render_ui_music_text_patch_bgra,
        render_ui_text_patch_bgra,
        render_view_one_video_content_b_title_patch_bgra,
        resolve_view_one_variant,
        variant_has_alternate,
        variant_uses_full_path,
    )
except ImportError as _exc:
    _log_optional_import_failure("view_one_variants", _exc)

try:
    from pigeon.widgets.playback_overlay import (
        PATCH_LAYER_RECEIVER_AUDIO,
        PATCH_LAYER_STREAMING_BADGE,
        PlaybackOverlayWidget,
        compose_playback_volume_widget_line,
        pigeon_wordmark_design_patch,
    )
except ImportError as _exc:
    _log_optional_import_failure("playback_widgets", _exc)

try:
    from pigeon.widgets.view_circles import ViewCirclesWidget
except ImportError as _exc:
    _log_optional_import_failure("view_circles", _exc)
    ViewCirclesWidget = None  # type: ignore[misc, assignment]

try:
    from pigeon.widgets.main_settings import MainSettingsWidget
except ImportError as _exc:
    _log_optional_import_failure("main_settings", _exc)
    MainSettingsWidget = None  # type: ignore[misc, assignment]

try:
    from pigeon.widgets.info_cluster import (
        INFO_CLUSTER_CLOCK_ROW_1BASED,
        INFO_CLUSTER_COL_RIGHT,
        build_info_cluster_design_patches,
    )
    from pigeon.widgets.poster_art import prepare_default_poster_at_startup
    from pigeon.raw_title import metadata_has_playback_title, resolve_metadata_tmdb_query
except ImportError as _exc:
    _log_optional_import_failure("info_poster_metadata", _exc)

try:
    from pigeon.splash_sequence import (
        FALLBACK_SPLASH_FRAME_COUNT,
        SPLASH_CLOCK_REVEAL_FRAME,
        SPLASH_FADE_OUT_FRAMES,
        SPLASH_FPS,
        SPLASH_MAX_DURATION_S,
        apply_splash_global_alpha,
        bgra_to_pil_rgba,
        builtin_splash_bgra_frame,
        composite_splash_over_bg,
        find_splash_video_path,
        flatten_bgra_over_bg_to_rgb,
        list_splash_png_paths,
        load_splash_bgra,
        resolve_splash_media,
        resize_bgra_if_needed,
        splash_effective_frame_count,
        splash_end_fade_factor,
    )
except ImportError as _exc:
    _log_optional_import_failure("splash_sequence", _exc)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# Native UI composes at 800×480. Window/display target matches the Pi panel (800×480).
UI_TARGET_W = 800
UI_TARGET_H = 480
DISPLAY_W = _env_int("PIGEON_DISPLAY_W", 800)
DISPLAY_H = _env_int("PIGEON_DISPLAY_H", 480)
try:
    _LAUNCH_WINDOW_SCALE = float(os.environ.get("PIGEON_WINDOW_SCALE", "1.0") or "1.0")
except ValueError:
    _LAUNCH_WINDOW_SCALE = 1.0
_LAUNCH_WINDOW_SCALE = max(0.25, min(_LAUNCH_WINDOW_SCALE, 4.0))
WINDOW_W = int(round(DISPLAY_W * _LAUNCH_WINDOW_SCALE))
WINDOW_H = int(round(DISPLAY_H * _LAUNCH_WINDOW_SCALE))


def _apple_tv_scan_timeout_s() -> int:
    default = 12 if sys.platform == "linux" else 6
    return _env_int("PIGEON_APPLE_TV_SCAN_TIMEOUT", default)


def _resize_bgr_to_dims(dst_w: int, dst_h: int, src: np.ndarray) -> np.ndarray:
    """Resize to window/cap size; LANCZOS4 when upscaling, AREA when downscaling."""
    sh, sw = int(src.shape[0]), int(src.shape[1])
    if sw == dst_w and sh == dst_h:
        return src
    return cv2.resize(
        src,
        (dst_w, dst_h),
        interpolation=cv_resize_interp(sw, sh, dst_w, dst_h),
    )


def _composite_cap_dims(display_w: int, display_h: int) -> tuple[int, int, bool]:
    """
    Internal composite size for video + mic EQ + UI blits.

    Always composes at the native 800×480 design resolution, then
    ``_present_frame_to_display`` letterboxes/pillarboxes to the live window when needed.
    """
    dw = max(1, int(display_w))
    dh = max(1, int(display_h))
    if dw == UI_TARGET_W and dh == UI_TARGET_H:
        return UI_TARGET_W, UI_TARGET_H, False
    return UI_TARGET_W, UI_TARGET_H, True


def _present_frame_to_display(image: np.ndarray, display_w: int, display_h: int) -> np.ndarray:
    """Scale entire frame into the window with black letterbox/pillarbox bars.

    Applies pixel-aspect compensation when the panel PAR is non-square (e.g. official
    Pi 7″ touchscreen) so designed circles stay round on glass.
    """
    dw = max(1, int(display_w))
    dh = max(1, int(display_h))
    try:
        from pigeon.display_par import apply_par_compensation

        return apply_par_compensation(image, display_w=dw, display_h=dh)
    except Exception:
        pass
    if int(image.shape[1]) == dw and int(image.shape[0]) == dh:
        return image
    if _PIGEON_EXT and scale_uniform_letterbox is not None:
        return scale_uniform_letterbox(image, dw, dh)
    return _resize_bgr_to_dims(dw, dh, image)


def _bgra_to_display_window(bgra: np.ndarray) -> np.ndarray:
    """Fit splash/UI BGRA (800×480) into the live display window with black bars when needed."""
    if not _PIGEON_EXT:
        return bgra
    assert resize_bgra_if_needed is not None
    fitted = resize_bgra_if_needed(bgra, UI_TARGET_W, UI_TARGET_H)
    try:
        from pigeon.display_par import apply_par_compensation

        return apply_par_compensation(fitted, display_w=WINDOW_W, display_h=WINDOW_H)
    except Exception:
        pass
    if scale_uniform_letterbox is not None:
        return scale_uniform_letterbox(fitted, WINDOW_W, WINDOW_H)
    return resize_bgra_if_needed(fitted, WINDOW_W, WINDOW_H)


# App-logo backdrop when TMDb has no art: letterbox canvas is at most this fraction of the live window.
APP_LOGO_FALLBACK_MAX_RESOLUTION_FRACTION = 0.9
# Reserved strip at bottom when developer mode is on (must not sit under the full-bleed video label).
OVERLAY_HUD_H = 52


class DevPhase(IntEnum):
    OFF = 0
    GRID = 1  # rare: Advanced matrix may stash this while open
    MAIN_SETTINGS = 2  # SVG settings_main + pigeon / prefs / update / keyboard


class DisplayView(IntEnum):
    """User-selectable display layout (keys 1, 4, 5). Unused values kept for stable ints."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5
    SIX = 6


class ViewOneLayout(IntEnum):
    """
    When ``DisplayView.ONE`` is active, Shift+1 cycles the layout toggle (historical
    full / simple / poster modes). View 1 always renders the 070326 now-playing screen.

    Member names are **historical** — they describe the happy-path visual that
    shipped before the v0.6.14 V01 ↔ V02 swap and the v0.6.19 view-naming
    refactor. The toggle's real meaning in the new vocabulary is
    ``viewOne.videoContent_a`` (initial) vs. ``viewOne.videoContent_b``
    (alternate). The V# variant that actually renders depends on this toggle
    *and* on which content assets are live (see
    ``pigeon.view_one_variants.resolve_view_one_variant``).

    ``PIGEON_FULL`` (0, initial) → ``viewOne.videoContent_a``: default mode.
    When all TMDb assets are present renders V01 — minimal pigeonTMDB_TT on
    black. With missing assets routes to V03 / V05 / V07 / V08 / V09.
    ``PIGEON_SIMPLE`` (1) → ``viewOne.videoContent_b``: alternate mode.
    Toggled on by pressing Shift+``1`` while on view 1. When all TMDb assets are
    present renders V02 — the full pigeonTMDB_BD + pigeonTMDB_TT + appLogo
    composition. With missing assets routes to V04 / V06.

    ``PIGEON_POSTER`` (2) → ``viewOne.videoContent_c``: poster mode. Keeps the
    same chrome stack as viewOne.videoContent_b but replaces TMDb TT/BD with
    ``pigeonTMDB_Poster`` centered horizontally in the top 7 rows.

    Related view identifiers in the new vocabulary (not modeled by this enum
    because they're driven by content presence or idle timers rather than a
    user toggle):

    * ``viewOne.audioContent`` — music-only content. A MediaType.Music
      override in ``_compose_shown_frame`` substitutes a two-line text patch
      ("Track title" + "Artist - Album") inside the pigeonTMDB_TT rect and
      suppresses the bottom gradient.
    * ``viewOne.startup`` / ``viewOne.noContent`` — V09 fallback; static Pigeon logo at 30% alpha.
    * ``viewOne.clockSaver_a`` / ``viewOne.clockSaver_b`` — the idle clock
      saver composites over whichever base (black or pigeonTMDB_BD) the
      active videoContent variant produced.
    """

    PIGEON_FULL = 0
    PIGEON_SIMPLE = 1
    PIGEON_POSTER = 2


# TMDb static backdrop scene (not paused-video dim 0.3).
BACKDROP_BRIGHTNESS = 0.8
# Static landing logo (no video): full brightness; old 0.3 “paused video” level hid the art.
LANDING_DISPLAY_BRIGHTNESS = 1.0
LANDING_DIM_BRIGHTNESS = 0.78  # Space-bar pulse “off” — still readable vs old 0.3
# After UI bootstrap, optional auto-restore of saved TMDb backdrop (env-gated) runs after this delay.
STARTUP_PIGEON_WORDMARK_MAX_S = 5.0
# After splash: optional startup transition timing (no mic EQ).
SKIP_POST_SPLASH_STARTUP_TRANSITION = True
# After splash lifts: optional clock ease-up from black (0 = full-on; PNG splash reveals saver).
CLOCK_STARTUP_FADE_S = 0.0
# If True and a saved TMDb backdrop exists, switch to it when this timer elapses.
STARTUP_AUTO_RESTORE_SAVED_BACKDROP = os.environ.get("PIGEON_STARTUP_RESTORE_BACKDROP", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Theater UI: red luma-mono only when neither Apple TV nor Pigeon UI shows activity this long.
# ATV side: poll metadata deltas (see _update_atv_interaction_from_poll_metadata).
# Pigeon side: mouse / keyboard / settings scroll (see _bump_pigeon_user_activity).
# For ATV, 0 = never bumped → treated as idle until the first remote-driven signal (still needs Pigeon idle).
THEATER_IDLE_DIM_AFTER_S = 45.0
# Red idle-dim is **off** by default until an ambient-light (or other) trigger is integrated.
# Re-enable anytime: ``PIGEON_THEATER_IDLE_DIM=1`` (or ``true`` / ``yes`` / ``on``). Explicit ``0`` / ``off`` keeps it off.
_THEATER_IDLE_DIM_ENV = os.environ.get("PIGEON_THEATER_IDLE_DIM", "").strip().lower()
THEATER_IDLE_DIM_ENABLED = (
    _THEATER_IDLE_DIM_ENV in ("1", "true", "yes", "on")
    if _THEATER_IDLE_DIM_ENV
    else False
)
# Ease in/out between full color and red luma-mono when idle-dim target changes.
ATV_IDLE_MONO_ANIM_S = 2.0
# Idle “clock saver” (screensaver): requires both ``CLOCK_SAVER_AFTER_S`` of **UI** inactivity (mouse/keys/etc.;
# mic does not touch those timestamps) **and** the same span without **significant** device signals (content
# selection, ``device_state``, or ``volume_percent`` from the player poll, plus receiver volume line changes).
CLOCK_SAVER_AFTER_S = 300.0
# Leave settings_main after this long with no mouse/key/rotary input.
SETTINGS_MENU_IDLE_EXIT_S = 60.0
# Saver text opacity when idle; tap while saver is up briefly uses 1.0 (see clock_saver_peek_until_mono).
CLOCK_SAVER_DIM_OPACITY = 0.54
CLOCK_SAVER_PEEK_S = 2.5
# With a TMDb backdrop visible: fade backdrop to black under the clock after this idle span on the saver.
# Clock saver: multiply backdrop RGB by this factor (0.3 → 30% brightness) while saver is active.
CLOCK_SAVER_BACKDROP_DIM = 0.3
# Position-stall grace: while content position has advanced within this window, the clock-saver
# "device signal" timestamp keeps being bumped to ``now``, so the 300 s saver timer stays reset.
# Once the reported playback position stays flat for longer than this, the timer resumes counting
# from the last advance toward ``CLOCK_SAVER_AFTER_S``. Tuned to survive short buffering stalls
# (typical re-buffer is < 2 s) without prematurely arming the saver. A position advance while the
# saver is already open ends it on the next render tick via the same bump.
CLOCK_SAVER_POSITION_STALL_GRACE_S = 5.0
# When player poll metadata stays unchanged for this long **and** no local Pigeon
# controls were used for the same span, force the clock saver on (independent of the
# 300 s UI+device idle path). Position not advancing is the primary signal; HDMI
# frame/title changes also refresh the stamp. When metadata is driving the saver,
# the HDMI 24-OCR streak rule is ignored.
CLOCK_SAVER_METADATA_IDLE_AFTER_S = 120.0

# Idle composite cadence when video is paused (lower than live playback).
PAUSED_COMPOSITE_MS = 83  # ~12 FPS

HOTKEY_BINDTAG = "Pigeon0_5_hotkeys"

def _paint_boolean_led(canvas: tk.Canvas, ok: bool | None) -> None:
    """Single lamp: green=detected+compatible, amber=detected+unknown/incompatible, red=not detected/not compatible."""
    canvas.delete("all")
    if ok is True:
        fill = "#1fcb5d"
    elif ok is None:
        fill = "#f0ad4e"
    else:
        fill = "#e74c3c"
    canvas.create_oval(2, 2, 14, 14, fill=fill, outline="#151518", width=1)


def _prepend_hotkey_bindtag(widget: tk.Misc, tag: str = HOTKEY_BINDTAG) -> None:
    widget.bindtags((tag,) + widget.bindtags())


def _widget_accepts_typing(widget: tk.Misc) -> bool:
    """True if Return/other keys should go to the widget (not global developer-mode shortcuts)."""
    try:
        cls = widget.winfo_class()
    except tk.TclError:
        return False
    if cls == "Text":
        try:
            if str(widget.cget("state")).lower() == "disabled":
                return False
        except tk.TclError:
            pass
        return True
    if cls in ("Entry",):
        return True
    if cls == "TEntry" or cls == "TCombobox":
        return True
    return False


def _raw_title_query_from_metadata(md: dict | None) -> str | None:
    """Verbatim playback ``title`` (rawTitle layer) refined for TMDb."""
    if not md:
        return None
    try:
        from pigeon.raw_title import raw_title_from_metadata_dict
        from pigeon.tmdb_poster import is_degenerate_tmdb_query, refine_tmdb_search_query

        rt = raw_title_from_metadata_dict(md)
        raw = (rt.raw_title or "").strip()
        if not raw:
            return None
        r = refine_tmdb_search_query(raw) or raw
        return r.strip() if r.strip() and not is_degenerate_tmdb_query(r) else None
    except Exception:
        raw = str(md.get("title") or "").strip()
        return raw or None


def _alternate_tmdb_query_from_metadata(md: dict | None, primary: str) -> str | None:
    """
    Pick a different TMDb query from last Apple TV metadata.

    Order prefers ``series_name`` / ``artist`` before ``title`` (title is often ``Sketch - SNL``).
    Refined strings that mean the same show as the primary (e.g. ``SNL`` vs ``Papyrus - SNL``)
    are skipped so ``?`` retry does not replace a good match with a sketch line.
    """
    if not primary or not md:
        return None
    try:
        from pigeon.tmdb_poster import equivalent_tmdb_search_queries, refine_tmdb_search_query
    except ImportError:

        def refine_tmdb_search_query(x: str | None) -> str | None:  # type: ignore[misc]
            return (str(x).strip() or None) if x else None

        def equivalent_tmdb_search_queries(a: str, b: str) -> bool:  # type: ignore[misc]
            return (a or "").strip().lower() == (b or "").strip().lower()

    pr = refine_tmdb_search_query(primary.strip()) or primary.strip()
    for key in ("series_name", "artist", "album", "title"):
        c = str(md.get(key) or "").strip()
        if not c:
            continue
        cr = refine_tmdb_search_query(c) or c
        if equivalent_tmdb_search_queries(cr, pr):
            continue
        return cr
    return None


def _pyatv_install_hint() -> str:
    exe = sys.executable or "python3"
    return f"Apple TV support requires pyatv.\n\nInstall with:\n  {exe} -m pip install pyatv"


@dataclass(frozen=True)
class SceneFit:
    target_w: int = WINDOW_W
    target_h: int = WINDOW_H

    def scale_and_crop(self, frame_bgr: np.ndarray) -> np.ndarray:
        if frame_bgr is None or frame_bgr.size == 0:
            raise ValueError("Empty frame")

        src_h, src_w = frame_bgr.shape[:2]
        if src_h <= 0 or src_w <= 0:
            raise ValueError(f"Invalid frame size {src_w}x{src_h}")

        scale = self.target_h / float(src_h)
        scaled_w = int(round(src_w * scale))
        scaled_h = self.target_h

        resized = cv2.resize(
            frame_bgr,
            (scaled_w, scaled_h),
            interpolation=cv_resize_interp(src_w, src_h, scaled_w, scaled_h),
        )

        if scaled_w == self.target_w:
            return resized

        if scaled_w < self.target_w:
            pad = self.target_w - scaled_w
            left = pad // 2
            right = pad - left
            sb, sg, sr = get_stage_bgr()
            return cv2.copyMakeBorder(
                resized,
                top=0,
                bottom=0,
                left=left,
                right=right,
                borderType=cv2.BORDER_CONSTANT,
                value=(sb, sg, sr),
            )

        x0 = (scaled_w - self.target_w) // 2
        x1 = x0 + self.target_w
        return resized[:, x0:x1]


def _default_render_fps() -> float:
    """Tk timer cadence for static landing + composited widgets (no scene video)."""
    import os
    import sys

    env = os.environ.get("PIGEON_UI_FPS", "").strip()
    if env:
        try:
            return max(5.0, min(60.0, float(env)))
        except ValueError:
            pass
    # Pi / Linux: keep PhotoImage uploads and settings SVG compositing lighter.
    if sys.platform.startswith("linux"):
        return 12.0
    return 30.0


def _build_landing_design_bgr(design_w: int, design_h: int, logo_path: Path | None) -> np.ndarray:
    """Pure black design-sized canvas with the Pigeon logo centered (alpha-aware)."""
    out = np.zeros((design_h, design_w, 3), dtype=np.uint8)
    if logo_path is None or not logo_path.is_file():
        sys.stderr.write("pigeon: landing logo not found — using black screen only\n")
        sys.stderr.flush()
        return out
    arr: np.ndarray | None = None
    try:
        arr = cv2.imread(str(logo_path), cv2.IMREAD_UNCHANGED)
    except Exception:
        arr = None
    if arr is None or arr.size == 0:
        try:
            pil_img = Image.open(logo_path).convert("RGBA")
            arr = np.asarray(pil_img, dtype=np.uint8)
        except Exception as e:
            sys.stderr.write(f"pigeon: could not load landing logo {logo_path}: {e}\n")
            sys.stderr.flush()
            return out
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    rh, rw = int(arr.shape[0]), int(arr.shape[1])
    if rw <= 0 or rh <= 0:
        return out
    max_side = int(0.55 * min(design_w, design_h))
    max_side = max(32, max_side)
    scale = min(max_side / float(rw), max_side / float(rh), 1.0)
    nw = max(1, int(round(rw * scale)))
    nh = max(1, int(round(rh * scale)))
    if nw != rw or nh != rh:
        arr = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
    rh, rw, ch = int(arr.shape[0]), int(arr.shape[1]), int(arr.shape[2])
    if ch >= 4:
        bgr = arr[:, :, :3]
        alpha = arr[:, :, 3:4].astype(np.float32) / 255.0
    else:
        bgr = arr[:, :, :3]
        alpha = np.ones((rh, rw, 1), dtype=np.float32)
    x0 = (design_w - nw) // 2
    y0 = (design_h - nh) // 2
    roi = out[y0 : y0 + nh, x0 : x0 + nw]
    roi[:] = (bgr.astype(np.float32) * alpha + roi.astype(np.float32) * (1.0 - alpha)).astype(np.uint8)
    return out


def _apply_brightness(frame_bgr: np.ndarray, factor: float) -> np.ndarray:
    factor = float(factor)
    if factor >= 0.999:
        return frame_bgr
    if factor <= 0.0:
        return np.zeros_like(frame_bgr)
    return cv2.convertScaleAbs(frame_bgr, alpha=factor, beta=0)


def _bgr_to_tk_image(frame_bgr: np.ndarray) -> ImageTk.PhotoImage:
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    return ImageTk.PhotoImage(image=img)


def _update_label_photo_from_bgr(
    label: tk.Label,
    frame_bgr: np.ndarray,
    holder: list[ImageTk.PhotoImage | None],
) -> None:
    """
    Reuse one ``PhotoImage`` and ``paste`` each frame. Creating hundreds of new
    ``PhotoImage`` objects per second leaks native Tk storage and locks up after a short run.
    """
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    h, w = int(rgb.shape[0]), int(rgb.shape[1])
    if h < 1 or w < 1:
        return
    pil_img = Image.fromarray(rgb)
    ph = holder[0]
    try:
        if ph is None or ph.width() != w or ph.height() != h:
            holder[0] = ImageTk.PhotoImage(image=pil_img)
            ph = holder[0]
        else:
            ph.paste(pil_img)
    except tk.TclError:
        holder[0] = ImageTk.PhotoImage(image=pil_img)
        ph = holder[0]
    # Re-configuring the same PhotoImage every tick still stresses Tk; paste updates pixels in place.
    if getattr(label, "image", None) is not ph:
        label.configure(image=ph)
    label.image = ph


def _load_persisted_scene_enabled(default: bool = True) -> bool:
    v = read_app_state().get("scene_enabled")
    if isinstance(v, bool):
        return v
    return default


def _save_persisted_scene_enabled(enabled: bool) -> None:
    write_app_state(scene_enabled=enabled)


def _format_hmmss(seconds_value: float | int | None) -> str:
    """Compact clock for on-screen TRT: drop leading zeros / unused fields (e.g. ``30:00``, ``59:29``)."""
    try:
        total_seconds = max(0, int(float(seconds_value or 0)))
    except (TypeError, ValueError):
        total_seconds = 0
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    if minutes > 0:
        return f"{minutes}:{seconds:02d}"
    return f"{seconds}"


def main() -> int:
    sys.stderr.write(f"pigeon: running script {os.path.abspath(__file__)}\n")
    sys.stderr.flush()
    try:
        from pigeon.pi_diagnostics import run_linux_startup_checks

        run_linux_startup_checks()
    except Exception:
        pass

    parser = argparse.ArgumentParser(prog=f"Pigeon {version_string()}", add_help=True)
    parser.parse_args()

    cap: cv2.VideoCapture | None = None

    root = tk.Tk()
    _app_startup_mono = time.monotonic()
    try:
        root.configure(bg="#000")
    except tk.TclError:
        pass
    root.title("")
    root.geometry(f"{WINDOW_W}x{WINDOW_H}")
    root.minsize(
        int(round((DISPLAY_W // 2) * _LAUNCH_WINDOW_SCALE)),
        int(round((DISPLAY_H // 2) * _LAUNCH_WINDOW_SCALE)),
    )
    root.resizable(True, True)
    try:
        root.wm_aspect(5, 3, 5, 3)
    except tk.TclError:
        pass
    if sys.platform == "linux" and _env_truthy("PIGEON_PI_FULLSCREEN", default=True):
        try:
            root.attributes("-fullscreen", True)
        except tk.TclError:
            try:
                root.state("zoomed")
            except tk.TclError:
                pass
    root.protocol("WM_DELETE_WINDOW", root.quit)
    # Ensure unexpected Tk callback errors are surfaced (and don't silently kill UI behavior).
    def _report_callback_exception(exc, val, tb) -> None:  # type: ignore[no-untyped-def]
        # Tk calls this as report_callback_exception(exc, val, tb) — no bound self.
        import traceback

        text = "".join(traceback.format_exception(exc, val, tb))
        try:
            sys.stderr.write("pigeon: Tk callback exception\n" + text + "\n")
            sys.stderr.flush()
        except Exception:
            pass
        try:
            messagebox.showerror("Pigeon error", text)
        except Exception:
            pass

    root.report_callback_exception = _report_callback_exception  # type: ignore[method-assign]

    shell = tk.Frame(root, bg="#111")
    shell.pack(fill=tk.BOTH, expand=True)
    # Main UI is built here; splash overlay sits above until the splash sequence finishes.
    content_host = tk.Frame(shell, bg="#111")
    content_host.pack(fill=tk.BOTH, expand=True)
    # Bridge host so the clock saver is visible the instant splash lifts — even if full
    # bootstrap has not created the real video ``Label`` yet. Bootstrap destroys this.
    _boot_clock_host = tk.Frame(content_host, bg="#000")
    _boot_clock_host.pack(fill=tk.BOTH, expand=True)
    _boot_clock_label = tk.Label(_boot_clock_host, bd=0, highlightthickness=0, bg="#000")
    _boot_clock_label.pack(fill=tk.BOTH, expand=True)
    _boot_clock_photo: list[ImageTk.PhotoImage | None] = [None]

    # Full-window splash: PNG sequence in ``pigeonSplash/`` if present, else H.264/HEVC
    # video (hardware-decoded on macOS), else built-in wordmark.
    startup_ph: list[tk.Widget | None] = [None]
    splash_png_paths: list[Path] = []
    splash_video_path: Path | None = None
    if _PIGEON_EXT:
        try:
            _assets_root = Path(_PROJECT_DIR) / "pigeonAssets"
            splash_png_paths, splash_video_path = resolve_splash_media(_assets_root)
        except Exception:
            splash_png_paths = []
            splash_video_path = None

    bootstrap_done: list[bool] = [False]
    splash_anim_done: list[bool] = [False]
    # Live underlay composited under splash PNG alpha. Stays black until frame 90.
    _splash_underlay_bgr: list[np.ndarray | None] = [None]
    # Pre-rasterized clock (background thread); swapped into underlay only at frame 90.
    _splash_clock_ready_bgr: list[np.ndarray | None] = [None]
    # True once splash reaches ``SPLASH_CLOCK_REVEAL_FRAME``.
    _splash_reveal_clock: list[bool] = [False]
    # Registered from bootstrap: keep the real video label in sync after reveal.
    _splash_on_reveal_paint: list[object] = [None]
    _splash_underlay_paint_mono: list[float] = [0.0]
    _splash_post_hook_ran: list[bool] = [False]
    # Post-splash UI timing (splash lift).
    post_splash_mono: list[float | None] = [None]
    _post_splash_startup_hook: list[object] = [None]

    def _rasterize_clock_saver_window_bgr() -> np.ndarray | None:
        """Full-window BGR clock saver, or None."""
        if clock_saver_composite_bgra is None or alpha_blend_bgra_over_bgr is None:
            return None
        try:
            dw = int(DESIGN_W) if int(DESIGN_W) > 0 else int(UI_TARGET_W)
            dh = int(DESIGN_H) if int(DESIGN_H) > 0 else int(UI_TARGET_H)
            canvas = np.zeros((dh, dw, 3), dtype=np.uint8)
            (time_bgra, t_rect), (date_bgra, d_rect) = clock_saver_composite_bgra(
                shadow_bgr=None,
                layer_opacity=1.0,
                time_layer_opacity=1.0,
                date_layer_opacity=1.0,
            )
            for cs_bgra, (sx, sy, sw, sh) in (
                (date_bgra, d_rect),
                (time_bgra, t_rect),
            ):
                x0 = max(0, int(sx))
                y0 = max(0, int(sy))
                x1 = min(dw, x0 + int(sw))
                y1 = min(dh, y0 + int(sh))
                if x1 <= x0 or y1 <= y0:
                    continue
                roi = canvas[y0:y1, x0:x1]
                patch = cs_bgra[0 : y1 - y0, 0 : x1 - x0]
                if patch.shape[0] != roi.shape[0] or patch.shape[1] != roi.shape[1]:
                    continue
                roi[:] = alpha_blend_bgra_over_bgr(roi, patch)
            return np.ascontiguousarray(_present_frame_to_display(canvas, WINDOW_W, WINDOW_H))
        except Exception:
            return None

    def _apply_clock_to_bridge_label(shown: np.ndarray) -> None:
        """Push clock pixels onto the content_host bridge (under the splash overlay)."""
        try:
            _boot_clock_photo[0] = _bgr_to_tk_image(shown)
            if _boot_clock_label.winfo_exists():
                _boot_clock_label.configure(image=_boot_clock_photo[0])
                _boot_clock_label.image = _boot_clock_photo[0]  # type: ignore[attr-defined]
        except tk.TclError:
            pass

    def _reveal_clock_under_splash(*, refresh: bool = False) -> bool:
        """From frame 90: put clock into underlay + bridge beneath the splash overlay."""
        if _splash_reveal_clock[0] and _splash_underlay_bgr[0] is not None and not refresh:
            return True
        shown = _splash_clock_ready_bgr[0]
        # Re-rasterize only when we have no buffer yet, or a once-per-second digit refresh.
        if shown is None or refresh:
            shown = _rasterize_clock_saver_window_bgr()
            if shown is not None:
                _splash_clock_ready_bgr[0] = shown
        if shown is None:
            return False
        _splash_underlay_bgr[0] = shown
        _apply_clock_to_bridge_label(shown)
        _splash_underlay_paint_mono[0] = time.monotonic()
        _splash_reveal_clock[0] = True
        paint = _splash_on_reveal_paint[0]
        if callable(paint):
            try:
                paint()
            except Exception:
                pass
        return True

    def _finish_post_splash_startup_transition() -> None:
        """Post-splash hook (registered from ``bootstrap``)."""
        if _splash_post_hook_ran[0]:
            return
        hook = _post_splash_startup_hook[0]
        if callable(hook):
            _splash_post_hook_ran[0] = True
            hook()

    def _try_remove_splash_overlay() -> None:
        """Destroy splash the instant the sequence ends (no bootstrap wait)."""
        if not _PIGEON_EXT:
            return
        if not splash_anim_done[0]:
            return
        w = startup_ph[0]
        if w is None:
            return
        # Clock must already be on the bridge underlay before the overlay disappears.
        _reveal_clock_under_splash(refresh=False)
        if bootstrap_done[0]:
            _finish_post_splash_startup_transition()
        try:
            w.destroy()
        except tk.TclError:
            pass
        startup_ph[0] = None
        try:
            sys.stderr.write(
                f"pigeon: splash overlay lifted +{time.monotonic() - _app_startup_mono:.3f}s\n"
            )
            sys.stderr.flush()
        except Exception:
            pass
        # Splash frames can hold tens of MB (full-window RGB/BGRA per frame);
        # release them now that the overlay is gone. NameError guard: the caches
        # only exist when the ext splash path ran.
        try:
            _splash_rgb_cache.clear()
            _splash_bgra_cache.clear()
            splash_photo[0] = None
        except NameError:
            pass
        if post_splash_mono[0] is None:
            post_splash_mono[0] = time.monotonic()

    _tk_pack_orig = tk.Widget.pack
    _tk_grid_orig = tk.Widget.grid
    _tk_place_orig = tk.Widget.place
    _splash_pump_next: list[float] = [0.0]

    def _splash_pump_maybe() -> None:
        if not _PIGEON_EXT or bootstrap_done[0]:
            return
        now = time.monotonic()
        if now < _splash_pump_next[0]:
            return
        _splash_pump_next[0] = now + (1.0 / 30.0)
        try:
            root.update()
        except tk.TclError:
            pass

    def _pack_patched(self: tk.Misc, *args: object, **kwargs: object) -> object | None:
        r = _tk_pack_orig(self, *args, **kwargs)
        _splash_pump_maybe()
        return r

    def _grid_patched(self: tk.Misc, *args: object, **kwargs: object) -> object | None:
        r = _tk_grid_orig(self, *args, **kwargs)
        _splash_pump_maybe()
        return r

    def _place_patched(self: tk.Misc, *args: object, **kwargs: object) -> object | None:
        r = _tk_place_orig(self, *args, **kwargs)
        _splash_pump_maybe()
        return r

    if _PIGEON_EXT:
        # Stay a direct child of ``shell`` (placed full-size). Do **not** pack into ``video_area`` after
        # the video ``Label``: two ``pack(..., fill=BOTH, expand=True)`` siblings leave the second with
        # zero height, so the splash would disappear. Transparent PNG / fade pixels show ``content_host``.
        splash_overlay = tk.Frame(shell, bg="#000", highlightthickness=0, bd=0)
        splash_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        # Placed widgets can sit under later-packed siblings (e.g. ``hud_bar``); pin above ``content_host``.
        try:
            splash_overlay.lift(content_host)
        except tk.TclError:
            try:
                splash_overlay.lift()
            except tk.TclError:
                pass
        startup_ph[0] = splash_overlay
        # Opaque black label: splash frames are always composited to RGB (never Tk alpha punch-through).
        splash_label = tk.Label(splash_overlay, bg="#000", bd=0)
        splash_label.pack(expand=True, fill="both")
        splash_photo: list[ImageTk.PhotoImage | None] = [None]
        splash_idx = [0]
        # Set on the first ``splash_tick`` so setup/prebake time doesn't skip early frames.
        splash_t0: list[float | None] = [None]
        _splash_bg_bgr = (0, 0, 0)
        # Black underlay until frame 90 — early PNG frames are transparent and must not reveal the clock.
        _splash_underlay_bgr[0] = np.zeros((WINDOW_H, WINDOW_W, 3), dtype=np.uint8)
        try:
            _boot_clock_photo[0] = _bgr_to_tk_image(_splash_underlay_bgr[0])
            _boot_clock_label.configure(image=_boot_clock_photo[0])
            _boot_clock_label.image = _boot_clock_photo[0]  # type: ignore[attr-defined]
        except Exception:
            pass
        # Pre-rasterize clock off the UI thread so frame 90 can swap it in instantly.
        def _prewarm_splash_clock_worker() -> None:
            shown = _rasterize_clock_saver_window_bgr()
            if shown is not None:
                _splash_clock_ready_bgr[0] = shown

        try:
            import threading

            threading.Thread(
                target=_prewarm_splash_clock_worker,
                name="pigeon-splash-clock-prewarm",
                daemon=True,
            ).start()
        except Exception:
            _prewarm_splash_clock_worker()

        # Resolve total frame count AND native fps up front. The PNG / built-in paths lock to
        # SPLASH_FPS, but a video drives its own cadence (e.g. 59.94) so the splash plays at
        # authored speed instead of being stretched or sped up by a hardcoded 30 Hz scheduler.
        _splash_fps_effective = float(max(1, SPLASH_FPS))
        if splash_video_path is not None:
            try:
                _probe = cv2.VideoCapture(str(splash_video_path))
                _vc_total = int(_probe.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                _vc_fps = float(_probe.get(cv2.CAP_PROP_FPS) or 0.0)
                _probe.release()
            except Exception:
                _vc_total = 0
                _vc_fps = 0.0
            splash_total_frames = max(1, _vc_total) if _vc_total > 0 else FALLBACK_SPLASH_FRAME_COUNT
            # Reject obviously-bogus fps values (VideoCapture sometimes returns 0 or 1000 on bad files).
            if 1.0 < _vc_fps < 240.0:
                _splash_fps_effective = _vc_fps
        elif splash_png_paths:
            splash_total_frames = len(splash_png_paths)
            # Drop trailing empty PNG frames (often 1s+ of held "last frame" after the art is gone).
            if callable(splash_effective_frame_count):
                try:
                    _trimmed = int(
                        splash_effective_frame_count(
                            splash_png_paths, reveal_frame=int(SPLASH_CLOCK_REVEAL_FRAME)
                        )
                    )
                    if 1 <= _trimmed < splash_total_frames:
                        sys.stderr.write(
                            f"pigeon: splash trim frames {splash_total_frames} → {_trimmed} "
                            f"(trailing transparent)\n"
                        )
                        sys.stderr.flush()
                        splash_total_frames = _trimmed
                except Exception:
                    pass
        else:
            splash_total_frames = FALLBACK_SPLASH_FRAME_COUNT

        frame_dt = 1.0 / _splash_fps_effective
        frame_ms = max(1, int(round(1000.0 * frame_dt)))

        # Cap by SPLASH_MAX_DURATION_S so a pathological asset can't block startup.
        _max_frames_for_duration = int(float(SPLASH_MAX_DURATION_S) * _splash_fps_effective)
        if _max_frames_for_duration > 0:
            splash_total_frames = min(splash_total_frames, _max_frames_for_duration)

        # Optional software fade-out (0 = none). Scale with source fps when configured.
        _splash_fade_frames = int(
            round(float(SPLASH_FADE_OUT_FRAMES) * _splash_fps_effective / float(max(1, SPLASH_FPS)))
        )
        if _splash_fade_frames < 0:
            _splash_fade_frames = 0
        _splash_fade_zone_start = max(
            0, splash_total_frames - min(_splash_fade_frames, splash_total_frames)
        )
        _splash_reveal_i = int(SPLASH_CLOCK_REVEAL_FRAME)

        # Two parallel caches keyed by frame index:
        #   * _splash_rgb_cache: opaque RGB ready for Tk (preferred — no per-tick composite).
        #   * _splash_bgra_cache: BGRA fallback for live composite over clock when RGB not ready.
        _splash_rgb_cache: dict[int, np.ndarray] = {}
        _splash_bgra_cache: dict[int, np.ndarray] = {}
        _splash_prebake_done = [False]
        _splash_video_cap_holder: list[cv2.VideoCapture | None] = [None]

        def _splash_raw_bgra(ii: int) -> np.ndarray | None:
            """Source-resolution BGRA for frame ``ii`` (PNG / built-in). Video path doesn't use this."""
            if splash_png_paths:
                if 0 <= ii < len(splash_png_paths):
                    return load_splash_bgra(splash_png_paths[ii])
                return None
            return builtin_splash_bgra_frame(
                ii, splash_total_frames, width=UI_TARGET_W, height=UI_TARGET_H
            )

        def _splash_bgra_over_bgr_to_rgb(bgra: np.ndarray, under_bgr: np.ndarray) -> np.ndarray:
            a = bgra[:, :, 3:4].astype(np.float32) / 255.0
            blended = (
                bgra[:, :, :3].astype(np.float32) * a
                + under_bgr.astype(np.float32) * (1.0 - a)
            )
            rgb = cv2.cvtColor(
                np.clip(blended, 0, 255).astype(np.uint8),
                cv2.COLOR_BGR2RGB,
            )
            return np.ascontiguousarray(rgb)

        def _splash_store_prebaked(ii: int, bgra_window: np.ndarray) -> None:
            """Store a window-sized frame. Pre-90 → RGB over black; 90+ → RGB over clock when ready."""
            if splash_png_paths and ii >= _splash_reveal_i:
                clock = _splash_clock_ready_bgr[0]
                if (
                    clock is not None
                    and clock.ndim == 3
                    and clock.shape[:2] == bgra_window.shape[:2]
                ):
                    try:
                        _splash_rgb_cache[ii] = _splash_bgra_over_bgr_to_rgb(bgra_window, clock)
                        return
                    except Exception:
                        pass
                _splash_bgra_cache[ii] = bgra_window
                return
            if ii >= _splash_fade_zone_start and _splash_fade_frames > 0:
                _splash_bgra_cache[ii] = bgra_window
                return
            try:
                _splash_rgb_cache[ii] = flatten_bgra_over_bg_to_rgb(bgra_window, _splash_bg_bgr)
            except Exception:
                _splash_bgra_cache[ii] = bgra_window

        def _splash_prebake_reveal_over_clock() -> None:
            """Once the clock buffer exists, bake frames 90+ to RGB over it (off UI thread)."""
            clock = _splash_clock_ready_bgr[0]
            if clock is None:
                return
            try:
                for ii in range(_splash_reveal_i, splash_total_frames):
                    if ii in _splash_rgb_cache:
                        continue
                    bgra = _splash_bgra_cache.get(ii)
                    if bgra is None:
                        fr = _splash_raw_bgra(ii)
                        if fr is None:
                            continue
                        bgra = _bgra_to_display_window(fr)
                        _splash_bgra_cache[ii] = bgra
                    if clock.shape[:2] != bgra.shape[:2]:
                        continue
                    _splash_rgb_cache[ii] = _splash_bgra_over_bgr_to_rgb(bgra, clock)
            except Exception:
                pass

        def _splash_prebake_worker_pngs() -> None:
            """Decode PNGs off the UI thread; flatten early frames over black for a fast blit path."""
            try:
                for ii in range(splash_total_frames):
                    if ii in _splash_rgb_cache or ii in _splash_bgra_cache:
                        continue
                    fr = _splash_raw_bgra(ii)
                    if fr is None:
                        continue
                    fr = _bgra_to_display_window(fr)
                    _splash_store_prebaked(ii, fr)
                # If clock prewarm finished first, bake the reveal tail now.
                if _splash_clock_ready_bgr[0] is not None:
                    _splash_prebake_reveal_over_clock()
            except Exception:
                pass
            finally:
                _splash_prebake_done[0] = True

        def _splash_prebake_worker_video() -> None:
            """Sequentially decode the splash video into the fast-path caches.

            Sequential reads on ``VideoCapture`` are hardware-accelerated on macOS
            (AVFoundation/VideoToolbox) and far cheaper than 100+ PNG decodes + resizes.
            """
            cap_v: cv2.VideoCapture | None = None
            try:
                cap_v = cv2.VideoCapture(str(splash_video_path))
                _splash_video_cap_holder[0] = cap_v
                if not cap_v.isOpened():
                    return
                for ii in range(splash_total_frames):
                    ok, bgr = cap_v.read()
                    if not ok or bgr is None:
                        break
                    if bgr.shape[1] != UI_TARGET_W or bgr.shape[0] != UI_TARGET_H:
                        _sw, _sh = int(bgr.shape[1]), int(bgr.shape[0])
                        bgr = cv2.resize(
                            bgr,
                            (UI_TARGET_W, UI_TARGET_H),
                            interpolation=cv_resize_interp(_sw, _sh, UI_TARGET_W, UI_TARGET_H),
                        )
                    bgra_fit = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
                    bgra_fit[:, :, 3] = 255
                    bgra_win = _bgra_to_display_window(bgra_fit)
                    bgr = bgra_win[:, :, :3]
                    if ii < _splash_fade_zone_start:
                        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                        _splash_rgb_cache[ii] = np.ascontiguousarray(rgb)
                    else:
                        # Fade-tail wants dynamic alpha, so synthesize opaque BGRA and let the
                        # tick multiply the alpha channel each frame.
                        h, w = bgr.shape[:2]
                        bgra = np.empty((h, w, 4), dtype=np.uint8)
                        bgra[:, :, :3] = bgr
                        bgra[:, :, 3] = 255
                        _splash_bgra_cache[ii] = bgra
            except Exception:
                pass
            finally:
                try:
                    if cap_v is not None:
                        cap_v.release()
                except Exception:
                    pass
                _splash_video_cap_holder[0] = None
                _splash_prebake_done[0] = True

        # Kick off the prebake thread immediately so frames are warm before ``splash_tick``
        # starts pulling from the cache post-``after_idle``.
        try:
            import threading

            _worker = _splash_prebake_worker_video if splash_video_path is not None else _splash_prebake_worker_pngs
            _splash_prebake_thread = threading.Thread(
                target=_worker, name="pigeon-splash-prebake", daemon=True
            )
            _splash_prebake_thread.start()
        except Exception:
            # Fall back to on-demand decode inside ``splash_tick``.
            _splash_prebake_done[0] = True

        def _splash_fallback_frame_sync(ii: int) -> np.ndarray | None:
            """On-demand decode if the worker hasn't populated this index yet (built-in only)."""
            if splash_video_path is not None:
                # We can't safely random-seek while the worker owns the VideoCapture.
                return None
            fr = _splash_raw_bgra(ii)
            if fr is None:
                return None
            fr = _bgra_to_display_window(fr)
            _splash_store_prebaked(ii, fr)
            return fr

        def _splash_composite_bgra_to_photo(bgra_hit: np.ndarray, fade_mul: float) -> None:
            """Composite splash BGRA over underlay into an opaque RGB ``splash_photo``.

            Before frame 90 the underlay is black; from frame 90 it is the clock saver.
            Always bake to RGB so the splash layer fully covers content_host.
            """
            bgra_out = (
                apply_splash_global_alpha(bgra_hit, fade_mul)
                if fade_mul < 0.999 and apply_splash_global_alpha is not None
                else bgra_hit
            )
            under = _splash_underlay_bgr[0]
            if under is None or under.ndim != 3 or under.shape[:2] != bgra_out.shape[:2]:
                rgb = flatten_bgra_over_bg_to_rgb(bgra_out, _splash_bg_bgr)
            else:
                a = bgra_out[:, :, 3:4].astype(np.float32) / 255.0
                blended = (
                    bgra_out[:, :, :3].astype(np.float32) * a
                    + under.astype(np.float32) * (1.0 - a)
                )
                rgb = cv2.cvtColor(
                    np.clip(blended, 0, 255).astype(np.uint8),
                    cv2.COLOR_BGR2RGB,
                )
                rgb = np.ascontiguousarray(rgb)
            splash_photo[0] = ImageTk.PhotoImage(image=Image.fromarray(rgb, "RGB"))

        def splash_tick() -> None:
            try:
                if not splash_label.winfo_exists():
                    return
            except tk.TclError:
                return
            ov_top = startup_ph[0]
            if ov_top is not None:
                try:
                    # Keep splash strictly above content_host (clock lives underneath).
                    ov_top.lift(content_host)
                except tk.TclError:
                    try:
                        ov_top.lift()
                    except tk.TclError:
                        pass
            ntot = splash_total_frames
            now = time.monotonic()
            if splash_t0[0] is None:
                splash_t0[0] = now
            t0 = float(splash_t0[0])
            if now - t0 > float(SPLASH_MAX_DURATION_S):
                splash_idx[0] = ntot
            # Wall-clock frame index — skip frames when the UI thread is behind so the
            # sequence ends on time instead of stretching the last frames.
            wall_i = int((now - t0) / frame_dt)
            i = max(int(splash_idx[0]), min(wall_i, ntot))
            if i >= ntot:
                splash_anim_done[0] = True
                try:
                    sys.stderr.write(
                        f"pigeon: splash sequence done +{time.monotonic() - _app_startup_mono:.3f}s "
                        f"frames={ntot}\n"
                    )
                    sys.stderr.flush()
                except Exception:
                    pass
                _try_remove_splash_overlay()
                return

            rgb_hit = _splash_rgb_cache.get(i)
            bgra_hit = _splash_bgra_cache.get(i) if rgb_hit is None else None

            # Worker hasn't reached this index yet: try a short spin before giving up.
            if rgb_hit is None and bgra_hit is None:
                bgra_fb = _splash_fallback_frame_sync(i)
                if bgra_fb is not None:
                    rgb_hit = _splash_rgb_cache.get(i)
                    bgra_hit = _splash_bgra_cache.get(i) if rgb_hit is None else None
                if rgb_hit is None and bgra_hit is None:
                    # Don't stall the wall clock on a missing frame — skip ahead if later
                    # frames are already baked, else retry briefly.
                    for j in range(i + 1, min(i + 8, ntot)):
                        if j in _splash_rgb_cache or j in _splash_bgra_cache:
                            splash_idx[0] = j
                            break
                    root.after(2, splash_tick)
                    return

            # Frame 90+: swap clock under the splash (prewarmed). Before that: black only.
            if i >= _splash_reveal_i:
                first_reveal = not _splash_reveal_clock[0]
                _reveal_clock_under_splash(refresh=False)
                if first_reveal:
                    try:
                        import threading

                        threading.Thread(
                            target=_splash_prebake_reveal_over_clock,
                            name="pigeon-splash-reveal-bake",
                            daemon=True,
                        ).start()
                    except Exception:
                        _splash_prebake_reveal_over_clock()
                    try:
                        sys.stderr.write(
                            f"pigeon: splash clock reveal frame={i} "
                            f"+{time.monotonic() - _app_startup_mono:.3f}s\n"
                        )
                        sys.stderr.flush()
                    except Exception:
                        pass

            splash_idx[0] = i + 1

            try:
                if rgb_hit is not None:
                    # Fast path: opaque RGB → Tk's direct blit (no per-pixel alpha work).
                    splash_photo[0] = ImageTk.PhotoImage(image=Image.fromarray(rgb_hit, "RGB"))
                else:
                    fade_mul = (
                        splash_end_fade_factor(i, ntot, min(_splash_fade_frames, ntot))
                        if _splash_fade_frames > 0 and splash_end_fade_factor is not None
                        else 1.0
                    )
                    _splash_composite_bgra_to_photo(bgra_hit, float(fade_mul))
            except Exception:
                # Best-effort RGB fallback so a single bad frame doesn't abort the splash.
                try:
                    fb = rgb_hit if rgb_hit is not None else flatten_bgra_over_bg_to_rgb(
                        bgra_hit if bgra_hit is not None else np.zeros((WINDOW_H, WINDOW_W, 4), np.uint8),
                        _splash_bg_bgr,
                    )
                    splash_photo[0] = ImageTk.PhotoImage(image=Image.fromarray(fb, "RGB"))
                except Exception:
                    pass
            splash_label.configure(image=splash_photo[0])

            # Schedule the next wall-clock frame; if we're behind, run ASAP and skip.
            now2 = time.monotonic()
            next_i = int(splash_idx[0])
            target = t0 + float(next_i) * frame_dt
            delay_ms = int(round((target - now2) * 1000.0))
            if delay_ms < 1:
                delay_ms = 1
            elif delay_ms > frame_ms * 3:
                delay_ms = frame_ms
            root.after(delay_ms, splash_tick)

    else:
        loading = tk.Label(
            content_host,
            text="Starting Pigeon…\n\n"
            "Tab / Shift+Tab / F9 toggle settings ↔ off. "
            "Key 5 shows grid overlay (press 5 again to toggle detail lines). "
            "Return opens the command bar in settings or grid overlay (5). "
            "Esc closes the bar or quits. F10 / double-click toggles the display. "
            "Space = activate in settings; else play/pause on the selected Player "
            "(Apple TV / Roku) when set; else TMDb backdrop + logo when loaded; else landing brightness pulse.",
            justify="center",
            fg="#ddd",
            bg="#111",
            wraplength=WINDOW_W - 40,
        )
        loading.pack(expand=True, fill="both")
        startup_ph[0] = loading

    root.update_idletasks()
    root.update()
    if _PIGEON_EXT and startup_ph[0] is not None:
        try:
            startup_ph[0].lift(content_host)
        except tk.TclError:
            try:
                startup_ph[0].lift()
            except tk.TclError:
                pass

    # Keep idle/paused composites intentionally slower to reduce Tk PhotoImage upload pressure.
    paused_interval_ms = max(67, PAUSED_COMPOSITE_MS)

    def bootstrap() -> None:
        nonlocal cap

        cap = None

        if not _PIGEON_EXT:
            _w0 = startup_ph[0]
            if _w0 is not None:
                try:
                    _w0.destroy()
                except tk.TclError:
                    pass
                startup_ph[0] = None
        else:
            tk.Widget.pack = _pack_patched  # type: ignore[method-assign]
            tk.Widget.grid = _grid_patched  # type: ignore[method-assign]
            tk.Widget.place = _place_patched  # type: ignore[method-assign]

        try:
            mig = consolidate_legacy_pigeondata_media_folders()
            for line in mig:
                sys.stderr.write(f"pigeon: media folders: {line}\n")
            if mig:
                sys.stderr.flush()
        except Exception as e:
            sys.stderr.write(f"pigeon: media folder consolidation: {e}\n")
            sys.stderr.flush()

        # Full-size video area (always WINDOW_H) so scene scale matches non-overlay mode.
        # Paint the clock onto the new label BEFORE destroying the splash bridge — otherwise
        # the screen flashes black between bridge teardown and the first composite.
        video_area = tk.Frame(content_host, bg="#000")
        label = tk.Label(video_area, bd=0, highlightthickness=0, takefocus=True, bg="#000")
        _startup_label_black_photo: list[ImageTk.PhotoImage | None] = [None]
        _early_clock_underlay_photo: list[ImageTk.PhotoImage | None] = [None]
        _handoff_clock = _boot_clock_photo[0]
        if _handoff_clock is None and _splash_underlay_bgr[0] is not None:
            try:
                _handoff_clock = _bgr_to_tk_image(_splash_underlay_bgr[0])
                _boot_clock_photo[0] = _handoff_clock
            except Exception:
                _handoff_clock = None
        if _handoff_clock is not None:
            try:
                label.configure(image=_handoff_clock)
                label.image = _handoff_clock
                _early_clock_underlay_photo[0] = _handoff_clock
            except Exception:
                _handoff_clock = None
        if _handoff_clock is None:
            try:
                _bb = np.zeros((WINDOW_H, WINDOW_W, 3), dtype=np.uint8)
                _startup_label_black_photo[0] = _bgr_to_tk_image(_bb)
                label.configure(image=_startup_label_black_photo[0])
                label.image = _startup_label_black_photo[0]
            except Exception:
                pass
        video_area.pack(fill=tk.BOTH, expand=True)
        label.pack(fill=tk.BOTH, expand=True)
        try:
            root.update_idletasks()
        except tk.TclError:
            pass
        # Bridge can go away only after the real label already shows the clock.
        try:
            if _boot_clock_host.winfo_exists():
                _boot_clock_host.destroy()
        except tk.TclError:
            pass

        def _early_splash_clock_underlay() -> None:
            """Copy clock underlay onto the real video label (under splash / after lift)."""
            if not _splash_reveal_clock[0]:
                return
                under = _splash_underlay_bgr[0]
            if under is None:
                _reveal_clock_under_splash(refresh=False)
                under = _splash_underlay_bgr[0]
            if under is None:
                return
            try:
                _early_clock_underlay_photo[0] = _bgr_to_tk_image(under)
                label.configure(image=_early_clock_underlay_photo[0])
                label.image = _early_clock_underlay_photo[0]
            except Exception:
                pass

        # Keep label in sync once splash has reached the reveal frame.
        _splash_on_reveal_paint[0] = _early_splash_clock_underlay
        if _splash_reveal_clock[0]:
            try:
                _early_splash_clock_underlay()
            except Exception:
                pass

        # Deprecated Tk settings form (never packed). Runtime state helpers below still
        # attach holders here; UI is settings_main SVG only (DevPhase.MAIN_SETTINGS).
        settings_frame = tk.Frame(video_area, bg="#111")
        settings_scroll_outer = tk.Frame(settings_frame, bg="#111")
        settings_scroll_outer.pack(fill=tk.BOTH, expand=True)
        settings_canvas = tk.Canvas(
            settings_scroll_outer,
            bg="#111",
            highlightthickness=0,
            bd=0,
        )
        settings_scrollbar = tk.Scrollbar(
            settings_scroll_outer,
            orient=tk.VERTICAL,
            command=settings_canvas.yview,
            bg="#2a2a2e",
            troughcolor="#111",
            activebackground="#3a3a40",
            highlightthickness=0,
        )
        settings_canvas.configure(yscrollcommand=settings_scrollbar.set)
        settings_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        settings_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        settings_inner = tk.Frame(settings_canvas, bg="#111")
        _settings_inner_win = settings_canvas.create_window((16, 12), window=settings_inner, anchor=tk.NW)

        _tk_font_families = frozenset(tkfont.families(root))
        _settings_sans_face = "Helvetica"
        for _cand in (
            "SharpSans",
            "Sharp Sans",
            "SharpSans-Medium",
            "Sharp Sans Medium",
            "SharpSansMedium",
            "Sharp Sans No2",
            "Sharp Sans No1",
        ):
            if _cand in _tk_font_families:
                _settings_sans_face = _cand
                break
        else:
            if "Helvetica Neue" in _tk_font_families:
                _settings_sans_face = "Helvetica Neue"
        _S = _settings_sans_face
        S_FONT_PAGE = (_S, 18, "bold")
        S_FONT_SEC = (_S, 12, "bold")
        S_FONT_BODY = (_S, 10)
        S_FONT_STATUS = (_S, 11)
        S_FONT_BTN = (_S, 11)
        S_FONT_LIST = (_S, 12)
        S_FONT_SMALL = (_S, 9)
        S_FONT_MICRO = (_S, 9)
        S_FONT_CAP_BOLD = (_S, 9, "bold")

        tk.Label(
            settings_inner,
            text="Pigeon Settings",
            fg="#f5f5f5",
            bg="#111",
            font=S_FONT_PAGE,
        ).pack(anchor=tk.W, pady=(0, 4))

        # Inner <Configure> fires often while scrolling an embedded window; only refresh scrollregion
        # when the inner frame actually changes size to avoid canvas flicker / jumpy redraws.
        _settings_inner_scroll_size: list[int] = [0, 0]

        def _settings_update_scrollregion(event: tk.Event | None = None) -> None:
            try:
                if event is not None and getattr(event, "widget", None) is not settings_inner:
                    return
                if event is not None:
                    settings_inner.update_idletasks()
                    rw = int(settings_inner.winfo_reqwidth())
                    rh = int(settings_inner.winfo_reqheight())
                    if rw == _settings_inner_scroll_size[0] and rh == _settings_inner_scroll_size[1]:
                        return
                    _settings_inner_scroll_size[0] = rw
                    _settings_inner_scroll_size[1] = rh
                settings_canvas.update_idletasks()
                bbox = settings_canvas.bbox("all")
                if bbox:
                    settings_canvas.configure(scrollregion=bbox)
            except tk.TclError:
                pass

        def _settings_on_canvas_configure(event: tk.Event) -> None:
            try:
                inner_w = max(1, int(event.width) - 32)
                settings_canvas.itemconfig(_settings_inner_win, width=inner_w)
            except tk.TclError:
                pass
            root.after_idle(lambda: _settings_update_scrollregion(None))

        settings_inner.bind("<Configure>", _settings_update_scrollregion)
        settings_canvas.bind("<Configure>", _settings_on_canvas_configure)

        settings_wheel_all_bound = [False]
        last_pigeon_user_activity_mono = [time.monotonic()]
        # Shared with metadata-idle clock saver (defined early so control bumps can dismiss it).
        last_metadata_activity_mono = [time.monotonic()]
        # Until playback is seen (or the user uses a control), stay on the clock saver after splash.
        _boot_clock_saver_until_playback = [True]

        def _bump_pigeon_user_activity(_event: object | None = None) -> None:
            """Marks local UI activity (theater idle-dim + dismiss metadata-idle clock saver)."""
            now_act = time.monotonic()
            last_pigeon_user_activity_mono[0] = now_act
            # Any Pigeon control (key, click, rotary, settings) exits the 2-minute saver.
            last_metadata_activity_mono[0] = now_act
            _boot_clock_saver_until_playback[0] = False

        def _maybe_exit_settings_menus_on_idle(now_mono: float | None = None) -> bool:
            """Close settings_main after ``SETTINGS_MENU_IDLE_EXIT_S`` without input.

            Returns True when menus were closed (compose should show now-playing or
            clock saver via the normal OFF-phase path).
            """
            nonlocal dev_phase, skip_cache
            if dev_phase != DevPhase.MAIN_SETTINGS:
                return False
            now_i = float(now_mono if now_mono is not None else time.monotonic())
            if (now_i - float(last_pigeon_user_activity_mono[0])) < float(
                SETTINGS_MENU_IDLE_EXIT_S
            ):
                return False
            if main_settings_widget is not None:
                try:
                    st_ms = main_settings_widget.state
                    if st_ms.keyboard_open:
                        st_ms.close_keyboard(commit=False)
                    st_ms.exit_pigeon_settings()
                    main_settings_widget.invalidate()
                except Exception:
                    pass
            dev_phase = DevPhase.OFF
            skip_cache = None
            try:
                sync_developer_chrome()
            except Exception:
                pass
            return True

        def _sync_preferences_now_playing_progress() -> None:
            """Feed live NP content into prefs; idle keeps SVG demos."""
            if main_settings_widget is None:
                return
            st_ms = main_settings_widget.state
            if not st_ms.show_preferences:
                return

            def _clear_prefs_live() -> None:
                st_ms.preferences_live_content = False
                st_ms.preferences_np_progress = None
                st_ms.preferences_poster_bgra = None
                st_ms.preferences_volume = None
                st_ms.preferences_volume_fraction = None
                st_ms.preferences_incoming = None
                st_ms.preferences_config = None
                st_ms.preferences_cast = None
                st_ms.preferences_elapsed_text = None
                st_ms.preferences_remaining_text = None
                st_ms.preferences_service_name = None
                st_ms.preferences_content_mode = None
                st_ms.preferences_song_title = None
                st_ms.preferences_album_title = None
                st_ms.preferences_artist_title = None

            try:
                prog = _playback_progress_fraction_for_bar()
            except Exception:
                prog = None
            clk = apple_tv_playback_clock
            has_playback = bool(clk.get("has_sync") or clk.get("live_mode"))
            poster = None
            try:
                poster = _circles_poster_bgra()
            except Exception:
                poster = None
            live = bool(
                has_playback
                or prog is not None
                or (poster is not None and getattr(poster, "size", 0) > 0)
                or bool(str(active_tmdb_title_key or "").strip())
            )
            if not live:
                _clear_prefs_live()
                return

            st_ms.preferences_live_content = True
            st_ms.preferences_np_progress = prog
            st_ms.preferences_poster_bgra = poster
            try:
                inc, cfg, vol = _resolve_receiver_lines_for_now_playing()
            except Exception:
                inc, cfg, vol = "", "", ""
            st_ms.preferences_incoming = inc
            st_ms.preferences_config = cfg
            st_ms.preferences_volume = vol
            try:
                from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

                st_ms.preferences_volume_fraction = float(
                    volume_fraction_from_display_line(vol)
                )
            except Exception:
                st_ms.preferences_volume_fraction = 0.0

            remaining_text = ""
            played_text = ""
            if clk.get("live_mode"):
                remaining_text = "LIVE"
                played_text = "LIVE"
            else:
                try:
                    pair = _playback_extrapolated_pair()
                except Exception:
                    pair = None
                if pair is not None:
                    played_text = _format_hmmss(int(pair[0]))
                    remaining_text = _format_hmmss(int(pair[1]))
            st_ms.preferences_elapsed_text = played_text
            st_ms.preferences_remaining_text = remaining_text

            sb = streaming_badge_state
            svc = str(sb.get("label") or "").strip()
            if not svc:
                lm_svc = apple_tv_auto_state.get("last_metadata")
                if isinstance(lm_svc, dict):
                    svc = str(lm_svc.get("app_name") or "").strip()
            st_ms.preferences_service_name = svc

            is_music = bool(_vv_is_music())
            st_ms.preferences_content_mode = "music" if is_music else "video"
            if is_music:
                lm_music = apple_tv_auto_state.get("last_metadata")
                song_t = album_t = artist_t = ""
                if isinstance(lm_music, dict):
                    song_t = str(lm_music.get("title") or "").strip()
                    album_t = str(lm_music.get("album") or "").strip()
                    artist_t = str(lm_music.get("artist") or "").strip()
                    if not song_t and album_t:
                        song_t, album_t = album_t, ""
                st_ms.preferences_song_title = song_t
                st_ms.preferences_album_title = album_t
                st_ms.preferences_artist_title = artist_t
                st_ms.preferences_cast = ()
            else:
                st_ms.preferences_song_title = ""
                st_ms.preferences_album_title = ""
                st_ms.preferences_artist_title = ""
                cast_rows: list[tuple[str, str]] = []
                try:
                    from pigeon.tmdb_poster import get_cached_tmdb_cast

                    tk = str(active_tmdb_title_key or "").strip()
                    if tk:
                        cast_rows = list(get_cached_tmdb_cast(tk) or [])
                except Exception:
                    cast_rows = []
                st_ms.preferences_cast = tuple(
                    (str(a or ""), str(c or "")) for a, c in cast_rows[:9]
                )

        def _settings_wheel_target_should_ignore(widget: tk.Misc) -> bool:
            """Let Listbox/Text/Entry keep their own scroll behavior."""
            try:
                w: tk.Misc | None = widget
                while w is not None:
                    cls = w.winfo_class()
                    if cls in ("Listbox", "Text", "Entry", "TEntry", "TCombobox"):
                        return True
                    master = w.master
                    w = master if isinstance(master, tk.Misc) else None
            except tk.TclError:
                pass
            return False

        def _settings_is_under_scroll_surface(widget: tk.Misc) -> bool:
            """True when ``widget`` is the settings canvas, scrollbar, or any descendant."""
            try:
                w: tk.Misc | None = widget
                while w is not None:
                    if w is settings_scroll_outer:
                        return True
                    master = w.master
                    w = master if isinstance(master, tk.Misc) else None
            except tk.TclError:
                pass
            return False

        def _settings_mousewheel(event: tk.Event) -> str | None:
            _bump_pigeon_user_activity(event)
            # Legacy Tk settings form is never shown.
            if not settings_frame.winfo_ismapped():
                return None
            try:
                under = root.winfo_containing(event.x_root, event.y_root)
            except tk.TclError:
                under = None
            if under is None or not _settings_is_under_scroll_surface(under):
                return None
            if _settings_wheel_target_should_ignore(under):
                return None
            try:
                if sys.platform == "darwin":
                    d = int(getattr(event, "delta", 0) or 0)
                    if d == 0:
                        return "break"
                    steps = max(1, abs(d) // 120) if abs(d) >= 120 else 1
                    settings_canvas.yview_scroll(-steps if d > 0 else steps, "units")
                else:
                    num = int(getattr(event, "num", 0) or 0)
                    if num == 4:
                        settings_canvas.yview_scroll(-3, "units")
                    elif num == 5:
                        settings_canvas.yview_scroll(3, "units")
            except tk.TclError:
                pass
            return "break"

        def _settings_bind_wheel_globals() -> None:
            if settings_wheel_all_bound[0]:
                return
            root.bind_all("<MouseWheel>", _settings_mousewheel)
            root.bind_all("<Button-4>", _settings_mousewheel)
            root.bind_all("<Button-5>", _settings_mousewheel)
            settings_wheel_all_bound[0] = True

        def _settings_unbind_wheel_globals() -> None:
            if not settings_wheel_all_bound[0]:
                return
            try:
                root.unbind_all("<MouseWheel>")
                root.unbind_all("<Button-4>")
                root.unbind_all("<Button-5>")
            except tk.TclError:
                pass
            settings_wheel_all_bound[0] = False

        def on_purge_image_media() -> None:
            if not messagebox.askokcancel(
                "Purge image media",
                "Delete all files in pigeonPulledMedia and pigeonReformattedMedia?",
                parent=root,
            ):
                return
            ok1, msg1 = purge_directory_contents(pigeon_pulled_media_dir())
            ok2, msg2 = purge_directory_contents(pigeon_reformatted_media_dir())
            if ok1 and ok2:
                messagebox.showinfo("Purge image media", f"{msg1}\n{msg2}")
            else:
                messagebox.showerror("Purge image media", f"{msg1}\n{msg2}")

        location_section = tk.Frame(settings_inner, bg="#111")
        location_section.pack(anchor=tk.W, fill=tk.X, pady=(8, 2))
        location_menu_var = tk.StringVar(value="")
        location_name_var = tk.StringVar(value="")
        location_option_holder: list[tk.OptionMenu | None] = [None]
        location_om_parent = tk.Frame(location_section, bg="#111")
        location_om_parent.pack(anchor=tk.W, fill=tk.X)
        tk.Label(
            location_om_parent,
            text="Current location",
            fg="#ccc",
            bg="#111",
            font=S_FONT_SEC,
        ).pack(side=tk.LEFT)
        location_om_frame = tk.Frame(location_om_parent, bg="#111")
        location_om_frame.pack(side=tk.LEFT, padx=(10, 0))
        delete_location_btn = tk.Button(
            location_om_parent,
            text="\u2715",
            font=("Helvetica", 12, "bold"),
            fg="#c44",
            bg="#111",
            activebackground="#2a1a1a",
            activeforeground="#f66",
            highlightthickness=1,
            highlightbackground="#553333",
            bd=0,
            cursor="hand2",
            padx=8,
            pady=0,
            command=lambda: None,
            state=tk.DISABLED,
        )
        delete_location_btn.pack(side=tk.LEFT, padx=(10, 0), anchor=tk.W)
        location_edit_row = tk.Frame(location_section, bg="#111")
        location_edit_row.pack(anchor=tk.W, fill=tk.X, pady=(6, 0))
        tk.Label(
            location_edit_row,
            text="Location name",
            fg="#888",
            bg="#111",
            font=S_FONT_MICRO,
        ).pack(side=tk.LEFT)
        location_name_entry = tk.Entry(
            location_edit_row,
            textvariable=location_name_var,
            width=28,
            bg="#1a1a1e",
            fg="#e8e8e8",
            insertbackground="#e8e8e8",
            highlightthickness=1,
            highlightbackground="#333",
            font=S_FONT_BODY,
        )
        location_name_entry.pack(side=tk.LEFT, padx=(8, 6))

        def _apply_location_rename() -> None:
            cid = (read_current_location_id() or "").strip()
            if not cid:
                return
            new_nm = (location_name_var.get() or "").strip() or "Room"
            if not rename_location_v2(cid, new_nm):
                return
            _refresh_location_selector()
            _apply_persisted_location_to_runtime()
            _start_location_toast()

        rename_name_btn = tk.Button(
            location_edit_row,
            text="Save name",
            command=_apply_location_rename,
            font=S_FONT_BTN,
            padx=10,
            pady=2,
        )
        rename_name_btn.pack(side=tk.LEFT)

        def _on_location_name_return(_event: tk.Event) -> str:
            _apply_location_rename()
            return "break"

        location_name_entry.bind("<Return>", _on_location_name_return)
        tk.Label(
            location_section,
            text="Playback and overlay follow the active location. Rename anytime; home room names are not inferred from device scan — set them here or when adding a custom location in Find device.",
            fg="#666",
            bg="#111",
            font=S_FONT_MICRO,
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(2, 0))

        apple_tv_section = tk.Frame(settings_inner, bg="#111")
        apple_tv_section.pack(anchor=tk.W, pady=(4, 3))
        streaming_slot_holder: list[dict[str, str] | None] = [None]
        avr_slot_holder: list[dict[str, str] | None] = [None]
        receiver_poll_busy = {"active": False}
        # Recent pyatv scan (non-empty results only); avoids full LAN scan when adding more devices.
        DISCOVERY_CACHE_TTL_S = 600.0
        discovery_scan_cache: dict[str, object] = {"rows": None, "mono_s": 0.0}
        pairing_led_holder: list[tk.Canvas | None] = [None, None]
        pair_led_busy = {"active": False}
        _pair_led_pending_retry = [False]
        current_apple_tv = read_last_apple_tv()
        receiver_http_host = {"host": str(read_last_receiver().get("host") or "").strip()}
        apple_tv_status_var = tk.StringVar()
        streaming_row_led_canvas_holder: list[tk.Canvas | None] = [None]
        receiver_panel_led_holder: list[tk.Canvas | None] = [None]
        paired_ui_leds: dict[str, tk.Canvas | None] = {"remote": None, "airplay": None, "receiver": None}
        paired_observed_led_rows: list[tuple[tk.Canvas, str, dict[str, str]]] = []
        paired_observed_led_last_state: dict[int, bool | None] = {}
        settings_footer_debug_holder: list[tk.Button | None] = [None]
        settings_footer_reset_holder: list[tk.Button | None] = [None]
        match_quality_glance_label_holder: list[tk.Label | None] = [None]
        match_quality_glance_sig: list[str] = [""]
        apple_tv_busy = {"active": False}
        apple_tv_auto_state: dict[str, object] = {
            "running": False,
            "content_key": None,
            "tmdb_key": None,
            "query": None,
            "prefer": "auto",
            "tmdb_fetch_in_flight": False,
            # Latest requested fetch while a worker is already running (drained in finish_tmdb).
            "pending_tmdb": None,
            "last_metadata": None,
            # Last TMDb worker actually started (see spawn_tmdb_poster_fetch); for view 4 debug.
            "last_tmdb_fetch_input": None,
            "last_tmdb_fetch_refined": None,
            "last_tmdb_fetch_prefer": None,
            # Music album art from pyatv ``metadata.artwork()`` (BGRA + track key).
            "music_artwork_bgra": None,
            "music_artwork_key": None,
            # After no-match / exhausted error-flag retries: stop empty-display poll respawn
            # and show "?" in the circles 2×3 poster slot.
            "tmdb_missing_art": False,
            "tmdb_exhausted_identity": None,
            # HDMI OCR is another metadata source (confirm / fill / pause check).
            "ocr_in_flight": False,
            # Last human-readable title decision (also mirrored on last_metadata).
            "last_title_decision": None,
        }
        tmdb_retry_rule_idx = [0]
        tmdb_adv_manual_btn_holder: list[tk.Button | None] = [None]
        tmdb_adv_report_btn_holder: list[tk.Button | None] = [None]
        tmdb_adv_log_text_holder: list[tk.Misc | None] = [None]

        def _register_tmdb_adv_widgets(manual: tk.Button, report: tk.Button, log_w: tk.Misc) -> None:
            tmdb_adv_manual_btn_holder[0] = manual
            tmdb_adv_report_btn_holder[0] = report
            tmdb_adv_log_text_holder[0] = log_w

        def _unregister_tmdb_adv_widgets() -> None:
            tmdb_adv_manual_btn_holder[0] = None
            tmdb_adv_report_btn_holder[0] = None
            tmdb_adv_log_text_holder[0] = None

        def _append_tmdb_retry_log_ui(line: str) -> None:
            w = tmdb_adv_log_text_holder[0]
            if w is None:
                return
            try:
                w.insert(tk.END, line + "\n")
                w.see(tk.END)
                body = w.get("1.0", "end-1c")
                if body.count("\n") > 130:
                    w.delete("1.0", "31.0")
            except tk.TclError:
                pass

        apple_tv_playback_clock: dict[str, object] = {
            "has_sync": False,
            "sync_mono": 0.0,
            "sync_position": 0.0,
            "live_mode": False,
            "playing": False,
            "latched_total": None,
            "latched_content_key": None,
            "last_reported_total": None,
            # Steady on-screen TRT: integer shown seconds (slewed), not raw extrapolation.
            "display_played_sec": None,
            "trt_next_fire_mono": None,
        }

        def _has_playback_position() -> bool:
            """True when zone5 can draw a real progress bar (not LIVE / empty)."""
            try:
                from pigeon.source_toggles import source_enabled

                if not source_enabled("metadata"):
                    return False
            except Exception:
                pass
            return _playback_progress_fraction_for_bar() is not None

        def _metadata_is_netflix_app(metadata: dict[str, object] | None) -> bool:
            if not isinstance(metadata, dict):
                return False
            bid = str(metadata.get("app_id") or "").strip().lower()
            bid2 = str(
                metadata.get("bundle_identifier")
                or metadata.get("app_identifier")
                or metadata.get("bundle_id")
                or ""
            ).strip().lower()
            an = str(metadata.get("app_name") or "").strip().lower()
            return "netflix" in an or "netflix" in bid or "netflix" in bid2

        def _atv_metadata_is_content_idle(metadata: dict[str, object]) -> bool:
            try:
                from pigeon.display_confidence import content_should_stay_active
                from pigeon.hdmi_ocr import hdmi_capture_available
                from pigeon.source_toggles import source_enabled

                if content_should_stay_active(
                    metadata,
                    hdmi_on=bool(source_enabled("hdmi")),
                    hdmi_present=hdmi_capture_available(),
                ):
                    return False
            except Exception:
                pass
            ds = str(metadata.get("device_state") or "")
            if "Idle" in ds or "Stopped" in ds:
                return True
            if "Playing" in ds:
                return False
            if resolve_metadata_tmdb_query is not None and resolve_metadata_tmdb_query(metadata):
                return False
            if metadata_has_playback_title is not None and metadata_has_playback_title(metadata):
                return False
            q = str(metadata.get("query") or "").strip()
            return not q

        def _show_paused_row_overlay() -> bool:
            """True when the player has substantive content loaded but is not actively playing."""
            lm_raw = apple_tv_auto_state.get("last_metadata")
            lm = lm_raw if isinstance(lm_raw, dict) else None
            if lm is None:
                return False
            if _atv_metadata_is_content_idle(lm):
                return False
            clk = apple_tv_playback_clock
            if clk.get("live_mode"):
                return False
            ds = str(lm.get("device_state") or "")
            if "Playing" in ds:
                return False
            if "Idle" in ds or "Stopped" in ds:
                return False
            has_title = bool(
                str(lm.get("title") or "").strip()
                or str(lm.get("query") or "").strip()
                or str(lm.get("artist") or "").strip()
            )
            if clk.get("has_sync"):
                return not bool(clk.get("playing"))
            if not has_title:
                return False
            return "Paused" in ds or "Pause" in ds

        def _denon_telnet_audio_fallback() -> tuple[str, str]:
            """Telnet snapshot when HTTP/XML left incoming/config empty."""
            dbg = receiver_telnet_debug_holder[0]
            if not isinstance(dbg, dict) or not dbg:
                return "", ""
            inc = str(
                dbg.get("SYSDA") or dbg.get("SSINFAISFOR") or dbg.get("DC") or ""
            ).strip()
            cfg = str(dbg.get("MS") or "").strip()
            if inc:
                inc = inc.lower()
            if cfg:
                cfg = cfg.lower()
            return inc, cfg

        def _resolve_receiver_lines_for_now_playing() -> tuple[str, str, str]:
            """Incoming/config/volume for View 1, with Denon telnet fallback."""
            if bool(receiver_standby_holder[0]):
                denon_vol_cache["np_hold"] = ""
                return "", "", ""
            inc = str(receiver_overlay_state.get("incoming") or "").strip()
            cfg = str(receiver_overlay_state.get("config") or "").strip()
            if not inc and not cfg:
                fb_inc, fb_cfg = _denon_telnet_audio_fallback()
                inc, cfg = fb_inc, fb_cfg
            vol = ""
            if compose_playback_volume_widget_line is not None:
                vol = compose_playback_volume_widget_line(
                    stream_row=streaming_slot_holder[0],
                    apple_tv_last_metadata=apple_tv_auto_state.get("last_metadata")
                    if isinstance(apple_tv_auto_state.get("last_metadata"), dict)
                    else None,
                    denon_vol_effective=str(denon_vol_cache.get("effective") or ""),
                    roku_tv_volume_percent="",
                )
            if not vol:
                raw_vol = str(receiver_overlay_state.get("volume") or "").strip()
                if raw_vol:
                    # Accept dB, mute, percent, and bare 0–100 (player-reported).
                    low = raw_vol.lower()
                    if (
                        low in ("mute", "muted", "off")
                        or "db" in low
                        or raw_vol.endswith("%")
                        or (raw_vol.isdigit() and 0 <= int(raw_vol) <= 100)
                        or raw_vol[:1] in "+-"
                    ):
                        vol = raw_vol
            if vol:
                denon_vol_cache["np_hold"] = vol
            else:
                # Keep last good readout so zone3 does not flash an empty ring
                # between AVR polls / while Apple TV reports volume_percent=0.
                held = str(denon_vol_cache.get("np_hold") or "").strip()
                if not held:
                    held = str(denon_vol_cache.get("effective") or "").strip()
                vol = held
            return inc, cfg, vol

        apple_tv_dashboard_track: dict[str, object] = {"last_poll_ok": None, "consecutive_fail": 0}
        content_indicator_cv_holder: list[tk.Canvas | None] = [None]
        _LISTBOX_BG = "#1a1a1e"
        _LISTBOX_FG = "#e8e8e8"

        atv_status_row = tk.Frame(apple_tv_section, bg="#111")
        atv_status_row.pack(anchor=tk.W, fill=tk.X, pady=(0, 6))
        tk.Label(
            atv_status_row,
            textvariable=apple_tv_status_var,
            fg="#cfcfcf",
            bg="#111",
            font=S_FONT_STATUS,
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W)

        devices_strip = tk.Frame(apple_tv_section, bg="#111")
        devices_strip.pack(anchor=tk.W, fill=tk.X, pady=(0, 6))
        devices_btn_row = tk.Frame(devices_strip, bg="#111")
        devices_btn_row.pack(anchor=tk.W, fill=tk.X, pady=(0, 4))
        find_device_btn = tk.Button(
            devices_btn_row,
            text="Find device",
            command=lambda: _open_find_device_dialog(),
            font=S_FONT_BTN,
            padx=10,
            pady=4,
        )
        find_device_btn.pack(side=tk.LEFT)
        advanced_matrix_btn = tk.Button(
            devices_btn_row,
            text="Advanced",
            command=lambda: _open_advanced_capability_matrix(),
            font=S_FONT_BTN,
            padx=10,
            pady=4,
        )
        advanced_matrix_btn.pack(side=tk.LEFT, padx=(10, 0))
        update_check_state: dict[str, object] = {
            "update_available": False,
            "remote_version": None,
            "github_branch": None,
            "checking": False,
            "last_check_mono": 0.0,
            "error": None,
            "applying": False,
        }
        _UPDATE_CHECK_INTERVAL_S = 30 * 60

        def _match_neighbor_button_style(btn: tk.Button, *, ref: tk.Button) -> None:
            """Copy default macOS/system button chrome from a sibling (Find device / Advanced)."""
            for key in (
                "bg",
                "fg",
                "activebackground",
                "activeforeground",
                "highlightbackground",
                "highlightcolor",
                "highlightthickness",
                "relief",
                "borderwidth",
                "disabledforeground",
            ):
                try:
                    btn.configure(**{key: ref.cget(key)})
                except tk.TclError:
                    pass

        def _sync_update_button_style() -> None:
            _match_neighbor_button_style(update_btn, ref=find_device_btn)
            if update_check_state.get("update_available"):
                update_btn.configure(
                    text="Updates ●",
                    state=tk.NORMAL,
                )
            else:
                update_btn.configure(
                    text="Updates",
                    state=tk.NORMAL,
                )

        def _resolve_install_root_for_update() -> Path:
            install_root = Path(__file__).resolve().parent.parent
            try:
                from pigeon.github_update import resolve_install_root

                resolved = resolve_install_root(script_path=__file__)
                if resolved is not None:
                    install_root = resolved
            except Exception:
                pass
            return install_root

        def _run_github_apply_worker(*, remote: str = "?", branch: str | None = None) -> None:
            install_root = _resolve_install_root_for_update()
            progress = tk.Toplevel(root)
            progress.title("Updating Pigeon")
            progress.transient(root)
            progress.grab_set()
            status_var = tk.StringVar(value="Downloading from GitHub…")
            tk.Label(progress, textvariable=status_var, padx=16, pady=16).pack()
            update_check_state["applying"] = True
            update_btn.configure(state=tk.DISABLED)

            def worker() -> None:
                try:
                    from pigeon.github_update import apply_github_update

                    apply_branch = branch
                    if apply_branch is None:
                        cached = update_check_state.get("github_branch")
                        if isinstance(cached, str) and cached.strip():
                            apply_branch = cached.strip()
                    result = apply_github_update(install_root, branch=apply_branch)
                except Exception as e:
                    from pigeon.github_update import ApplyUpdateResult

                    result = ApplyUpdateResult(False, str(e))

                def finish_apply() -> None:
                    update_check_state["applying"] = False
                    update_btn.configure(state=tk.NORMAL)
                    if result.ok:
                        status_var.set("Update complete — restarting Pigeon…")
                        update_check_state["update_available"] = False
                        if result.remote_version:
                            update_check_state["remote_version"] = result.remote_version
                        else:
                            update_check_state["remote_version"] = remote
                        _sync_update_button_style()

                        def _restart_and_exit() -> None:
                            try:
                                progress.grab_release()
                                progress.destroy()
                            except tk.TclError:
                                pass
                            try:
                                from pigeon.github_update import restart_pigeon_after_update

                                restart_pigeon_after_update(
                                    install_root, parent_pid=os.getpid()
                                )
                            except Exception:
                                pass
                            try:
                                root.destroy()
                            except tk.TclError:
                                pass
                            os._exit(0)

                        root.after(400, _restart_and_exit)
                        return

                    try:
                        progress.grab_release()
                        progress.destroy()
                    except tk.TclError:
                        pass
                    messagebox.showerror(
                        "Update failed",
                        result.message,
                        parent=root,
                    )

                root.after(0, finish_apply)

            threading.Thread(target=worker, daemon=True).start()

        def _linux_on_updates_button() -> None:
            """Pi/Linux: skip Python version check; run curl|bash updater from GitHub."""
            if update_check_state.get("applying") or update_check_state.get("checking"):
                return
            local = version_string()
            github_target = "0.8 on GitHub main"
            try:
                from pigeon.update_check import fetch_remote_version_tuple, format_version_tuple

                remote_t, _, _, _ = fetch_remote_version_tuple(
                    timeout_s=10.0, force=True
                )
                if remote_t is not None:
                    github_target = format_version_tuple(remote_t)
            except Exception:
                pass
            if not messagebox.askyesno(
                "Updates",
                f"Download, install, and restart Pigeon from GitHub?\n\n"
                f"Installed: {local}\n"
                f"GitHub:    {github_target}\n\n"
                f"• Uses curl only (public repo — no GitHub token)\n"
                f"• App code and pigeonAssets are updated to 0.8\n"
                f"• Settings in ~/.pigeon_0_6 are kept\n"
                f"• Pigeon will restart as 0.8 when finished — no further steps\n\n"
                f"Continue?",
                parent=root,
            ):
                return
            _run_github_apply_worker(remote=github_target)

        def _begin_apply_update(*, remote: str, branch: object) -> None:
            local = version_string()
            if not messagebox.askyesno(
                "Install update",
                f"A newer Pigeon is on GitHub.\n\n"
                f"Installed: {local}\n"
                f"Latest:    {remote}\n\n"
                f"Download, install, and restart Pigeon now?\n\n"
                f"• App code and UI assets (pigeonAssets) will be updated from GitHub\n"
                f"• Settings stay in ~/.pigeon_0_6 (devices, TMDb key, pairing)\n"
                f"• Cached TMDb downloads in the app folder are kept\n"
                f"• Pigeon will restart automatically when finished — no further steps",
                parent=root,
            ):
                return

            _run_github_apply_worker(remote=str(remote), branch=str(branch) if branch else None)

        def _on_updates_button() -> None:
            if sys.platform.startswith("linux"):
                _linux_on_updates_button()
                return
            if update_check_state.get("applying") or update_check_state.get("checking"):
                return

            progress = tk.Toplevel(root)
            progress.title("Updates")
            progress.transient(root)
            progress.grab_set()
            status_var = tk.StringVar(value="Checking GitHub for updates…")
            tk.Label(progress, textvariable=status_var, padx=16, pady=16).pack()
            update_check_state["checking"] = True
            update_btn.configure(state=tk.DISABLED)

            def worker() -> None:
                try:
                    from pigeon.update_check import check_for_update

                    result = check_for_update(force=True)
                except Exception as e:
                    from pigeon.update_check import UpdateCheckResult

                    result = UpdateCheckResult(
                        local_version=version_string(),
                        remote_version=None,
                        update_available=False,
                        error=str(e),
                    )

                def finish_check() -> None:
                    update_check_state["checking"] = False
                    _finish_update_check(result)
                    try:
                        progress.grab_release()
                        progress.destroy()
                    except tk.TclError:
                        pass
                    update_btn.configure(state=tk.NORMAL)

                    from pigeon.update_check import UpdateCheckResult

                    if not isinstance(result, UpdateCheckResult):
                        return
                    if result.error:
                        messagebox.showerror(
                            "Updates",
                            f"Could not check GitHub for updates.\n\n"
                            f"Installed: {result.local_version}\n\n"
                            f"{result.error}",
                            parent=root,
                        )
                        return
                    if result.update_available:
                        _begin_apply_update(
                            remote=str(result.remote_version or "?"),
                            branch=result.github_branch,
                        )
                        return
                    remote = result.remote_version
                    if remote:
                        body = (
                            f"You are on the latest version GitHub reports.\n\n"
                            f"Installed: {result.local_version}\n"
                            f"GitHub:    {remote}"
                        )
                    else:
                        body = (
                            f"No update information from GitHub.\n\n"
                            f"Installed: {result.local_version}\n\n"
                            f"If the repo is private, set PIGEON_UPDATE_GITHUB_TOKEN "
                            f"in the environment and try again."
                        )
                    messagebox.showinfo("Updates", body, parent=root)

                root.after(0, finish_check)

            threading.Thread(target=worker, daemon=True).start()

        def _finish_update_check(result: object) -> None:
            update_check_state["checking"] = False
            update_check_state["last_check_mono"] = time.monotonic()
            try:
                from pigeon.update_check import UpdateCheckResult

                if isinstance(result, UpdateCheckResult):
                    update_check_state["update_available"] = bool(result.update_available)
                    update_check_state["remote_version"] = result.remote_version
                    update_check_state["github_branch"] = result.github_branch
                    update_check_state["error"] = result.error
            except Exception:
                update_check_state["update_available"] = False
            _sync_update_button_style()

        def _check_for_updates(*, force: bool = False) -> None:
            if update_check_state.get("checking"):
                return
            now = time.monotonic()
            last = float(update_check_state.get("last_check_mono") or 0.0)
            if not force and (now - last) < _UPDATE_CHECK_INTERVAL_S:
                return
            update_check_state["checking"] = True

            def worker() -> None:
                try:
                    from pigeon.update_check import check_for_update

                    result = check_for_update()
                except Exception as e:
                    from pigeon.update_check import UpdateCheckResult

                    result = UpdateCheckResult(
                        local_version=version_string(),
                        remote_version=None,
                        update_available=False,
                        error=str(e),
                    )

                root.after(0, lambda r=result: _finish_update_check(r))

            threading.Thread(target=worker, daemon=True).start()

        def _schedule_periodic_update_check() -> None:
            _check_for_updates(force=False)
            root.after(int(_UPDATE_CHECK_INTERVAL_S * 1000), _schedule_periodic_update_check)

        update_btn = tk.Button(
            devices_btn_row,
            text="Updates",
            command=_on_updates_button,
            font=S_FONT_BTN,
            padx=10,
            pady=4,
        )
        update_btn.pack(side=tk.LEFT, padx=(10, 0))
        _sync_update_button_style()
        root.after(4000, lambda: _check_for_updates(force=True))
        root.after(int(_UPDATE_CHECK_INTERVAL_S * 1000), _schedule_periodic_update_check)
        tk.Label(
            devices_strip,
            text="Choose a device role, pick a device or enter Host/IP, then Confirm to save it to the current location.",
            fg="#666",
            bg="#111",
            font=S_FONT_MICRO,
            wraplength=520,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(4, 0))
        paired_devices_inner = tk.Frame(apple_tv_section, bg="#111")
        paired_devices_inner.pack(anchor=tk.W, fill=tk.X, pady=(0, 8))

        content_section = tk.Frame(apple_tv_section, bg="#111")
        content_section.pack(anchor=tk.W, fill=tk.X, pady=(2, 6))
        content_heading_row = tk.Frame(content_section, bg="#111")
        content_heading_row.pack(anchor=tk.W, fill=tk.X, pady=(0, 6))
        tk.Label(
            content_heading_row,
            text="Content",
            fg="#ccc",
            bg="#111",
            font=S_FONT_SEC,
        ).pack(side=tk.LEFT)
        content_indicator_cv = tk.Canvas(
            content_heading_row,
            width=20,
            height=20,
            bg="#111",
            highlightthickness=0,
            bd=0,
        )
        content_indicator_cv.pack(side=tk.LEFT, padx=(10, 6))
        content_indicator_cv_holder[0] = content_indicator_cv
        _paint_boolean_led(content_indicator_cv, False)
        tk.Label(
            content_heading_row,
            text="● green = detected   ● red = not",
            fg="#666",
            bg="#111",
            font=S_FONT_MICRO,
        ).pack(side=tk.LEFT, padx=(6, 0))
        content_buttons_row = tk.Frame(content_section, bg="#111")
        content_buttons_row.pack(anchor=tk.W, pady=(0, 6))
        # Purge is parented here after handlers. TMDb (Manual Fetch, Report Failure, retry log) lives on Advanced.

        # Bottom “info bar” HUD removed; Tab toggles settings_main ↔ off.
        hud_bar = None
        hud = None

        fps_sched = _default_render_fps()
        display_dims = [WINDOW_W, WINDOW_H]
        fit_holder = [SceneFit(target_w=WINDOW_W, target_h=WINDOW_H)]
        try:
            from pigeon.display_par import read_par_mode, resolve_display_par

            _par0, _par_reason0 = resolve_display_par(
                display_w=int(display_dims[0]), display_h=int(display_dims[1])
            )
            sys.stderr.write(
                f"pigeon: display PAR mode={read_par_mode()}  "
                f"effective={_par0:.4f}  ({_par_reason0})  "
                f"[hold P+A+R to toggle auto/off]\n"
            )
            sys.stderr.flush()
        except Exception:
            pass

        if _PIGEON_EXT:
            from pigeon.design import DESIGN_W as _DESIGN_W_L, DESIGN_H as _DESIGN_H_L

            _land_w, _land_h = int(_DESIGN_W_L), int(_DESIGN_H_L)
        else:
            _land_w, _land_h = UI_TARGET_W, UI_TARGET_H
        # No PNG on the landing plate; playback overlay is badge + receiver lines only.
        landing_scene_design_bgr = _build_landing_design_bgr(_land_w, _land_h, None)

        def _disp_fit() -> SceneFit:
            return fit_holder[0]

        def _black_screen_bgr() -> np.ndarray:
            sb, sg, sr = get_stage_bgr()
            out = np.empty((display_dims[1], display_dims[0], 3), dtype=np.uint8)
            out[:] = (sb, sg, sr)
            return out

        frame_interval_ms = max(1, int(round(1000.0 / fps_sched)))

        playing = False
        display_view_holder: list[DisplayView] = [DisplayView.ONE]
        # View 4 (key 4): 0=Title Info, 1=Source Info, 2=Playback Info — press 4 again to cycle.
        view_four_subview_holder: list[int] = [0]
        # View 5: 0=viewFive_a, 1=viewFive_b, 2=viewFive_c (testing text view).
        view_five_mode_holder: list[int] = [0]
        # View 1 (Shift+1): cycle a -> b -> c layouts (see ``ViewOneLayout``).
        view_one_layout_holder: list[int] = [int(ViewOneLayout.PIGEON_FULL)]
        # Last view-1 a/b/c while view 1 was live — developer GRID and view-5 grid overlay composite this.
        last_view_one_layout_snapshot: list[int] = [int(ViewOneLayout.PIGEON_FULL)]

        def _capture_last_view_one_layout_from_live_view() -> None:
            if display_view_holder[0] == DisplayView.ONE:
                last_view_one_layout_snapshot[0] = int(view_one_layout_holder[0])

        def _view_one_layout_effective() -> int:
            if _PIGEON_EXT and (
                dev_phase == DevPhase.GRID or display_view_holder[0] == DisplayView.FIVE
            ):
                return int(last_view_one_layout_snapshot[0])
            return int(view_one_layout_holder[0])

        def _stage_is_view_one_video_layout() -> bool:
            if not _PIGEON_EXT:
                return False
            if dev_phase == DevPhase.GRID or display_view_holder[0] == DisplayView.FIVE:
                return True
            return display_view_holder[0] == DisplayView.ONE

        def _view_one_is_pigeon_full() -> bool:
            if not _stage_is_view_one_video_layout():
                return False
            return int(_view_one_layout_effective()) == int(ViewOneLayout.PIGEON_FULL)

        def _view_one_is_pigeon_simple() -> bool:
            if not _stage_is_view_one_video_layout():
                return False
            return int(_view_one_layout_effective()) == int(ViewOneLayout.PIGEON_SIMPLE)

        def _view_one_is_pigeon_poster() -> bool:
            if not _stage_is_view_one_video_layout():
                return False
            return int(_view_one_layout_effective()) == int(ViewOneLayout.PIGEON_POSTER)
        last_frame: np.ndarray | None = landing_scene_design_bgr
        brightness_current = LANDING_DISPLAY_BRIGHTNESS
        brightness_from = LANDING_DISPLAY_BRIGHTNESS
        brightness_target = LANDING_DISPLAY_BRIGHTNESS
        brightness_t0 = time.monotonic()
        brightness_duration_s = 3.0
        brightness_duration_up_s = 1.0
        brightness_duration_down_s = 1.0

        last_atv_interaction_mono = 0.0
        last_device_interaction_mono = 0.0
        last_timecode_motion_mono = 0.0
        # ``last_metadata_activity_mono`` is initialized with pigeon user-activity state above.
        last_clock_saver_significant_device_mono = [time.monotonic()]
        _cs_sig_init = [False]
        _cs_sig_ck: list[str | None] = [None]
        _cs_sig_ds = [""]
        _cs_sig_vol = [""]
        _cs_sig_fp = [""]
        _atv_ix_sig_ds = ""
        _atv_ix_sig_ck: str | None = None
        _atv_ix_pos: float | None = None
        _atv_ix_pos_mono = 0.0
        _atv_ix_extrap_playing = False
        _atv_ix_prev_idle = True

        def _atv_idle_monochrome_active() -> bool:
            """True when theater idle-dim should be fully on (both ATV and Pigeon quiet long enough)."""
            if not current_apple_tv.get("identifier"):
                return False
            # Live TV and some streams never advance ``position``; metadata stays stable for minutes.
            # Without this guard, we never bump ``last_atv_interaction_mono`` and the red idle overlay
            # kicks in after THEATER_IDLE_DIM_AFTER_S even though pyatv still reports Playing.
            if bool(apple_tv_playback_clock.get("playing")):
                return False
            now = time.monotonic()
            pigeon_quiet = (now - last_pigeon_user_activity_mono[0]) >= THEATER_IDLE_DIM_AFTER_S
            if not pigeon_quiet:
                return False
            if last_atv_interaction_mono <= 0.0:
                return True
            return (now - last_atv_interaction_mono) >= THEATER_IDLE_DIM_AFTER_S

        def _vol_norm_for_clock_saver(vol_raw: object) -> str:
            if vol_raw is None:
                return ""
            try:
                v = float(vol_raw)
                if v != v:
                    return ""
                return f"{v:.6g}"
            except (TypeError, ValueError):
                return str(vol_raw).strip()

        def _bump_clock_saver_significant_device() -> None:
            last_clock_saver_significant_device_mono[0] = time.monotonic()

        def _note_metadata_activity(now: float | None = None) -> None:
            """Refresh the 2-minute metadata-idle clock-saver stamp."""
            last_metadata_activity_mono[0] = float(
                time.monotonic() if now is None else now
            )

        def _clear_reported_position_stall_stamp() -> None:
            """Reset metadata-idle saver stamp (device removed / location change)."""
            last_metadata_activity_mono[0] = time.monotonic()

        def _coarse_device_state_for_saver(ds: str) -> str:
            d = str(ds or "").strip().lower()
            if "playing" in d:
                return "playing"
            if "paus" in d:
                return "paused"
            if (not d) or ("idle" in d) or ("stop" in d):
                return "idle"
            return d

        def _metadata_activity_fingerprint(md: dict[str, object]) -> str:
            """Content/playback signature for the 2-minute metadata-idle saver.

            Volume is excluded (receiver/ATV level noise must not reset the timer).
            Position is only included while Playing so Idle/home None↔0 flaps are ignored.
            TMDb art presence is ignored.
            """
            ck = _content_key_from_metadata(md) or ""
            ds = _coarse_device_state_for_saver(str(md.get("device_state") or ""))
            title = str(md.get("title") or "").strip().lower()
            artist = str(md.get("artist") or "").strip().lower()
            album = str(md.get("album") or "").strip().lower()
            series = str(md.get("series_name") or "").strip().lower()
            app = str(md.get("app_name") or md.get("app") or "").strip().lower()
            app_id = str(md.get("app_id") or "").strip().lower()
            query = str(md.get("query") or "").strip().lower()
            pos_tok = ""
            if ds == "playing":
                pos_raw = md.get("position")
                if pos_raw is not None:
                    try:
                        pos_tok = f"{int(max(0.0, float(pos_raw)) * 4.0)}"
                    except (TypeError, ValueError):
                        pos_tok = ""
            return "|".join(
                (ck, ds, title, artist, album, series, app, app_id, query, pos_tok)
            )

        def _bump_clock_saver_significant_device_from_metadata(md: dict[str, object]) -> None:
            """Content/play-state changes postpone savers; volume only affects the 300 s path."""
            ck = _content_key_from_metadata(md)
            ds = _coarse_device_state_for_saver(str(md.get("device_state") or ""))
            vk = _vol_norm_for_clock_saver(md.get("volume_percent"))
            fp = _metadata_activity_fingerprint(md)
            if not _cs_sig_init[0]:
                _cs_sig_init[0] = True
                _cs_sig_ck[0] = ck
                _cs_sig_ds[0] = ds
                _cs_sig_vol[0] = vk
                _cs_sig_fp[0] = fp
                _note_metadata_activity()
                return
            vol_bump = vk != _cs_sig_vol[0] and (vk or _cs_sig_vol[0])
            content_bump = fp != _cs_sig_fp[0]
            # Keep legacy ck/ds tracking for diagnostics; content fingerprint is authoritative.
            if ck != _cs_sig_ck[0] and (ck or _cs_sig_ck[0]):
                content_bump = True
            if ds != _cs_sig_ds[0]:
                content_bump = True
            _cs_sig_ck[0] = ck
            _cs_sig_ds[0] = ds
            _cs_sig_vol[0] = vk
            _cs_sig_fp[0] = fp
            if content_bump:
                _bump_clock_saver_significant_device()
                _note_metadata_activity()
            elif vol_bump:
                # Volume must not reset the 2-minute metadata-idle saver.
                _bump_clock_saver_significant_device()

        def _reset_clock_saver_device_signal_baseline() -> None:
            _cs_sig_init[0] = False
            _cs_sig_ck[0] = None
            _cs_sig_ds[0] = ""
            _cs_sig_vol[0] = ""
            _cs_sig_fp[0] = ""
            _bump_clock_saver_significant_device()
            _note_metadata_activity()

        def _apply_position_stall_grace_to_clock_saver(now: float) -> None:
            """Keep the saver device-signal timer pinned to ``now`` while content position is live.

            The saver timer is "paused" (continually bumped to ``now``) for as long as the reported
            content position has advanced within :data:`CLOCK_SAVER_POSITION_STALL_GRACE_S`. Once the
            position stays flat beyond that window, we stop bumping and the existing 300 s
            ``CLOCK_SAVER_AFTER_S`` accumulator begins counting from the last advance. A fresh
            position advance during an already-open saver drops the dev-idle span back to zero here,
            so ``_clock_saver_active`` returns ``False`` on the very next tick → saver ends.
            """
            lm = last_timecode_motion_mono
            if lm <= 0.0:
                return
            if (now - lm) >= CLOCK_SAVER_POSITION_STALL_GRACE_S:
                return
            last_clock_saver_significant_device_mono[0] = now

        _cs_meta_idle_log_mono = [0.0]
        _cs_meta_idle_was_active = [False]

        def _something_playing_now() -> bool:
            """True when the player reports active playback (not idle/home/paused-only)."""
            if bool(apple_tv_playback_clock.get("playing")):
                return True
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict):
                return False
            return "Playing" in str(lm.get("device_state") or "")

        def _tmdb_info_current_and_available() -> bool:
            """True when live TMDb art/title matches the current show and is ready."""
            if apple_tv_auto_state.get("tmdb_missing_art"):
                return False
            if apple_tv_auto_state.get("tmdb_fetch_in_flight"):
                return False
            if not str(active_tmdb_title_key or "").strip():
                return False
            md = apple_tv_auto_state.get("last_metadata")
            query = ""
            if isinstance(md, dict):
                query = str(md.get("query") or md.get("ocr_title") or "").strip()
            if not query:
                query = str(apple_tv_auto_state.get("query") or "").strip()
            if not query:
                return False
            try:
                from pigeon.tmdb_poster import is_degenerate_tmdb_query

                if is_degenerate_tmdb_query(query):
                    return False
            except Exception:
                pass
            prev = apple_tv_auto_state.get("tmdb_key")
            if not prev:
                return True
            prefer = str(apple_tv_auto_state.get("prefer") or "auto")
            new_id = _tmdb_spawn_identity(query, prefer)
            if prev == new_id:
                return True
            if isinstance(prev, tuple) and len(prev) == 2:
                try:
                    from pigeon.tmdb_poster import equivalent_tmdb_search_queries

                    if equivalent_tmdb_search_queries(str(prev[0]), new_id[0]):
                        return True
                except Exception:
                    pass
            return False

        def _metadata_drives_clock_saver() -> bool:
            """True when player metadata (not HDMI OCR streak) owns clock-saver idle.

            Position progress is the primary signal. When metadata is driving, the
            HDMI 24-OCR unchanged-frame rule is ignored.
            """
            try:
                from pigeon.source_toggles import source_enabled

                if not source_enabled("metadata"):
                    return False
            except Exception:
                pass
            if bool(apple_tv_playback_clock.get("has_sync")):
                return True
            md = apple_tv_auto_state.get("last_metadata")
            if not isinstance(md, dict):
                return False
            try:
                from pigeon.display_confidence import player_metadata_adequate

                return bool(player_metadata_adequate(md))
            except Exception:
                return bool(str(md.get("query") or md.get("title") or "").strip())

        def _clock_saver_active(now: float) -> bool:
            if clock_saver_composite_bgra is None:
                return False
            if dev_phase != DevPhase.OFF:
                return False
            # Do not require ``scene_enabled``: view ONE now-playing (circles) commonly
            # runs with the video scene off, and the saver must still arm there.
            _apply_position_stall_grace_to_clock_saver(now)
            # TMDb art dismisses the boot saver, but does not block idle / position stall.
            if _tmdb_info_current_and_available():
                _boot_clock_saver_until_playback[0] = False
            # Boot: if nothing is playing after splash, stay on the saver until playback
            # starts or a local control dismisses it.
            if _boot_clock_saver_until_playback[0]:
                if _something_playing_now():
                    _boot_clock_saver_until_playback[0] = False
                    _note_metadata_activity(now)
                elif startup_ph[0] is None or _splash_reveal_clock[0]:
                    return True
            # HDMI-only: 24 consecutive unchanged OCR frames → saver.
            # Ignored while player metadata is driving (position is authoritative).
            if not _metadata_drives_clock_saver():
                try:
                    from pigeon.hdmi_ocr import hdmi_clock_saver_due
                    from pigeon.source_toggles import source_enabled

                    if source_enabled("hdmi") and hdmi_clock_saver_due():
                        return True
                except Exception:
                    pass
            # Content metadata + HDMI unchanged for 2 min → saver until content changes
            # **or** any local Pigeon control is used. Position not advancing is primary.
            lma = float(last_metadata_activity_mono[0])
            meta_age = (now - lma) if lma > 0.0 else 0.0
            ui_age = now - float(last_pigeon_user_activity_mono[0])
            meta_idle = (
                lma > 0.0
                and meta_age >= CLOCK_SAVER_METADATA_IDLE_AFTER_S
                and ui_age >= CLOCK_SAVER_METADATA_IDLE_AFTER_S
            )
            if meta_idle != _cs_meta_idle_was_active[0] or (
                now - float(_cs_meta_idle_log_mono[0])
            ) >= 30.0:
                _cs_meta_idle_log_mono[0] = now
                _cs_meta_idle_was_active[0] = bool(meta_idle)
                sys.stderr.write(
                    f"pigeon: clock_saver meta_idle age={meta_age:.0f}s "
                    f"ui_age={ui_age:.0f}s need={CLOCK_SAVER_METADATA_IDLE_AFTER_S:.0f}s "
                    f"active={bool(meta_idle)} boot={bool(_boot_clock_saver_until_playback[0])} "
                    f"scene={bool(scene_enabled)} phase={dev_phase!s}\n"
                )
                sys.stderr.flush()
            if meta_idle:
                return True
            ui_idle = (now - float(last_pigeon_user_activity_mono[0])) >= CLOCK_SAVER_AFTER_S
            dev_idle = (now - float(last_clock_saver_significant_device_mono[0])) >= CLOCK_SAVER_AFTER_S
            return ui_idle and dev_idle

        def _effective_display_view() -> DisplayView:
            """Logical display for composition. GRID / view 5 overlay preview as view 1 + snapshot layout."""
            if _PIGEON_EXT and (
                dev_phase == DevPhase.GRID or display_view_holder[0] == DisplayView.FIVE
            ):
                return DisplayView.ONE
            return display_view_holder[0]

        def _clock_startup_intro_opacity(now: float) -> float | None:
            """Smooth 0→1 while the post-splash clock is the first reveal; else ``None``."""
            t0 = post_splash_mono[0]
            if t0 is None or startup_ph[0] is not None:
                return None
            if clock_saver_composite_bgra is None:
                return None
            elapsed = now - float(t0)
            if elapsed >= float(CLOCK_STARTUP_FADE_S):
                return None
            u = max(0.0, min(1.0, elapsed / max(1e-6, float(CLOCK_STARTUP_FADE_S))))
            return u * u * (3.0 - 2.0 * u)

        def _clock_saver_layer_opacity(now: float) -> float:
            intro = _clock_startup_intro_opacity(now)
            if intro is not None:
                return float(intro)
            if now < clock_saver_peek_until_mono[0]:
                return 1.0
            # Boot / splash-reveal: full-on clock (no ease from black, no idle dim).
            if _boot_clock_saver_until_playback[0] or _splash_reveal_clock[0]:
                return 1.0
            return CLOCK_SAVER_DIM_OPACITY

        def _backdrop_active_for_view() -> bool:
            """True when backdrop scene should be used by the current effective view."""
            return bool(use_backdrop_scene and _effective_display_view() != DisplayView.SIX)

        def _design_grid_overlay_active() -> bool:
            """Grid overlay on the composite (developer GRID phase or view 5)."""
            return dev_phase == DevPhase.GRID or display_view_holder[0] == DisplayView.FIVE

        def _stage_grid_overlay_mode() -> str:
            """Overlay mode for grid rendering."""
            if display_view_holder[0] == DisplayView.FIVE:
                vf = int(view_five_mode_holder[0])
                if vf == 1:
                    return "absolute_lines_fractional"
                if vf == 2:
                    return "absolute_lines_test"
                return "absolute_lines"
            return "legacy_grid"

        def _clock_saver_for_compose(now: float) -> bool:
            """True when the large saver time/date patches should be drawn (idle path)."""
            if clock_saver_composite_bgra is None:
                return False
            if _clock_startup_intro_opacity(now) is not None:
                if _tmdb_info_current_and_available():
                    return False
                return True
            if dev_phase != DevPhase.OFF:
                return False
            # Splash overlay: keep underlay black until reveal frame, then paint clock under PNG alpha.
            if startup_ph[0] is not None:
                return bool(_splash_reveal_clock[0])
            ev = _effective_display_view()
            if ev == DisplayView.FOUR:
                return False
            # View ONE now-playing may run with scene off; still allow the idle saver.
            if (not scene_enabled) and ev != DisplayView.ONE:
                return False
            if clock_saver_force_on[0]:
                return True
            return _clock_saver_active(now)

        def _toggle_clock_saver_force(event: tk.Event | None = None) -> str | None:
            """Global [2]: force clock saver on/off (digit 2 is not a display-view hotkey)."""
            nonlocal skip_cache
            if event is not None and _widget_accepts_typing(event.widget):
                return None
            if not _PIGEON_EXT or clock_saver_composite_bgra is None:
                return None
            now = time.monotonic()
            showing = bool(_clock_saver_for_compose(now))
            if showing or clock_saver_force_on[0]:
                clock_saver_force_on[0] = False
                _boot_clock_saver_until_playback[0] = False
                _bump_pigeon_user_activity(event)
                _note_metadata_activity(now)
                _bump_clock_saver_significant_device()
            else:
                clock_saver_force_on[0] = True
                if event is not None:
                    _bump_pigeon_user_activity(event)
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            return "break"

        def _app_logo_clock_saver_style_now() -> bool:
            """Dim, row-2–top app logo layout when there is no TMDb still (letterbox master) in saver contexts."""
            if not backdrop_app_logo_letterbox_fit:
                return False
            return _clock_saver_for_compose(time.monotonic())

        def _clock_saver_backdrop_brightness(now: float) -> float:
            """1 = full brightness; idle clock-saver on backdrop uses ``CLOCK_SAVER_BACKDROP_DIM``."""
            if not _backdrop_active_for_view() or backdrop_master_bgr is None:
                return 1.0
            if not _clock_saver_for_compose(now):
                return 1.0
            return float(CLOCK_SAVER_BACKDROP_DIM)

        def _clock_saver_dim_pre_digit_canvas(canvas: np.ndarray, dim: float) -> None:
            """Darken backdrop/gradient/mic before drawing saver glyphs so only the large time reads at full brilliance."""
            d = max(0.0, min(1.0, float(dim)))
            if d >= 1.0 - 1e-9:
                return
            canvas[:] = (canvas.astype(np.float32) * d).clip(0, 255).astype(np.uint8)

        def _clock_saver_dim_overlay_bgra(patch_bgra: np.ndarray, dim: float) -> np.ndarray:
            """Idle-dim streaming badge / receiver rows during clock saver (alpha scale; callers may skip if dim≈1)."""
            d = max(0.0, min(1.0, float(dim)))
            if d >= 1.0 - 1e-9:
                return patch_bgra
            out = patch_bgra.copy().astype(np.float32)
            out[:, :, 3] *= d
            return np.clip(out, 0, 255).astype(np.uint8)

        idle_dim_anim_strength = 0.0
        _idle_dim_anim_goal = 0.0
        _idle_dim_anim_from = 0.0
        _idle_dim_anim_t0 = time.monotonic()

        def _update_idle_dim_strength(now: float) -> float:
            """0 = full color, 1 = red luma-mono; eases in/out over ATV_IDLE_MONO_ANIM_S when combined idle state changes."""
            nonlocal idle_dim_anim_strength, _idle_dim_anim_goal, _idle_dim_anim_from, _idle_dim_anim_t0
            if not THEATER_IDLE_DIM_ENABLED:
                if idle_dim_anim_strength != 0.0 or _idle_dim_anim_goal != 0.0:
                    idle_dim_anim_strength = 0.0
                    _idle_dim_anim_goal = 0.0
                    _idle_dim_anim_from = 0.0
                    _idle_dim_anim_t0 = now
                return 0.0
            want = 1.0 if _atv_idle_monochrome_active() else 0.0
            if want != _idle_dim_anim_goal:
                _idle_dim_anim_goal = want
                _idle_dim_anim_from = idle_dim_anim_strength
                _idle_dim_anim_t0 = now
            dur = float(ATV_IDLE_MONO_ANIM_S)
            t = min(1.0, (now - _idle_dim_anim_t0) / dur) if dur > 0 else 1.0
            idle_dim_anim_strength = _idle_dim_anim_from + (_idle_dim_anim_goal - _idle_dim_anim_from) * t
            return idle_dim_anim_strength

        _compose_idle_strength_holder: list[float] = [0.0]

        scaled_display: np.ndarray | None = None
        scaled_version = 0
        if last_frame is not None:
            scaled_display = _disp_fit().scale_and_crop(last_frame)
            scaled_version = 1

        skip_cache: tuple[object, ...] | None = None
        scene_enabled = _load_persisted_scene_enabled(True)
        dev_phase = DevPhase.OFF
        # Advanced matrix: temporarily show GRID behind the dialog when opened from Settings; restore on close.
        advanced_matrix_restore_phase: list[object] = [None]
        advanced_matrix_close_skip: list[bool] = [False]
        location_toast_state: dict[str, object] = {
            "active": False,
            "text": "",
            "t0": 0.0,
            "startup_top_left": False,
        }
        prev_dev_phase_for_location_toast: list[DevPhase] = [DevPhase.OFF]
        clock_saver_peek_until_mono: list[float] = [0.0]
        # Manual [2] force: True = show saver until toggled off (ignores idle timers).
        clock_saver_force_on: list[bool] = [False]
        black_photo: ImageTk.PhotoImage | None = None
        label_live_photo: list[ImageTk.PhotoImage | None] = [None]
        _render_after_id: list[str | None] = [None]
        use_backdrop_scene = False
        backdrop_master_bgr: np.ndarray | None = None
        # Last TMDb backdrop (copy); survives display off so developer-grid F10 can return to backdrop.
        saved_backdrop_master_bgr: np.ndarray | None = None
        # True when the saved/current master came from the streaming app logo (not TMDb stills).
        saved_backdrop_app_logo_letterbox_fit: bool = False
        backdrop_app_logo_letterbox_fit: bool = False

        if _PIGEON_EXT and prepare_default_poster_at_startup is not None:
            try:
                ok_sp, msg_sp, _gc_sp = prepare_default_poster_at_startup()
                sys.stderr.write(f"pigeon: startup poster: {msg_sp}\n")
                sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f"pigeon: startup poster error: {e}\n")
                sys.stderr.flush()

        clock_widget = (
            ClockCalendarWidget(
                anchor_row=CLOCK_WIDGET_ROW,
                anchor_col_right=float(VIEW_ONE_CLOCK_COL_RIGHT),
            )
            if _PIGEON_EXT and ClockCalendarWidget is not None
            else None
        )
        info_cluster_clock_widget = (
            ClockCalendarWidget(
                anchor_row=float(INFO_CLUSTER_CLOCK_ROW_1BASED),
                anchor_col_right=float(INFO_CLUSTER_COL_RIGHT),
                placement="overlay",
            )
            if _PIGEON_EXT and ClockCalendarWidget is not None
            else None
        )
        tmdb_logo_widget = (
            TmdbLogoWidget(
                anchor_row=TMDB_LOGO_ANCHOR_ROW,
                anchor_col=0,
                span_wide=TMDB_LOGO_SPAN_W,
                span_tall=TMDB_LOGO_SPAN_H,
                fit_scale=TMDB_LOGO_FIT_SCALE,
                vertical_align="top",
                top_right_col_1based=float(TMDB_LOGO_TOP_RIGHT_COL),
            )
            if _PIGEON_EXT and TmdbLogoWidget is not None
            else None
        )
        tmdb_logo_widget_view_six = (
            TmdbLogoWidget(
                anchor_row=TMDB_LOGO_VIEW6_ANCHOR_ROW,
                anchor_col=TMDB_LOGO_VIEW6_ANCHOR_COL,
                span_wide=TMDB_LOGO_VIEW6_SPAN_W,
                span_tall=TMDB_LOGO_VIEW6_SPAN_H,
                fit_scale=TMDB_LOGO_VIEW6_FIT_SCALE,
                vertical_align="center",
            )
            if _PIGEON_EXT and TmdbLogoWidget is not None
            else None
        )
        active_tmdb_title_key: str | None = None
        active_tmdb_display_title: str | None = None
        # When TMDb returns no match for the current playback title, surface the streaming
        # app's own logo in the content-logo slot instead of popping an error dialog.
        # ``_warm_tmdb_logo_patch`` consults this flag whenever no TMDb logo is active.
        tmdb_logo_app_fallback_active: bool = False
        # Contrast-aware bottom gradient tint: ``_warm_tmdb_logo_patch`` evaluates the active
        # TT logo via ``pigeon.tmdb_tt_contrast.pick_gradient_bgr`` and stores the winning
        # ``(B, G, R)`` here. Default black preserves legacy behaviour when no TT is loaded.
        tmdb_tt_gradient_bgr_holder: list[tuple[int, int, int]] = [GRADIENT_BGR_DARK]
        status_bar_widget = None
        if _PIGEON_EXT and StatusBarWidget is not None:
            status_bar_widget = StatusBarWidget(
                assets_dir=Path(_PROJECT_DIR) / "pigeonAssets",
                trt_row=TRT_DISPLAY_ROW,
                trt_played_col=TRT_PLAYED_COL,
                trt_remaining_col=TRT_REMAINING_COL,
                trt_played_text=TRT_PLAYED_TEXT,
                trt_remaining_text=TRT_REMAINING_TEXT,
                trt_label_span_wide=TRT_LABEL_SPAN_W,
                trt_label_span_tall=TRT_LABEL_SPAN_H,
            )

        receiver_overlay_state: dict[str, str] = {
            "incoming": "",
            "config": "",
            "volume": "",
        }
        receiver_telnet_debug_holder: list[dict[str, str]] = [{}]
        # Track the last usable Denon volume reading so the Apple TV metadata poll (which
        # reports ``volume_percent=0`` when an AV receiver owns the volume line) does not
        # briefly overwrite the authoritative dB value on its own cadence. The receiver
        # poll keeps running on its own schedule; this cache only controls *display*.
        denon_vol_cache: dict[str, object] = {
            "effective": "",
            "mono_usable": 0.0,
            # Last volume string shown on View 1 (survives brief empty polls).
            "np_hold": "",
        }
        # True when the last Denon poll answered but reported OFF/STANDBY — hide all
        # receiver metadata and treat the receiver indicator as inactive.
        receiver_standby_holder: list[bool] = [False]
        streaming_badge_state: dict[str, object] = {
            "show": False,
            "filename": "",
            "label": "",
        }
        playback_overlay_flags: dict[str, bool] = {
            "show_paused_row": False,
            "clock_saver_volume_only": False,
            "clock_saver_netflix_full_overlay": False,
            "badge_live_instead_of_logo": False,
        }
        playback_overlay_widget = None
        if _PIGEON_EXT and PlaybackOverlayWidget is not None:
            playback_overlay_widget = PlaybackOverlayWidget(
                assets_dir=Path(_PROJECT_DIR) / "pigeonAssets",
                receiver_state=receiver_overlay_state,
                service_badge=streaming_badge_state,
                overlay_flags=playback_overlay_flags,
                badge_top_right_col_1based=VIEW_ONE_BADGE_COL_RIGHT,
                volume_top_right_col_1based=float(VIEW_ONE_CLOCK_COL_RIGHT),
            )

        # Now-playing: five-zone circles skin only.
        view_circles_widget = None
        if _PIGEON_EXT and ViewCirclesWidget is not None:
            view_circles_widget = ViewCirclesWidget(
                assets_dir=Path(_PROJECT_DIR) / "pigeonAssets",
            )
        main_settings_widget = None
        if _PIGEON_EXT and MainSettingsWidget is not None:
            main_settings_widget = MainSettingsWidget(
                assets_dir=Path(_PROJECT_DIR) / "pigeonAssets",
            )
            try:
                main_settings_widget.state.version_string = version_string()
                main_settings_widget.state.update_local_version = version_string()
            except Exception:
                pass

        def _view_one_uses_now_playing_screen() -> bool:
            return (
                _effective_display_view() == DisplayView.ONE
                and view_circles_widget is not None
            )

        def _sync_now_playing_screen_state() -> None:
            nonlocal skip_cache
            if view_circles_widget is None:
                return
            try:
                from pigeon.hdmi_ocr import probe_hdmi_presence

                probe_hdmi_presence()
            except Exception:
                pass
            prog = _playback_progress_fraction_for_bar()
            progress = float(prog) if prog is not None else 0.0
            remaining_text = ""
            played_text = ""
            clk = apple_tv_playback_clock
            if clk.get("live_mode"):
                remaining_text = "LIVE"
                played_text = "LIVE"
            else:
                pair = _playback_extrapolated_pair()
                if pair is not None:
                    played_text = _format_hmmss(int(pair[0]))
                    remaining_text = _format_hmmss(int(pair[1]))
            inc, cfg, vol = _resolve_receiver_lines_for_now_playing()
            circles_poster_bgra = _circles_poster_bgra()
            has_np = _effective_display_view() == DisplayView.ONE
            sb = streaming_badge_state
            badge_label = str(sb.get("label") or "").strip()
            changed = False
            vol_frac = 0.0
            try:
                from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

                vol_frac = float(volume_fraction_from_display_line(vol))
            except Exception:
                vol_frac = 0.0
            try:
                from pigeon.runtime_state import update_receiver_runtime

                update_receiver_runtime(
                    incoming=inc,
                    config=cfg,
                    volume=vol,
                    volume_fraction=vol_frac,
                    muted=vol.strip().lower() in ("mute", "muted", "off"),
                )
            except Exception:
                pass
            circles_paused = bool(_show_paused_row_overlay())
            circles_service = badge_label
            if not circles_service:
                lm_svc = apple_tv_auto_state.get("last_metadata")
                if isinstance(lm_svc, dict):
                    circles_service = str(lm_svc.get("app_name") or "").strip()
            if _vv_is_music():
                lm_music = apple_tv_auto_state.get("last_metadata")
                song_t = album_t = artist_t = ""
                if isinstance(lm_music, dict):
                    song_t = str(lm_music.get("title") or "").strip()
                    album_t = str(lm_music.get("album") or "").strip()
                    artist_t = str(lm_music.get("artist") or "").strip()
                    # Match classic music text: promote album when title is empty.
                    if not song_t and album_t:
                        song_t, album_t = album_t, ""
                if view_circles_widget.update_state(
                    progress=progress,
                    elapsed_text=played_text,
                    remaining_text=remaining_text,
                    volume_text=vol,
                    volume_fraction=vol_frac,
                    incoming_audio=inc,
                    playback_config=cfg,
                    cast=[],
                    poster_bgra=circles_poster_bgra,
                    has_now_playing=has_np,
                    searching=False,
                    missing_art=False,
                    content_mode="music",
                    song_title=song_t,
                    album_title=album_t,
                    artist_title=artist_t,
                    paused=circles_paused,
                    service_name=circles_service,
                    has_position=_has_playback_position(),
                ):
                    changed = True
            else:
                cast_rows: list[tuple[str, str]] = []
                try:
                    from pigeon.tmdb_poster import get_cached_tmdb_cast

                    tk = str(active_tmdb_title_key or "").strip()
                    if tk:
                        cast_rows = get_cached_tmdb_cast(tk)
                except Exception:
                    cast_rows = []
                fetch_busy = bool(
                    apple_tv_auto_state.get("tmdb_fetch_in_flight")
                    or apple_tv_auto_state.get("pending_tmdb")
                )
                missing_art = bool(apple_tv_auto_state.get("tmdb_missing_art"))
                if view_circles_widget.update_state(
                    progress=progress,
                    elapsed_text=played_text,
                    remaining_text=remaining_text,
                    volume_text=vol,
                    volume_fraction=vol_frac,
                    incoming_audio=inc,
                    playback_config=cfg,
                    cast=cast_rows,
                    poster_bgra=circles_poster_bgra,
                    has_now_playing=has_np,
                    searching=fetch_busy and not missing_art,
                    missing_art=missing_art and not fetch_busy,
                    content_mode="video",
                    song_title="",
                    album_title="",
                    artist_title="",
                    paused=circles_paused,
                    service_name=circles_service,
                    has_position=_has_playback_position(),
                ):
                    changed = True
            if changed:
                skip_cache = None

        def _clear_now_playing_view_caches() -> None:
            if view_circles_widget is not None:
                view_circles_widget.clear_cache()

        def _enable_now_playing_screen() -> None:
            """Show View 1 now-playing chrome (circles skin). Idempotent."""
            nonlocal skip_cache, last_frame, scene_enabled, brightness_current, brightness_from, brightness_target
            if not _PIGEON_EXT or view_circles_widget is None:
                return
            display_view_holder[0] = DisplayView.ONE
            # View 1 chrome composites without a video ``last_frame``; keep scene off so
            # ``render_once`` does not early-return before painting the now-playing screen.
            scene_enabled = False
            last_frame = None
            brightness_current = brightness_from = brightness_target = LANDING_DISPLAY_BRIGHTNESS
            if status_bar_widget is not None:
                if status_bar_widget.set_now_playing_chrome_visible(True):
                    _warm_status_bar_blits()
            _sync_now_playing_screen_state()
            skip_cache = None

        def _activate_now_playing_after_splash() -> None:
            """Splash lifted — now-playing should already be live under the overlay."""
            nonlocal skip_cache
            _enable_now_playing_screen()
            _startup_splash_complete[0] = True
            skip_cache = None
            render_once()

        _post_splash_startup_hook[0] = _activate_now_playing_after_splash

        _splash_view_one_warm_done: list[bool] = [False]

        def _log_view_one_startup_phase(phase: str) -> None:
            try:
                dt = time.monotonic() - _app_startup_mono
                sys.stderr.write(f"pigeon: view1 {phase} +{dt:.3f}s\n")
                sys.stderr.flush()
            except Exception:
                pass

        def _warm_view_one_splash_chrome_only(*, phase: str = "chrome-only") -> None:
            """Rasterize View 1 SVG chrome early (no playback poll helpers required)."""
            if not _PIGEON_EXT or view_circles_widget is None:
                return
            t0 = time.monotonic()
            display_view_holder[0] = DisplayView.ONE
            if status_bar_widget is not None:
                status_bar_widget.set_now_playing_chrome_visible(True)
            if view_circles_widget.set_now_playing_chrome_visible(True):
                view_circles_widget.clear_cache()
            try:
                view_circles_widget.bgra_frame()
            except Exception:
                pass
            try:
                root.update_idletasks()
            except tk.TclError:
                pass
            _log_view_one_startup_phase(f"{phase} ({(time.monotonic() - t0) * 1000.0:.0f} ms raster)")

        def _warm_view_one_under_splash(*, phase: str = "full-warm") -> None:
            """Full View 1 enable + state sync once playback helpers exist."""
            if not _PIGEON_EXT:
                return
            t0 = time.monotonic()
            _enable_now_playing_screen()
            _warm_status_bar_blits()
            try:
                if view_circles_widget is not None:
                    view_circles_widget.bgra_frame()
            except Exception:
                pass
            try:
                root.update_idletasks()
            except tk.TclError:
                pass
            _splash_view_one_warm_done[0] = True
            _log_view_one_startup_phase(f"{phase} ({(time.monotonic() - t0) * 1000.0:.0f} ms)")

        root.after(150, lambda: _warm_view_one_splash_chrome_only(phase="chrome-early"))

        def _playback_is_netflix_stream() -> bool:
            lm = apple_tv_auto_state.get("last_metadata")
            if _metadata_is_netflix_app(lm if isinstance(lm, dict) else None):
                return True
            sb = streaming_badge_state
            lbl = str(sb.get("label") or "").strip().lower()
            fn = str(sb.get("filename") or "").strip().lower()
            return "netflix" in lbl or "netflix" in fn

        def _backdrop_master_from_streaming_app_logo() -> np.ndarray | None:
            """Letterbox streaming app logo on black; resolves path from metadata when badge file unset."""
            assets_root = Path(_PROJECT_DIR) / "pigeonAssets"
            fn = str(streaming_badge_state.get("filename") or "").strip()
            if not fn:
                lm = apple_tv_auto_state.get("last_metadata")
                if isinstance(lm, dict) and _metadata_is_netflix_app(lm):
                    from pigeon.streaming_service_badges import resolve_streaming_badge_media

                    fn2, _lbl = resolve_streaming_badge_media(
                        assets_root,
                        app_name=str(lm.get("app_name") or ""),
                        app_id=str(lm.get("app_id") or ""),
                    )
                    fn = (fn2 or "").strip()
            if not fn:
                return None
            p = assets_root / fn
            if not p.is_file():
                return None
            try:
                from pigeon.image_ui_protocol import (
                    app_logo_fallback_master_bgr,
                    bgra_to_bgr_on_black,
                    load_image_bgra,
                )

                bgra = load_image_bgra(p)
                if bgra is None or bgra.size == 0:
                    return None
                bgr = bgra_to_bgr_on_black(bgra)
                return app_logo_fallback_master_bgr(
                    bgr,
                    display_w=int(display_dims[0]),
                    display_h=int(display_dims[1]),
                    fraction=float(APP_LOGO_FALLBACK_MAX_RESOLUTION_FRACTION),
                )
            except Exception:
                return None

        def _apply_netflix_backdrop_when_running() -> bool:
            """Netflix foreground: letterbox Netflix logo as backdrop (swap if scene exists, else open scene)."""
            if not _playback_is_netflix_stream():
                return False
            logo_bd = _backdrop_master_from_streaming_app_logo()
            if logo_bd is None:
                return False
            nonlocal cap, scene_enabled, playing, use_backdrop_scene, backdrop_master_bgr
            nonlocal saved_backdrop_master_bgr, backdrop_app_logo_letterbox_fit, saved_backdrop_app_logo_letterbox_fit
            nonlocal last_frame, scaled_display, scaled_version, skip_cache
            nonlocal active_tmdb_title_key, active_tmdb_display_title, tmdb_logo_patch_bgra
            nonlocal tmdb_logo_app_fallback_active
            nonlocal brightness_current, brightness_from, brightness_target, brightness_t0

            fn_sb = str(streaming_badge_state.get("filename") or "").lower()
            if use_backdrop_scene and backdrop_master_bgr is not None:
                if backdrop_app_logo_letterbox_fit and "netflix" in fn_sb:
                    return False
                backdrop_master_bgr = logo_bd
                saved_backdrop_master_bgr = np.asarray(logo_bd, dtype=np.uint8).copy()
                saved_backdrop_app_logo_letterbox_fit = True
                backdrop_app_logo_letterbox_fit = True
                scaled_display = None
                scaled_version += 1
                skip_cache = None
                if status_bar_widget is not None:
                    bd_arr = np.asarray(logo_bd, dtype=np.uint8)
                    if status_bar_widget.set_accent_from_backdrop_bgr(bd_arr):
                        _warm_status_bar_blits()
                        skip_cache = None
                return True

            if not scene_enabled:
                return False
            if not str(current_apple_tv.get("identifier") or "").strip():
                return False

            active_tmdb_title_key = None
            active_tmdb_display_title = None
            tmdb_logo_app_fallback_active = False
            if tmdb_logo_widget is not None:
                tmdb_logo_widget.clear_cache()
            if tmdb_logo_widget_view_six is not None:
                tmdb_logo_widget_view_six.clear_cache()
            _warm_tmdb_logo_patch()
            tmdb_logo_patch_bgra = None
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
                cap = None
            backdrop_master_bgr = logo_bd
            saved_backdrop_master_bgr = np.asarray(logo_bd, dtype=np.uint8).copy()
            saved_backdrop_app_logo_letterbox_fit = True
            backdrop_app_logo_letterbox_fit = True
            use_backdrop_scene = True
            scene_enabled = True
            playing = False
            last_frame = None
            scaled_display = None
            scaled_version += 1
            _save_persisted_scene_enabled(True)
            brightness_current = brightness_from = brightness_target = BACKDROP_BRIGHTNESS
            brightness_t0 = time.monotonic()
            skip_cache = None
            if status_bar_widget is not None:
                bd_arr = np.asarray(logo_bd, dtype=np.uint8)
                if status_bar_widget.set_accent_from_backdrop_bgr(bd_arr):
                    _warm_status_bar_blits()
                    skip_cache = None
            return True

        _pigeon_ui_started_mono = time.monotonic()
        _startup_splash_complete: list[bool] = [False]

        clock_patch_bgra: np.ndarray | None = None
        tmdb_logo_patch_bgra: np.ndarray | None = None
        status_bar_blits: list = []
        playback_overlay_blits: list = []
        info_cluster_blits: list = []
        _info_cluster_blits_sig: list[object | None] = [None]
        # [unix_sec, status_bar accent BGR or None] — clock patch invalidation.
        _clock_patch_sig: list = [-1, None]
        # TopGradient.png removed — top gradient overlay disabled (no-op).
        def _blend_top_gradient_design(canvas: np.ndarray) -> None:
            return

        def _blend_top_gradient_fast(base: np.ndarray, cap_w: int, cap_h: int) -> None:
            return

        def _apply_stage_chrome_colors() -> None:
            b, g, r = get_stage_bgr()
            hx = bgr_to_tk_hex(b, g, r)
            try:
                video_area.configure(bg=hx)
                label.configure(bg=hx)
            except tk.TclError:
                pass

        def _refresh_stage_from_poster() -> None:
            nonlocal black_photo, skip_cache
            if not _PIGEON_EXT:
                set_stage_bgr(0, 0, 0)
            else:
                from pigeon.widgets.poster_art import sync_stage_background_from_active_poster

                sync_stage_background_from_active_poster()
            _apply_stage_chrome_colors()
            black_photo = None
            skip_cache = None

        _refresh_stage_from_poster()

        def _design_rect_to_target(
            wx: int, wy: int, ww: int, wh: int, Wt: int, Ht: int
        ) -> tuple[int, int, int, int]:
            """Map a design-canvas rectangle to target size Wt×Ht (same math as final window mapping)."""
            Wd, Hd = DESIGN_W, DESIGN_H
            scaled_w = int(round(Wd * Ht / float(Hd)))
            x_off = max(0, (scaled_w - Wt) // 2)

            def mx(xd: float) -> int:
                return int(round(xd * scaled_w / float(Wd))) - x_off

            def my(yd: float) -> int:
                return int(round(yd * Ht / float(Hd)))

            x0, y0 = mx(wx), my(wy)
            x1, y1 = mx(wx + ww), my(wy + wh)
            rw = max(1, x1 - x0)
            rh = max(1, y1 - y0)
            if x0 < 0:
                rw += x0
                x0 = 0
            if y0 < 0:
                rh += y0
                y0 = 0
            rw = min(rw, Wt - x0)
            rh = min(rh, Ht - y0)
            if rw < 1 or rh < 1:
                return (0, 0, 1, 1)
            return (x0, y0, rw, rh)

        def _design_rect_to_window(wx: int, wy: int, ww: int, wh: int) -> tuple[int, int, int, int]:
            return _design_rect_to_target(wx, wy, ww, wh, display_dims[0], display_dims[1])

        def _warm_status_bar_blits() -> None:
            nonlocal status_bar_blits
            if status_bar_widget is None:
                status_bar_blits = []
                return
            status_bar_blits = list(status_bar_widget.design_blits())

        def _set_playback_overlay_clock_saver_volume_flag() -> None:
            """True while clock saver is active and the receiver shows a real volume string."""
            from pigeon.widgets.playback_overlay import _receiver_volume_display_line

            vol = _receiver_volume_display_line(receiver_overlay_state.get("volume", ""))
            cs_ok = _clock_saver_for_compose(time.monotonic())
            nf_bd = bool(cs_ok and _backdrop_active_for_view() and _playback_is_netflix_stream())
            playback_overlay_flags["clock_saver_netflix_full_overlay"] = nf_bd
            playback_overlay_flags["clock_saver_volume_only"] = bool(
                cs_ok and vol and not nf_bd
            )

        def _warm_playback_overlay_blits() -> None:
            nonlocal playback_overlay_blits
            if playback_overlay_widget is None:
                playback_overlay_blits = []
            else:
                try:
                    playback_overlay_flags["show_paused_row"] = _show_paused_row_overlay()
                    playback_overlay_flags["badge_live_instead_of_logo"] = (
                        _view_one_streaming_logo_duplicate_fallback()
                    )
                    _set_playback_overlay_clock_saver_volume_flag()
                    playback_overlay_blits = list(playback_overlay_widget.design_blits())
                except Exception as exc:
                    print(f"[pigeon] playback overlay blit warmup failed: {exc}", flush=True)
                    playback_overlay_blits = []
            try:
                _warm_info_cluster_blits(time.monotonic())
            except Exception as exc:
                print(f"[pigeon] info cluster blit warmup failed: {exc}", flush=True)

        def _info_cluster_compose_active(now_mono: float) -> bool:
            if not _PIGEON_EXT:
                return False
            if dev_phase != DevPhase.OFF:
                return False
            if _effective_display_view() == DisplayView.FOUR:
                return False
            if _clock_saver_for_compose(now_mono):
                return False
            # New now-playing screen (070326) draws clock, audio config, and volume.
            if _view_one_uses_now_playing_screen():
                return False
            return True

        def _warm_info_cluster_blits(now_mono: float) -> None:
            nonlocal info_cluster_blits
            if (
                not _PIGEON_EXT
                or build_info_cluster_design_patches is None
                or info_cluster_clock_widget is None
            ):
                info_cluster_blits = []
                _info_cluster_blits_sig[0] = None
                return
            if not _info_cluster_compose_active(now_mono):
                info_cluster_blits = []
                _info_cluster_blits_sig[0] = None
                return
            st = location_toast_state
            startup_tl = bool(st.get("startup_top_left"))
            ta = (
                _location_toast_alpha(now_mono)
                if (bool(st.get("active")) and not startup_tl)
                else 0.0
            )
            acc: tuple[int, int, int] | None = (
                tuple(status_bar_widget.accent_bgr)
                if status_bar_widget is not None
                else None
            )
            sig = (
                int(time.time()),
                str(receiver_overlay_state.get("config", "")),
                str(receiver_overlay_state.get("volume", "")),
                str(st.get("text", "")),
                int(round(float(ta) * 1000.0)),
                int(bool(startup_tl)),
                acc,
            )
            if sig == _info_cluster_blits_sig[0]:
                return
            _info_cluster_blits_sig[0] = sig
            info_cluster_blits = build_info_cluster_design_patches(
                clock_widget=info_cluster_clock_widget,
                audio_config=str(receiver_overlay_state.get("config", "")),
                volume=str(receiver_overlay_state.get("volume", "")),
                location=str(st.get("text", "")),
                location_alpha=float(ta),
                shadow_bgr=acc,
            )

        def _blend_info_cluster_into_target(
            target: np.ndarray, cap_w: int, cap_h: int, now_mono: float
        ) -> None:
            if not _info_cluster_compose_active(now_mono):
                return
            _warm_info_cluster_blits(now_mono)
            if not info_cluster_blits or alpha_blend_bgra_over_bgr is None:
                return
            for ib in info_cluster_blits:
                x0b, y0b, wwb, whb = int(ib.x), int(ib.y), int(ib.w), int(ib.h)
                x2, y2, rw2, rh2 = _design_rect_to_target(x0b, y0b, wwb, whb, cap_w, cap_h)
                _ph2, _pw2 = ib.bgra.shape[:2]
                patch = cv2.resize(
                    ib.bgra,
                    (rw2, rh2),
                    interpolation=cv_resize_interp(_pw2, _ph2, rw2, rh2),
                )
                sub = target[y2 : y2 + rh2, x2 : x2 + rw2]
                sub[:] = alpha_blend_bgra_over_bgr(sub, patch)

        def _active_tmdb_logo_widget():
            if _effective_display_view() == DisplayView.SIX and tmdb_logo_widget_view_six is not None:
                return tmdb_logo_widget_view_six
            return tmdb_logo_widget

        _tmdb_poster_cache: dict[str, object] = {"key": None, "bgra": None}

        def _styled_video_content_c_poster(src_bgra: np.ndarray | None) -> np.ndarray | None:
            """Return poster BGRA with rounded corners + faint white border for viewOne.videoContent_c."""
            if src_bgra is None or src_bgra.size == 0 or src_bgra.ndim != 3:
                return None
            if src_bgra.shape[2] == 4:
                out = src_bgra.copy()
            elif src_bgra.shape[2] == 3:
                out = cv2.cvtColor(src_bgra, cv2.COLOR_BGR2BGRA)
            else:
                return None
            h, w = int(out.shape[0]), int(out.shape[1])
            if h < 4 or w < 4:
                return out

            radius = max(6, int(round(min(w, h) * 0.035)))
            radius = min(radius, max(1, (min(w, h) // 2) - 1))

            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.rectangle(mask, (radius, 0), (w - radius, h), 255, thickness=-1)
            cv2.rectangle(mask, (0, radius), (w, h - radius), 255, thickness=-1)
            cv2.circle(mask, (radius, radius), radius, 255, thickness=-1)
            cv2.circle(mask, (w - radius, radius), radius, 255, thickness=-1)
            cv2.circle(mask, (radius, h - radius), radius, 255, thickness=-1)
            cv2.circle(mask, (w - radius, h - radius), radius, 255, thickness=-1)

            out_alpha = out[:, :, 3].astype(np.float32) * (mask.astype(np.float32) / 255.0)
            out[:, :, 3] = np.clip(out_alpha, 0, 255).astype(np.uint8)

            stroke = max(1, int(round(min(w, h) * 0.006)))
            if stroke > 0 and (h - 2 * stroke) > 2 and (w - 2 * stroke) > 2:
                inner = np.zeros((h, w), dtype=np.uint8)
                ir = max(1, radius - stroke)
                cv2.rectangle(inner, (stroke + ir, stroke), (w - stroke - ir, h - stroke), 255, thickness=-1)
                cv2.rectangle(inner, (stroke, stroke + ir), (w - stroke, h - stroke - ir), 255, thickness=-1)
                cv2.circle(inner, (stroke + ir, stroke + ir), ir, 255, thickness=-1)
                cv2.circle(inner, (w - stroke - ir, stroke + ir), ir, 255, thickness=-1)
                cv2.circle(inner, (stroke + ir, h - stroke - ir), ir, 255, thickness=-1)
                cv2.circle(inner, (w - stroke - ir, h - stroke - ir), ir, 255, thickness=-1)
                border = cv2.subtract(mask, inner)
            else:
                border = np.zeros((h, w), dtype=np.uint8)
            if np.any(border > 0):
                out[border > 0, 0] = 255
                out[border > 0, 1] = 255
                out[border > 0, 2] = 255
                out[border > 0, 3] = np.maximum(out[border > 0, 3], 96)
            return out

        def _active_tmdb_poster_bgra() -> np.ndarray | None:
            """Return cached TMDb *poster* BGRA for the active title key (or ``None``).

            Tries the active key, then a year-stripped alias — Apple TV raw titles often
            keep ``(YYYY)`` while assets are stored under the clean TMDb display name.
            Never falls back to backdrop art (circles poster slot is poster-only).
            """
            if not active_tmdb_title_key:
                return None
            try:
                from pigeon.media_cache import ASSET_POSTER_ART, find_cached_reformatted_asset
                from pigeon.image_ui_protocol import load_image_bgra
                from pigeon.tmdb_poster import split_query_and_year
            except Exception:
                return None
            keys: list[str] = []
            tk0 = str(active_tmdb_title_key).strip()
            if tk0:
                keys.append(tk0)
            try:
                cleaned, _year = split_query_and_year(tk0)
                cleaned = (cleaned or "").strip()
                if cleaned and cleaned not in keys:
                    keys.append(cleaned)
            except Exception:
                pass
            poster_path = None
            for tk in keys:
                poster_path = find_cached_reformatted_asset(tk, ASSET_POSTER_ART)
                if poster_path is not None and poster_path.is_file():
                    break
                poster_path = None
            if poster_path is None:
                return None
            try:
                mtime = poster_path.stat().st_mtime
            except OSError:
                return None
            key = (str(poster_path), float(mtime))
            if _tmdb_poster_cache.get("key") == key:
                hit = _tmdb_poster_cache.get("bgra")
                return hit if isinstance(hit, np.ndarray) else None
            raw = load_image_bgra(poster_path)
            if raw is None or raw.size == 0:
                _tmdb_poster_cache["key"] = key
                _tmdb_poster_cache["bgra"] = None
                return None
            _tmdb_poster_cache["key"] = key
            _tmdb_poster_cache["bgra"] = raw
            return raw

        def _clear_music_artwork_cache() -> None:
            """Drop cached pyatv music artwork (leaving music / idle / track miss)."""
            if (
                apple_tv_auto_state.get("music_artwork_bgra") is None
                and apple_tv_auto_state.get("music_artwork_key") is None
            ):
                return
            apple_tv_auto_state["music_artwork_bgra"] = None
            apple_tv_auto_state["music_artwork_key"] = None

        def _music_artwork_track_key(md: dict[str, object]) -> str:
            return "|".join(
                (
                    str(md.get("hash") or "").strip(),
                    str(md.get("artwork_id") or "").strip(),
                    str(md.get("title") or "").strip(),
                    str(md.get("artist") or "").strip(),
                    str(md.get("album") or "").strip(),
                )
            )

        def _decode_artwork_bytes_bgra(raw: object) -> np.ndarray | None:
            if not isinstance(raw, (bytes, bytearray)) or not raw:
                return None
            try:
                data = np.frombuffer(raw, dtype=np.uint8)
                img = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
            except Exception:
                return None
            if img is None or img.size == 0:
                return None
            if img.ndim == 2:
                return cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
            if img.shape[2] == 3:
                return cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
            if img.shape[2] >= 4:
                return img[:, :, :4].copy()
            return None

        def _store_music_artwork_from_metadata(md: dict[str, object] | None) -> None:
            """Decode/store pyatv artwork bytes for Music; clear when not music."""
            if not isinstance(md, dict):
                _clear_music_artwork_cache()
                return
            mt = str(md.get("media_type") or "").strip().lower()
            is_music = mt == "music" or mt.endswith(".music")
            if not is_music or _atv_metadata_is_content_idle(md):
                _clear_music_artwork_cache()
                return
            track_key = _music_artwork_track_key(md)
            art_bytes = md.get("artwork_bytes")
            bgra = _decode_artwork_bytes_bgra(art_bytes)
            prev_key = apple_tv_auto_state.get("music_artwork_key")
            if bgra is not None:
                apple_tv_auto_state["music_artwork_bgra"] = bgra
                apple_tv_auto_state["music_artwork_key"] = track_key
                return
            # Track changed without artwork this poll — drop stale cover.
            if track_key != prev_key:
                apple_tv_auto_state["music_artwork_bgra"] = None
                apple_tv_auto_state["music_artwork_key"] = track_key

        def _circles_poster_bgra() -> np.ndarray | None:
            """Poster slot for view_circles — poster art only (no backdrop fill)."""
            if _vv_is_music():
                bgra = apple_tv_auto_state.get("music_artwork_bgra")
                if isinstance(bgra, np.ndarray) and bgra.size > 0:
                    return bgra
                return None
            if apple_tv_auto_state.get("tmdb_fetch_in_flight") or apple_tv_auto_state.get(
                "pending_tmdb"
            ):
                return None
            return _active_tmdb_poster_bgra()

        def _resolve_streaming_app_logo_bgra() -> np.ndarray | None:
            """Resolve the streaming-service badge source BGRA (same filename resolution as the
            app-logo backdrop fallback). Returns ``None`` when no usable image is available."""
            assets_root = Path(_PROJECT_DIR) / "pigeonAssets"
            fn = str(streaming_badge_state.get("filename") or "").strip()
            if not fn:
                lm = apple_tv_auto_state.get("last_metadata")
                if isinstance(lm, dict):
                    try:
                        from pigeon.streaming_service_badges import (
                            resolve_streaming_badge_media,
                        )

                        fn2, _lbl = resolve_streaming_badge_media(
                            assets_root,
                            app_name=str(lm.get("app_name") or ""),
                            app_id=str(lm.get("app_id") or ""),
                        )
                        fn = (fn2 or "").strip()
                    except Exception:
                        fn = ""
            if not fn:
                return None
            p = assets_root / fn
            if not p.is_file():
                return None
            try:
                from pigeon.image_ui_protocol import load_image_bgra

                bgra = load_image_bgra(p)
            except Exception:
                return None
            if bgra is None or bgra.size == 0:
                return None
            return bgra

        def _refresh_tmdb_tt_gradient_tint() -> None:
            """Evaluate TT brightness and pick the bottom-gradient tint (black vs white).

            Runs every time ``_warm_tmdb_logo_patch`` refreshes the cached TT patch. Falls
            back to the legacy dark gradient when no TT is available.
            """
            prev = tmdb_tt_gradient_bgr_holder[0]
            chosen, lum = pick_gradient_bgr(tmdb_logo_patch_bgra)
            tmdb_tt_gradient_bgr_holder[0] = chosen
            if chosen != prev:
                label = "white" if chosen == (255, 255, 255) else "black"
                title = active_tmdb_display_title or active_tmdb_title_key or "(no-title)"
                lum_s = f"{lum:.3f}" if lum is not None else "n/a"
                print(
                    f"pigeon: TT contrast → {label} gradient (luminance={lum_s}, title={title!r})",
                    file=sys.stderr,
                )

        def _warm_tmdb_logo_patch() -> None:
            nonlocal tmdb_logo_patch_bgra
            logo_w = _active_tmdb_logo_widget()
            if logo_w is None:
                tmdb_logo_patch_bgra = None
                _refresh_tmdb_tt_gradient_tint()
                return
            patch_wh = None
            if active_tmdb_title_key:
                tmdb_logo_patch_bgra = logo_w.bgra_patch_for_title(
                    active_tmdb_title_key,
                    display_title=active_tmdb_display_title,
                    patch_wh=patch_wh,
                ).copy()
                _refresh_tmdb_tt_gradient_tint()
                return
            if tmdb_logo_app_fallback_active:
                src = _resolve_streaming_app_logo_bgra()
                if src is not None:
                    tmdb_logo_patch_bgra = logo_w.bgra_patch_from_source_bgra(
                        src,
                        patch_wh=patch_wh,
                    ).copy()
                    _refresh_tmdb_tt_gradient_tint()
                    return
            tmdb_logo_patch_bgra = None
            _refresh_tmdb_tt_gradient_tint()

        # ---- View 1 fallback-variant detection (viewOne.01 .. .09) --------
        # These probe live state so ``_current_view_one_variant`` can route the
        # View-1 composition path through the correct fallback. See
        # ``pigeon/view_one_variants.py`` for the decision table.
        def _playback_display_title() -> str:
            """Best on-screen title: TMDb display name, else Apple TV metadata."""
            if (active_tmdb_display_title or "").strip():
                return str(active_tmdb_display_title).strip()
            lm = apple_tv_auto_state.get("last_metadata")
            if isinstance(lm, dict):
                for key in ("title", "series_name", "query", "artist"):
                    s = str(lm.get(key) or "").strip()
                    if s:
                        return s
            return ""

        def _vv_has_content_title() -> bool:
            return bool(_playback_display_title())

        def _vv_has_current_app() -> bool:
            if str(streaming_badge_state.get("filename") or "").strip():
                return True
            lm = apple_tv_auto_state.get("last_metadata")
            if isinstance(lm, dict):
                if str(lm.get("app_name") or "").strip():
                    return True
                if str(lm.get("app_id") or "").strip():
                    return True
            return False

        def _vv_has_tmdb_bd() -> bool:
            # A real TMDb backdrop — NOT the app-logo letterbox fallback that
            # reuses ``backdrop_master_bgr`` as a black-canvas app-logo strip.
            if backdrop_master_bgr is not None and not backdrop_app_logo_letterbox_fit:
                return True
            if (
                saved_backdrop_master_bgr is not None
                and not saved_backdrop_app_logo_letterbox_fit
            ):
                return True
            return False

        def _vv_has_tmdb_tt() -> bool:
            return bool(active_tmdb_title_key)

        def _vv_has_app_logo() -> bool:
            return _resolve_streaming_app_logo_bgra() is not None

        def _vv_is_music() -> bool:
            """True when the currently playing media is of type Music.

            Reads ``last_metadata['media_type']`` (populated via pyatv) and accepts
            both the pyatv ``MediaType.Music`` stringified form and the bare ``Music``
            label to be robust across metadata sources.
            """
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict):
                return False
            mt = str(lm.get("media_type") or "").strip().lower()
            if not mt:
                return False
            return mt == "music" or mt.endswith(".music")

        def _vv_music_track_title() -> str:
            """Return the preferred Music track title for text rendering.

            Prefers ``title``; falls back to ``album`` (often the only populated
            field for certain streaming sources). Returns an empty string when
            nothing usable is available.
            """
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict):
                return ""
            for k in ("title", "album"):
                v = str(lm.get(k) or "").strip()
                if v:
                    return v
            return ""

        def _vv_music_text_lines() -> tuple[str, str]:
            """Return ``(title, subtitle)`` for Music text rendering.

            ``title`` is the track title (large top line); ``subtitle`` is the
            composed ``"Artist - Album"`` string (smaller line beneath) with a
            graceful collapse when either field is missing:

            * both present  → ``"Artist - Album"``
            * artist only   → ``"Artist"``
            * album only    → ``"Album"``
            * neither       → ``""``

            If ``title`` is empty but ``album`` is populated, ``album`` is
            promoted to ``title`` so the large line is never blank; the
            subtitle then collapses to just the artist (if any).
            """
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict):
                return ("", "")
            title = str(lm.get("title") or "").strip()
            artist = str(lm.get("artist") or "").strip()
            album = str(lm.get("album") or "").strip()
            if not title and album:
                title, album = album, ""
            if artist and album:
                subtitle = f"{artist} - {album}"
            elif artist:
                subtitle = artist
            elif album:
                subtitle = album
            else:
                subtitle = ""
            return (title, subtitle)

        def _current_view_one_variant():
            if resolve_view_one_variant is None:
                return None
            return resolve_view_one_variant(
                layout_is_simple=(
                    int(_view_one_layout_effective()) == int(ViewOneLayout.PIGEON_SIMPLE)
                ),
                has_title_meta=_vv_has_content_title(),
                has_app_meta=_vv_has_current_app(),
                has_tmdb_bd=_vv_has_tmdb_bd(),
                has_tmdb_tt=_vv_has_tmdb_tt(),
                has_app_logo=_vv_has_app_logo(),
            )

        def _view_one_streaming_logo_duplicate_fallback() -> bool:
            """viewOne.07: no TMDb title; streaming app logo occupies pigeonTMDB_TT (badge would duplicate it)."""
            if ViewOneVariant is None:
                return False
            return _current_view_one_variant() == ViewOneVariant.V07

        def _view_one_variant_uses_full_path() -> bool:
            if _view_one_is_pigeon_poster():
                return False
            v = _current_view_one_variant()
            if v is None or variant_uses_full_path is None:
                return _view_one_is_pigeon_full()
            return variant_uses_full_path(v)

        def _view_one_variant_uses_simple_path() -> bool:
            if _view_one_is_pigeon_poster():
                return False
            v = _current_view_one_variant()
            if v is None or variant_uses_full_path is None:
                return _view_one_is_pigeon_simple()
            return _stage_is_view_one_video_layout() and not variant_uses_full_path(v)

        def _view_one_video_content_a_tt_contain_rect_design() -> tuple[int, int, int, int]:
            """Design-pixel (x, y, w, h) for pigeonTMDB_TT uniform contain-fit on viewOne.videoContent_a.

            Horizontally the slot is **10 design cells wide**, centered on column **7.5**
            (≈ columns 2.5–12.5): uniform-contain–fit, as large as that band and vertical clearance
            allow. Vertically it clears the streaming badge, receiver-driven overlay lines, and the
            gradient / status region (with a slightly lower floor and tighter gap to the gradient).
            """
            if (
                get_grid_geometry is None
                or rect_for_span_top_right_at_cell is None
                or rect_for_span_at_cell is None
            ):
                return (0, 0, max(1, int(DESIGN_W)), max(1, int(DESIGN_H)))
            g = get_grid_geometry()
            pad = max(4, int(round(0.12 * float(g.cell))))

            bx, by, bw, bh = rect_for_span_top_right_at_cell(
                2,
                1,
                row_1based=0.5,
                col_right_1based=float(VIEW_ONE_BADGE_COL_RIGHT),
            )

            top_min = int(by) + int(bh) + pad
            top_min = max(
                top_min,
                int(round(float(g.y0) + (3.0 - 1.0) * float(g.cell))),
            )
            if playback_overlay_widget is not None:
                try:
                    for _p in playback_overlay_widget.design_blits():
                        if getattr(_p, "layer", "") != PATCH_LAYER_RECEIVER_AUDIO:
                            continue
                        py1 = int(_p.y) + int(_p.h) + pad
                        if py1 > top_min:
                            top_min = min(py1, int(DESIGN_H) - 8)
                except Exception:
                    pass

            # Allow the title treatment to use more vertical band (still below TRT / status row).
            bottom_max = int(round(float(g.y0) + (7.45 - 1.0) * float(g.cell)))
            try:
                if playback_lower_gradient_bgra is not None:
                    _gx, gy, _gw, _gh, _grad = playback_lower_gradient_bgra(
                        gradient_bgr=tmdb_tt_gradient_bgr_holder[0]
                    )
                    # Tighter than ``pad`` so the logo can sit closer to the gradient top edge.
                    _grad_pad = max(2, int(round(0.04 * float(g.cell))))
                    bottom_max = min(bottom_max, int(gy) - _grad_pad)
            except Exception:
                pass
            bottom_max = max(top_min + 8, min(int(DESIGN_H) - pad, bottom_max))

            # Columns 2.5–12.5 (10 cells wide, centered on the former 3–12 band): wider slot → larger TT.
            _tt_span_w = 10.0
            _tt_col_center = 7.5
            gx_tt, _gy_tt, gw_tt, _gh_tt = rect_for_span_at_cell(
                float(_tt_span_w),
                1.0,
                row_1based=1.0,
                col_1based=float(_tt_col_center) - 0.5 * float(_tt_span_w),
            )
            x0 = int(gx_tt)
            x1 = int(gx_tt) + int(gw_tt)
            y0 = max(0, top_min)
            y1 = bottom_max
            rw = int(x1 - x0)
            rh = int(y1 - y0)
            if rw < 48 or rh < 48 or x1 <= x0:
                _legacy = rect_for_span_top_right_at_cell(
                    14, 4, row_1based=2, col_right_1based=17.5
                )
                _rt = int(round(float(g.y0) + (2.5 - 1.0) * float(g.cell)))
                _rb = int(round(float(g.y0) + (6.0 - 1.0) * float(g.cell)))
                _rh = max(1, _rb - _rt)
                return (
                    int(_legacy[0]),
                    int(_rt),
                    int(_legacy[2]),
                    int(_rh),
                )
            return (x0, y0, rw, rh)

        def _paste_bgra_contain_on_design(
            canvas_bgr: np.ndarray,
            patch_bgra: np.ndarray | None,
            rect: tuple[int, int, int, int] | list[int],
        ) -> None:
            """Paste ``patch_bgra`` centered inside ``rect`` on a design-sized
            BGR canvas using uniform contain-fit (no crop)."""
            if patch_bgra is None or alpha_blend_bgra_over_bgr is None:
                return
            rx, ry, rw, rh = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
            if rw < 1 or rh < 1:
                return
            ph, pw = int(patch_bgra.shape[0]), int(patch_bgra.shape[1])
            if pw < 1 or ph < 1:
                return
            scale = min(rw / float(pw), rh / float(ph))
            nw = max(1, int(round(pw * scale)))
            nh = max(1, int(round(ph * scale)))
            rsz = cv2.resize(
                patch_bgra,
                (nw, nh),
                interpolation=cv_resize_interp(pw, ph, nw, nh),
            )
            ox = rx + (rw - nw) // 2
            oy = ry + (rh - nh) // 2
            dst_x0 = max(0, ox)
            dst_y0 = max(0, oy)
            dst_x1 = min(DESIGN_W, ox + nw)
            dst_y1 = min(DESIGN_H, oy + nh)
            if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
                return
            src_x0 = dst_x0 - ox
            src_y0 = dst_y0 - oy
            cw = dst_x1 - dst_x0
            ch = dst_y1 - dst_y0
            crop = rsz[src_y0 : src_y0 + ch, src_x0 : src_x0 + cw]
            sub = canvas_bgr[dst_y0:dst_y1, dst_x0:dst_x1]
            sub[:] = alpha_blend_bgra_over_bgr(sub, crop)

        def _current_app_display_name() -> str:
            """Human-readable name for the currently foregrounded streaming app."""
            label = str(streaming_badge_state.get("label") or "").strip()
            if label:
                return label
            lm = apple_tv_auto_state.get("last_metadata")
            if isinstance(lm, dict):
                for k in ("app_name", "app_id"):
                    v = str(lm.get(k) or "").strip()
                    if v:
                        return v
            return ""

        def _refresh_clock_patch_bgra() -> None:
            nonlocal clock_patch_bgra, _clock_patch_sig
            if clock_widget is None:
                return
            t = int(time.time())
            if status_bar_widget is not None:
                acc: tuple[int, int, int] | None = tuple(status_bar_widget.accent_bgr)
                clock_widget.set_shadow_accent_bgr(acc)
                if info_cluster_clock_widget is not None:
                    info_cluster_clock_widget.set_shadow_accent_bgr(acc)
            else:
                acc = None
            if (
                clock_patch_bgra is not None
                and t == _clock_patch_sig[0]
                and acc == _clock_patch_sig[1]
            ):
                return
            clock_patch_bgra = clock_widget.bgra_patch().copy()
            _clock_patch_sig[0] = t
            _clock_patch_sig[1] = acc

        _playback_overlay_fast_sig: list[tuple[bool, bool, bool, bool, bool] | None] = [None]

        def compose_display_fast_no_grid(
            frame_bgr: np.ndarray | None,
            brightness: float,
            *,
            frame_is_display_sized: bool = False,
        ) -> np.ndarray:
            """Video at display size + poster/clock blits (no full design canvas). Used when developer grid is off."""
            assert _PIGEON_EXT
            # View 1: 070326 now-playing screen only (no classic chrome / TMDB backdrop stack).
            if _effective_display_view() == DisplayView.ONE:
                _set_playback_overlay_clock_saver_volume_flag()
                _warm_tmdb_logo_patch()
                dw, dh = display_dims[0], display_dims[1]
                cap_w, cap_h, use_cap = _composite_cap_dims(dw, dh)
                canvas_np = np.zeros((int(DESIGN_H), int(DESIGN_W), 3), dtype=np.uint8)
                canvas_np[:] = (0, 0, 0)
                now_cs = time.monotonic()
                intro_op = _clock_startup_intro_opacity(now_cs)
                cs_v1 = _clock_saver_for_compose(now_cs)
                # Pre-reveal splash: black underlay. From frame 90: full clock under PNG alpha.
                if startup_ph[0] is not None and not _splash_reveal_clock[0]:
                    pass
                elif intro_op is not None and clock_saver_composite_bgra is not None and alpha_blend_bgra_over_bgr is not None:
                    acc_cs = (
                        tuple(status_bar_widget.accent_bgr)
                        if status_bar_widget is not None
                        else None
                    )
                    (time_bgra, t_rect), (date_bgra, d_rect) = clock_saver_composite_bgra(
                        shadow_bgr=acc_cs,
                        layer_opacity=float(intro_op),
                        time_layer_opacity=float(intro_op),
                        date_layer_opacity=float(intro_op),
                        date_anchor_row=CLOCK_ANCHOR_ROW,
                        date_anchor_col=CLOCK_ANCHOR_COL,
                    )
                    for cs_bgra, (sx, sy, sw, sh) in (
                        (date_bgra, d_rect),
                        (time_bgra, t_rect),
                    ):
                        roi2 = canvas_np[sy : sy + sh, sx : sx + sw]
                        roi2[:] = alpha_blend_bgra_over_bgr(roi2, cs_bgra)
                elif dev_phase == DevPhase.MAIN_SETTINGS and main_settings_widget is not None:
                    main_settings_widget.render(canvas_np)
                elif (
                    cs_v1
                    and clock_saver_composite_bgra is not None
                    and alpha_blend_bgra_over_bgr is not None
                ):
                    # Idle / position-stall saver replaces circles / now-playing chrome.
                    acc_cs = (
                        tuple(status_bar_widget.accent_bgr)
                        if status_bar_widget is not None
                        else None
                    )
                    _cs_dim_v1 = _clock_saver_layer_opacity(now_cs)
                    _clock_saver_dim_pre_digit_canvas(canvas_np, _cs_dim_v1)
                    (time_bgra, t_rect), (date_bgra, d_rect) = clock_saver_composite_bgra(
                        shadow_bgr=acc_cs,
                        layer_opacity=_cs_dim_v1,
                        time_layer_opacity=1.0,
                        date_layer_opacity=_cs_dim_v1,
                        date_anchor_row=CLOCK_ANCHOR_ROW,
                        date_anchor_col=CLOCK_ANCHOR_COL,
                    )
                    for cs_bgra, (sx, sy, sw, sh) in (
                        (date_bgra, d_rect),
                        (time_bgra, t_rect),
                    ):
                        roi2 = canvas_np[sy : sy + sh, sx : sx + sw]
                        roi2[:] = alpha_blend_bgra_over_bgr(roi2, cs_bgra)
                else:
                    _sync_now_playing_screen_state()
                    if view_circles_widget is not None:
                        view_circles_widget.render(canvas_np)
                base2 = cv2.resize(
                    canvas_np,
                    (cap_w, cap_h),
                    interpolation=cv_resize_interp(
                        int(DESIGN_W), int(DESIGN_H), cap_w, cap_h
                    ),
                )
                if use_cap:
                    return _present_frame_to_display(base2, dw, dh)
                return base2
            _set_playback_overlay_clock_saver_volume_flag()
            fast_sig = (
                bool(_backdrop_active_for_view()),
                _show_paused_row_overlay(),
                bool(playback_overlay_flags["clock_saver_volume_only"]),
                bool(playback_overlay_flags["clock_saver_netflix_full_overlay"]),
                bool(playback_overlay_flags.get("badge_live_instead_of_logo")),
            )
            if playback_overlay_widget is not None and _playback_overlay_fast_sig[0] != fast_sig:
                _playback_overlay_fast_sig[0] = fast_sig
                _warm_playback_overlay_blits()
            dw, dh = display_dims[0], display_dims[1]
            cap_w, cap_h, use_cap = _composite_cap_dims(dw, dh)

            if frame_bgr is None or frame_bgr.size == 0:
                sb, sg, sr = get_stage_bgr()
                base = np.empty((cap_h, cap_w, 3), dtype=np.uint8)
                base[:] = (sb, sg, sr)
            else:
                lit = _apply_brightness(frame_bgr, brightness)
                if frame_is_display_sized:
                    if use_cap and (
                        int(lit.shape[1]) != cap_w or int(lit.shape[0]) != cap_h
                    ):
                        _lh, _lw = lit.shape[:2]
                        base = cv2.resize(
                            lit,
                            (cap_w, cap_h),
                            interpolation=cv_resize_interp(_lw, _lh, cap_w, cap_h),
                        )
                    else:
                        base = lit
                else:
                    fit = SceneFit(target_w=cap_w, target_h=cap_h) if use_cap else _disp_fit()
                    base = fit.scale_and_crop(lit)
            now_cs = time.monotonic()
            cs = _clock_saver_for_compose(now_cs)
            intro_op = _clock_startup_intro_opacity(now_cs)
            bdim = _clock_saver_backdrop_brightness(now_cs)
            if intro_op is not None:
                base[:] = 0
            elif bdim < 1.0 - 1e-6:
                base = (base.astype(np.float32) * bdim).astype(np.uint8)
            # Composite order: clock saver / small clock / overlays sit above
            # the bottom gradient.
            if intro_op is None:
                _blend_top_gradient_fast(base, cap_w, cap_h)
            if cs:
                if alpha_blend_bgra_over_bgr is not None:
                    acc_cs = (
                        tuple(status_bar_widget.accent_bgr)
                        if status_bar_widget is not None
                        else None
                    )
                    _cs_dim = _clock_saver_layer_opacity(now_cs)
                    if intro_op is None:
                        _clock_saver_dim_pre_digit_canvas(base, _cs_dim)
                    _time_op = float(intro_op) if intro_op is not None else 1.0
                    _date_op = float(intro_op) if intro_op is not None else _cs_dim
                    (time_bgra, t_rect), (date_bgra, d_rect) = clock_saver_composite_bgra(
                        shadow_bgr=acc_cs,
                        layer_opacity=_cs_dim,
                        time_layer_opacity=_time_op,
                        date_layer_opacity=_date_op,
                        date_anchor_row=CLOCK_ANCHOR_ROW,
                        date_anchor_col=CLOCK_ANCHOR_COL,
                    )
                    for cs_bgra, (sx, sy, sw, sh) in (
                        (date_bgra, d_rect),
                        (time_bgra, t_rect),
                    ):
                        x, y, rw, rh = _design_rect_to_target(sx, sy, sw, sh, cap_w, cap_h)
                        _ch, _cw = cs_bgra.shape[:2]
                        patch = cv2.resize(
                            cs_bgra, (rw, rh), interpolation=cv_resize_interp(_cw, _ch, rw, rh)
                        )
                        sub = base[y : y + rh, x : x + rw]
                        sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                    if (
                        (
                            playback_overlay_flags.get("clock_saver_volume_only")
                            or playback_overlay_flags.get("clock_saver_netflix_full_overlay")
                        )
                        and playback_overlay_blits
                        and alpha_blend_bgra_over_bgr is not None
                    ):
                        for pb in playback_overlay_blits:
                            x0, y0, ww, wh = int(pb.x), int(pb.y), int(pb.w), int(pb.h)
                            x, y, rw, rh = _design_rect_to_target(x0, y0, ww, wh, cap_w, cap_h)
                            _ph, _pw = pb.bgra.shape[:2]
                            patch = cv2.resize(
                                _clock_saver_dim_overlay_bgra(pb.bgra, _cs_dim),
                                (rw, rh),
                                interpolation=cv_resize_interp(_pw, _ph, rw, rh),
                            )
                            sub = base[y : y + rh, x : x + rw]
                            sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
            else:
                if (
                    playback_lower_gradient_bgra is not None
                    and alpha_blend_bgra_over_bgr is not None
                    and not _vv_is_music()
                ):
                    gx, gy, gw, gh, grad_bgra = playback_lower_gradient_bgra(
                        gradient_bgr=tmdb_tt_gradient_bgr_holder[0]
                    )
                    x, y, rw, rh = _design_rect_to_target(gx, gy, gw, gh, cap_w, cap_h)
                    _gh, _gw = grad_bgra.shape[:2]
                    patch = cv2.resize(
                        grad_bgra, (rw, rh), interpolation=cv_resize_interp(_gw, _gh, rw, rh)
                    )
                    sub = base[y : y + rh, x : x + rw]
                    sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                if _effective_display_view() != DisplayView.FOUR:
                    if _info_cluster_compose_active(now_cs):
                        _blend_info_cluster_into_target(base, cap_w, cap_h, now_cs)
                    else:
                        _refresh_clock_patch_bgra()
                        if (
                            clock_patch_bgra is not None
                            and clock_widget is not None
                            and alpha_blend_bgra_over_bgr is not None
                        ):
                            dr = getattr(clock_widget, "design_rect", None)
                            wx, wy, ww, wh = dr() if callable(dr) else (0, 0, 0, 0)
                            if ww >= 1 and wh >= 1:
                                x, y, rw, rh = _design_rect_to_target(wx, wy, ww, wh, cap_w, cap_h)
                                _kh, _kw = clock_patch_bgra.shape[:2]
                                patch = cv2.resize(
                                    clock_patch_bgra,
                                    (rw, rh),
                                    interpolation=cv_resize_interp(_kw, _kh, rw, rh),
                                )
                                sub = base[y : y + rh, x : x + rw]
                                sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                if status_bar_blits and alpha_blend_bgra_over_bgr is not None:
                    for sb in status_bar_blits:
                        x0, y0, ww, wh = int(sb.x), int(sb.y), int(sb.w), int(sb.h)
                        x, y, rw, rh = _design_rect_to_target(x0, y0, ww, wh, cap_w, cap_h)
                        _bh, _bw = sb.bgra.shape[:2]
                        patch = cv2.resize(
                            sb.bgra,
                            (rw, rh),
                            interpolation=cv_resize_interp(_bw, _bh, rw, rh),
                        )
                        sub = base[y : y + rh, x : x + rw]
                        sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                if playback_overlay_blits and alpha_blend_bgra_over_bgr is not None:
                    for pb in playback_overlay_blits:
                        if (
                            _info_cluster_compose_active(now_cs)
                            and getattr(pb, "layer", "") == PATCH_LAYER_RECEIVER_AUDIO
                        ):
                            continue
                        x0, y0, ww, wh = int(pb.x), int(pb.y), int(pb.w), int(pb.h)
                        x, y, rw, rh = _design_rect_to_target(x0, y0, ww, wh, cap_w, cap_h)
                        _ph, _pw = pb.bgra.shape[:2]
                        patch = cv2.resize(
                            pb.bgra,
                            (rw, rh),
                            interpolation=cv_resize_interp(_pw, _ph, rw, rh),
                        )
                        sub = base[y : y + rh, x : x + rw]
                        sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                if (
                    dev_phase == DevPhase.OFF
                    and location_toast_patch_bgra is not None
                    and alpha_blend_bgra_over_bgr is not None
                    and (
                        not _info_cluster_compose_active(now_cs)
                        or bool(location_toast_state.get("startup_top_left"))
                    )
                ):
                    now_lt = time.monotonic()
                    ta = _location_toast_alpha(now_lt)
                    if ta > 1e-6:
                        acc = (
                            tuple(status_bar_widget.accent_bgr)
                            if status_bar_widget is not None
                            else None
                        )
                        patch_lt, (lwx, lwy, lww, lwh) = location_toast_patch_bgra(
                            str(location_toast_state["text"]),
                            alpha=ta,
                            shadow_bgr=acc,
                            col_right_offset_cells=0.0,
                            row_offset_cells=0.0,
                            startup_top_left=bool(
                                location_toast_state.get("startup_top_left")
                            ),
                        )
                        if patch_lt is not None:
                            x, y, rw, rh = _design_rect_to_target(lwx, lwy, lww, lwh, cap_w, cap_h)
                            _th, _tw = patch_lt.shape[:2]
                            patch = cv2.resize(
                                patch_lt,
                                (rw, rh),
                                interpolation=cv_resize_interp(_tw, _th, rw, rh),
                            )
                            sub = base[y : y + rh, x : x + rw]
                            sub[:] = alpha_blend_bgra_over_bgr(sub, patch)
                if (
                    _effective_display_view() not in (DisplayView.FOUR, DisplayView.TWO)
                    and (_logo_w := _active_tmdb_logo_widget()) is not None
                    and active_tmdb_title_key
                    and alpha_blend_bgra_over_bgr is not None
                    and not _view_one_uses_now_playing_screen()
                ):
                    if (
                        _effective_display_view() == DisplayView.ONE
                        and not _view_one_is_pigeon_poster()
                    ):
                        dwx, dwy, dww, dwh = _view_one_video_content_a_tt_contain_rect_design()
                    else:
                        dwx, dwy, dww, dwh = _logo_w.design_rect()
                    x, y, rw, rh = _design_rect_to_target(dwx, dwy, dww, dwh, cap_w, cap_h)
                    _logo_patch = _logo_w.bgra_patch_for_title(
                        active_tmdb_title_key,
                        display_title=active_tmdb_display_title,
                        patch_wh=(int(dww), int(dwh)),
                    )
                    _ph, _pw = int(_logo_patch.shape[0]), int(_logo_patch.shape[1])
                    if _pw >= 1 and _ph >= 1 and rw >= 1 and rh >= 1:
                        _sc = min(rw / float(_pw), rh / float(_ph))
                        _nw = max(1, int(round(_pw * _sc)))
                        _nh = max(1, int(round(_ph * _sc)))
                        patch = cv2.resize(
                            _logo_patch,
                            (_nw, _nh),
                            interpolation=cv_resize_interp(_pw, _ph, _nw, _nh),
                        )
                        _ox = x + (rw - _nw) // 2
                        _oy = y + (rh - _nh) // 2
                        _dx0 = max(0, _ox)
                        _dy0 = max(0, _oy)
                        _dx1 = min(cap_w, _ox + _nw)
                        _dy1 = min(cap_h, _oy + _nh)
                        if _dx1 > _dx0 and _dy1 > _dy0:
                            _sx0 = _dx0 - _ox
                            _sy0 = _dy0 - _oy
                            _cw = _dx1 - _dx0
                            _ch = _dy1 - _dy0
                            _crop = patch[_sy0 : _sy0 + _ch, _sx0 : _sx0 + _cw]
                            sub = base[_dy0:_dy1, _dx0:_dx1]
                            sub[:] = alpha_blend_bgra_over_bgr(sub, _crop)
            if use_cap:
                return _present_frame_to_display(base, dw, dh)
            return base

        def compose_display_from_source(
            frame_bgr: np.ndarray | None,
            brightness: float,
            *,
            show_grid: bool,
            frame_is_design_sized: bool = False,
            tmdb_logo_cover_design_xywh: tuple[int, int, int, int] | None = None,
        ) -> np.ndarray:
            """
            Build display output: scale **source** video to design, draw widgets, optionally grid,
            then scale down. Using the raw frame avoids letterboxing an already 800×480 image (which shifted
            the grid/poster and cropped them on the left). Developer grid mode uses uniform letterboxing so
            the full design width (including grid column 1) is visible on narrow windows.
            """
            assert _PIGEON_EXT
            assert scale_height_and_center_crop is not None
            assert scale_cover_center_crop is not None
            assert blend_overlay_bgr is not None
            assert build_stage_overlay_source_bgra is not None

            def _paste_tmdb_logo_uniform_cover_design(
                canvas_bgr: np.ndarray,
                logo_w,
                rx: int,
                ry: int,
                rw: int,
                rh: int,
            ) -> None:
                if (
                    logo_w is None
                    or rw < 1
                    or rh < 1
                    or not active_tmdb_title_key
                    or alpha_blend_bgra_over_bgr is None
                ):
                    return
                patch_bgra = logo_w.bgra_patch_for_title(
                    active_tmdb_title_key,
                    display_title=active_tmdb_display_title,
                    patch_wh=(rw, rh),
                )
                ph, pw = int(patch_bgra.shape[0]), int(patch_bgra.shape[1])
                if pw < 1 or ph < 1:
                    return
                # Uniform fit inside the grid box (no crop): largest scale where both dimensions fit;
                # centers the patch so ascenders / top caps are not clipped (cover would crop).
                scale_c = min(rw / float(pw), rh / float(ph))
                nw = max(1, int(round(pw * scale_c)))
                nh = max(1, int(round(ph * scale_c)))
                rsz = cv2.resize(
                    patch_bgra,
                    (nw, nh),
                    interpolation=cv_resize_interp(pw, ph, nw, nh),
                )
                ox = rx + (rw - nw) // 2
                oy = ry + (rh - nh) // 2
                dst_x0 = max(0, ox)
                dst_y0 = max(0, oy)
                dst_x1 = min(DESIGN_W, ox + nw)
                dst_y1 = min(DESIGN_H, oy + nh)
                if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
                    return
                src_x0 = dst_x0 - ox
                src_y0 = dst_y0 - oy
                cw = dst_x1 - dst_x0
                ch = dst_y1 - dst_y0
                crop2 = rsz[src_y0 : src_y0 + ch, src_x0 : src_x0 + cw]
                sub = canvas_bgr[dst_y0:dst_y1, dst_x0:dst_x1]
                sub[:] = alpha_blend_bgra_over_bgr(sub, crop2)

            def _paste_video_content_c_poster_above_top_gradient(canvas_bgr: np.ndarray) -> None:
                if (
                    not _view_one_is_pigeon_poster()
                    or _effective_display_view() != DisplayView.ONE
                    or alpha_blend_bgra_over_bgr is None
                    or get_grid_geometry is None
                ):
                    return
                g = get_grid_geometry()
                # Full-width band, vertically centered in rows 1→7.5 so the poster reads centered
                # on the canvas (not biased toward the top margin above row 1).
                top_y = int(round(g.y0 + (1.0 - 1.0) * float(g.cell)))
                bottom_y = int(round(g.y0 + (7.5 - 1.0) * float(g.cell)))
                poster_h = max(1, bottom_y - top_y)
                rect = (0, int(top_y), int(DESIGN_W), int(poster_h))
                patch_bgra = _styled_video_content_c_poster(_active_tmdb_poster_bgra())
                if patch_bgra is None:
                    return
                rx, ry, rw, rh = (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3]))
                ph, pw = int(patch_bgra.shape[0]), int(patch_bgra.shape[1])
                if rw < 1 or rh < 1 or pw < 1 or ph < 1:
                    return
                scale = min(rw / float(pw), rh / float(ph))
                nw = max(1, int(round(pw * scale)))
                nh = max(1, int(round(ph * scale)))
                rsz = cv2.resize(
                    patch_bgra,
                    (nw, nh),
                    interpolation=cv_resize_interp(pw, ph, nw, nh),
                )
                ox = rx + (rw - nw) // 2
                oy = ry + (rh - nh) // 2
                dst_x0 = max(0, ox)
                dst_y0 = max(0, oy)
                dst_x1 = min(DESIGN_W, ox + nw)
                dst_y1 = min(DESIGN_H, oy + nh)
                if dst_x1 <= dst_x0 or dst_y1 <= dst_y0:
                    return
                src_x0 = dst_x0 - ox
                src_y0 = dst_y0 - oy
                cw = dst_x1 - dst_x0
                ch = dst_y1 - dst_y0
                crop2 = rsz[src_y0 : src_y0 + ch, src_x0 : src_x0 + cw]
                sub = canvas_bgr[dst_y0:dst_y1, dst_x0:dst_x1]
                sub[:] = alpha_blend_bgra_over_bgr(sub, crop2)
            if frame_bgr is None or frame_bgr.size == 0:
                sb, sg, sr = get_stage_bgr()
                canvas = np.empty((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
                canvas[:] = (sb, sg, sr)
            else:
                lit = _apply_brightness(frame_bgr, brightness)
                if frame_is_design_sized:
                    canvas = lit
                else:
                    fit_d = SceneFit(target_w=DESIGN_W, target_h=DESIGN_H)
                    canvas = fit_d.scale_and_crop(lit)
            # All widget/grid math is in design pixels (DESIGN_W×DESIGN_H). If the base layer is off-size
            # (e.g. a bad master path), resize so overlays are not clipped on the left before scaling to the window.
            ch_can, cw_can = int(canvas.shape[0]), int(canvas.shape[1])
            if cw_can != DESIGN_W or ch_can != DESIGN_H:
                canvas = cv2.resize(
                    canvas,
                    (DESIGN_W, DESIGN_H),
                    interpolation=cv_resize_interp(cw_can, ch_can, DESIGN_W, DESIGN_H),
                )
            if not canvas.flags["C_CONTIGUOUS"]:
                canvas = np.ascontiguousarray(canvas)
            if _maybe_exit_settings_menus_on_idle():
                pass
            if dev_phase == DevPhase.MAIN_SETTINGS and main_settings_widget is not None:
                _sync_preferences_now_playing_progress()
                main_settings_widget.render(canvas)
                dw, dh = display_dims[0], display_dims[1]
                cap_w, cap_h, use_cap = _composite_cap_dims(dw, dh)
                base2 = cv2.resize(
                    canvas,
                    (cap_w, cap_h),
                    interpolation=cv_resize_interp(
                        int(DESIGN_W), int(DESIGN_H), cap_w, cap_h
                    ),
                )
                if use_cap:
                    return _present_frame_to_display(base2, dw, dh)
                return base2
            _set_playback_overlay_clock_saver_volume_flag()
            now_cs = time.monotonic()
            cs = _clock_saver_for_compose(now_cs) and not show_grid
            intro_op = _clock_startup_intro_opacity(now_cs)
            bdim_c = _clock_saver_backdrop_brightness(now_cs)
            if intro_op is not None:
                canvas[:] = 0
            elif bdim_c < 1.0 - 1e-6:
                canvas = (canvas.astype(np.float32) * bdim_c).astype(np.uint8)
            # Layer order: top gradient first, then bottom gradient (in the
            # non-saver branch below), then the mic/EQ visualizer on top of
            # the gradient, then clock saver / clock widget / overlays on
            # top of the visualizer. See the saver branch for the no-gradient
            # variant.
            if intro_op is None and not (_view_one_uses_now_playing_screen() and not cs):
                _blend_top_gradient_design(canvas)
            if not cs and _view_one_uses_now_playing_screen():
                canvas[:] = (0, 0, 0)
                if startup_ph[0] is not None and not _splash_reveal_clock[0]:
                    # Pre-reveal splash underlay stays black.
                    pass
                elif dev_phase == DevPhase.MAIN_SETTINGS and main_settings_widget is not None:
                    _sync_preferences_now_playing_progress()
                    main_settings_widget.render(canvas)
                else:
                    _sync_now_playing_screen_state()
                    if view_circles_widget is not None:
                        view_circles_widget.render(canvas)
            elif cs:
                if alpha_blend_bgra_over_bgr is not None:
                    acc_cs = (
                        tuple(status_bar_widget.accent_bgr)
                        if status_bar_widget is not None
                        else None
                    )
                    _cs_dim_d = _clock_saver_layer_opacity(now_cs)
                    if intro_op is None:
                        _clock_saver_dim_pre_digit_canvas(canvas, _cs_dim_d)
                    _time_op_d = float(intro_op) if intro_op is not None else 1.0
                    _date_op_d = float(intro_op) if intro_op is not None else _cs_dim_d
                    (time_bgra, t_rect), (date_bgra, d_rect) = clock_saver_composite_bgra(
                        shadow_bgr=acc_cs,
                        layer_opacity=_cs_dim_d,
                        time_layer_opacity=_time_op_d,
                        date_layer_opacity=_date_op_d,
                        date_anchor_row=CLOCK_ANCHOR_ROW,
                        date_anchor_col=CLOCK_ANCHOR_COL,
                    )
                    for cs_bgra, (sx, sy, sw, sh) in (
                        (date_bgra, d_rect),
                        (time_bgra, t_rect),
                    ):
                        roi2 = canvas[sy : sy + sh, sx : sx + sw]
                        roi2[:] = alpha_blend_bgra_over_bgr(roi2, cs_bgra)
                    if playback_overlay_widget is not None and (
                        playback_overlay_flags.get("clock_saver_volume_only")
                        or playback_overlay_flags.get("clock_saver_netflix_full_overlay")
                    ):
                        ch, cw = canvas.shape[:2]
                        for p in playback_overlay_widget.design_blits():
                            x, y, w, h = p.x, p.y, p.w, p.h
                            if w < 1 or h < 1:
                                continue
                            x0 = max(0, x)
                            y0 = max(0, y)
                            x1 = min(cw, x + w)
                            y1 = min(ch, y + h)
                            if x0 >= x1 or y0 >= y1:
                                continue
                            sx0 = x0 - x
                            sy0 = y0 - y
                            roi = canvas[y0:y1, x0:x1]
                            src = _clock_saver_dim_overlay_bgra(p.bgra, _cs_dim_d)
                            patch = src[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
                            roi[:] = alpha_blend_bgra_over_bgr(roi, patch)
            else:
                if (
                    playback_lower_gradient_bgra is not None
                    and alpha_blend_bgra_over_bgr is not None
                    and not _vv_is_music()
                ):
                    gx, gy, gw, gh, grad_bgra = playback_lower_gradient_bgra(
                        gradient_bgr=tmdb_tt_gradient_bgr_holder[0]
                    )
                    sub = canvas[gy : gy + gh, gx : gx + gw]
                    sub[:] = alpha_blend_bgra_over_bgr(sub, grad_bgra)
                # viewOne.videoContent_c poster: sits above the top gradient and
                # below the nowPlaying widget (status bar + playback overlay).
                _paste_video_content_c_poster_above_top_gradient(canvas)
                _warm_playback_overlay_blits()
                if clock_widget is not None and _effective_display_view() != DisplayView.FOUR:
                    if _info_cluster_compose_active(now_cs):
                        _blend_info_cluster_into_target(
                            canvas, int(DESIGN_W), int(DESIGN_H), now_cs
                        )
                    else:
                        clock_widget.render(canvas)
                if status_bar_widget is not None:
                    status_bar_widget.render(canvas)
                if playback_overlay_widget is not None and alpha_blend_bgra_over_bgr is not None:
                    playback_overlay_flags["show_paused_row"] = _show_paused_row_overlay()
                    ch, cw = canvas.shape[:2]
                    for p in playback_overlay_widget.design_blits():
                        if (
                            _info_cluster_compose_active(now_cs)
                            and getattr(p, "layer", "") == PATCH_LAYER_RECEIVER_AUDIO
                        ):
                            continue
                        x, y, w, h = p.x, p.y, p.w, p.h
                        if w < 1 or h < 1:
                            continue
                        x0 = max(0, x)
                        y0 = max(0, y)
                        x1 = min(cw, x + w)
                        y1 = min(ch, y + h)
                        if x0 >= x1 or y0 >= y1:
                            continue
                        sx0 = x0 - x
                        sy0 = y0 - y
                        roi = canvas[y0:y1, x0:x1]
                        patch = p.bgra[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
                        roi[:] = alpha_blend_bgra_over_bgr(roi, patch)
                if (
                    dev_phase == DevPhase.OFF
                    and location_toast_patch_bgra is not None
                    and alpha_blend_bgra_over_bgr is not None
                    and (
                        not _info_cluster_compose_active(now_cs)
                        or bool(location_toast_state.get("startup_top_left"))
                    )
                ):
                    now_lt = time.monotonic()
                    ta = _location_toast_alpha(now_lt)
                    if ta > 1e-6:
                        acc = (
                            tuple(status_bar_widget.accent_bgr)
                            if status_bar_widget is not None
                            else None
                        )
                        patch_lt, (lwx, lwy, lww, lwh) = location_toast_patch_bgra(
                            str(location_toast_state["text"]),
                            alpha=ta,
                            shadow_bgr=acc,
                            col_right_offset_cells=0.0,
                            row_offset_cells=0.0,
                            startup_top_left=bool(
                                location_toast_state.get("startup_top_left")
                            ),
                        )
                        if patch_lt is not None:
                            sub = canvas[lwy : lwy + lwh, lwx : lwx + lww]
                            sub[:] = alpha_blend_bgra_over_bgr(sub, patch_lt)
                _logo_w2 = _active_tmdb_logo_widget()
                if _logo_w2 is not None and _effective_display_view() not in (
                    DisplayView.FOUR,
                    DisplayView.TWO,
                    DisplayView.THREE,
                ) and not _view_one_is_pigeon_poster() and not _view_one_uses_now_playing_screen():
                    if tmdb_logo_cover_design_xywh is not None:
                        lx, ly, lw, lh = tmdb_logo_cover_design_xywh
                        _paste_tmdb_logo_uniform_cover_design(
                            canvas, _logo_w2, int(lx), int(ly), int(lw), int(lh)
                        )
                    elif (
                        _effective_display_view() == DisplayView.ONE
                        and active_tmdb_title_key
                    ):
                        _dwx, _dwy, _dww, _dwh = _view_one_video_content_a_tt_contain_rect_design()
                        _paste_tmdb_logo_uniform_cover_design(
                            canvas, _logo_w2, int(_dwx), int(_dwy), int(_dww), int(_dwh)
                        )
                    else:
                        _logo_w2.render(
                            canvas,
                            title_key_str=active_tmdb_title_key,
                            display_title=active_tmdb_display_title,
                        )
            if show_grid:
                ov = build_stage_overlay_source_bgra(_stage_grid_overlay_mode())
                canvas = blend_overlay_bgr(canvas, ov)
            tw, th = display_dims[0], display_dims[1]
            return _present_frame_to_display(canvas, tw, th)

        def _view_four_text_is_placeholder(s: str) -> bool:
            """Hide rows whose text is empty or ends with ``-`` / ``NONE`` (any case)."""
            t = str(s).strip()
            if not t:
                return True
            while t and t[-1] in {"'", '"'}:
                t = t[:-1].rstrip()
            if not t:
                return True
            up = t.upper()
            return (
                up.endswith("NONE")
                or up.endswith("UNKNOWN")
                or t.endswith("-")
                or t.endswith("—")
            )

        def _view_four_has_value(v: object) -> bool:
            """True when a View 4 debug field should be listed (skip None / empty / NONE / -)."""
            if v is None:
                return False
            if isinstance(v, bool):
                return True
            if isinstance(v, str):
                return not _view_four_text_is_placeholder(v)
            if isinstance(v, (list, tuple, set)):
                return any(_view_four_has_value(x) for x in v)
            if isinstance(v, dict):
                return any(_view_four_has_value(x) for x in v.values())
            if isinstance(v, float) and v != v:
                return False
            return True

        def _view_four_display_metadata() -> dict[str, object] | None:
            """last_metadata with disabled METADATA / HDMI sources removed."""
            md = apple_tv_auto_state.get("last_metadata")
            if not isinstance(md, dict):
                return None
            try:
                from pigeon.source_toggles import redact_disabled_source_fields

                redacted = redact_disabled_source_fields(md)
            except Exception:
                redacted = dict(md)
            return redacted if isinstance(redacted, dict) else dict(md)

        def _view_four_metadata_source_on() -> bool:
            try:
                from pigeon.source_toggles import source_enabled

                return bool(source_enabled("metadata"))
            except Exception:
                return True

        def _collect_view_four_ocr_lines(md: dict[str, object]) -> list[str]:
            """HDMI OCR clues for View 4 Title Info; omit empty fields."""
            pairs = (
                ("ocr_status", "ocr.status"),
                ("ocr_title", "ocr.title"),
                ("ocr_lines", "ocr.lines"),
                ("ocr_season", "ocr.season"),
                ("ocr_episode", "ocr.episode"),
                ("ocr_year", "ocr.year"),
                ("ocr_runtime_min", "ocr.runtime_min"),
                ("ocr_extras", "ocr.extras"),
                ("ocr_reason", "ocr.trigger"),
                ("ocr_agrees", "ocr.agrees"),
                ("ocr_at", "ocr.at"),
                ("ocr_capture", "ocr.capture"),
            )
            out: list[str] = []
            for key, label in pairs:
                val: object = md.get(key)
                if key == "ocr_reason":
                    mapped = {
                        "no_metadata": "no_pyatv_title",
                        "watch": "stay_alert",
                        "pause": "pause",
                        "confirm": "confirm",
                    }
                    val = mapped.get(str(val or ""), val)
                if key == "ocr_at" and val is not None:
                    try:
                        val = time.strftime(
                            "%Y-%m-%d %H:%M:%S", time.localtime(float(val))
                        )
                    except (TypeError, ValueError, OSError, OverflowError):
                        pass
                if not _view_four_has_value(val):
                    continue
                if isinstance(val, (list, tuple)):
                    items: list[str] = []
                    junk_fn = None
                    if key == "ocr_lines":
                        try:
                            from pigeon.ocr_clues import looks_like_ocr_junk

                            junk_fn = looks_like_ocr_junk
                        except Exception:
                            junk_fn = None
                    for item in val:
                        text = str(item).strip()
                        if not _view_four_has_value(text):
                            continue
                        if junk_fn is not None and junk_fn(text):
                            continue
                        items.append(text)
                    if not items:
                        continue
                    shown = items[:6]
                    extra_n = len(items) - len(shown)
                    text = ", ".join(shown)
                    if extra_n > 0:
                        text = f"{text} +{extra_n} more"
                    out.append(f"{label}={text}")
                    continue
                out.append(f"{label}={val!r}")
            return out

        def _collect_view_four_raw_title_lines() -> list[tuple[str, bool]]:
            """View 4: streaming label, rawTitle + OCR fields that have a value, last TMDb fetch."""
            rows: list[tuple[str, bool]] = []

            def _ln(s: str) -> None:
                if _view_four_text_is_placeholder(s):
                    return
                rows.append((s, False))

            lm_rt = _view_four_display_metadata()
            metadata_on = _view_four_metadata_source_on()
            if isinstance(lm_rt, dict):
                for ocr_line in _collect_view_four_ocr_lines(lm_rt):
                    _ln(ocr_line)
                try:
                    from pigeon.display_confidence import scores_for_metadata
                    from pigeon.hdmi_ocr import hdmi_capture_available
                    from pigeon.source_toggles import source_enabled

                    clk = apple_tv_playback_clock
                    advancing = bool(clk.get("has_sync") and clk.get("playing"))
                    tmdb_ok = bool(_tmdb_info_current_and_available())

                    scores = scores_for_metadata(
                        lm_rt,
                        position_advancing=advancing,
                        tmdb_matches=tmdb_ok,
                        hdmi_on=bool(source_enabled("hdmi")),
                        hdmi_present=hdmi_capture_available(),
                    )
                    src = str(lm_rt.get("identity_source") or "").strip()
                    if src:
                        _ln(f"identity.source={src!r}")
                    try:
                        from pigeon.title_decision import decision_from_metadata

                        why = decision_from_metadata(lm_rt) or str(
                            apple_tv_auto_state.get("last_title_decision") or ""
                        ).strip()
                        if why:
                            _ln(f"title.decision={why}")
                    except Exception:
                        pass
                    pending = str(lm_rt.get("ocr_pending_title") or "").strip()
                    if pending:
                        _ln(f"identity.pending={pending!r}")
                        hits = lm_rt.get("ocr_pending_hits")
                        if hits is not None:
                            _ln(f"identity.pending_hits={hits!r}")
                    for key in ("identity", "position", "art", "app"):
                        val = scores.get(key)
                        if val is None:
                            continue
                        _ln(f"confidence.{key}={float(val):.2f}")
                    if scores.get("ocr_charge"):
                        _ln("ocr.charge=true")
                except Exception:
                    pass
            if metadata_on:
                _svc_label = str(streaming_badge_state.get("label") or "").strip()
                _svc_app = (
                    str(lm_rt.get("app_name") or "").strip()
                    if isinstance(lm_rt, dict)
                    else ""
                )
                if _svc_label:
                    _ln(f"streamingService={_svc_label!r}")
                elif _svc_app:
                    _ln(f"streamingService={_svc_app!r}")

            if not isinstance(lm_rt, dict):
                _ln("rawTitle: (no last_metadata dict)")
            else:
                if metadata_on:
                    try:
                        from pigeon.raw_title import raw_title_from_metadata_dict

                        rt = raw_title_from_metadata_dict(lm_rt)
                        if _view_four_has_value(rt.source):
                            _ln(f"rawTitle.source={rt.source!r}")
                        for fn in (
                            "raw_title",
                            "raw_series_name",
                            "raw_artist",
                            "raw_album",
                            "raw_episode_title",
                            "raw_query",
                            "season_index",
                            "episode_index",
                            "layer_series_title",
                            "layer_series_number",
                            "layer_episode_number",
                            "layer_episode_title",
                            "media_type_label",
                        ):
                            val = getattr(rt, fn, None)
                            if _view_four_has_value(val):
                                _ln(f"rawTitle.{fn}={val!r}")
                        if rt.notes:
                            _ln(f"rawTitle.notes={rt.notes!r}")
                        sig = rt.training_signature_normalized()
                        if sig:
                            _ln(f"rawTitle.training_signature_normalized={sig!r}")
                    except Exception as e:
                        _ln(f"rawTitle err={e}")
            if metadata_on and isinstance(lm_rt, dict):
                _pp = str(lm_rt.get("prefer_pyatv_media") or "").strip().lower()
                if _pp in ("auto", "tv", "movie"):
                    _ln(f"metadata.prefer_pyatv_media={_pp!r}")
                _ip = str(lm_rt.get("inferred_prefer") or "").strip().lower()
                if _ip in ("auto", "tv", "movie"):
                    _ln(f"metadata.prefer_tmdb={_ip!r}")
            ocr_guess = (
                str(lm_rt.get("ocr_title") or "").strip().casefold()
                if isinstance(lm_rt, dict)
                else ""
            )

            def _tmdb_row_allowed(q: object) -> bool:
                if metadata_on:
                    return True
                t = str(q or "").strip().casefold()
                return bool(t and ocr_guess and (t == ocr_guess or t in ocr_guess or ocr_guess in t))

            _ti = apple_tv_auto_state.get("last_tmdb_fetch_input")
            _tr = apple_tv_auto_state.get("last_tmdb_fetch_refined")
            _tp = apple_tv_auto_state.get("last_tmdb_fetch_prefer")
            if _ti is not None and str(_ti).strip() and _tmdb_row_allowed(_ti):
                _ln(f"tmdbFetch.input_query={str(_ti)!r}")
            if _tr is not None and str(_tr).strip() and _tmdb_row_allowed(_tr):
                _ln(f"tmdbFetch.refined_query={str(_tr)!r}")
            if _tp is not None and str(_tp).strip() and (_tmdb_row_allowed(_ti) or _tmdb_row_allowed(_tr)):
                _ln(f"tmdbFetch.prefer={str(_tp)!r}")
            rx_dbg = receiver_telnet_debug_holder[0] if receiver_telnet_debug_holder else {}
            if isinstance(rx_dbg, dict) and rx_dbg:
                _ln("denonTelnet (debug):")
                for key in ("SI", "MS", "DC", "PS_MULTEQ", "PS_DYNEQ", "PS_DYNVOL", "PS_REFLEV"):
                    val = str(rx_dbg.get(key) or "").strip()
                    if val:
                        _ln(f"  {key}={val!r}")
                raw_blob = str(rx_dbg.get("_raw") or "").strip()
                if raw_blob:
                    _ln("  _raw=(see receiver_denon_telnet dump)")
            return rows

        def _metadata_debug_provider() -> dict[str, object]:
            """Rows for the [4] metadata inspector (player / hdmi / pigeon pages)."""
            from pigeon.hdmi_ocr import hdmi_capture_available
            from pigeon.source_toggles import source_enabled

            lm = _view_four_display_metadata() or {}
            metadata_on = _view_four_metadata_source_on()
            try:
                player_active = bool(metadata_on and _content_indicator_ok())
            except Exception:
                player_active = False
            try:
                hdmi_active = bool(source_enabled("hdmi")) and bool(
                    hdmi_capture_available()
                )
            except Exception:
                hdmi_active = False

            rt = None
            if metadata_on and lm:
                try:
                    from pigeon.raw_title import raw_title_from_metadata_dict

                    rt = raw_title_from_metadata_dict(lm)
                except Exception:
                    rt = None

            def _rt(field: str) -> str:
                return str(getattr(rt, field, "") or "").strip() if rt is not None else ""

            # --- player: network metadata exactly as the box broadcasts it ---
            p_title = str(lm.get("title") or "").strip() if metadata_on else ""
            p_series = str(lm.get("series_name") or "").strip() if metadata_on else ""
            is_tv = bool(p_series) or "tv" in _rt("media_type_label").casefold()
            if is_tv and not p_series:
                # layer_series_title falls back to the plain title for movies,
                # so only trust it once we know this is episodic content.
                p_series = _rt("layer_series_title")
            p_episode = (
                (_rt("raw_episode_title") or _rt("layer_episode_title") or p_title)
                if is_tv
                else p_title
            )
            player_rows = {
                "service": str(lm.get("app_name") or "").strip() if metadata_on else "",
                "series": p_series,
                "title": p_title,
                "episode": p_episode,
            }

            # --- hdmi: whatever OCR pulled off the video feed. No capture card
            # connected → red LED and no metadata at all (stale OCR fields may
            # still sit in last_metadata after an unplug).
            h_episode = ""
            h_title = ""
            h_year_s = ""
            if hdmi_active:
                h_season = lm.get("ocr_season")
                h_episode_n = lm.get("ocr_episode")
                try:
                    if h_season is not None and h_episode_n is not None:
                        h_episode = f"S{int(h_season)} E{int(h_episode_n)}"
                    elif h_episode_n is not None:
                        h_episode = f"E{int(h_episode_n)}"
                except (TypeError, ValueError):
                    h_episode = ""
                h_title = str(lm.get("ocr_title") or "").strip()
                h_year = lm.get("ocr_year")
                h_year_s = str(int(h_year)) if isinstance(h_year, (int, float)) else ""
            hdmi_rows = {
                "service": "",
                "series": "",
                "title": h_title,
                "episode": h_episode,
                "year": h_year_s,
                "ocr_lines": list(lm.get("ocr_lines") or []) if hdmi_active else [],
                "ocr_status": str(lm.get("ocr_status") or "").strip() if hdmi_active else "",
                "ocr_reason": str(lm.get("ocr_reason") or "").strip() if hdmi_active else "",
            }

            # --- pigeon: the final verdict (search terms + confidence) ---
            refined = str(
                apple_tv_auto_state.get("last_tmdb_fetch_refined")
                or apple_tv_auto_state.get("last_tmdb_fetch_input")
                or ""
            ).strip()
            g_title = refined or _rt("raw_query") or p_title or hdmi_rows["title"]
            g_year = ""
            try:
                from pigeon.tmdb_poster import split_query_and_year

                cleaned, year = split_query_and_year(g_title)
                if cleaned:
                    g_title = cleaned.strip()
                if year:
                    g_year = str(year)
            except Exception:
                pass
            if not g_year:
                g_year = hdmi_rows["year"]
            g_series = p_series
            g_episode = ""
            se_i, ep_i = getattr(rt, "season_index", None), getattr(rt, "episode_index", None)
            if is_tv:
                g_episode = _rt("layer_episode_title") or _rt("raw_episode_title")
                if not g_episode and se_i is not None and ep_i is not None:
                    g_episode = f"S{se_i} E{ep_i}"
                g_episode = g_episode or h_episode
            g_episode = g_episode or g_title
            svc_label = str(streaming_badge_state.get("label") or "").strip()
            pigeon_rows: dict[str, object] = {
                "service": svc_label or player_rows["service"],
                "series": g_series,
                "title": g_title,
                "episode": g_episode,
                "year": g_year,
            }
            try:
                from pigeon.title_decision import decision_from_metadata

                why = decision_from_metadata(lm) or str(
                    apple_tv_auto_state.get("last_title_decision") or ""
                ).strip()
                if why:
                    pigeon_rows["decision"] = why
            except Exception:
                pass
            try:
                from pigeon.display_confidence import scores_for_metadata

                clk = apple_tv_playback_clock
                advancing = bool(clk.get("has_sync") and clk.get("playing"))
                scores = scores_for_metadata(
                    lm,
                    position_advancing=advancing,
                    tmdb_matches=bool(_tmdb_info_current_and_available()),
                    hdmi_on=bool(source_enabled("hdmi")),
                    hdmi_present=hdmi_capture_available(),
                )
                pigeon_rows["confidence"] = {
                    "service": scores.get("app"),
                    "series": scores.get("identity"),
                    "title": scores.get("identity"),
                    "episode": scores.get("position"),
                    "year": scores.get("art"),
                }
            except Exception:
                pigeon_rows["confidence"] = {}

            return {
                "player": player_rows,
                "hdmi": hdmi_rows,
                "pigeon": pigeon_rows,
                "player_active": player_active,
                "hdmi_active": hdmi_active,
            }

        try:
            from pigeon.widgets.metadata_debug import set_metadata_debug_data_provider

            set_metadata_debug_data_provider(_metadata_debug_provider)
        except Exception:
            pass

        def _collect_view_four_source_lines() -> list[tuple[str, bool]]:
            """View 4 subview: best-effort file/stream stats from poll metadata + receiver text hints."""
            from math import gcd

            rows: list[tuple[str, bool]] = []

            def _ln(s: str, bold: bool = False) -> None:
                if _view_four_text_is_placeholder(s):
                    return
                rows.append((s, bold))

            def _md_pick(md: dict[str, object], *keys: str) -> str | None:
                for k in keys:
                    if k not in md:
                        continue
                    v = md[k]
                    if v is None:
                        continue
                    if isinstance(v, (int, float)):
                        if isinstance(v, float) and v != v:
                            continue
                        t = str(int(v)) if float(v) == int(v) else str(v)
                    else:
                        t = str(v).strip()
                    if t:
                        return t
                return None

            def _aspect_from_wh(w_s: str | None, h_s: str | None) -> str | None:
                if not w_s or not h_s:
                    return None
                try:
                    wi = int(round(float(w_s)))
                    hi = int(round(float(h_s)))
                except (TypeError, ValueError):
                    return None
                if wi <= 0 or hi <= 0:
                    return None
                g = gcd(wi, hi)
                return f"{wi // g}:{hi // g}"

            md = _view_four_display_metadata()
            inc = str(receiver_overlay_state.get("incoming") or "").strip()
            cfg = str(receiver_overlay_state.get("config") or "").strip()
            rx_blob = f"{inc} {cfg}".strip()

            if not isinstance(md, dict):
                _ln("(no last_metadata dict)", False)
                return rows

            w = _md_pick(md, "video_width", "width", "source_width", "ImageWidth", "image_width")
            h = _md_pick(md, "video_height", "height", "source_height", "ImageHeight", "image_height")
            res_one = _md_pick(
                md,
                "video_resolution",
                "source_resolution",
                "resolution",
                "VideoResolution",
            )

            def _ln_val(label: str, value: str | None) -> None:
                if _view_four_has_value(value):
                    _ln(f"{label}: {value}", False)

            if res_one:
                _ln_val("Video source resolution", res_one)
            elif w and h:
                _ln_val("Video source resolution", f"{w}×{h}")
            elif w or h:
                _ln_val("Video source resolution", f"{w or '?'}×{h or '?'}")

            ar = _md_pick(md, "aspect_ratio", "video_aspect_ratio", "AspectRatio", "DisplayAspectRatio")
            if not ar:
                ar = _aspect_from_wh(w, h)
            _ln_val("Video source aspect ratio", ar)
            _ln_val(
                "Video source color space",
                _md_pick(
                    md,
                    "color_space",
                    "color_primaries",
                    "VideoColorSpace",
                    "ColorSpace",
                    "colour_space",
                ),
            )
            _ln_val(
                "Video source frame rate",
                _md_pick(
                    md,
                    "frame_rate",
                    "framerate",
                    "fps",
                    "video_frame_rate",
                    "FrameRate",
                ),
            )
            _ln_val(
                "Video source bit depth",
                _md_pick(md, "video_bit_depth", "bit_depth", "bits_per_pixel", "VideoBitDepth"),
            )
            _ln_val(
                "Video source codec",
                _md_pick(md, "video_codec", "codec", "video_format", "VideoCodec", "format"),
            )
            _ln_val(
                "Video source wrapper",
                _md_pick(md, "container", "wrapper", "mime_type", "MimeType", "file_extension"),
            )
            _ln_val(
                "Audio source wrapper",
                _md_pick(md, "audio_container", "audio_wrapper", "AudioContainer"),
            )
            _ln_val(
                "Audio source bit depth",
                _md_pick(md, "audio_bit_depth", "source_audio_bit_depth", "AudioBitDepth"),
            )
            _ln_val(
                "Audio source bit rate",
                _md_pick(md, "audio_bit_rate", "source_audio_bit_rate", "AudioBitrate", "audio_bitrate"),
            )
            _ln_val(
                "Audio source codec",
                _md_pick(md, "audio_codec", "audio_format", "AudioCodec", "AudioFormat"),
            )

            def _lpcm_vs_bitstream(blob: str, md2: dict[str, object]) -> str:
                ac = str(md2.get("audio_codec") or md2.get("audio_format") or "").lower()
                blob_l = blob.lower()
                joined = f"{ac} {blob_l}"
                if "pcm" in joined or "lpcm" in joined or "linear pcm" in joined:
                    return "LPCM/PCM (from metadata/receiver text)"
                if any(
                    x in joined
                    for x in (
                        "dolby",
                        "dts",
                        "truehd",
                        "true-hd",
                        "eac3",
                        "e-ac-3",
                        "atmos",
                        "bitstream",
                        "dd+",
                        "dtsx",
                    )
                ):
                    return "Compressed / bitstream (from metadata/receiver text)"
                if blob:
                    return "Unknown (see receiver lines below)"
                return "—"

            _lpcm = _lpcm_vs_bitstream(rx_blob, md)
            if _view_four_has_value(_lpcm):
                _ln(f"Audio source LPCM vs bitstream: {_lpcm}", False)

            proto = _md_pick(md, "protocol")
            if proto:
                _ln(f"Poll protocol: {proto}", False)
            if _view_four_metadata_source_on():
                appn = str(md.get("app_name") or "").strip()
                appid = str(md.get("app_id") or "").strip()
                if appn or appid:
                    _ln(f"App: {appn!r} id={appid!r}", False)

            if inc:
                _ln(f"Receiver incoming (raw): {inc}", False)
            if cfg:
                _ln(f"Receiver config (raw): {cfg}", False)

            known = {
                "query",
                "title",
                "artist",
                "series_name",
                "album",
                "media_type",
                "total_time",
                "position",
                "device_state",
                "inferred_prefer",
                "prefer_pyatv_media",
                "content_key",
                "app_name",
                "app_id",
                "volume_percent",
                "prefer",
                "ocr_title",
                "ocr_lines",
                "ocr_season",
                "ocr_episode",
                "ocr_year",
                "ocr_runtime_min",
                "ocr_extras",
                "ocr_reason",
                "ocr_agrees",
                "ocr_at",
                "ocr_status",
                "ocr_capture",
            }
            extra_keys = [
                k
                for k in sorted(md.keys())
                if k not in known
                and not str(k).startswith("_")
                and _view_four_has_value(md.get(k))
            ]
            if extra_keys and _view_four_metadata_source_on():
                _ln("other metadata keys", False)
                for k in extra_keys[:36]:
                    try:
                        vv = md[k]
                        rep = repr(vv)
                        if len(rep) > 140:
                            rep = rep[:137] + "..."
                    except Exception:
                        rep = "?"
                    _ln(f"  {k}={rep}", False)
                if len(extra_keys) > 36:
                    _ln(f"  … ({len(extra_keys) - 36} more keys)", False)
            return rows

        def _collect_view_four_playback_lines() -> list[tuple[str, bool]]:
            rows: list[tuple[str, bool]] = []

            def _ln(s: str, bold: bool = False) -> None:
                if _view_four_text_is_placeholder(s):
                    return
                rows.append((s, bold))

            md_raw = _view_four_display_metadata()
            md = md_raw if isinstance(md_raw, dict) else None
            inc = str(receiver_overlay_state.get("incoming") or "").strip()
            cfg = str(receiver_overlay_state.get("config") or "").strip()
            vol_line = str(receiver_overlay_state.get("volume") or "").strip()
            blob = f"{inc} {cfg}".lower()

            def _channels_guess(s: str) -> str:
                if "7.1" in s or "7_1" in s:
                    return "7.1 (hint)"
                if "5.1" in s or "5_1" in s:
                    return "5.1 (hint)"
                if "2.0" in s or "stereo" in s or "2ch" in s:
                    return "2.0 / stereo (hint)"
                if "atmos" in s:
                    return "Atmos (hint)"
                return "—"

            fmt_parts: list[str] = []
            if md:
                mt = str(md.get("media_type") or "").strip()
                if mt:
                    fmt_parts.append(mt)
            if inc or cfg:
                fmt_parts.append(f"receiver: {(inc + ' ' + cfg).strip()[:120]}")
            if fmt_parts:
                _ln(f"Audio playback format: {' | '.join(fmt_parts)}", False)

            if md and md.get("audio_playback_bit_rate") is not None:
                _ln(f"Audio playback bit rate: {str(md.get('audio_playback_bit_rate')).strip()}", False)
            if md and md.get("audio_playback_bit_depth") is not None:
                _ln(f"Audio playback bit depth: {str(md.get('audio_playback_bit_depth')).strip()}", False)
            ch = _channels_guess(blob)
            if _view_four_has_value(ch):
                _ln(f"Audio playback available channels: {ch}", False)
                _ln(f"Audio playback active channels: {ch}", False)

            if vol_line:
                scale = "dB scale" if ("db" in vol_line.lower() or re.search(r"-?\d+\.\d+\s*d", vol_line.lower())) else (
                    "0–100" if re.search(r"\b\d{1,3}\b", vol_line) and "%" not in vol_line and "db" not in vol_line.lower() else "receiver raw"
                )
                _ln(f"Audio playback volume: {vol_line}", False)
                _ln(f"Audio playback volume scale: {scale}", False)
            elif md and md.get("volume_percent") is not None:
                try:
                    vp = int(max(0, min(100, round(float(md["volume_percent"])))))
                    _ln(f"Audio playback volume: {vp}", False)
                    _ln("Audio playback volume scale: Apple TV 0–100", False)
                except (TypeError, ValueError):
                    pass

            dw, dh = int(display_dims[0]), int(display_dims[1])
            if dw > 0 and dh > 0:
                _ln(f"Video playback resolution (window): {dw}×{dh}", False)

            cap_fps = None
            try:
                if cap is not None and cap.isOpened():
                    cf = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                    if cf > 1.0:
                        cap_fps = cf
            except Exception:
                cap_fps = None
            if cap_fps is not None:
                _ln(f"Video capture nominal FPS: {cap_fps:.3g}", False)

            ui_hz = 1000.0 / float(frame_interval_ms) if frame_interval_ms else 0.0
            if ui_hz > 0:
                _ln(f"UI composite cadence: ~{ui_hz:.2f} Hz (frame_interval_ms={frame_interval_ms})", False)

            if md:
                ds = str(md.get("device_state") or "").strip()
                pos = md.get("position")
                tot = md.get("total_time")
                if ds:
                    _ln(f"Device state: {ds}", False)
                if _view_four_has_value(pos) or _view_four_has_value(tot):
                    _ln(f"Position / duration: {pos!r} / {tot!r}", False)
            return rows

        def _blend_view_four_debug(bgr: np.ndarray) -> np.ndarray:
            if _effective_display_view() != DisplayView.FOUR:
                return bgr
            out = bgr.copy()
            font = cv2.FONT_HERSHEY_SIMPLEX
            mx = 10
            my_top = 12
            my_bot = 10
            H, W = int(out.shape[0]), int(out.shape[1])
            max_w = max(24, W - 2 * mx)
            sub_i = max(0, min(2, int(view_four_subview_holder[0])))
            sub_titles = ("Title Info", "Source Info", "Playback Info")
            if sub_i == 0:
                raw_debug_lines = [(f"View 4 — {sub_titles[sub_i]}", True)] + _collect_view_four_raw_title_lines()
            elif sub_i == 1:
                raw_debug_lines = [(f"View 4 — {sub_titles[sub_i]}", True)] + _collect_view_four_source_lines()
            else:
                raw_debug_lines = [(f"View 4 — {sub_titles[sub_i]}", True)] + _collect_view_four_playback_lines()
            _any_bold = bool(raw_debug_lines)
            rows = [
                (str(raw).strip(), is_bold)
                for raw, is_bold in raw_debug_lines
                if str(raw).strip()
            ]
            if not rows:
                rows = [("(no rawTitle lines yet)", False)]

            def _vf_thick(sc: float) -> int:
                return 2 if sc >= 0.48 else 1

            def _vf_metrics(sc: float) -> tuple[int, int, int, int]:
                thick_n = _vf_thick(sc)
                thick_b = max(thick_n + 2, 3) if _any_bold else thick_n
                (_rw, th), bl = cv2.getTextSize("|pqgy", font, sc, thick_b)
                line_step = max(th + 8, int(th + bl * 0.5) + 6)
                return thick_n, thick_b, th, line_step

            def _vf_row_width(text: str, sc: float, is_bold: bool) -> int:
                thick_n, thick_b, _th, _ls = _vf_metrics(sc)
                return int(cv2.getTextSize(text, font, sc, thick_b if is_bold else thick_n)[0][0])

            def _vf_wrap(text: str, sc: float, is_bold: bool) -> list[str]:
                """Keep short fields on one line; wrap only when the row is too wide."""
                if _vf_row_width(text, sc, is_bold) <= max_w:
                    return [text]
                parts = text.split(" ")
                lines: list[str] = []
                cur = ""

                def _flush() -> None:
                    nonlocal cur
                    if cur:
                        lines.append(cur)
                        cur = ""

                def _append_token(token: str) -> None:
                    nonlocal cur
                    trial = token if not cur else f"{cur} {token}"
                    if _vf_row_width(trial, sc, is_bold) <= max_w:
                        cur = trial
                        return
                    _flush()
                    if _vf_row_width(token, sc, is_bold) <= max_w:
                        cur = token
                        return
                    chunk = ""
                    for ch in token:
                        next_chunk = chunk + ch
                        if chunk and _vf_row_width(next_chunk, sc, is_bold) > max_w:
                            lines.append(chunk)
                            chunk = ch
                        else:
                            chunk = next_chunk
                    cur = chunk

                for part in parts:
                    _append_token(part)
                _flush()
                return lines or [text]

            # One readable size for every View 4 row. Long values wrap.
            sc = 0.64
            if H < 800:
                sc = 0.56
            elif H > 1400:
                sc = 0.72

            thick_n, _thick_b, th, line_step = _vf_metrics(sc)
            y = my_top + th
            color_dim = (220, 228, 238)
            color_bold = (255, 255, 255)
            y_limit = H - my_bot
            for raw, is_bold in rows:
                t_draw = max(thick_n + 2, 3) if is_bold else thick_n
                c = color_bold if is_bold else color_dim
                for piece in raw.splitlines() or [raw]:
                    piece = piece.rstrip()
                    if not piece:
                        continue
                    for row in _vf_wrap(piece, sc, is_bold):
                        if y > y_limit:
                            return out
                        cv2.putText(out, row, (mx, y), font, sc, c, t_draw, cv2.LINE_AA)
                        y += line_step
            return out

        def _compose_shown_frame(frame_bgr: np.ndarray | None, brightness: float) -> np.ndarray:
            if (
                _PIGEON_EXT
                and view_circles_widget is not None
                and _effective_display_view() == DisplayView.ONE
            ):
                return compose_display_fast_no_grid(
                    frame_bgr,
                    brightness,
                    frame_is_display_sized=bool(
                        frame_bgr is not None
                        and frame_bgr.size > 0
                        and int(frame_bgr.shape[0]) == int(DESIGN_H)
                        and int(frame_bgr.shape[1]) == int(DESIGN_W)
                    ),
                )

            def _view_one_dark_accent_bg_bgr() -> tuple[int, int, int]:
                """Darker variant of the current accent color for viewOne video a/c backgrounds.

                Stays black until ``pigeonTMDB_BD`` is ready (so the accent is actually
                sampled from a real backdrop, not the orange fallback).
                """
                if not _vv_has_tmdb_bd():
                    return (0, 0, 0)
                if status_bar_widget is None:
                    return (0, 0, 0)
                base = tuple(int(v) & 255 for v in status_bar_widget.accent_bgr)
                # Keep the hue but darken enough to sit behind TT/poster overlays.
                darken = 0.42
                return (
                    int(round(base[0] * darken)),
                    int(round(base[1] * darken)),
                    int(round(base[2] * darken)),
                )

            if _PIGEON_EXT and _effective_display_view() == DisplayView.FOUR:
                return _black_screen_bgr()
            if (
                _PIGEON_EXT
                and _effective_display_view() != DisplayView.ONE
                and _view_one_is_pigeon_poster()
                and not _vv_is_music()
                and _vv_has_content_title()
            ):
                # viewOne.videoContent_c: black base + active TMDb poster only
                # (no pigeonTMDB_TT / no pigeonTMDB_BD), with the same chrome stack
                # as the other View One layouts. Poster occupies y=[0, top(row 7)].
                sb, sg, sr = _view_one_dark_accent_bg_bgr()
                black = np.empty((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
                black[:] = (sb, sg, sr)
                return compose_display_from_source(
                    black,
                    brightness,
                    show_grid=_design_grid_overlay_active(),
                    frame_is_design_sized=True,
                )
            if (
                _PIGEON_EXT
                and _effective_display_view() != DisplayView.ONE
                and _view_one_variant_uses_simple_path()
                and not _backdrop_active_for_view()
            ):
                sb, sg, sr = _view_one_dark_accent_bg_bgr()
                black = np.empty((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
                black[:] = (sb, sg, sr)
                sub2_logo_rect = _view_one_video_content_a_tt_contain_rect_design()
                # MediaType.Music override (viewOne.01): TMDb doesn't index music
                # tracks, so no pigeonTMDB_TT is available. Substitute a two-line
                # text patch (track title large; "Artist - Album" smaller beneath)
                # inside the same rect pigeonTMDB_TT would occupy. Short-circuits
                # the V# resolver dispatch below so Music content consistently
                # renders text regardless of which fallback variant would otherwise
                # apply. The single-small-line case in ``render_ui_music_text_patch_bgra``
                # gives the title ~76% of the box height and the subtitle the rest.
                if _vv_is_music() and render_ui_music_text_patch_bgra is not None:
                    _m_title, _m_subtitle = _vv_music_text_lines()
                    if _m_title or _m_subtitle:
                        _music_bgra = render_ui_music_text_patch_bgra(
                            _m_title,
                            _m_subtitle,
                            "",
                            int(sub2_logo_rect[2]),
                            int(sub2_logo_rect[3]),
                        )
                        if _music_bgra is not None:
                            _paste_bgra_contain_on_design(
                                black, _music_bgra, sub2_logo_rect
                            )
                            return compose_display_from_source(
                                black,
                                brightness,
                                show_grid=_design_grid_overlay_active(),
                                frame_is_design_sized=True,
                            )
                # Variant-aware TT-slot content. V01/V04 draw the real pigeonTMDB_TT via
                # compose_display_from_source (post–v0.6.14 swap: V01 is the TT-only default,
                # V04 is the BD-missing alternate); V06/.07/.08/.09 substitute a generated
                # patch (title text / appLogo / app name / pigeonTempLogo).
                _vv_simple = _current_view_one_variant()
                _vv_use_default_tt = ViewOneVariant is None or _vv_simple in (
                    ViewOneVariant.V01,
                    ViewOneVariant.V04,
                )
                if not _vv_use_default_tt:
                    _override_bgra = None
                    if _vv_simple == ViewOneVariant.V06 and render_ui_text_patch_bgra is not None:
                        _override_bgra = render_ui_text_patch_bgra(
                            _playback_display_title(),
                            int(sub2_logo_rect[2]),
                            int(sub2_logo_rect[3]),
                        )
                    elif _vv_simple == ViewOneVariant.V07:
                        _override_bgra = _resolve_streaming_app_logo_bgra()
                    elif _vv_simple == ViewOneVariant.V08 and render_ui_text_patch_bgra is not None:
                        _override_bgra = render_ui_text_patch_bgra(
                            _current_app_display_name(),
                            int(sub2_logo_rect[2]),
                            int(sub2_logo_rect[3]),
                        )
                    elif _vv_simple == ViewOneVariant.V09 and load_pigeon_temp_logo_bgra is not None:
                        # Keep startup/no-content logo treatment centered, matching other app-logo
                        # presentations, while preserving the same max slot size.
                        _rw = max(1, int(sub2_logo_rect[2]))
                        _rh = max(1, int(sub2_logo_rect[3]))
                        _rx = max(0, (int(DESIGN_W) - _rw) // 2)
                        _ry = max(0, (int(DESIGN_H) - _rh) // 2)
                        sub2_logo_rect = (_rx, _ry, _rw, _rh)
                        _override_bgra = load_pigeon_temp_logo_bgra(
                            Path(_PROJECT_DIR) / "pigeonAssets"
                        )
                        if _override_bgra is None:
                            print(
                                "pigeon: pigeonAssets/App logos/AppLogo_Pigeon.png not found — "
                                "viewOne.noContent will render black only.",
                                file=sys.stderr,
                            )
                        else:
                            # viewOne.noContent: Pigeon logo at 30% opacity.
                            _override_bgra = _override_bgra.copy()
                            _override_bgra[..., 3] = (
                                _override_bgra[..., 3].astype(np.float32) * 0.30
                            ).clip(0, 255).astype(np.uint8)
                    _v07_skip_tt_for_clock_saver = (
                        _vv_simple == ViewOneVariant.V07
                        and _clock_saver_for_compose(time.monotonic())
                    )
                    if not _v07_skip_tt_for_clock_saver:
                        _paste_bgra_contain_on_design(
                            black, _override_bgra, sub2_logo_rect
                        )
                    return compose_display_from_source(
                        black,
                        brightness,
                        show_grid=_design_grid_overlay_active(),
                        frame_is_design_sized=True,
                    )
                return compose_display_from_source(
                    black,
                    brightness,
                    show_grid=_design_grid_overlay_active(),
                    frame_is_design_sized=True,
                    tmdb_logo_cover_design_xywh=sub2_logo_rect,
                )
            # View 2 backdrop + visualizer-only is handled in ``compose_display_fast_no_grid``.
            # View 1 pigeonFull also uses that backdrop fast path.
            if (
                _backdrop_active_for_view()
                and backdrop_master_bgr is not None
                and _effective_display_view() != DisplayView.TWO
                and not _view_one_variant_uses_full_path()
            ):
                from pigeon.image_ui_protocol import build_backdrop_design_layer_bgr

                if not _PIGEON_EXT:
                    # Legacy path: use backdrop-only display if extension isn't available.
                    bd = build_backdrop_design_layer_bgr(
                        backdrop_master_bgr,
                        app_logo_letterbox_fit=backdrop_app_logo_letterbox_fit,
                        app_logo_clock_saver_style=_app_logo_clock_saver_style_now(),
                    )
                    return compose_display_from_source(bd, brightness, show_grid=False, frame_is_design_sized=True)
                bd = build_backdrop_design_layer_bgr(
                    backdrop_master_bgr,
                    app_logo_letterbox_fit=backdrop_app_logo_letterbox_fit,
                    app_logo_clock_saver_style=_app_logo_clock_saver_style_now(),
                )
                return compose_display_from_source(
                    bd,
                    brightness,
                    show_grid=_design_grid_overlay_active(),
                    frame_is_design_sized=True,
                )

            if not _PIGEON_EXT:
                if frame_bgr is None or frame_bgr.size == 0:
                    return _black_screen_bgr()
                lit = _apply_brightness(frame_bgr, brightness)
                dw, dh = display_dims[0], display_dims[1]
                cw, ch, cap_down = _composite_cap_dims(dw, dh)
                small = SceneFit(target_w=cw, target_h=ch).scale_and_crop(lit)
                if cap_down:
                    return _present_frame_to_display(small, dw, dh)
                return small
            if _PIGEON_EXT and _design_grid_overlay_active():
                return compose_display_from_source(frame_bgr, brightness, show_grid=True)
            return compose_display_fast_no_grid(frame_bgr, brightness)

        if _PIGEON_EXT:
            _warm_status_bar_blits()
            _warm_playback_overlay_blits()

        # Display off: no landing art (black / stage composite only in render_once).
        if not scene_enabled:
            last_frame = None
            scaled_display = None
            scaled_version = 0

        command_entry_visible = False
        command_bar = tk.Frame(shell, bg="#1a1a1e", height=30)
        command_bar.pack_propagate(False)
        command_entry = tk.Entry(
            command_bar,
            bg="#2d2d32",
            fg="#f0f0f0",
            insertbackground="#f0f0f0",
            relief=tk.FLAT,
            highlightthickness=1,
            highlightbackground="#0a84ff",
            font=("Helvetica", 12),
        )
        command_entry.pack(fill=tk.BOTH, expand=True, padx=4, pady=3)

        def _ui_scale() -> float:
            dw, dh = display_dims[0], display_dims[1]
            return max(0.45, min(min(dw / float(WINDOW_W), dh / float(WINDOW_H)), 5.0))

        def _layout_chrome() -> None:
            dw, dh = display_dims[0], display_dims[1]
            _ui_scale()
            if command_entry_visible:
                place_command_bar()

        def place_command_bar() -> None:
            dw, dh = display_dims[0], display_dims[1]
            ui = _ui_scale()
            bar_h = max(24, int(32 * ui))
            yb = dh - bar_h - int(4 * ui)
            command_bar.place(x=0, y=yb, width=dw, height=bar_h)

        def hide_command_entry(_event=None) -> None:
            nonlocal command_entry_visible
            command_entry_visible = False
            command_bar.place_forget()
            try:
                label.focus_set()
            except tk.TclError:
                pass

        def _apply_dev_phase_widgets() -> None:
            # settings_main composites onto the video label; never pack the Tk form.
            try:
                settings_frame.pack_forget()
            except tk.TclError:
                pass
            label.pack(fill=tk.BOTH, expand=True)

        def _current_location_display_name() -> str:
            cid = (read_current_location_id() or "").strip()
            for L in read_all_locations_v2():
                if str(L.get("id") or "") == cid:
                    n = str(L.get("name") or "").strip()
                    return n or "Room"
            return "Location"

        def _location_toast_alpha(now: float) -> float:
            st = location_toast_state
            if not st["active"]:
                return 0.0
            hold = float(st.get("hold_full_s", LOCATION_TOAST_FULL_S))
            elapsed = now - float(st["t0"])
            if elapsed < hold:
                return 1.0
            if elapsed < hold + LOCATION_TOAST_FADE_S:
                return max(0.0, 1.0 - (elapsed - hold) / LOCATION_TOAST_FADE_S)
            st["active"] = False
            return 0.0

        def _start_location_toast(*, startup: bool = False) -> None:
            nonlocal skip_cache
            if not _PIGEON_EXT:
                return
            st = location_toast_state
            st["text"] = _current_location_display_name()
            st["active"] = True
            st["t0"] = time.monotonic()
            st["startup_top_left"] = bool(startup)
            st["hold_full_s"] = 15.0 if startup else 5.0
            skip_cache = None

        def sync_developer_chrome() -> None:
            was_phase = prev_dev_phase_for_location_toast[0]
            _apply_dev_phase_widgets()
            _layout_chrome()
            if dev_phase == DevPhase.GRID:
                root.title(f"Pigeon {version_string()} — Developer mode (grid)")
                label.configure(
                    highlightthickness=3,
                    highlightbackground="#0a84ff",
                    highlightcolor="#0a84ff",
                )
            elif dev_phase == DevPhase.MAIN_SETTINGS:
                root.title(f"Pigeon {version_string()} — settings")
                try:
                    label.configure(highlightthickness=0)
                except tk.TclError:
                    pass
            else:
                root.title("")
                label.configure(highlightthickness=0)
                hide_command_entry()
            _settings_unbind_wheel_globals()
            if command_entry_visible:
                place_command_bar()
                command_bar.lift()
            if _PIGEON_EXT and dev_phase == DevPhase.OFF and was_phase != DevPhase.OFF:
                # Keep the same launch placement after leaving Settings.
                _start_location_toast(startup=True)
            prev_dev_phase_for_location_toast[0] = dev_phase

        def toggle_play(_event=None) -> None:
            nonlocal playing, brightness_from, brightness_target, brightness_t0, brightness_duration_s
            _bump_pigeon_user_activity()
            if not scene_enabled or use_backdrop_scene or last_frame is None:
                return
            playing = not playing
            brightness_from = brightness_current
            # False → full brightness; True → slightly dimmed (inverse of old “video playing” semantics).
            brightness_target = LANDING_DIM_BRIGHTNESS if playing else LANDING_DISPLAY_BRIGHTNESS
            brightness_duration_s = (
                brightness_duration_up_s if brightness_target > brightness_from else brightness_duration_down_s
            )
            brightness_t0 = time.monotonic()

        def quit_app(_event=None) -> None:
            root.quit()

        def cycle_dev_phase(_event=None) -> str:
            """Toggle OFF ↔ MAIN_SETTINGS (also exits GRID → OFF)."""
            nonlocal dev_phase, skip_cache
            _bump_pigeon_user_activity()
            if dev_phase == DevPhase.MAIN_SETTINGS:
                if main_settings_widget is not None:
                    try:
                        st_ms = main_settings_widget.state
                        if st_ms.keyboard_open:
                            st_ms.close_keyboard(commit=False)
                        st_ms.exit_pigeon_settings()
                        main_settings_widget.invalidate()
                    except Exception:
                        pass
                dev_phase = DevPhase.OFF
            elif dev_phase == DevPhase.GRID:
                dev_phase = DevPhase.OFF
            else:
                if main_settings_widget is not None:
                    try:
                        if main_settings_widget.state.keyboard_open:
                            main_settings_widget.state.close_keyboard(commit=False)
                            main_settings_widget.invalidate()
                    except Exception:
                        pass
                    try:
                        from pigeon.widgets.ui_color_settings import (
                            load_persisted_theme_into_state,
                        )

                        load_persisted_theme_into_state(main_settings_widget.state)
                        main_settings_widget.invalidate()
                    except Exception:
                        pass
                    try:
                        main_settings_widget.prefetch_scans_for_settings()
                    except Exception:
                        pass
                dev_phase = DevPhase.MAIN_SETTINGS
            skip_cache = None
            sync_developer_chrome()
            if dev_phase == DevPhase.MAIN_SETTINGS:
                try:
                    from pigeon.weather import DEFAULT_WEATHER_ZIP, refresh_weather

                    refresh_weather(zip_code=DEFAULT_WEATHER_ZIP, force=True)
                except Exception:
                    pass
                render_once()
            return "break"

        def _open_landing_scene() -> bool:
            """Black landing page + centered logo; clears TMDb backdrop display flags."""
            nonlocal last_frame, scaled_display, scaled_version, frame_interval_ms, use_backdrop_scene, backdrop_master_bgr
            use_backdrop_scene = False
            backdrop_master_bgr = None
            last_frame = landing_scene_design_bgr
            frame_interval_ms = max(1, int(round(1000.0 / _default_render_fps())))
            if not _PIGEON_EXT:
                scaled_display = _disp_fit().scale_and_crop(last_frame)
            else:
                scaled_display = None
            scaled_version += 1
            return True

        def toggle_scene(_event=None, *, require_overlay: bool = True) -> None:
            nonlocal cap, scene_enabled, last_frame, scaled_display, scaled_version, skip_cache, black_photo, playing, frame_interval_ms, use_backdrop_scene, backdrop_master_bgr
            _bump_pigeon_user_activity()
            if require_overlay and not _design_grid_overlay_active():
                return

            if scene_enabled:
                playing = False
                scene_enabled = False
                use_backdrop_scene = False
                backdrop_master_bgr = None
            else:
                if not _open_landing_scene():
                    return
                scene_enabled = True

            _save_persisted_scene_enabled(scene_enabled)
            skip_cache = None

            if not scene_enabled:
                if _PIGEON_EXT:
                    out_bgr = _compose_shown_frame(None, 1.0)
                    _update_label_photo_from_bgr(label, out_bgr, label_live_photo)
                else:
                    if black_photo is None:
                        black_photo = _bgr_to_tk_image(_black_screen_bgr())
                    label.configure(image=black_photo)
                    label.image = black_photo
            elif scaled_display is not None:
                if _PIGEON_EXT:
                    shown = _compose_shown_frame(last_frame, brightness_current)
                else:
                    shown = _apply_brightness(scaled_display, brightness_current)
                _update_label_photo_from_bgr(label, shown, label_live_photo)

        _last_overlay_mono = [0.0]
        _last_s_mono = [0.0]
        _last_f10_mono = [0.0]
        _last_tmdb_hotkey_mono = [0.0]
        _last_tmdb_quality_report_mono = [0.0]
        # Set True by ⌘⇧X / Ctrl+Shift+X; failure count bumps immediately, cleared when a
        # successful TMDb populate is scored for a new content event key.
        tmdb_quality_error_flag: list[bool] = [False]
        tmdb_quality_flag_set_mono: list[float] = [0.0]
        tmdb_quality_auto_unlog_after_id: list[str | None] = [None]
        tmdb_error_flag_retry_rule_idx: list[int] = [0]
        # When True, finish_tmdb auto-advances through one full error-flag rule cycle.
        tmdb_error_flag_retry_active: list[bool] = [False]
        TMDB_QUALITY_UNLOG_WINDOW_S = 20.0
        TMDB_ERROR_FLAG_RETRY_RULES: list[tuple[str, str, str]] = [
            ("tv", "raw_title", "tv+raw_title"),
            ("tv", "alternate", "tv+alternate_query"),
            ("auto", "raw_title", "auto+raw_title"),
            ("auto", "alternate", "auto+alternate_query"),
            ("movie", "raw_title", "movie+raw_title"),
        ]
        # Last content event key that has already been scored for TMDb quality.
        tmdb_quality_last_scored_event_key: list[str] = [""]
        tmdb_quality_overlay_mode: list[str] = [""]
        tmdb_quality_overlay_t0: list[float] = [0.0]
        _last_tmdb_match_toggle_mono = [0.0]
        _last_space_mono = [0.0]
        _last_adv_shift_tab_mono = [0.0]

        def _trigger_tmdb_quality_toggle_overlay(mode: str) -> None:
            tmdb_quality_overlay_mode[0] = str(mode or "")
            tmdb_quality_overlay_t0[0] = time.monotonic()

        def _tmdb_quality_toggle_overlay_state(
            now_mono: float,
        ) -> tuple[tuple[int, int, int], float, str, int]:
            """Return (BGR color, alpha, caption, phase_key) for the toggle-confirmation X overlay."""
            mode = str(tmdb_quality_overlay_mode[0] or "")
            if mode not in ("flag", "undo"):
                return ((255, 255, 255), 0.0, "", 0)
            t0 = float(tmdb_quality_overlay_t0[0] or 0.0)
            dt = max(0.0, now_mono - t0)
            hold_primary = 1.0
            hold_secondary = 3.0
            fade_s = 0.8
            total = hold_primary + hold_secondary + fade_s
            if dt >= total:
                tmdb_quality_overlay_mode[0] = ""
                return ((255, 255, 255), 0.0, "", 0)
            if mode == "flag":
                c1 = (255, 255, 255)  # white first
                c2 = (0, 0, 255)  # red second
                text = "TMDB ERROR FLAGGED"
            else:
                c1 = (0, 0, 255)  # red first
                c2 = (255, 255, 255)  # white second
                text = "TMDB ERROR REPORT UNDONE"
            if dt < hold_primary:
                return (c1, 1.0, text, 1)
            if dt < (hold_primary + hold_secondary):
                return (c2, 1.0, text, 2)
            fade_t = (dt - hold_primary - hold_secondary) / max(1e-6, fade_s)
            alpha = max(0.0, 1.0 - float(fade_t))
            return (c2, alpha, text, 3)

        def _blend_tmdb_quality_toggle_overlay(
            frame_bgr: np.ndarray,
            *,
            color_bgr: tuple[int, int, int],
            alpha: float,
            caption: str,
        ) -> None:
            if frame_bgr is None or frame_bgr.size == 0:
                return
            a = max(0.0, min(1.0, float(alpha)))
            if a <= 1e-6:
                return
            h, w = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
            if h < 8 or w < 8:
                return
            overlay = frame_bgr.copy()
            margin = max(16, int(round(min(w, h) * 0.24)))
            x0, y0 = margin, margin
            x1, y1 = max(x0 + 1, w - margin), max(y0 + 1, h - margin)
            thick = max(6, int(round(min(w, h) * 0.02)))
            cv2.line(overlay, (x0, y0), (x1, y1), color_bgr, thickness=thick, lineType=cv2.LINE_AA)
            cv2.line(overlay, (x0, y1), (x1, y0), color_bgr, thickness=thick, lineType=cv2.LINE_AA)
            if caption:
                fs = max(0.7, min(2.6, float(min(w, h)) / 520.0))
                txt_th = max(1, int(round(thick * 0.35)))
                tw, th = cv2.getTextSize(caption, cv2.FONT_HERSHEY_SIMPLEX, fs, txt_th)[0]
                tx = max(8, (w - tw) // 2)
                ty = min(h - 10, y1 + max(22, int(round(0.08 * h))))
                cv2.putText(
                    overlay,
                    caption,
                    (tx, ty),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    fs,
                    color_bgr,
                    txt_th,
                    cv2.LINE_AA,
                )
            cv2.addWeighted(overlay, a, frame_bgr, 1.0 - a, 0.0, dst=frame_bgr)

        def _blend_tmdb_quality_flag_badge(frame_bgr: np.ndarray) -> None:
            """Persistent bottom-right red X while the TMDb quality flag is active."""
            if frame_bgr is None or frame_bgr.size == 0:
                return
            h, w = int(frame_bgr.shape[0]), int(frame_bgr.shape[1])
            if h < 16 or w < 16:
                return
            size = max(16, int(round(min(w, h) * 0.06)))
            pad = max(10, int(round(min(w, h) * 0.025)))
            x1 = w - pad
            y1 = h - pad
            x0 = max(0, x1 - size)
            y0 = max(0, y1 - size)
            thick = max(2, int(round(size * 0.22)))
            # Draw directly on the destination frame to avoid per-tick full-frame copies.
            cv2.line(frame_bgr, (x0, y0), (x1, y1), (0, 0, 255), thickness=thick, lineType=cv2.LINE_AA)
            cv2.line(frame_bgr, (x0, y1), (x1, y0), (0, 0, 255), thickness=thick, lineType=cv2.LINE_AA)

        def try_cycle_dev_phase(event: tk.Event | None) -> str | None:
            """Debounced OFF↔MAIN_SETTINGS cycle (mouse / F9); design grid overlay is key 5."""
            now = time.monotonic()
            if now - _last_overlay_mono[0] < 0.08:
                return "break"
            _last_overlay_mono[0] = now
            cycle_dev_phase()
            return "break"

        def on_tab_key(event: tk.Event) -> str | None:
            """Plain Tab: toggle OFF ↔ MAIN_SETTINGS (settings_main)."""
            if _widget_accepts_typing(event.widget):
                return None
            if getattr(event, "keysym", "") == "ISO_Left_Tab":
                return None
            st_tab = int(getattr(event, "state", 0))
            if st_tab & 0x0001:
                return None
            if st_tab & 0x0004:
                return None
            now = time.monotonic()
            if now - _last_overlay_mono[0] < 0.08:
                return "break"
            _last_overlay_mono[0] = now
            cycle_dev_phase()
            return "break"

        def on_shift_tab_dev_cycle(event: tk.Event) -> str | None:
            """Shift+Tab: toggle Settings ↔ off (same as Tab; grid overlay is key 5)."""
            if _widget_accepts_typing(event.widget):
                return None
            ks = getattr(event, "keysym", "") or ""
            st = int(getattr(event, "state", 0))
            if st & 0x0004:
                return None
            if ks != "ISO_Left_Tab" and not (ks == "Tab" and (st & 0x0001)):
                return None
            now = time.monotonic()
            if now - _last_overlay_mono[0] < 0.08:
                return "break"
            _last_overlay_mono[0] = now
            cycle_dev_phase()
            return "break"

        def on_ctrl_tab(event: tk.Event) -> str | None:
            if _widget_accepts_typing(event.widget):
                return None
            if not (int(getattr(event, "state", 0)) & 0x0004):
                return None
            now = time.monotonic()
            if now - _last_overlay_mono[0] < 0.08:
                return "break"
            _last_overlay_mono[0] = now
            cycle_dev_phase()
            return "break"

        def on_s_key(event: tk.Event) -> str | None:
            keysym = (getattr(event, "keysym", "") or "").lower()
            ch = (getattr(event, "char", "") or "").lower()
            if keysym != "s" and ch != "s":
                return None
            if not _design_grid_overlay_active():
                return None
            now = time.monotonic()
            if now - _last_s_mono[0] < 0.08:
                return "break"
            _last_s_mono[0] = now
            toggle_scene(require_overlay=True)
            return "break"

        def apply_saved_tmdb_backdrop_to_display() -> None:
            """Apply last TMDb backdrop + title logo (same as F10’s backdrop step)."""
            nonlocal playing, backdrop_master_bgr, backdrop_app_logo_letterbox_fit, use_backdrop_scene, scene_enabled, last_frame, scaled_display, scaled_version, skip_cache, brightness_current, brightness_from, brightness_target, brightness_t0
            if saved_backdrop_master_bgr is None:
                return
            playing = False
            backdrop_master_bgr = saved_backdrop_master_bgr.copy()
            backdrop_app_logo_letterbox_fit = saved_backdrop_app_logo_letterbox_fit
            if _view_one_uses_now_playing_screen():
                use_backdrop_scene = False
                scaled_version += 1
                _warm_tmdb_logo_patch()
                if view_circles_widget is not None:
                    view_circles_widget.clear_cache()
                _sync_now_playing_screen_state()
                skip_cache = None
                render_once()
                return
            use_backdrop_scene = True
            scene_enabled = True
            last_frame = None
            if status_bar_widget is not None and status_bar_widget.set_accent_from_backdrop_bgr(
                backdrop_master_bgr
            ):
                _warm_status_bar_blits()
            if not _PIGEON_EXT:
                from pigeon.image_ui_protocol import backdrop_scene_bgr_for_display

                scaled_display = backdrop_scene_bgr_for_display(
                    backdrop_master_bgr,
                    display_dims[0],
                    display_dims[1],
                    app_logo_letterbox_fit=backdrop_app_logo_letterbox_fit,
                    app_logo_clock_saver_style=_app_logo_clock_saver_style_now(),
                )
            else:
                scaled_display = None
            scaled_version += 1
            brightness_current = brightness_from = brightness_target = BACKDROP_BRIGHTNESS
            brightness_t0 = time.monotonic()
            _warm_tmdb_logo_patch()
            _save_persisted_scene_enabled(True)
            skip_cache = None
            _apply_netflix_backdrop_when_running()
            render_once()

        def f10_cycle_scene_grid() -> None:
            """
            Developer grid only: F10 cycles display on (landing) → off → backdrop (if saved) → landing.
            """
            nonlocal cap, scene_enabled, last_frame, scaled_display, scaled_version, skip_cache, playing, frame_interval_ms, use_backdrop_scene, backdrop_master_bgr, brightness_current, brightness_from, brightness_target, brightness_t0

            _bump_pigeon_user_activity()
            landing_on = scene_enabled and (not use_backdrop_scene) and last_frame is not None

            if use_backdrop_scene and backdrop_master_bgr is not None:
                if not _open_landing_scene():
                    scene_enabled = False
                    _save_persisted_scene_enabled(False)
                    skip_cache = None
                    render_once()
                    return
                scene_enabled = True
                playing = False
                brightness_current = brightness_from = brightness_target = LANDING_DISPLAY_BRIGHTNESS
                brightness_t0 = time.monotonic()
                _save_persisted_scene_enabled(True)
                skip_cache = None
                render_once()
                return

            if landing_on:
                playing = False
                scene_enabled = False
                use_backdrop_scene = False
                backdrop_master_bgr = None
                last_frame = None
                scaled_display = None
                _save_persisted_scene_enabled(False)
                skip_cache = None
                render_once()
                return

            if saved_backdrop_master_bgr is not None:
                apply_saved_tmdb_backdrop_to_display()
                return

            if not _open_landing_scene():
                scene_enabled = False
                _save_persisted_scene_enabled(False)
                skip_cache = None
                render_once()
                return
            scene_enabled = True
            _save_persisted_scene_enabled(True)
            skip_cache = None
            render_once()

        def on_f10_key(_event: tk.Event | None = None) -> str:
            now = time.monotonic()
            if now - _last_f10_mono[0] < 0.12:
                return "break"
            _last_f10_mono[0] = now
            if _design_grid_overlay_active():
                f10_cycle_scene_grid()
            else:
                toggle_scene(require_overlay=False)
            return "break"

        def on_click_focus(_event: tk.Event | None = None) -> None:
            _bump_pigeon_user_activity(_event)
            try:
                label.focus_set()
            except tk.TclError:
                try:
                    root.focus_set()
                except tk.TclError:
                    pass

        def on_double_click_scene(_event: tk.Event | None = None) -> None:
            toggle_scene(require_overlay=False)

        def show_command_entry(_event=None) -> None:
            nonlocal command_entry_visible
            if dev_phase != DevPhase.GRID and display_view_holder[0] != DisplayView.FIVE:
                return
            command_entry_visible = True
            place_command_bar()
            command_bar.lift()
            command_entry.focus_set()

            def _focus_cmd() -> None:
                try:
                    command_entry.focus_force()
                except tk.TclError:
                    try:
                        command_entry.focus_set()
                    except tk.TclError:
                        pass

            root.after_idle(_focus_cmd)

        _last_command_submit_mono = [0.0]

        def parse_tmdb_command_phrase(phrase: str) -> tuple[str, str]:
            """Return (query, prefer) with prefer one of auto | movie | tv."""
            p = phrase.strip()
            m_tv = re.match(r"(?i)^tv\s+(.+)$", p)
            if m_tv:
                return m_tv.group(1).strip(), "tv"
            m_mov = re.match(r"(?i)^movie\s+(.+)$", p)
            if m_mov:
                return m_mov.group(1).strip(), "movie"
            return p, "auto"

        def _escape_log_field(s: str | None) -> str:
            return (s or "").replace("\\", "\\\\").replace("\t", " ").replace("\r", " ").replace("\n", " ")

        def _append_tmdb_quality_event_report_log(
            *,
            outcome: str,
            title_key: str | None,
            display_title: str | None,
            msg_m: str,
        ) -> None:
            """Append one scored TMDb quality event line (SUCCESS/FAILURE) with metadata context."""
            log_p = pigeon_state_dir() / "tmdb_quality_event_reports.log"
            log_p.parent.mkdir(parents=True, exist_ok=True)
            md_raw = apple_tv_auto_state.get("last_metadata")
            raw_bits: list[str] = []
            app_bits: list[str] = []
            if isinstance(md_raw, dict):
                app_name = str(md_raw.get("app_name") or "").strip()
                app_id = str(md_raw.get("app_id") or "").strip()
                if app_name:
                    app_bits.append(f'app_name="{_escape_log_field(app_name)}"')
                if app_id:
                    app_bits.append(f'app_id="{_escape_log_field(app_id)}"')
                try:
                    from pigeon.raw_title import raw_title_from_metadata_dict

                    rt = raw_title_from_metadata_dict(md_raw)
                    if (rt.raw_title or "").strip():
                        raw_bits.append(f'raw_title="{_escape_log_field(rt.raw_title)}"')
                    if (rt.raw_series_name or "").strip():
                        raw_bits.append(f'raw_series_name="{_escape_log_field(rt.raw_series_name)}"')
                    if (rt.raw_query or "").strip():
                        raw_bits.append(f'raw_query="{_escape_log_field(rt.raw_query)}"')
                    if (rt.raw_episode_title or "").strip():
                        raw_bits.append(f'raw_episode_title="{_escape_log_field(rt.raw_episode_title)}"')
                    if (rt.layer_series_title or "").strip():
                        raw_bits.append(f'layer_series_title="{_escape_log_field(rt.layer_series_title)}"')
                    if not raw_bits:
                        raw_bits.append("(rawTitle layers empty for this snapshot)")
                except Exception:
                    t_fallback = str(md_raw.get("title") or "").strip()
                    q_fallback = str(md_raw.get("query") or "").strip()
                    raw_bits.append(
                        f'fallback_title="{_escape_log_field(t_fallback)}" query="{_escape_log_field(q_fallback)}"'
                    )
            else:
                raw_bits.append("(no last_metadata dict)")
            sb_label = str(streaming_badge_state.get("label") or "").strip()
            sb_filename = str(streaming_badge_state.get("filename") or "").strip()
            if sb_label:
                app_bits.append(f'streaming_service_label="{_escape_log_field(sb_label)}"')
            if sb_filename:
                app_bits.append(f'streaming_service_badge="{_escape_log_field(sb_filename)}"')
            if not app_bits:
                app_bits.append("(streaming_service unknown)")
            fetch_head = (msg_m or "").split("::", 1)[0].strip()
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            out_u = str(outcome or "").strip().upper()
            if out_u not in ("SUCCESS", "FAILURE"):
                out_u = "UNKNOWN"
            line = (
                f"{ts}\t{out_u}\t"
                f'tmdb_title_key="{_escape_log_field(title_key or "")}"\t'
                f'display_title="{_escape_log_field(display_title or "")}"\t'
                f'fetch_summary_head="{_escape_log_field(fetch_head)}"\t'
                f"{' '.join(app_bits)}\t"
                f"{' '.join(raw_bits)}\n"
            )
            with log_p.open("a", encoding="utf-8") as lf:
                lf.write(line)
            try:
                from pigeon.tmdb_desktop_report import append_tmdb_quality_row

                append_tmdb_quality_row(
                    outcome=out_u,
                    title_key=title_key,
                    display_title=display_title,
                    fetch_summary_head=fetch_head,
                    app_streaming_context=" ".join(app_bits),
                    raw_title_context=" ".join(raw_bits),
                )
            except Exception:
                pass
            if out_u == "FAILURE":
                try:
                    from pigeon.tmdb_desktop_report import append_tmdb_error_event

                    append_tmdb_error_event(
                        last_metadata=md_raw if isinstance(md_raw, dict) else None,
                        streaming_badge_state=streaming_badge_state,
                    )
                except Exception:
                    pass

        def _clear_displayed_tmdb_art_for_content_change() -> None:
            """Drop on-screen TMDb art/cast when the playing title changes.

            Clears the active title key and live backdrop master so circles/classic
            stop showing the prior poster/cast. Leaves ``saved_backdrop_master_bgr``
            for classic scene restore after a full idle. Resets ``tmdb_key`` so the
            next spawn is not suppressed as “same identity”.
            """
            nonlocal active_tmdb_title_key, active_tmdb_display_title, tmdb_logo_patch_bgra
            nonlocal tmdb_logo_app_fallback_active, skip_cache, backdrop_master_bgr
            active_tmdb_title_key = None
            active_tmdb_display_title = None
            tmdb_logo_app_fallback_active = False
            backdrop_master_bgr = None
            apple_tv_auto_state["tmdb_key"] = None
            apple_tv_auto_state["tmdb_missing_art"] = False
            apple_tv_auto_state["tmdb_exhausted_identity"] = None
            tmdb_error_flag_retry_active[0] = False
            tmdb_error_flag_retry_rule_idx[0] = 0
            _tmdb_poster_cache["key"] = None
            _tmdb_poster_cache["bgra"] = None
            if tmdb_logo_widget is not None:
                tmdb_logo_widget.clear_cache()
            if tmdb_logo_widget_view_six is not None:
                tmdb_logo_widget_view_six.clear_cache()
            tmdb_logo_patch_bgra = None
            _warm_tmdb_logo_patch()
            if _view_one_uses_now_playing_screen():
                _clear_now_playing_view_caches()
                _sync_now_playing_screen_state()
            skip_cache = None

        def _mark_tmdb_missing_art(*, identity: object | None = None) -> None:
            """Stop empty-display poll respawns and show the circles '?' placeholder."""
            apple_tv_auto_state["tmdb_missing_art"] = True
            if identity is not None:
                apple_tv_auto_state["tmdb_exhausted_identity"] = identity
                apple_tv_auto_state["tmdb_key"] = identity
            tmdb_error_flag_retry_active[0] = False
            try:
                sys.stderr.write(
                    "pigeon: TMDb give-up — showing missing-art placeholder (no further auto-retries).\n"
                )
                sys.stderr.flush()
            except Exception:
                pass

        def _clear_tmdb_missing_art() -> None:
            apple_tv_auto_state["tmdb_missing_art"] = False
            apple_tv_auto_state["tmdb_exhausted_identity"] = None
            tmdb_error_flag_retry_active[0] = False

        def spawn_tmdb_poster_fetch(
            query: str, *, prefer: str = "auto", force: bool = False
        ) -> None:
            """TMDb search + download + poster pipeline on a worker thread.

            Short-circuits for MediaType.Music: on viewOne.audioContent the
            only TT-rect substitute we render is the two-line text patch
            (track title + "Artist – Album"), so there is no consumer for a
            TMDb movie/TV backdrop or title treatment. Skipping the fetch
            here also avoids ~1–3 s of background network work per track
            change plus the misleading retry-log entries that a music title
            would otherwise generate against a TV/movie-only index.

            Only one worker runs at a time. If a fetch is already in flight,
            the latest request is stored in ``pending_tmdb`` and started when
            the current worker finishes (``force`` still queues; it no longer
            starts a concurrent second worker). ``tmdb_key`` is set only when a
            worker actually starts, so a skipped in-flight poll cannot poison
            the next identity check.
            """
            from pigeon.tmdb_poster import is_degenerate_tmdb_query, refine_tmdb_search_query

            del force  # kept for call-site compat; queueing replaces concurrent force

            if _vv_is_music():
                # Clear any prior fetch breadcrumbs so the debug view doesn't
                # show stale values carried over from the previous track/video.
                apple_tv_auto_state["last_tmdb_fetch_input"] = None
                apple_tv_auto_state["last_tmdb_fetch_refined"] = None
                apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
                apple_tv_auto_state["pending_tmdb"] = None
                return

            try:
                from pigeon.source_toggles import source_enabled

                if not source_enabled("wifi"):
                    return
            except Exception:
                pass

            q_in = (query or "").strip()
            q = refine_tmdb_search_query(q_in) or ""
            if not q:
                return
            if is_degenerate_tmdb_query(q):
                return
            try:
                from pigeon.tmdb_poster import tmdb_is_configured
            except ImportError:
                tmdb_is_configured = lambda: False  # type: ignore[misc, assignment]
            if not tmdb_is_configured():
                if not apple_tv_auto_state.get("tmdb_missing_warned"):
                    apple_tv_auto_state["tmdb_missing_warned"] = True
                    hint = (
                        "pigeon: TMDb not configured — skipping artwork fetch. "
                        f"Add API key to {pigeon_state_dir() / 'tmdb_api_key'} "
                        "(see installer/setup/README on Pi)."
                    )
                    sys.stderr.write(hint + "\n")
                    sys.stderr.flush()
                    try:
                        from pigeon.pi_diagnostics import append_pigeon_log

                        append_pigeon_log(hint)
                    except Exception:
                        pass
                return
            prefer_n = str(prefer or "auto").strip() or "auto"
            if not _tmdb_spawn_identity_changed(q_in, prefer_n):
                return
            if apple_tv_auto_state.get("tmdb_fetch_in_flight"):
                # Keep spinner up; run this title as soon as the worker ends.
                apple_tv_auto_state["pending_tmdb"] = {"query": q_in, "prefer": prefer_n}
                try:
                    from pigeon.pi_diagnostics import append_pigeon_log

                    append_pigeon_log(
                        f"tmdb fetch queued (in flight): {q!r} prefer={prefer_n!r}"
                    )
                except Exception:
                    pass
                if _view_one_uses_now_playing_screen():
                    _sync_now_playing_screen_state()
                    try:
                        render_once()
                    except Exception:
                        pass
                return
            apple_tv_auto_state["pending_tmdb"] = None
            apple_tv_auto_state["tmdb_key"] = _tmdb_spawn_identity(q_in, prefer_n)
            apple_tv_auto_state["query"] = q_in
            apple_tv_auto_state["prefer"] = prefer_n
            apple_tv_auto_state["last_tmdb_fetch_input"] = q_in
            apple_tv_auto_state["last_tmdb_fetch_refined"] = q
            apple_tv_auto_state["last_tmdb_fetch_prefer"] = prefer_n
            apple_tv_auto_state["tmdb_fetch_in_flight"] = True
            try:
                from pigeon.title_decision import (
                    apply_decision_to_metadata,
                    record_title_decision,
                )

                decision = record_title_decision(
                    q,
                    source="tmdb",
                    reason=f"spawned TMDb search (prefer={prefer_n})",
                    extras={"input": q_in, "prefer": prefer_n},
                )
                apple_tv_auto_state["last_title_decision"] = decision.explain()
                md_dec = apple_tv_auto_state.get("last_metadata")
                if isinstance(md_dec, dict):
                    apply_decision_to_metadata(md_dec, decision)
            except Exception:
                pass
            try:
                from pigeon.pi_diagnostics import append_pigeon_log

                append_pigeon_log(f"tmdb fetch started: {q!r} prefer={prefer_n!r}")
            except Exception:
                pass
            # Show searching spinner in the poster immediately.
            if _view_one_uses_now_playing_screen():
                _sync_now_playing_screen_state()
                try:
                    render_once()
                except Exception:
                    pass

            def _drain_pending_tmdb_spawn() -> None:
                pend = apple_tv_auto_state.get("pending_tmdb")
                apple_tv_auto_state["pending_tmdb"] = None
                if not isinstance(pend, dict):
                    return
                pq = str(pend.get("query") or "").strip()
                pp = str(pend.get("prefer") or "auto").strip() or "auto"
                if not pq:
                    return
                root.after(0, lambda: spawn_tmdb_poster_fetch(pq, prefer=pp, force=True))

            def finish_tmdb(
                ok_m: bool,
                msg_m: str,
                backdrop_master: np.ndarray | None = None,
                match_tier: int = 0,
                search_query: str = "",
            ) -> None:
                nonlocal skip_cache, cap, scene_enabled, last_frame, scaled_display, scaled_version, playing, use_backdrop_scene, backdrop_master_bgr, saved_backdrop_master_bgr, saved_backdrop_app_logo_letterbox_fit, backdrop_app_logo_letterbox_fit, brightness_current, brightness_from, brightness_target, brightness_t0, active_tmdb_title_key, active_tmdb_display_title, tmdb_logo_patch_bgra, tmdb_logo_app_fallback_active
                apple_tv_auto_state["tmdb_fetch_in_flight"] = False
                sys.stderr.write(f"pigeon: tmdb → {msg_m}\n")
                sys.stderr.flush()
                try:
                    from pigeon.pi_diagnostics import append_pigeon_log

                    append_pigeon_log(f"tmdb → {msg_m}")
                except Exception:
                    pass
                # Only ignore results when a newer title is already queued. Do not use
                # live ``query`` alternation (pyatv show vs episode) — that was marking
                # successful fetches stale and leaving the poster empty forever.
                pending_raw = apple_tv_auto_state.get("pending_tmdb")
                result_stale = False
                if isinstance(pending_raw, dict):
                    pq = str(pending_raw.get("query") or "").strip()
                    if pq and pq != q_in:
                        try:
                            from pigeon.tmdb_poster import equivalent_tmdb_search_queries

                            if not equivalent_tmdb_search_queries(pq, q_in):
                                result_stale = True
                        except Exception:
                            result_stale = True
                if result_stale:
                    try:
                        from pigeon.pi_diagnostics import append_pigeon_log

                        append_pigeon_log(
                            f"tmdb result ignored (stale/pending): worker={q_in!r}"
                        )
                    except Exception:
                        pass
                    _drain_pending_tmdb_spawn()
                    return
                tier_ok = _tmdb_match_tier_acceptable(search_query or q, int(match_tier))
                if not ok_m:
                    # No show title found: do not interrupt with an error dialog. Surface the
                    # streaming-app logo in the content-logo slot and leave the current scene
                    # alone — no new backdrop is enabled here (successful matches still get
                    # their own backdrop below).
                    sys.stderr.write(
                        "pigeon: TMDb search found no match — showing streaming app logo in "
                        "the content-logo slot (no backdrop).\n"
                    )
                    sys.stderr.flush()
                    try:
                        from pigeon.tmdb_desktop_report import append_tmdb_error_event

                        md_err = apple_tv_auto_state.get("last_metadata")
                        append_tmdb_error_event(
                            last_metadata=md_err if isinstance(md_err, dict) else None,
                            streaming_badge_state=streaming_badge_state,
                            supplemental_metadata=(
                                f"no_match refined_query={q!r} prefer={prefer!r} msg={msg_m!s}"
                            ),
                        )
                    except Exception:
                        pass
                    # Error-flag path: auto-advance through one full rule cycle, then give up.
                    if tmdb_error_flag_retry_active[0]:
                        if tmdb_error_flag_retry_rule_idx[0] < len(TMDB_ERROR_FLAG_RETRY_RULES):
                            _drain_pending_tmdb_spawn()
                            root.after(0, _perform_tmdb_error_flag_retry)
                            return
                        _mark_tmdb_missing_art(
                            identity=_tmdb_spawn_identity(q_in, prefer_n)
                        )
                    else:
                        # Ordinary no-match: one attempt per identity — do not let the
                        # empty-display poll respawn forever (spinner with blank poster).
                        _mark_tmdb_missing_art(
                            identity=_tmdb_spawn_identity(q_in, prefer_n)
                        )
                    if _apply_rawtitle_text_tt_fallback():
                        if tmdb_logo_widget is not None:
                            tmdb_logo_widget.clear_cache()
                        if tmdb_logo_widget_view_six is not None:
                            tmdb_logo_widget_view_six.clear_cache()
                        _warm_tmdb_logo_patch()
                        if _view_one_uses_now_playing_screen():
                            _clear_now_playing_view_caches()
                            _sync_now_playing_screen_state()
                        skip_cache = None
                        render_once()
                        _drain_pending_tmdb_spawn()
                        return
                    active_tmdb_title_key = None
                    active_tmdb_display_title = None
                    tmdb_logo_app_fallback_active = True
                    if tmdb_logo_widget is not None:
                        tmdb_logo_widget.clear_cache()
                    if tmdb_logo_widget_view_six is not None:
                        tmdb_logo_widget_view_six.clear_cache()
                    _warm_tmdb_logo_patch()
                    if _view_one_uses_now_playing_screen():
                        _clear_now_playing_view_caches()
                        _sync_now_playing_screen_state()
                    skip_cache = None
                    render_once()
                    _drain_pending_tmdb_spawn()
                    return
                _clear_tmdb_missing_art()
                tmdb_logo_app_fallback_active = False
                # msg_m includes a prefix when successful: "<title_key>::<display_title>::<summary>"
                parts = msg_m.split("::", 2)
                if len(parts) >= 2:
                    active_tmdb_title_key = parts[0].strip() or None
                    active_tmdb_display_title = parts[1].strip() or None
                else:
                    active_tmdb_title_key = None
                    active_tmdb_display_title = None
                if ok_m and not tier_ok:
                    sys.stderr.write(
                        f"pigeon: TMDb match tier {match_tier} below threshold for {search_query or q!r} "
                        "— using rawTitle text TT.\n"
                    )
                    sys.stderr.flush()
                    _apply_rawtitle_text_tt_fallback()
                if tmdb_logo_widget is not None:
                    tmdb_logo_widget.clear_cache()
                if tmdb_logo_widget_view_six is not None:
                    tmdb_logo_widget_view_six.clear_cache()
                _warm_tmdb_logo_patch()
                bd_use = backdrop_master
                from_app_logo = False
                if bd_use is None:
                    bd_use = _backdrop_master_from_streaming_app_logo()
                    from_app_logo = bd_use is not None
                if bd_use is not None:
                    backdrop_master_bgr = bd_use
                    saved_backdrop_master_bgr = np.asarray(bd_use, dtype=np.uint8).copy()
                    saved_backdrop_app_logo_letterbox_fit = from_app_logo
                    backdrop_app_logo_letterbox_fit = from_app_logo
                    scaled_version += 1
                    if _view_one_uses_now_playing_screen():
                        # View 1 paints TMDB in the now-playing bar only. Keep scene off so
                        # render_once always takes the chrome compose path (skip-cache there
                        # omits TMDB bar state and would freeze the bar empty).
                        use_backdrop_scene = False
                        if status_bar_widget is not None:
                            bd_arr = np.asarray(backdrop_master_bgr, dtype=np.uint8)
                            if status_bar_widget.set_accent_from_backdrop_bgr(bd_arr):
                                _warm_status_bar_blits()
                                skip_cache = None
                    else:
                        if cap is not None:
                            try:
                                cap.release()
                            except Exception:
                                pass
                            cap = None
                        use_backdrop_scene = True
                        scene_enabled = True
                        playing = False
                        last_frame = None
                        if not _PIGEON_EXT:
                            scaled_display = None
                        else:
                            scaled_display = None
                        _save_persisted_scene_enabled(True)
                        # Backdrop is static image — not paused-video 0.3; use dedicated backdrop level.
                        brightness_current = brightness_from = brightness_target = BACKDROP_BRIGHTNESS
                        brightness_t0 = time.monotonic()
                        if not _apply_netflix_backdrop_when_running():
                            if status_bar_widget is not None:
                                bd_arr = np.asarray(backdrop_master_bgr, dtype=np.uint8)
                                if status_bar_widget.set_accent_from_backdrop_bgr(bd_arr):
                                    _warm_status_bar_blits()
                                    skip_cache = None
                # Match-quality counters: score only when TMDb material changes to a
                # new content event key (not on same-content retries/refetches).
                if active_tmdb_title_key:
                    try:
                        ev_key = str(apple_tv_auto_state.get("content_key") or "").strip()
                        if not ev_key:
                            # Fallback so manual fetches without poll metadata still have
                            # a deterministic event key.
                            qk = str(apple_tv_auto_state.get("query") or "").strip()
                            ev_key = f"{str(active_tmdb_title_key or '').strip()}::{qk}"
                        if ev_key and ev_key != str(tmdb_quality_last_scored_event_key[0] or ""):
                            had_qe = bool(tmdb_quality_error_flag[0])
                            if had_qe:
                                _cancel_tmdb_quality_auto_unlog_timer()
                            tmdb_quality_error_flag[0] = False
                            cur_q = read_app_state()
                            s_q = int(cur_q.get("tmdb_quality_successes", 0) or 0)
                            f_q = int(cur_q.get("tmdb_quality_failures", 0) or 0)
                            if not had_qe:
                                s_q += 1
                                try:
                                    _append_tmdb_quality_event_report_log(
                                        outcome="SUCCESS",
                                        title_key=active_tmdb_title_key,
                                        display_title=active_tmdb_display_title,
                                        msg_m=msg_m,
                                    )
                                except Exception:
                                    pass
                            write_app_state(tmdb_quality_successes=s_q, tmdb_quality_failures=f_q)
                            tmdb_quality_last_scored_event_key[0] = ev_key
                    except Exception:
                        pass
                if _view_one_uses_now_playing_screen():
                    _clear_now_playing_view_caches()
                    _sync_now_playing_screen_state()
                skip_cache = None
                render_once()
                _drain_pending_tmdb_spawn()

            def worker() -> None:
                used_q = q
                try:
                    from pigeon.raw_title import tmdb_query_candidates_from_metadata
                    from pigeon.tmdb_poster import apply_tmdb_movie_query

                    candidates: list[str] = []
                    md_raw = apple_tv_auto_state.get("last_metadata")
                    app_nm: str | None = None
                    app_ident: str | None = None
                    if isinstance(md_raw, dict):
                        app_nm = str(md_raw.get("app_name") or "").strip() or None
                        app_ident = str(md_raw.get("app_id") or "").strip() or None
                        for cand in tmdb_query_candidates_from_metadata(md_raw):
                            if cand not in candidates:
                                candidates.append(cand)
                    if q not in candidates:
                        candidates.insert(0, q)
                    elif candidates and candidates[0] != q:
                        candidates = [q] + [c for c in candidates if c != q]
                    if not candidates:
                        candidates = [q]
                    ok_w, msg_w, bd_w, tier_w = False, "No candidates.", None, 0
                    used_q = q
                    for cand in candidates:
                        ok_try, msg_try, bd_try, tier_try = apply_tmdb_movie_query(
                            cand,
                            prefer=prefer,
                            app_name=app_nm,
                            app_id=app_ident,
                        )  # type: ignore[arg-type]
                        used_q = cand
                        if ok_try and _tmdb_match_tier_acceptable(cand, int(tier_try)):
                            ok_w, msg_w, bd_w, tier_w = ok_try, msg_try, bd_try, tier_try
                            break
                        if ok_try and not ok_w:
                            ok_w, msg_w, bd_w, tier_w = ok_try, msg_try, bd_try, tier_try
                    if not ok_w:
                        for cand in candidates:
                            ok_try, msg_try, bd_try, tier_try = apply_tmdb_movie_query(
                                cand,
                                prefer=prefer,
                                forgiving=True,
                                app_name=app_nm,
                                app_id=app_ident,
                            )  # type: ignore[arg-type]
                            used_q = cand
                            if ok_try:
                                ok_w, msg_w, bd_w, tier_w = ok_try, msg_try, bd_try, tier_try
                                break
                except Exception as e:
                    ok_w, msg_w, bd_w, tier_w, used_q = False, str(e), None, 0, q
                root.after(
                    0,
                    lambda o=ok_w, m=msg_w, b=bd_w, t=tier_w, sq=used_q: finish_tmdb(
                        o, m, b, t, sq
                    ),
                )

            threading.Thread(target=worker, daemon=True).start()

        def _content_indicator_ok() -> bool:
            if not _PIGEON_EXT:
                return False
            if not current_apple_tv.get("identifier"):
                return False
            if apple_tv_busy["active"]:
                return False
            lf = apple_tv_dashboard_track.get("last_poll_ok")
            # Failed poll must not be masked by a stale latched query.
            if lf is False:
                return False
            q = apple_tv_auto_state.get("query")
            if q:
                return True
            return False

        def _advanced_feature_pipeline_ok() -> bool:
            """Stricter than the Content LED: last poll succeeded and metadata is active (not idle/unknown)."""
            if not _PIGEON_EXT:
                return False
            if not current_apple_tv.get("identifier"):
                return False
            if apple_tv_busy["active"]:
                return False
            if apple_tv_dashboard_track.get("last_poll_ok") is not True:
                return False
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict) or _atv_metadata_is_content_idle(lm):
                return False
            return True

        def _read_tmdb_quality_counts() -> tuple[int, int]:
            if not _PIGEON_EXT:
                return (0, 0)
            try:
                st = read_app_state()
                s = int(st.get("tmdb_quality_successes", 0) or 0)
                f = int(st.get("tmdb_quality_failures", 0) or 0)
                return (s, f)
            except Exception:
                return (0, 0)

        def _adjust_tmdb_quality_failure_delta(delta: int) -> None:
            """Persist ±1 failure immediately (⌘⇧X flag on / undo); refreshes Settings glance."""
            if not _PIGEON_EXT or int(delta) == 0:
                return
            try:
                st = read_app_state()
                s = int(st.get("tmdb_quality_successes", 0) or 0)
                f = max(0, int(st.get("tmdb_quality_failures", 0) or 0) + int(delta))
                write_app_state(tmdb_quality_successes=s, tmdb_quality_failures=f)
                match_quality_glance_sig[0] = ""
                _refresh_match_quality_glance_label()
            except Exception:
                pass

        def _cancel_tmdb_quality_auto_unlog_timer() -> None:
            aid = tmdb_quality_auto_unlog_after_id[0]
            if aid:
                try:
                    root.after_cancel(aid)
                except tk.TclError:
                    pass
            tmdb_quality_auto_unlog_after_id[0] = None

        def _clear_tmdb_quality_flag(*, undo: bool, show_overlay: bool) -> None:
            nonlocal skip_cache
            if tmdb_quality_error_flag[0] and undo:
                _adjust_tmdb_quality_failure_delta(-1)
            tmdb_quality_error_flag[0] = False
            _cancel_tmdb_quality_auto_unlog_timer()
            if show_overlay:
                _trigger_tmdb_quality_toggle_overlay("undo")
            skip_cache = None

        def _schedule_tmdb_quality_auto_expire() -> None:
            _cancel_tmdb_quality_auto_unlog_timer()
            delay_ms = int(round(TMDB_QUALITY_UNLOG_WINDOW_S * 1000.0))

            def _expire() -> None:
                nonlocal skip_cache
                tmdb_quality_auto_unlog_after_id[0] = None
                if tmdb_quality_error_flag[0]:
                    tmdb_quality_error_flag[0] = False
                    skip_cache = None

            tmdb_quality_auto_unlog_after_id[0] = root.after(delay_ms, _expire)

        def _apply_rawtitle_text_tt_fallback() -> bool:
            nonlocal active_tmdb_title_key, active_tmdb_display_title, tmdb_logo_app_fallback_active
            try:
                from pigeon.raw_title import raw_title_from_metadata_dict
                from pigeon.tmdb_poster import title_key
            except ImportError:
                return False
            md = apple_tv_auto_state.get("last_metadata")
            if not isinstance(md, dict):
                return False
            rt = raw_title_from_metadata_dict(md)
            raw = (rt.raw_title or "").strip()
            if not raw:
                return False
            # TT/display can use the Apple TV raw label, but do not replace an existing
            # TMDb media key — poster/cast are cached under the clean match title
            # (e.g. ``Game Night``), while raw often keeps ``(YYYY)``.
            if not active_tmdb_title_key:
                active_tmdb_title_key = title_key(raw)
            active_tmdb_display_title = raw
            tmdb_logo_app_fallback_active = False
            return True

        def _tmdb_match_tier_acceptable(query: str, tier: int) -> bool:
            try:
                from pigeon.tmdb_poster import _literal_min_acceptable_tier
            except ImportError:
                return int(tier) >= 4
            return int(tier) >= int(_literal_min_acceptable_tier(query))

        def on_reset_tmdb_match_quality_stats() -> None:
            """Zero the Settings success/fail counters (state.json only). Logs and desktop reports unchanged."""
            if not _PIGEON_EXT:
                return
            try:
                write_app_state(tmdb_quality_successes=0, tmdb_quality_failures=0)
                match_quality_glance_sig[0] = ""
                _refresh_match_quality_glance_label()
            except Exception:
                pass

        def _format_tmdb_match_quality_glance(s: int, f: int) -> str:
            tot = s + f
            if tot <= 0:
                return "TMDb match quality  —  no scored events yet"
            ok_pct = 100.0 * float(s) / float(tot)
            return f"TMDb match quality   {s} ok   {f} fail   {tot} events   {ok_pct:.0f}% ok"

        def _refresh_match_quality_glance_label() -> None:
            # Legacy Tk glance label — unused with settings_main.
            return

        def _refresh_content_indicator() -> None:
            cv = content_indicator_cv_holder[0]
            if cv is not None:
                try:
                    _paint_boolean_led(cv, _content_indicator_ok())
                except tk.TclError:
                    pass
            _refresh_match_quality_glance_label()

        def _paint_pair_led(which: int, ok: bool | None) -> None:
            """Pairing LED: green=detected+compatible, amber=detected+unknown/incompatible, red=not detected/not compatible."""
            canvas = pairing_led_holder[which] if 0 <= which < len(pairing_led_holder) else None
            if canvas is None:
                return
            try:
                canvas.delete("all")
                if ok is True:
                    fill = "#1fcb5d"
                elif ok is None:
                    fill = "#f0ad4e"
                else:
                    fill = "#e74c3c"
                canvas.create_oval(2, 2, 12, 12, fill=fill, outline="#151518", width=1)
            except tk.TclError:
                pass

        def _paint_cred_led_canvas(canvas: tk.Canvas | None, ok: bool | None) -> None:
            """Canvas LED: green=detected+compatible, amber=detected+unknown/incompatible, red=not detected/not compatible."""
            if canvas is None:
                return
            try:
                canvas.delete("all")
                if ok is True:
                    fill = "#1fcb5d"
                elif ok is None:
                    fill = "#f0ad4e"
                else:
                    fill = "#e74c3c"
                canvas.create_oval(2, 2, 12, 12, fill=fill, outline="#151518", width=1)
            except tk.TclError:
                pass

        def _remove_saved_player_device(for_location_id: str | None = None) -> None:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            if not messagebox.askyesno(
                "Remove Player",
                "Remove the saved Player device?\n\n"
                "Playback metadata stops using this Apple TV. "
                "pyatv credentials on this Mac are not deleted (use Reset to wipe those).",
                parent=root,
            ):
                return
            lid = (for_location_id or read_current_location_id() or "").strip()
            write_saved_streaming_device(None, for_location_id=lid or None)
            cur = read_current_location_id()
            if lid and cur and lid == cur:
                streaming_slot_holder[0] = None
                clear_last_apple_tv()
                current_apple_tv.clear()
                current_apple_tv.update(
                    {"identifier": "", "address": "", "name": "", "label": ""}
                )
                apple_tv_auto_state["content_key"] = None
                apple_tv_auto_state["tmdb_key"] = None
                apple_tv_auto_state["query"] = None
                apple_tv_auto_state["last_metadata"] = None
                apple_tv_auto_state["last_tmdb_fetch_input"] = None
                apple_tv_auto_state["last_tmdb_fetch_refined"] = None
                apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
                apple_tv_playback_clock.clear()
                apple_tv_playback_clock.update(
                    {
                        "has_sync": False,
                        "sync_mono": 0.0,
                        "sync_position": 0.0,
                        "live_mode": False,
                        "playing": False,
                        "latched_total": None,
                        "latched_content_key": None,
                        "last_reported_total": None,
                        "display_played_sec": None,
                        "trt_next_fire_mono": None,
                    }
                )
                _clear_reported_position_stall_stamp()
                apple_tv_dashboard_track["last_poll_ok"] = None
                apple_tv_dashboard_track["consecutive_fail"] = 0
                _sync_status_bar_visibility_for_playback(None)
            else:
                streaming_slot_holder[0] = read_saved_streaming_device()
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _remove_streaming_device_at(for_location_id: str, index: int) -> None:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            if not messagebox.askyesno(
                "Remove Player",
                "Remove this Player entry from this location?",
                parent=root,
            ):
                return
            lid = str(for_location_id or "").strip()
            remove_device_at_slot_index("streaming", int(index), for_location_id=lid or None)
            cur = read_current_location_id()
            remaining = read_saved_streaming_devices_all()
            if lid and cur and lid == cur:
                streaming_slot_holder[0] = read_saved_streaming_device()
                if not remaining:
                    clear_last_apple_tv()
                    current_apple_tv.clear()
                    current_apple_tv.update(
                        {"identifier": "", "address": "", "name": "", "label": ""}
                    )
                    apple_tv_auto_state["content_key"] = None
                    apple_tv_auto_state["tmdb_key"] = None
                    apple_tv_auto_state["query"] = None
                    apple_tv_auto_state["last_metadata"] = None
                    apple_tv_auto_state["last_tmdb_fetch_input"] = None
                    apple_tv_auto_state["last_tmdb_fetch_refined"] = None
                    apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
                    apple_tv_playback_clock.clear()
                    apple_tv_playback_clock.update(
                        {
                            "has_sync": False,
                            "sync_mono": 0.0,
                            "sync_position": 0.0,
                            "live_mode": False,
                            "playing": False,
                            "latched_total": None,
                            "latched_content_key": None,
                            "last_reported_total": None,
                            "display_played_sec": None,
                            "trt_next_fire_mono": None,
                        }
                    )
                    _clear_reported_position_stall_stamp()
                    apple_tv_dashboard_track["last_poll_ok"] = None
                    apple_tv_dashboard_track["consecutive_fail"] = 0
                    _sync_status_bar_visibility_for_playback(None)
                if playback_overlay_widget is not None:
                    playback_overlay_widget.clear_cache()
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _remove_receiver_device_at(for_location_id: str, index: int) -> None:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            if not messagebox.askyesno(
                "Remove Receiver",
                "Remove this Receiver entry from this location?",
                parent=root,
            ):
                return
            lid = str(for_location_id or "").strip()
            remove_device_at_slot_index("av_receiver", int(index), for_location_id=lid or None)
            cur = read_current_location_id()
            if lid and cur and lid == cur:
                avr_slot_holder[0] = read_saved_av_receiver()
                if avr_slot_holder[0] is None:
                    clear_last_receiver()
                    receiver_http_host["host"] = ""
                else:
                    av2 = avr_slot_holder[0]
                    adr = str(av2.get("address") or "").strip()
                    if adr:
                        write_last_receiver(
                            host=adr,
                            name=str(av2.get("name") or "").strip() or None,
                            label=str(av2.get("label") or "").strip() or None,
                            device_id=str(av2.get("identifier") or "").strip() or None,
                        )
                        receiver_http_host["host"] = adr
                if playback_overlay_widget is not None:
                    playback_overlay_widget.clear_cache()
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _remove_saved_receiver_device(for_location_id: str | None = None) -> None:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            if not messagebox.askyesno(
                "Remove Receiver",
                "Remove the saved Receiver device and stop the overlay status poll for it?",
                parent=root,
            ):
                return
            lid = (for_location_id or read_current_location_id() or "").strip()
            write_saved_av_receiver(None, for_location_id=lid or None)
            cur = read_current_location_id()
            if lid and cur and lid == cur:
                avr_slot_holder[0] = None
                clear_last_receiver()
                receiver_http_host["host"] = ""
                if playback_overlay_widget is not None:
                    playback_overlay_widget.clear_cache()
                try:
                    _warm_playback_overlay_blits()
                except Exception:
                    pass
                nonlocal skip_cache
                skip_cache = None
                try:
                    render_once()
                except Exception:
                    pass
            else:
                avr_slot_holder[0] = read_saved_av_receiver()
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _paired_box_close_button(parent: tk.Frame, bg: str, command: object) -> tk.Button:
            return tk.Button(
                parent,
                text="\u00d7",
                command=command,
                font=(_S, 14, "normal"),
                fg="#888",
                bg=bg,
                activebackground=bg,
                activeforeground="#f0f0f0",
                bd=0,
                padx=6,
                pady=0,
                highlightthickness=0,
                cursor="hand2",
            )

        def _settings_parse_device_rows(raw: object) -> list[dict[str, str]]:
            """Normalize a location slot (list or legacy dict) to filled device rows."""
            if isinstance(raw, list):
                out: list[dict[str, str]] = []
                for x in raw:
                    if not isinstance(x, dict):
                        continue
                    if str(x.get("identifier") or "").strip() and str(x.get("address") or "").strip():
                        out.append(dict(x))
                return out
            if isinstance(raw, dict):
                d = dict(raw)
                if str(d.get("identifier") or "").strip() and str(d.get("address") or "").strip():
                    return [d]
            return []

        def _settings_parse_receiver_rows(raw: object) -> list[dict[str, str]]:
            """Receivers: show any row with a host/IP (identifier optional for legacy)."""
            if isinstance(raw, list):
                return [
                    dict(x)
                    for x in raw
                    if isinstance(x, dict) and str(x.get("address") or "").strip()
                ]
            if isinstance(raw, dict) and str(raw.get("address") or "").strip():
                return [dict(raw)]
            return []

        def _remove_aux_slot_device_at(
            for_location_id: str,
            slot_key: str,
            index: int,
            *,
            role_title: str,
        ) -> None:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            if not messagebox.askyesno(
                f"Remove {role_title}",
                f"Remove this {role_title} entry from this location?",
                parent=root,
            ):
                return
            lid = str(for_location_id or "").strip()
            remove_device_at_slot_index(slot_key, int(index), for_location_id=lid or None)
            _apply_persisted_location_to_runtime()
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()
            nonlocal skip_cache
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass

        def _rebuild_paired_devices_panel() -> None:
            paired_ui_leds["remote"] = None
            paired_ui_leds["airplay"] = None
            paired_ui_leds["receiver"] = None
            paired_observed_led_rows.clear()
            paired_observed_led_last_state.clear()
            receiver_panel_led_holder[0] = None
            for ch in list(paired_devices_inner.winfo_children()):
                try:
                    ch.destroy()
                except tk.TclError:
                    pass
            locs = read_all_locations_v2()
            cur_id = read_current_location_id()
            if not locs:
                tk.Label(
                    paired_devices_inner,
                    text="No locations yet — use Find device to add a Player and pick a room.",
                    fg="#555",
                    bg="#111",
                    font=S_FONT_SMALL,
                ).pack(anchor=tk.W)
                return

            tk.Label(
                paired_devices_inner,
                text="Locations & saved devices",
                fg="#aaa",
                bg="#111",
                font=S_FONT_SEC,
            ).pack(anchor=tk.W, pady=(0, 4))

            # Per location: every saved role row is listed (same IP can appear as Player and TV, etc.).
            visible_locs: list[
                tuple[str, str, bool, dict[str, list[dict[str, str]]]]
            ] = []
            for loc in locs:
                loc_id = str(loc.get("id") or "")
                loc_name = str(loc.get("name") or "Room").strip()
                is_cur = bool(cur_id and loc_id == cur_id)
                slots: dict[str, list[dict[str, str]]] = {
                    "streaming": _settings_parse_device_rows(loc.get("streaming")),
                    "av_receiver": _settings_parse_receiver_rows(loc.get("av_receiver")),
                    "tv": _settings_parse_device_rows(loc.get("tv")),
                    "projector": _settings_parse_device_rows(loc.get("projector")),
                    "game": _settings_parse_device_rows(loc.get("game")),
                    "other": _settings_parse_device_rows(loc.get("other")),
                }
                if not any(slots.values()):
                    continue
                visible_locs.append((loc_id, loc_name, is_cur, slots))

            if not visible_locs:
                tk.Label(
                    paired_devices_inner,
                    text="No devices saved in any location yet — use Find device.",
                    fg="#555",
                    bg="#111",
                    font=S_FONT_SMALL,
                ).pack(anchor=tk.W)
                return

            loc_grid = tk.Frame(paired_devices_inner, bg="#111")
            loc_grid.pack(fill=tk.BOTH, expand=True)
            loc_grid.columnconfigure(0, weight=1, uniform="locpair")
            loc_grid.columnconfigure(1, weight=1, uniform="locpair")

            _aux_role_rows_ui: tuple[tuple[str, str, str, str, str], ...] = (
                ("tv", "TV", "#1a2520", "#3a5c4a", "#8fd4a8"),
                ("projector", "Projector", "#25201a", "#5c543a", "#d4c48f"),
                ("game", "Game", "#1a1f28", "#3a445c", "#9ab8ff"),
                ("other", "Other", "#222228", "#444454", "#c0c0d0"),
            )

            for idx, (loc_id, loc_name, is_cur, slots) in enumerate(visible_locs):
                stream_rows = slots["streaming"]
                av_rows = slots["av_receiver"]
                row, col = divmod(idx, 2)
                padx_g = (0, 5) if col == 0 else (5, 0)
                outer_bd = "#4a6a8a" if is_cur else "#3a3a44"
                outer = tk.Frame(
                    loc_grid,
                    bg="#15151c",
                    highlightthickness=1,
                    highlightbackground=outer_bd,
                )
                outer.grid(row=row, column=col, sticky="ew", padx=padx_g, pady=(0, 10))
                head_loc = tk.Frame(outer, bg="#15151c")
                head_loc.pack(fill=tk.X, padx=8, pady=(6, 4))
                sub = f"{loc_name}  (active)" if is_cur else loc_name
                tk.Label(
                    head_loc,
                    text=sub,
                    fg="#8ec8ff" if is_cur else "#aaa",
                    bg="#15151c",
                    font=S_FONT_CAP_BOLD,
                ).pack(side=tk.LEFT)

                inner = tk.Frame(outer, bg="#15151c")
                inner.pack(fill=tk.X, padx=6, pady=(0, 6))

                for si, st in enumerate(stream_rows):
                    nick = str(st.get("nickname") or "").strip()
                    nm = nick or str(st.get("label") or st.get("name") or "Device").strip()
                    ip = str(st.get("address") or "").strip()
                    slot_tag = f" #{si + 1}" if len(stream_rows) > 1 else ""
                    use_live = bool(is_cur and si == 0)
                    if row_is_playback_apple_tv(st):
                        grp = tk.Frame(
                            inner,
                            bg="#1a1a24",
                            highlightthickness=1,
                            highlightbackground="#333",
                        )
                        grp.pack(fill=tk.X, pady=(0, 6))

                        def _player_row(
                            parent: tk.Frame,
                            led_key: str,
                            subtitle: str,
                            *,
                            use_live_leds: bool,
                            grp_bg: str,
                            ip_s: str,
                        ) -> None:
                            line = tk.Frame(parent, bg=grp_bg)
                            line.pack(fill=tk.X, padx=8, pady=(2, 2))
                            cv = tk.Canvas(line, width=14, height=18, bg=grp_bg, highlightthickness=0, bd=0)
                            cv.pack(side=tk.LEFT)
                            # Never leave this blank while waiting for the first live poll.
                            _paint_cred_led_canvas(cv, False)
                            if use_live_leds:
                                paired_ui_leds[led_key] = cv
                            tk.Label(
                                line,
                                text=f"{subtitle}  ·  {ip_s}",
                                fg="#bbb",
                                bg=grp_bg,
                                font=S_FONT_SMALL,
                            ).pack(side=tk.LEFT, padx=(6, 0))

                        head = tk.Frame(grp, bg="#1a1a24")
                        head.pack(fill=tk.X, padx=8, pady=(6, 2))
                        _paired_box_close_button(
                            head,
                            "#1a1a24",
                            lambda lid=loc_id, ix=si: _remove_streaming_device_at(lid, ix),
                        ).pack(side=tk.RIGHT, padx=(8, 0))
                        tk.Label(head, text="Player", fg="#7eb8ff", bg="#1a1a24", font=S_FONT_CAP_BOLD).pack(
                            side=tk.LEFT
                        )
                        tk.Label(head, text=f"{slot_tag}  {nm}", fg="#ccc", bg="#1a1a24", font=S_FONT_SMALL).pack(
                            side=tk.LEFT
                        )
                        _player_row(
                            grp,
                            "remote",
                            "AppleTV Remote",
                            use_live_leds=use_live,
                            grp_bg="#1a1a24",
                            ip_s=ip,
                        )
                        _player_row(
                            grp,
                            "airplay",
                            "AppleTV AirPlay",
                            use_live_leds=use_live,
                            grp_bg="#1a1a24",
                            ip_s=ip,
                        )
                    else:
                        grp_o = tk.Frame(
                            inner,
                            bg="#1c1c20",
                            highlightthickness=1,
                            highlightbackground="#3a3a44",
                        )
                        grp_o.pack(fill=tk.X, pady=(0, 6))
                        head_o = tk.Frame(grp_o, bg="#1c1c20")
                        head_o.pack(fill=tk.X, padx=8, pady=(6, 6))
                        _paired_box_close_button(
                            head_o,
                            "#1c1c20",
                            lambda lid=loc_id, ix=si: _remove_streaming_device_at(lid, ix),
                        ).pack(side=tk.RIGHT, padx=(8, 0))
                        tk.Label(head_o, text="Player", fg="#7eb8ff", bg="#1c1c20", font=S_FONT_CAP_BOLD).pack(
                            side=tk.LEFT
                        )
                        line_o = tk.Frame(grp_o, bg="#1c1c20")
                        line_o.pack(fill=tk.X, padx=8, pady=(2, 6))
                        cv_o = tk.Canvas(line_o, width=14, height=18, bg="#1c1c20", highlightthickness=0, bd=0)
                        cv_o.pack(side=tk.LEFT)
                        _paint_cred_led_canvas(cv_o, False)
                        if is_cur:
                            paired_observed_led_rows.append((cv_o, str(loc_id), dict(st)))
                        tk.Label(
                            line_o,
                            text=f"{slot_tag}  {nm}  ·  {ip}",
                            fg="#aaa",
                            bg="#1c1c20",
                            font=S_FONT_SMALL,
                        ).pack(side=tk.LEFT, padx=(6, 0))
                for ai, av in enumerate(av_rows):
                    grp_r = tk.Frame(
                        inner,
                        bg="#221a22",
                        highlightthickness=1,
                        highlightbackground="#5a3d5a",
                    )
                    grp_r.pack(fill=tk.X, pady=(0, 2))
                    head_r = tk.Frame(grp_r, bg="#221a22")
                    head_r.pack(fill=tk.X, padx=8, pady=(6, 2))
                    _paired_box_close_button(
                        head_r,
                        "#221a22",
                        lambda lid=loc_id, ix=ai: _remove_receiver_device_at(lid, ix),
                    ).pack(side=tk.RIGHT, padx=(8, 0))
                    tk.Label(head_r, text="Receiver", fg="#c9a0ff", bg="#221a22", font=S_FONT_CAP_BOLD).pack(
                        side=tk.LEFT
                    )
                    rnick = str(av.get("nickname") or "").strip()
                    an = rnick or str(av.get("label") or av.get("name") or "Receiver").strip()
                    aip = str(av.get("address") or "").strip()
                    rtag = f" #{ai + 1}" if len(av_rows) > 1 else ""
                    line = tk.Frame(grp_r, bg="#221a22")
                    line.pack(fill=tk.X, padx=8, pady=(2, 6))
                    cv_r = tk.Canvas(line, width=14, height=18, bg="#221a22", highlightthickness=0, bd=0)
                    cv_r.pack(side=tk.LEFT)
                    if is_cur and ai == 0:
                        paired_ui_leds["receiver"] = cv_r
                        receiver_panel_led_holder[0] = cv_r
                        _paint_boolean_led(cv_r, False)
                    else:
                        _paint_cred_led_canvas(cv_r, False)
                    tk.Label(
                        line,
                        text=f"{rtag}  {an}  ·  {aip}",
                        fg="#bbb",
                        bg="#221a22",
                        font=S_FONT_SMALL,
                    ).pack(side=tk.LEFT, padx=(6, 0))

                for slot_key, role_title, bg_c, bd_c, title_c in _aux_role_rows_ui:
                    aux_rows = slots.get(slot_key) or []
                    for ri, arow in enumerate(aux_rows):
                        nick_a = str(arow.get("nickname") or "").strip()
                        nm_a = nick_a or str(arow.get("label") or arow.get("name") or role_title).strip()
                        ip_a = str(arow.get("address") or "").strip()
                        tag_a = f" #{ri + 1}" if len(aux_rows) > 1 else ""
                        grp_a = tk.Frame(
                            inner,
                            bg=bg_c,
                            highlightthickness=1,
                            highlightbackground=bd_c,
                        )
                        grp_a.pack(fill=tk.X, pady=(0, 4))
                        head_a = tk.Frame(grp_a, bg=bg_c)
                        head_a.pack(fill=tk.X, padx=8, pady=(6, 6))
                        _paired_box_close_button(
                            head_a,
                            bg_c,
                            lambda lid=loc_id, sk=slot_key, ix=ri, rt=role_title: _remove_aux_slot_device_at(
                                lid, sk, ix, role_title=rt
                            ),
                        ).pack(side=tk.RIGHT, padx=(8, 0))
                        tk.Label(
                            head_a,
                            text=role_title,
                            fg=title_c,
                            bg=bg_c,
                            font=S_FONT_CAP_BOLD,
                        ).pack(side=tk.LEFT)
                        line_a = tk.Frame(grp_a, bg=bg_c)
                        line_a.pack(fill=tk.X, padx=8, pady=(2, 6))
                        cv_a = tk.Canvas(line_a, width=14, height=18, bg=bg_c, highlightthickness=0, bd=0)
                        cv_a.pack(side=tk.LEFT)
                        _paint_cred_led_canvas(cv_a, False)
                        if is_cur:
                            paired_observed_led_rows.append((cv_a, str(loc_id), dict(arow)))
                        tk.Label(
                            line_a,
                            text=f"{tag_a}  {nm_a}  ·  {ip_a}",
                            fg="#ccc",
                            bg=bg_c,
                            font=S_FONT_SMALL,
                        ).pack(side=tk.LEFT, padx=(6, 0))

        def _seed_current_apple_tv_from_streaming_slot() -> None:
            """Keep the poll target in sync with the location's saved player.

            ``current_apple_tv`` is initialized from ``last_apple_tv``. That key
            can be missing (Mac state after a location-only save, or a raced
            write) even when FAMILY ROOM still has Downstairs AppleTV. Without
            an identifier the poll tick returns immediately and the inspector
            / now-playing stay empty.
            """
            row = streaming_slot_holder[0]
            if not isinstance(row, dict):
                return
            ident = str(row.get("identifier") or "").strip()
            if not ident:
                return
            if str(current_apple_tv.get("identifier") or "").strip() == ident:
                if not str(current_apple_tv.get("address") or "").strip():
                    current_apple_tv["address"] = str(row.get("address") or "").strip()
                return
            current_apple_tv.clear()
            current_apple_tv.update(
                {
                    "identifier": ident,
                    "address": str(row.get("address") or "").strip(),
                    "name": str(row.get("name") or "").strip(),
                    "label": str(row.get("label") or "").strip(),
                }
            )
            write_last_apple_tv(
                identifier=ident,
                address=str(row.get("address") or "").strip(),
                name=str(row.get("name") or "").strip() or None,
                label=str(row.get("label") or "").strip() or None,
            )

        def describe_current_apple_tv(*, suffix: str | None = None) -> None:
            if current_apple_tv.get("name"):
                play = f'Playback: {current_apple_tv["name"]}'
            elif current_apple_tv.get("label"):
                play = f'Playback: {current_apple_tv["label"]}'
            else:
                play = "Playback: none"
            rh = str(receiver_http_host.get("host") or "").strip()
            if rh:
                lr = read_last_receiver()
                nm = str(lr.get("name") or lr.get("label") or rh).strip() or rh
                ov = f"Overlay: {nm}"
            else:
                ov = "Overlay: none"
            base = f"{play}  ·  {ov}"
            if suffix:
                base = f"{base} ({suffix})"
            apple_tv_status_var.set(base)
            _refresh_content_indicator()

        def set_apple_tv_controls_enabled(enabled: bool) -> None:
            state = tk.NORMAL if enabled else tk.DISABLED
            try:
                find_device_btn.configure(state=state)
                _m = tmdb_adv_manual_btn_holder[0]
                if _m is not None:
                    _m.configure(state=state)
                _r = tmdb_adv_report_btn_holder[0]
                if _r is not None:
                    _r.configure(state=state)
                purge_image_media_btn.configure(state=state)
                _fdb = settings_footer_debug_holder[0]
                if _fdb is not None:
                    _fdb.configure(state=state)
                _frb = settings_footer_reset_holder[0]
                if _frb is not None:
                    _frb.configure(state=state)
                root.configure(cursor="" if enabled else "watch")
            except tk.TclError:
                pass

        def begin_apple_tv_operation(status_suffix: str) -> bool:
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return False
            apple_tv_busy["active"] = True
            set_apple_tv_controls_enabled(False)
            describe_current_apple_tv(suffix=status_suffix)
            return True

        def end_apple_tv_operation(*, suffix: str | None = None) -> None:
            apple_tv_busy["active"] = False
            set_apple_tv_controls_enabled(True)
            describe_current_apple_tv(suffix=suffix)

        def set_current_apple_tv(row: dict[str, str], *, persist: bool) -> None:
            nonlocal last_atv_interaction_mono, _atv_ix_sig_ds, _atv_ix_sig_ck
            nonlocal _atv_ix_pos, _atv_ix_pos_mono, _atv_ix_extrap_playing, _atv_ix_prev_idle
            current_apple_tv.clear()
            current_apple_tv.update(
                {
                    "identifier": row.get("identifier", ""),
                    "address": row.get("address", ""),
                    "name": row.get("name", ""),
                    "label": row.get("label", ""),
                }
            )
            if persist:
                write_last_apple_tv(
                    identifier=row.get("identifier", ""),
                    address=row.get("address", ""),
                    name=row.get("name"),
                    label=row.get("label"),
                )
            apple_tv_auto_state["content_key"] = None
            apple_tv_auto_state["tmdb_key"] = None
            apple_tv_auto_state["query"] = None
            apple_tv_auto_state["last_metadata"] = None
            apple_tv_auto_state["last_tmdb_fetch_input"] = None
            apple_tv_auto_state["last_tmdb_fetch_refined"] = None
            apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
            apple_tv_playback_clock.clear()
            apple_tv_playback_clock.update(
                {
                    "has_sync": False,
                    "sync_mono": 0.0,
                    "sync_position": 0.0,
                    "live_mode": False,
                    "playing": False,
                    "latched_total": None,
                    "latched_content_key": None,
                    "last_reported_total": None,
                    "display_played_sec": None,
                    "trt_next_fire_mono": None,
                }
            )
            _clear_reported_position_stall_stamp()
            apple_tv_dashboard_track["last_poll_ok"] = None
            apple_tv_dashboard_track["consecutive_fail"] = 0
            last_atv_interaction_mono = 0.0
            _atv_ix_sig_ds = ""
            _atv_ix_sig_ck = None
            _atv_ix_pos = None
            _atv_ix_pos_mono = time.monotonic()
            _atv_ix_extrap_playing = False
            _atv_ix_prev_idle = True
            _reset_clock_saver_device_signal_baseline()
            _sync_status_bar_visibility_for_playback(None)
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def set_current_receiver_only(row: dict[str, str], *, persist: bool = True) -> None:
            """Persist AVR / AirPlay-only row for Denon HTTP overlay only; does not change Apple TV playback."""
            adr = str(row.get("address") or "").strip()
            if not adr:
                return
            if persist:
                write_last_receiver(
                    host=adr,
                    name=str(row.get("name") or "").strip() or None,
                    label=str(row.get("label") or "").strip() or None,
                    device_id=str(row.get("identifier") or "").strip() or None,
                )
            receiver_http_host["host"] = adr
            if playback_overlay_widget is not None:
                playback_overlay_widget.clear_cache()
            try:
                _warm_playback_overlay_blits()
            except Exception:
                pass
            nonlocal skip_cache
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _apply_persisted_location_to_runtime() -> None:
            """Reload holders and runtime targets from the persisted current location."""
            nonlocal last_atv_interaction_mono, _atv_ix_sig_ds, _atv_ix_sig_ck
            nonlocal _atv_ix_pos, _atv_ix_pos_mono, _atv_ix_extrap_playing, _atv_ix_prev_idle
            nonlocal skip_cache
            streaming_slot_holder[0] = read_saved_streaming_device()
            avr_slot_holder[0] = read_saved_av_receiver()
            st2 = streaming_slot_holder[0]
            av2 = avr_slot_holder[0]
            if st2:
                current_apple_tv.clear()
                current_apple_tv.update(
                    {
                        "identifier": st2.get("identifier", ""),
                        "address": st2.get("address", ""),
                        "name": st2.get("name", ""),
                        "label": st2.get("label", ""),
                    }
                )
                write_last_apple_tv(
                    identifier=st2.get("identifier", ""),
                    address=st2.get("address", ""),
                    name=st2.get("name"),
                    label=st2.get("label"),
                )
            else:
                clear_last_apple_tv()
                current_apple_tv.clear()
                current_apple_tv.update({"identifier": "", "address": "", "name": "", "label": ""})
            apple_tv_auto_state["content_key"] = None
            apple_tv_auto_state["tmdb_key"] = None
            apple_tv_auto_state["query"] = None
            apple_tv_auto_state["last_metadata"] = None
            apple_tv_auto_state["last_tmdb_fetch_input"] = None
            apple_tv_auto_state["last_tmdb_fetch_refined"] = None
            apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
            apple_tv_playback_clock.clear()
            apple_tv_playback_clock.update(
                {
                    "has_sync": False,
                    "sync_mono": 0.0,
                    "sync_position": 0.0,
                    "live_mode": False,
                    "playing": False,
                    "latched_total": None,
                    "latched_content_key": None,
                    "last_reported_total": None,
                    "display_played_sec": None,
                    "trt_next_fire_mono": None,
                }
            )
            _clear_reported_position_stall_stamp()
            apple_tv_dashboard_track["last_poll_ok"] = None
            apple_tv_dashboard_track["consecutive_fail"] = 0
            last_atv_interaction_mono = 0.0
            _atv_ix_sig_ds = ""
            _atv_ix_sig_ck = None
            _atv_ix_pos = None
            _atv_ix_pos_mono = time.monotonic()
            _atv_ix_extrap_playing = False
            _atv_ix_prev_idle = True
            _reset_clock_saver_device_signal_baseline()
            if av2:
                adr = str(av2.get("address") or "").strip()
                if adr:
                    write_last_receiver(
                        host=adr,
                        name=str(av2.get("name") or "").strip() or None,
                        label=str(av2.get("label") or "").strip() or None,
                        device_id=str(av2.get("identifier") or "").strip() or None,
                    )
                    receiver_http_host["host"] = adr
            else:
                clear_last_receiver()
                receiver_http_host["host"] = ""
            if playback_overlay_widget is not None:
                playback_overlay_widget.clear_cache()
            try:
                _warm_playback_overlay_blits()
            except Exception:
                pass
            skip_cache = None
            _start_location_toast()
            _sync_status_bar_visibility_for_playback(None)
            try:
                render_once()
            except Exception:
                pass
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()

        def _refresh_location_selector() -> None:
            for w in location_om_frame.winfo_children():
                try:
                    w.destroy()
                except tk.TclError:
                    pass
            locs = read_all_locations_v2()
            labels: list[str] = []
            ids: list[str] = []
            counts: dict[str, int] = {}
            for L in locs:
                base = str(L.get("name") or "Room").strip() or "Room"
                counts[base] = counts.get(base, 0) + 1
                c = counts[base]
                lab = base if c == 1 else f"{base} ({c})"
                labels.append(lab)
                ids.append(str(L.get("id") or ""))
            labels.append("+ Custom location…")
            ids.append("__custom__")
            cur_id = read_current_location_id()

            def _pick(label_val: str) -> None:
                if label_val == "+ Custom location…":
                    name = simpledialog.askstring(
                        "Custom location",
                        "Name for this location:",
                        parent=root,
                    )
                    if name and str(name).strip():
                        add_empty_location_v2(str(name).strip())
                        _apply_persisted_location_to_runtime()
                    _refresh_location_selector()
                    return
                idx = labels.index(label_val) if label_val in labels else -1
                if idx < 0 or idx >= len(ids):
                    return
                lid = ids[idx]
                if lid == "__custom__":
                    return
                if lid == read_current_location_id():
                    return
                if lid and set_current_location_id(lid):
                    _apply_persisted_location_to_runtime()
                    _refresh_location_selector()

            if not locs:
                location_menu_var.set("+ Custom location…")

                def _pick_empty(val: str) -> None:
                    if val == "+ Custom location…":
                        name = simpledialog.askstring(
                            "Custom location",
                            "Name for this location:",
                            parent=root,
                        )
                        if name and str(name).strip():
                            add_empty_location_v2(str(name).strip())
                            _apply_persisted_location_to_runtime()
                        _refresh_location_selector()

                om = tk.OptionMenu(
                    location_om_frame,
                    location_menu_var,
                    "+ Custom location…",
                    command=_pick_empty,
                )
                om.pack(side=tk.LEFT)
                location_option_holder[0] = om
                location_name_var.set("")
                try:
                    location_name_entry.configure(state=tk.DISABLED)
                    rename_name_btn.configure(state=tk.DISABLED)
                    delete_location_btn.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
                return

            cur_label = labels[0]
            if cur_id:
                for i, xid in enumerate(ids):
                    if xid == cur_id and i < len(labels) - 1:
                        cur_label = labels[i]
                        break
            location_menu_var.set(cur_label)
            om = tk.OptionMenu(location_om_frame, location_menu_var, *labels, command=_pick)
            om.pack(side=tk.LEFT)
            location_option_holder[0] = om
            cid_nm = (read_current_location_id() or "").strip()
            raw_nm = ""
            if cid_nm:
                for L in read_all_locations_v2():
                    if str(L.get("id") or "").strip() == cid_nm:
                        raw_nm = str(L.get("name") or "Room").strip() or "Room"
                        break
            location_name_var.set(raw_nm)
            try:
                location_name_entry.configure(state=tk.NORMAL if cid_nm else tk.DISABLED)
                rename_name_btn.configure(state=tk.NORMAL if cid_nm else tk.DISABLED)
                delete_location_btn.configure(
                    state=tk.NORMAL if len(locs) > 1 and cid_nm else tk.DISABLED
                )
            except tk.TclError:
                pass

        def _on_delete_current_location() -> None:
            locs = read_all_locations_v2()
            if len(locs) <= 1:
                messagebox.showinfo(
                    "Delete location",
                    "You cannot delete the only location. Add another room first, then remove this one.",
                    parent=root,
                )
                return
            cid = (read_current_location_id() or "").strip()
            if not cid:
                return
            nm = "this location"
            for L in locs:
                if str(L.get("id") or "").strip() == cid:
                    nm = str(L.get("name") or "Room").strip() or "Room"
                    break
            if not messagebox.askyesno(
                "Delete location",
                f'Delete "{nm}" and remove all saved devices and Advanced (delegation) data '
                f"for that room?\n\nThis cannot be undone.",
                parent=root,
            ):
                return
            try:
                from pigeon.observed_capability import clear_observed_capabilities_for_location

                if not delete_location_v2(cid):
                    messagebox.showerror(
                        "Delete location",
                        "Could not delete that location.",
                        parent=root,
                    )
                    return
                clear_observed_capabilities_for_location(cid)
            except Exception as e:
                messagebox.showerror("Delete location", str(e), parent=root)
                return
            _apply_persisted_location_to_runtime()
            _refresh_location_selector()
            _start_location_toast()
            messagebox.showinfo("Delete location", f'"{nm}" was deleted.')

        delete_location_btn.configure(command=_on_delete_current_location)

        def _refresh_observed_pairing_led_rows() -> None:
            if not paired_observed_led_rows:
                return
            try:
                from device_capability_matrix import device_row_stable_key
            except Exception:
                for cv, _lid, _row in paired_observed_led_rows:
                    try:
                        _paint_cred_led_canvas(cv, False)
                    except tk.TclError:
                        pass
                return
            try:
                app_state = read_app_state()
            except Exception:
                app_state = {}
            observed_store_by_loc: dict[str, dict[str, object]] = {}
            for cv, lid, row in paired_observed_led_rows:
                try:
                    tri_state: bool | None = False
                    lid_s = str(lid or "").strip()
                    if lid_s:
                        if lid_s not in observed_store_by_loc:
                            raw_blob = app_state.get(f"observed_capability_live_v1.{lid_s}")
                            observed_store_by_loc[lid_s] = raw_blob if isinstance(raw_blob, dict) else {}
                        blob = observed_store_by_loc.get(lid_s, {})
                        sk = str(device_row_stable_key(row) or "").strip()
                        if sk:
                            feats = blob.get(sk) if isinstance(blob, dict) else None
                            if isinstance(feats, dict) and feats:
                                has_full = any(
                                    isinstance(v, str) and v == "full" for v in feats.values()
                                )
                                tri_state = True if has_full else None
                    k = id(cv)
                    if paired_observed_led_last_state.get(k, "__missing__") == tri_state:
                        continue
                    _paint_cred_led_canvas(cv, tri_state)
                    paired_observed_led_last_state[k] = tri_state
                except tk.TclError:
                    pass

        def _schedule_refresh_pairing_leds() -> None:

            stream_led = streaming_row_led_canvas_holder[0]
            # Extra pyatv scans here during discover/pair overlap the TV; refresh after busy clears instead.
            if apple_tv_busy["active"]:
                return
            if not _PIGEON_EXT:
                if stream_led is not None:
                    try:
                        _paint_boolean_led(stream_led, False)
                    except tk.TclError:
                        pass
                _paint_pair_led(0, False)
                _paint_pair_led(1, False)
                _refresh_observed_pairing_led_rows()
                return
            if pair_led_busy["active"]:
                if not _pair_led_pending_retry[0]:
                    _pair_led_pending_retry[0] = True

                    def _retry_pair_leds() -> None:
                        _pair_led_pending_retry[0] = False
                        _schedule_refresh_pairing_leds()

                    root.after(120, _retry_pair_leds)
                return
            pair_led_busy["active"] = True
            row_snap = streaming_slot_holder[0]

            def work() -> None:
                comp_sel, air_sel = False, False
                both_ok = False
                if row_snap:
                    try:
                        from pigeon.apple_tv_now_playing import apple_tv_pairing_credentials_status

                        c, a = apple_tv_pairing_credentials_status(
                            device_identifier=str(row_snap.get("identifier", "")),
                            device_address=str(row_snap.get("address", "")),
                        )
                        comp_sel, air_sel = bool(c), bool(a)
                        both_ok = comp_sel and air_sel
                    except Exception:
                        comp_sel, air_sel = False, False

                def apply_leds() -> None:
                    pair_led_busy["active"] = False
                    lpo = apple_tv_dashboard_track.get("last_poll_ok")
                    cf = int(apple_tv_dashboard_track.get("consecutive_fail", 0) or 0)
                    poll_unhealthy = lpo is False and cf >= 1

                    def _cred_led(has_cred: bool) -> bool | None:
                        if not has_cred:
                            return False
                        if poll_unhealthy:
                            return None
                        return True

                    stream_tri: bool | None = False
                    if row_snap and comp_sel and air_sel:
                        stream_tri = None if poll_unhealthy else True
                    elif row_snap and (comp_sel or air_sel):
                        stream_tri = False
                    if stream_led is not None:
                        try:
                            _paint_boolean_led(stream_led, stream_tri)
                        except tk.TclError:
                            pass
                    _paint_pair_led(0, _cred_led(comp_sel))
                    _paint_pair_led(1, _cred_led(air_sel))
                    pr = paired_ui_leds.get("remote")
                    pa = paired_ui_leds.get("airplay")
                    if pr is not None:
                        _paint_cred_led_canvas(pr, _cred_led(comp_sel))
                    if pa is not None:
                        _paint_cred_led_canvas(pa, _cred_led(air_sel))
                    _refresh_observed_pairing_led_rows()
                    if main_settings_widget is not None:
                        try:
                            st_led = main_settings_widget.state
                            if st_led.show_pigeon_settings:
                                meta_ok = bool(_content_indicator_ok())
                                if st_led.pigeon_metadata_ok != meta_ok:
                                    st_led.pigeon_metadata_ok = meta_ok
                                    main_settings_widget.invalidate()
                        except Exception:
                            pass

                root.after(0, apply_leds)

            threading.Thread(target=work, daemon=True).start()

        def _device_addr_key(addr: str) -> str:
            s = str(addr or "").strip().lower()
            if not s:
                return ""
            if s.startswith("["):
                return s
            if s.count(":") == 1:
                left, right = s.rsplit(":", 1)
                if right.isdigit():
                    return left
            return s.split("%")[0]

        def _device_row_matches_saved(row: dict[str, str], saved: dict[str, str]) -> bool:
            ri = str(row.get("identifier") or "").strip()
            si = str(saved.get("identifier") or "").strip()
            if ri and si and ri == si:
                return True
            ra = _device_addr_key(str(row.get("address") or ""))
            sa = _device_addr_key(str(saved.get("address") or ""))
            return bool(ra and sa and ra == sa)

        def _verify_added_devices_after_save(added: list[dict[str, str]]) -> None:
            if not added:
                return

            def work() -> None:
                bad: list[str] = []
                for row in added:
                    addr = str(row.get("address") or "").strip()
                    if not addr:
                        continue
                    try:
                        from pigeon.apple_tv_now_playing import probe_pyatv_host

                        ok_w, msg_w, _, _ = probe_pyatv_host(addr, scan_timeout_s=6)
                    except Exception as e:
                        ok_w, msg_w = False, str(e)
                    if not ok_w:
                        label = str(row.get("label") or row.get("name") or addr)
                        tail = (msg_w or "no response")[:160]
                        bad.append(f"• {label} ({addr}): {tail}")

                def done() -> None:
                    if bad:
                        messagebox.showwarning(
                            "Device check",
                            "After saving, a quick follow-up scan could not reach some new entries "
                            "(sleeping, offline, or firewalled):\n\n" + "\n".join(bad),
                            parent=root,
                        )

                root.after(0, done)

            threading.Thread(target=work, daemon=True).start()

        describe_current_apple_tv()
        _refresh_location_selector()
        _rebuild_paired_devices_panel()

        def _ask_pairing_pin_modal(
            parent: tk.Misc,
            *,
            title: str,
            device_name: str,
            pair_kind: str,
            session_key: str | None,
        ) -> str | None:
            out: list[str | None] = [None]
            closed = [False]
            dlg = tk.Toplevel(parent)
            dlg.title(title)
            dlg.configure(bg="#1a1a1e")
            try:
                dlg.transient(root)
                dlg.grab_set()
            except tk.TclError:
                pass
            tk.Label(
                dlg,
                text=f"{pair_kind}\nDevice: {device_name}\nEnter the 4-digit code shown on the television.",
                fg="#ddd",
                bg="#1a1a1e",
                font=S_FONT_BODY,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=14, pady=(12, 8))
            pin_var = tk.StringVar(value="")

            def _close() -> None:
                if closed[0]:
                    return
                closed[0] = True
                try:
                    dlg.grab_release()
                except tk.TclError:
                    pass
                dlg.destroy()

            ent = tk.Entry(
                dlg,
                textvariable=pin_var,
                width=12,
                font=("Menlo", 18) if sys.platform == "darwin" else ("Consolas", 18),
                justify=tk.CENTER,
                bg="#252528",
                fg="#e8e8e8",
                insertbackground="#e8e8e8",
            )
            ent.pack(padx=14, pady=(0, 8))

            def on_pin_write(*_args: object) -> None:
                raw = pin_var.get()
                d = "".join(c for c in raw if c.isdigit())[:4]
                if raw != d:
                    pin_var.set(d)
                    return
                if len(d) == 4 and not closed[0]:
                    out[0] = d
                    _close()

            pin_var.trace_add("write", on_pin_write)

            def append_digit(ch: str) -> None:
                cur = "".join(c for c in pin_var.get() if c.isdigit())
                if len(cur) < 4:
                    pin_var.set(cur + ch)

            def backspace() -> None:
                cur = "".join(c for c in pin_var.get() if c.isdigit())
                pin_var.set(cur[:-1])

            pad = tk.Frame(dlg, bg="#1a1a1e")
            pad.pack(padx=10, pady=(0, 10))
            for keys in (("1", "2", "3"), ("4", "5", "6"), ("7", "8", "9")):
                rf = tk.Frame(pad, bg="#1a1a1e")
                rf.pack()
                for d in keys:
                    tk.Button(
                        rf,
                        text=d,
                        width=4,
                        command=lambda x=d: append_digit(x),
                        font=S_FONT_BTN,
                    ).pack(side=tk.LEFT, padx=4, pady=4)
            rowz = tk.Frame(pad, bg="#1a1a1e")
            rowz.pack()
            tk.Button(rowz, text="\u232b", width=4, command=backspace, font=S_FONT_BTN).pack(
                side=tk.LEFT, padx=4, pady=4
            )
            tk.Button(rowz, text="0", width=4, command=lambda: append_digit("0"), font=S_FONT_BTN).pack(
                side=tk.LEFT, padx=4, pady=4
            )

            bf = tk.Frame(dlg, bg="#1a1a1e")
            bf.pack(pady=(0, 14))

            def on_ok() -> None:
                d = "".join(c for c in pin_var.get() if c.isdigit())
                if len(d) != 4:
                    messagebox.showwarning("Pairing", "Enter the 4-digit code from the TV.", parent=dlg)
                    return
                out[0] = d
                _close()

            def on_cancel() -> None:
                if session_key:
                    try:
                        from pigeon.apple_tv_now_playing import abandon_pairing_session

                        abandon_pairing_session(session_key)
                    except Exception:
                        pass
                out[0] = None
                _close()

            tk.Button(bf, text="OK", command=on_ok, font=S_FONT_BTN, padx=12, pady=4).pack(
                side=tk.LEFT, padx=8
            )
            tk.Button(bf, text="Cancel", command=on_cancel, font=S_FONT_BTN, padx=12, pady=4).pack(
                side=tk.LEFT, padx=8
            )
            dlg.protocol("WM_DELETE_WINDOW", on_cancel)
            ent.focus_set()
            dlg.wait_window()
            return out[0]

        def _finish_remote_then_start_airplay(row: dict[str, str], dn: str, session_key_w: str, pin: str) -> None:
            # Caller still holds apple_tv_busy from "starting AppleTV Remote" — do not poll until Remote is fully done.

            def worker_remote_finish() -> None:
                try:
                    from pigeon.apple_tv_now_playing import finish_companion_pairing_for_device

                    ok_f, msg_f = finish_companion_pairing_for_device(
                        session_key=session_key_w, pin_code=pin
                    )
                except ImportError:
                    ok_f, msg_f = False, _pyatv_install_hint()
                except Exception as e:
                    ok_f, msg_f = False, str(e)
                if ok_f:
                    time.sleep(1.5)

                def done_rf() -> None:
                    end_apple_tv_operation()
                    if not ok_f:
                        messagebox.showerror("AppleTV Remote", msg_f)
                        _schedule_refresh_pairing_leds()
                        return
                    messagebox.showinfo(
                        "AppleTV Remote",
                        f"{msg_f}\n\n"
                        "Wait until the Apple TV leaves the Remote pairing screen before continuing.",
                    )
                    _schedule_refresh_pairing_leds()
                    _start_airplay_pairing_sequence(row, dn)

                root.after(0, done_rf)

            threading.Thread(target=worker_remote_finish, daemon=True).start()

        def _start_airplay_pairing_sequence(row: dict[str, str], dn: str) -> None:
            if not messagebox.askokcancel(
                "AppleTV AirPlay",
                "Next: AppleTV AirPlay pairing will show a new code on the Apple TV.\n\n"
                "Continue only after the first (Remote) pairing has fully finished on the TV.\n\n"
                "Then open AirPlay / on-screen pairing on the Apple TV so it can show the next code.",
                parent=root,
            ):
                return
            if not begin_apple_tv_operation("starting AppleTV AirPlay"):
                return

            def worker_air_begin() -> None:
                try:
                    from pigeon.apple_tv_now_playing import begin_airplay_pairing_for_device

                    ok_a, msg_a, sk_a, _r2 = begin_airplay_pairing_for_device(
                        device_identifier=row["identifier"],
                        device_address=row["address"],
                        tv_displays_pin=True,
                    )
                except ImportError:
                    ok_a, msg_a, sk_a, _r2 = False, _pyatv_install_hint(), None, None
                except Exception as e:
                    ok_a, msg_a, sk_a, _r2 = False, str(e), None, None

                def ui_air_b() -> None:
                    if not ok_a or not sk_a:
                        end_apple_tv_operation()
                        messagebox.showerror("AppleTV AirPlay", msg_a or "Pairing failed to start.")
                        return
                    # Keep apple_tv_busy True until PIN is submitted so auto-poll cannot start a second connection.
                    describe_current_apple_tv(suffix="enter AirPlay PIN")
                    pin2 = _ask_pairing_pin_modal(
                        root,
                        title="AppleTV AirPlay",
                        device_name=dn,
                        pair_kind="AppleTV AirPlay pairing",
                        session_key=sk_a,
                    )
                    if pin2 is None:
                        end_apple_tv_operation()
                        messagebox.showinfo("AppleTV AirPlay", "Pairing cancelled.")
                        _schedule_refresh_pairing_leds()
                        return

                    def worker_air_finish() -> None:
                        try:
                            from pigeon.apple_tv_now_playing import finish_companion_pairing_for_device

                            ok_af, msg_af = finish_companion_pairing_for_device(
                                session_key=sk_a, pin_code=pin2
                            )
                        except ImportError:
                            ok_af, msg_af = False, _pyatv_install_hint()
                        except Exception as e:
                            ok_af, msg_af = False, str(e)

                        def done_af() -> None:
                            end_apple_tv_operation()
                            if ok_af:
                                messagebox.showinfo("AppleTV AirPlay", msg_af)
                            else:
                                messagebox.showerror("AppleTV AirPlay", msg_af)
                            _schedule_refresh_pairing_leds()

                        root.after(0, done_af)

                    threading.Thread(target=worker_air_finish, daemon=True).start()

                root.after(0, ui_air_b)

            threading.Thread(target=worker_air_begin, daemon=True).start()

        def _run_sequential_player_pairing_wizard(row: dict[str, str]) -> None:
            if not row_is_playback_apple_tv(row):
                return
            dn = str(row.get("name") or row.get("label") or "Apple TV")
            if not messagebox.askokcancel(
                "AppleTV Remote",
                "Pair AppleTV Remote first, then AppleTV AirPlay. Codes appear on the Apple TV.\n\n"
                "On the Apple TV: Settings → Remotes and Devices → Remote App and Devices — keep it open until a code appears.\n\n"
                "Continue?",
                parent=root,
            ):
                return
            if not begin_apple_tv_operation("starting AppleTV Remote"):
                return

            def worker_remote_begin() -> None:
                try:
                    from pigeon.apple_tv_now_playing import begin_companion_pairing_for_device

                    ok_w, msg_w, session_key_w, _rev = begin_companion_pairing_for_device(
                        device_identifier=row["identifier"],
                        device_address=row["address"],
                        tv_displays_pin=True,
                    )
                except ImportError:
                    ok_w, msg_w, session_key_w, _rev = False, _pyatv_install_hint(), None, None
                except Exception as e:
                    ok_w, msg_w, session_key_w, _rev = False, str(e), None, None

                def finish_rb() -> None:
                    if not ok_w or not session_key_w:
                        end_apple_tv_operation()
                        messagebox.showerror("AppleTV Remote", msg_w or "Pairing failed to start.")
                        return
                    # Stay busy through PIN entry so background Apple TV polling cannot connect yet.
                    describe_current_apple_tv(suffix="enter Remote PIN")
                    pin = _ask_pairing_pin_modal(
                        root,
                        title="AppleTV Remote",
                        device_name=dn,
                        pair_kind="AppleTV Remote pairing",
                        session_key=session_key_w,
                    )
                    if pin is None:
                        end_apple_tv_operation()
                        messagebox.showinfo("AppleTV Remote", "Pairing cancelled.")
                        _schedule_refresh_pairing_leds()
                        return
                    _finish_remote_then_start_airplay(row, dn, session_key_w, pin)

                root.after(0, finish_rb)

            threading.Thread(target=worker_remote_begin, daemon=True).start()

        def _save_box_pair_device_row(box_num: int, row: dict[str, str]) -> None:
            from pigeon.app_state import (
                append_device_to_location_slot,
                read_current_location_id,
                read_saved_av_receiver,
                read_saved_streaming_device,
                write_saved_av_receiver,
                write_saved_streaming_device,
            )

            saved = dict(row)
            saved["device_role"] = "player" if box_num == 2 else "receiver"
            lid = read_current_location_id() or None
            if box_num == 2:
                append_device_to_location_slot(
                    "streaming",
                    saved,
                    for_location_id=lid,
                    new_location_name=None,
                )
                write_saved_streaming_device(saved, for_location_id=lid)
                streaming_slot_holder[0] = read_saved_streaming_device()
            else:
                append_device_to_location_slot(
                    "av_receiver",
                    saved,
                    for_location_id=lid,
                    new_location_name=None,
                )
                write_saved_av_receiver(saved, for_location_id=lid)
                avr_slot_holder[0] = read_saved_av_receiver()
            describe_current_apple_tv()
            _rebuild_paired_devices_panel()

        def _handle_main_settings_action(action: str) -> None:
            nonlocal skip_cache
            if main_settings_widget is None:
                return
            st = main_settings_widget.state

            if str(action or "").startswith("source_toggle:"):
                parts = str(action).split(":")
                kind = parts[1] if len(parts) > 1 else ""
                on = parts[2] == "1" if len(parts) > 2 else True
                md = apple_tv_auto_state.get("last_metadata")
                if isinstance(md, dict) and not on:
                    if kind == "metadata":
                        try:
                            from pigeon.source_toggles import strip_streaming_identity

                            strip_streaming_identity(md)
                        except Exception:
                            pass
                    elif kind == "hdmi":
                        try:
                            from pigeon.hdmi_ocr import clear_ocr_fields

                            clear_ocr_fields(md)
                        except Exception:
                            pass
                skip_cache = None
                return

            if action == "pigeon_settings":
                # Entered settings_pigeon — seed update badge and refresh in background.
                def _prefetch_pigeon_update_badge() -> None:
                    if not getattr(st, "pigeon_needs_update_prefetch", False):
                        return
                    st.pigeon_needs_update_prefetch = False
                    try:
                        if update_check_state.get("update_available") and not update_check_state.get(
                            "error"
                        ):
                            st.update_available = True
                            st.update_remote_version = update_check_state.get("remote_version")
                            st.update_github_branch = update_check_state.get("github_branch")
                            st.update_error = None
                            main_settings_widget.invalidate()
                            skip_cache = None
                    except Exception:
                        pass

                    def worker_prefetch() -> None:
                        try:
                            from pigeon.update_check import check_for_update

                            result = check_for_update(force=False)
                        except Exception as e:
                            from pigeon.update_check import UpdateCheckResult

                            result = UpdateCheckResult(
                                local_version=version_string(),
                                remote_version=None,
                                update_available=False,
                                error=str(e),
                            )

                        def finish_prefetch() -> None:
                            nonlocal skip_cache
                            if main_settings_widget is None:
                                return
                            st2 = main_settings_widget.state
                            if not st2.show_pigeon_settings or st2.show_update_popup:
                                return
                            err = getattr(result, "error", None)
                            available = bool(getattr(result, "update_available", False)) and not err
                            st2.update_available = available
                            st2.update_remote_version = getattr(result, "remote_version", None)
                            st2.update_github_branch = getattr(result, "github_branch", None)
                            st2.update_error = err
                            try:
                                update_check_state["update_available"] = bool(available)
                                update_check_state["remote_version"] = st2.update_remote_version
                                update_check_state["github_branch"] = st2.update_github_branch
                                update_check_state["error"] = err
                                update_check_state["last_check_mono"] = time.monotonic()
                                _sync_update_button_style()
                            except Exception:
                                pass
                            main_settings_widget.invalidate()
                            skip_cache = None

                        root.after(0, finish_prefetch)

                    threading.Thread(
                        target=worker_prefetch, name="pigeon-update-prefetch", daemon=True
                    ).start()

                _prefetch_pigeon_update_badge()
                return

            if action == "pigeon_factory_reset":
                from pigeon.widgets.pigeon_settings import factory_reset_pigeon_persisted_state
                from pigeon.widgets.ui_color_settings import (
                    apply_color_keys_to_state,
                    load_persisted_theme_into_state,
                )

                factory_reset_pigeon_persisted_state()
                try:
                    discovery_scan_cache["rows"] = None
                    discovery_scan_cache["mono_s"] = 0.0
                except Exception:
                    pass
                try:
                    streaming_slot_holder[0] = None
                    avr_slot_holder[0] = None
                except Exception:
                    pass
                try:
                    current_apple_tv.clear()
                    current_apple_tv.update(
                        {"identifier": "", "address": "", "name": "", "label": ""}
                    )
                except Exception:
                    pass
                try:
                    receiver_http_host["host"] = ""
                except Exception:
                    pass
                try:
                    apple_tv_auto_state["content_key"] = None
                    apple_tv_auto_state["tmdb_key"] = None
                    apple_tv_auto_state["query"] = None
                    apple_tv_auto_state["last_metadata"] = None
                    apple_tv_dashboard_track["last_poll_ok"] = None
                    apple_tv_dashboard_track["consecutive_fail"] = 0
                except Exception:
                    pass
                try:
                    load_persisted_theme_into_state(st)
                    apply_color_keys_to_state(
                        st,
                        {"accent": "white", "ui": "red", "button": "black"},
                        persist=False,
                    )
                except Exception:
                    pass
                try:
                    st.reset_box_device_panel(2)
                    st.reset_box_device_panel(3)
                except Exception:
                    pass
                st.selected_wifi_ssid = ""
                st.pigeon_metadata_ok = False
                st.pigeon_hdmi_ok = False
                st.pigeon_audio_ok = False
                try:
                    from pigeon.source_toggles import apply_toggles_to_settings_state

                    apply_toggles_to_settings_state(st)
                except Exception:
                    pass
                try:
                    describe_current_apple_tv()
                    _refresh_location_selector()
                    _rebuild_paired_devices_panel()
                    _schedule_refresh_pairing_leds()
                except Exception:
                    pass
                main_settings_widget.invalidate()
                skip_cache = None
                return

            if action == "update_popup:open":
                # Always re-check GitHub (ignore any prior in-memory poll).
                st.update_local_version = version_string()
                st.update_checking = True
                st.update_error = None
                st.update_available = False
                st.update_remote_version = None
                st.update_github_branch = None
                st.update_changelog = "Checking GitHub for updates…"
                main_settings_widget.invalidate()
                skip_cache = None

                def worker_ms_update_check() -> None:
                    try:
                        from pigeon.update_check import check_for_update

                        result = check_for_update(force=True)
                    except Exception as e:
                        from pigeon.update_check import UpdateCheckResult

                        result = UpdateCheckResult(
                            local_version=version_string(),
                            remote_version=None,
                            update_available=False,
                            error=str(e),
                        )

                    def finish_ms_check() -> None:
                        nonlocal skip_cache
                        from pigeon.widgets.update_popup import (
                            DEFAULT_CHANGELOG,
                            UP_TO_DATE_CHANGELOG,
                        )

                        st.update_checking = False
                        st.update_local_version = str(
                            getattr(result, "local_version", None) or version_string()
                        )
                        st.update_remote_version = getattr(result, "remote_version", None)
                        st.update_github_branch = getattr(result, "github_branch", None)
                        st.update_error = getattr(result, "error", None)
                        available = bool(getattr(result, "update_available", False))
                        st.update_available = available and not st.update_error
                        if st.update_available:
                            st.update_changelog = DEFAULT_CHANGELOG
                            try:
                                from pigeon.widgets.update_popup import update_popup_focus_ring

                                ring = update_popup_focus_ring(update_available=True)
                                st.update_popup_focus_index = (
                                    ring.index("now") if "now" in ring else 0
                                )
                            except Exception:
                                st.update_popup_focus_index = 1
                        else:
                            st.update_changelog = UP_TO_DATE_CHANGELOG
                            st.update_popup_focus_index = 0
                        # Keep legacy Tk Updates button in sync when present.
                        try:
                            update_check_state["update_available"] = bool(st.update_available)
                            update_check_state["remote_version"] = st.update_remote_version
                            update_check_state["github_branch"] = st.update_github_branch
                            update_check_state["error"] = st.update_error
                            update_check_state["last_check_mono"] = time.monotonic()
                            _sync_update_button_style()
                        except Exception:
                            pass
                        main_settings_widget.invalidate()
                        skip_cache = None

                    root.after(0, finish_ms_check)

                threading.Thread(target=worker_ms_update_check, daemon=True).start()
                return

            if action in ("update_popup:later", "update_popup:dismiss", "update_popup:busy"):
                main_settings_widget.invalidate()
                skip_cache = None
                return

            if action == "update_popup:now":
                if st.update_applying or st.update_checking:
                    return
                if not st.update_available:
                    st.close_update_popup()
                    main_settings_widget.invalidate()
                    skip_cache = None
                    return
                remote = str(st.update_remote_version or "?")
                branch = st.update_github_branch
                st.update_applying = True
                st.update_progress = 0.0
                st.update_changelog = f"Downloading {remote}…"
                main_settings_widget.invalidate()
                skip_cache = None
                # Worker never calls root.after — Tk is not thread-safe and flooding
                # after() from download progress can drop the final restart callback.
                _ms_events: queue.Queue = queue.Queue()
                _ms_progress_last = [0.0]
                _ms_progress_label = [""]
                _ms_poll_active = [True]

                def _ms_on_progress(fraction: float, label: str) -> None:
                    frac = max(0.0, min(1.0, float(fraction)))
                    lab = str(label or "Updating…")[:96]
                    if (
                        frac < 0.999
                        and frac - _ms_progress_last[0] < 0.02
                        and lab == _ms_progress_label[0]
                    ):
                        return
                    _ms_progress_last[0] = frac
                    _ms_progress_label[0] = lab
                    _ms_events.put(("progress", frac, lab))

                def _ms_poll_events() -> None:
                    nonlocal skip_cache
                    if main_settings_widget is None:
                        return
                    done_payload = None
                    try:
                        while True:
                            kind, *payload = _ms_events.get_nowait()
                            if kind == "progress":
                                frac, lab = payload
                                if st.update_applying:
                                    st.update_progress = float(frac)
                                    st.update_changelog = str(lab)
                                    main_settings_widget.invalidate()
                                    skip_cache = None
                            elif kind == "done":
                                done_payload = payload[0]
                    except queue.Empty:
                        pass
                    if done_payload is not None:
                        _ms_poll_active[0] = False
                        result, install_root = done_payload
                        if result.ok:
                            st.update_progress = 1.0
                            st.update_changelog = "Update complete — restarting…"
                            st.update_available = False
                            try:
                                update_check_state["update_available"] = False
                                if result.remote_version:
                                    update_check_state["remote_version"] = (
                                        result.remote_version
                                    )
                                _sync_update_button_style()
                            except Exception:
                                pass
                            main_settings_widget.invalidate()
                            skip_cache = None

                            def _restart_ms() -> None:
                                try:
                                    # Linux curl|bash updater already schedules
                                    # in-app relaunch (PIGEON_UPDATE_IN_APP=1). On
                                    # macOS/desktop we must schedule it here.
                                    if not sys.platform.startswith("linux"):
                                        from pigeon.github_update import (
                                            restart_pigeon_after_update,
                                        )

                                        restart_pigeon_after_update(
                                            install_root, parent_pid=os.getpid()
                                        )
                                except Exception:
                                    pass
                                # Exit unconditionally — do not wait on root.destroy().
                                os._exit(0)

                            root.after(400, _restart_ms)
                            return

                        st.update_applying = False
                        st.update_progress = 0.0
                        st.update_error = result.message
                        st.update_changelog = (result.message or "Update failed.")[:96]
                        main_settings_widget.invalidate()
                        skip_cache = None
                        messagebox.showerror(
                            "Update failed",
                            result.message,
                            parent=root,
                        )
                        return
                    if _ms_poll_active[0]:
                        root.after(50, _ms_poll_events)

                def worker_ms_apply() -> None:
                    install_root = _resolve_install_root_for_update()
                    try:
                        from pigeon.github_update import apply_github_update

                        apply_branch = branch
                        if apply_branch is None:
                            cached = update_check_state.get("github_branch")
                            if isinstance(cached, str) and cached.strip():
                                apply_branch = cached.strip()
                        result = apply_github_update(
                            install_root,
                            branch=apply_branch,
                            progress=_ms_on_progress,
                        )
                    except Exception as e:
                        from pigeon.github_update import ApplyUpdateResult

                        result = ApplyUpdateResult(False, str(e))
                    _ms_events.put(("done", (result, install_root)))

                root.after(50, _ms_poll_events)
                threading.Thread(target=worker_ms_apply, daemon=True).start()
                return

            if action == "keyboard_pin_incomplete":
                messagebox.showwarning("Pairing", "Enter the 4-digit code from the TV.", parent=root)
                return

            if action == "wifi_logout:yes":
                try:
                    from pigeon.app_state import clear_location_wifi

                    clear_location_wifi()
                except Exception:
                    pass
                st.selected_wifi_ssid = ""
                st.wifi_password = ""
                st.pending_wifi_ssid = ""
                st.pending_network_password = ""
                st.network_password_error = False
                st.ensure_focus_ring()
                main_settings_widget.invalidate()
                skip_cache = None
                return

            if action == "wifi_logout:no":
                main_settings_widget.invalidate()
                skip_cache = None
                return

            if action == "keyboard_go:network":
                ssid = str(st.pending_wifi_ssid or "").strip()
                password = str(st.pending_network_password or "")
                if not ssid:
                    return

                st.wifi_connecting = True
                st.wifi_connect_started_mono = time.monotonic()
                st.wifi_scan_angle_deg = 0.0
                st.network_password_error = False
                main_settings_widget.invalidate()
                skip_cache = None

                def worker_wifi_join() -> None:
                    try:
                        from pigeon.wifi_connect import try_join_wifi_network

                        ok_w, _msg_w = try_join_wifi_network(ssid, password)
                    except Exception:
                        ok_w, _msg_w = False, "incorrect password"

                    def finish_wifi() -> None:
                        nonlocal skip_cache
                        st.wifi_connecting = False
                        if ok_w:
                            st.selected_wifi_ssid = ssid
                            st.wifi_password = password
                            st.pending_wifi_ssid = ""
                            st.pending_network_password = ""
                            st.network_password_error = False
                            st.wifi_onboarding = False
                            st.show_instructions = False
                            st.ensure_focus_ring()
                            try:
                                write_location_wifi(ssid, password)
                            except Exception:
                                pass
                            try:
                                from pigeon.local_ip import clear_local_ipv4_cache

                                clear_local_ipv4_cache()
                            except Exception:
                                pass
                        else:
                            st.network_password_error = True
                            st.pending_network_password = ""
                            st.open_keyboard(
                                "network",
                                assets_dir=main_settings_widget._assets_dir,
                                trigger_button="main_dual_network_button",
                            )
                        main_settings_widget.invalidate()
                        skip_cache = None

                    root.after(0, finish_wifi)

                threading.Thread(target=worker_wifi_join, daemon=True).start()
                return

            if action == "keyboard_go:location":
                nm = str(st.location_name or "").strip() or "Room"
                lid = str(getattr(st, "renaming_location_id", "") or "").strip()
                if not lid:
                    lid = read_current_location_id()
                if lid:
                    rename_location_v2(lid, nm)
                try:
                    st.refresh_location_slots()
                except Exception:
                    pass
                st.renaming_location_id = ""
                st.renaming_location_slot = 0
                try:
                    st.location_name = read_current_location_name()
                except Exception:
                    st.location_name = nm
                if st.show_location_picker:
                    st.ensure_focus_ring()
                main_settings_widget.invalidate()
                skip_cache = None
                return

            if action == "location_switch:busy":
                return

            if action == "location_switch":
                if st.location_switching:
                    return
                st.begin_location_switching()
                try:
                    main_settings_widget._ensure_location_switch_spinner_frames()
                except Exception:
                    pass
                main_settings_widget.invalidate()
                skip_cache = None

                def _finish_location_switch() -> None:
                    nonlocal skip_cache
                    if main_settings_widget is None:
                        return
                    st_fin = main_settings_widget.state
                    # Keep spinner up until pairing LED credential check settles.
                    if pair_led_busy.get("active"):
                        root.after(100, _finish_location_switch)
                        return
                    st_fin.finish_location_switching()
                    main_settings_widget.invalidate()
                    skip_cache = None

                def _run_location_switch() -> None:
                    nonlocal skip_cache
                    try:
                        _apply_persisted_location_to_runtime()
                        try:
                            st.load_saved_box_devices()
                            st.location_name = read_current_location_name()
                        except Exception:
                            pass
                        try:
                            st.refresh_location_slots()
                        except Exception:
                            pass
                    finally:
                        main_settings_widget.invalidate()
                        skip_cache = None
                        root.after(0, _finish_location_switch)

                # Yield so the UI can paint; spinner appears if reload exceeds ~2s.
                root.after(0, _run_location_switch)
                return

            if action == "box3_pair_start":
                sess = st.box_pairing
                if sess is None or int(sess.box_num) != 3:
                    return
                _save_box_pair_device_row(3, sess.row)
                st.clear_box_pairing()
                main_settings_widget.invalidate()
                skip_cache = None
                _schedule_refresh_pairing_leds()
                _rebuild_paired_devices_panel()
                return

            if action == "box2_pair_start":
                sess = st.box_pairing
                if sess is None or int(sess.box_num) != 2:
                    return
                row = dict(sess.row)
                if not begin_apple_tv_operation("starting AppleTV Remote"):
                    return

                def worker_remote_begin_settings() -> None:
                    try:
                        from pigeon.apple_tv_now_playing import begin_companion_pairing_for_device

                        ok_w, msg_w, session_key_w, _rev = begin_companion_pairing_for_device(
                            device_identifier=row["identifier"],
                            device_address=row["address"],
                            tv_displays_pin=True,
                        )
                    except ImportError:
                        ok_w, msg_w, session_key_w = False, _pyatv_install_hint(), None
                    except Exception as e:
                        ok_w, msg_w, session_key_w = False, str(e), None

                    def finish_rb_settings() -> None:
                        nonlocal skip_cache
                        if not ok_w or not session_key_w:
                            end_apple_tv_operation()
                            st.clear_box_pairing()
                            main_settings_widget.invalidate()
                            skip_cache = None
                            messagebox.showerror("AppleTV Remote", msg_w or "Pairing failed to start.")
                            return
                        if st.box_pairing is not None:
                            st.box_pairing.session_key = str(session_key_w)
                            st.box_pairing.step = "remote_pin"
                        st.open_keyboard("pin", assets_dir=main_settings_widget._assets_dir)
                        main_settings_widget.invalidate()
                        skip_cache = None
                        describe_current_apple_tv(suffix="enter Remote PIN")

                    root.after(0, finish_rb_settings)

                threading.Thread(target=worker_remote_begin_settings, daemon=True).start()
                return

            if action.startswith("keyboard_pin:"):
                pin = action.split(":", 1)[1].strip()
                pin = "".join(c for c in pin if c.isdigit())
                if len(pin) != 4:
                    messagebox.showwarning("Pairing", "Enter the 4-digit code from the TV.", parent=root)
                    return
                sess = st.box_pairing
                if sess is None or not sess.session_key:
                    return
                row = dict(sess.row)
                dn = sess.device_name

                if sess.step == "remote_pin":

                    def worker_remote_finish_settings() -> None:
                        try:
                            from pigeon.apple_tv_now_playing import begin_airplay_pairing_for_device, finish_companion_pairing_for_device

                            ok_f, msg_f = finish_companion_pairing_for_device(
                                session_key=sess.session_key, pin_code=pin
                            )
                        except ImportError:
                            ok_f, msg_f = False, _pyatv_install_hint()
                        except Exception as e:
                            ok_f, msg_f = False, str(e)

                        if ok_f:
                            time.sleep(1.5)

                        def done_rf_settings() -> None:
                            nonlocal skip_cache
                            if not ok_f:
                                end_apple_tv_operation()
                                st.clear_box_pairing()
                                main_settings_widget.invalidate()
                                skip_cache = None
                                messagebox.showerror("AppleTV Remote", msg_f)
                                _schedule_refresh_pairing_leds()
                                return
                            try:
                                ok_a, msg_a, sk_a, _r2 = begin_airplay_pairing_for_device(
                                    device_identifier=row["identifier"],
                                    device_address=row["address"],
                                    tv_displays_pin=True,
                                )
                            except ImportError:
                                ok_a, msg_a, sk_a = False, _pyatv_install_hint(), None
                            except Exception as e:
                                ok_a, msg_a, sk_a = False, str(e), None
                            if not ok_a or not sk_a:
                                end_apple_tv_operation()
                                st.clear_box_pairing()
                                main_settings_widget.invalidate()
                                skip_cache = None
                                messagebox.showerror("AppleTV AirPlay", msg_a or "AirPlay pairing failed to start.")
                                _schedule_refresh_pairing_leds()
                                return
                            if st.box_pairing is not None:
                                st.box_pairing.session_key = str(sk_a)
                                st.box_pairing.step = "airplay_pin"
                            st.open_keyboard("pin", assets_dir=main_settings_widget._assets_dir)
                            main_settings_widget.invalidate()
                            skip_cache = None
                            describe_current_apple_tv(suffix="enter AirPlay PIN")

                        root.after(0, done_rf_settings)

                    threading.Thread(target=worker_remote_finish_settings, daemon=True).start()
                    return

                if sess.step == "airplay_pin":

                    def worker_air_finish_settings() -> None:
                        try:
                            from pigeon.apple_tv_now_playing import finish_companion_pairing_for_device

                            ok_af, msg_af = finish_companion_pairing_for_device(
                                session_key=sess.session_key, pin_code=pin
                            )
                        except ImportError:
                            ok_af, msg_af = False, _pyatv_install_hint()
                        except Exception as e:
                            ok_af, msg_af = False, str(e)

                        def done_air_settings() -> None:
                            nonlocal skip_cache
                            end_apple_tv_operation()
                            if ok_af:
                                _save_box_pair_device_row(2, row)
                                st.load_saved_box_devices()
                                st.show_box2_panel = True
                            st.clear_box_pairing()
                            main_settings_widget.invalidate()
                            skip_cache = None
                            if ok_af:
                                messagebox.showinfo(
                                    "Apple TV",
                                    f"{msg_af}\n\nPlayer “{dn}” is paired for this location.",
                                )
                            else:
                                messagebox.showerror("AppleTV AirPlay", msg_af)
                            _schedule_refresh_pairing_leds()
                            _rebuild_paired_devices_panel()

                        root.after(0, done_air_settings)

                    threading.Thread(target=worker_air_finish_settings, daemon=True).start()

        def _force_advanced_feature_try(feature_id: str) -> None:
            """Advanced matrix refresh: re-run the probe path relevant to this feature row."""
            try:
                if feature_id == "title":
                    on_apple_tv_selected_then_tmdb()
                else:
                    if feature_id == "volume":
                        _receiver_poll_tick()
                    _apple_tv_auto_poll_tick()
            except Exception:
                pass

        def _on_advanced_matrix_closed() -> None:
            nonlocal dev_phase, skip_cache
            tgt = advanced_matrix_restore_phase[0]
            if tgt is not None:
                advanced_matrix_restore_phase[0] = None
                dev_phase = tgt  # type: ignore[assignment]
                skip_cache = None
                sync_developer_chrome()

        def _open_advanced_capability_matrix() -> None:
            nonlocal dev_phase, skip_cache
            try:
                from settings_advanced_matrix import open_advanced_capability_matrix
            except ImportError as e:
                messagebox.showerror("Advanced", f"Could not open capability matrix:\n{e}", parent=root)
                return
            adv_kw: dict[str, object] = {
                "playback_content_ok": _advanced_feature_pipeline_ok,
                "on_closed": _on_advanced_matrix_closed,
                "close_skip_once": advanced_matrix_close_skip,
                "feature_force_try": _force_advanced_feature_try,
            }
            if _PIGEON_EXT:
                adv_kw.update(
                    {
                        "tmdb_manual_fetch": on_apple_tv_selected_then_tmdb,
                        "tmdb_report_failure": _perform_tmdb_artwork_retry,
                        "tmdb_read_log_tail": _tmdb_retry_log_read_tail,
                        "tmdb_register_widgets": _register_tmdb_adv_widgets,
                        "tmdb_unregister_widgets": _unregister_tmdb_adv_widgets,
                        "prepend_hotkey_bindtag": _prepend_hotkey_bindtag,
                        "tmdb_quality_stats_read": lambda: {
                            "successes": int(
                                read_app_state().get("tmdb_quality_successes", 0) or 0
                            ),
                            "failures": int(
                                read_app_state().get("tmdb_quality_failures", 0) or 0
                            ),
                        },
                    }
                )
            open_advanced_capability_matrix(root, **adv_kw)

        def _open_find_device_dialog() -> None:
            if not _PIGEON_EXT:
                messagebox.showinfo("Devices", "Pigeon extensions not loaded.")
                return
            top = tk.Toplevel(root)
            top.title("Find device")
            top.configure(bg="#1a1a1e")
            try:
                top.transient(root)
                top.grab_set()
            except tk.TclError:
                pass

            scan_rows: list[list[dict[str, str]]] = [[]]
            busy = {"v": False}
            confirm_holder: list[tk.Button | None] = [None]

            hdr = tk.Frame(top, bg="#1a1a1e")
            hdr.pack(fill=tk.X, padx=12, pady=(12, 8))
            find_btn = tk.Button(hdr, text="Find devices", font=S_FONT_BTN, padx=12, pady=4)
            refresh_btn = tk.Button(hdr, text="Refresh (network scan)", font=S_FONT_BTN, padx=10, pady=4)
            find_btn.pack(side=tk.LEFT, padx=(0, 8))
            refresh_btn.pack(side=tk.LEFT, padx=(0, 0))

            tk.Label(
                top,
                text="Use Find devices (cached scan when available) or Refresh for a live network scan. "
                "The list shows every device the scan returns (nothing is hidden). "
                "Pick a row or enter Host/IP, then Confirm — you will choose the device type and optional nickname. "
                "The same device can be saved more than once for different roles.",
                fg="#888",
                bg="#1a1a1e",
                font=S_FONT_MICRO,
                wraplength=560,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=12, pady=(0, 6))

            search_banner_var = tk.StringVar(value="")
            tk.Label(
                top,
                textvariable=search_banner_var,
                fg="#ffb020",
                bg="#1a1a1e",
                font=("Helvetica", 22, "bold"),
            ).pack(anchor=tk.W, padx=12, pady=(0, 4))

            status_var = tk.StringVar(value="")

            loc_pick_var = tk.StringVar(value="")
            loc_pick_holder: list[list[tuple[str, str | None, str | None]]] = [[]]

            def build_location_pick_choices() -> list[tuple[str, str | None, str | None]]:
                ch: list[tuple[str, str | None, str | None]] = []
                counts: dict[str, int] = {}
                for L in read_all_locations_v2():
                    base = str(L.get("name") or "Room").strip() or "Room"
                    counts[base] = counts.get(base, 0) + 1
                    c = counts[base]
                    lab = base if c == 1 else f"{base} ({c})"
                    lid_g = str(L.get("id") or "").strip() or None
                    ch.append((lab, lid_g, None))
                for p in LOCATION_PRESET_ROOM_NAMES:
                    ch.append((f"+ New: {p}", None, p))
                ch.append(("+ New: Custom…", None, "__custom__"))
                return ch

            loc_pick_row = tk.Frame(top, bg="#1a1a1e")
            loc_pick_frame = tk.Frame(loc_pick_row, bg="#1a1a1e")
            btn_row = tk.Frame(loc_pick_row, bg="#1a1a1e")

            def refresh_location_pick_menu() -> None:
                for w in loc_pick_frame.winfo_children():
                    try:
                        w.destroy()
                    except tk.TclError:
                        pass
                chs = build_location_pick_choices()
                loc_pick_holder[0] = chs
                labels = [t[0] for t in chs]
                cur = read_current_location_id()
                pick_default = labels[0] if labels else ""
                for disp, lid_g, _nn in chs:
                    if lid_g and lid_g == cur:
                        pick_default = disp
                        break
                if pick_default:
                    loc_pick_var.set(pick_default)
                if labels:
                    tk.OptionMenu(loc_pick_frame, loc_pick_var, *labels).pack(side=tk.LEFT)

            tk.Label(
                loc_pick_row,
                text="Save to location:",
                fg="#aaa",
                bg="#1a1a1e",
                font=S_FONT_SMALL,
            ).pack(side=tk.LEFT)
            loc_pick_frame.pack(side=tk.LEFT, padx=(8, 0))
            loc_pick_row.pack(anchor=tk.W, padx=12, pady=(0, 6))
            refresh_location_pick_menu()

            def resolve_save_location() -> tuple[str | None, str | None]:
                pick = str(loc_pick_var.get() or "")
                for disp, lid_g, nn in loc_pick_holder[0]:
                    if disp != pick:
                        continue
                    if nn == "__custom__":
                        name = simpledialog.askstring(
                            "Location name",
                            "Custom room name:",
                            parent=top,
                        )
                        return (None, (name or "").strip() or "Room")
                    if lid_g:
                        return (lid_g, None)
                    if nn:
                        return (None, nn)
                cur = read_current_location_id()
                return (cur or None, None)

            list_rows_holder: list[list[dict[str, str]]] = [[]]
            lb_frame = tk.Frame(top, bg="#1a1a1e")
            lb_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
            sb = tk.Scrollbar(lb_frame, orient=tk.VERTICAL)
            lb = tk.Listbox(
                lb_frame,
                height=12,
                bg=_LISTBOX_BG,
                fg=_LISTBOX_FG,
                font=S_FONT_STATUS,
                selectmode=tk.SINGLE,
                highlightthickness=1,
                highlightbackground="#333",
            )
            sb.config(command=lb.yview)
            lb.configure(yscrollcommand=sb.set)
            sb.pack(side=tk.RIGHT, fill=tk.Y)
            lb.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            host_var = tk.StringVar(value="")

            def apply_listbox_rows(rows: list[dict[str, str]], *, empty_message: str | None = None) -> None:
                lb.delete(0, tk.END)
                list_rows_holder[0] = [dict(r) for r in rows]
                if not list_rows_holder[0]:
                    lb.insert(tk.END, empty_message or "No devices in list — try Refresh or Host / IP.")
                else:
                    for r in list_rows_holder[0]:
                        lb.insert(tk.END, str(r.get("label") or r.get("name") or r.get("address")))

            def repopulate_from_scan() -> None:
                rows_full = [dict(r) for r in scan_rows[0]]
                apply_listbox_rows(rows_full)

            def list_selection_row() -> dict[str, str] | None:
                sel = lb.curselection()
                if not sel:
                    return None
                idx = int(sel[0])
                rows_now = list_rows_holder[0]
                if idx < 0 or idx >= len(rows_now):
                    return None
                return dict(rows_now[idx])

            def set_scan(rows_in: list[dict[str, str]], msg: str) -> None:
                scan_rows[0] = [dict(r) for r in rows_in]
                status_var.set(msg)
                repopulate_from_scan()

            def run_scan(*, force_network: bool) -> None:
                if busy["v"]:
                    return
                busy["v"] = True
                now_chk = time.monotonic()
                cached_chk = discovery_scan_cache.get("rows")
                cache_mono_chk = float(discovery_scan_cache.get("mono_s") or 0.0)
                has_cache = (
                    not force_network
                    and isinstance(cached_chk, list)
                    and len(cached_chk) > 0
                    and (now_chk - cache_mono_chk) <= DISCOVERY_CACHE_TTL_S
                )
                search_banner_var.set("" if has_cache else "SEARCHING")
                status_var.set("Scanning\u2026" if force_network else "Loading\u2026")
                apply_listbox_rows(
                    [],
                    empty_message=(
                        "SEARCHING \u2014 scanning the network\u2026"
                        if not has_cache
                        else "Loading cached devices\u2026"
                    ),
                )
                for w in (find_btn, refresh_btn):
                    w.configure(state=tk.DISABLED)
                c = confirm_holder[0]
                if c is not None:
                    c.configure(state=tk.DISABLED)
                result: dict[str, object] = {}

                def worker() -> None:
                    now_m = time.monotonic()
                    rows_w: list[dict[str, str]] = []
                    ok_w = True
                    msg_w = ""
                    used_cache = False
                    cached = discovery_scan_cache.get("rows")
                    cache_mono = float(discovery_scan_cache.get("mono_s") or 0.0)
                    if (
                        not force_network
                        and isinstance(cached, list)
                        and len(cached) > 0
                        and (now_m - cache_mono) <= DISCOVERY_CACHE_TTL_S
                    ):
                        rows_w = [dict(r) for r in cached]
                        used_cache = True
                    else:
                        try:
                            from pigeon.apple_tv_now_playing import scan_apple_tv_devices

                            ok_w, msg_w, rows_w = scan_apple_tv_devices(scan_timeout_s=15)
                        except ImportError:
                            ok_w, msg_w, rows_w = False, _pyatv_install_hint(), []
                        except Exception as e:
                            ok_w, msg_w, rows_w = False, str(e), []
                        if ok_w and rows_w:
                            discovery_scan_cache["rows"] = [dict(r) for r in rows_w]
                            discovery_scan_cache["mono_s"] = time.monotonic()
                    result["ok"] = ok_w
                    result["rows"] = rows_w
                    result["msg"] = msg_w
                    result["used"] = used_cache

                def finish_scan() -> None:
                    busy["v"] = False
                    search_banner_var.set("")
                    for w in (find_btn, refresh_btn):
                        w.configure(state=tk.NORMAL)
                    c2 = confirm_holder[0]
                    if c2 is not None:
                        c2.configure(state=tk.NORMAL)
                    ok_w = bool(result.get("ok", True))
                    rows_w = result.get("rows") or []
                    msg_w = str(result.get("msg") or "")
                    used_cache = bool(result.get("used"))
                    if not isinstance(rows_w, list):
                        rows_w = []
                    if not ok_w:
                        messagebox.showerror("Find device", msg_w)
                        status_var.set("Scan failed.")
                        apply_listbox_rows([], empty_message="Search failed — try Refresh.")
                        return
                    if not rows_w:
                        messagebox.showinfo("Find device", msg_w or "No devices found.")
                        status_var.set("No devices.")
                        scan_rows[0] = []
                        apply_listbox_rows([], empty_message="No devices found — try Refresh or Host / IP.")
                        return
                    suffix = f"{len(rows_w)} found" + (" (cached)" if used_cache else "")
                    set_scan(rows_w, suffix)

                threading.Thread(target=lambda: (worker(), root.after(0, finish_scan)), daemon=True).start()

            def on_find_devices_click() -> None:
                run_scan(force_network=False)

            def on_refresh_click() -> None:
                run_scan(force_network=True)

            find_btn.configure(command=on_find_devices_click)
            refresh_btn.configure(command=on_refresh_click)

            tk.Label(
                top,
                text="Host / IP (optional, instead of list):",
                fg="#aaa",
                bg="#1a1a1e",
                font=S_FONT_SMALL,
            ).pack(anchor=tk.W, padx=12)
            tk.Entry(
                top,
                textvariable=host_var,
                width=36,
                bg="#252528",
                fg="#e8e8e8",
                insertbackground="#e8e8e8",
                highlightthickness=1,
                highlightbackground="#333",
                font=S_FONT_BODY,
            ).pack(anchor=tk.W, padx=12, pady=(2, 8))

            tk.Label(
                top,
                textvariable=status_var,
                fg="#777",
                bg="#1a1a1e",
                font=S_FONT_MICRO,
                wraplength=500,
                justify=tk.LEFT,
            ).pack(anchor=tk.W, padx=12, pady=(0, 6))

            def close_top() -> None:
                try:
                    top.grab_release()
                except tk.TclError:
                    pass
                top.destroy()

            def on_cancel() -> None:
                close_top()

            def _after_find_device_save(lid_written: str, verify_rows: list[dict[str, str]]) -> None:
                if lid_written:
                    set_current_location_id(lid_written)
                _apply_persisted_location_to_runtime()
                _refresh_location_selector()
                _verify_added_devices_after_save(verify_rows)

            def _ask_save_device_role() -> str | None:
                choice: list[str | None] = [None]
                dlg = tk.Toplevel(top)
                dlg.title("Device type")
                dlg.configure(bg="#1a1a1e")
                try:
                    dlg.transient(top)
                    dlg.grab_set()
                except tk.TclError:
                    pass
                tk.Label(
                    dlg,
                    text="What kind of device is this?",
                    fg="#eee",
                    bg="#1a1a1e",
                    font=S_FONT_SMALL,
                ).pack(anchor=tk.W, padx=12, pady=(12, 8))
                row_f = tk.Frame(dlg, bg="#1a1a1e")
                row_f.pack(fill=tk.X, padx=12, pady=(0, 8))
                var = tk.StringVar(value="player")
                for lab, val in (
                    ("Player (playback / metadata)", "player"),
                    ("Receiver (Denon/Marantz-style IP)", "receiver"),
                    ("TV", "tv"),
                    ("Projector", "projector"),
                    ("Game console", "game"),
                    ("Other", "other"),
                ):
                    tk.Radiobutton(
                        row_f,
                        text=lab,
                        variable=var,
                        value=val,
                        bg="#1a1a1e",
                        fg="#eee",
                        selectcolor="#333",
                        activebackground="#1a1a1e",
                        highlightthickness=0,
                        font=S_FONT_SMALL,
                    ).pack(anchor=tk.W)

                def ok() -> None:
                    choice[0] = str(var.get() or "").strip() or None
                    dlg.destroy()

                def cancel() -> None:
                    choice[0] = None
                    dlg.destroy()

                br = tk.Frame(dlg, bg="#1a1a1e")
                br.pack(pady=(0, 12))
                tk.Button(br, text="OK", command=ok, font=S_FONT_BTN, padx=14, pady=4).pack(
                    side=tk.LEFT, padx=6
                )
                tk.Button(br, text="Cancel", command=cancel, font=S_FONT_BTN, padx=14, pady=4).pack(
                    side=tk.LEFT, padx=6
                )
                dlg.wait_window(dlg)
                return choice[0]

            def on_confirm() -> None:
                host = str(host_var.get() or "").strip()

                def _tag_row_device_role(row: dict[str, str], dr: str) -> None:
                    row["device_role"] = dr

                base_row: dict[str, str] | None = None
                if not host:
                    base_row = list_selection_row()
                    if base_row is None:
                        messagebox.showwarning(
                            "Find device",
                            "Select a device from the list (wait until search finishes), or enter Host / IP.",
                            parent=top,
                        )
                        return

                r0 = _ask_save_device_role()
                if not r0:
                    return

                nick_raw = simpledialog.askstring(
                    "Nickname",
                    "Optional nickname for this entry (shown in lists and Advanced):",
                    parent=top,
                )
                nick = (nick_raw or "").strip()

                def _merge_nick(row: dict[str, str]) -> dict[str, str]:
                    m = dict(row)
                    if nick:
                        m["nickname"] = nick
                    return m

                to_id, new_nm = resolve_save_location()

                if r0 in ("tv", "projector", "game", "other"):
                    if host:
                        ident = f"{r0}:{host.split('%')[0].strip()}"
                        nm = r0.capitalize() if r0 != "other" else "Other"
                        row_any = {
                            "identifier": ident,
                            "address": host.strip(),
                            "name": nm,
                            "label": f"{nm} — {host.strip()}",
                            "looks_like_apple_tv": "false",
                        }
                        _tag_row_device_role(row_any, r0)
                    else:
                        row_any = _merge_nick(dict(base_row or {}))
                        _tag_row_device_role(row_any, r0)
                    row_any = _merge_nick(row_any)
                    slot_key = {"tv": "tv", "projector": "projector", "game": "game", "other": "other"}[r0]
                    lid = append_device_to_location_slot(
                        slot_key,
                        row_any,
                        for_location_id=to_id,
                        new_location_name=new_nm,
                    )
                    _after_find_device_save(lid, [row_any])
                    close_top()
                    return

                if r0 == "receiver":
                    if host:
                        row_r = {
                            "identifier": f"denon:{host.split('%')[0].strip()}",
                            "address": host.strip(),
                            "name": "Receiver",
                            "label": f"Receiver \u2014 {host.strip()}",
                            "looks_like_apple_tv": "false",
                        }
                        _tag_row_device_role(row_r, "receiver")
                    else:
                        row_r = _merge_nick(dict(base_row or {}))
                        _tag_row_device_role(row_r, "receiver")
                    row_r = _merge_nick(row_r)
                    lid = append_device_to_location_slot(
                        "av_receiver",
                        row_r,
                        for_location_id=to_id,
                        new_location_name=new_nm,
                    )
                    _after_find_device_save(lid, [row_r])
                    close_top()
                    return

                # Player
                if host:
                    close_top()
                    if not begin_apple_tv_operation("probing address"):
                        return

                    def w_probe() -> None:
                        try:
                            from pigeon.apple_tv_now_playing import probe_pyatv_host

                            ok_w, msg_w, row_w, looks_w = probe_pyatv_host(host, scan_timeout_s=8)
                        except ImportError:
                            ok_w, msg_w, row_w, looks_w = False, _pyatv_install_hint(), None, False
                        except Exception as e:
                            ok_w, msg_w, row_w, looks_w = False, str(e), None, False

                        def d_probe() -> None:
                            end_apple_tv_operation()
                            if not ok_w or row_w is None:
                                messagebox.showerror("Find device", msg_w)
                                return
                            row_d = _merge_nick(dict(row_w))
                            _tag_row_device_role(row_d, "player")
                            lid = append_device_to_location_slot(
                                "streaming",
                                row_d,
                                for_location_id=to_id,
                                new_location_name=new_nm,
                            )
                            _after_find_device_save(lid, [row_d])
                            if row_is_playback_apple_tv(row_d):
                                _run_sequential_player_pairing_wizard(row_d)

                        root.after(0, d_probe)

                    threading.Thread(target=w_probe, daemon=True).start()
                    return

                row_p = _merge_nick(dict(base_row or {}))
                _tag_row_device_role(row_p, "player")
                lid = append_device_to_location_slot(
                    "streaming",
                    row_p,
                    for_location_id=to_id,
                    new_location_name=new_nm,
                )
                _after_find_device_save(lid, [row_p])
                close_top()
                if row_is_playback_apple_tv(row_p):
                    _run_sequential_player_pairing_wizard(row_p)

            confirm_btn = tk.Button(btn_row, text="Confirm", command=on_confirm, font=S_FONT_BTN, padx=12, pady=4)
            confirm_holder[0] = confirm_btn
            cancel_btn = tk.Button(btn_row, text="Cancel", command=on_cancel, font=S_FONT_BTN, padx=12, pady=4)
            confirm_btn.pack(side=tk.LEFT, padx=(0, 8))
            cancel_btn.pack(side=tk.LEFT)
            btn_row.pack(side=tk.LEFT, padx=(16, 0))
            top.protocol("WM_DELETE_WINDOW", on_cancel)
            run_scan(force_network=False)

        def on_reset_pigeon_devices_and_media() -> None:
            nonlocal skip_cache
            if not messagebox.askokcancel(
                "Reset",
                "This wipes everything Pigeon has stored for devices and local image media:\n\n"
                "• Saved locations, Players, and Receivers\n"
                "• pyatv credentials\n"
                "• Discovery cache\n"
                "• pigeonPulledMedia and pigeonReformattedMedia\n\n"
                "This cannot be undone. Continue?",
                parent=root,
            ):
                return
            ok1, msg1 = purge_directory_contents(pigeon_pulled_media_dir())
            ok2, msg2 = purge_directory_contents(pigeon_reformatted_media_dir())
            cred_path = pigeon_state_dir() / "pyatv_credentials"
            cred_err = ""
            try:
                if cred_path.is_file():
                    cred_path.unlink()
            except OSError as e:
                cred_err = str(e)
            clear_all_persisted_devices_and_targets()
            clear_last_apple_tv()
            clear_last_receiver()
            discovery_scan_cache["rows"] = None
            discovery_scan_cache["mono_s"] = 0.0
            streaming_slot_holder[0] = None
            avr_slot_holder[0] = None
            current_apple_tv.clear()
            current_apple_tv.update(
                {"identifier": "", "address": "", "name": "", "label": ""}
            )
            receiver_http_host["host"] = ""
            apple_tv_auto_state["content_key"] = None
            apple_tv_auto_state["tmdb_key"] = None
            apple_tv_auto_state["query"] = None
            apple_tv_auto_state["last_metadata"] = None
            apple_tv_auto_state["last_tmdb_fetch_input"] = None
            apple_tv_auto_state["last_tmdb_fetch_refined"] = None
            apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
            apple_tv_dashboard_track["last_poll_ok"] = None
            apple_tv_dashboard_track["consecutive_fail"] = 0
            if playback_overlay_widget is not None:
                playback_overlay_widget.clear_cache()
            describe_current_apple_tv()
            _refresh_location_selector()
            _rebuild_paired_devices_panel()
            _schedule_refresh_pairing_leds()
            try:
                _warm_playback_overlay_blits()
            except Exception:
                pass
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            tail = f"{msg1}\n{msg2}"
            if cred_err:
                tail += f"\nCredentials file: {cred_err}"
            if ok1 and ok2 and not cred_err:
                messagebox.showinfo("Reset", tail)
            else:
                messagebox.showwarning("Reset", tail)

        def on_apple_tv_selected_then_tmdb() -> None:
            """Use the saved streaming slot: pyatv (Apple TV) or Roku ECP, then TMDb + backdrop."""
            if not _PIGEON_EXT:
                messagebox.showinfo("Devices", "Pigeon extensions not loaded.")
                return
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            row = streaming_slot_holder[0]
            if row is None:
                _open_find_device_dialog()
                return
            if not begin_apple_tv_operation("detecting content"):
                return

            def worker() -> None:
                ok_w, msg_w, title_w = False, "", None
                if row_is_playback_apple_tv(row):
                    try:
                        from pigeon.apple_tv_now_playing import fetch_now_playing_title_for_device

                        ok_w, msg_w, title_w = fetch_now_playing_title_for_device(
                            device_identifier=row["identifier"],
                            device_address=row["address"],
                        )
                    except ImportError:
                        ok_w, msg_w, title_w = (
                            False,
                            _pyatv_install_hint(),
                            None,
                        )
                    except Exception as e:
                        ok_w, msg_w, title_w = False, str(e), None
                else:
                    try:
                        from pigeon.roku_ecp import (
                            fetch_roku_title_for_metadata,
                            resolve_roku_ecp_base_url_for_row,
                        )

                        rbase = resolve_roku_ecp_base_url_for_row(row)
                        if not rbase:
                            ok_w, msg_w, title_w = (
                                False,
                                "",
                                None,
                            )
                        else:
                            ok_w, msg_w, title_w = fetch_roku_title_for_metadata(
                                rbase, timeout=10.0
                            )
                    except Exception as e:
                        ok_w, msg_w, title_w = False, str(e), None

                def finish() -> None:
                    nonlocal last_atv_interaction_mono
                    if not row_is_playback_apple_tv(row) and not ok_w and not msg_w:
                        end_apple_tv_operation()
                        messagebox.showinfo(
                            "Devices",
                            "This Player is not an Apple TV (pyatv) row, and Pigeon could not use "
                            "Roku ECP on its IP (port 8060).\n\n"
                            "• If this is a Roku / Roku TV (e.g. Onn), ensure the TV’s IP is in the "
                            "Player slot and try again, or set \"roku_ecp_base_url\" in "
                            f"{PIGEON_STATE_DIR_TILDE}/state.json to http://TV_IP:8060\n"
                            "• For an actual Apple TV, re-add it from Find devices so the label "
                            "shows “Apple TV / tvOS”.\n"
                            "• For a receiver only, choose Receiver in Find device for the overlay.",
                        )
                        return
                    if not ok_w:
                        end_apple_tv_operation()
                        messagebox.showerror("Devices", msg_w or "Could not read now playing.")
                        return
                    if not title_w:
                        end_apple_tv_operation()
                        messagebox.showinfo(
                            "Devices",
                            msg_w or "No title reported by the selected device.",
                        )
                        return
                    from pigeon.tmdb_poster import is_degenerate_tmdb_query

                    if is_degenerate_tmdb_query(title_w):
                        end_apple_tv_operation()
                        messagebox.showinfo(
                            "Devices",
                            "The device only reported app or channel branding, not the show or movie "
                            "title, so Pigeon did not search TMDb.\n\n"
                            "On Disney+ via Roku, wait until playback has started and try Manual fetch again.",
                        )
                        return
                    set_current_apple_tv(row, persist=True)
                    last_atv_interaction_mono = time.monotonic()
                    end_apple_tv_operation(suffix="title detected")
                    sys.stderr.write(f"pigeon: {msg_w}\n")
                    sys.stderr.flush()
                    spawn_tmdb_poster_fetch(title_w, prefer="auto", force=True)

                root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def _update_status_bar_from_metadata(metadata: dict[str, object] | None) -> None:
            if metadata:
                _apply_playback_clock_from_poll(metadata)
            # Sync TRT digits to the latest polled integer second. The steady 1 Hz metronome
            # continues stepping from this anchor.
            _sync_trt_text_to_true_once()

        def _content_key_from_metadata(metadata: dict[str, object]) -> str | None:
            pyatv_q = str(metadata.get("query") or "").strip()
            if resolve_metadata_tmdb_query is not None:
                query = pyatv_q or resolve_metadata_tmdb_query(metadata)
            else:
                query = pyatv_q
            if not query:
                query = str(metadata.get("ocr_title") or "").strip()
            if not query:
                return None
            prefer = _tmdb_pref_from_metadata(metadata)
            title = str(metadata.get("title") or "").strip()
            try:
                from pigeon.raw_title import (
                    _title_looks_like_episode_in_metadata,
                    raw_title_from_metadata_dict,
                )
                from pigeon.tmdb_poster import is_degenerate_tmdb_query

                rt = raw_title_from_metadata_dict(metadata)
                if _title_looks_like_episode_in_metadata(metadata, rt):
                    for key in ("album", "series_name", "artist"):
                        val = str(metadata.get(key) or "").strip()
                        if val and not is_degenerate_tmdb_query(val):
                            title = val
                            break
            except ImportError:
                pass
            return "|".join((query, prefer, title))

        def _tmdb_spawn_identity(query: str, prefer: str) -> tuple[str, str]:
            try:
                from pigeon.tmdb_poster import refine_tmdb_search_query

                refined = refine_tmdb_search_query(query) or str(query or "").strip()
            except ImportError:
                refined = str(query or "").strip()
            pref = str(prefer or "auto").strip().lower()
            if pref not in ("auto", "tv", "movie"):
                pref = "auto"
            return (refined, pref)

        def _tmdb_spawn_identity_changed(
            query: str,
            prefer: str,
            metadata: dict[str, object] | None = None,
            *,
            prev_content_key: object | None = None,
        ) -> bool:
            """True when this poll should start a new TMDb worker (equivalent-aware).

            ``prev_content_key`` must be the content_key from *before* this poll
            updates ``apple_tv_auto_state["content_key"]``. Reading the live state
            key here poisons the title-suffix guard (prev and new look identical).
            """
            new_id = _tmdb_spawn_identity(query, prefer)
            prev = apple_tv_auto_state.get("tmdb_key")
            if prev == new_id:
                return False
            if prev and isinstance(prev, tuple) and len(prev) == 2:
                try:
                    from pigeon.tmdb_poster import equivalent_tmdb_search_queries

                    pq, pp = str(prev[0]), str(prev[1])
                    if pp == new_id[1] and equivalent_tmdb_search_queries(new_id[0], pq):
                        return False
                    # HBO/Max: pyatv query may alternate show name vs episode title while
                    # candidates still cover the same series — do not re-fetch every poll.
                    if metadata is not None:
                        from pigeon.raw_title import tmdb_query_candidates_from_metadata

                        for cand in tmdb_query_candidates_from_metadata(metadata):
                            if equivalent_tmdb_search_queries(cand, pq):
                                return False
                        # Prefer the caller-supplied prior key; fall back only if missing.
                        prev_ck = str(
                            prev_content_key
                            if prev_content_key is not None
                            else apple_tv_auto_state.get("content_key")
                            or ""
                        )
                        new_ck = _content_key_from_metadata(metadata) or ""
                        if prev_ck and new_ck and prev_ck == new_ck:
                            return False
                        if prev_ck and new_ck:
                            pt = prev_ck.rsplit("|", 1)[-1]
                            nt = new_ck.rsplit("|", 1)[-1]
                            if pt and pt == nt:
                                return False
                except ImportError:
                    pass
            return True

        def _tmdb_pref_from_metadata(metadata: dict[str, object]) -> str:
            prefer = str(metadata.get("prefer") or "auto").strip().lower()
            if prefer not in ("auto", "tv", "movie"):
                prefer = "auto"
            try:
                from pigeon.tmdb_poster import prefer_media_for_streaming_service

                prefer = prefer_media_for_streaming_service(
                    prefer,
                    app_name=str(metadata.get("app_name") or "") or None,
                    app_id=str(metadata.get("app_id") or "") or None,
                )
            except ImportError:
                pass
            return prefer

        def _apply_playback_clock_from_poll(metadata: dict[str, object]) -> None:
            """Anchor wall clock to last reported position; polls resync and correct drift."""
            nonlocal last_timecode_motion_mono
            clk = apple_tv_playback_clock
            ds = str(metadata.get("device_state") or "")
            playing_now = "Playing" in ds
            now_m = time.monotonic()
            content_key = _content_key_from_metadata(metadata)
            prev_has_sync = bool(clk.get("has_sync"))
            prev_pos = float(clk.get("sync_position") or 0.0)

            tt_raw = metadata.get("total_time")
            try:
                reported_total = float(tt_raw) if tt_raw is not None else None
            except (TypeError, ValueError):
                reported_total = None
            if reported_total is not None:
                try:
                    if float(reported_total) > 0:
                        clk["last_reported_total"] = reported_total
                except (TypeError, ValueError):
                    pass
            elif content_key and content_key == clk.get("latched_content_key"):
                # pyatv on some paths (often Linux) omits total_time while still reporting position.
                for key in ("latched_total", "last_reported_total"):
                    prev = clk.get(key)
                    if prev is None:
                        continue
                    try:
                        pf = float(prev)
                    except (TypeError, ValueError):
                        continue
                    if pf > 0:
                        reported_total = pf
                        break

            pos_raw = metadata.get("position")
            pos_f: float | None = None
            if pos_raw is not None:
                try:
                    pos_f = max(0.0, float(pos_raw))
                except (TypeError, ValueError):
                    pos_f = None

            # Live/continuous content: playing with no duration and no scrub position.
            live_now = bool(playing_now) and (reported_total is None or reported_total <= 0)
            if live_now and pos_f is not None:
                live_now = False
            if live_now:
                clk["live_mode"] = True
                clk["has_sync"] = False
                clk["latched_total"] = None
                clk["display_played_sec"] = None
                clk["trt_next_fire_mono"] = None
                clk["playing"] = True
                # Still latch content so TMDb artwork doesn't keep swapping.
                if content_key and content_key != clk.get("latched_content_key"):
                    clk["latched_content_key"] = content_key
                # Live has no position; metadata fingerprint (title/state) alone
                # decides whether the 2-minute idle saver arms.
                return
            clk["live_mode"] = False

            if content_key and content_key != clk.get("latched_content_key"):
                clk["latched_content_key"] = content_key
                clk["latched_total"] = reported_total
                clk["display_played_sec"] = None
                clk["trt_next_fire_mono"] = None

            if pos_f is not None:
                clk["sync_mono"] = now_m
                clk["sync_position"] = pos_f
                clk["playing"] = playing_now
                clk["has_sync"] = True
                pos_moved = not prev_has_sync or abs(pos_f - prev_pos) >= 0.25
                if playing_now and pos_moved:
                    last_timecode_motion_mono = now_m
                # Metadata-idle saver stamp is refreshed via fingerprint (incl. position)
                # in ``_bump_clock_saver_significant_device_from_metadata``.
                return

            if not clk.get("has_sync"):
                clk["playing"] = playing_now
                return

            sp = float(clk["sync_position"])
            sm = float(clk["sync_mono"])
            extrap = sp + (now_m - sm) if clk.get("playing") else sp
            extrap = max(0.0, extrap)
            lt = clk.get("latched_total")
            if lt is not None:
                try:
                    extrap = min(extrap, float(lt))
                except (TypeError, ValueError):
                    pass
            clk["sync_position"] = extrap
            clk["sync_mono"] = now_m
            clk["playing"] = playing_now
            if playing_now and abs(extrap - sp) >= 0.25:
                last_timecode_motion_mono = now_m

        def _playback_extrapolated_pair() -> tuple[int, int] | None:
            clk = apple_tv_playback_clock
            if clk.get("live_mode"):
                return None
            if not clk.get("has_sync"):
                return None
            now_m = time.monotonic()
            sp = float(clk["sync_position"])
            sm = float(clk["sync_mono"])
            pos = sp + (now_m - sm) if clk.get("playing") else sp
            pos = max(0.0, pos)
            lt = clk.get("latched_total")
            if lt is None:
                lt = clk.get("last_reported_total")
            if lt is not None:
                try:
                    tft = float(lt)
                    pos = min(pos, tft)
                except (TypeError, ValueError):
                    tft = None
                else:
                    played = int(pos)
                    remaining = max(0, int(tft) - played)
                    return played, remaining
            return int(pos), 0

        def _refresh_trt_progress_only() -> None:
            nonlocal skip_cache
            if status_bar_widget is None:
                return
            pfrac = _playback_progress_fraction_for_bar()
            prog = pfrac if pfrac is not None else 0.0
            if status_bar_widget.set_now_playing_display(progress=prog):
                _warm_status_bar_blits()
                skip_cache = None
            _sync_now_playing_screen_state()

        def _sync_trt_text_to_true_once() -> None:
            """Set TRT text to the latest polled integer second (used to recover from missed metronome ticks)."""
            nonlocal skip_cache
            try:
                if status_bar_widget is None:
                    return
                clk = apple_tv_playback_clock
                if clk.get("live_mode"):
                    # Live: digits are suppressed; show LIVE.
                    if status_bar_widget.set_now_playing_display(
                        played_text="LIVE",
                        remaining_text="",
                        progress=0.0,
                    ):
                        _warm_status_bar_blits()
                        skip_cache = None
                    return

                pair = _playback_extrapolated_pair()
                if pair is None:
                    return
                played_true, rem_true = pair
                disp_true = int(played_true)

                # If we're already showing this second, avoid touching TRT text cadence.
                disp_cur = clk.get("display_played_sec")
                if disp_cur is not None and int(disp_cur) == disp_true:
                    _refresh_trt_progress_only()
                    return

                clk["display_played_sec"] = disp_true
                played_text = _format_hmmss(disp_true)
                remaining_text = _format_hmmss(int(rem_true))
                pfrac = _playback_progress_fraction_for_bar()
                prog = pfrac if pfrac is not None else 0.0
                if status_bar_widget.set_now_playing_display(
                    played_text=played_text,
                    remaining_text=remaining_text,
                    progress=prog,
                ):
                    _warm_status_bar_blits()
                    skip_cache = None
            finally:
                _sync_status_bar_trt_substantive()

        def _playback_progress_fraction_for_bar() -> float | None:
            """Integer-second played / total for progress bar (None if duration unknown)."""
            clk = apple_tv_playback_clock
            if clk.get("live_mode"):
                return None
            if not clk.get("has_sync"):
                return None
            lt = clk.get("latched_total")
            if lt is None:
                lt = clk.get("last_reported_total")
            if lt is None:
                return None
            try:
                total_f = float(lt)
            except (TypeError, ValueError):
                return None
            if total_f <= 0:
                return None
            now_m = time.monotonic()
            sp = float(clk["sync_position"])
            sm = float(clk["sync_mono"])
            pos = sp + (now_m - sm) if clk.get("playing") else sp
            pos = max(0.0, min(pos, total_f))
            played_i = int(pos)
            total_i = max(1, int(round(total_f)))
            return max(0.0, min(1.0, played_i / float(total_i)))

        # Mid-bootstrap (~1–2 s in): SVG chrome is warm; full sync is safe now.
        _warm_view_one_splash_chrome_only(phase="chrome-mid-bootstrap")

        def _refresh_extrapolated_timecodes(*, tick_steps: int = 1) -> None:
            """Update TRT labels + progress using stepped display time (steady rhythm)."""
            nonlocal skip_cache
            if status_bar_widget is None:
                return
            try:
                clk = apple_tv_playback_clock
                if clk.get("live_mode"):
                    if status_bar_widget.set_now_playing_display(
                        played_text="LIVE",
                        remaining_text="",
                        progress=0.0,
                    ):
                        _warm_status_bar_blits()
                        skip_cache = None
                    return
                pair = _playback_extrapolated_pair()
                pfrac = _playback_progress_fraction_for_bar()
                if pair is None:
                    clk["display_played_sec"] = None
                    clk["trt_next_fire_mono"] = None
                    if status_bar_widget.set_now_playing_display(progress=0.0):
                        _warm_status_bar_blits()
                        skip_cache = None
                    return
                played_true, _rem_true = pair
                lt_use = clk.get("latched_total")
                if lt_use is None:
                    lt_use = clk.get("last_reported_total")
                playing = bool(clk.get("playing"))
                disp = clk.get("display_played_sec")

                if disp is None:
                    disp = int(played_true)
                elif not playing:
                    disp = int(played_true)
                else:
                    pt = int(played_true)
                    diff = pt - disp
                    # Keep the on-screen cadence steady: at most ±1 per tick for small drift.
                    # Snap only for major seeks (e.g. user scrubs).
                    if diff >= 10:
                        disp = pt
                    elif diff > 0:
                        disp = min(pt, disp + 1)
                    elif diff <= -10:
                        disp = pt
                    elif diff < 0:
                        disp = max(pt, disp - 1)
                clk["display_played_sec"] = disp

                if lt_use is not None:
                    try:
                        tft = int(round(float(lt_use)))
                        remaining_secs = max(0, tft - disp)
                    except (TypeError, ValueError):
                        remaining_secs = max(0, int(_rem_true) + int(played_true) - disp)
                else:
                    remaining_secs = max(0, int(_rem_true) + int(played_true) - disp)

                played_text = _format_hmmss(disp)
                remaining_text = _format_hmmss(remaining_secs)
                prog = pfrac if pfrac is not None else 0.0
                if status_bar_widget.set_now_playing_display(
                    played_text=played_text,
                    remaining_text=remaining_text,
                    progress=prog,
                ):
                    _warm_status_bar_blits()
                    skip_cache = None
            finally:
                _sync_status_bar_trt_substantive()

        def _playback_ui_tick() -> None:
            try:
                clk = apple_tv_playback_clock
                now_m = time.monotonic()
                nf = clk.get("trt_next_fire_mono")
                if nf is None:
                    nf = now_m + 1.0
                    clk["trt_next_fire_mono"] = nf
                nf = float(nf)
                tick_steps = 0
                while nf <= now_m and tick_steps < 12:
                    nf += 1.0
                    tick_steps += 1
                if tick_steps == 0:
                    root.after(max(1, int(round((nf - now_m) * 1000))), _playback_ui_tick)
                    return
                clk["trt_next_fire_mono"] = nf
                _refresh_extrapolated_timecodes(tick_steps=tick_steps)
                _refresh_content_indicator()
                now2 = time.monotonic()
                next_nf_raw = clk.get("trt_next_fire_mono")
                if next_nf_raw is None:
                    next_nf_raw = now2 + 1.0
                    clk["trt_next_fire_mono"] = next_nf_raw
                next_nf = float(next_nf_raw)
                delay_ms = max(1, min(120_000, int(round((next_nf - now2) * 1000))))
                root.after(delay_ms, _playback_ui_tick)
            except Exception:
                # Never let TRT tick crashes take down the UI loop (discovery/pairing run on Tk too).
                try:
                    clk = apple_tv_playback_clock
                    clk["trt_next_fire_mono"] = time.monotonic() + 1.0
                except Exception:
                    pass
                root.after(1000, _playback_ui_tick)

        def _update_atv_interaction_from_poll_metadata(metadata: dict[str, object]) -> None:
            """Approximate Siri Remote / UI use from pyatv poll deltas (not plain playback time)."""
            nonlocal last_atv_interaction_mono, _atv_ix_sig_ds, _atv_ix_sig_ck
            nonlocal _atv_ix_pos, _atv_ix_pos_mono, _atv_ix_extrap_playing, _atv_ix_prev_idle
            if not current_apple_tv.get("identifier"):
                return
            now = time.monotonic()
            ds = str(metadata.get("device_state") or "")
            ck = _content_key_from_metadata(metadata)
            idle_now = _atv_metadata_is_content_idle(metadata)
            pos_raw = metadata.get("position")
            try:
                pos = float(pos_raw) if pos_raw is not None else None
            except (TypeError, ValueError):
                pos = None

            bump = False
            if _atv_ix_sig_ds and ds != _atv_ix_sig_ds:
                bump = True
            if ck != _atv_ix_sig_ck and (ck or _atv_ix_sig_ck):
                bump = True
            if not idle_now and _atv_ix_prev_idle:
                bump = True
            if (
                pos is not None
                and _atv_ix_pos is not None
                and 0 < (now - _atv_ix_pos_mono) < 60.0
            ):
                dt = now - _atv_ix_pos_mono
                expected = _atv_ix_pos + (dt if _atv_ix_extrap_playing else 0.0)
                if abs(pos - expected) > 3.0:
                    bump = True

            if bump:
                last_atv_interaction_mono = now
                last_device_interaction_mono = now

            _atv_ix_sig_ds = ds
            _atv_ix_sig_ck = ck
            _atv_ix_prev_idle = idle_now
            if pos is not None:
                _atv_ix_pos = pos
                _atv_ix_pos_mono = now
            _atv_ix_extrap_playing = "Playing" in ds

        def _trt_substantive_from_clock() -> bool:
            """True when we have duration-based played/remaining timecodes (not live / unknown TRT)."""
            clk = apple_tv_playback_clock
            if clk.get("live_mode"):
                return False
            if not clk.get("has_sync"):
                return False
            lt = clk.get("latched_total")
            if lt is None:
                lt = clk.get("last_reported_total")
            if lt is not None:
                try:
                    if float(lt) > 0.0:
                        return True
                except (TypeError, ValueError):
                    pass
            # Position-only streams: show elapsed TRT even when total_time never arrives.
            if clk.get("playing"):
                try:
                    return float(clk.get("sync_position") or 0.0) >= 0.0
                except (TypeError, ValueError):
                    return False
            return False

        def _trt_substantive_for_status_bar() -> bool:
            return _trt_substantive_from_clock()

        def _sync_status_bar_trt_substantive() -> None:
            nonlocal skip_cache
            if status_bar_widget is None:
                return
            if status_bar_widget.set_trt_substantive(_trt_substantive_for_status_bar()):
                _warm_status_bar_blits()
                skip_cache = None

        def _sync_status_bar_visibility_for_playback(metadata: dict[str, object] | None) -> None:
            """Hide now-playing bar + TRT pills when no Apple TV or idle (nothing queued); show when content is active."""
            nonlocal skip_cache
            if status_bar_widget is None:
                return
            if (
                _effective_display_view() == DisplayView.ONE
            ):
                show = True
            elif not current_apple_tv.get("identifier"):
                show = False
            elif metadata is not None:
                show = not _atv_metadata_is_content_idle(metadata)
            else:
                clk = apple_tv_playback_clock
                show = bool(clk.get("has_sync"))
            if status_bar_widget.set_now_playing_chrome_visible(show):
                _warm_status_bar_blits()
                skip_cache = None
            _sync_status_bar_trt_substantive()
            _sync_now_playing_screen_state()

        def _sync_streaming_badge_from_playback_sources(
            md: dict[str, object] | None,
            *,
            roku_app_name: str | None = None,
        ) -> None:
            """Streaming-service badge: pyatv metadata when present, else Roku ECP active-app name."""
            nonlocal skip_cache
            if playback_overlay_widget is None:
                return
            from pigeon.streaming_service_badges import resolve_streaming_badge_media

            assets = Path(_PROJECT_DIR) / "pigeonAssets"
            show = False
            filename = ""
            label = ""
            md_active = md is not None and not _atv_metadata_is_content_idle(md)
            pyatv_app = False
            if md_active:
                an = str(md.get("app_name") or "").strip()
                bid = str(md.get("app_id") or "").strip()
                pyatv_app = bool(an or bid)
                if pyatv_app:
                    fn, label = resolve_streaming_badge_media(
                        assets,
                        app_name=an,
                        app_id=bid,
                    )
                    if fn or label:
                        show = True
                        filename = fn or ""
            if not show and roku_app_name and str(roku_app_name).strip():
                ra = str(roku_app_name).strip()
                fn, label = resolve_streaming_badge_media(
                    assets,
                    app_name=ra,
                    app_id="",
                )
                if fn or label:
                    show = True
                    filename = fn or ""
            if not show and md_active:
                fn, label = resolve_streaming_badge_media(
                    assets,
                    app_name=str(md.get("app_name") or ""),
                    app_id=str(md.get("app_id") or ""),
                )
                if fn or label:
                    show = True
                    filename = fn or ""
            if not show and isinstance(md, dict):
                an = str(md.get("app_name") or "").strip()
                bid = str(md.get("app_id") or "").strip()
                if an or bid:
                    fn, label = resolve_streaming_badge_media(
                        assets,
                        app_name=an,
                        app_id=bid,
                    )
                    if fn or label:
                        show = True
                        filename = fn or ""
            streaming_badge_state["show"] = show
            streaming_badge_state["filename"] = filename
            streaming_badge_state["label"] = label
            _warm_playback_overlay_blits()
            skip_cache = None
            _apply_netflix_backdrop_when_running()
            try:
                render_once()
            except Exception:
                pass

        def _return_to_landing_if_atv_idle(metadata: dict[str, object]) -> None:
            """When Apple TV reports no playback, drop TMDb backdrop and show the static landing page."""
            nonlocal use_backdrop_scene, backdrop_master_bgr, backdrop_app_logo_letterbox_fit, last_frame, scaled_display, scaled_version, skip_cache
            nonlocal active_tmdb_title_key, active_tmdb_display_title, tmdb_logo_patch_bgra, scene_enabled, playing
            nonlocal tmdb_logo_app_fallback_active
            if not _atv_metadata_is_content_idle(metadata):
                return
            if _view_one_uses_now_playing_screen():
                # Keep displayed TMDB art through brief idle polls, but clear the spawn
                # identity so the next title is not suppressed as "same tmdb_key".
                apple_tv_auto_state["tmdb_key"] = None
                apple_tv_auto_state["pending_tmdb"] = None
                _clear_music_artwork_cache()
                clk = apple_tv_playback_clock
                clk["has_sync"] = False
                clk["playing"] = False
                clk["live_mode"] = False
                clk["latched_content_key"] = None
                clk["latched_total"] = None
                clk["display_played_sec"] = None
                clk["trt_next_fire_mono"] = None
                clk["sync_position"] = 0.0
                clk["sync_mono"] = time.monotonic()
                _sync_status_bar_visibility_for_playback(metadata)
                _sync_now_playing_screen_state()
                skip_cache = None
                render_once()
                return
            apple_tv_auto_state["content_key"] = None
            apple_tv_auto_state["tmdb_key"] = None
            apple_tv_auto_state["query"] = None
            apple_tv_auto_state["prefer"] = "auto"
            apple_tv_auto_state["pending_tmdb"] = None
            apple_tv_auto_state["last_tmdb_fetch_input"] = None
            apple_tv_auto_state["last_tmdb_fetch_refined"] = None
            apple_tv_auto_state["last_tmdb_fetch_prefer"] = None
            _clear_music_artwork_cache()
            lm = apple_tv_auto_state.get("last_metadata")
            if isinstance(lm, dict):
                lm["query"] = ""
                lm["content_key"] = None

            had_art = bool(use_backdrop_scene or backdrop_master_bgr is not None or active_tmdb_title_key)

            use_backdrop_scene = False
            backdrop_master_bgr = None
            backdrop_app_logo_letterbox_fit = False
            playing = False
            active_tmdb_title_key = None
            active_tmdb_display_title = None
            tmdb_logo_app_fallback_active = False
            if tmdb_logo_widget is not None:
                tmdb_logo_widget.clear_cache()
            if tmdb_logo_widget_view_six is not None:
                tmdb_logo_widget_view_six.clear_cache()
            tmdb_logo_patch_bgra = None
            _warm_tmdb_logo_patch()

            clk = apple_tv_playback_clock
            clk["has_sync"] = False
            clk["playing"] = False
            clk["live_mode"] = False
            clk["latched_content_key"] = None
            clk["latched_total"] = None
            clk["display_played_sec"] = None
            clk["trt_next_fire_mono"] = None
            clk["sync_position"] = 0.0
            clk["sync_mono"] = time.monotonic()

            if status_bar_widget is not None and status_bar_widget.set_accent_from_backdrop_bgr(None):
                _warm_status_bar_blits()

            _sync_status_bar_visibility_for_playback(metadata)

            if scene_enabled:
                last_frame = landing_scene_design_bgr
                if not _PIGEON_EXT:
                    scaled_display = _disp_fit().scale_and_crop(last_frame)
                else:
                    scaled_display = None
            scaled_version += 1
            skip_cache = None
            _refresh_content_indicator()

            if had_art:
                render_once()

        def _on_hdmi_ocr_clues(clues) -> None:
            """Merge HDMI OCR into last_metadata. Spawn TMDb only when clues add a title."""
            try:
                root.after(0, lambda c=clues: _apply_hdmi_ocr_clues(c))
            except Exception:
                # Tk gone / shutdown: clear the gate so OCR is not stuck off forever.
                apple_tv_auto_state["ocr_in_flight"] = False

        def _apply_hdmi_ocr_clues(clues) -> None:
            from pigeon.display_confidence import ocr_is_in_charge
            from pigeon.hdmi_ocr import (
                apply_clues_to_metadata,
                apply_ocr_title_as_identity,
                hdmi_capture_available,
                hdmi_last_frame_changed,
            )
            from pigeon.tmdb_poster import is_degenerate_tmdb_query

            apple_tv_auto_state["ocr_in_flight"] = False
            try:
                from pigeon.source_toggles import source_enabled

                if not source_enabled("hdmi"):
                    return
                metadata_on = source_enabled("metadata")
                hdmi_on = True
            except Exception:
                metadata_on = True
                hdmi_on = True
            md = apple_tv_auto_state.get("last_metadata")
            if not isinstance(md, dict):
                md = {}
            raw_query = str(md.get("query") or "").strip()
            had_query = bool(raw_query) and not is_degenerate_tmdb_query(raw_query)
            merged = apply_clues_to_metadata(md, clues)
            identity_changed = apply_ocr_title_as_identity(merged)
            merged["content_key"] = _content_key_from_metadata(merged)
            apple_tv_auto_state["last_metadata"] = merged
            # Meaningful HDMI screen change postpones the 2-minute metadata-idle saver.
            if hdmi_last_frame_changed() or identity_changed:
                _note_metadata_activity()
                _bump_clock_saver_significant_device()
            if identity_changed:
                apple_tv_auto_state["last_title_decision"] = str(
                    merged.get("title_decision") or ""
                ).strip() or apple_tv_auto_state.get("last_title_decision")
            agrees = bool(merged.get("ocr_agrees"))
            guess = str(clues.title_guess or "").strip()
            reason = str(clues.reason or "")
            ocr_owns = ocr_is_in_charge(
                merged,
                hdmi_on=hdmi_on,
                hdmi_present=hdmi_capture_available(),
            ) or (not metadata_on)
            # Player still owns a real title: OCR watches, but does not replace art.
            if metadata_on and had_query and not ocr_owns and reason in ("pause", "confirm", "watch") and agrees:
                try:
                    _sync_now_playing_screen_state()
                except Exception:
                    pass
                return
            if guess and not is_degenerate_tmdb_query(guess) and ocr_owns and identity_changed:
                apple_tv_auto_state["query"] = guess
                spawn_tmdb_poster_fetch(guess, prefer="auto", force=True)
            try:
                _sync_now_playing_screen_state()
            except Exception:
                pass

        def _schedule_hdmi_ocr_from_poll(metadata: dict[str, object]) -> None:
            """OCR until TMDb has a title, then every 5s to catch a new screen."""
            try:
                from pigeon.source_toggles import source_enabled

                if not source_enabled("hdmi"):
                    return
            except Exception:
                pass
            try:
                from pigeon.hdmi_ocr import decide_ocr_reason, request_ocr
            except Exception:
                return
            reason = decide_ocr_reason(
                metadata,
                tmdb_up=bool(_tmdb_info_current_and_available()),
            )
            if not reason:
                return
            if apple_tv_auto_state.get("ocr_in_flight"):
                return
            apple_tv_auto_state["ocr_in_flight"] = True
            if not request_ocr(reason, _on_hdmi_ocr_clues):
                apple_tv_auto_state["ocr_in_flight"] = False

        def _apple_tv_auto_poll_tick() -> None:
            if apple_tv_auto_state.get("running"):
                root.after(APPLE_TV_POLL_MS, _apple_tv_auto_poll_tick)
                return
            # Avoid overlapping pyatv scan/connect with discover / pairing / probe — reduces spurious TV pairing prompts.
            if apple_tv_busy["active"]:
                root.after(APPLE_TV_POLL_MS, _apple_tv_auto_poll_tick)
                return
            if not current_apple_tv.get("identifier"):
                _seed_current_apple_tv_from_streaming_slot()
            if not current_apple_tv.get("identifier") or not _PIGEON_EXT:
                _sync_streaming_badge_from_playback_sources(None)
                _sync_status_bar_visibility_for_playback(None)
                root.after(APPLE_TV_POLL_MS, _apple_tv_auto_poll_tick)
                return
            apple_tv_auto_state["running"] = True
            device_identifier = current_apple_tv.get("identifier", "")
            device_address = current_apple_tv.get("address", "")

            def worker() -> None:
                try:
                    from pigeon.apple_tv_now_playing import fetch_now_playing_info_for_device

                    ok_w, msg_w, metadata_w = fetch_now_playing_info_for_device(
                        device_identifier=device_identifier,
                        device_address=device_address,
                        scan_timeout_s=_apple_tv_scan_timeout_s(),
                    )
                except ImportError:
                    ok_w, msg_w, metadata_w = (
                        False,
                        _pyatv_install_hint(),
                        None,
                    )
                except Exception as e:
                    ok_w, msg_w, metadata_w = False, str(e), None

                # Roku ECP calls can block for multi-second HTTP timeouts; never run them on the Tk thread
                # or the whole UI (including the mic visualizer) freezes on every poll cadence.
                wk_roku_nm: str | None = None
                md_poll_w = metadata_w if isinstance(metadata_w, dict) else None
                md_act_w = md_poll_w is not None and not _atv_metadata_is_content_idle(md_poll_w)
                pyatv_has_app_w = False
                if md_act_w:
                    pyatv_has_app_w = bool(
                        str(md_poll_w.get("app_name") or "").strip()
                        or str(md_poll_w.get("app_id") or "").strip()
                    )
                if not pyatv_has_app_w:
                    try:
                        from pigeon.roku_ecp import (
                            fetch_roku_active_app_name,
                            resolve_roku_ecp_base_url_for_row,
                        )

                        row0_wk = streaming_slot_holder[0]
                        rb_wk = resolve_roku_ecp_base_url_for_row(row0_wk) if row0_wk else ""
                        if rb_wk:
                            t_nm = fetch_roku_active_app_name(rb_wk)
                            if t_nm:
                                wk_roku_nm = t_nm
                    except Exception:
                        wk_roku_nm = None

                pyatv_tmdb_eligible_w = False
                if isinstance(metadata_w, dict):
                    from pigeon.tmdb_poster import is_degenerate_tmdb_query

                    _q_wk = (
                        resolve_metadata_tmdb_query(metadata_w)
                        if resolve_metadata_tmdb_query is not None
                        else str(metadata_w.get("query") or "").strip()
                    )
                    if (
                        _q_wk
                        and not is_degenerate_tmdb_query(_q_wk)
                        and not _atv_metadata_is_content_idle(metadata_w)
                    ):
                        pyatv_tmdb_eligible_w = True

                wk_roku_title: tuple[bool, str, str | None] | None = None
                if not pyatv_tmdb_eligible_w:
                    row0_nf_wk = streaming_slot_holder[0]
                    if row0_nf_wk is not None and not row_is_playback_apple_tv(row0_nf_wk):
                        try:
                            from pigeon.roku_ecp import (
                                fetch_roku_title_for_metadata,
                                resolve_roku_ecp_base_url_for_row,
                            )

                            rb_nf_wk = resolve_roku_ecp_base_url_for_row(row0_nf_wk)
                            if rb_nf_wk:
                                wk_roku_title = fetch_roku_title_for_metadata(rb_nf_wk, timeout=6.0)
                        except Exception:
                            wk_roku_title = None

                def finish() -> None:
                    nonlocal skip_cache
                    apple_tv_auto_state["running"] = False
                    pyatv_ok = bool(ok_w and isinstance(metadata_w, dict))
                    md_for_status: dict[str, object] | None = metadata_w if pyatv_ok else None
                    next_poll_ms = APPLE_TV_POLL_MS
                    if current_apple_tv.get("identifier"):
                        if pyatv_ok:
                            apple_tv_dashboard_track["last_poll_ok"] = True
                            apple_tv_dashboard_track["consecutive_fail"] = 0
                            if isinstance(metadata_w, dict) and _atv_metadata_is_content_idle(metadata_w):
                                # Idle Apple TV metadata does not need aggressive 3s reconnect cadence.
                                next_poll_ms = max(APPLE_TV_POLL_MS, APPLE_TV_IDLE_POLL_MS)
                        else:
                            apple_tv_dashboard_track["last_poll_ok"] = False
                            apple_tv_dashboard_track["consecutive_fail"] = int(
                                apple_tv_dashboard_track.get("consecutive_fail", 0)
                            ) + 1
                            cf = int(apple_tv_dashboard_track.get("consecutive_fail", 0) or 0)
                            if cf == 1 or cf % 5 == 0:
                                try:
                                    from pigeon.pi_diagnostics import append_pigeon_log

                                    append_pigeon_log(
                                        f"metadata poll failed ({cf}×): {str(msg_w or '')[:240]}"
                                    )
                                except Exception:
                                    pass
                            # Back off repeated connect attempts to reduce socket churn and UI pressure.
                            next_poll_ms = min(
                                APPLE_TV_FAIL_POLL_MAX_MS,
                                APPLE_TV_POLL_MS * max(2, min(cf, 5)),
                            )
                            if cf == 3:
                                try:
                                    from device_capability_matrix import (
                                        FEATURES as _delegation_feature_rows,
                                        active_device_columns as _adv_dev_cols,
                                    )

                                    lid_log = str(read_current_location_id() or "").strip()
                                    if lid_log:
                                        _poll_entries = [
                                            (
                                                str(fid),
                                                f"{_fn}: player poll failed ({cf}\u00d7) while using this "
                                                f"delegation chain \u2014 {str(msg_w or '')[:120]}",
                                            )
                                            for _fn, fid in _delegation_feature_rows
                                        ]
                                        append_delegation_log_lines(lid_log, _poll_entries)
                                        ndev = len(_adv_dev_cols())
                                        if ndev > 1:
                                            advance_delegation_active(lid_log, "title", ndev)
                                except Exception:
                                    pass
                    try:
                        from pigeon.observed_capability import (
                            update_observed_capabilities_from_player_poll,
                        )

                        _lid_ob = str(read_current_location_id() or "").strip()
                        if _lid_ob and device_identifier and device_address:
                            row_poll: dict[str, str] | None = None
                            for _r in read_saved_streaming_devices_all():
                                if (
                                    str(_r.get("identifier") or "").strip()
                                    == str(device_identifier).strip()
                                    and str(_r.get("address") or "").strip()
                                    == str(device_address).strip()
                                ):
                                    row_poll = dict(_r)
                                    break
                            if row_poll is None:
                                row_poll = {
                                    "identifier": str(device_identifier).strip(),
                                    "address": str(device_address).strip(),
                                    "name": str(current_apple_tv.get("name") or "").strip(),
                                    "label": str(current_apple_tv.get("label") or "").strip(),
                                }
                            update_observed_capabilities_from_player_poll(
                                _lid_ob,
                                row_poll,
                                ok=bool(ok_w),
                                metadata=metadata_w if isinstance(metadata_w, dict) else None,
                            )
                    except Exception:
                        pass
                    _refresh_observed_pairing_led_rows()
                    _refresh_content_indicator()
                    md_for_spawn: dict[str, object] | None = None
                    if metadata_w:
                        if ok_w:
                            _update_atv_interaction_from_poll_metadata(metadata_w)
                        prefer_snap = _tmdb_pref_from_metadata(metadata_w)
                        _ppm = str(metadata_w.get("prefer_pyatv_media") or "").strip().lower()
                        if _ppm not in ("auto", "tv", "movie"):
                            _ppm = "auto"
                        # Keep full poll dict for view-4 diagnostics; normalize the fields Pigeon logic relies on.
                        merged_md: dict[str, object] = dict(metadata_w)
                        pyatv_query = str(metadata_w.get("query") or "").strip()
                        resolved_query = (
                            resolve_metadata_tmdb_query(metadata_w)
                            if resolve_metadata_tmdb_query is not None
                            else pyatv_query
                        )
                        merged_md["query"] = pyatv_query or resolved_query
                        merged_md["title"] = str(metadata_w.get("title") or "").strip()
                        merged_md["artist"] = str(metadata_w.get("artist") or "").strip()
                        merged_md["series_name"] = str(metadata_w.get("series_name") or "").strip()
                        merged_md["album"] = str(metadata_w.get("album") or "").strip()
                        merged_md["media_type"] = str(metadata_w.get("media_type") or "").strip()
                        merged_md["total_time"] = metadata_w.get("total_time")
                        merged_md["position"] = metadata_w.get("position")
                        merged_md["device_state"] = str(metadata_w.get("device_state") or "").strip()
                        merged_md["inferred_prefer"] = prefer_snap
                        merged_md["prefer_pyatv_media"] = _ppm
                        merged_md["content_key"] = _content_key_from_metadata(merged_md)
                        merged_md["app_name"] = str(metadata_w.get("app_name") or "").strip()
                        merged_md["app_id"] = str(metadata_w.get("app_id") or "").strip()
                        merged_md["volume_percent"] = metadata_w.get("volume_percent")
                        try:
                            from pigeon.source_toggles import (
                                source_enabled,
                                strip_streaming_identity,
                            )

                            if not source_enabled("metadata"):
                                strip_streaming_identity(merged_md)
                                merged_md["content_key"] = _content_key_from_metadata(
                                    merged_md
                                )
                        except Exception:
                            pass
                        if ok_w:
                            _bump_clock_saver_significant_device_from_metadata(merged_md)
                        md_for_spawn = merged_md
                        prev_ocr_md = apple_tv_auto_state.get("last_metadata")
                        try:
                            from pigeon.display_confidence import (
                                PYATV_IDENTITY,
                                mark_identity,
                                mark_stale,
                                player_metadata_adequate,
                            )
                            from pigeon.source_toggles import source_enabled
                            from pigeon.hdmi_ocr import (
                                apply_ocr_title_as_identity,
                                clear_ocr_fields,
                                copy_ocr_fields,
                                ocr_session_anchor,
                            )

                            prev_app = ""
                            if isinstance(prev_ocr_md, dict):
                                prev_app = str(
                                    prev_ocr_md.get("app_id")
                                    or prev_ocr_md.get("app_name")
                                    or ""
                                ).strip().casefold()
                            new_app = str(
                                merged_md.get("app_id") or merged_md.get("app_name") or ""
                            ).strip().casefold()
                            app_changed = bool(prev_app and new_app and prev_app != new_app)
                            player_ok = player_metadata_adequate(merged_md)
                            if player_ok:
                                mark_identity(
                                    merged_md, source="pyatv", confidence=PYATV_IDENTITY
                                )
                                try:
                                    from pigeon.title_decision import (
                                        apply_decision_to_metadata,
                                        record_title_decision,
                                    )

                                    q_py = str(
                                        merged_md.get("query")
                                        or merged_md.get("title")
                                        or ""
                                    ).strip()
                                    prev_dec = str(
                                        merged_md.get("title_decision_title") or ""
                                    ).strip()
                                    if q_py and q_py.casefold() != prev_dec.casefold():
                                        decision = record_title_decision(
                                            q_py,
                                            source="pyatv",
                                            reason="player metadata supplied a usable title",
                                        )
                                        apply_decision_to_metadata(merged_md, decision)
                                        apple_tv_auto_state["last_title_decision"] = (
                                            decision.explain()
                                        )
                                except Exception:
                                    pass
                            if source_enabled("hdmi"):
                                # content_key includes the OCR-filled query, so it
                                # changes on the next poll and used to drop clues.
                                # Keep HDMI results until the Apple TV app / real
                                # pyatv title changes (Disney+ is not a title).
                                session = ocr_session_anchor(merged_md)
                                prev_session = (
                                    str(prev_ocr_md.get("ocr_session") or "")
                                    if isinstance(prev_ocr_md, dict)
                                    else ""
                                )
                                if app_changed and not player_ok:
                                    # Metadata-rich app → no-meta app: drop stale
                                    # identity/art; OCR will refill what it can.
                                    mark_stale(merged_md)
                                    _clear_displayed_tmdb_art_for_content_change()
                                elif not prev_session or prev_session == session:
                                    copy_ocr_fields(prev_ocr_md, merged_md)
                                    if (
                                        not player_ok
                                        and isinstance(prev_ocr_md, dict)
                                        and str(prev_ocr_md.get("identity_source") or "")
                                        == "ocr"
                                    ):
                                        mark_identity(
                                            merged_md,
                                            source="ocr",
                                            confidence=float(
                                                prev_ocr_md.get("identity_confidence")
                                                or 0.65
                                            ),
                                        )
                                merged_md["ocr_session"] = session
                                if apply_ocr_title_as_identity(merged_md):
                                    merged_md["content_key"] = _content_key_from_metadata(
                                        merged_md
                                    )
                            else:
                                clear_ocr_fields(merged_md)
                        except Exception:
                            pass
                        apple_tv_auto_state["last_metadata"] = merged_md
                        try:
                            _schedule_hdmi_ocr_from_poll(merged_md)
                        except Exception:
                            pass
                        # Music artwork (bytes live only on the poll dict; not stored in last_metadata).
                        try:
                            art_md = dict(merged_md)
                            if isinstance(metadata_w, dict) and metadata_w.get("artwork_bytes"):
                                art_md["artwork_bytes"] = metadata_w.get("artwork_bytes")
                                if metadata_w.get("artwork_id"):
                                    art_md["artwork_id"] = metadata_w.get("artwork_id")
                            _store_music_artwork_from_metadata(art_md)
                        except Exception:
                            pass
                        _update_status_bar_from_metadata(metadata_w)
                        if playback_overlay_widget is not None:
                            row_av = streaming_slot_holder[0]
                            if row_av and row_is_playback_apple_tv(row_av):
                                from pigeon.widgets.playback_overlay import (
                                    volume_percent_to_widget_line,
                                )

                                v_line = volume_percent_to_widget_line(
                                    metadata_w.get("volume_percent")
                                )
                                # The Denon poll runs on its own short cadence and is the
                                # authoritative source whenever it has produced a usable
                                # reading recently — Apple TV's ``volume_percent`` reads 0
                                # when a physical AV receiver owns the volume, which would
                                # otherwise flash "0" over the correct dB value every
                                # metadata tick. Keep polling (scale-change detection stays
                                # active) but do not let that poll update the widget while
                                # Denon still owns the line.
                                last_denon_usable = float(
                                    denon_vol_cache.get("mono_usable") or 0.0
                                )
                                denon_staleness_s = time.monotonic() - last_denon_usable
                                denon_authoritative = (
                                    not receiver_standby_holder[0]
                                    and bool(denon_vol_cache.get("effective"))
                                    and denon_staleness_s
                                    < (RECEIVER_POLL_MS / 1000.0) * 6
                                )
                                if v_line and not denon_authoritative:
                                    old_v = str(receiver_overlay_state.get("volume", ""))
                                    if old_v != v_line:
                                        receiver_overlay_state["volume"] = v_line
                                        _bump_clock_saver_significant_device()
                                        _warm_playback_overlay_blits()
                                        skip_cache = None
                                        render_once()
                    md_poll = metadata_w if isinstance(metadata_w, dict) else None
                    _sync_streaming_badge_from_playback_sources(
                        md_poll,
                        roku_app_name=wk_roku_nm,
                    )
                    pyatv_tmdb_eligible = False
                    if isinstance(md_for_spawn, dict):
                        from pigeon.tmdb_poster import is_degenerate_tmdb_query

                        query = str(md_for_spawn.get("query") or "").strip()
                        content_key = _content_key_from_metadata(md_for_spawn)
                        prefer = _tmdb_pref_from_metadata(md_for_spawn)
                        if (
                            query
                            and not is_degenerate_tmdb_query(query)
                            and not _atv_metadata_is_content_idle(md_for_spawn)
                        ):
                            pyatv_tmdb_eligible = True
                            prev_key = apple_tv_auto_state.get("content_key")
                            content_changed = bool(content_key and content_key != prev_key)
                            if content_changed:
                                apple_tv_auto_state["content_key"] = content_key
                                # Clear prior poster/cast and unlock spawn identity so the
                                # new title can fetch (force-quit was previously the only
                                # path that cleared tmdb_key after a stuck empty state).
                                _clear_displayed_tmdb_art_for_content_change()
                            apple_tv_auto_state["query"] = query
                            apple_tv_auto_state["prefer"] = prefer
                            needs_spawn = bool(
                                content_changed
                                or _tmdb_spawn_identity_changed(
                                    query,
                                    prefer,
                                    md_for_spawn,
                                    prev_content_key=prev_key,
                                )
                            )
                            # Empty display with active playback: retry once per identity.
                            # After a no-match / exhausted error-flag cycle we set
                            # ``tmdb_missing_art`` so this path cannot spin forever.
                            if (
                                not needs_spawn
                                and active_tmdb_title_key is None
                                and not apple_tv_auto_state.get("tmdb_fetch_in_flight")
                                and not apple_tv_auto_state.get("pending_tmdb")
                                and not apple_tv_auto_state.get("tmdb_missing_art")
                            ):
                                needs_spawn = True
                            if needs_spawn:
                                spawn_tmdb_poster_fetch(
                                    query, prefer=prefer, force=content_changed
                                )
                        if ok_w:
                            _return_to_landing_if_atv_idle(md_for_spawn)
                    if not pyatv_tmdb_eligible and wk_roku_title is not None:
                        try:
                            from pigeon.source_toggles import source_enabled
                            from pigeon.tmdb_poster import is_degenerate_tmdb_query

                            if not source_enabled("metadata"):
                                r_ok, _rmsg, rtitle = False, "", None
                            else:
                                r_ok, _rmsg, rtitle = wk_roku_title
                            if (
                                r_ok
                                and rtitle
                                and not is_degenerate_tmdb_query(rtitle)
                            ):
                                r_md: dict[str, object] = {
                                    "query": str(rtitle).strip(),
                                    "title": str(rtitle).strip(),
                                    "artist": "",
                                    "series_name": "",
                                    "album": "",
                                    "media_type": "",
                                    "total_time": None,
                                    "position": None,
                                    "device_state": "Playing",
                                    "app_name": str(wk_roku_nm or ""),
                                    "app_id": "",
                                    "prefer_pyatv_media": "auto",
                                }
                                prefer_r = _tmdb_pref_from_metadata(r_md)
                                r_md["inferred_prefer"] = prefer_r
                                r_md["content_key"] = _content_key_from_metadata(r_md)
                                apple_tv_auto_state["last_metadata"] = r_md
                                _update_status_bar_from_metadata(r_md)
                                md_for_status = r_md
                                prev_rk = apple_tv_auto_state.get("content_key")
                                r_ck = r_md.get("content_key")
                                r_changed = bool(r_ck and r_ck != prev_rk)
                                if r_changed:
                                    apple_tv_auto_state["content_key"] = r_ck
                                    _clear_displayed_tmdb_art_for_content_change()
                                r_q = str(rtitle).strip()
                                apple_tv_auto_state["query"] = r_q
                                apple_tv_auto_state["prefer"] = "auto"
                                r_needs = bool(
                                    r_changed
                                    or _tmdb_spawn_identity_changed(
                                        r_q, "auto", r_md, prev_content_key=prev_rk
                                    )
                                )
                                if (
                                    not r_needs
                                    and active_tmdb_title_key is None
                                    and not apple_tv_auto_state.get("tmdb_fetch_in_flight")
                                    and not apple_tv_auto_state.get("pending_tmdb")
                                    and not apple_tv_auto_state.get("tmdb_missing_art")
                                ):
                                    r_needs = True
                                if r_needs:
                                    spawn_tmdb_poster_fetch(
                                        r_q, prefer="auto", force=r_changed
                                    )
                                if not pyatv_ok:
                                    apple_tv_dashboard_track["last_poll_ok"] = True
                                    apple_tv_dashboard_track["consecutive_fail"] = 0
                        except Exception:
                            pass
                    _sync_status_bar_visibility_for_playback(md_for_status)
                    root.after(max(APPLE_TV_POLL_MS, int(next_poll_ms)), _apple_tv_auto_poll_tick)

                root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def on_debug_streaming_slot_apple_tv() -> None:
            if not _PIGEON_EXT:
                messagebox.showinfo("Devices", "Pigeon extensions not loaded.")
                return
            if apple_tv_busy["active"]:
                describe_current_apple_tv(suffix="busy")
                return
            row = streaming_slot_holder[0]
            if row is None:
                _open_find_device_dialog()
                return
            if not row_is_playback_apple_tv(row):
                messagebox.showinfo(
                    "Devices",
                    "Metadata debug applies to Apple TV rows (label shows “Apple TV / tvOS”), not receivers.",
                )
                return
            if not begin_apple_tv_operation("debugging metadata"):
                return

            def worker() -> None:
                try:
                    from pigeon.apple_tv_now_playing import debug_metadata_for_device

                    ok_w, dump_w = debug_metadata_for_device(
                        device_identifier=row["identifier"],
                        device_address=row["address"],
                    )
                except ImportError:
                    ok_w, dump_w = (
                        False,
                        _pyatv_install_hint(),
                    )
                except Exception as e:
                    ok_w, dump_w = False, str(e)

                def finish() -> None:
                    title = "Apple TV Metadata Debug"
                    end_apple_tv_operation()
                    if ok_w:
                        messagebox.showinfo(title, dump_w)
                    else:
                        messagebox.showerror(title, dump_w)

                root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()

        def _attach_hover_tooltip(widget: tk.Misc, message: str) -> None:
            tip: list[tk.Toplevel | None] = [None]

            def show(_event: tk.Event | None = None) -> None:
                if tip[0] is not None:
                    return
                tw = tk.Toplevel(root)
                tw.wm_overrideredirect(True)
                try:
                    tw.wm_attributes("-topmost", True)
                except tk.TclError:
                    pass
                x = widget.winfo_rootx() + 4
                y = widget.winfo_rooty() + int(widget.winfo_height()) + 4
                tw.wm_geometry(f"+{x}+{y}")
                tk.Label(
                    tw,
                    text=message,
                    bg="#2a2a30",
                    fg="#e8e8e8",
                    font=S_FONT_SMALL,
                    padx=8,
                    pady=4,
                ).pack()
                tip[0] = tw

            def hide(_event: tk.Event | None = None) -> None:
                if tip[0] is not None:
                    tip[0].destroy()
                    tip[0] = None

            widget.bind("<Enter>", show)
            widget.bind("<Leave>", hide)

        def _perform_tmdb_error_flag_retry() -> None:
            """Second-chance TMDb fetch when the user flags bad artwork (⌘⇧X).

            Runs at most one full pass of ``TMDB_ERROR_FLAG_RETRY_RULES`` (auto-chained
            from ``finish_tmdb`` on failure). After the cycle is exhausted, marks
            missing art so the circles poster shows "?" instead of respawning forever.
            """
            nonlocal skip_cache
            if not _PIGEON_EXT:
                return
            rules = TMDB_ERROR_FLAG_RETRY_RULES
            if not tmdb_error_flag_retry_active[0]:
                return
            idx = int(tmdb_error_flag_retry_rule_idx[0])
            if idx >= len(rules):
                primary = str(apple_tv_auto_state.get("query") or "").strip()
                prefer_ex = str(apple_tv_auto_state.get("prefer") or "auto")
                _mark_tmdb_missing_art(
                    identity=_tmdb_spawn_identity(primary, prefer_ex) if primary else None
                )
                if _view_one_uses_now_playing_screen():
                    _clear_now_playing_view_caches()
                    _sync_now_playing_screen_state()
                skip_cache = None
                try:
                    render_once()
                except Exception:
                    pass
                return
            prefer, qsource, rule_id = rules[idx]
            primary = str(apple_tv_auto_state.get("query") or "").strip()
            md_raw = apple_tv_auto_state.get("last_metadata")
            md = md_raw if isinstance(md_raw, dict) else {}
            alt = _alternate_tmdb_query_from_metadata(md if md else None, primary)
            raw_q = _raw_title_query_from_metadata(md if md else None)
            if qsource == "raw_title":
                q = (raw_q or primary).strip()
            elif qsource == "alternate":
                q = (alt or raw_q or primary).strip()
            else:
                q = primary
            if not q:
                _mark_tmdb_missing_art(identity=None)
                return
            apple_tv_auto_state["tmdb_missing_art"] = False
            tmdb_error_flag_retry_rule_idx[0] = idx + 1
            _tmdb_retry_log_append(
                {
                    "event": "tmdb_error_flag_retry",
                    "rule_index": idx,
                    "rule_id": rule_id,
                    "prefer": prefer,
                    "query_source": qsource,
                    "query_sent": q,
                    "primary_query": primary,
                    "raw_title_query": raw_q,
                    "alternate_available": bool(alt),
                    "alternate_query": alt,
                }
            )
            spawn_tmdb_poster_fetch(q, prefer=prefer, force=True)
            sys.stderr.write(
                f"pigeon: tmdb error-flag retry ({rule_id}) prefer={prefer} q={q!r}\n"
            )
            sys.stderr.flush()

        def _perform_tmdb_artwork_retry() -> None:
            if not _PIGEON_EXT:
                return
            rules = [
                ("movie", "primary", "movie+primary"),
                ("tv", "primary", "tv+primary"),
                ("auto", "alternate", "auto+alternate_query"),
                ("auto", "primary", "auto+primary"),
            ]
            idx = tmdb_retry_rule_idx[0] % len(rules)
            prefer, qsource, rule_id = rules[idx]
            primary = str(apple_tv_auto_state.get("query") or "").strip()
            md_raw = apple_tv_auto_state.get("last_metadata")
            md = md_raw if isinstance(md_raw, dict) else {}
            alt = _alternate_tmdb_query_from_metadata(md if md else None, primary)
            if qsource == "primary":
                q = primary
            else:
                q = (alt or primary).strip()
            if not q:
                messagebox.showwarning(
                    "TMDb retry",
                    "No playback search query yet. Play something on the device and wait for metadata, "
                    "or type a query in the command bar (tmdb …).",
                )
                return
            tmdb_retry_rule_idx[0] = idx + 1
            entry = {
                "event": "tmdb_retry_hotkey",
                "rule_index": idx,
                "rule_id": rule_id,
                "prefer": prefer,
                "query_source": qsource,
                "query_sent": q,
                "primary_query": primary,
                "alternate_available": bool(alt),
                "alternate_query": alt,
                "active_tmdb_title_key_before": active_tmdb_title_key,
                "active_tmdb_display_title_before": active_tmdb_display_title,
                "apple_tv_auto_prefer": apple_tv_auto_state.get("prefer"),
                "content_key": apple_tv_auto_state.get("content_key"),
                "live_mode": apple_tv_playback_clock.get("live_mode"),
                "metadata_excerpt": {
                    k: md.get(k)
                    for k in ("title", "artist", "series_name", "media_type", "inferred_prefer", "device_state")
                    if md.get(k)
                },
            }
            _tmdb_retry_log_append(entry)
            ts = time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())
            was = active_tmdb_display_title or "—"
            _append_tmdb_retry_log_ui(f"{ts}  {rule_id}  prefer={prefer}  q={q!r}  was={was!r}")
            # Manual "?" / retry hotkey clears give-up so this attempt can run.
            apple_tv_auto_state["tmdb_missing_art"] = False
            apple_tv_auto_state["tmdb_exhausted_identity"] = None
            spawn_tmdb_poster_fetch(q, prefer=prefer, force=True)
            sys.stderr.write(f"pigeon: tmdb retry ({rule_id}) prefer={prefer} q={q!r}\n")
            sys.stderr.flush()

        def on_tmdb_retry_hotkey(event: tk.Event) -> str | None:
            _bump_pigeon_user_activity(event)
            if not _PIGEON_EXT:
                return None
            if _widget_accepts_typing(event.widget):
                return None
            now_hk = time.monotonic()
            if now_hk - _last_tmdb_hotkey_mono[0] < 0.15:
                return "break"
            _last_tmdb_hotkey_mono[0] = now_hk
            _perform_tmdb_artwork_retry()
            return "break"

        def on_tmdb_quality_error_report_hotkey(event: tk.Event) -> str | None:
            """Flag TMDb artwork error (⌘⇧X); log immediately, retry fetch, 20s undo window.

            Bound only to Control/Command+Shift+X, so trust the binding — Wayland/X11
            often omits modifier bits from ``event.state`` after the combo is matched.
            """
            nonlocal skip_cache
            _bump_pigeon_user_activity(event)
            if not _PIGEON_EXT:
                return None
            if _widget_accepts_typing(event.widget):
                return None
            ks = (getattr(event, "keysym", "") or "").lower()
            if ks not in ("x",):
                return None
            now_q = time.monotonic()
            if now_q - _last_tmdb_quality_report_mono[0] < 0.15:
                return "break"
            _last_tmdb_quality_report_mono[0] = now_q
            if tmdb_quality_error_flag[0]:
                elapsed = now_q - float(tmdb_quality_flag_set_mono[0] or 0.0)
                if elapsed <= TMDB_QUALITY_UNLOG_WINDOW_S:
                    _clear_tmdb_quality_flag(undo=True, show_overlay=True)
                    skip_cache = None  # force redraw so undo X appears immediately
                    try:
                        sys.stderr.write(
                            "pigeon: TMDb quality flag undone within 20s window (⌘⇧X).\n"
                        )
                        sys.stderr.flush()
                    except Exception:
                        pass
                    return "break"
                _clear_tmdb_quality_flag(undo=False, show_overlay=False)
            tmdb_quality_error_flag[0] = True
            tmdb_quality_flag_set_mono[0] = now_q
            _adjust_tmdb_quality_failure_delta(1)
            _trigger_tmdb_quality_toggle_overlay("flag")
            skip_cache = None  # force redraw so confirmation X appears immediately
            try:
                _append_tmdb_quality_event_report_log(
                    outcome="FAILURE",
                    title_key=active_tmdb_title_key,
                    display_title=active_tmdb_display_title,
                    msg_m="user_flagged",
                )
            except Exception:
                pass
            # One full rule cycle only (auto-chained on failure in finish_tmdb).
            tmdb_error_flag_retry_active[0] = True
            tmdb_error_flag_retry_rule_idx[0] = 0
            apple_tv_auto_state["tmdb_missing_art"] = False
            apple_tv_auto_state["tmdb_exhausted_identity"] = None
            _perform_tmdb_error_flag_retry()
            _schedule_tmdb_quality_auto_expire()
            try:
                sys.stderr.write(
                    "pigeon: TMDb material quality issue flagged (⌘⇧X). "
                    "Logged immediately; retrying TMDb fetch; undo available for 20s.\n"
                )
                sys.stderr.flush()
            except Exception:
                pass
            return "break"

        purge_image_media_btn = tk.Button(
            content_buttons_row,
            text="Purge Image Media",
            command=on_purge_image_media,
            font=S_FONT_BTN,
            padx=8,
            pady=4,
        )
        purge_image_media_btn.pack(side=tk.LEFT, padx=(0, 8))
        if _PIGEON_EXT:
            _mq_glance = tk.Label(
                content_buttons_row,
                text="",
                fg="#e4e6ef",
                bg="#111",
                font=(_S, 13, "bold"),
                anchor=tk.W,
                justify=tk.LEFT,
            )
            _mq_glance.pack(side=tk.LEFT, padx=(8, 0), anchor=tk.CENTER)
            match_quality_glance_label_holder[0] = _mq_glance
            _attach_hover_tooltip(
                _mq_glance,
                "⌘⇧X / Ctrl+Shift+X toggles the quality flag: turning it on adds a persisted failure "
                "immediately; turning it off removes one (not below zero). Each new content event still "
                "scores one outcome after a successful populate (success, or FAILURE log only if the flag "
                "was on). Persisted in state.json; details + log on Advanced → TMDb.",
            )
            reset_mq_stats_btn = tk.Button(
                content_buttons_row,
                text="Reset match stats",
                command=on_reset_tmdb_match_quality_stats,
                font=S_FONT_BTN,
                padx=8,
                pady=4,
            )
            reset_mq_stats_btn.pack(side=tk.LEFT, padx=(10, 0))
            _attach_hover_tooltip(
                reset_mq_stats_btn,
                "Clears the ok / fail counts and % shown here (saved in state.json). Does not change "
                "tmdb_error_report.numbers, pigeonTMDBReport.csv, or tmdb_quality_event_reports.log.",
            )
            root.after_idle(_refresh_match_quality_glance_label)

        settings_footer_row = tk.Frame(settings_inner, bg="#111")
        settings_footer_row.pack(anchor=tk.W, fill=tk.X, pady=(16, 12))
        _frb = tk.Button(
            settings_footer_row,
            text="Reset",
            command=on_reset_pigeon_devices_and_media,
            font=S_FONT_BTN,
            padx=14,
            pady=6,
        )
        _frb.pack(side=tk.LEFT, padx=(0, 12))
        settings_footer_reset_holder[0] = _frb
        _fdb = tk.Button(
            settings_footer_row,
            text="Debug metadata",
            command=on_debug_streaming_slot_apple_tv,
            font=S_FONT_BTN,
            padx=10,
            pady=4,
        )
        _fdb.pack(side=tk.LEFT, padx=(0, 0))
        settings_footer_debug_holder[0] = _fdb
        tk.Label(
            settings_footer_row,
            text=f"Version {version_string()}",
            fg="#6d6d75",
            bg="#111",
            font=S_FONT_BODY,
        ).pack(side=tk.RIGHT, anchor=tk.E)
        _attach_hover_tooltip(
            _frb,
            "Clears all saved devices, pyatv credentials, discovery cache, and purges pigeonTMDB originals/backdrops/title-treatments.",
        )

        migrate_device_slots_from_legacy_if_needed()
        merge_legacy_saved_receivers_into_av_slot()
        streaming_slot_holder[0] = read_saved_streaming_device()
        avr_slot_holder[0] = read_saved_av_receiver()
        _seed_current_apple_tv_from_streaming_slot()
        # Prefer the location AV slot address over last_receiver — the latter can
        # linger on a stale IP after the Denon DHCP/address changes, which leaves
        # zone3 with an empty volume fraction (no red ring).
        _av_boot = avr_slot_holder[0]
        _av_adr = str((_av_boot or {}).get("address") or "").strip() if _av_boot else ""
        _last_rx_host = str(read_last_receiver().get("host") or "").strip()
        if _av_adr:
            if _av_adr != _last_rx_host:
                write_last_receiver(
                    host=_av_adr,
                    name=str(_av_boot.get("name") or "").strip() or None,
                    label=str(_av_boot.get("label") or "").strip() or None,
                    device_id=str(_av_boot.get("identifier") or "").strip() or None,
                )
            receiver_http_host["host"] = _av_adr
        else:
            receiver_http_host["host"] = _last_rx_host
        describe_current_apple_tv()
        _refresh_location_selector()
        _rebuild_paired_devices_panel()
        root.after(300, _schedule_refresh_pairing_leds)
        root.after(2500, _apple_tv_auto_poll_tick)
        root.after(PLAYBACK_UI_TICK_MS, _playback_ui_tick)

        def submit_command_entry(_event=None) -> str:
            nonlocal skip_cache
            if dev_phase != DevPhase.GRID and display_view_holder[0] != DisplayView.FIVE:
                return "break"
            _bump_pigeon_user_activity()
            now_sub = time.monotonic()
            if now_sub - _last_command_submit_mono[0] < 0.2:
                return "break"
            _last_command_submit_mono[0] = now_sub
            text = command_entry.get().strip()
            key = text.lower()
            if text and _PIGEON_EXT:
                m_tmdb = re.match(r"(?i)tmdb\s+(?P<q>.+)$", text)
                if m_tmdb:
                    qrest = m_tmdb.group("q").strip()
                    if qrest:
                        q2, pref = parse_tmdb_command_phrase(qrest)
                        if q2:
                            spawn_tmdb_poster_fetch(q2, prefer=pref, force=True)
                    else:
                        sys.stderr.write("pigeon: tmdb: empty query (use: tmdb Movie Title)\n")
                        sys.stderr.flush()
                else:
                    # Plain title or tv/movie hint — TMDb (auto picks movie vs TV by popularity)
                    q2, pref = parse_tmdb_command_phrase(text)
                    if q2:
                        spawn_tmdb_poster_fetch(q2, prefer=pref, force=True)
            elif text:
                sys.stderr.write(f"pigeon: command: {text}\n")
                sys.stderr.flush()
            command_entry.delete(0, tk.END)
            hide_command_entry()
            return "break"

        for _seq in ("<Return>", "<KeyPress-Return>"):
            command_entry.bind(_seq, submit_command_entry)
        command_entry.bind("<KP_Enter>", submit_command_entry)
        command_entry.bind("<KeyPress-KP_Enter>", submit_command_entry)

        label.bind("<Button-1>", on_click_focus, add="+")
        # Tap-to-wake: some platforms deliver release more reliably for “tap” than press alone.
        # While the saver layer is visible, a tap brightens it briefly (see CLOCK_SAVER_PEEK_S).
        def _on_label_button_release_peek_or_bump(event: tk.Event) -> None:
            nonlocal skip_cache
            if _PIGEON_EXT and clock_saver_composite_bgra is not None:
                now_e = time.monotonic()
                if _clock_saver_for_compose(now_e):
                    clock_saver_peek_until_mono[0] = now_e + CLOCK_SAVER_PEEK_S
                    _bump_pigeon_user_activity(event)
                    skip_cache = None
                    try:
                        render_once()
                    except Exception:
                        pass
                    return
            _bump_pigeon_user_activity(event)

        label.bind("<ButtonRelease-1>", _on_label_button_release_peek_or_bump, add="+")
        label.bind("<Double-Button-1>", on_double_click_scene, add="+")
        label.bind("<Button-3>", lambda e: try_cycle_dev_phase(None))

        # Omit command_entry: it needs local <Return> to submit before bind_all runs.
        for w in (
            root,
            shell,
            video_area,
            label,
            command_bar,
            settings_frame,
            settings_scroll_outer,
            settings_canvas,
            settings_inner,
            settings_footer_row,
        ):
            if w is not None:
                _prepend_hotkey_bindtag(w)

        for seq in ("<KeyPress-Tab>", "<Key-Tab>"):
            root.bind_class(HOTKEY_BINDTAG, seq, on_tab_key)
        root.bind_class(HOTKEY_BINDTAG, "<Control-KeyPress-Tab>", on_ctrl_tab)
        root.bind_class(HOTKEY_BINDTAG, "<Control-Key-Tab>", on_ctrl_tab)
        root.bind_class(HOTKEY_BINDTAG, "<KeyPress-s>", on_s_key)
        root.bind_class(HOTKEY_BINDTAG, "<KeyPress-S>", on_s_key)

        def on_return_overlay_command(event: tk.Event) -> str | None:
            _bump_pigeon_user_activity(event)
            w = event.widget
            if w == command_entry or str(w) == str(command_entry):
                return None
            if _widget_accepts_typing(w):
                return None
            if dev_phase == DevPhase.GRID or display_view_holder[0] == DisplayView.FIVE:
                if command_entry_visible:
                    try:
                        command_entry.focus_force()
                    except tk.TclError:
                        command_entry.focus_set()
                else:
                    show_command_entry()
                return "break"
            if _PIGEON_EXT:
                from pigeon.player_remote import queue_player_remote_action

                queue_player_remote_action(
                    streaming_slot_holder[0],
                    current_apple_tv=current_apple_tv,
                    action="select",
                    apple_tv_busy=apple_tv_busy,
                )
                return "break"
            return None

        root.bind_all("<KeyPress-Tab>", on_tab_key)
        root.bind_all("<Key-Tab>", on_tab_key)
        # Do not bind_all(Return): on macOS that can run before the Entry binding and swallow the key.
        for _rseq in ("<Return>", "<KeyPress-Return>", "<KP_Enter>", "<KeyPress-KP_Enter>"):
            root.bind_class(HOTKEY_BINDTAG, _rseq, on_return_overlay_command)

        def _send_player_play_pause_hotkey() -> bool:
            """
            If a **Player** slot is set, send play/pause on a worker thread (Apple TV: pyatv;
            Roku: ECP). Returns True when a send was queued (so Space should not fall through).
            """
            if not _PIGEON_EXT:
                return False
            if apple_tv_busy["active"]:
                return False
            row = streaming_slot_holder[0]
            if not row:
                return False
            if row_is_playback_apple_tv(row):
                ident = str(current_apple_tv.get("identifier") or "").strip() or str(
                    row.get("identifier") or ""
                ).strip()
                addr = str(current_apple_tv.get("address") or "").strip() or str(
                    row.get("address") or ""
                ).strip()
                if not ident:
                    return False
                if not addr:
                    addr = ident

                try:
                    from pigeon.apple_tv_now_playing import enqueue_apple_tv_remote_command

                    if enqueue_apple_tv_remote_command(
                        device_identifier=ident,
                        device_address=addr,
                        method_name="play_pause",
                        scan_timeout_s=3,
                    ):
                        return True
                except Exception:
                    pass
                return False
            try:
                from pigeon.roku_ecp import resolve_roku_ecp_base_url_for_row, roku_send_play_pause

                rbase = str(resolve_roku_ecp_base_url_for_row(row) or "").strip()
                if not rbase:
                    return False
            except Exception:
                return False

            def _work_roku() -> None:
                try:
                    from pigeon.roku_ecp import roku_send_play_pause

                    roku_send_play_pause(base_url=rbase, timeout=3.0)
                except Exception:
                    pass

            threading.Thread(target=_work_roku, daemon=True).start()
            return True

        def on_space_play(event: tk.Event) -> str | None:
            nonlocal dev_phase, skip_cache
            if _widget_accepts_typing(event.widget):
                return None
            _bump_pigeon_user_activity(event)
            now = time.monotonic()
            if now - _last_space_mono[0] < 0.12:
                return "break"
            _last_space_mono[0] = now
            if dev_phase == DevPhase.MAIN_SETTINGS and main_settings_widget is not None:
                action = main_settings_widget.activate()
                if action == "exit":
                    dev_phase = DevPhase.OFF
                    skip_cache = None
                    sync_developer_chrome()
                    render_once()
                else:
                    _handle_main_settings_action(action)
                    skip_cache = None
                    render_once()
                return "break"
            if _send_player_play_pause_hotkey():
                return "break"
            # After a TMDb fetch, bring backdrop + title logo to the screen (toggle_play often no-ops here).
            if saved_backdrop_master_bgr is not None and not use_backdrop_scene:
                apply_saved_tmdb_backdrop_to_display()
                return "break"
            toggle_play()
            return "break"

        # <KeyPress-Space> is invalid on some Tk builds (TclError: bad keysym "Space").
        for _space_seq in ("<space>", "<KeyPress-space>"):
            root.bind_all(_space_seq, on_space_play)

        def on_display_view_digit(event: tk.Event) -> str | None:
            if _widget_accepts_typing(event.widget):
                return None
            if not _PIGEON_EXT:
                return None
            ch = getattr(event, "char", "") or ""
            if ch not in "145":
                return None
            nonlocal skip_cache, dev_phase
            st_md = main_settings_widget.state if main_settings_widget is not None else None
            md_open = bool(
                st_md is not None
                and dev_phase == DevPhase.MAIN_SETTINGS
                and st_md.show_metadata_debug
            )
            if md_open and ch != "4":
                # Inspector is modal: EXIT (or [4] → legacy) are the only ways out.
                _bump_pigeon_user_activity(event)
                return "break"
            if ch == "4" and st_md is not None:
                if dev_phase == DevPhase.MAIN_SETTINGS and st_md.keyboard is not None:
                    return None
                if md_open:
                    # [4] on the inspector → legacy metadata view (view 4).
                    st_md.exit_pigeon_settings()
                    main_settings_widget.invalidate()
                    dev_phase = DevPhase.OFF
                    display_view_holder[0] = DisplayView.FOUR
                    view_four_subview_holder[0] = 0
                else:
                    # [4] anywhere (incl. legacy view 4) → open the inspector.
                    st_md.open_metadata_debug()
                    main_settings_widget.invalidate()
                    if dev_phase != DevPhase.MAIN_SETTINGS:
                        dev_phase = DevPhase.MAIN_SETTINGS
                        try:
                            main_settings_widget.prefetch_scans_for_settings()
                        except Exception:
                            pass
                skip_cache = None
                sync_developer_chrome()
                _bump_pigeon_user_activity(event)
                _capture_last_view_one_layout_from_live_view()
                render_once()
                return "break"
            if ch == "1" and display_view_holder[0] == DisplayView.ONE:
                st_key = int(getattr(event, "state", 0))
                sh_key = bool(st_key & 0x0001)
                opt_key = bool(st_key & 0x0008) or bool(st_key & 0x20000)
                if sh_key:
                    # Shift+1: cycle view-one layout (full / simple / poster).
                    _vv_now = _current_view_one_variant()
                    if (
                        _vv_now is not None
                        and variant_has_alternate is not None
                        and not variant_has_alternate(_vv_now)
                    ):
                        _bump_pigeon_user_activity(event)
                        return "break"
                    _cur = int(view_one_layout_holder[0])
                    if _cur == int(ViewOneLayout.PIGEON_FULL):
                        view_one_layout_holder[0] = int(ViewOneLayout.PIGEON_SIMPLE)
                    elif _cur == int(ViewOneLayout.PIGEON_SIMPLE):
                        view_one_layout_holder[0] = int(ViewOneLayout.PIGEON_POSTER)
                    else:
                        view_one_layout_holder[0] = int(ViewOneLayout.PIGEON_FULL)
                    skip_cache = None
                    _bump_pigeon_user_activity(event)
                    _capture_last_view_one_layout_from_live_view()
                    return "break"
                if opt_key:
                    _bump_pigeon_user_activity(event)
                    return "break"
                _bump_pigeon_user_activity(event)
                return "break"
            if ch == "4" and display_view_holder[0] == DisplayView.FOUR:
                view_four_subview_holder[0] = (int(view_four_subview_holder[0]) + 1) % 3
                skip_cache = None
                _bump_pigeon_user_activity(event)
                return "break"
            if ch == "5" and display_view_holder[0] == DisplayView.FIVE:
                view_five_mode_holder[0] = (int(view_five_mode_holder[0]) + 1) % 3
                skip_cache = None
                _bump_pigeon_user_activity(event)
                return "break"
            display_view_holder[0] = DisplayView(int(ch))
            if ch == "1":
                view_one_layout_holder[0] = int(ViewOneLayout.PIGEON_FULL)
            if ch == "4":
                view_four_subview_holder[0] = 0
            if ch == "5":
                view_five_mode_holder[0] = 0
            skip_cache = None
            _bump_pigeon_user_activity(event)
            _capture_last_view_one_layout_from_live_view()
            return "break"

        for _dv_ch in ("1", "4", "5"):
            root.bind_all(f"<KeyPress-{_dv_ch}>", on_display_view_digit)

        # [2] is free (display views use 1/4/5 only) — force clock saver on/off.
        root.bind_all("<KeyPress-2>", _toggle_clock_saver_force)

        def on_arrow_remote(event: tk.Event) -> str | None:
            if _widget_accepts_typing(event.widget):
                return None
            if not _PIGEON_EXT:
                return None
            ks = getattr(event, "keysym", "") or ""
            if ks not in ("Up", "Down", "Left", "Right"):
                return None
            if dev_phase == DevPhase.MAIN_SETTINGS and main_settings_widget is not None:
                nonlocal skip_cache
                if ks == "Right":
                    main_settings_widget.navigate(forward=True)
                    skip_cache = None
                    render_once()
                    return "break"
                if ks == "Left":
                    main_settings_widget.navigate(forward=False)
                    skip_cache = None
                    render_once()
                    return "break"
                return "break"
            from pigeon.player_remote import queue_player_remote_action

            st = int(getattr(event, "state", 0))
            if st & 0x0004:
                return None
            sh = bool(st & 0x0001)
            meta_cmd = (
                bool(st & 0x100000)
                or bool(st & 0x080000)
                or bool(st & 0x0008)
                or bool(st & 0x20000)
            )
            row = streaming_slot_holder[0]
            if meta_cmd:
                cmd_map = {
                    "Up": "power_on",
                    "Down": "power_off",
                    "Left": "back",
                    "Right": "home",
                }
                act = cmd_map.get(ks)
                if act:
                    queue_player_remote_action(
                        row,
                        current_apple_tv=current_apple_tv,
                        action=act,
                        apple_tv_busy=apple_tv_busy,
                    )
                return "break"
            if sh:
                if ks == "Up":
                    queue_player_remote_action(
                        row,
                        current_apple_tv=current_apple_tv,
                        action="volume_up",
                        apple_tv_busy=apple_tv_busy,
                    )
                elif ks == "Down":
                    queue_player_remote_action(
                        row,
                        current_apple_tv=current_apple_tv,
                        action="volume_down",
                        apple_tv_busy=apple_tv_busy,
                    )
                elif ks == "Left":
                    queue_player_remote_action(
                        row,
                        current_apple_tv=current_apple_tv,
                        action="skip_back",
                        apple_tv_busy=apple_tv_busy,
                    )
                elif ks == "Right":
                    queue_player_remote_action(
                        row,
                        current_apple_tv=current_apple_tv,
                        action="skip_fwd",
                        apple_tv_busy=apple_tv_busy,
                    )
                return "break"
            nav = {
                "Up": "nav_up",
                "Down": "nav_down",
                "Left": "nav_left",
                "Right": "nav_right",
            }.get(ks)
            if nav:
                queue_player_remote_action(
                    row,
                    current_apple_tv=current_apple_tv,
                    action=nav,
                    apple_tv_busy=apple_tv_busy,
                )
            return "break"

        for _ak in ("<KeyPress-Up>", "<KeyPress-Down>", "<KeyPress-Left>", "<KeyPress-Right>"):
            root.bind_all(_ak, on_arrow_remote)

        def on_escape(event: tk.Event) -> str | None:
            _bump_pigeon_user_activity(event)
            if command_entry_visible:
                hide_command_entry()
                return "break"
            quit_app()
            return "break"

        root.bind_all("<Escape>", on_escape)
        root.bind_all("<KeyPress-F9>", lambda e: try_cycle_dev_phase(None))
        root.bind_all("<KeyPress-F10>", on_f10_key)

        def on_ctrl_shift_tab_advanced(event: tk.Event) -> str | None:
            if _widget_accepts_typing(event.widget):
                return None
            if not _PIGEON_EXT:
                return None
            st = int(getattr(event, "state", 0))
            if not (st & 0x0004) or not (st & 0x0001):
                return None
            _bump_pigeon_user_activity(event)
            now = time.monotonic()
            if now - _last_adv_shift_tab_mono[0] < 0.35:
                return "break"
            _last_adv_shift_tab_mono[0] = now
            _open_advanced_capability_matrix()
            return "break"

        for _adv_hot in (
            "<Control-Shift-KeyPress-Tab>",
            "<Control-Shift-Key-Tab>",
            "<Control-Shift-KeyPress-ISO_Left_Tab>",
        ):
            root.bind_all(_adv_hot, on_ctrl_shift_tab_advanced)
        for _stab in (
            "<KeyPress-ISO_Left_Tab>",
            "<Shift-KeyPress-Tab>",
            "<Shift-Key-Tab>",
        ):
            root.bind_all(_stab, on_shift_tab_dev_cycle)
        for _tmdb_key in ("<KeyPress-question>", "<Shift-KeyPress-slash>"):
            root.bind_all(_tmdb_key, on_tmdb_retry_hotkey)
        for _tmdb_qx in (
            "<Command-Shift-KeyPress-x>",
            "<Command-Shift-KeyPress-X>",
            "<Control-Shift-KeyPress-x>",
            "<Control-Shift-KeyPress-X>",
        ):
            root.bind_all(_tmdb_qx, on_tmdb_quality_error_report_hotkey)

        def on_tmdb_match_mode_toggle(event: tk.Event) -> str | None:
            _bump_pigeon_user_activity(event)
            if not _PIGEON_EXT:
                return None
            if _widget_accepts_typing(event.widget):
                return None
            now_tm = time.monotonic()
            if now_tm - _last_tmdb_match_toggle_mono[0] < 0.2:
                return "break"
            _last_tmdb_match_toggle_mono[0] = now_tm
            try:
                from pigeon.tmdb_poster import toggle_tmdb_match_mode
            except ImportError:
                return None
            mode = toggle_tmdb_match_mode()
            sys.stderr.write(f"pigeon: TMDb title match: {mode} (Ctrl+Shift+M to toggle)\n")
            sys.stderr.flush()
            return "break"

        def on_dev_series_title_training_hotkey(event: tk.Event) -> str | None:
            """Dev-only: map current playback metadata fingerprint → series title (training JSON)."""
            _bump_pigeon_user_activity(event)
            if not _PIGEON_EXT:
                return None
            if dev_phase != DevPhase.GRID and display_view_holder[0] != DisplayView.FIVE:
                return None
            if _widget_accepts_typing(event.widget):
                return None
            lm = apple_tv_auto_state.get("last_metadata")
            if not isinstance(lm, dict) or not any(
                str(lm.get(k) or "").strip()
                for k in ("title", "series_name", "artist", "album", "query")
            ):
                messagebox.showinfo(
                    "Series title training",
                    "No playback metadata snapshot yet. Start playback and wait for a poll, then try again.",
                    parent=root,
                )
                return "break"
            try:
                from pigeon.raw_title import raw_title_from_metadata_dict
                from pigeon.series_title_training import add_training_mapping
            except ImportError:
                messagebox.showinfo(
                    "Series title training",
                    "Training modules are not available in this build.",
                    parent=root,
                )
                return "break"

            rt = raw_title_from_metadata_dict(lm)
            sig = rt.training_signature_normalized()
            if not sig:
                messagebox.showinfo(
                    "Series title training",
                    "Could not build a stable fingerprint from the current metadata.",
                    parent=root,
                )
                return "break"

            tw = tk.Toplevel(root)
            tw.title("Series title training")
            tw.transient(root)
            tk.Label(
                tw,
                text="Map this playback fingerprint to a TMDb series title.\n"
                f"Saved under {PIGEON_STATE_DIR_TILDE}/series_title_training_hints.json",
                justify="center",
            ).pack(padx=12, pady=(10, 4))
            preview = sig[:180] + ("…" if len(sig) > 180 else "")
            tk.Label(
                tw,
                text=f"Key: {preview}",
                fg="#888",
                wraplength=420,
                justify="left",
            ).pack(padx=12, pady=4)
            ent = tk.Entry(tw, width=48)
            ent.pack(padx=12, pady=6)
            hint = (rt.layer_series_title or rt.raw_series_name or rt.raw_title or "").strip()
            if hint:
                ent.insert(0, hint)

            def _save_training() -> None:
                q_sp = ent.get().strip()
                ok_h, msg_h = add_training_mapping(sig, q_sp)
                if ok_h:
                    sys.stderr.write(f"pigeon: series title training: {msg_h}\n")
                    sys.stderr.flush()
                    tw.destroy()
                    if q_sp:
                        spawn_tmdb_poster_fetch(q_sp, prefer=str(apple_tv_auto_state.get("prefer") or "auto"), force=True)
                else:
                    messagebox.showerror("Series title training", msg_h, parent=tw)

            bf = tk.Frame(tw)
            bf.pack(pady=(4, 12))
            tk.Button(bf, text="Save & refetch TMDb", command=_save_training).pack(side=tk.LEFT, padx=6)
            tk.Button(bf, text="Cancel", command=tw.destroy).pack(side=tk.LEFT, padx=6)
            root.after_idle(lambda: ent.focus_set())
            return "break"

        for _plus_key in (
            "<KeyPress-plus>",
            "<Shift-KeyPress-equal>",
            "<Shift-KeyPress-plus>",
            "<KeyPress-KP_Add>",
        ):
            root.bind_all(_plus_key, on_dev_series_title_training_hotkey)

        root.bind_all("<Control-Shift-KeyPress-m>", on_tmdb_match_mode_toggle)
        root.bind_all("<Control-Shift-KeyPress-M>", on_tmdb_match_mode_toggle)

        root.bind_all("<Control-Shift-KeyPress-s>", lambda e: toggle_scene(require_overlay=False))
        root.bind_all("<Control-Shift-KeyPress-S>", lambda e: toggle_scene(require_overlay=False))

        # Pixel-aspect override: hold P+A+R together to toggle auto ↔ off.
        _par_chord_held: set[str] = set()
        _par_chord_fired = [False]

        def _on_par_chord_press(event: tk.Event) -> str | None:
            nonlocal skip_cache
            if _widget_accepts_typing(event.widget):
                return None
            ks = (getattr(event, "keysym", "") or "").lower()
            if ks not in ("p", "a", "r"):
                return None
            _par_chord_held.add(ks)
            if not ({"p", "a", "r"} <= _par_chord_held) or _par_chord_fired[0]:
                return None
            _par_chord_fired[0] = True
            try:
                from pigeon.display_par import cycle_par_mode

                mode, par, reason = cycle_par_mode()
            except Exception as exc:
                sys.stderr.write(f"pigeon: PAR chord failed: {exc}\n")
                sys.stderr.flush()
                return "break"
            msg = f"pigeon: display PAR → mode={mode}  effective={par:.4f}  ({reason})"
            sys.stderr.write(msg + "\n")
            sys.stderr.flush()
            try:
                from pigeon.pi_diagnostics import append_pigeon_log

                append_pigeon_log(msg)
            except Exception:
                pass
            skip_cache = None
            try:
                render_once()
            except Exception:
                pass
            return "break"

        def _on_par_chord_release(event: tk.Event) -> str | None:
            ks = (getattr(event, "keysym", "") or "").lower()
            if ks in ("p", "a", "r"):
                _par_chord_held.discard(ks)
            if not _par_chord_held:
                _par_chord_fired[0] = False
            return None

        for _par_ch in ("p", "a", "r", "P", "A", "R"):
            root.bind_all(f"<KeyPress-{_par_ch}>", _on_par_chord_press, add="+")
            root.bind_all(f"<KeyRelease-{_par_ch}>", _on_par_chord_release, add="+")

        def _focus_when_mapped(_event=None) -> None:
            try:
                root.focus_force()
            except tk.TclError:
                root.focus_set()

        root.bind("<Map>", _focus_when_mapped)
        root.after_idle(_focus_when_mapped)

        for _pigeon_act in ("<Button-1>", "<B1-Motion>", "<KeyPress>"):
            root.bind_all(_pigeon_act, _bump_pigeon_user_activity, add="+")

        # Serial rotary (non-HID USB): CW/CCW/PUSH → navigate / activate.
        # Prefer a direct callback so settings work even when Tk focus is elsewhere.
        def _enter_main_settings_for_rotary() -> bool:
            """Bring up main settings so the encoder can drive the new menus."""
            nonlocal skip_cache, dev_phase
            if main_settings_widget is None:
                return False
            if dev_phase == DevPhase.MAIN_SETTINGS:
                return True
            try:
                if main_settings_widget.state.keyboard_open:
                    main_settings_widget.state.close_keyboard(commit=False)
                    main_settings_widget.invalidate()
            except Exception:
                pass
            try:
                from pigeon.widgets.ui_color_settings import load_persisted_theme_into_state

                load_persisted_theme_into_state(main_settings_widget.state)
                main_settings_widget.invalidate()
            except Exception:
                pass
            dev_phase = DevPhase.MAIN_SETTINGS
            try:
                main_settings_widget.prefetch_scans_for_settings()
            except Exception:
                pass
            try:
                from pigeon.weather import DEFAULT_WEATHER_ZIP, refresh_weather

                refresh_weather(zip_code=DEFAULT_WEATHER_ZIP, force=True)
            except Exception:
                pass
            skip_cache = None
            sync_developer_chrome()
            render_once()
            return True

        def _on_rotary_action(action: str) -> None:
            nonlocal skip_cache, dev_phase
            _bump_pigeon_user_activity()
            was_main = dev_phase == DevPhase.MAIN_SETTINGS
            if not _enter_main_settings_for_rotary():
                try:
                    from pigeon.rotary_serial import inject_keysym

                    keysym = {
                        "forward": "Right",
                        "backward": "Left",
                        "activate": "space",
                    }.get(action)
                    if keysym:
                        inject_keysym(root, keysym)
                except Exception:
                    pass
                return
            # First click that opens settings should not also activate a control.
            if action == "activate" and not was_main:
                return
            if action == "forward":
                main_settings_widget.navigate(forward=True)
                skip_cache = None
                render_once()
                return
            if action == "backward":
                main_settings_widget.navigate(forward=False)
                skip_cache = None
                render_once()
                return
            if action == "activate":
                ms_action = main_settings_widget.activate()
                if ms_action == "exit":
                    dev_phase = DevPhase.OFF
                    skip_cache = None
                    sync_developer_chrome()
                    render_once()
                else:
                    _handle_main_settings_action(ms_action)
                    skip_cache = None
                    render_once()

        try:
            from pigeon.rotary_serial import start_rotary_serial_listener

            start_rotary_serial_listener(root, on_action=_on_rotary_action)
        except Exception as _rotary_exc:
            sys.stderr.write(f"pigeon: rotary_serial: not started: {_rotary_exc}\n")
            sys.stderr.flush()

        def _apply_shell_size(w: int, h: int) -> None:
            nonlocal skip_cache, black_photo, scaled_display, scaled_version
            if w < 32 or h < 32:
                return
            if display_dims[0] == w and display_dims[1] == h:
                return
            display_dims[0] = w
            display_dims[1] = h
            # Display geometry changed — re-resolve auto PAR next present.
            try:
                from pigeon.display_par import clear_auto_par_cache

                clear_auto_par_cache()
            except Exception:
                pass
            fit_holder[0] = SceneFit(target_w=w, target_h=h)
            black_photo = None
            skip_cache = None
            if use_backdrop_scene and backdrop_master_bgr is not None and not _PIGEON_EXT:
                from pigeon.image_ui_protocol import backdrop_scene_bgr_for_display

                scaled_display = backdrop_scene_bgr_for_display(
                    backdrop_master_bgr,
                    w,
                    h,
                    app_logo_letterbox_fit=backdrop_app_logo_letterbox_fit,
                    app_logo_clock_saver_style=_app_logo_clock_saver_style_now(),
                )
                scaled_version += 1
            sync_developer_chrome()

        def _on_shell_configure(event: tk.Event) -> None:
            if event.widget is not shell:
                return
            w, h = int(event.width), int(event.height)
            # Apply on every configure so chrome (buttons, HUD, bars) tracks live resize;
            # debounced after() only ran after drag ended.
            _apply_shell_size(w, h)

        sync_developer_chrome()

        def render_once() -> None:
            nonlocal last_frame, brightness_current, scaled_display, scaled_version, skip_cache, black_photo
            nonlocal scene_enabled, playing, use_backdrop_scene, backdrop_master_bgr

            if _render_after_id[0] is not None:
                try:
                    root.after_cancel(_render_after_id[0])
                except tk.TclError:
                    pass
                _render_after_id[0] = None

            _render_tick_t0 = time.perf_counter()
            now = time.monotonic()
            _intro_mono = post_splash_mono[0]
            if _maybe_exit_settings_menus_on_idle(now):
                # Fall through to OFF-phase compose (now-playing or clock saver).
                pass

            def _schedule_next_render() -> None:
                elapsed_ms = int((time.perf_counter() - _render_tick_t0) * 1000.0)
                interval = _next_render_ms()
                delay = max(interval, elapsed_ms + 1)
                _schedule_render_oneshot(delay)

            def _next_render_ms() -> int:
                if playing:
                    return frame_interval_ms
                # Post-splash clock fade-up needs a smooth cadence.
                if _PIGEON_EXT and _clock_startup_intro_opacity(time.monotonic()) is not None:
                    return 33
                # WiFi / box scan spinner: keep responsive without 60 FPS full-frame uploads on Pi.
                if (
                    dev_phase == DevPhase.MAIN_SETTINGS
                    and main_settings_widget is not None
                    and (
                        main_settings_widget.state.wifi_scanning
                        or main_settings_widget.state.wifi_connecting
                        or main_settings_widget.state.box2_devices.scanning
                        or main_settings_widget.state.box3_devices.scanning
                        or main_settings_widget.state.location_switching
                    )
                ):
                    return 50 if sys.platform.startswith("linux") else 16
                # Circles poster searching spinner while TMDb fetch is in flight.
                if (
                    _view_one_uses_now_playing_screen()
                    and view_circles_widget is not None
                    and view_circles_widget.searching
                ):
                    return 50 if sys.platform.startswith("linux") else 16
                # Static settings UI: wake often enough for input, but avoid busy PhotoImage paste.
                if dev_phase == DevPhase.MAIN_SETTINGS and sys.platform.startswith("linux"):
                    return 500
                return paused_interval_ms

            # With ext + splash, only count this window **after** splash removal.
            _startup_elapsed = -1.0
            if _PIGEON_EXT:
                _startup_elapsed = (now - _intro_mono) if _intro_mono is not None else -1.0
            if (
                _PIGEON_EXT
                and not _startup_splash_complete[0]
                and _intro_mono is not None
                and (
                    SKIP_POST_SPLASH_STARTUP_TRANSITION
                    or _startup_elapsed >= STARTUP_PIGEON_WORDMARK_MAX_S
                )
            ):
                _startup_splash_complete[0] = True
                if (
                    STARTUP_AUTO_RESTORE_SAVED_BACKDROP
                    and saved_backdrop_master_bgr is not None
                    and scene_enabled
                    and not use_backdrop_scene
                ):
                    apply_saved_tmdb_backdrop_to_display()
                    # Inner ``render_once`` schedules the loop, but guarantee a timer if that path returns early.
                    _schedule_next_render()
                    return
                skip_cache = None
                _warm_playback_overlay_blits()

            if _PIGEON_EXT:
                _compose_idle_strength_holder[0] = _update_idle_dim_strength(now)
            else:
                _compose_idle_strength_holder[0] = 0.0
            t = (now - brightness_t0) / brightness_duration_s if brightness_duration_s > 0 else 1.0
            if t <= 0.0:
                brightness_current = brightness_from
            elif t >= 1.0:
                brightness_current = brightness_target
            else:
                brightness_current = brightness_from + (brightness_target - brightness_from) * t

            if not scene_enabled:
                if _PIGEON_EXT:
                    settings_tok = (
                        main_settings_widget.frame_cache_token()
                        if (
                            dev_phase == DevPhase.MAIN_SETTINGS
                            and main_settings_widget is not None
                        )
                        else ()
                    )
                    (
                        _tmdb_x_bgr_off,
                        _tmdb_x_alpha_off,
                        _tmdb_x_caption_off,
                        _tmdb_x_phase_off,
                    ) = _tmdb_quality_toggle_overlay_state(now)
                    tmdb_x_animating_off = _tmdb_x_alpha_off > 1e-6
                    tmdb_flag_badge_on_off = bool(tmdb_quality_error_flag[0])
                    tmdb_x_cache_key_off = (
                        int(round(_tmdb_x_alpha_off * 1000.0))
                        + (int(_tmdb_x_phase_off) * 2000)
                        + (1 if tuple(_tmdb_x_bgr_off) == (0, 0, 255) else 0)
                    )
                    no_anim = True
                    if (
                        dev_phase == DevPhase.MAIN_SETTINGS
                        and main_settings_widget is not None
                    ):
                        st_ms = main_settings_widget.state
                        no_anim = not (
                            st_ms.wifi_scanning
                            or st_ms.wifi_connecting
                            or st_ms.box2_devices.scanning
                            or st_ms.box3_devices.scanning
                            or st_ms.location_switching
                        )
                    if (
                        no_anim
                        and view_circles_widget is not None
                        and view_circles_widget.searching
                    ):
                        no_anim = False
                    if tmdb_x_animating_off or tmdb_flag_badge_on_off:
                        no_anim = False
                    # Must re-evaluate every second: circles clock digits + metadata-idle saver.
                    # A static scene_off_key previously froze the UI after the first frame and
                    # prevented the 2-minute clock saver from ever arming when scene was off.
                    tick_key_off = (
                        main_settings_widget.frame_cache_token()
                        if (
                            dev_phase == DevPhase.MAIN_SETTINGS
                            and main_settings_widget is not None
                        )
                        else int(time.time())
                    )
                    clock_saver_off = 1 if _clock_saver_for_compose(now) else 0
                    if clock_saver_off:
                        no_anim = False
                    _np_vol_sig = ""
                    if view_circles_widget is not None:
                        try:
                            _np_vol_sig = (
                                f"{view_circles_widget._state.volume!s}\x1f"
                                f"{round(float(view_circles_widget._state.volume_fraction), 4)}"
                            )
                        except Exception:
                            _np_vol_sig = ""
                    scene_off_key = (
                        int(dev_phase),
                        settings_tok,
                        display_dims[0],
                        display_dims[1],
                        bool(
                            view_circles_widget is not None
                            and view_circles_widget.searching
                        ),
                        tmdb_x_cache_key_off,
                        1 if tmdb_flag_badge_on_off else 0,
                        tick_key_off,
                        clock_saver_off,
                        int(display_view_holder[0]),
                        _np_vol_sig,
                    )
                    if no_anim and skip_cache == scene_off_key:
                        _schedule_next_render()
                        return
                    out_bgr = _compose_shown_frame(None, 1.0)
                    out_bgr = _blend_view_four_debug(out_bgr)
                    if lerp_bgr_red_monochrome is not None:
                        sm = max(0.0, min(1.0, _compose_idle_strength_holder[0]))
                        if sm > 1e-6 and _effective_display_view() != DisplayView.FOUR:
                            out_bgr = lerp_bgr_red_monochrome(out_bgr, sm)
                    if tmdb_x_animating_off:
                        _blend_tmdb_quality_toggle_overlay(
                            out_bgr,
                            color_bgr=_tmdb_x_bgr_off,
                            alpha=_tmdb_x_alpha_off,
                            caption=_tmdb_x_caption_off,
                        )
                    if tmdb_flag_badge_on_off:
                        _blend_tmdb_quality_flag_badge(out_bgr)
                    _update_label_photo_from_bgr(label, out_bgr, label_live_photo)
                    skip_cache = scene_off_key
                else:
                    if black_photo is None:
                        black_photo = _bgr_to_tk_image(_black_screen_bgr())
                    label.configure(image=black_photo)
                    label.image = black_photo
                if _PIGEON_EXT:
                    try:
                        _capture_splash_underlay(out_bgr)
                    except NameError:
                        pass
                    except Exception:
                        pass
                _schedule_next_render()
                return

            if _backdrop_active_for_view():
                if backdrop_master_bgr is None:
                    use_backdrop_scene = False
            if not _backdrop_active_for_view():
                _static_compose_without_video = (
                    _PIGEON_EXT
                    and (
                        (
                            view_circles_widget is not None
                            and _effective_display_view() == DisplayView.ONE
                        )
                        or not scene_enabled
                        or _effective_display_view() in (
                            DisplayView.TWO,
                            DisplayView.THREE,
                            DisplayView.FOUR,
                            DisplayView.FIVE,
                            DisplayView.SIX,
                        )
                    )
                )
                if last_frame is None and not _static_compose_without_video:
                    _schedule_next_render()
                    return
                if not _PIGEON_EXT and scaled_display is None:
                    _schedule_next_render()
                    return

            brightness_animating = abs(brightness_current - brightness_target) > 1e-4
            # TMDb backdrop: fixed level; paused video uses 0.3.
            _backdrop_active = _backdrop_active_for_view()
            b_scene = BACKDROP_BRIGHTNESS if _backdrop_active else brightness_current
            b_key = round(float(b_scene), 4)
            # Clock text changes every second; include wall time when widgets are active.
            # Main settings has no clock — use its frame token so static UI can skip uploads.
            if (
                _PIGEON_EXT
                and dev_phase == DevPhase.MAIN_SETTINGS
                and main_settings_widget is not None
            ):
                tick_key = main_settings_widget.frame_cache_token()
            else:
                tick_key = int(time.time()) if _PIGEON_EXT else 0
            idle_s_here = (
                max(0.0, min(1.0, _compose_idle_strength_holder[0])) if _PIGEON_EXT else 0.0
            )
            idle_want_here = (
                (1.0 if _atv_idle_monochrome_active() else 0.0)
                if THEATER_IDLE_DIM_ENABLED
                else 0.0
            )
            # While easing toward dim or back to full bright, always composite (skip-cache can quantize away steps).
            idle_dim_animating = _PIGEON_EXT and abs(idle_s_here - idle_want_here) > 1e-4
            idle_cache_key = int(round(idle_s_here * 500)) if _PIGEON_EXT else 0
            ta_toast = _location_toast_alpha(now) if _PIGEON_EXT else 0.0
            location_toast_animating = _PIGEON_EXT and 0.0 < ta_toast < 1.0
            location_toast_cache_key = int(round(ta_toast * 1000)) if _PIGEON_EXT else 0
            clock_saver_cache_key = 1 if (_PIGEON_EXT and _clock_saver_for_compose(now)) else 0
            clock_intro_op = _clock_startup_intro_opacity(now) if _PIGEON_EXT else None
            clock_intro_cache_key = (
                int(round(float(clock_intro_op) * 1000.0)) if clock_intro_op is not None else -1
            )
            clock_intro_animating = clock_intro_op is not None
            clock_saver_peek_cache_key = (
                1 if (_PIGEON_EXT and now < clock_saver_peek_until_mono[0]) else 0
            )
            startup_wm_cache_key = 0
            paused_row_cache_key = 1 if (_PIGEON_EXT and _show_paused_row_overlay()) else 0
            mic_viz_cache_key = 0
            mic_eq_needs_composite = False
            if _PIGEON_EXT and status_bar_widget is not None:
                if status_bar_widget.set_theater_dim_suppressed(idle_s_here >= 0.5):
                    _warm_status_bar_blits()
            theater_dim_key = (
                1
                if (
                    _PIGEON_EXT
                    and status_bar_widget is not None
                    and status_bar_widget.theater_dim_suppressed
                )
                else 0
            )
            # Receiver overlay text must bust skip-cache when paused/backdrop.
            receiver_overlay_skip_sig = ""
            if _PIGEON_EXT:
                _set_playback_overlay_clock_saver_volume_flag()
                receiver_overlay_skip_sig = "\x1e".join(
                    str(receiver_overlay_state.get(k, "")) for k in ("incoming", "config", "volume")
                )
                receiver_overlay_skip_sig += "\x1e" + (
                    f"{int(bool(playback_overlay_flags.get('clock_saver_volume_only')))}"
                    f"{int(bool(playback_overlay_flags.get('clock_saver_netflix_full_overlay')))}"
                    f"{int(bool(playback_overlay_flags.get('badge_live_instead_of_logo')))}"
                )
            (_tmdb_x_bgr, _tmdb_x_alpha, _tmdb_x_caption, _tmdb_x_phase) = _tmdb_quality_toggle_overlay_state(now)
            tmdb_x_animating = _tmdb_x_alpha > 1e-6
            tmdb_x_cache_key = (
                int(round(_tmdb_x_alpha * 1000.0))
                + (int(_tmdb_x_phase) * 2000)
                + (1 if tuple(_tmdb_x_bgr) == (0, 0, 255) else 0)
            )
            tmdb_flag_badge_on = bool(tmdb_quality_error_flag[0])
            tmdb_flag_badge_cache_key = 1 if tmdb_flag_badge_on else 0

            if (
                not playing
                and not mic_eq_needs_composite
                and not brightness_animating
                and not idle_dim_animating
                and not location_toast_animating
                and not tmdb_x_animating
                and not clock_intro_animating
                and skip_cache
                == (
                    scaled_version,
                    b_key,
                    int(dev_phase),
                    int(display_view_holder[0]),
                    int(_effective_display_view()),
                    int(view_five_mode_holder[0]),
                    int(view_one_layout_holder[0]),
                    int(view_four_subview_holder[0]),
                    tick_key,
                    display_dims[0],
                    display_dims[1],
                    1 if _backdrop_active else 0,
                    idle_cache_key,
                    location_toast_cache_key,
                    clock_saver_cache_key,
                    clock_saver_peek_cache_key,
                    startup_wm_cache_key,
                    paused_row_cache_key,
                    receiver_overlay_skip_sig,
                    theater_dim_key,
                    mic_viz_cache_key,
                    tmdb_x_cache_key,
                    tmdb_flag_badge_cache_key,
                    clock_intro_cache_key,
                )
            ):
                _schedule_next_render()
                return

            if _PIGEON_EXT:
                shown = _compose_shown_frame(
                    last_frame if not _backdrop_active else None, b_scene
                )
                shown = _blend_view_four_debug(shown)
                if (
                    lerp_bgr_red_monochrome is not None
                    and _effective_display_view() != DisplayView.FOUR
                ):
                    sm = max(0.0, min(1.0, _compose_idle_strength_holder[0]))
                    if sm > 1e-6:
                        shown = lerp_bgr_red_monochrome(shown, sm)
            else:
                shown = _apply_brightness(scaled_display, b_scene)
            if tmdb_x_animating:
                _blend_tmdb_quality_toggle_overlay(
                    shown,
                    color_bgr=_tmdb_x_bgr,
                    alpha=_tmdb_x_alpha,
                    caption=_tmdb_x_caption,
                )
            if tmdb_flag_badge_on:
                _blend_tmdb_quality_flag_badge(shown)
            _update_label_photo_from_bgr(label, shown, label_live_photo)
            if _PIGEON_EXT:
                try:
                    _capture_splash_underlay(shown)
                except NameError:
                    pass
                except Exception:
                    pass

            if (
                not playing
                and not brightness_animating
                and not idle_dim_animating
                and not location_toast_animating
                and not tmdb_x_animating
                and not clock_intro_animating
            ):
                skip_cache = (
                    scaled_version,
                    b_key,
                    int(dev_phase),
                    int(display_view_holder[0]),
                    int(_effective_display_view()),
                    int(view_five_mode_holder[0]),
                    int(view_one_layout_holder[0]),
                    int(view_four_subview_holder[0]),
                    tick_key,
                    display_dims[0],
                    display_dims[1],
                    1 if _backdrop_active else 0,
                    idle_cache_key,
                    location_toast_cache_key,
                    clock_saver_cache_key,
                    clock_saver_peek_cache_key,
                    startup_wm_cache_key,
                    paused_row_cache_key,
                    receiver_overlay_skip_sig,
                    theater_dim_key,
                    mic_viz_cache_key,
                    tmdb_x_cache_key,
                    tmdb_flag_badge_cache_key,
                    clock_intro_cache_key,
                )
            else:
                skip_cache = None

            _schedule_next_render()

        def _invoke_render_after() -> None:
            _render_after_id[0] = None
            render_once()

        def _schedule_render_oneshot(delay_ms: int) -> None:
            if _render_after_id[0] is not None:
                try:
                    root.after_cancel(_render_after_id[0])
                except tk.TclError:
                    pass
            _render_after_id[0] = root.after(max(1, int(delay_ms)), _invoke_render_after)

        def _receiver_poll_tick() -> None:
            root.after(RECEIVER_POLL_MS, _receiver_poll_tick)
            if not _PIGEON_EXT:
                return
            if receiver_poll_busy["active"]:
                return

            # Keep poll host aligned with the saved AV slot (not a stale last_receiver).
            # Re-read from disk so an updated AVR IP (DHCP/move) applies without restart.
            try:
                _av_disk = read_saved_av_receiver()
            except Exception:
                _av_disk = None
            if _av_disk:
                avr_slot_holder[0] = _av_disk
            _av_row = avr_slot_holder[0]
            if _av_row:
                _slot_adr = str(_av_row.get("address") or "").strip()
                if _slot_adr and _slot_adr != str(receiver_http_host.get("host") or "").strip():
                    receiver_http_host["host"] = _slot_adr
            host = str(receiver_http_host.get("host") or "").strip()
            if not host:
                return

            def apply_overlay(incoming: str, config: str, volume: str) -> None:
                nonlocal skip_cache, last_device_interaction_mono
                from pigeon.widgets.playback_overlay import _looks_like_receiver_debug_blob

                receiver_poll_busy["active"] = False
                old_vol_raw = str(receiver_overlay_state.get("volume", ""))
                old_in = str(receiver_overlay_state.get("incoming", ""))
                old_cf = str(receiver_overlay_state.get("config", ""))
                new_in = "" if _looks_like_receiver_debug_blob(incoming) else str(incoming or "")
                new_cf = "" if _looks_like_receiver_debug_blob(config) else str(config or "")
                new_vol = str(volume or "")
                overlay_unchanged = (
                    old_in == new_in and old_cf == new_cf and old_vol_raw == new_vol
                )
                receiver_overlay_state["incoming"] = new_in
                receiver_overlay_state["config"] = new_cf
                receiver_overlay_state["volume"] = new_vol
                if overlay_unchanged:
                    if _view_one_uses_now_playing_screen():
                        _sync_now_playing_screen_state()
                    return
                last_device_interaction_mono = time.monotonic()
                if old_vol_raw != new_vol:
                    _bump_clock_saver_significant_device()
                _warm_playback_overlay_blits()
                skip_cache = None
                if _view_one_uses_now_playing_screen():
                    _sync_now_playing_screen_state()
                render_once()

            receiver_poll_busy["active"] = True

            def work() -> None:
                from pigeon.widgets.playback_overlay import (
                    _receiver_volume_display_line,
                    compose_playback_volume_widget_line,
                )

                r = None
                if host:
                    try:
                        from pigeon.receiver_denon import poll_denon_like_receiver

                        r = poll_denon_like_receiver(host, timeout=5.0)
                    except Exception:
                        r = None

                roku_line = ""
                roku_vol_pct = ""
                roku_app_name = ""
                try:
                    from pigeon.roku_ecp import (
                        fetch_roku_active_app_name,
                        fetch_roku_playback_line,
                        resolve_roku_ecp_base_url,
                        resolve_roku_ecp_base_url_for_row,
                    )

                    row_r = streaming_slot_holder[0]
                    rbase_line = ""
                    if row_r and not row_is_playback_apple_tv(row_r):
                        rbase_line = str(resolve_roku_ecp_base_url_for_row(row_r) or "").strip()
                    if not rbase_line:
                        rbase_line = str(resolve_roku_ecp_base_url() or "").strip()
                    if rbase_line:
                        rl, rv = fetch_roku_playback_line(rbase_line, timeout=3.0)
                        roku_line = rl or ""
                        roku_vol_pct = str(rv or "").strip()
                        # Keep all Roku ECP I/O off the Tk thread; this call can block on socket connect.
                        try:
                            apnm_w = fetch_roku_active_app_name(rbase_line)
                            if apnm_w:
                                roku_app_name = str(apnm_w).strip()
                        except Exception:
                            roku_app_name = ""
                except Exception:
                    roku_line = ""
                    roku_vol_pct = ""
                    roku_app_name = ""

                denon_vol_raw = ""
                if r is not None and r.ok:
                    denon_vol_raw = str(r.volume or "").strip()
                denon_vol_effective = (
                    denon_vol_raw if _receiver_volume_display_line(denon_vol_raw) else ""
                )
                merged_volume = compose_playback_volume_widget_line(
                    stream_row=streaming_slot_holder[0],
                    apple_tv_last_metadata=apple_tv_auto_state.get("last_metadata"),
                    denon_vol_effective=denon_vol_effective,
                    roku_tv_volume_percent=roku_vol_pct,
                )

                def apply() -> None:
                    rpl = receiver_panel_led_holder[0]
                    try:
                        _apply_body(rpl)
                    except tk.TclError:
                        pass
                    finally:
                        # Never leave the poll loop stuck if anything above threw.
                        receiver_poll_busy["active"] = False

                def _apply_body(rpl: object) -> None:
                    denon_ok = r is not None and r.ok
                    denon_standby = bool(r is not None and getattr(r, "standby", False))
                    receiver_standby_holder[0] = denon_standby
                    if denon_standby:
                        # Standby is treated as off: drop cached volume and overlay text.
                        denon_vol_cache["effective"] = ""
                        denon_vol_cache["mono_usable"] = 0.0
                        denon_vol_cache["np_hold"] = ""
                    elif denon_ok and denon_vol_effective:
                        denon_vol_cache["effective"] = denon_vol_effective
                        denon_vol_cache["mono_usable"] = time.monotonic()
                        denon_vol_cache["np_hold"] = denon_vol_effective
                    try:
                        from pigeon.runtime_state import update_receiver_runtime

                        update_receiver_runtime(
                            host=host,
                            reachable=bool(denon_ok and not denon_standby),
                            standby=denon_standby,
                        )
                    except Exception:
                        pass
                    try:
                        from pigeon.app_state import (
                            read_current_location_id,
                            read_saved_av_receiver,
                        )
                        from pigeon.observed_capability import (
                            update_observed_capabilities_from_receiver_poll,
                        )

                        update_observed_capabilities_from_receiver_poll(
                            str(read_current_location_id() or ""),
                            read_saved_av_receiver(),
                            denon_reachable=denon_ok and not denon_standby,
                            denon_volume_usable=bool(denon_vol_effective) and not denon_standby,
                            denon_has_incoming=bool(
                                r is not None
                                and not denon_standby
                                and str(r.incoming or "").strip()
                            ),
                            denon_has_config=bool(
                                r is not None
                                and not denon_standby
                                and str(r.config or "").strip()
                            ),
                        )
                    except Exception:
                        pass
                    _refresh_observed_pairing_led_rows()
                    if denon_standby:
                        receiver_telnet_debug_holder[0] = dict(
                            getattr(r, "telnet_debug", {}) or {}
                        ) if r is not None else {}
                        apply_overlay("", "", "")
                        if rpl is not None:
                            _paint_boolean_led(rpl, False)
                    elif r is not None and r.ok:
                        receiver_telnet_debug_holder[0] = dict(
                            getattr(r, "telnet_debug", {}) or {}
                        )
                        poll_inc = str(r.incoming or "").strip()
                        poll_cfg = str(r.config or "").strip()
                        if not poll_inc and not poll_cfg:
                            poll_inc, poll_cfg = _denon_telnet_audio_fallback()
                        apply_overlay(poll_inc, poll_cfg, merged_volume)
                        if rpl is not None:
                            _paint_boolean_led(rpl, True)
                    elif merged_volume:
                        receiver_telnet_debug_holder[0] = {}
                        apply_overlay("", "", merged_volume)
                        if rpl is not None:
                            _paint_boolean_led(rpl, False)
                    else:
                        receiver_telnet_debug_holder[0] = {}
                        apply_overlay("", "", "")
                        if rpl is not None:
                            _paint_boolean_led(rpl, False)
                    if roku_app_name:
                        _sync_streaming_badge_from_playback_sources(
                            None,
                            roku_app_name=roku_app_name,
                        )

                root.after(0, apply)

            def work_safe() -> None:
                try:
                    work()
                except Exception:
                    # Worker died before scheduling apply(); unblock future polls.
                    receiver_poll_busy["active"] = False

            threading.Thread(target=work_safe, daemon=True).start()

        def _capture_splash_underlay(out_bgr: np.ndarray) -> None:
            """Keep a window-sized UI snapshot for the splash fade unveil."""
            if startup_ph[0] is None or out_bgr is None or out_bgr.size == 0:
                return
            try:
                if out_bgr.shape[0] == WINDOW_H and out_bgr.shape[1] == WINDOW_W:
                    _splash_underlay_bgr[0] = np.ascontiguousarray(out_bgr)
                else:
                    _splash_underlay_bgr[0] = np.ascontiguousarray(
                        _present_frame_to_display(out_bgr, WINDOW_W, WINDOW_H)
                    )
            except Exception:
                pass

        def _splash_paint_view_one_under_overlay() -> None:
            """First full View 1 paint after splash (helpers now exist)."""
            if not _PIGEON_EXT:
                return
            _warm_view_one_under_splash()
            nonlocal skip_cache
            skip_cache = None
            render_once()
            try:
                root.update_idletasks()
            except tk.TclError:
                pass

        root.after(0, _splash_paint_view_one_under_overlay)

        shell.bind("<Configure>", _on_shell_configure)

        _warm_view_one_under_splash(phase="full-warm-bootstrap-end")
        _enable_now_playing_screen()
        skip_cache = None
        t_render0 = time.monotonic()
        render_once()
        _log_view_one_startup_phase(f"first-render ({(time.monotonic() - t_render0) * 1000.0:.0f} ms)")
        root.after(600, _receiver_poll_tick)

        if _PIGEON_EXT:
            tk.Widget.pack = _tk_pack_orig  # type: ignore[method-assign]
            tk.Widget.grid = _tk_grid_orig  # type: ignore[method-assign]
            tk.Widget.place = _tk_place_orig  # type: ignore[method-assign]
            try:
                root.update()
            except tk.TclError:
                pass
        bootstrap_done[0] = True
        _log_view_one_startup_phase("bootstrap-done")
        # Splash already finished before bootstrap started; run the deferred post-splash hook.
        if startup_ph[0] is None:
            _finish_post_splash_startup_transition()
        else:
            _try_remove_splash_overlay()

    if _PIGEON_EXT:
        # Play the splash at full rate on a free UI thread. Heavy bootstrap used to run in
        # parallel and starve ``after()``, freezing the last splash frame for many seconds.
        def _bootstrap_after_splash() -> None:
            if not splash_anim_done[0]:
                root.after(16, _bootstrap_after_splash)
                return
            try:
                sys.stderr.write(
                    f"pigeon: starting bootstrap after splash "
                    f"+{time.monotonic() - _app_startup_mono:.3f}s\n"
                )
                sys.stderr.flush()
            except Exception:
                pass
            bootstrap()

        root.after_idle(splash_tick)
        root.after_idle(_bootstrap_after_splash)
    else:
        root.after(1, bootstrap)
    try:
        root.mainloop()
    finally:
        if cap is not None:
            cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
