"""
PigeonOS 0.10 zoned now-playing skin (1280×800).

Portrait widgets live in ``pigeonAssets/nowPlaying/widget_np_01-02-03_*.svg``
and are placed into zones 1–3. 16×9 poster art uses ``widget_np_06_16x9`` /
``widget_np_07_16x9`` across zones 6 (1+2) or 7 (2+3). Dynamic layers (cast,
clock digital, volume pie, poster/album art, pause play) are drawn on top
with Pillow / OpenCV.
"""

from __future__ import annotations

import copy
import io
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr, cv_resize_interp
from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.np_layout import (
    CAST_ACTOR_SIZE_PX,
    CAST_CHAR_SIZE_PX,
    CAST_LOCAL_ROWS,
    CAST_NAMES_PER_ZONE,
    CAST_STRIP_STACK_GAP_PX,
    CAST_VIEW_H,
    CAST_VIEW_W,
    CLOCK_DAY_LOCAL,
    CLOCK_DIGITAL_LOCAL,
    CLOCK_DIGITAL_NUDGE,
    CLOCK_DIGITAL_SIZE_PX,
    CLOCK_HEADER_SIZE_PX,
    CLOCK_LOCAL_CX,
    CLOCK_LOCAL_CY,
    CLOCK_MONTH_DATE_LOCAL,
    CLOCK_VIEW_H,
    CLOCK_VIEW_W,
    DEFAULT_16X9_POSTER_ZONE,
    NOW_PLAYING_ZONES,
    NowPlayingZone,
    POSTER_1X1_LOCAL,
    POSTER_16X9_LOCAL,
    POSTER_16X9_VIEW_H,
    POSTER_16X9_VIEW_W,
    POSTER_2X3_LOCAL,
    STATUS_BAR_ELAPSED_LOCAL,
    STATUS_BAR_HANDOFF_S,
    STATUS_BAR_PAUSED_LOCAL,
    STATUS_BAR_PAUSED_SIZE_PX,
    STATUS_BAR_REMAINING_LOCAL,
    STATUS_BAR_SERVICE_LOCAL,
    STATUS_BAR_TIME_SIZE_PX,
    STATUS_BAR_TRACK,
    STATUS_BAR_VIEW_H,
    STATUS_BAR_VIEW_W,
    STATUS_BAR_VIEW_Y0,
    VOLUME_FORMAT_LOCAL,
    VOLUME_FORMAT_SIZE_PX,
    VOLUME_INNER_R,
    VOLUME_LOCAL_CX,
    VOLUME_LOCAL_CY,
    VOLUME_OUTER_R,
    VOLUME_SCALE_LOCAL,
    VOLUME_SCALE_SIZE_PX,
    VOLUME_SOURCE_LOCAL,
    VOLUME_SOURCE_SIZE_PX,
    VOLUME_VALUE_LOCAL,
    VOLUME_VALUE_SIZE_PX,
    VOLUME_VIEW_H,
    VOLUME_VIEW_W,
    WIDGET_FILENAMES,
    apply_16x9_poster_override,
    canonical_zone_widget,
    cast_names_for_zone,
    design_rect_from_local,
    design_xy_from_local,
    is_status_bar_widget,
    status_bar_elapsed_left_x,
    status_bar_elapsed_travel_x,
    status_bar_handoff_alphas,
    status_bar_service_has_room,
    strip_cast_columns,
    volume_readout_y_shift,
    wants_16x9_poster,
    widget_filename,
)
from pigeon.font_paths import (
    resolve_digital7_font,
    resolve_ui_font_extrabold,
    resolve_ui_font_extrabold_italic,
    resolve_ui_font_light_italic,
    resolve_ui_font_medium_italic,
    resolve_ui_font_semibold,
)
from pigeon.widgets.playback_overlay import (
    _receiver_audio_display_line,
    _receiver_volume_display_line,
    volume_fraction_from_display_line,
)
from pigeon.widgets.search_spinner import (
    advance_angle_deg,
    blit_spinner_patch,
    build_search_spinner_frames,
    rotated_patch_for_angle,
)

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

_SVG_W = 398.0
_SVG_H = 488.0

_COLOR_BG_HEX = "#000000"
_COLOR_ACCENT_BGR = (0, 0, 255)  # #FF0000 — legacy default (mapped to theme.ui)
_COLOR_UNPLAYED_BGR = (147, 147, 147)  # #939393
_COLOR_BUTTON_BGR = (35, 35, 35)  # #232323 — legacy; inner discs use pure black
_COLOR_CENTER_BLACK_HEX = "#000000"
_COLOR_CENTER_BLACK_BGR = (0, 0, 0)
_COLOR_CHROME_BGR = (147, 147, 147)  # #939393
_COLOR_CHROME_RGB = (147, 147, 147)


def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
    if len(h) != 6:
        return _COLOR_ACCENT_BGR
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return _COLOR_ACCENT_BGR
    return (b, g, r)


def _darken_hex(hex_color: str, *, factor: float = 0.5) -> str:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) == 3:
        h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
    if len(h) != 6:
        return "#800000"
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return "#800000"
    f = max(0.0, min(1.0, float(factor)))
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"


@dataclass(frozen=True)
class _NpTheme:
    """Settings color page → now-playing paint roles."""

    ui_hex: str = "#4EA6F7"
    accent_hex: str = "#FFFFFF"
    button_hex: str = "#202020"

    @property
    def ui_bgr(self) -> tuple[int, int, int]:
        return _hex_to_bgr(self.ui_hex)

    @property
    def accent_bgr(self) -> tuple[int, int, int]:
        return _hex_to_bgr(self.accent_hex)

    @property
    def button_bgr(self) -> tuple[int, int, int]:
        return _hex_to_bgr(self.button_hex)

    @property
    def tick_active(self) -> str:
        return self.ui_hex

    @property
    def tick_dim(self) -> str:
        return _darken_hex(self.ui_hex, factor=0.5)

    @property
    def cache_key(self) -> tuple[str, str, str]:
        return (self.ui_hex.lower(), self.accent_hex.lower(), self.button_hex.lower())


def np_theme_from_settings() -> _NpTheme:
    """Load accent / ui / button colors from the settings color page."""
    try:
        from pigeon.widgets.ui_color_settings import (
            hex_for_color_key,
            read_ui_color_keys,
        )

        keys = read_ui_color_keys()
        return _NpTheme(
            ui_hex=hex_for_color_key("ui", keys.get("ui", "blue")),
            accent_hex=hex_for_color_key("accent", keys.get("accent", "white")),
            button_hex=hex_for_color_key("button", keys.get("button", "black")),
        )
    except Exception:
        return _NpTheme()

def _zone_spec(zone: int):
    z = int(zone)
    return NOW_PLAYING_ZONES.get(z, NOW_PLAYING_ZONES[1])


def _zone_clock_center_const(zone: int) -> tuple[float, float]:
    return design_xy_from_local(_zone_spec(zone), CLOCK_LOCAL_CX, CLOCK_LOCAL_CY)


# Zone centers (design coords) — clock/volume disc in each portrait slot.
_ZONE1_CX, _ZONE1_CY = _zone_clock_center_const(1)
_ZONE2_CX, _ZONE2_CY = _zone_clock_center_const(2)
_ZONE3_CX, _ZONE3_CY = _zone_clock_center_const(3)

_RING_OUTER_R = VOLUME_OUTER_R
_RING_INNER_R = VOLUME_INNER_R

# Zone2 poster 2×3 / album 1×1.
# SVG ``poster_accent-2`` path bbox ≈ 200×300 @ (300.7, 14.5); placed demo image is
# 780×1170 × 0.26 (=202.8×304.2) @ translate(300.52, 14.47). Overfill slightly so
# cover-fit art seats under the accent stroke without a gap.
_POSTER_VIDEO_X, _POSTER_VIDEO_Y, _POSTER_VIDEO_W, _POSTER_VIDEO_H, _POSTER_VIDEO_RX = (
    int(round(POSTER_2X3_LOCAL[0])),
    int(round(POSTER_2X3_LOCAL[1])),
    int(round(POSTER_2X3_LOCAL[2])),
    int(round(POSTER_2X3_LOCAL[3])),
    int(round(POSTER_2X3_LOCAL[4])),
)
_CLOCK_EXTERIOR_ACCENT_R = 199.0
_CLOCK_MIDDLE_ACCENT_R = 164.45
_CLOCK_INTERIOR_ACCENT_R = 105.54
# Dimmed minute/second ticks: red mixed with 50% black → dark red; current stays full red.
_TICK_DIM_FILL = "#800000"
_TICK_ACTIVE_FILL = "red"
_CLOCK_MINUTE_TICK_OPACITY = 0.7  # 30% transparent

_POSTER_MUSIC_X, _POSTER_MUSIC_Y, _POSTER_MUSIC_W, _POSTER_MUSIC_H, _POSTER_MUSIC_RX = (
    int(round(POSTER_1X1_LOCAL[0])),
    int(round(POSTER_1X1_LOCAL[1])),
    int(round(POSTER_1X1_LOCAL[2])),
    int(round(POSTER_1X1_LOCAL[3])),
    int(round(POSTER_1X1_LOCAL[4])),
)

_ARTWORK_BG_OPACITY = 0.24
_ARTWORK_BG_BLUR_DOWNSCALE = 4
_ARTWORK_BG_BLUR_SIGMA = 6.0

# Soft white halo behind active clock widgets only (volume has no glow).
_ZONE_HALO_R = _CLOCK_EXTERIOR_ACCENT_R
_ZONE_HALO_OPACITY = 0.36  # 10% more transparent than 0.40
_ZONE_HALO_BLUR_SIGMA = 3.0  # slight soft edge only

# While TMDb is fetching (``searching``), clock ticks + volume race ahead of wall time.
_CLOCK_SPIN_SEC_RATE = 48.0  # second-ticks per real second (~1.25s / full ring)
_CLOCK_SPIN_MIN_RATE = 16.0  # minute-ticks per real second
_CLOCK_SPIN_HOUR_RATE = 6.0  # hour faces per real second
_CLOCK_SPIN_VOL_RATE = 1.8  # volume-ring revolutions per real second
_ZONE_CIRCLE_HALO_KEYS: tuple[tuple[str, float, float], ...] = (
    ("zone1_clock_group", _ZONE1_CX, _ZONE1_CY),
    ("zone2_clock_group", _ZONE2_CX, _ZONE2_CY),
    ("zone3_clock_group", _ZONE3_CX, _ZONE3_CY),
)

_ACCENT_OPACITY = 0.70
_ACCENT_STROKE_PX = 2
_ACCENT_STROKE_OPACITY = 0.12
_CHROME_FILL_OPACITY = 0.12
_BUTTON_FILL_OPACITY = 0.35
_POSTER_PAUSED_DIM = 0.30

_POSTER_X = _POSTER_VIDEO_X
_POSTER_Y = _POSTER_VIDEO_Y
_POSTER_W = _POSTER_VIDEO_W
_POSTER_H = _POSTER_VIDEO_H
_POSTER_RX = _POSTER_VIDEO_RX

# Music track titles under zone2 album art.
_TRACK_CX = 400.0
_TRACK_MAX_W = 360
_SONG_CY, _SONG_SIZE = 320.0, 28
_ALBUM_CY, _ALBUM_SIZE = 344.0, 20
_ARTIST_CY, _ARTIST_SIZE = 364.0, 20

_CONTENT_MODE_VIDEO = "video"
_CONTENT_MODE_MUSIC = "music"

# Zone5 status bar (SVG paths ≈ 76–724, y≈391, h≈40).
_BAR_L = 76
_BAR_R = 724
_BAR_T = 391
_BAR_H = 40
_BAR_RX = 8
_BAR_W = _BAR_R - _BAR_L
_CTI_W = 8
_CTI_OVERHANG_TOP = 0
_CTI_OVERHANG_BOTTOM = 0
_CTI_H = _BAR_H + _CTI_OVERHANG_TOP + _CTI_OVERHANG_BOTTOM
_CTI_Y = _BAR_T - _CTI_OVERHANG_TOP
_MIN_ELAPSED_W = 4
_ELAPSED_REMAINING_GAP_PX = 16
_SERVICE_FADE_PROGRESS = 0.12

# Volume readout centered in volume_container; audio config sits above the ring.
_VOLUME_CX = _ZONE3_CX
_AUDIO_CFG_CX = _ZONE3_CX
_VOLUME_SIZE_PX = VOLUME_VALUE_SIZE_PX
_AUDIO_CFG_SIZE_PX = VOLUME_FORMAT_SIZE_PX
_CLOCK_DIGITAL_SIZE = CLOCK_DIGITAL_SIZE_PX
# Date / audio-config baselines sit this many px above the widget exterior top.
_WIDGET_LABEL_BASELINE_GAP_PX = 20.0
_CLOCK_DATE_SIZE_PX = 32
# Keep volume readout inside ``volume_container`` (inner ring).
_VOLUME_TEXT_INNER_FIT = 0.78

# Audio-levels channel labels (PyMuPDF can't paint Digital-7 — redrawn in Pillow).
_AUDIO_LEVEL_LABEL_SIZE = 33
_AUDIO_LEVEL_LABEL_BASELINE_Y = 304.67
_AUDIO_LEVEL_LABELS_BY_ZONE: dict[int, tuple[tuple[str, float], ...]] = {
    # (label, bar center x) — from pigeon_now_playing.svg meter columns.
    1: (
        ("sl", 44.115),
        ("l", 99.245),
        ("c", 152.705),
        ("r", 206.165),
        ("sr", 258.815),
    ),
    2: (
        ("sl", 293.615),
        ("l", 348.745),
        ("c", 402.205),
        ("r", 455.665),
        ("sr", 508.315),
    ),
    3: (
        ("sl", 542.355),
        ("l", 597.475),
        ("c", 650.935),
        ("r", 704.395),
        ("sr", 757.055),
    ),
}

# Zone4 cast columns (center x, actor baseline y, character baseline y) — SVG geometry.
_CAST_COLS_Z4: tuple[tuple[float, float, float], ...] = (
    (_ZONE1_CX, 351.08, 369.08),
    (_ZONE2_CX, 351.08, 369.08),
    (_ZONE3_CX, 351.08, 369.08),
)
# Zone5 expanded cast strip (same columns, lower baselines).
_CAST_COLS_Z5: tuple[tuple[float, float, float], ...] = (
    (_ZONE1_CX, 405.73, 423.61),
    (_ZONE2_CX, 406.08, 423.61),
    (_ZONE3_CX, 405.73, 423.61),
)
_CAST_COLS = _CAST_COLS_Z4  # back-compat alias
_CAST_COL_W = 200
_CAST_TEXT_PAD = 2

_ELAPSED_TEXT_Y = 460
_REMAINING_TEXT_Y = 460
_SERVICE_TEXT_X = 28
_SERVICE_TEXT_Y = 460
_PAUSED_TEXT_CX = 400.0
_PAUSED_TEXT_CY = 411.0

# Hour face layers: wall-clock hour → SVG data-name.
_HOUR_FACE_NAMES: dict[int, str] = {
    1: "hours_05_01",
    2: "hours_10_02",
    3: "hours_15_03",
    4: "hours_20_04",
    5: "hours_25_05",
    6: "hours_30_06",
    7: "hours_35_07",
    8: "hours_40_08",
    9: "hours_45_09",
    10: "hours_50_10",
    11: "hours_55_11",
    12: "hours_60_12",
}

# Canonical names (or id prefixes) stripped / hidden before rasterize (demo text / images).
_STRIP_OR_HIDE_NAMES: tuple[str, ...] = (
    "zone1_clock_digital_text",
    "zone2_clock_digital_text",
    "zone3_clock_digital_text",
    "zone3_volume_text",
    "zone3_voume_audio_config_text",
    "zone2_volume_text",
    "zone2_voume_audio_config_text",
    "zone1_volume_text",
    "zone1_voume_audio_config_text",
    "zone1_voume_audio_config_text-2",
    "zone5_now_playing_remaining_text",
    "zone5_now_playing_elapsed_text",
    "zone5_now_playing_service_text",
    "zone5_now_playing_paused_text",
    "zone5_now_playing_remaining_icon",
    "zone5_now_playing_elapsed_icon",
    "zone5_now_playing_cti_icon",
    "zone4_actor1_text",
    "zone4_character1_text",
    "zone4_actor2_text",
    "zone4_character2_text",
    "zone4_actor3_text",
    "zone4_character3_text",
    "zone0_date_left_text",
    "zone0_date_center_text",
    "zone0_date_right_text",
    "poster_tmdb",
    "zone3_volume_deselected_buton",
    "zone3_volume_selected_button",
    "zone3_volume_container",
    "zone2_volume_deselected_buton",
    "zone2_volume_selected_button",
    "zone2_volume_container",
    "zone1_volume_deselected_buton",
    "zone1_volume_selected_button",
    "zone1_volume_container",
)

# Legacy zone0 date header (removed from live NP; prefs still hides the SVG group).
_ZONE0_DATE_ALIGN_DEFAULT = "left"
_ZONE0_DATE_SIZE_PX = _CLOCK_DATE_SIZE_PX
_ZONE0_DATE_BASELINE_Y = 19.94
_ZONE0_DATE_CENTER_X: dict[str, float] = {
    "left": _ZONE1_CX,
    "center": _ZONE2_CX,
    "right": _ZONE3_CX,
}


@dataclass
class ViewCirclesState:
    progress: float = 0.0
    elapsed_text: str = ""
    remaining_text: str = ""
    volume: str = ""
    volume_fraction: float = 0.0
    volume_muted: bool = False
    incoming: str = ""
    config: str = ""
    chrome_visible: bool = False
    cast: list[tuple[str, str]] = field(default_factory=list)
    content_mode: str = _CONTENT_MODE_VIDEO  # "video" | "music"
    song_title: str = ""
    album_title: str = ""
    artist_title: str = ""
    searching: bool = False
    search_angle_deg: float = 0.0
    missing_art: bool = False
    paused: bool = False
    service_name: str = ""
    has_position: bool = False
    # False → clock-only layout until playback / receiver broadcast / title.
    content_active: bool = False


def _normalize_content_mode(mode: str | None) -> str:
    m = str(mode or "").strip().lower()
    if m == _CONTENT_MODE_MUSIC:
        return _CONTENT_MODE_MUSIC
    return _CONTENT_MODE_VIDEO


def default_view_circles_svg_path(
    assets_dir: Path | str | None = None,
    *,
    content_mode: str = _CONTENT_MODE_VIDEO,
) -> Path:
    """Directory of per-widget now-playing SVGs (legacy env still accepted)."""
    del content_mode
    env = (
        os.environ.get("PIGEON_VIEW_CIRCLES_SVG", "").strip()
        or os.environ.get("PIGEON_NOW_PLAYING_SVG", "").strip()
    )
    if env:
        p = Path(env).expanduser().resolve()
        return p.parent if p.is_file() else p
    if assets_dir is not None:
        return Path(assets_dir) / "nowPlaying"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "nowPlaying"


def _poster_geometry(
    content_mode: str,
    *,
    zone: int = 2,
) -> tuple[int, int, int, int, int]:
    z = _zone_spec(zone)
    if int(zone) in (6, 7):
        return design_rect_from_local(
            z,
            POSTER_16X9_LOCAL,
            view_w=POSTER_16X9_VIEW_W,
            view_h=POSTER_16X9_VIEW_H,
        )
    local = (
        POSTER_1X1_LOCAL
        if _normalize_content_mode(content_mode) == _CONTENT_MODE_MUSIC
        else POSTER_2X3_LOCAL
    )
    return design_rect_from_local(z, local)


def _zone_for_widget(assignments: tuple[str, str, str, str, str], widget: str) -> int | None:
    for i, name in enumerate(assignments):
        if name == widget:
            return i + 1
    return None


def _cover_fit_bgra(src: np.ndarray, tw: int, th: int) -> np.ndarray:
    if src is None or src.size == 0 or tw < 1 or th < 1:
        return np.zeros((max(1, th), max(1, tw), 4), dtype=np.uint8)
    arr = src
    if arr.ndim == 2:
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2BGRA)
    elif arr.ndim == 3 and arr.shape[2] == 3:
        arr = cv2.cvtColor(arr, cv2.COLOR_BGR2BGRA)
    sh, sw = arr.shape[:2]
    if sh < 1 or sw < 1:
        return np.zeros((th, tw, 4), dtype=np.uint8)
    scale = max(tw / float(sw), th / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(
        arr,
        (nw, nh),
        interpolation=cv_resize_interp(sw, sh, nw, nh),
    )
    x0 = max(0, (nw - tw) // 2)
    y0 = max(0, (nh - th) // 2)
    crop = resized[y0 : y0 + th, x0 : x0 + tw]
    if crop.shape[0] != th or crop.shape[1] != tw:
        crop = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
    return crop


def _build_artwork_blur_bgra(src: np.ndarray) -> np.ndarray:
    tw, th = int(DESIGN_W), int(DESIGN_H)
    cover = _cover_fit_bgra(src, tw, th)
    dw = max(1, tw // _ARTWORK_BG_BLUR_DOWNSCALE)
    dh = max(1, th // _ARTWORK_BG_BLUR_DOWNSCALE)
    small = cv2.resize(cover, (dw, dh), interpolation=cv2.INTER_AREA)
    bgr = small[:, :, :3]
    sigma = float(_ARTWORK_BG_BLUR_SIGMA)
    k = max(3, int(round(sigma * 2)) | 1)
    blurred = cv2.GaussianBlur(bgr, (k, k), sigmaX=sigma, sigmaY=sigma)
    up = cv2.resize(blurred, (tw, th), interpolation=cv2.INTER_LINEAR)
    out = np.zeros((th, tw, 4), dtype=np.uint8)
    out[:, :, :3] = up
    out[:, :, 3] = int(round(255.0 * _ARTWORK_BG_OPACITY))
    return out


def _layer_key(el: ET.Element) -> str:
    """Prefer data-name; strip Illustrator ``-N`` id suffixes; normalize spaces."""
    dn = (el.get("data-name") or "").strip()
    raw = dn if dn else (el.get("id") or "").strip()
    if not dn and raw:
        raw = re.sub(r"-\d+$", "", raw)
    return re.sub(r"\s+", "_", raw)


def _find_by_id(root: ET.Element, layer_id: str) -> ET.Element | None:
    for el in root.iter():
        if el.get("id") == layer_id:
            return el
    return None


def _find_by_key(scope: ET.Element, name: str) -> ET.Element | None:
    want = re.sub(r"\s+", "_", str(name or "").strip())
    if not want:
        return None
    for el in scope.iter():
        if _layer_key(el) == want or el.get("id") == want:
            return el
    return None


def _find_direct_child_by_key(parent: ET.Element, name: str) -> ET.Element | None:
    want = re.sub(r"\s+", "_", str(name or "").strip())
    for el in list(parent):
        if _layer_key(el) == want or el.get("id") == want:
            return el
    return None


def _detach_element(root: ET.Element, el: ET.Element | None) -> bool:
    """Remove ``el`` from its parent. PyMuPDF ignores ``display:none`` on SVG groups."""
    if el is None:
        return False
    for parent in root.iter():
        for child in list(parent):
            if child is el:
                parent.remove(child)
                return True
    return False


def _remove_element_by_id(root: ET.Element, element_id: str) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.get("id") == element_id:
                parent.remove(child)
                return


def _remove_by_key(root: ET.Element, name: str) -> bool:
    """Remove every element whose canonical key or id matches ``name``."""
    want = re.sub(r"\s+", "_", str(name or "").strip())
    if not want:
        return False
    removed = False
    # Restart scan after each removal — tree mutates under iter().
    while True:
        hit = None
        for el in root.iter():
            if _layer_key(el) == want or el.get("id") == want:
                hit = el
                break
        if hit is None:
            break
        if not _detach_element(root, hit):
            break
        removed = True
    return removed


# (path, mtime_ns) → parsed template. Chrome rasters miss every second, so
# without this the SVG is re-read and re-parsed from disk once per tick.
_SVG_TEMPLATE_CACHE: dict[tuple[str, int], ET.Element] = {}


def _svg_tree_from_path(path: Path) -> ET.Element:
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = -1
    key = (str(path), mtime)
    template = _SVG_TEMPLATE_CACHE.get(key)
    if template is None:
        tree = ET.parse(path)
        template = tree.getroot()
        while len(_SVG_TEMPLATE_CACHE) >= 12:  # one slot per now-playing widget SVG
            _SVG_TEMPLATE_CACHE.pop(next(iter(_SVG_TEMPLATE_CACHE)))
        _SVG_TEMPLATE_CACHE[key] = template
    # Callers mutate the tree (strip layers, set text), so hand out a copy.
    return copy.deepcopy(template)


def _scale_raster_to_design(bgra: np.ndarray, src_w: int, src_h: int) -> np.ndarray:
    if bgra.shape[0] != src_h or bgra.shape[1] != src_w:
        bgra = cv2.resize(bgra, (src_w, src_h), interpolation=cv2.INTER_AREA)
    return cv2.resize(bgra, (int(DESIGN_W), int(DESIGN_H)), interpolation=cv2.INTER_AREA)


def _svg_viewbox_wh(root: ET.Element) -> tuple[int, int]:
    raw = (root.get("viewBox") or "").replace(",", " ").split()
    if len(raw) == 4:
        try:
            return max(1, int(round(float(raw[2])))), max(1, int(round(float(raw[3]))))
        except (TypeError, ValueError):
            pass
    return int(_SVG_W), int(_SVG_H)


def _rasterize_svg_tree(
    root: ET.Element, *, dest_w: int | None = None, dest_h: int | None = None
) -> np.ndarray:
    svg_bytes = ET.tostring(root, encoding="utf-8")
    vw, vh = _svg_viewbox_wh(root)
    src_w = int(dest_w) if dest_w is not None else vw
    src_h = int(dest_h) if dest_h is not None else vh
    last_err: Exception | None = None

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=svg_bytes, filetype="svg")
        page = doc[0]
        pix = page.get_pixmap(
            matrix=fitz.Matrix(src_w / max(1e-6, page.rect.width), src_h / max(1e-6, page.rect.height)),
            alpha=True,
        )
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            bgra = cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGRA)
        else:
            bgra = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGRA)
        if bgra.shape[1] != src_w or bgra.shape[0] != src_h:
            bgra = cv2.resize(bgra, (src_w, src_h), interpolation=cv2.INTER_AREA)
        return bgra
    except ImportError as exc:
        last_err = exc
    except Exception as exc:
        last_err = exc

    try:
        import cairosvg

        out = io.BytesIO()
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=out,
            output_width=src_w,
            output_height=src_h,
        )
        data = np.frombuffer(out.getvalue(), dtype=np.uint8)
        raw = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
        if raw is None:
            raise RuntimeError("SVG raster decode failed")
        if raw.ndim == 2:
            bgra = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGRA)
        elif raw.shape[2] == 3:
            bgra = cv2.cvtColor(raw, cv2.COLOR_BGR2BGRA)
        else:
            bgra = raw
        if bgra.shape[1] != src_w or bgra.shape[0] != src_h:
            bgra = cv2.resize(bgra, (src_w, src_h), interpolation=cv2.INTER_AREA)
        return bgra
    except ImportError as exc:
        last_err = exc
    except OSError as exc:
        last_err = exc
    except Exception as exc:
        last_err = exc

    msg = "view_circles needs PyMuPDF (pip install pymupdf) or cairosvg with system cairo."
    if last_err is not None:
        raise RuntimeError(msg) from last_err
    raise RuntimeError(msg)


def _decanvas_white_bgra(src: np.ndarray, *, threshold: int = 252) -> np.ndarray:
    """Punch out Illustrator page white, keeping intentional interior white fills.

    Only near-white pixels connected to the image border become transparent so
    solid white discs (e.g. ``zone*_clock_exterior_accent``) stay opaque.
    """
    if src is None or src.size == 0 or src.ndim != 3 or src.shape[2] < 4:
        return src
    out = src.copy()
    rgb = out[:, :, :3]
    white = (
        (rgb[:, :, 0] >= threshold)
        & (rgb[:, :, 1] >= threshold)
        & (rgb[:, :, 2] >= threshold)
    )
    if not bool(np.any(white)):
        return out
    h, w = white.shape
    # cv2.floodFill needs a 2-pixel border mask.
    flood_mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    seed = (white.astype(np.uint8) * 255)
    for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if not white[y, x]:
            continue
        cv2.floodFill(
            seed,
            flood_mask,
            (int(x), int(y)),
            128,
            loDiff=0,
            upDiff=0,
            flags=cv2.FLOODFILL_FIXED_RANGE,
        )
    edge_white = seed == 128
    # Also treat any border white that flood missed (non-corner border seeds).
    if not bool(np.any(edge_white)):
        edge_white = white & False
        edge_white[0, :] = white[0, :]
        edge_white[-1, :] = white[-1, :]
        edge_white[:, 0] = white[:, 0]
        edge_white[:, -1] = white[:, -1]
    out[edge_white, 3] = 0
    return out


def _default_zone_widget_assignments() -> tuple[str, str, str, str, str]:
    try:
        from pigeon.widgets.preferences_settings import read_now_playing_zone_widgets

        return read_now_playing_zone_widgets()
    except Exception:
        return ("clock", "poster", "volume", "cast_info", "status_bar")


_CAST_NAMES_PER_ZONE = CAST_NAMES_PER_ZONE


def _layout_is_fullscreen_clock(
    assignments: tuple[str, ...] | list[str],
) -> bool:
    """True when no populated NP boxes remain — clock should fill the frame."""
    keys = [str(z or "").strip() for z in assignments]
    filled = [k for k in keys if k]
    return not filled or all(k == "clock" for k in filled)


def _effective_zone_widgets(
    *,
    has_position: bool,
    cast_count: int = 0,
    content_active: bool = True,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
    poster_16x9: bool = False,
    poster_16x9_zone: int = DEFAULT_16X9_POSTER_ZONE,
    has_poster: bool = True,
) -> tuple[str, str, str, str, str]:
    """Show only widgets we have content for; never two copies of the same one.

    When ``content_active`` is False (no title / playback / receiver broadcast),
    keep the clock only so empty poster/volume/cast/bar shells do not look broken.
    The renderer then expands that clock-only layout to fill the screen.

    Status bar needs a live position. Without it, that zone can show the next
    unused cast names. A second cast strip is kept only when more names remain.
    Any other repeated widget is dropped so the layout does not duplicate.

    When ``poster_16x9`` is True and a poster is actually available, user poster
    placement is overridden: the landscape widget occupies zone 7 (default) or
    zone 6 and the conflicting portrait columns are cleared. A missing poster
    never occupies a slot (no empty 16×9 shell).
    """
    zones = list(zone_widgets or _default_zone_widget_assignments())
    if len(zones) < 5:
        return _default_zone_widget_assignments()
    zones = [canonical_zone_widget(i + 1, w) for i, w in enumerate(zones[:5])]
    if not content_active:
        return tuple("clock" if z == "clock" else "" for z in zones)
    named = max(0, int(cast_count))
    if not has_position:
        for i, widget in enumerate(zones):
            if is_status_bar_widget(widget, i + 1):
                zones[i] = "cast_info"
    seen: set[str] = set()
    cast_used = 0
    for i, widget in enumerate(zones):
        key = str(widget or "").strip()
        if not key:
            zones[i] = ""
            continue
        if key == "poster" and not has_poster:
            zones[i] = ""
            continue
        if key == "cast_info":
            remaining = named - cast_used
            if remaining <= 0:
                zones[i] = ""
                continue
            cast_used += cast_names_for_zone(i + 1)
            continue
        if key in seen:
            zones[i] = ""
            continue
        seen.add(key)
    result = (zones[0], zones[1], zones[2], zones[3], zones[4])
    if poster_16x9 and has_poster:
        return apply_16x9_poster_override(result, zone=int(poster_16x9_zone))
    return result


def configured_status_bar_zone(
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> int | None:
    """Prefs zone that holds the status bar, ignoring live position/cast fallbacks."""
    zones = list(zone_widgets or _default_zone_widget_assignments())
    if len(zones) < 5:
        zones = list(_default_zone_widget_assignments())
    for i, name in enumerate(zones[:5]):
        key = canonical_zone_widget(i + 1, name)
        if is_status_bar_widget(key, i + 1):
            return i + 1
    return None


def settings_main_keeps_np_status_bar(
    *, show_pigeon_settings: bool, content_playing: bool
) -> bool:
    """Keep the NP bar on settings_main while content is up; never on settings_pigeon."""
    return bool(content_playing) and not bool(show_pigeon_settings)


def _zone_widget_visibility(
    *,
    content_mode: str,
    paused: bool,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> dict[str, bool]:
    """Zone widget on/off map from preferences (defaults: clock / poster / volume / cast / bar)."""
    mode = _normalize_content_mode(content_mode)
    is_music = mode == _CONTENT_MODE_MUSIC
    assignments = zone_widgets if zone_widgets is not None else _default_zone_widget_assignments()

    def _is(zone: int, widget: str) -> bool:
        if not (1 <= zone <= 5):
            return False
        return assignments[zone - 1] == widget

    # Poster/album: music prefers 1×1 album art; video prefers 2×3 poster.
    def _poster_on(zone: int) -> tuple[bool, bool]:
        if not _is(zone, "poster"):
            return False, False
        if is_music:
            return False, True
        return True, False

    z1_poster, z1_album = _poster_on(1)
    z2_poster, z2_album = _poster_on(2)
    z3_poster, z3_album = _poster_on(3)
    play_z1 = bool(paused) and (z1_poster or z1_album)
    play_z2 = bool(paused) and (z2_poster or z2_album)
    play_z3 = bool(paused) and (z3_poster or z3_album)
    vis = {
        # zone1
        # volume chrome is also used by circular now_playing (playback progress).
        "zone1_volume_group": _is(1, "volume") or _is(1, "now_playing"),
        "zone1_audio_levels_group": _is(1, "audio_levels"),
        "zone1_clock_group": _is(1, "clock"),
        "zone1_poster_2x3": z1_poster,
        "zone1_album_art_1x1": z1_album,
        "zone1_cast_group": _is(1, "cast_info"),
        "zone1_play_button": play_z1,
        # zone2
        "zone2_volume_group": _is(2, "volume") or _is(2, "now_playing"),
        "zone2_audio_levels_group": _is(2, "audio_levels"),
        "zone2_audio_levles_gtoup": _is(2, "audio_levels"),  # Illustrator typo id
        "zone2_clock_group": _is(2, "clock"),
        "zone2_poster_2x3": z2_poster,
        "zone2_album_art_1x1": z2_album,
        "zone2_cast_group": _is(2, "cast_info"),
        "zone2_play_button": play_z2,
        # zone3
        "zone3_volume_group": _is(3, "volume") or _is(3, "now_playing"),
        "zone3_audio_levels_group": _is(3, "audio_levels"),
        "zone3_clock_group": _is(3, "clock"),
        "zone3_poster_2x3": z3_poster,
        "zone3_2x3_poster_group": z3_poster,
        "zone3_album_art_1x1": z3_album,
        "zone3_cast_group": _is(3, "cast_info"),
        "zone3_play_button": play_z3,
        # zone4 — TMDb cast strip
        "zone4_cast_group": _is(4, "cast_info"),
        # zone5 — status bar or expanded cast
        "zone5_now_playing_group": _is(5, "now_playing") or _is(5, "status_bar"),
        "zone5_locations_group": False,
        "zone5_cast_group": _is(5, "cast_info"),
        "zone5_now_playing_paused_text": False,  # redrawn with Sharp Sans
        # zone0 — retired; date now lives above the clock widget
        "zone0_header_group": False,
    }
    return vis


def _zone0_date_align(
    assignments: tuple[str, str, str, str, str] | None = None,
) -> str | None:
    """``left`` / ``center`` / ``right``, or ``None`` when the header is off."""
    try:
        from pigeon.widgets.preferences_settings import zone0_date_align

        return zone0_date_align(assignments)
    except Exception:
        return "left"


def _ordinal_day(day: int) -> str:
    d = int(day)
    if 11 <= (d % 100) <= 13:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
    return f"{d}{suf}"


def _format_zone0_date(now: datetime) -> str:
    """``SAT, AUG 15`` — abbreviated weekday and month, all caps."""
    return f"{now.strftime('%a')}, {now.strftime('%b')} {int(now.day)}".upper()


def _clock_date_baseline_y(cy: float) -> float:
    """Baseline for the date line: 20px above the clock exterior top."""
    return float(cy) - float(_CLOCK_EXTERIOR_ACCENT_R) - float(_WIDGET_LABEL_BASELINE_GAP_PX)


def _volume_audio_cfg_baseline_y(cy: float) -> float:
    """Baseline for audio-config: 20px above the volume ring exterior top."""
    return float(cy) - float(_RING_OUTER_R) - float(_WIDGET_LABEL_BASELINE_GAP_PX)


def _volume_readout_patch(
    text: str,
    *,
    inner_r: float = _RING_INNER_R,
    max_size_px: int = _VOLUME_SIZE_PX,
) -> tuple[np.ndarray, int, int]:
    """Digital-7 volume line fitted inside the volume_container disc."""
    label = str(text or "").strip()
    if not label:
        return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0
    max_box = max(24, int(round(2.0 * float(inner_r) * float(_VOLUME_TEXT_INNER_FIT))))
    pad = 2  # matches ``_text_patch_digital7``
    start = max(18, int(max_size_px))
    best: tuple[np.ndarray, int, int] | None = None
    for size in range(start, 15, -1):
        patch, w, h = _text_patch_digital7(label, size_px=size)
        best = (patch, w, h)
        # Transparent pad does not count toward circle overflow.
        if (w - 2 * pad) <= max_box and (h - 2 * pad) <= max_box:
            return patch, w, h
    assert best is not None
    return best


def _apply_zone_visibility(root: ET.Element, vis: dict[str, bool]) -> None:
    for name, on in vis.items():
        if not on:
            _remove_by_key(root, name)


def _clock_group_for_zone(root: ET.Element, zone: int) -> ET.Element | None:
    return _find_by_key(root, f"zone{zone}_clock_group")


def _seconds_group(clock_group: ET.Element) -> ET.Element | None:
    for el in list(clock_group):
        key = _layer_key(el)
        if "seconds" in key and "group" in key:
            return el
    return _find_by_key(clock_group, "zone1_clock_seconds_group")


def _minutes_group(clock_group: ET.Element) -> ET.Element | None:
    for el in list(clock_group):
        key = _layer_key(el)
        if "minutes" in key and "group" in key:
            return el
    return None


def _hours_group(clock_group: ET.Element) -> ET.Element | None:
    for el in list(clock_group):
        key = _layer_key(el)
        if "hours" in key and "group" in key:
            return el
    return None


def _iter_named_children(group: ET.Element | None):
    if group is None:
        return
    for el in list(group):
        yield el, _layer_key(el)


def _fix_seconds_08_label(seconds_group: ET.Element | None) -> None:
    """SVG ships a mislabeled duplicate ``seconds_60`` where ``seconds_08`` should be."""
    if seconds_group is None:
        return
    if _find_direct_child_by_key(seconds_group, "seconds_08") is not None:
        return
    kids = list(seconds_group)
    idx09 = idx07 = None
    for i, child in enumerate(kids):
        key = _layer_key(child)
        if key == "seconds_09":
            idx09 = i
        elif key == "seconds_07":
            idx07 = i
    if idx09 is None or idx07 is None:
        return
    lo, hi = (idx09, idx07) if idx09 < idx07 else (idx07, idx09)
    for child in kids[lo + 1 : hi]:
        if _layer_key(child) == "seconds_60":
            child.set("data-name", "seconds_08")
            return


def _resolve_seconds_el(seconds_group: ET.Element | None, second_index: int) -> ET.Element | None:
    """``second_index`` in 1..60."""
    if seconds_group is None:
        return None
    _fix_seconds_08_label(seconds_group)
    name = f"seconds_{second_index:02d}"
    el = _find_direct_child_by_key(seconds_group, name)
    if el is not None:
        return el
    return _find_by_key(seconds_group, name)


def _resolve_hour_face_el(hours_group: ET.Element | None, hour_1_12: int) -> ET.Element | None:
    if hours_group is None:
        return None
    name = _HOUR_FACE_NAMES.get(int(hour_1_12))
    if not name:
        return None
    el = _find_direct_child_by_key(hours_group, name)
    if el is not None:
        return el
    return _find_by_key(hours_group, name)


def _zone_clock_center(zone: int) -> tuple[float, float]:
    return design_xy_from_local(_zone_spec(zone), CLOCK_LOCAL_CX, CLOCK_LOCAL_CY)


def _zone_volume_center(zone: int) -> tuple[float, float]:
    return design_xy_from_local(_zone_spec(zone), VOLUME_LOCAL_CX, VOLUME_LOCAL_CY)


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _set_tick_paint(
    el: ET.Element, *, color: str, opacity: float | None = None
) -> None:
    """Recolor path fills/strokes under ``el``."""
    op: str | None = None
    if opacity is not None:
        op = f"{max(0.0, min(1.0, float(opacity))):.3g}"
        el.set("opacity", op)
    elif "opacity" in el.attrib:
        del el.attrib["opacity"]
    for node in el.iter():
        tag = _local_tag(node.tag)
        if tag not in ("path", "polygon", "polyline", "circle", "ellipse", "rect"):
            continue
        if op is not None:
            node.set("opacity", op)
        elif "opacity" in node.attrib:
            del node.attrib["opacity"]
        fill = (node.get("fill") or "").strip().lower()
        stroke = (node.get("stroke") or "").strip().lower()
        if fill and fill != "none":
            node.set("fill", color)
        if stroke and stroke != "none":
            node.set("stroke", color)
        # Bare paths sometimes omit fill (Illustrator default = black); force paint.
        if not fill and not stroke:
            node.set("fill", color)


def _raise_in_group(group: ET.Element | None, el: ET.Element | None) -> None:
    """Move ``el`` to the end of ``group`` so it paints above siblings."""
    if group is None or el is None:
        return
    kids = list(group)
    if el not in kids:
        return
    group.remove(el)
    group.append(el)


def _accent_circle_r(clock: ET.Element, zone: int, kind: str, default: float) -> float:
    el = _find_by_key(clock, f"zone{zone}_clock_{kind}_accent")
    if el is None:
        return default
    try:
        return float(el.get("r") or default)
    except (TypeError, ValueError):
        return default


def _place_in_group(group: ET.Element | None, el: ET.Element | None, index: int) -> None:
    """Move ``el`` to ``index`` within ``group`` (paint order)."""
    if group is None or el is None:
        return
    kids = list(group)
    if el not in kids:
        return
    group.remove(el)
    group.insert(max(0, min(int(index), len(list(group)))), el)


def _seconds_wedge_path_d(cx: float, cy: float, r: float, fraction: float) -> str | None:
    """SVG pie path from 12 o'clock, clockwise, covering ``fraction`` of the disc.

    Returns None when ``fraction`` is full (caller should use a solid circle fill).
    """
    frac = max(0.0, min(1.0, float(fraction)))
    if frac <= 1e-6:
        return ""
    if frac >= 0.999:
        return None
    sweep = 2.0 * math.pi * frac
    x1 = cx
    y1 = cy - r
    x2 = cx + r * math.sin(sweep)
    y2 = cy - r * math.cos(sweep)
    large = 1 if frac > 0.5 else 0
    return (
        f"M {cx:.4f},{cy:.4f} L {x1:.4f},{y1:.4f} "
        f"A {r:.4f},{r:.4f} 0 {large},1 {x2:.4f},{y2:.4f} Z"
    )


def _svg_parent(root: ET.Element, el: ET.Element) -> ET.Element | None:
    for parent in root.iter():
        if el in list(parent):
            return parent
    return None


def _circle_cx_cy_r(
    el: ET.Element | None,
    *,
    default_cx: float,
    default_cy: float,
    default_r: float,
) -> tuple[float, float, float]:
    if el is None:
        return float(default_cx), float(default_cy), float(default_r)
    try:
        cx = float(el.get("cx") or default_cx)
        cy = float(el.get("cy") or default_cy)
        r = float(el.get("r") or default_r)
    except (TypeError, ValueError):
        return float(default_cx), float(default_cy), float(default_r)
    return cx, cy, r


def _evenodd_ring_d(cx: float, cy: float, r_outer: float, r_inner: float) -> str:
    """Closed outer circle minus inner circle (evenodd)."""
    ro = max(0.5, float(r_outer))
    ri = max(0.0, min(float(r_inner), ro - 0.5))
    return (
        f"M {cx:.4f},{cy - ro:.4f} "
        f"A {ro:.4f},{ro:.4f} 0 1,1 {cx:.4f},{cy + ro:.4f} "
        f"A {ro:.4f},{ro:.4f} 0 1,1 {cx:.4f},{cy - ro:.4f} Z "
        f"M {cx:.4f},{cy - ri:.4f} "
        f"A {ri:.4f},{ri:.4f} 0 1,0 {cx:.4f},{cy + ri:.4f} "
        f"A {ri:.4f},{ri:.4f} 0 1,0 {cx:.4f},{cy - ri:.4f} Z"
    )


def _annulus_wedge_path_d(
    cx: float, cy: float, r_outer: float, r_inner: float, fraction: float
) -> str | None:
    """Outer-ring sector from 12 o'clock clockwise. None = full ring."""
    frac = max(0.0, min(1.0, float(fraction)))
    if frac <= 1e-6:
        return ""
    if frac >= 0.999:
        return None
    ro = max(0.5, float(r_outer))
    ri = max(0.0, min(float(r_inner), ro - 0.5))
    sweep = 2.0 * math.pi * frac
    large = 1 if frac > 0.5 else 0
    x1 = cx
    y1 = cy - ro
    x2 = cx + ro * math.sin(sweep)
    y2 = cy - ro * math.cos(sweep)
    x3 = cx + ri * math.sin(sweep)
    y3 = cy - ri * math.cos(sweep)
    x4 = cx
    y4 = cy - ri
    return (
        f"M {x1:.4f},{y1:.4f} "
        f"A {ro:.4f},{ro:.4f} 0 {large},1 {x2:.4f},{y2:.4f} "
        f"L {x3:.4f},{y3:.4f} "
        f"A {ri:.4f},{ri:.4f} 0 {large},0 {x4:.4f},{y4:.4f} Z"
    )


def _clock_accent_key(zone: int | None, kind: str) -> str:
    if zone is None:
        return f"clock_{kind}_accent"
    return f"zone{int(zone)}_clock_{kind}_accent"


def _apply_clock_black_rings(clock: ET.Element, *, zone: int | None = None) -> None:
    """Black outer ring (exterior minus middle hole) + black interior disc.

    Middle stays unfilled so the backdrop shows through that band.
    """
    exterior = _find_by_key(clock, _clock_accent_key(zone, "exterior"))
    middle = _find_by_key(clock, _clock_accent_key(zone, "middle"))
    interior = _find_by_key(clock, _clock_accent_key(zone, "interior"))
    ring_id = (
        "clock_outer_ring_fill"
        if zone is None
        else f"zone{int(zone)}_clock_outer_band_fill"
    )
    disc_id = (
        "clock_interior_disc_fill"
        if zone is None
        else f"zone{int(zone)}_clock_disc_fill"
    )
    _remove_by_key(clock, ring_id)
    _remove_by_key(clock, disc_id)
    if exterior is not None:
        exterior.set("fill", "none")
    if middle is not None:
        middle.set("fill", "none")
    if interior is not None:
        interior.set("fill", "none")
    host = _svg_parent(clock, exterior) if exterior is not None else clock
    if host is None:
        host = clock
    cx, cy, r_out = _circle_cx_cy_r(
        exterior,
        default_cx=CLOCK_LOCAL_CX,
        default_cy=CLOCK_LOCAL_CY,
        default_r=_CLOCK_EXTERIOR_ACCENT_R,
    )
    _mx, _my, r_mid = _circle_cx_cy_r(
        middle,
        default_cx=cx,
        default_cy=cy,
        default_r=_CLOCK_MIDDLE_ACCENT_R,
    )
    icx, icy, r_in = _circle_cx_cy_r(
        interior,
        default_cx=cx,
        default_cy=cy,
        default_r=_CLOCK_INTERIOR_ACCENT_R,
    )
    ring = ET.Element(f"{{{SVG_NS}}}path")
    ring.set("id", ring_id)
    ring.set("data-name", ring_id)
    ring.set("d", _evenodd_ring_d(cx, cy, r_out, r_mid))
    ring.set("fill", _COLOR_CENTER_BLACK_HEX)
    ring.set("fill-rule", "evenodd")
    host.insert(0, ring)
    disc = ET.Element(f"{{{SVG_NS}}}circle")
    disc.set("id", disc_id)
    disc.set("data-name", disc_id)
    disc.set("cx", f"{icx:.4f}")
    disc.set("cy", f"{icy:.4f}")
    disc.set("r", f"{r_in:.4f}")
    disc.set("fill", _COLOR_CENTER_BLACK_HEX)
    host.insert(1, disc)


def _punch_clock_open_ring_white(bgra: np.ndarray) -> np.ndarray:
    """Knock out trapped page-white in the middle (open) clock band."""
    if bgra is None or bgra.size == 0 or bgra.ndim < 3 or bgra.shape[2] < 4:
        return bgra
    h, w = int(bgra.shape[0]), int(bgra.shape[1])
    sx = float(w) / CLOCK_VIEW_W
    sy = float(h) / CLOCK_VIEW_H
    cx = CLOCK_LOCAL_CX * sx
    cy = CLOCK_LOCAL_CY * sy
    yy, xx = np.ogrid[:h, :w]
    dx = (xx - cx) / max(sx, 1e-6)
    dy = (yy - cy) / max(sy, 1e-6)
    d = np.sqrt(dx * dx + dy * dy)
    band = (d > (_CLOCK_INTERIOR_ACCENT_R + 3.0)) & (
        d < (_CLOCK_MIDDLE_ACCENT_R - 3.0)
    )
    rgb = bgra[:, :, :3]
    white = (
        (rgb[:, :, 0] >= 252)
        & (rgb[:, :, 1] >= 252)
        & (rgb[:, :, 2] >= 252)
    )
    bgra[band & white, 3] = 0
    return bgra


def _apply_exterior_seconds_fill(
    clock: ET.Element,
    zone: int,
    *,
    sec_idx: int,
    theme: _NpTheme | None = None,
) -> None:
    """Accent wedge on the outer black ring only (middle band stays open)."""
    th = theme or np_theme_from_settings()
    exterior = _find_by_key(clock, f"zone{zone}_clock_exterior_accent")
    middle = _find_by_key(clock, f"zone{zone}_clock_middle_accent")
    fill_name = f"zone{zone}_clock_exterior_seconds_fill"
    _remove_by_key(clock, fill_name)
    if exterior is None:
        return
    cx, cy, r_out = _circle_cx_cy_r(
        exterior,
        default_cx=_zone_clock_center(zone)[0],
        default_cy=_zone_clock_center(zone)[1],
        default_r=_CLOCK_EXTERIOR_ACCENT_R,
    )
    _mx, _my, r_mid = _circle_cx_cy_r(
        middle,
        default_cx=cx,
        default_cy=cy,
        default_r=_CLOCK_MIDDLE_ACCENT_R,
    )
    idx = max(1, min(60, int(sec_idx)))
    frac = idx / 60.0
    d = _annulus_wedge_path_d(cx, cy, r_out, r_mid, frac)
    if d is None:
        d = _evenodd_ring_d(cx, cy, r_out, r_mid)
    if not d:
        return
    wedge = ET.Element(f"{{{SVG_NS}}}path")
    wedge.set("id", fill_name)
    wedge.set("data-name", fill_name)
    wedge.set("d", d)
    wedge.set("fill", th.accent_hex)
    wedge.set("fill-rule", "evenodd")
    host = _svg_parent(clock, exterior) or clock
    # Sit above the black outer ring, under tick groups.
    host.insert(2, wedge)


def _apply_clock_accent_fills(root: ET.Element, *, theme: _NpTheme | None = None) -> None:
    """Black outer ring, open middle band, black interior disc."""
    del theme
    for zone in (1, 2, 3):
        clock = _clock_group_for_zone(root, zone)
        if clock is None:
            continue
        for child in list(clock):
            key = _layer_key(child)
            if key in (
                f"zone{zone}_clock_disc_fill",
                f"zone{zone}_clock_outer_band_fill",
                f"zone{zone}_clock_exterior_seconds_fill",
            ):
                clock.remove(child)
        _apply_clock_black_rings(clock, zone=zone)


def _apply_clock_ticks(
    root: ET.Element,
    zone: int,
    now: datetime,
    *,
    theme: _NpTheme | None = None,
) -> None:
    """Drive clock tick layers for the active zone.

    Hours: original geometry; only the current face layer is kept.
    Minutes + seconds: keep layers 1..current (0 → 60); prior ticks dim UI,
    current full UI and raised above siblings.
    """
    th = theme or np_theme_from_settings()
    clock = _clock_group_for_zone(root, zone)
    if clock is None:
        return
    hours_g = _hours_group(clock)
    minutes_g = _minutes_group(clock)
    seconds_g = _seconds_group(clock)
    _fix_seconds_08_label(seconds_g)

    h12 = now.hour % 12
    if h12 == 0:
        h12 = 12
    minute = int(now.minute)
    second = int(now.second)
    # Ring index: 0 → layer 60; 1..59 → matching index.
    min_idx = 60 if minute == 0 else minute
    sec_idx = 60 if second == 0 else second

    # Exterior: button base + accent sector under seconds 1..current.
    _apply_exterior_seconds_fill(clock, zone, sec_idx=sec_idx, theme=th)

    # Hours: only the matching face layer (original SVG shape), painted UI.
    face_name = _HOUR_FACE_NAMES.get(h12, "")
    for el, key in list(_iter_named_children(hours_g)):
        if not key.startswith("hours_"):
            continue
        # hours_61 maps to missing hours_51 — never a face hour for display.
        if key != face_name:
            _detach_element(root, el)
            continue
        _set_tick_paint(el, color=th.tick_active)

    # Minutes: layers 1..current; dim priors, highlight current.
    current_min: ET.Element | None = None
    for el, key in list(_iter_named_children(minutes_g)):
        m = re.fullmatch(r"minutes_(\d{2})", key)
        if not m:
            continue
        idx = int(m.group(1))
        if not (1 <= idx <= min_idx):
            _detach_element(root, el)
            continue
        if idx == min_idx:
            current_min = el
            _set_tick_paint(el, color=th.tick_active, opacity=_CLOCK_MINUTE_TICK_OPACITY)
        else:
            _set_tick_paint(el, color=th.tick_dim, opacity=_CLOCK_MINUTE_TICK_OPACITY)
    _raise_in_group(minutes_g, current_min)

    # Seconds: layers 1..current; dim priors, highlight current.
    current_sec = _resolve_seconds_el(seconds_g, sec_idx)
    for el, key in list(_iter_named_children(seconds_g)):
        if not key.startswith("seconds_"):
            continue
        if el is current_sec:
            _set_tick_paint(el, color=th.tick_active)
            continue
        m = re.fullmatch(r"seconds_(\d{2})", key)
        idx = int(m.group(1)) if m else -1
        if 1 <= idx <= sec_idx:
            _set_tick_paint(el, color=th.tick_dim)
        else:
            _detach_element(root, el)
    _raise_in_group(seconds_g, current_sec)


def _clock_widget_is_analog() -> bool:
    try:
        from pigeon.widgets.options_settings import clock_widget_analog

        return bool(clock_widget_analog())
    except Exception:
        return False


def _detach_clock_tick_groups(root: ET.Element, *, zone: int | None = None) -> None:
    if zone is None:
        keys = ("clock_hours_group", "clock_minutes_group", "clock_seconds_group")
    else:
        z = int(zone)
        keys = (
            f"zone{z}_clock_hours_group",
            f"zone{z}_clock_minutes_group",
            f"zone{z}_clock_seconds_group",
        )
    for key in keys:
        el = _find_by_key(root, key)
        if el is not None:
            _detach_element(root, el)


def _apply_standalone_clock_ticks(
    root: ET.Element, now: datetime, *, theme: _NpTheme
) -> None:
    """Drive the 1280×800 clock widget (no zone prefix). Seconds fill white."""
    _apply_clock_black_rings(root, zone=None)
    hours_g = _find_by_key(root, "clock_hours_group")
    minutes_g = _find_by_key(root, "clock_minutes_group")
    seconds_g = _find_by_key(root, "clock_seconds_group")
    _fix_seconds_08_label(seconds_g)

    h12 = now.hour % 12
    if h12 == 0:
        h12 = 12
    minute = int(now.minute)
    second = int(now.second)
    min_idx = 60 if minute == 0 else minute
    sec_idx = 60 if second == 0 else second
    ui = theme.tick_active
    white = "#FFFFFF"

    face_name = _HOUR_FACE_NAMES.get(h12, "")
    for el, key in list(_iter_named_children(hours_g)):
        if not key.startswith("hours_"):
            continue
        if key != face_name:
            _detach_element(root, el)
            continue
        _set_tick_paint(el, color=ui)

    current_min: ET.Element | None = None
    for el, key in list(_iter_named_children(minutes_g)):
        m = re.fullmatch(r"minutes_(\d{2})", key)
        if not m:
            continue
        idx = int(m.group(1))
        if not (1 <= idx <= min_idx):
            _detach_element(root, el)
            continue
        _set_tick_paint(el, color=ui, opacity=_CLOCK_MINUTE_TICK_OPACITY)
        if idx == min_idx:
            current_min = el
    _raise_in_group(minutes_g, current_min)

    current_sec = _resolve_seconds_el(seconds_g, sec_idx)
    for el, key in list(_iter_named_children(seconds_g)):
        if not key.startswith("seconds_"):
            continue
        m = re.fullmatch(r"seconds_(\d{2})", key)
        idx = int(m.group(1)) if m else -1
        if el is current_sec or (1 <= idx <= sec_idx):
            _set_tick_paint(el, color=white)
        else:
            _detach_element(root, el)
    _raise_in_group(seconds_g, current_sec)

    for name in ("clock_digital_text", "day_text", "month_date_text"):
        _clear_text_content(_find_by_key(root, name))
        el = _find_by_key(root, name)
        if el is not None:
            _detach_element(root, el)


def _now_playing_widget_path(
    assets_dir: Path | str | None, widget_key: str, zone: int | None = None
) -> Path:
    folder = default_view_circles_svg_path(assets_dir)
    return folder / widget_filename(widget_key, zone)


def _prepare_volume_svg(root: ET.Element) -> None:
    for name in (
        "volume_selected_button",
        "volume_text",
        "volume_scale_text",
        "volume_source_text",
        "volume_format_text",
    ):
        el = _find_by_key(root, name)
        if el is not None:
            _detach_element(root, el)


def _prepare_cast_svg(root: ET.Element) -> None:
    for el in list(root.iter()):
        key = _layer_key(el)
        if key.endswith("_text") and key.startswith("cast_info_"):
            _detach_element(root, el)


def _prepare_status_bar_svg(root: ET.Element) -> None:
    """Keep remaining_icon; drop demo elapsed/text. Crop artboard to the used 130px band."""
    root.set("viewBox", f"0 {STATUS_BAR_VIEW_Y0} {STATUS_BAR_VIEW_W} {STATUS_BAR_VIEW_H}")
    root.set("width", str(int(STATUS_BAR_VIEW_W)))
    root.set("height", str(int(STATUS_BAR_VIEW_H)))
    for name in (
        "status_bar_elapsed_icon",
        "status_bar_elapsed_text",
        "status_bar_remaining_text",
        "status_bar_service_text",
        "status_bar_paused_text",
    ):
        el = _find_by_key(root, name)
        if el is not None:
            _detach_element(root, el)


_NAMED_WIDGET_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_NAMED_WIDGET_CACHE_MAX = 64


def _rasterize_named_widget(
    *,
    assets_dir: Path | str | None,
    widget_key: str,
    dest_w: int,
    dest_h: int,
    now: datetime,
    theme: _NpTheme,
    zone: int | None = None,
    include_play_overlay: bool = True,
) -> np.ndarray | None:
    path = _now_playing_widget_path(assets_dir, widget_key, zone)
    if not path.is_file():
        return None
    analog_clock = widget_key == "clock" and _clock_widget_is_analog()
    cache_key = (
        str(path),
        int(dest_w),
        int(dest_h),
        int(zone) if zone is not None else -1,
        widget_key,
        theme.cache_key,
        bool(include_play_overlay),
        now.hour % 12 if analog_clock else -1,
        int(now.minute) if analog_clock else -1,
        int(now.second) if analog_clock else -1,
    )
    cached = _NAMED_WIDGET_CACHE.get(cache_key)
    if cached is not None:
        return cached
    root = _svg_tree_from_path(path)
    if widget_key == "clock":
        if _clock_widget_is_analog():
            _apply_standalone_clock_ticks(root, now, theme=theme)
        else:
            _apply_clock_black_rings(root, zone=None)
            _detach_clock_tick_groups(root, zone=None)
            for name in ("clock_digital_text", "day_text", "month_date_text"):
                el = _find_by_key(root, name)
                if el is not None:
                    _clear_text_content(el)
                    _detach_element(root, el)
    elif widget_key == "volume":
        _prepare_volume_svg(root)
    elif widget_key == "cast_info":
        _prepare_cast_svg(root)
    elif widget_key in ("now_playing", "status_bar"):
        _prepare_status_bar_svg(root)
    elif widget_key == "play" and not include_play_overlay:
        overlay = _find_by_key(root, "50_percent_overlay")
        if overlay is None:
            overlay = _find_by_id(root, "_50_percent_overlay")
        _detach_element(root, overlay)
    bgra = _rasterize_svg_tree(root, dest_w=dest_w, dest_h=dest_h)
    bgra = _decanvas_white_bgra(bgra)
    if widget_key == "clock":
        bgra = _punch_clock_open_ring_white(bgra)
    while len(_NAMED_WIDGET_CACHE) >= _NAMED_WIDGET_CACHE_MAX:
        _NAMED_WIDGET_CACHE.pop(next(iter(_NAMED_WIDGET_CACHE)))
    _NAMED_WIDGET_CACHE[cache_key] = bgra
    return bgra


# Idle saver (zone 9 / full frame): scale NP clock so the disc matches digital
# clocksaver digit height (~368px after letterbox) with a little extra presence.
_SAVER_CLOCK_SCALE = 1.15
_CENTERED_CLOCK_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_CENTERED_CLOCK_CACHE_MAX = 2


def _draw_clock_labels_in_zone(
    out: np.ndarray,
    zone: NowPlayingZone,
    now: datetime,
    *,
    include_digital_time: bool = True,
) -> None:
    """Day / month_date / optional HH:MM for a clock placed in ``zone``."""
    sx = float(zone.w) / CLOCK_VIEW_W
    header_px = max(12, int(round(CLOCK_HEADER_SIZE_PX * sx)))
    digital_px = max(12, int(round(CLOCK_DIGITAL_SIZE_PX * sx)))
    day_x, day_y = design_xy_from_local(
        zone,
        CLOCK_DAY_LOCAL[0],
        CLOCK_DAY_LOCAL[1],
        view_w=CLOCK_VIEW_W,
        view_h=CLOCK_VIEW_H,
    )
    month_x, month_y = design_xy_from_local(
        zone,
        CLOCK_MONTH_DATE_LOCAL[0],
        CLOCK_MONTH_DATE_LOCAL[1],
        view_w=CLOCK_VIEW_W,
        view_h=CLOCK_VIEW_H,
    )
    zx, zy, zw, zh = zone.xywh
    cx = float(zx) + CLOCK_DIGITAL_LOCAL[0] * (float(zw) / CLOCK_VIEW_W)
    cy = float(zy) + CLOCK_DIGITAL_LOCAL[1] * (float(zh) / CLOCK_VIEW_H)
    day_label = now.strftime("%A")
    month_label = f"{now.strftime('%b')} {int(now.day)}"
    day_max_w = max(40, int(round(month_x - day_x - 8.0)))
    font_day = _load_sharp_extrabold(header_px)
    day_patch, dw, _dh = _text_patch_font(
        day_label, font=font_day, fill_rgb=(255, 255, 255)
    )
    if dw > day_max_w:
        for size in range(header_px - 2, 18, -2):
            font_day = _load_sharp_extrabold(size)
            day_patch, dw, _dh = _text_patch_font(
                day_label, font=font_day, fill_rgb=(255, 255, 255)
            )
            if dw <= day_max_w:
                break
    _paste_baseline_left(
        out,
        day_patch,
        day_x,
        day_y,
        bbox_top=_font_bbox_top(day_label, font_day),
    )
    font_month = _load_sharp_semibold(header_px)
    month_patch, _mw, _mh = _text_patch_font(
        month_label, font=font_month, fill_rgb=(255, 255, 255)
    )
    _paste_baseline_left(
        out,
        month_patch,
        month_x,
        month_y,
        bbox_top=_font_bbox_top(month_label, font_month),
    )
    if not include_digital_time:
        return
    time_p = _ink_crop_bgra(
        _matching_hhmm_patch(
            _clock_hhmm(now), size_px=digital_px, fill_rgb=(255, 255, 255)
        )
    )
    nudge_x = CLOCK_DIGITAL_NUDGE[0] * sx
    nudge_y = CLOCK_DIGITAL_NUDGE[1] * (float(zh) / CLOCK_VIEW_H)
    _paste_centered(out, time_p, cx + nudge_x, cy + nudge_y)


def render_centered_clock_widget_bgra(
    *,
    layer_opacity: float = 1.0,
    assets_dir: Path | str | None = None,
    now: datetime | None = None,
    scale: float | None = None,
) -> np.ndarray:
    """Full-frame BGRA: NP clock widget centered (analog idle / zone 9)."""
    from pigeon.widgets.clock_calendar import _resolve_display_time

    when = now if now is not None else _resolve_display_time()
    th = np_theme_from_settings()
    s = float(_SAVER_CLOCK_SCALE if scale is None else scale)
    s = max(0.5, min(2.0, s))
    o = max(0.0, min(1.0, float(layer_opacity)))
    cache_key = (
        when.year,
        when.month,
        when.day,
        when.hour,
        when.minute,
        when.second,
        round(s, 3),
        round(o, 3),
        th.cache_key,
        bool(_clock_widget_is_analog()),
        str(assets_dir or ""),
    )
    hit = _CENTERED_CLOCK_CACHE.get(cache_key)
    if hit is not None:
        return hit
    z1 = NOW_PLAYING_ZONES[1]
    dest_w = max(64, int(round(float(z1.w) * s)))
    dest_h = max(64, int(round(float(z1.h) * s)))
    # Optically center the disc (not the artboard) on the design canvas.
    sx = float(dest_w) / CLOCK_VIEW_W
    sy = float(dest_h) / CLOCK_VIEW_H
    x = int(round(DESIGN_W * 0.5 - CLOCK_LOCAL_CX * sx))
    y = int(round(DESIGN_H * 0.5 - CLOCK_LOCAL_CY * sy))
    zone = NowPlayingZone(9, float(x), float(y), float(dest_w), float(dest_h), ())
    out = np.zeros((int(DESIGN_H), int(DESIGN_W), 4), dtype=np.uint8)
    try:
        patch = _rasterize_named_widget(
            assets_dir=assets_dir,
            widget_key="clock",
            dest_w=dest_w,
            dest_h=dest_h,
            now=when,
            theme=th,
            zone=None,
        )
    except Exception:
        patch = None
    if patch is not None and patch.size > 0:
        _paste_patch_bgra(out, patch, x, y)
    _draw_clock_labels_in_zone(out, zone, when, include_digital_time=True)
    if o < 0.999:
        faded = out.astype(np.float32)
        faded[:, :, 3] *= o
        out = np.clip(faded, 0, 255).astype(np.uint8)
    if len(_CENTERED_CLOCK_CACHE) >= _CENTERED_CLOCK_CACHE_MAX:
        _CENTERED_CLOCK_CACHE.clear()
    _CENTERED_CLOCK_CACHE[cache_key] = out
    return out


def _clear_text_content(el: ET.Element | None) -> None:
    if el is None:
        return
    el.text = None
    for node in el.iter():
        node.text = None
        node.tail = None


def apply_view_circles_svg_state(
    root: ET.Element,
    *,
    content_mode: str = _CONTENT_MODE_VIDEO,
    paused: bool = False,
    now: datetime | None = None,
    active_clock_zone: int = 1,
    theme: _NpTheme | None = None,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> None:
    mode = _normalize_content_mode(content_mode)
    th = theme or np_theme_from_settings()
    root.set("style", "background:transparent")
    _remove_element_by_id(root, "background")
    _remove_element_by_id(root, "background-2")

    vis = _zone_widget_visibility(
        content_mode=mode, paused=paused, zone_widgets=zone_widgets
    )
    _apply_zone_visibility(root, vis)

    # Play triangle: match chrome grey used for icons / unplayed bar (#939393).
    for z in (1, 2, 3):
        play = _find_by_key(root, f"zone{z}_play_button")
        if play is not None:
            _set_tick_paint(play, color="#939393")

    # Remove demo text / embedded images / volume pies we redraw (keep tick paths).
    for name in _STRIP_OR_HIDE_NAMES:
        _remove_by_key(root, name)
    # Remove poster_tmdb images (Illustrator -N copies) and unused cast demo text.
    # Also strip audio-levels channel labels (Digital-7 redrawn after rasterize).
    pending: list[ET.Element] = []
    for el in root.iter():
        key = _layer_key(el)
        eid = el.get("id") or ""
        if key == "poster_tmdb" or eid.startswith("poster_tmdb"):
            pending.append(el)
            continue
        if key.endswith("_text") and any(
            key.startswith(p)
            for p in (
                "zone1_actor",
                "zone1_character",
                "zone2_actor",
                "zone2_character",
                "zone3_actor",
                "zone3_character",
                "zone4_actor",
                "zone4_character",
                "zone5_actor",
                "zone5_character",
                "zone5character",
            )
        ):
            pending.append(el)
            continue
        if "voume_audio_config" in key or key.endswith("audio_config_text"):
            pending.append(el)
            continue
        if el.tag.endswith("text") and "audio_levels" in key:
            pending.append(el)
    for el in pending:
        _detach_element(root, el)

    dt = now if now is not None else datetime.now()
    # Exterior (button) under middle (button); active zone adds accent seconds wedge.
    _apply_clock_accent_fills(root, theme=th)
    # Audio-levels channel bars follow settings UI color (LFE stays chrome).
    for zone in (1, 2, 3):
        group = None
        if vis.get(f"zone{zone}_audio_levels_group", False):
            group = _find_by_key(root, f"zone{zone}_audio_levels_group")
        if group is None and zone == 2 and vis.get("zone2_audio_levles_gtoup", False):
            group = _find_by_key(root, "zone2_audio_levles_gtoup")
        if group is None:
            continue
        for el in group.iter():
            key = _layer_key(el)
            if "lfe" in key.lower():
                continue
            if "audio_levels" in key and "button" in key and not key.endswith("_text"):
                _set_tick_paint(el, color=th.ui_hex)
    # Drive ticks for whichever clock zone is visible (preferences may move it).
    clock_zone = int(active_clock_zone)
    for z in (1, 2, 3):
        if vis.get(f"zone{z}_clock_group", False):
            clock_zone = z
            break
    if vis.get(f"zone{clock_zone}_clock_group", False):
        if _clock_widget_is_analog():
            _apply_clock_ticks(root, clock_zone, dt, theme=th)
        else:
            _apply_clock_black_rings(root, zone=clock_zone)
            _detach_clock_tick_groups(root, zone=clock_zone)
    # Clear any leftover digital clock text nodes.
    for z in (1, 2, 3):
        _clear_text_content(_find_by_key(root, f"zone{z}_clock_digital_text"))


def render_view_circles_svg_base_bgra(
    *,
    svg_path: Path | str | None = None,
    assets_dir: Path | str | None = None,
    content_mode: str = _CONTENT_MODE_VIDEO,
    paused: bool = False,
    now: datetime | None = None,
    theme: _NpTheme | None = None,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> np.ndarray:
    del svg_path, paused, content_mode
    th = theme or np_theme_from_settings()
    assignments = zone_widgets or _default_zone_widget_assignments()
    out = np.zeros((int(DESIGN_H), int(DESIGN_W), 4), dtype=np.uint8)
    now_dt = now if now is not None else datetime.now()
    chrome_keys = {"clock": "clock", "volume": "volume", "cast_info": "cast_info"}
    for z in (1, 2, 3, 4, 5):
        key = str(assignments[z - 1] if z <= len(assignments) else "").strip()
        if key == "cast_info" or key in ("now_playing", "status_bar"):
            # Cast text and status-bar fill are drawn in Pillow.
            continue
        widget_key = chrome_keys.get(key)
        if not widget_key:
            continue
        zone = _zone_spec(z)
        zx, zy, zw, zh = zone.xywh
        try:
            patch = _rasterize_named_widget(
                assets_dir=assets_dir,
                widget_key=widget_key,
                dest_w=zw,
                dest_h=zh,
                now=now_dt,
                theme=th,
                zone=z,
            )
        except Exception:
            patch = None
        if patch is None or patch.size == 0:
            continue
        _paste_patch_bgra(out, patch, zx, zy)
    return out


@lru_cache(maxsize=32)
def _load_digital7(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_digital7_font()
    candidates: list[str] = []
    if path:
        candidates.append(str(path))
    for fallback in (
        "/usr/share/fonts/truetype/digital-7/digital-7.ttf",
        "/Library/Fonts/Digital-7.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        if fallback not in candidates:
            candidates.append(fallback)
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, px)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=8)
def _load_sharp_extrabold(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_ui_font_extrabold()
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return _load_sharp_italic(px)


@lru_cache(maxsize=8)
def _load_sharp_semibold(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_ui_font_semibold()
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return _load_sharp_extrabold(px)


@lru_cache(maxsize=8)
def _load_sharp_italic(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_ui_font_extrabold_italic()
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return _load_digital7(px)


@lru_cache(maxsize=8)
def _load_sharp_medium_italic(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_ui_font_medium_italic()
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return _load_sharp_italic(px)


@lru_cache(maxsize=8)
def _load_sharp_light_italic(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    px = max(6, int(size))
    path = resolve_ui_font_light_italic()
    if path:
        try:
            return ImageFont.truetype(path, px)
        except OSError:
            pass
    return _load_sharp_italic(px)


def _text_patch_digital7(
    text: str,
    *,
    size_px: int,
    fill_rgb: tuple[int, int, int] = _COLOR_CHROME_RGB,
    max_width_px: int | None = None,
) -> tuple[np.ndarray, int, int]:
    draw_text = str(text or "")
    if not draw_text:
        return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0
    font = _load_digital7(size_px)
    pad = 2
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)

    def _measure(s: str) -> tuple[int, int, int, int]:
        return draw.textbbox((0, 0), s, font=font)

    if max_width_px is not None and max_width_px > 0:
        l, t, r, b = _measure(draw_text)
        if (r - l) > max_width_px:
            ell = "..."
            for n in range(len(draw_text), 0, -1):
                candidate = draw_text[:n].rstrip() + ell
                l2, t2, r2, b2 = _measure(candidate)
                if (r2 - l2) <= max_width_px:
                    draw_text = candidate
                    break

    l, t, r, b = _measure(draw_text)
    tw, th = max(1, r - l), max(1, b - t)
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - l, pad - t), draw_text, font=font, fill=(*fill_rgb, 255))
    arr = np.asarray(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA), tw + pad * 2, th + pad * 2


def _text_patch_font(
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill_rgb: tuple[int, int, int] = _COLOR_CHROME_RGB,
) -> tuple[np.ndarray, int, int]:
    draw_text = str(text or "")
    if not draw_text:
        return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0
    pad = 2
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    l, t, r, b = draw.textbbox((0, 0), draw_text, font=font)
    tw, th = max(1, r - l), max(1, b - t)
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((pad - l, pad - t), draw_text, font=font, fill=(*fill_rgb, 255))
    arr = np.asarray(img)
    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA), tw + pad * 2, th + pad * 2


def _fade_bgra(patch: np.ndarray, opacity: float) -> np.ndarray:
    """Return a copy with alpha multiplied by ``opacity`` (clamped to 0..1)."""
    if patch is None or patch.size == 0 or patch.ndim < 3 or patch.shape[2] < 4:
        return patch
    o = max(0.0, min(1.0, float(opacity)))
    if o >= 0.999:
        return patch
    out = patch.copy()
    out[:, :, 3] = np.clip(
        out[:, :, 3].astype(np.float32) * o, 0.0, 255.0
    ).astype(np.uint8)
    return out


def _paste_patch_bgra(canvas: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    if patch is None or patch.size == 0 or canvas is None or canvas.size == 0:
        return
    ph, pw = patch.shape[:2]
    if pw < 1 or ph < 1:
        return
    x0 = int(x)
    y0 = int(y)
    x1 = x0 + pw
    y1 = y0 + ph
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1 = min(int(canvas.shape[1]), x1)
    cy1 = min(int(canvas.shape[0]), y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    sx0 = cx0 - x0
    sy0 = cy0 - y0
    src = patch[sy0 : sy0 + (cy1 - cy0), sx0 : sx0 + (cx1 - cx0)]
    roi = canvas[cy0:cy1, cx0:cx1]
    if roi.ndim == 3 and roi.shape[2] == 3:
        roi[:] = alpha_blend_bgra_over_bgr(roi, src)
    elif roi.ndim == 3 and roi.shape[2] >= 4:
        blended = alpha_blend_bgra_over_bgr(roi[:, :, :3], src)
        roi[:, :, :3] = blended
        roi[:, :, 3] = np.maximum(roi[:, :, 3], src[:, :, 3])


def _restore_masked_region(
    canvas: np.ndarray,
    under: np.ndarray,
    mask: np.ndarray,
    *,
    x: int,
    y: int,
) -> None:
    if (
        canvas is None
        or canvas.size == 0
        or under is None
        or under.size == 0
        or mask is None
        or mask.size == 0
    ):
        return
    mh, mw = mask.shape[:2]
    if under.shape[0] != mh or under.shape[1] != mw:
        return
    x0 = int(x)
    y0 = int(y)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1 = min(int(canvas.shape[1]), x0 + mw)
    cy1 = min(int(canvas.shape[0]), y0 + mh)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    sx0, sy0 = cx0 - x0, cy0 - y0
    m = mask[sy0 : sy0 + (cy1 - cy0), sx0 : sx0 + (cx1 - cx0)] > 0
    if not np.any(m):
        return
    src = under[sy0 : sy0 + (cy1 - cy0), sx0 : sx0 + (cx1 - cx0)]
    roi = canvas[cy0:cy1, cx0:cx1]
    roi[m] = src[m]


def _restore_from_backdrop_abs(
    canvas: np.ndarray,
    mask: np.ndarray,
    *,
    x: int,
    y: int,
    backdrop: np.ndarray,
    backdrop_x: int,
    backdrop_y: int,
) -> None:
    if (
        canvas is None
        or canvas.size == 0
        or backdrop is None
        or backdrop.size == 0
        or mask is None
        or mask.size == 0
    ):
        return
    mh, mw = mask.shape[:2]
    bh, bw = int(backdrop.shape[0]), int(backdrop.shape[1])
    x0, y0 = int(x), int(y)
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1 = min(int(canvas.shape[1]), x0 + mw)
    cy1 = min(int(canvas.shape[0]), y0 + mh)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    sx0, sy0 = cx0 - x0, cy0 - y0
    roi_h, roi_w = cy1 - cy0, cx1 - cx0
    m = mask[sy0 : sy0 + roi_h, sx0 : sx0 + roi_w] > 0
    if not np.any(m):
        return
    yy, xx = np.nonzero(m)
    by = (cy0 - int(backdrop_y)) + yy
    bx = (cx0 - int(backdrop_x)) + xx
    valid = (by >= 0) & (by < bh) & (bx >= 0) & (bx < bw)
    if not np.any(valid):
        return
    yy, xx, by, bx = yy[valid], xx[valid], by[valid], bx[valid]
    canvas[cy0 + yy, cx0 + xx] = backdrop[by, bx]


def _paste_stroke_over_blur(
    canvas: np.ndarray,
    stroke_patch: np.ndarray,
    x: int,
    y: int,
    *,
    backdrop: np.ndarray | None,
    backdrop_x: int,
    backdrop_y: int,
    opacity: float,
) -> None:
    if stroke_patch is None or stroke_patch.size == 0:
        return
    coverage = stroke_patch[:, :, 3]
    if not np.any(coverage):
        return
    if backdrop is not None:
        _restore_from_backdrop_abs(
            canvas,
            coverage,
            x=x,
            y=y,
            backdrop=backdrop,
            backdrop_x=backdrop_x,
            backdrop_y=backdrop_y,
        )
    sop = max(0.0, min(1.0, float(opacity)))
    if sop >= 1.0 - 1e-6:
        _paste_patch_bgra(canvas, stroke_patch, x, y)
        return
    patch = stroke_patch.copy()
    patch[:, :, 3] = np.clip(patch[:, :, 3].astype(np.float32) * sop, 0, 255).astype(
        np.uint8
    )
    _paste_patch_bgra(canvas, patch, x, y)


def _ink_crop_bgra(patch: np.ndarray, *, pad: int = 1) -> np.ndarray:
    """Trim transparent padding so centering uses the visible ink box."""
    if patch is None or patch.size == 0 or patch.ndim < 3 or patch.shape[2] < 4:
        return patch
    ys, xs = np.where(patch[:, :, 3] > 8)
    if ys.size == 0:
        return patch
    y0 = max(0, int(ys.min()) - pad)
    x0 = max(0, int(xs.min()) - pad)
    y1 = min(int(patch.shape[0]), int(ys.max()) + 1 + pad)
    x1 = min(int(patch.shape[1]), int(xs.max()) + 1 + pad)
    return patch[y0:y1, x0:x1]


def _paste_ink_centered(
    canvas: np.ndarray, patch: np.ndarray, cx: float, top_y: float
) -> None:
    """Paste ``patch`` horizontally centered on ``cx`` with its top at ``top_y``."""
    if patch is None or patch.size == 0:
        return
    ph, pw = patch.shape[:2]
    _paste_patch_bgra(
        canvas, patch, int(round(float(cx) - pw / 2.0)), int(round(float(top_y)))
    )


def _cast_line_patch(text: str, *, size_px: int, max_width_px: int) -> np.ndarray | None:
    label = str(text or "").strip()
    if not label:
        return None
    patch, tw, _th = _text_patch_digital7(
        label.upper(), size_px=size_px, max_width_px=max_width_px
    )
    if tw < 1:
        return None
    return _ink_crop_bgra(patch)


def _draw_stacked_cast_pair(
    canvas: np.ndarray,
    *,
    actor: str,
    character: str,
    cx: float,
    max_width_px: int,
    actor_px: int,
    char_px: int,
    gap_px: int,
    mid_y: float | None = None,
    actor_top: float | None = None,
    char_top: float | None = None,
) -> None:
    """Actor over character, both horizontally centered on ``cx``.

    Either ``mid_y`` (v-center the stack) or explicit ``actor_top`` / ``char_top``.
    """
    ap = _cast_line_patch(actor, size_px=actor_px, max_width_px=max_width_px)
    cp = _cast_line_patch(character, size_px=char_px, max_width_px=max_width_px)
    ah = int(ap.shape[0]) if ap is not None else 0
    ch = int(cp.shape[0]) if cp is not None else 0
    gap = int(gap_px) if ap is not None and cp is not None else 0
    if mid_y is not None:
        total = ah + gap + ch
        top = float(mid_y) - total * 0.5
        if ap is not None:
            _paste_ink_centered(canvas, ap, cx, top)
        if cp is not None:
            _paste_ink_centered(canvas, cp, cx, top + ah + gap)
        return
    if ap is not None and actor_top is not None:
        _paste_ink_centered(canvas, ap, cx, float(actor_top))
    if cp is not None and char_top is not None:
        _paste_ink_centered(canvas, cp, cx, float(char_top))


def _paste_left_vcenter(canvas: np.ndarray, patch: np.ndarray, x: float, cy: float) -> None:
    """Paste left-aligned with the patch's vertical center on ``cy``."""
    if patch is None or patch.size == 0:
        return
    ph, _pw = patch.shape[:2]
    _paste_patch_bgra(canvas, patch, int(round(float(x))), int(round(float(cy) - ph / 2.0)))


def _paste_centered(canvas: np.ndarray, patch: np.ndarray, cx: float, cy: float) -> None:
    if patch is None or patch.size == 0:
        return
    ph, pw = patch.shape[:2]
    _paste_patch_bgra(canvas, patch, int(round(cx - pw / 2.0)), int(round(cy - ph / 2.0)))


def _paste_baseline_centered(
    canvas: np.ndarray,
    patch: np.ndarray,
    cx: float,
    baseline_y: float,
    *,
    bbox_top: float,
    pad: int = 2,
) -> None:
    """Paste a text patch so its typographic baseline sits on ``baseline_y``.

    Patches from :func:`_text_patch_font` / :func:`_text_patch_digital7` are built
    with Pillow's default (non-``ls``) metrics. For those, the ink bottom is the
    practical baseline for caps-only strings — place the patch so its bottom edge
    lands on ``baseline_y`` (keeps labels clear of circular widgets).
    """
    if patch is None or patch.size == 0:
        return
    ph, pw = patch.shape[:2]
    paste_x = int(round(cx - pw / 2.0))
    # Default Pillow metrics (SharpSans/Digital-7): positive bbox top means the
    # old ``pad - t`` baseline formula pushed ink into the widget. Prefer ink
    # bottom == baseline for label clearance above rings.
    if float(bbox_top) >= 0:
        paste_y = int(round(float(baseline_y) - float(ph)))
    else:
        paste_y = int(round(float(baseline_y) - (float(pad) - float(bbox_top))))
    _paste_patch_bgra(canvas, patch, paste_x, paste_y)


def _paste_baseline_left(
    canvas: np.ndarray,
    patch: np.ndarray,
    x: float,
    baseline_y: float,
    *,
    bbox_top: float,
    pad: int = 2,
) -> None:
    """Paste a text patch left-aligned with its ink bottom on ``baseline_y``."""
    if patch is None or patch.size == 0:
        return
    ph, _pw = patch.shape[:2]
    paste_x = int(round(float(x)))
    if float(bbox_top) >= 0:
        paste_y = int(round(float(baseline_y) - float(ph)))
    else:
        paste_y = int(round(float(baseline_y) - (float(pad) - float(bbox_top))))
    _paste_patch_bgra(canvas, patch, paste_x, paste_y)


def _paste_label_above_widget(
    canvas: np.ndarray,
    patch: np.ndarray,
    cx: float,
    *,
    widget_top_y: float,
    gap_px: float = _WIDGET_LABEL_BASELINE_GAP_PX,
) -> None:
    """Paste so the patch bottom (≈ baseline) is ``gap_px`` above the widget top."""
    if patch is None or patch.size == 0:
        return
    ph, pw = patch.shape[:2]
    paste_x = int(round(cx - pw / 2.0))
    paste_y = int(round(float(widget_top_y) - float(gap_px) - float(ph)))
    _paste_patch_bgra(canvas, patch, paste_x, paste_y)


def _paste_label_above_circle_curved(
    canvas: np.ndarray,
    patch: np.ndarray,
    cx: float,
    *,
    widget_cy: float,
    widget_r: float,
    gap_px: float = _WIDGET_LABEL_BASELINE_GAP_PX,
) -> None:
    """Paste label in a flat-top / curved-bottom envelope above a circular widget.

    Same width and topline as the flat paste. Each column is scaled uniformly so
    the bottom edge rides the concentric arc ``widget_r + gap`` (Illustrator-style
    envelope distort — not a bottom-only stretch, which causes spike artifacts).
    """
    if patch is None or patch.size == 0 or canvas is None or canvas.size == 0:
        return
    src_full = np.asarray(patch)
    if src_full.ndim != 3 or src_full.shape[2] < 4:
        return

    # Tight-crop to opaque ink so the glyph body fills the cap (pad would
    # leave the curved baseline sitting in empty space).
    alpha = src_full[:, :, 3]
    rows = np.where(alpha.max(axis=1) > 8)[0]
    cols = np.where(alpha.max(axis=0) > 8)[0]
    if rows.size < 1 or cols.size < 1:
        return
    r0, r1 = int(rows[0]), int(rows[-1]) + 1
    c0, c1 = int(cols[0]), int(cols[-1]) + 1
    src = np.ascontiguousarray(src_full[r0:r1, c0:c1])
    ph, pw = int(src.shape[0]), int(src.shape[1])
    if ph < 1 or pw < 1:
        return

    full_h, full_w = int(src_full.shape[0]), int(src_full.shape[1])
    r_arc = float(widget_r) + float(gap_px)
    if r_arc <= 1.0:
        _paste_label_above_widget(
            canvas,
            src_full,
            cx,
            widget_top_y=float(widget_cy) - float(widget_r),
            gap_px=gap_px,
        )
        return

    widget_top = float(widget_cy) - float(widget_r)
    # Match the previous flat paste's ink topline (skip transparent pad above glyphs).
    top_y = widget_top - float(gap_px) - float(full_h) + float(r0)
    x0_full = float(cx) - float(full_w) / 2.0
    x0 = x0_full + float(c0)

    xs = (x0 + 0.5) + np.arange(pw, dtype=np.float64)
    dx = xs - float(cx)
    inside = np.abs(dx) < (r_arc - 1e-3)
    # Default to a rectangular bottom; replace with the circle arc where valid.
    bottoms = np.full(pw, top_y + float(ph), dtype=np.float64)
    bottoms[inside] = float(widget_cy) - np.sqrt(
        np.maximum(0.0, r_arc * r_arc - dx[inside] * dx[inside])
    )
    # Never crush below the natural glyph height (keeps center from squashing).
    heights = np.maximum(bottoms - top_y, float(ph))
    dest_h = int(math.ceil(float(np.max(heights)))) + 1
    if dest_h < 1:
        return

    yy = np.arange(dest_h, dtype=np.float64)[:, None]
    xx = np.arange(pw, dtype=np.float64)[None, :]
    valid = yy < heights[None, :]

    # Uniform vertical envelope: each column maps [0, h) → full source [0, ph).
    h = np.maximum(heights[None, :], 1e-3)
    if ph <= 1:
        v_src = np.zeros((dest_h, pw), dtype=np.float64)
    else:
        v_src = (yy + 0.5) / h * float(ph) - 0.5
        v_src = np.clip(v_src, 0.0, float(ph - 1))

    map_x = np.broadcast_to(xx.astype(np.float32), (dest_h, pw)).copy()
    map_y = v_src.astype(np.float32)
    map_x = np.where(valid, map_x, np.float32(-1.0))
    map_y = np.where(valid, map_y, np.float32(-1.0))

    warped = cv2.remap(
        src,
        map_x,
        map_y,
        interpolation=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )
    if warped.dtype != np.uint8:
        warped = np.clip(warped, 0, 255).astype(np.uint8)
    warped = np.ascontiguousarray(warped)
    if warped.shape[2] >= 4:
        warped[~valid, :] = 0

    _paste_patch_bgra(
        canvas,
        warped,
        int(round(x0)),
        int(round(top_y)),
    )


def _font_bbox_top(text: str, font: ImageFont.ImageFont) -> float:
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    _l, t, _r, _b = draw.textbbox((0, 0), str(text or ""), font=font)
    return float(t)


def _rounded_rect_mask(w: int, h: int, radius: int) -> np.ndarray:
    if w < 1 or h < 1:
        return np.zeros((max(0, h), max(0, w)), dtype=np.uint8)
    r = max(0, min(radius, min(w, h) // 2))
    mask = np.zeros((h, w), dtype=np.uint8)
    if r <= 0:
        mask[:, :] = 255
        return mask
    cv2.rectangle(mask, (r, 0), (w - r - 1, h - 1), 255, -1)
    cv2.rectangle(mask, (0, r), (w - 1, h - r - 1), 255, -1)
    cv2.circle(mask, (r, r), r, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (w - r - 1, r), r, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (r, h - r - 1), r, 255, -1, lineType=cv2.LINE_AA)
    cv2.circle(mask, (w - r - 1, h - r - 1), r, 255, -1, lineType=cv2.LINE_AA)
    return mask


def _draw_rounded_bar_bgra(
    bgra: np.ndarray,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    fill_bgr: tuple[int, int, int],
    radius: int,
    stroke_bgr: tuple[int, int, int] | None = None,
    stroke: int = 2,
    fill_opacity: float = 1.0,
    stroke_opacity: float = 1.0,
    stroke_backdrop: np.ndarray | None = None,
    stroke_backdrop_x: int = 0,
    stroke_backdrop_y: int = 0,
) -> None:
    if w < 1 or h < 1:
        return
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(int(DESIGN_W), x + w), min(int(DESIGN_H), y + h)
    if x0 >= x1 or y0 >= y1:
        return
    lw, lh = x1 - x0, y1 - y0
    pad = max(0, int(stroke) + 1) if stroke_bgr is not None and stroke > 0 else 0
    mw, mh = lw + pad * 2, lh + pad * 2
    mask = np.zeros((mh, mw), dtype=np.uint8)
    inner = _rounded_rect_mask(lw, lh, min(radius, lw // 2, lh // 2))
    mask[pad : pad + lh, pad : pad + lw] = inner
    fill_a = int(round(255.0 * max(0.0, min(1.0, float(fill_opacity)))))
    fill = np.zeros((mh, mw, 4), dtype=np.uint8)
    fill[mask > 0, :3] = fill_bgr
    fill[mask > 0, 3] = fill_a
    _paste_patch_bgra(bgra, fill, x0 - pad, y0 - pad)
    if stroke_bgr is not None and stroke > 0:
        stroke_patch = np.zeros((mh, mw, 4), dtype=np.uint8)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            cv2.drawContours(
                stroke_patch,
                contours,
                -1,
                (*stroke_bgr, 255),
                thickness=max(1, int(stroke)),
                lineType=cv2.LINE_AA,
            )
            _paste_stroke_over_blur(
                bgra,
                stroke_patch,
                x0 - pad,
                y0 - pad,
                backdrop=stroke_backdrop,
                backdrop_x=stroke_backdrop_x,
                backdrop_y=stroke_backdrop_y,
                opacity=stroke_opacity,
            )


def annular_sector_mask(
    size: int,
    *,
    outer_r: float,
    inner_r: float,
    fraction: float,
    start_deg: float = -90.0,
) -> np.ndarray:
    s = max(1, int(size))
    mask = np.zeros((s, s), dtype=np.uint8)
    frac = max(0.0, min(1.0, float(fraction)))
    if frac <= 1e-6:
        return mask
    cx_i = (s - 1) // 2
    cy_i = (s - 1) // 2
    outer = max(1, int(round(outer_r)))
    inner = max(0, min(int(round(inner_r)), outer - 1))
    cv2.circle(mask, (cx_i, cy_i), outer, 255, -1, lineType=cv2.LINE_AA)
    if inner > 0:
        cv2.circle(mask, (cx_i, cy_i), inner, 0, -1, lineType=cv2.LINE_AA)
    if frac >= 0.999:
        return mask
    sweep = 360.0 * frac
    start = float(start_deg)
    end = start + sweep
    arc = cv2.ellipse2Poly(
        (cx_i, cy_i),
        (outer + 2, outer + 2),
        0,
        int(round(start)),
        int(round(end)),
        1,
    )
    wedge = np.zeros((s, s), dtype=np.uint8)
    poly = np.vstack([[[cx_i, cy_i]], arc])
    cv2.fillPoly(wedge, [poly], 255)
    return cv2.bitwise_and(mask, wedge)


def _halo_pad(sigma: float) -> int:
    return int(math.ceil(max(0.5, float(sigma)) * 3.0)) + 4


def _ui_halo_from_mask(mask01: np.ndarray, *, sigma: float, opacity: float) -> np.ndarray:
    """Turn a 0..1 float mask into a blurred white BGRA halo patch."""
    sig = max(0.5, float(sigma))
    op = max(0.0, min(1.0, float(opacity)))
    k = max(3, int(round(sig * 4.0)) | 1)
    soft = cv2.GaussianBlur(mask01, (k, k), sigmaX=sig, sigmaY=sig)
    patch = np.zeros((mask01.shape[0], mask01.shape[1], 4), dtype=np.uint8)
    patch[:, :, 0] = 255
    patch[:, :, 1] = 255
    patch[:, :, 2] = 255
    patch[:, :, 3] = np.clip(soft * (255.0 * op), 0, 255).astype(np.uint8)
    return patch


@lru_cache(maxsize=4)
def _zone_ring_halo_patch(
    outer_r: float = _CLOCK_EXTERIOR_ACCENT_R,
    inner_r: float = _CLOCK_MIDDLE_ACCENT_R,
    sigma: float = _ZONE_HALO_BLUR_SIGMA,
    opacity: float = _ZONE_HALO_OPACITY,
) -> np.ndarray:
    """White ring behind the outer clock band only (middle stays open)."""
    outer = max(1, int(round(float(outer_r))))
    inner = max(0, min(int(round(float(inner_r))), outer - 1))
    pad = _halo_pad(sigma)
    size = outer * 2 + pad * 2
    mask = np.zeros((size, size), dtype=np.float32)
    c = size // 2
    cv2.circle(mask, (c, c), outer, 1.0, -1, lineType=cv2.LINE_AA)
    if inner > 0:
        cv2.circle(mask, (c, c), inner, 0.0, -1, lineType=cv2.LINE_AA)
    return _ui_halo_from_mask(mask, sigma=sigma, opacity=opacity)


def _draw_zone_halos(
    bgra: np.ndarray,
    *,
    content_mode: str,
    paused: bool = False,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> None:
    """Gentle white glow behind the clock's outer ring (not the open middle)."""
    del content_mode, paused
    assignments = zone_widgets if zone_widgets is not None else _default_zone_widget_assignments()
    circle = _zone_ring_halo_patch()
    ch, cw = circle.shape[:2]
    for z in (1, 2, 3):
        if z > len(assignments) or assignments[z - 1] != "clock":
            continue
        cx, cy = _zone_clock_center(z)
        _paste_patch_bgra(
            bgra,
            circle,
            int(round(cx - cw / 2.0)),
            int(round(cy - ch / 2.0)),
        )


def _draw_filled_circle_bgra(
    bgra: np.ndarray,
    *,
    cx: float,
    cy: float,
    r: float,
    fill_bgr: tuple[int, int, int],
    stroke_bgr: tuple[int, int, int] = _COLOR_CHROME_BGR,
    stroke: int = 2,
    fill_opacity: float = 1.0,
) -> None:
    radius = max(1, int(round(r)))
    pad = stroke + 2
    size = radius * 2 + pad * 2
    center = (size // 2, size // 2)
    mask = np.zeros((size, size), dtype=np.uint8)
    cv2.circle(mask, center, radius, 255, -1, lineType=cv2.LINE_AA)
    op = max(0.0, min(1.0, float(fill_opacity)))
    patch = np.zeros((size, size, 4), dtype=np.uint8)
    patch[:, :, 0] = fill_bgr[0]
    patch[:, :, 1] = fill_bgr[1]
    patch[:, :, 2] = fill_bgr[2]
    patch[:, :, 3] = np.clip(mask.astype(np.float32) * op, 0, 255).astype(np.uint8)
    x = int(round(cx - size / 2.0))
    y = int(round(cy - size / 2.0))
    _paste_patch_bgra(bgra, patch, x, y)
    if stroke > 0:
        stroke_patch = np.zeros((size, size, 4), dtype=np.uint8)
        cv2.circle(
            stroke_patch, center, radius, (*stroke_bgr, 255), stroke, lineType=cv2.LINE_AA
        )
        _paste_patch_bgra(bgra, stroke_patch, x, y)


def _draw_progress_ring(
    bgra: np.ndarray,
    *,
    cx: float,
    cy: float,
    outer_r: float,
    inner_r: float,
    fraction: float,
    fill_bgr: tuple[int, int, int] = _COLOR_ACCENT_BGR,
    stroke_bgr: tuple[int, int, int] = _COLOR_CHROME_BGR,
    stroke: int = _ACCENT_STROKE_PX,
    fill_opacity: float = _ACCENT_OPACITY,
    stroke_opacity: float = _ACCENT_STROKE_OPACITY,
    stroke_backdrop: np.ndarray | None = None,
    stroke_backdrop_x: int = 0,
    stroke_backdrop_y: int = 0,
) -> None:
    frac = max(0.0, min(1.0, float(fraction)))
    if frac <= 1e-6:
        return
    pad = max(4, stroke + 2)
    size = int(math.ceil(outer_r * 2)) + pad * 2
    mask = annular_sector_mask(
        size,
        outer_r=outer_r,
        inner_r=inner_r,
        fraction=frac,
    )
    fill_a = int(round(255.0 * max(0.0, min(1.0, float(fill_opacity)))))
    fill = np.zeros((size, size, 4), dtype=np.uint8)
    fill[mask > 0, :3] = fill_bgr
    fill[mask > 0, 3] = fill_a
    x = int(round(cx - size / 2.0))
    y = int(round(cy - size / 2.0))
    _paste_patch_bgra(bgra, fill, x, y)
    if stroke > 0:
        stroke_patch = np.zeros((size, size, 4), dtype=np.uint8)
        cx_i = cy_i = size // 2
        outer = max(1, int(round(outer_r)))
        inner = max(0, min(int(round(inner_r)), outer - 1))
        start = -90.0
        end = start + 360.0 * frac
        thick = max(1, int(stroke))
        color = (*stroke_bgr, 255)
        if frac >= 0.999:
            cv2.circle(stroke_patch, (cx_i, cy_i), outer, color, thick, lineType=cv2.LINE_AA)
            if inner > 0:
                cv2.circle(stroke_patch, (cx_i, cy_i), inner, color, thick, lineType=cv2.LINE_AA)
        else:
            cv2.ellipse(
                stroke_patch,
                (cx_i, cy_i),
                (outer, outer),
                0,
                start,
                end,
                color,
                thick,
                lineType=cv2.LINE_AA,
            )
            if inner > 0:
                cv2.ellipse(
                    stroke_patch,
                    (cx_i, cy_i),
                    (inner, inner),
                    0,
                    start,
                    end,
                    color,
                    thick,
                    lineType=cv2.LINE_AA,
                )
            for ang in (start, end):
                rad = math.radians(ang)
                x_o = int(round(cx_i + outer * math.cos(rad)))
                y_o = int(round(cy_i + outer * math.sin(rad)))
                x_i = int(round(cx_i + inner * math.cos(rad)))
                y_i = int(round(cy_i + inner * math.sin(rad)))
                cv2.line(
                    stroke_patch, (x_i, y_i), (x_o, y_o), color, thick, lineType=cv2.LINE_AA
                )
        _paste_stroke_over_blur(
            bgra,
            stroke_patch,
            x,
            y,
            backdrop=stroke_backdrop,
            backdrop_x=stroke_backdrop_x,
            backdrop_y=stroke_backdrop_y,
            opacity=stroke_opacity,
        )


def _draw_circle_pair(
    bgra: np.ndarray,
    *,
    cx: float,
    cy: float,
    fraction: float,
    show_accent: bool,
    theme: _NpTheme | None = None,
) -> None:
    """Volume / circular-NP ring: grey track, UI-color level, black center.

    Pure-black track on the black stage reads as a "blacked out" zone once the
    TMDb intro spin stops (especially at low / zero volume). Chrome-grey empty
    track + edge strokes keep the widget present; theme.ui shows level. Center
    disc stays pure black (not the settings button swatch).
    """
    del show_accent  # track always drawn; UI overlay follows fraction
    th = theme or np_theme_from_settings()
    frac = max(0.0, min(1.0, float(fraction)))
    # Empty headroom — opaque enough to read on black / blurred artwork.
    _draw_progress_ring(
        bgra,
        cx=cx,
        cy=cy,
        outer_r=_RING_OUTER_R,
        inner_r=_RING_INNER_R,
        fraction=1.0,
        fill_bgr=_COLOR_CHROME_BGR,
        fill_opacity=0.42,
        stroke=0,
    )
    if frac > 1e-6:
        _draw_progress_ring(
            bgra,
            cx=cx,
            cy=cy,
            outer_r=_RING_OUTER_R,
            inner_r=_RING_INNER_R,
            fraction=frac,
            fill_bgr=th.ui_bgr,
            fill_opacity=1.0,
            stroke=0,
        )
    # Outer / inner rim so the annulus stays defined even at 0% volume.
    _draw_progress_ring(
        bgra,
        cx=cx,
        cy=cy,
        outer_r=_RING_OUTER_R,
        inner_r=_RING_INNER_R,
        fraction=1.0,
        fill_bgr=_COLOR_CHROME_BGR,
        fill_opacity=0.0,
        stroke=2,
        stroke_bgr=_COLOR_CHROME_BGR,
        stroke_opacity=0.85,
    )
    _draw_filled_circle_bgra(
        bgra,
        cx=cx,
        cy=cy,
        r=_RING_INNER_R,
        fill_bgr=_COLOR_CENTER_BLACK_BGR,
        fill_opacity=1.0,
    )


def _matching_hhmm_patch(
    text: str,
    *,
    size_px: int,
    fill_rgb: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    """Digital-7 time using clock-saver matching-cell spacing (skinny ``1`` / ``:`` / ``-``)."""
    from pigeon.widgets.clock_saver import (
        _HHMMSS_CHAR_SET,
        _cell_metrics,
        _hhmmss_advance,
    )

    label = str(text or "").strip()
    if not label:
        return np.zeros((1, 1, 4), dtype=np.uint8)
    font = _load_digital7(size_px)
    probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    charset = _HHMMSS_CHAR_SET + "-"
    matching_w, cell_h = _cell_metrics(draw, font, charset)
    total_w = 0
    advances: list[int] = []
    for ch in label:
        if ch == "-":
            adv = max(1, int(round(0.5 * matching_w)))
        else:
            adv = _hhmmss_advance(matching_w, ch)
        advances.append(adv)
        total_w += adv
    pad_x = 4
    pad_y = 10
    img = Image.new(
        "RGBA",
        (max(1, total_w + pad_x * 2), max(1, cell_h + pad_y * 2)),
        (0, 0, 0, 0),
    )
    draw = ImageDraw.Draw(img)
    x = pad_x
    cy = pad_y + cell_h // 2
    color = (*fill_rgb, 255)
    for ch, adv in zip(label, advances):
        draw.text((x + adv // 2, cy), ch, font=font, fill=color, anchor="mm")
        x += adv
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)


def format_status_bar_timecode(raw: str, *, remaining: bool = False) -> str:
    """Clock-saver duration: drop hours when they are 00; remaining is ``-MM:SS``."""
    s = str(raw or "").strip()
    if not s:
        return ""
    if s.upper() == "LIVE":
        return "LIVE"
    sign = s.startswith("-")
    body = s[1:] if sign else s
    parts = [p for p in re.split(r"[:.]", body) if p != ""]
    try:
        nums = [max(0, int(p)) for p in parts]
    except ValueError:
        return s
    hours = minutes = seconds = 0
    if len(nums) >= 3:
        hours, minutes, seconds = nums[0], nums[1], nums[2]
    elif len(nums) == 2:
        minutes, seconds = nums[0], nums[1]
    elif len(nums) == 1:
        seconds = nums[0]
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
    if hours > 0:
        out = f"{hours}:{minutes:02d}:{seconds:02d}"
    else:
        out = f"{minutes:02d}:{seconds:02d}"
    if remaining or sign:
        out = "-" + out.lstrip("-")
    return out


def _draw_volume_selected_pie(
    bgra: np.ndarray,
    *,
    cx: float,
    cy: float,
    fraction: float,
    theme: _NpTheme | None = None,
) -> None:
    """UI-color volume pie from 12 o'clock clockwise; SVG already paints the grey track."""
    th = theme or np_theme_from_settings()
    frac = max(0.0, min(1.0, float(fraction)))
    if frac <= 1e-6:
        return
    _draw_progress_ring(
        bgra,
        cx=cx,
        cy=cy,
        outer_r=VOLUME_OUTER_R,
        inner_r=VOLUME_INNER_R,
        fraction=frac,
        fill_bgr=th.ui_bgr,
        fill_opacity=1.0,
        stroke=0,
    )


def _clock_hhmm(now: datetime | None = None) -> str:
    dt = now if now is not None else datetime.now()
    h12 = dt.hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{dt.minute:02d}"


def _fallback_base_bgra() -> np.ndarray:
    out = np.zeros((int(DESIGN_H), int(DESIGN_W), 4), dtype=np.uint8)
    out[:, :, 3] = 255
    return out


class ViewCirclesWidget:
    """Zoned now-playing layout for DisplayView.ONE (circles skin)."""

    def __init__(self, *, assets_dir: Path) -> None:
        self._assets_dir = Path(assets_dir)
        self._state = ViewCirclesState()
        self._poster_bgra: np.ndarray | None = None
        self._cached_bgra: np.ndarray | None = None
        self._cached_sig: tuple[object, ...] | None = None
        self._svg_chrome_by_key: dict[tuple[object, ...], np.ndarray] = {}
        self._bar_overlay_layer: np.ndarray | None = None
        self._artwork_blur_bgra: np.ndarray | None = None
        self._artwork_blur_poster_id: int | None = None
        self._search_frames: tuple[np.ndarray, ...] | None = None
        self._search_frames_tried = False
        self._last_tick_mono: float | None = None
        self._bar_handoff = 0.0
        self._bar_handoff_want = 0.0
        self._bar_handoff_inited = False
        self._bar_handoff_mono: float | None = None
        self._spin_sec_phase = 0.0
        self._spin_min_phase = 0.0
        self._spin_hour_phase = 0.0
        self._spin_vol_phase = 0.0

    @property
    def chrome_visible(self) -> bool:
        return self._state.chrome_visible

    @property
    def searching(self) -> bool:
        return bool(self._state.searching)

    @property
    def content_mode(self) -> str:
        return _normalize_content_mode(self._state.content_mode)

    def clear_cache(self) -> None:
        self._cached_bgra = None
        self._cached_sig = None

    def _clear_artwork_blur_cache(self) -> None:
        self._artwork_blur_bgra = None
        self._artwork_blur_poster_id = None

    def _reset_clock_spin_from_wall(self) -> None:
        """Seed intro spin phases from the current wall clock + volume."""
        n = datetime.now()
        h12 = n.hour % 12
        self._spin_hour_phase = float(h12)  # 0 = 12 o'clock face
        self._spin_min_phase = float(n.minute)
        self._spin_sec_phase = float(n.second)
        self._spin_vol_phase = float(self._state.volume_fraction)

    def _clock_now_for_display(self) -> datetime:
        """Wall clock. (Intro racing-clock animation removed — real values always.)"""
        return datetime.now()

    def _volume_fraction_for_display(self) -> float:
        if self._state.volume_muted:
            return 0.0
        return float(self._state.volume_fraction)

    def set_now_playing_chrome_visible(self, visible: bool) -> bool:
        v = bool(visible)
        if v == self._state.chrome_visible:
            return False
        self._state.chrome_visible = v
        if v and self._state.searching:
            self._reset_clock_spin_from_wall()
            self._last_tick_mono = None
        self.clear_cache()
        return True

    def set_poster_bgra(self, poster_bgra: np.ndarray | None) -> bool:
        if poster_bgra is None:
            if self._poster_bgra is None:
                return False
            self._poster_bgra = None
            self._clear_artwork_blur_cache()
            self.clear_cache()
            return True
        arr = np.asarray(poster_bgra, dtype=np.uint8)
        if self._poster_bgra is not None and self._poster_bgra.shape == arr.shape:
            if np.array_equal(self._poster_bgra, arr):
                return False
        self._poster_bgra = arr.copy()
        self._clear_artwork_blur_cache()
        self.clear_cache()
        return True

    def _ensure_artwork_blur_bgra(self) -> np.ndarray | None:
        src = self._poster_bgra
        if src is None or src.size == 0:
            self._clear_artwork_blur_cache()
            return None
        pid = id(src)
        if self._artwork_blur_bgra is not None and self._artwork_blur_poster_id == pid:
            return self._artwork_blur_bgra
        self._artwork_blur_bgra = _build_artwork_blur_bgra(src)
        self._artwork_blur_poster_id = pid
        return self._artwork_blur_bgra

    def update_state(
        self,
        *,
        progress: float,
        elapsed_text: str,
        remaining_text: str,
        volume_text: str,
        volume_fraction: float | None = None,
        incoming_audio: str = "",
        playback_config: str = "",
        cast: list[tuple[str, str]] | None = None,
        poster_bgra: np.ndarray | None = None,
        has_now_playing: bool = True,
        searching: bool | None = None,
        missing_art: bool | None = None,
        content_mode: str | None = None,
        song_title: str | None = None,
        album_title: str | None = None,
        artist_title: str | None = None,
        paused: bool | None = None,
        service_name: str | None = None,
        has_position: bool | None = None,
        content_active: bool | None = None,
    ) -> bool:
        changed = False
        if self.set_now_playing_chrome_visible(has_now_playing):
            changed = True
        if content_active is not None:
            want_active = bool(content_active)
            if want_active != self._state.content_active:
                self._state.content_active = want_active
                changed = True
        if content_mode is not None:
            mode = _normalize_content_mode(content_mode)
            if mode != self._state.content_mode:
                self._state.content_mode = mode
                changed = True
        is_music = self._state.content_mode == _CONTENT_MODE_MUSIC
        pf = max(0.0, min(1.0, float(progress)))
        if abs(pf - self._state.progress) > 1e-9:
            self._state.progress = pf
            changed = True
        et = str(elapsed_text or "")
        if et != self._state.elapsed_text:
            self._state.elapsed_text = et
            changed = True
        rt = str(remaining_text or "")
        if rt != self._state.remaining_text:
            self._state.remaining_text = rt
            changed = True
        vol = str(volume_text or "")
        if vol != self._state.volume:
            self._state.volume = vol
            changed = True
        muted = vol.strip().lower() in ("mute", "muted", "off")
        if muted != self._state.volume_muted:
            self._state.volume_muted = muted
            changed = True
        if volume_fraction is None:
            vf = volume_fraction_from_display_line(vol)
        else:
            vf = max(0.0, min(1.0, float(volume_fraction)))
        if abs(vf - self._state.volume_fraction) > 1e-6:
            self._state.volume_fraction = vf
            changed = True
        inc = str(incoming_audio or "")
        cfg = str(playback_config or "")
        if inc != self._state.incoming:
            self._state.incoming = inc
            changed = True
        if cfg != self._state.config:
            self._state.config = cfg
            changed = True
        if paused is not None:
            want_paused = bool(paused)
            if want_paused != self._state.paused:
                self._state.paused = want_paused
                changed = True
        if has_position is not None:
            want_pos = bool(has_position)
            if want_pos != self._state.has_position:
                self._state.has_position = want_pos
                changed = True
        if service_name is not None:
            svc = str(service_name or "").strip()
            if svc != self._state.service_name:
                self._state.service_name = svc
                changed = True
        if is_music:
            if song_title is not None:
                st = str(song_title or "")
                if st != self._state.song_title:
                    self._state.song_title = st
                    changed = True
            if album_title is not None:
                al = str(album_title or "")
                if al != self._state.album_title:
                    self._state.album_title = al
                    changed = True
            if artist_title is not None:
                ar = str(artist_title or "")
                if ar != self._state.artist_title:
                    self._state.artist_title = ar
                    changed = True
            if self._state.cast:
                self._state.cast = []
                changed = True
        else:
            if self._state.song_title or self._state.album_title or self._state.artist_title:
                self._state.song_title = ""
                self._state.album_title = ""
                self._state.artist_title = ""
                changed = True
            if cast is not None:
                norm = [(str(a or ""), str(c or "")) for a, c in cast[:21]]
                if norm != self._state.cast:
                    self._state.cast = norm
                    changed = True
        if searching is not None:
            want = bool(searching)
            if want != self._state.searching:
                self._state.searching = want
                if want:
                    self._state.search_angle_deg = 0.0
                    self._last_tick_mono = None
                    self._reset_clock_spin_from_wall()
                    self._ensure_search_frames()
                else:
                    # TMDb settled — next frame uses real wall clock / volume.
                    self._last_tick_mono = None
                changed = True
        if missing_art is not None:
            want_miss = bool(missing_art)
            if want_miss != self._state.missing_art:
                self._state.missing_art = want_miss
                changed = True
        if self._state.searching:
            if self.set_poster_bgra(None):
                changed = True
        elif self.set_poster_bgra(poster_bgra):
            changed = True
        if changed:
            self.clear_cache()
        return changed

    def _advance_status_bar_handoff(self, now: float | None = None) -> bool:
        """Lerp parked-elapsed → service + traveling elapsed. True if ``t`` changed."""
        if now is None:
            now = time.monotonic()
        want = float(self._bar_handoff_want)
        cur = float(self._bar_handoff)
        if self._bar_handoff_mono is None:
            self._bar_handoff_mono = now
            return False
        dt = max(0.0, now - self._bar_handoff_mono)
        self._bar_handoff_mono = now
        if abs(cur - want) <= 1e-4:
            if cur != want:
                self._bar_handoff = want
                return True
            return False
        step = dt / max(1e-6, float(STATUS_BAR_HANDOFF_S))
        if cur < want:
            self._bar_handoff = min(want, cur + step)
        else:
            self._bar_handoff = max(want, cur - step)
        return True

    def tick(self) -> None:
        now = time.monotonic()
        faded = self._advance_status_bar_handoff(now)
        if not self._state.searching:
            self._last_tick_mono = None
            if faded:
                self.clear_cache()
            return
        if self._last_tick_mono is None:
            self._last_tick_mono = now
            if faded:
                self.clear_cache()
            return
        dt = max(0.0, now - self._last_tick_mono)
        self._last_tick_mono = now
        prev_angle = self._state.search_angle_deg
        self._state.search_angle_deg = advance_angle_deg(prev_angle, dt)
        # Intro racing clock/volume removed: only the search spinner animates.
        spun = int(round(prev_angle / 10.0)) != int(
            round(self._state.search_angle_deg / 10.0)
        )
        if spun or faded:
            self.clear_cache()

    def _ensure_search_frames(self) -> tuple[np.ndarray, ...] | None:
        if self._search_frames is not None:
            return self._search_frames
        if self._search_frames_tried:
            return None
        self._search_frames_tried = True
        self._search_frames = build_search_spinner_frames(self._assets_dir)
        return self._search_frames

    def _wants_16x9_poster(self) -> bool:
        return wants_16x9_poster(
            service_name=self._state.service_name,
            poster_bgra=self._poster_bgra,
            content_mode=self.content_mode,
        )

    def _has_drawable_poster(self) -> bool:
        """True when the poster slot has art or an in-flight search spinner."""
        if self._state.searching:
            return True
        src = self._poster_bgra
        return src is not None and src.size > 0

    def _assignments(self) -> tuple[str, str, str, str, str]:
        named = sum(1 for actor, _role in (self._state.cast or []) if str(actor or "").strip())
        has_poster = self._has_drawable_poster()
        return _effective_zone_widgets(
            has_position=bool(self._state.has_position),
            cast_count=named,
            content_active=bool(self._state.content_active),
            poster_16x9=self._wants_16x9_poster() and has_poster,
            has_poster=has_poster,
        )

    def _poster_zone(self) -> int | None:
        if not self._has_drawable_poster():
            return None
        if self._wants_16x9_poster() and self._state.content_active:
            return int(DEFAULT_16X9_POSTER_ZONE)
        return _zone_for_widget(self._assignments(), "poster")

    def _cache_sig(self) -> tuple[object, ...]:
        st = self._state
        cast_sig = tuple(st.cast[:21])
        poster_id = id(self._poster_bgra) if self._poster_bgra is not None else None
        now = self._clock_now_for_display()
        search_frame = (
            int(round(st.search_angle_deg / 10.0)) % 36 if st.searching else -1
        )
        h12 = now.hour % 12
        if h12 == 0:
            h12 = 12
        vol_disp = self._volume_fraction_for_display()
        zone_widgets = self._assignments()
        theme_key = np_theme_from_settings().cache_key
        return (
            42,  # cache schema — 16x9 poster slot (zone 7 default)
            st.content_mode,
            st.has_position,
            st.content_active,
            round(st.progress, 6),
            st.elapsed_text,
            st.remaining_text,
            st.volume,
            round(vol_disp, 5),
            st.volume_muted and not st.searching,
            st.incoming,
            st.config,
            st.chrome_visible,
            cast_sig,
            st.song_title,
            st.album_title,
            st.artist_title,
            poster_id,
            st.searching,
            st.missing_art,
            st.paused,
            st.service_name,
            search_frame,
            h12,
            int(now.minute),
            int(now.second),
            _format_zone0_date(datetime.now()),
            zone_widgets,
            theme_key,
            round(self._bar_handoff, 2),
        )

    def _svg_chrome_cache_key(self, now: datetime) -> tuple[object, ...]:
        mode = self.content_mode
        path = default_view_circles_svg_path(self._assets_dir, content_mode=mode)
        mtime = 0
        try:
            if path.is_dir():
                for name in WIDGET_FILENAMES.values():
                    try:
                        mtime ^= (path / name).stat().st_mtime_ns
                    except OSError:
                        mtime ^= -1
            else:
                mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = -1
        h12 = now.hour % 12
        if h12 == 0:
            h12 = 12
        zone_widgets = self._assignments()
        # Ticks live in the per-widget raster cache; do not keep 96 full-frame
        # 1280×800 copies (that was hundreds of MB and a raster every second).
        return (
            str(path),
            mtime,
            mode,
            bool(self._state.paused),
            h12,
            int(now.minute),
            10,  # chrome pipeline — settings theme colors
            zone_widgets,
            np_theme_from_settings().cache_key,
        )

    def _render_svg_base(self, now: datetime) -> np.ndarray:
        key = self._svg_chrome_cache_key(now)
        cached = self._svg_chrome_by_key.get(key)
        if cached is not None:
            # Clock second ticks are per-widget; restamp them onto the minute chrome.
            analog = _clock_widget_is_analog()
            if analog:
                clock_zone = _zone_for_widget(self._assignments(), "clock")
                if clock_zone is not None:
                    out = cached.copy()
                    z = _zone_spec(int(clock_zone))
                    zx, zy, zw, zh = z.xywh
                    patch = _rasterize_named_widget(
                        assets_dir=self._assets_dir,
                        widget_key="clock",
                        dest_w=zw,
                        dest_h=zh,
                        now=now,
                        theme=np_theme_from_settings(),
                        zone=int(clock_zone),
                    )
                    if patch is not None and patch.size:
                        _paste_patch_bgra(out, patch, zx, zy)
                    return out
            return cached
        try:
            base = render_view_circles_svg_base_bgra(
                assets_dir=self._assets_dir,
                content_mode=self.content_mode,
                paused=bool(self._state.paused),
                now=now,
                theme=np_theme_from_settings(),
                zone_widgets=self._assignments(),
            )
        except Exception:
            base = _fallback_base_bgra()
        while len(self._svg_chrome_by_key) > 8:
            self._svg_chrome_by_key.pop(next(iter(self._svg_chrome_by_key)))
        self._svg_chrome_by_key[key] = base
        return base

    def _draw_missing_art_placeholder(
        self, out: np.ndarray, px: int, py: int, pw: int, ph: int, prx: int
    ) -> None:
        mask = _rounded_rect_mask(pw, ph, prx)
        plate = np.zeros((ph, pw, 4), dtype=np.uint8)
        plate[:, :, 0] = 28
        plate[:, :, 1] = 28
        plate[:, :, 2] = 28
        plate[:, :, 3] = np.minimum(np.uint8(210), mask)
        _paste_patch_bgra(out, plate, px, py)
        try:
            from pigeon.font_paths import resolve_ui_font_extrabold

            font_path = resolve_ui_font_extrabold()
        except Exception:
            font_path = None
        cx = int(round(px + pw / 2.0))
        cy = int(round(py + ph / 2.0))
        if font_path is not None:
            try:
                font = ImageFont.truetype(str(font_path), size=max(48, int(round(ph * 0.42))))
                img = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                glyph = "?"
                bbox = draw.textbbox((0, 0), glyph, font=font)
                tw = max(1, int(bbox[2] - bbox[0]))
                th = max(1, int(bbox[3] - bbox[1]))
                tx = (pw - tw) // 2 - int(bbox[0])
                ty = (ph - th) // 2 - int(bbox[1]) - max(2, ph // 40)
                draw.text((tx, ty), glyph, font=font, fill=(220, 220, 220, 255))
                arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
                arr[:, :, 3] = np.minimum(arr[:, :, 3], mask)
                _paste_patch_bgra(out, arr, px, py)
                return
            except Exception:
                pass
        scale = max(1.5, ph / 140.0)
        thickness = max(2, int(round(scale * 2.2)))
        (tw, th), _ = cv2.getTextSize("?", cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        tx = cx - tw // 2
        ty = cy + th // 2
        cv2.putText(
            out,
            "?",
            (tx, ty),
            cv2.FONT_HERSHEY_SIMPLEX,
            scale,
            (220, 220, 220, 255) if out.shape[2] == 4 else (220, 220, 220),
            thickness,
            lineType=cv2.LINE_AA,
        )

    def _draw_poster(self, out: np.ndarray) -> None:
        poster_zone = self._poster_zone()
        if poster_zone is None:
            return
        px, py, pw, ph, prx = _poster_geometry(
            self.content_mode, zone=int(poster_zone)
        )
        src = self._poster_bgra
        if src is not None and src.size > 0 and not self._state.searching:
            if src.ndim == 3 and src.shape[2] == 3:
                src = cv2.cvtColor(src, cv2.COLOR_BGR2BGRA)
            sh, sw = src.shape[:2]
            if sh >= 1 and sw >= 1:
                scale = max(pw / float(sw), ph / float(sh))
                nw = max(1, int(round(sw * scale)))
                nh = max(1, int(round(sh * scale)))
                resized = cv2.resize(
                    src,
                    (nw, nh),
                    interpolation=cv_resize_interp(sw, sh, nw, nh),
                )
                x0 = max(0, (nw - pw) // 2)
                y0 = max(0, (nh - ph) // 2)
                crop = resized[y0 : y0 + ph, x0 : x0 + pw]
                if crop.shape[0] != ph or crop.shape[1] != pw:
                    crop = cv2.resize(crop, (pw, ph), interpolation=cv2.INTER_AREA)
                mask = _rounded_rect_mask(pw, ph, prx)
                patch = crop.copy()
                if patch.shape[2] == 3:
                    patch = cv2.cvtColor(patch, cv2.COLOR_BGR2BGRA)
                patch[:, :, 3] = np.minimum(patch[:, :, 3], mask)
                _paste_patch_bgra(out, patch, px, py)
        if self._state.searching:
            frames = self._ensure_search_frames()
            if frames:
                cx = int(round(px + pw / 2.0))
                cy = int(round(py + ph / 2.0))
                patch = rotated_patch_for_angle(frames, self._state.search_angle_deg)
                blit_spinner_patch(out, patch, cx=cx, cy=cy)

    def _draw_status_bar(self, out: np.ndarray, *, zone: int | None = None) -> None:
        bar_zone = int(zone) if zone is not None else None
        if bar_zone is None:
            assignments = self._assignments()
            for i, name in enumerate(assignments[:5]):
                if is_status_bar_widget(name, i + 1):
                    bar_zone = i + 1
                    break
        if bar_zone is None:
            return
        zone = _zone_spec(int(bar_zone))
        st = self._state
        pf = max(0.0, min(1.0, float(st.progress)))
        tx, ty, tw, th, trx = design_rect_from_local(
            zone,
            STATUS_BAR_TRACK,
            view_w=STATUS_BAR_VIEW_W,
            view_h=STATUS_BAR_VIEW_H,
        )
        _draw_rounded_bar_bgra(
            out,
            x=tx,
            y=ty,
            w=tw,
            h=th,
            fill_bgr=_COLOR_UNPLAYED_BGR,
            radius=trx,
            stroke_bgr=None,
            fill_opacity=1.0,
        )
        # Reveal elapsed by cropping a UI-colored copy of the same rounded rect
        # from the left (0% hidden … 100% fully visible).
        if pf > 1e-6 and tw > 1 and th > 1:
            vis_w = max(0, min(tw, int(round(pf * float(tw)))))
            if vis_w > 0:
                mask = _rounded_rect_mask(tw, th, trx)
                elapsed = np.zeros((th, tw, 4), dtype=np.uint8)
                elapsed[:, :, :3] = np_theme_from_settings().ui_bgr
                elapsed[:, :, 3] = mask
                elapsed[:, vis_w:, 3] = 0
                _paste_patch_bgra(out, elapsed, tx, ty)

        def _bar_xy(local: tuple[float, float]) -> tuple[float, float]:
            return design_xy_from_local(
                zone, local[0], local[1], view_w=STATUS_BAR_VIEW_W, view_h=STATUS_BAR_VIEW_H
            )

        et = format_status_bar_timecode(st.elapsed_text, remaining=False)
        rt = format_status_bar_timecode(st.remaining_text, remaining=True)
        svc = str(st.service_name or "").strip()
        if svc.lower() in ("", "unknown", "none", "n/a", "na", "--"):
            svc = ""

        sx, sy = _bar_xy(STATUS_BAR_SERVICE_LOCAL)
        _, ey = _bar_xy(STATUS_BAR_ELAPSED_LOCAL)
        _, ry = _bar_xy(STATUS_BAR_REMAINING_LOCAL)
        size = STATUS_BAR_TIME_SIZE_PX

        def _label_patch(label: str) -> tuple[np.ndarray, int, int]:
            if not label:
                return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0
            if label.upper() == "LIVE":
                patch, pw, ph = _text_patch_digital7(label, size_px=size)
            else:
                patch = _matching_hhmm_patch(
                    label, size_px=size, fill_rgb=_COLOR_CHROME_RGB
                )
                ph, pw = patch.shape[:2]
            return patch, int(pw), int(ph)

        def _paste_patch(
            patch: np.ndarray,
            x: float,
            y: float,
            *,
            right: bool = False,
            opacity: float = 1.0,
        ) -> None:
            if opacity <= 0.01:
                return
            if opacity < 0.999:
                patch = _fade_bgra(patch, opacity)
            if patch.size == 0 or int(patch[:, :, 3].max()) < 8:
                return
            ph, pw = patch.shape[:2]
            paste_x = int(round(x - pw)) if right else int(round(x))
            paste_y = int(round(y - ph))
            _paste_patch_bgra(out, patch, paste_x, paste_y)

        et_patch, et_w, _et_h = _label_patch(et)
        rt_patch, rt_w, _rt_h = _label_patch(rt)
        svc_patch, svc_w, _svc_h = _label_patch(svc.lower() if svc else "")
        remaining_left = float(tx + tw) - float(rt_w) if rt_w > 0 else None
        travel_x = status_bar_elapsed_travel_x(
            track_x=float(tx),
            track_w=float(tw),
            progress=pf,
            elapsed_w=float(et_w),
            remaining_left_x=remaining_left,
        )
        if svc:
            ready = status_bar_service_has_room(
                service_x=float(sx),
                service_w=float(svc_w),
                elapsed_x=float(travel_x),
            )
            want = 1.0 if ready else 0.0
            self._bar_handoff_want = want
            if not self._bar_handoff_inited:
                self._bar_handoff = want
                self._bar_handoff_inited = True
            parked_a, svc_a, travel_a = status_bar_handoff_alphas(self._bar_handoff)
            if parked_a > 0.01:
                _paste_patch(et_patch, sx, ey, opacity=parked_a)
            if svc_a > 0.01:
                _paste_patch(svc_patch, sx, sy, opacity=svc_a)
            if travel_a > 0.01:
                _paste_patch(et_patch, travel_x, ey, opacity=travel_a)
        else:
            self._bar_handoff_want = 0.0
            if not self._bar_handoff_inited:
                self._bar_handoff = 0.0
                self._bar_handoff_inited = True
            ex = status_bar_elapsed_left_x(
                track_x=float(tx),
                track_w=float(tw),
                progress=pf,
                elapsed_w=float(et_w),
                park_x=float(sx),
                remaining_left_x=remaining_left,
            )
            _paste_patch(et_patch, ex, ey)
        # Remaining is authored near the right; right-align to the track end.
        _paste_patch(rt_patch, float(tx + tw), ry, right=True)

        if st.paused:
            px, py = _bar_xy(STATUS_BAR_PAUSED_LOCAL)
            font = _load_sharp_medium_italic(STATUS_BAR_PAUSED_SIZE_PX)
            paused_patch, _, _ = _text_patch_font(
                "paused", font=font, fill_rgb=(255, 255, 255)
            )
            _paste_baseline_left(
                out,
                paused_patch,
                px,
                py,
                bbox_top=_font_bbox_top("paused", font),
            )

    def _draw_play_overlay(self, out: np.ndarray) -> None:
        if not self._state.paused:
            return
        poster_zone = self._poster_zone()
        if poster_zone is None:
            return
        px, py, pw, ph, prx = _poster_geometry(
            self.content_mode, zone=int(poster_zone)
        )
        if int(poster_zone) in (6, 7):
            mask = _rounded_rect_mask(pw, ph, prx)
            dim = np.zeros((ph, pw, 4), dtype=np.uint8)
            dim[:, :, 3] = (mask.astype(np.float32) * 127.0).astype(np.uint8)
            _paste_patch_bgra(out, dim, px, py)
            dest_h = max(1, int(ph))
            dest_w = max(
                1,
                int(round(dest_h * float(NOW_PLAYING_ZONES[1].w) / float(NOW_PLAYING_ZONES[1].h))),
            )
            try:
                patch = _rasterize_named_widget(
                    assets_dir=self._assets_dir,
                    widget_key="play",
                    dest_w=dest_w,
                    dest_h=dest_h,
                    now=self._clock_now_for_display(),
                    theme=np_theme_from_settings(),
                    include_play_overlay=False,
                )
            except Exception:
                patch = None
            if patch is None or patch.size == 0:
                return
            ox = int(round(px + (pw - dest_w) / 2.0))
            oy = int(round(py + (ph - dest_h) / 2.0))
            _paste_patch_bgra(out, patch, ox, oy)
            return
        zone = _zone_spec(int(poster_zone))
        zx, zy, zw, zh = zone.xywh
        try:
            patch = _rasterize_named_widget(
                assets_dir=self._assets_dir,
                widget_key="play",
                dest_w=zw,
                dest_h=zh,
                now=self._clock_now_for_display(),
                theme=np_theme_from_settings(),
            )
        except Exception:
            patch = None
        if patch is None or patch.size == 0:
            return
        _paste_patch_bgra(out, patch, zx, zy)

    def _draw_clock_digital(self, out: np.ndarray, now: datetime) -> None:
        assignments = self._assignments()
        clock_zone = _zone_for_widget(assignments, "clock")
        if clock_zone is None:
            return
        zone = _zone_spec(int(clock_zone))
        _draw_clock_labels_in_zone(out, zone, now, include_digital_time=True)

    def _draw_clock_date_above(
        self,
        out: np.ndarray,
        *,
        cx: float,
        cy: float,
        now: datetime,
    ) -> None:
        """Date now lives in ``_draw_clock_digital`` (day + month_date layers)."""
        del out, cx, cy, now
        return

    def _draw_zone0_date(
        self,
        out: np.ndarray,
        now: datetime,
        *,
        align: str | None = None,
    ) -> None:
        """Deprecated zone0 header path — no-op (date draws with the clock)."""
        return

    def _draw_audio_sep_line(
        self,
        out: np.ndarray,
        *,
        cx: float = _ZONE3_CX,
        cy: float = _ZONE1_CY,
    ) -> None:
        """Deprecated separator between in-ring volume/config — no-op."""
        return

    def _draw_audio_level_labels(self, out: np.ndarray) -> None:
        """Paint sl/l/c/r/sr under each meter column in Digital-7."""
        assignments = self._assignments()
        for zone, cols in _AUDIO_LEVEL_LABELS_BY_ZONE.items():
            if assignments[zone - 1] != "audio_levels":
                continue
            for label, cx in cols:
                patch, tw, th = _text_patch_digital7(
                    label,
                    size_px=_AUDIO_LEVEL_LABEL_SIZE,
                )
                if tw < 1 or th < 1:
                    continue
                # SVG baseline → patch is tight ink; sit on the same baseline band.
                _paste_centered(
                    out,
                    patch,
                    cx,
                    float(_AUDIO_LEVEL_LABEL_BASELINE_Y) - th * 0.35,
                )

    def _draw_audio_group(
        self,
        out: np.ndarray,
        *,
        cx: float = _ZONE3_CX,
        cy: float = _ZONE1_CY,
        zone: int | None = None,
    ) -> None:
        """Volume value, dB suffix, incoming source, and outgoing format."""
        del cx, cy
        assignments = self._assignments()
        vol_zone = int(zone) if zone is not None else _zone_for_widget(assignments, "volume")
        if vol_zone is None:
            return
        z = _zone_spec(vol_zone)
        st = self._state
        vol_line = _receiver_volume_display_line(st.volume)
        is_db = bool(re.search(r"dB", vol_line, flags=re.I))
        vol_value = vol_line
        if is_db:
            vol_value = re.sub(r"\s*dB\s*$", "", vol_line, flags=re.I).strip()
        muted = vol_line.strip().lower() in ("mute", "muted", "off") or st.volume_muted
        fmt = str(_receiver_audio_display_line(st.config) or "").strip()
        src = str(_receiver_audio_display_line(st.incoming) or "").strip()

        def _local(xy: tuple[float, float]) -> tuple[float, float]:
            return design_xy_from_local(
                z, xy[0], xy[1], view_w=VOLUME_VIEW_W, view_h=VOLUME_VIEW_H
            )

        if fmt:
            fx, fy = _local(VOLUME_FORMAT_LOCAL)
            font = _load_sharp_extrabold(VOLUME_FORMAT_SIZE_PX)
            patch, _w, _h = _text_patch_font(
                fmt.upper(), font=font, fill_rgb=(255, 255, 255)
            )
            _paste_baseline_left(
                out, patch, fx, fy, bbox_top=_font_bbox_top(fmt.upper(), font)
            )
        if src:
            sx, sy = _local(VOLUME_SOURCE_LOCAL)
            patch, _w, _h = _text_patch_digital7(
                src.upper(),
                size_px=VOLUME_SOURCE_SIZE_PX,
                max_width_px=int(round(z.w - 40)),
                fill_rgb=(255, 255, 255),
            )
            font_src = _load_digital7(VOLUME_SOURCE_SIZE_PX)
            _paste_baseline_left(
                out, patch, sx, sy, bbox_top=_font_bbox_top(src.upper(), font_src)
            )
        show_value = bool(vol_value) and not muted
        show_scale = bool(is_db) and not muted
        vol_p = None
        vol_h = 0
        db_p = None
        db_h = 0
        if show_value:
            vol_p, _, vol_h = _text_patch_digital7(
                vol_value, size_px=VOLUME_VALUE_SIZE_PX
            )
        if show_scale:
            db_p, _, db_h = _text_patch_digital7(
                "dB", size_px=VOLUME_SCALE_SIZE_PX, fill_rgb=_COLOR_CHROME_RGB
            )
        dy_local = volume_readout_y_shift(
            has_source=bool(src),
            value_h=float(vol_h) if show_value else 0.0,
            scale_h=float(db_h) if show_scale else 0.0,
        )
        if show_value and vol_p is not None:
            vx, vy = _local((VOLUME_VALUE_LOCAL[0], VOLUME_VALUE_LOCAL[1] + dy_local))
            font_vol = _load_digital7(VOLUME_VALUE_SIZE_PX)
            _paste_baseline_left(
                out, vol_p, vx, vy, bbox_top=_font_bbox_top(vol_value, font_vol)
            )
        if show_scale and db_p is not None:
            dx, dy = _local((VOLUME_SCALE_LOCAL[0], VOLUME_SCALE_LOCAL[1] + dy_local))
            font_db = _load_digital7(VOLUME_SCALE_SIZE_PX)
            _paste_baseline_left(
                out, db_p, dx, dy, bbox_top=_font_bbox_top("dB", font_db)
            )

    def _draw_circular_now_playing(
        self,
        out: np.ndarray,
        *,
        cx: float,
        cy: float,
        now: datetime | None = None,
    ) -> None:
        """Volume-ring chrome: red = watched; centered readout is time of day.

        No audio-config line (unlike the volume widget).
        """
        st = self._state
        pf = max(0.0, min(1.0, float(st.progress)))
        _draw_circle_pair(
            out,
            cx=cx,
            cy=cy,
            fraction=pf,
            show_accent=pf > 1e-6,
            theme=np_theme_from_settings(),
        )
        time_p, _, _ = _text_patch_digital7(
            _clock_hhmm(now),
            size_px=_CLOCK_DIGITAL_SIZE,
        )
        _paste_centered(out, time_p, cx, cy)

    def _draw_cast(self, out: np.ndarray, *, cast_zone: int = 4) -> None:
        assignments = self._assignments()
        z_idx = int(cast_zone)
        if not (1 <= z_idx <= 5) or assignments[z_idx - 1] != "cast_info":
            return
        start = 0
        for z in range(1, z_idx):
            if assignments[z - 1] == "cast_info":
                start += cast_names_for_zone(z)
        n_names = cast_names_for_zone(z_idx)
        cast = list(self._state.cast or [])[start : start + n_names]
        zone = _zone_spec(z_idx)
        if z_idx in (4, 5):
            mid_y = float(zone.y) + float(zone.h) * 0.5
            for i, (x0, col_w) in enumerate(strip_cast_columns(zone)):
                actor, character = cast[i] if i < len(cast) else ("", "")
                _draw_stacked_cast_pair(
                    out,
                    actor=actor,
                    character=character,
                    cx=float(x0) + float(col_w) * 0.5,
                    max_width_px=max(48, int(col_w) - 24),
                    actor_px=CAST_ACTOR_SIZE_PX,
                    char_px=CAST_CHAR_SIZE_PX,
                    gap_px=CAST_STRIP_STACK_GAP_PX,
                    mid_y=mid_y,
                )
            return
        cx = float(zone.x) + float(zone.w) * 0.5
        max_w = max(80, int(round(zone.w - 32)))
        for i, (_actor_x, actor_y, char_y) in enumerate(CAST_LOCAL_ROWS):
            actor, character = cast[i] if i < len(cast) else ("", "")
            _ax, ay = design_xy_from_local(
                zone, 0.0, actor_y, view_w=CAST_VIEW_W, view_h=CAST_VIEW_H
            )
            _cx, cy = design_xy_from_local(
                zone, 0.0, char_y, view_w=CAST_VIEW_W, view_h=CAST_VIEW_H
            )
            del _ax, _cx
            ap = _cast_line_patch(
                actor, size_px=CAST_ACTOR_SIZE_PX, max_width_px=max_w
            )
            cp = _cast_line_patch(
                character, size_px=CAST_CHAR_SIZE_PX, max_width_px=max_w
            )
            # SVG y is the typographic baseline; ink-cropped patches sit on it.
            if ap is not None:
                _paste_ink_centered(out, ap, cx, ay - ap.shape[0])
            if cp is not None:
                _paste_ink_centered(out, cp, cx, cy - cp.shape[0])

    def _draw_track_titles(self, out: np.ndarray) -> None:
        st = self._state
        for text, cy, size in (
            (st.song_title, _SONG_CY, _SONG_SIZE),
            (st.album_title, _ALBUM_CY, _ALBUM_SIZE),
            (st.artist_title, _ARTIST_CY, _ARTIST_SIZE),
        ):
            label = str(text or "").strip()
            if not label:
                continue
            patch, _, _ = _text_patch_digital7(
                label.upper(),
                size_px=size,
                max_width_px=_TRACK_MAX_W,
            )
            _paste_centered(out, patch, _TRACK_CX, cy)

    def _render_static_bgra(self) -> np.ndarray:
        now = self._clock_now_for_display()
        assignments = self._assignments()
        if _layout_is_fullscreen_clock(assignments):
            out = _fallback_base_bgra()
            clock = render_centered_clock_widget_bgra(
                assets_dir=self._assets_dir,
                now=now,
            )
            _paste_patch_bgra(out, clock, 0, 0)
            return out
        out = _fallback_base_bgra()
        theme = np_theme_from_settings()
        if (
            self._state.content_active
            and self._poster_bgra is not None
            and self._poster_bgra.size > 0
            and not self._state.searching
        ):
            blur = self._ensure_artwork_blur_bgra()
            if blur is not None:
                _paste_patch_bgra(out, blur, 0, 0)
        # Soft white halos behind active circular widgets (under poster + SVG chrome).
        _draw_zone_halos(
            out,
            content_mode=self.content_mode,
            paused=bool(self._state.paused),
            zone_widgets=assignments,
        )
        # Poster/album under play overlay; SVG chrome sits in sibling zones.
        self._draw_poster(out)
        self._draw_play_overlay(out)
        _paste_patch_bgra(out, self._render_svg_base(now), 0, 0)
        vol_zone = _zone_for_widget(assignments, "volume")
        if vol_zone is not None:
            vol_frac = self._volume_fraction_for_display()
            vcx, vcy = _zone_volume_center(int(vol_zone))
            _draw_volume_selected_pie(
                out, cx=vcx, cy=vcy, fraction=vol_frac, theme=theme
            )
            self._draw_audio_group(out, zone=int(vol_zone))
        self._draw_clock_digital(out, now)
        self._draw_audio_level_labels(out)
        if any(is_status_bar_widget(assignments[i], i + 1) for i in range(5)):
            self._draw_status_bar(out)
        if self.content_mode != _CONTENT_MODE_MUSIC:
            for z in (1, 2, 3, 4, 5):
                if assignments[z - 1] == "cast_info":
                    self._draw_cast(out, cast_zone=z)
        return out

    def overlay_status_bar(self, canvas_bgr: np.ndarray) -> bool:
        """Composite the NP status bar onto an existing BGR canvas (settings_main)."""
        if canvas_bgr is None or canvas_bgr.size == 0 or canvas_bgr.ndim < 3:
            return False
        bar_zone = configured_status_bar_zone()
        if bar_zone is None:
            return False
        self._advance_status_bar_handoff()
        zone = _zone_spec(int(bar_zone))
        zx, zy, zw, zh = (int(v) for v in zone.xywh)
        ch, cw = int(canvas_bgr.shape[0]), int(canvas_bgr.shape[1])
        y0 = max(0, zy)
        x0 = max(0, zx)
        y1 = min(ch, zy + zh)
        x1 = min(cw, zx + zw)
        if y1 <= y0 or x1 <= x0:
            return False
        dh, dw = int(DESIGN_H), int(DESIGN_W)
        layer = self._bar_overlay_layer
        if layer is None or layer.shape[0] != dh or layer.shape[1] != dw:
            layer = np.zeros((dh, dw, 4), dtype=np.uint8)
            self._bar_overlay_layer = layer
        else:
            layer[y0:y1, x0:x1] = 0
        self._draw_status_bar(layer, zone=int(bar_zone))
        bar = layer[y0:y1, x0:x1]
        if int(bar[:, :, 3].max()) < 8:
            return False
        roi = canvas_bgr[y0:y1, x0:x1]
        roi[:] = alpha_blend_bgra_over_bgr(roi, bar)
        return True

    def bgra_frame(self) -> np.ndarray | None:
        if not self._state.chrome_visible:
            return None
        sig = self._cache_sig()
        if self._cached_bgra is None or self._cached_sig != sig:
            self._cached_bgra = self._render_static_bgra()
            self._cached_sig = sig
        return self._cached_bgra

    def render(self, canvas_bgr: np.ndarray) -> None:
        if canvas_bgr is None or canvas_bgr.size == 0:
            return
        self.tick()
        if not self._state.chrome_visible:
            canvas_bgr[:] = (0, 0, 0)
            return
        frame = self.bgra_frame()
        if frame is None:
            return
        if frame.shape[2] >= 4:
            canvas_bgr[:] = alpha_blend_bgra_over_bgr(canvas_bgr, frame)
        else:
            h = min(canvas_bgr.shape[0], frame.shape[0])
            w = min(canvas_bgr.shape[1], frame.shape[1])
            canvas_bgr[:h, :w] = frame[:h, :w, :3]
