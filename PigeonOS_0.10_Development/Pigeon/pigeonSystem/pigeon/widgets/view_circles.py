"""
PigeonOS 0.10 zoned now-playing skin (800×480).

Static chrome is rasterized from ``pigeonAssets/pigeon_now_playing.svg``.
Zone widget groups are shown/hidden per defaults + content mode + pause state
before rasterize. Dynamic layers (cast, clock digital, volume pie/text, progress
bar, poster/album art) are drawn on top with Pillow / OpenCV.
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
from pigeon.font_paths import (
    resolve_digital7_font,
    resolve_ui_font_extrabold,
    resolve_ui_font_extrabold_italic,
    resolve_ui_font_light_italic,
    resolve_ui_font_semibold,
)
from pigeon.widgets.playback_overlay import (
    _receiver_volume_display_line,
    receiver_audio_config_display_line,
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

_SVG_W = 800.0
_SVG_H = 480.0

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

    ui_hex: str = "#ff0013"
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
            ui_hex=hex_for_color_key("ui", keys.get("ui", "red")),
            accent_hex=hex_for_color_key("accent", keys.get("accent", "white")),
            button_hex=hex_for_color_key("button", keys.get("button", "black")),
        )
    except Exception:
        return _NpTheme()

# Zone centers (design coords).
_ZONE1_CX, _ZONE1_CY = 152.0, 192.0
_ZONE2_CX, _ZONE2_CY = 401.0, 192.0
_ZONE3_CX, _ZONE3_CY = 650.0, 192.0

_RING_OUTER_R = 114.26
_RING_INNER_R = 93.54

# Zone2 poster 2×3 / album 1×1.
# SVG ``poster_accent-2`` path bbox ≈ 200×300 @ (300.7, 14.5); placed demo image is
# 780×1170 × 0.26 (=202.8×304.2) @ translate(300.52, 14.47). Overfill slightly so
# cover-fit art seats under the accent stroke without a gap.
_POSTER_VIDEO_X = 299
_POSTER_VIDEO_Y = 12
_POSTER_VIDEO_W = 204
_POSTER_VIDEO_H = 306
_POSTER_VIDEO_RX = 10
_CLOCK_EXTERIOR_ACCENT_R = 116.72
_CLOCK_MIDDLE_ACCENT_R = 96.46
# Dimmed minute/second ticks: red mixed with 50% black → dark red; current stays full red.
_TICK_DIM_FILL = "#800000"
_TICK_ACTIVE_FILL = "red"

# SVG ``zone2_album_art_1x1`` is 198.65² @ (303.04, 93.1).
_POSTER_MUSIC_X = 301
_POSTER_MUSIC_Y = 91
_POSTER_MUSIC_W = 203
_POSTER_MUSIC_H = 203
_POSTER_MUSIC_RX = 10

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
_VOLUME_SIZE_PX = 72
_AUDIO_CFG_SIZE_PX = 42
_CLOCK_DIGITAL_SIZE = 42
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
    """Single zoned SVG for video and music (zone2 poster vs album toggled)."""
    del content_mode  # one asset for both modes
    env = (
        os.environ.get("PIGEON_VIEW_CIRCLES_SVG", "").strip()
        or os.environ.get("PIGEON_NOW_PLAYING_SVG", "").strip()
    )
    filename = "pigeon_now_playing.svg"
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / filename
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / filename


def _poster_geometry(
    content_mode: str,
    *,
    zone: int = 2,
) -> tuple[int, int, int, int, int]:
    if _normalize_content_mode(content_mode) == _CONTENT_MODE_MUSIC:
        px, py, pw, ph, prx = (
            _POSTER_MUSIC_X,
            _POSTER_MUSIC_Y,
            _POSTER_MUSIC_W,
            _POSTER_MUSIC_H,
            _POSTER_MUSIC_RX,
        )
    else:
        px, py, pw, ph, prx = (
            _POSTER_VIDEO_X,
            _POSTER_VIDEO_Y,
            _POSTER_VIDEO_W,
            _POSTER_VIDEO_H,
            _POSTER_VIDEO_RX,
        )
    z = int(zone)
    if z == 2:
        return px, py, pw, ph, prx
    # Shift art so its center tracks the target zone center (defaults are zone2).
    base_cx, base_cy = _ZONE2_CX, _ZONE2_CY
    cx, cy = _zone_clock_center(z)
    return (
        int(round(px + (cx - base_cx))),
        int(round(py + (cy - base_cy))),
        pw,
        ph,
        prx,
    )


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
        template.set("viewBox", f"0 0 {int(_SVG_W)} {int(_SVG_H)}")
        template.set("width", str(int(_SVG_W)))
        template.set("height", str(int(_SVG_H)))
        while len(_SVG_TEMPLATE_CACHE) >= 4:  # a couple of mode variants at most
            _SVG_TEMPLATE_CACHE.pop(next(iter(_SVG_TEMPLATE_CACHE)))
        _SVG_TEMPLATE_CACHE[key] = template
    # Callers mutate the tree (strip layers, set text), so hand out a copy.
    return copy.deepcopy(template)


def _scale_raster_to_design(bgra: np.ndarray, src_w: int, src_h: int) -> np.ndarray:
    if bgra.shape[0] != src_h or bgra.shape[1] != src_w:
        bgra = cv2.resize(bgra, (src_w, src_h), interpolation=cv2.INTER_AREA)
    return cv2.resize(bgra, (int(DESIGN_W), int(DESIGN_H)), interpolation=cv2.INTER_AREA)


def _rasterize_svg_tree(root: ET.Element) -> np.ndarray:
    svg_bytes = ET.tostring(root, encoding="utf-8")
    src_w, src_h = int(_SVG_W), int(_SVG_H)
    last_err: Exception | None = None

    try:
        import fitz  # PyMuPDF

        doc = fitz.open(stream=svg_bytes, filetype="svg")
        page = doc[0]
        pix = page.get_pixmap(
            matrix=fitz.Matrix(src_w / page.rect.width, src_h / page.rect.height),
            alpha=True,
        )
        rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            bgra = cv2.cvtColor(rgb, cv2.COLOR_RGBA2BGRA)
        else:
            bgra = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGRA)
        return _scale_raster_to_design(bgra, src_w, src_h)
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
        return _scale_raster_to_design(bgra, src_w, src_h)
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
        return ("clock", "poster", "volume", "cast_info", "now_playing")


_CAST_NAMES_PER_ZONE = 3


def _effective_zone_widgets(
    *,
    has_position: bool,
    cast_count: int = 0,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> tuple[str, str, str, str, str]:
    """Show only widgets we have content for; never two copies of the same one.

    Status bar needs a live position. Without it, that zone can show the next
    unused cast names. A second cast strip is kept only when more names remain.
    Any other repeated widget is dropped so the layout does not duplicate.
    """
    zones = list(zone_widgets or _default_zone_widget_assignments())
    if len(zones) < 5:
        return _default_zone_widget_assignments()
    named = max(0, int(cast_count))
    if not has_position:
        for i, widget in enumerate(zones):
            if widget == "now_playing":
                zones[i] = "cast_info"
    seen: set[str] = set()
    cast_used = 0
    for i, widget in enumerate(zones):
        key = str(widget or "").strip()
        if not key:
            zones[i] = ""
            continue
        if key == "cast_info":
            remaining = named - cast_used
            if remaining <= 0:
                zones[i] = ""
                continue
            cast_used += _CAST_NAMES_PER_ZONE
            continue
        if key in seen:
            zones[i] = ""
            continue
        seen.add(key)
    return (zones[0], zones[1], zones[2], zones[3], zones[4])


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
        "zone5_now_playing_group": _is(5, "now_playing"),
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
    if zone == 2:
        return _ZONE2_CX, _ZONE2_CY
    if zone == 3:
        return _ZONE3_CX, _ZONE3_CY
    return _ZONE1_CX, _ZONE1_CY


def _local_tag(tag: str) -> str:
    return tag.split("}")[-1] if "}" in tag else tag


def _set_tick_paint(el: ET.Element, *, color: str) -> None:
    """Recolor path fills/strokes under ``el`` (solid color, full opacity)."""
    if "opacity" in el.attrib:
        del el.attrib["opacity"]
    for node in el.iter():
        tag = _local_tag(node.tag)
        if tag not in ("path", "polygon", "polyline", "circle", "ellipse", "rect"):
            continue
        if "opacity" in node.attrib:
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


def _apply_exterior_seconds_fill(
    clock: ET.Element,
    zone: int,
    *,
    sec_idx: int,
    theme: _NpTheme | None = None,
) -> None:
    """Paint exterior button color; accent only under seconds layers 1..``sec_idx``.

    Black middle disc sits above the accent wedge so the visible accent is the
    outer ring under the active second ticks.
    """
    th = theme or np_theme_from_settings()
    exterior = _find_by_key(clock, f"zone{zone}_clock_exterior_accent")
    middle = _find_by_key(clock, f"zone{zone}_clock_middle_accent")
    fill_name = f"zone{zone}_clock_exterior_seconds_fill"
    # Drop any prior wedge from a reused tree.
    _remove_by_key(clock, fill_name)
    if exterior is None:
        return
    try:
        cx = float(exterior.get("cx") or _zone_clock_center(zone)[0])
        cy = float(exterior.get("cy") or _zone_clock_center(zone)[1])
        r = float(exterior.get("r") or _CLOCK_EXTERIOR_ACCENT_R)
    except (TypeError, ValueError):
        cx, cy = _zone_clock_center(zone)
        r = _CLOCK_EXTERIOR_ACCENT_R
    idx = max(1, min(60, int(sec_idx)))
    frac = idx / 60.0
    exterior.set("fill", th.button_hex)
    d = _seconds_wedge_path_d(cx, cy, r, frac)
    if d is None:
        # Full minute (:00 / layer 60) — entire exterior turns accent.
        exterior.set("fill", th.accent_hex)
        _place_in_group(clock, exterior, 0)
        _place_in_group(clock, middle, 1)
        return
    if not d:
        _place_in_group(clock, exterior, 0)
        _place_in_group(clock, middle, 1)
        return
    wedge = ET.Element(f"{{{SVG_NS}}}path")
    wedge.set("id", fill_name)
    wedge.set("data-name", fill_name)
    wedge.set("d", d)
    wedge.set("fill", th.accent_hex)
    clock.append(wedge)
    # Exterior (button) → accent seconds wedge → middle (black).
    _place_in_group(clock, exterior, 0)
    _place_in_group(clock, wedge, 1)
    _place_in_group(clock, middle, 2)


def _apply_clock_accent_fills(root: ET.Element, *, theme: _NpTheme | None = None) -> None:
    """Clock accent stack (bottom → top): exterior button, then black middle disc.

    Exterior starts as button color; an accent seconds wedge is layered above it
    for the active clock (see ``_apply_exterior_seconds_fill``). Middle is always
    pure black so the face reads as a solid center above the ring.
    """
    th = theme or np_theme_from_settings()
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
        exterior = _find_by_key(clock, f"zone{zone}_clock_exterior_accent")
        middle = _find_by_key(clock, f"zone{zone}_clock_middle_accent")
        interior = _find_by_key(clock, f"zone{zone}_clock_interior_accent")
        if exterior is not None:
            exterior.set("fill", th.button_hex)
        if middle is not None:
            middle.set("fill", _COLOR_CENTER_BLACK_HEX)
        # Interior must not be white-filled — leave stroke-only / none.
        if interior is not None and str(interior.get("fill") or "").strip().upper() in (
            "#FFFFFF",
            "#FFF",
            "WHITE",
        ):
            interior.set("fill", "none")
        # Bottom of widget: exterior, then middle immediately above it.
        _place_in_group(clock, exterior, 0)
        _place_in_group(clock, middle, 1)


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
            _set_tick_paint(el, color=th.tick_active)
        else:
            _set_tick_paint(el, color=th.tick_dim)
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
        _apply_clock_ticks(root, clock_zone, dt, theme=th)
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
    mode = _normalize_content_mode(content_mode)
    th = theme or np_theme_from_settings()
    if svg_path is not None:
        path = Path(svg_path)
    else:
        path = default_view_circles_svg_path(assets_dir, content_mode=mode)
    if not path.is_file():
        raise FileNotFoundError(f"now-playing SVG not found: {path}")
    root = _svg_tree_from_path(path)
    apply_view_circles_svg_state(
        root,
        content_mode=mode,
        paused=paused,
        now=now,
        active_clock_zone=1,
        theme=th,
        zone_widgets=zone_widgets,
    )
    bgra = _rasterize_svg_tree(root)
    bgra = _decanvas_white_bgra(bgra)
    return bgra


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
def _zone_circle_halo_patch(
    r: float = _ZONE_HALO_R,
    sigma: float = _ZONE_HALO_BLUR_SIGMA,
    opacity: float = _ZONE_HALO_OPACITY,
) -> np.ndarray:
    """White disc, Gaussian-blurred, at ``opacity`` (BGRA)."""
    radius = max(1, int(round(float(r))))
    pad = _halo_pad(sigma)
    size = radius * 2 + pad * 2
    mask = np.zeros((size, size), dtype=np.float32)
    cv2.circle(mask, (size // 2, size // 2), radius, 1.0, -1, lineType=cv2.LINE_AA)
    return _ui_halo_from_mask(mask, sigma=sigma, opacity=opacity)


def _draw_zone_halos(
    bgra: np.ndarray,
    *,
    content_mode: str,
    paused: bool = False,
    zone_widgets: tuple[str, str, str, str, str] | None = None,
) -> None:
    """Gentle white glow behind active clock widgets (not volume)."""
    vis = _zone_widget_visibility(
        content_mode=content_mode, paused=paused, zone_widgets=zone_widgets
    )
    circle = _zone_circle_halo_patch()
    ch, cw = circle.shape[:2]
    seen: set[tuple[float, float]] = set()
    for key, cx, cy in _ZONE_CIRCLE_HALO_KEYS:
        if not vis.get(key, False):
            continue
        pt = (cx, cy)
        if pt in seen:
            continue
        seen.add(pt)
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
        self._artwork_blur_bgra: np.ndarray | None = None
        self._artwork_blur_poster_id: int | None = None
        self._search_frames: tuple[np.ndarray, ...] | None = None
        self._search_frames_tried = False
        self._last_tick_mono: float | None = None
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
    ) -> bool:
        changed = False
        if self.set_now_playing_chrome_visible(has_now_playing):
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
                norm = [(str(a or ""), str(c or "")) for a, c in cast[:9]]
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

    def tick(self) -> None:
        if not self._state.searching:
            self._last_tick_mono = None
            return
        now = time.monotonic()
        if self._last_tick_mono is None:
            self._last_tick_mono = now
            return
        dt = max(0.0, now - self._last_tick_mono)
        self._last_tick_mono = now
        prev_angle = self._state.search_angle_deg
        self._state.search_angle_deg = advance_angle_deg(prev_angle, dt)
        # Intro racing clock/volume removed: only the search spinner animates.
        spun = int(round(prev_angle / 10.0)) != int(
            round(self._state.search_angle_deg / 10.0)
        )
        if spun:
            self.clear_cache()

    def _ensure_search_frames(self) -> tuple[np.ndarray, ...] | None:
        if self._search_frames is not None:
            return self._search_frames
        if self._search_frames_tried:
            return None
        self._search_frames_tried = True
        self._search_frames = build_search_spinner_frames(self._assets_dir)
        return self._search_frames

    def _assignments(self) -> tuple[str, str, str, str, str]:
        named = sum(1 for actor, _role in (self._state.cast or []) if str(actor or "").strip())
        return _effective_zone_widgets(
            has_position=bool(self._state.has_position),
            cast_count=named,
        )

    def _cache_sig(self) -> tuple[object, ...]:
        st = self._state
        cast_sig = tuple(st.cast[:9])
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
            31,  # cache schema — zone adapt / no duplicate widgets
            st.content_mode,
            st.has_position,
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
        )

    def _svg_chrome_cache_key(self, now: datetime) -> tuple[object, ...]:
        mode = self.content_mode
        path = default_view_circles_svg_path(self._assets_dir, content_mode=mode)
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            mtime = -1
        h12 = now.hour % 12
        if h12 == 0:
            h12 = 12
        zone_widgets = self._assignments()
        # Reuse rasters across hours/days — ticks only depend on h/m/s + mode/pause.
        return (
            str(path),
            mtime,
            mode,
            bool(self._state.paused),
            h12,
            int(now.minute),
            int(now.second),
            10,  # chrome pipeline — settings theme colors
            zone_widgets,
            np_theme_from_settings().cache_key,
        )

    def _render_svg_base(self, now: datetime) -> np.ndarray:
        key = self._svg_chrome_cache_key(now)
        cached = self._svg_chrome_by_key.get(key)
        if cached is not None:
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
        # Keep a full minute of second-states (and a bit more) so the Pi doesn't
        # re-rasterize every tick — slow rasters were causing the second hand to skip.
        # Evict oldest instead of wiping: a wipe forced a full minute of re-rasters.
        while len(self._svg_chrome_by_key) > 96:
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
        assignments = self._assignments()
        poster_zone = _zone_for_widget(assignments, "poster")
        if poster_zone is None:
            return
        px, py, pw, ph, prx = _poster_geometry(
            self.content_mode, zone=int(poster_zone)
        )
        dim = _POSTER_PAUSED_DIM if self._state.paused else 1.0
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
                if dim < 0.999:
                    patch = patch.copy()
                    patch[:, :, :3] = np.clip(
                        patch[:, :, :3].astype(np.float32) * dim, 0, 255
                    ).astype(np.uint8)
                    patch[:, :, 3] = np.clip(
                        patch[:, :, 3].astype(np.float32) * max(dim, 0.5), 0, 255
                    ).astype(np.uint8)
                _paste_patch_bgra(out, patch, px, py)
        elif (
            self._state.missing_art
            and not self._state.searching
            and self._state.content_mode != _CONTENT_MODE_MUSIC
        ):
            self._draw_missing_art_placeholder(out, px, py, pw, ph, prx)
        if self._state.searching:
            frames = self._ensure_search_frames()
            if frames:
                cx = int(round(px + pw / 2.0))
                cy = int(round(py + ph / 2.0))
                patch = rotated_patch_for_angle(frames, self._state.search_angle_deg)
                blit_spinner_patch(out, patch, cx=cx, cy=cy)

    def _draw_status_bar(self, out: np.ndarray) -> None:
        st = self._state
        pf = max(0.0, min(1.0, float(st.progress)))
        elapsed_w = max(_MIN_ELAPSED_W, int(round(pf * float(_BAR_W))))
        if pf <= 0.0:
            elapsed_w = _MIN_ELAPSED_W if st.elapsed_text else 0
        elapsed_w = min(elapsed_w, _BAR_W) if elapsed_w > 0 else 0
        under = None
        bar_pad = 3
        bar_x = _BAR_L - bar_pad
        bar_y = _BAR_T - bar_pad
        if elapsed_w > 0:
            ew = elapsed_w + bar_pad * 2
            eh = _BAR_H + bar_pad * 2
            x0, y0 = max(0, bar_x), max(0, bar_y)
            x1 = min(int(DESIGN_W), bar_x + ew)
            y1 = min(int(DESIGN_H), bar_y + eh)
            if x0 < x1 and y0 < y1:
                under = np.zeros((eh, ew, out.shape[2]), dtype=out.dtype)
                under[y0 - bar_y : y1 - bar_y, x0 - bar_x : x1 - bar_x] = out[y0:y1, x0:x1]
        _draw_rounded_bar_bgra(
            out,
            x=_BAR_L,
            y=_BAR_T,
            w=_BAR_W,
            h=_BAR_H,
            fill_bgr=_COLOR_UNPLAYED_BGR,
            radius=_BAR_RX,
            stroke_bgr=_COLOR_CHROME_BGR,
            stroke=_ACCENT_STROKE_PX,
            fill_opacity=_CHROME_FILL_OPACITY,
            stroke_opacity=_ACCENT_STROKE_OPACITY,
        )
        if elapsed_w > 0:
            if under is not None:
                lw, lh = elapsed_w, _BAR_H
                mw, mh = lw + bar_pad * 2, lh + bar_pad * 2
                mask = np.zeros((mh, mw), dtype=np.uint8)
                inner = _rounded_rect_mask(lw, lh, min(_BAR_RX, lw // 2, lh // 2))
                mask[bar_pad : bar_pad + lh, bar_pad : bar_pad + lw] = inner
                _restore_masked_region(out, under, mask, x=bar_x, y=bar_y)
            _draw_rounded_bar_bgra(
                out,
                x=_BAR_L,
                y=_BAR_T,
                w=elapsed_w,
                h=_BAR_H,
                fill_bgr=np_theme_from_settings().ui_bgr,
                radius=_BAR_RX,
                stroke_bgr=_COLOR_CHROME_BGR,
                stroke=_ACCENT_STROKE_PX,
                fill_opacity=_ACCENT_OPACITY,
                stroke_opacity=_ACCENT_STROKE_OPACITY,
                stroke_backdrop=under,
                stroke_backdrop_x=bar_x,
                stroke_backdrop_y=bar_y,
            )
        cti_x = _BAR_L + min(elapsed_w, _BAR_W) - _CTI_W // 2
        cti_x = max(_BAR_L, min(_BAR_R - _CTI_W, cti_x))
        cti = np.zeros((_CTI_H, _CTI_W, 4), dtype=np.uint8)
        cti[:, :, :3] = np_theme_from_settings().ui_bgr
        cti[:, :, 3] = 255
        _paste_patch_bgra(out, cti, cti_x, _CTI_Y)

        et = str(st.elapsed_text or "").strip()
        rt = str(st.remaining_text or "").strip()
        rt_x = _BAR_R
        et_w = 0
        et_x = 0
        if rt:
            rt_patch, rt_w, rt_h = _text_patch_digital7(rt, size_px=24)
            rt_x = _BAR_R - rt_w
            _paste_patch_bgra(
                out,
                rt_patch,
                rt_x,
                _REMAINING_TEXT_Y - rt_h // 2,
            )
        if et:
            et_patch, et_w, et_h = _text_patch_digital7(et, size_px=24)
            et_x = int(round(cti_x + _CTI_W / 2.0 - et_w / 2.0))
            if et_x + et_w + _ELAPSED_REMAINING_GAP_PX < rt_x:
                _paste_patch_bgra(
                    out,
                    et_patch,
                    et_x,
                    _ELAPSED_TEXT_Y - et_h // 2,
                )
            else:
                et_w = 0

        # Service label: hidden near start; fade in when progress leaves room.
        svc = str(st.service_name or "").strip()
        if svc:
            show_svc = pf >= _SERVICE_FADE_PROGRESS
            if et_w > 0 and et_x < _SERVICE_TEXT_X + 80:
                show_svc = False
            if show_svc:
                fade = max(0.0, min(1.0, (pf - _SERVICE_FADE_PROGRESS) / 0.08))
                svc_patch, sw, sh = _text_patch_digital7(svc.lower(), size_px=24)
                if fade < 0.999:
                    svc_patch = svc_patch.copy()
                    svc_patch[:, :, 3] = np.clip(
                        svc_patch[:, :, 3].astype(np.float32) * fade, 0, 255
                    ).astype(np.uint8)
                _paste_patch_bgra(
                    out,
                    svc_patch,
                    _SERVICE_TEXT_X,
                    _SERVICE_TEXT_Y - sh // 2,
                )

        if st.paused:
            font = _load_sharp_italic(28)
            paused_patch, _, _ = _text_patch_font("paused", font=font)
            _paste_centered(out, paused_patch, _PAUSED_TEXT_CX, _PAUSED_TEXT_CY)

    def _draw_clock_digital(self, out: np.ndarray, now: datetime) -> None:
        assignments = self._assignments()
        clock_zone = _zone_for_widget(assignments, "clock")
        if clock_zone is None:
            return
        cx, cy = _zone_clock_center(int(clock_zone))
        self._draw_clock_date_above(out, cx=cx, cy=cy, now=now)
        time_p, _, _ = _text_patch_digital7(_clock_hhmm(now), size_px=_CLOCK_DIGITAL_SIZE)
        _paste_centered(out, time_p, cx, cy)

    def _draw_clock_date_above(
        self,
        out: np.ndarray,
        *,
        cx: float,
        cy: float,
        now: datetime,
    ) -> None:
        """Sharp Sans Semibold date; curved baseline matching the clock exterior."""
        label = _format_zone0_date(now)
        if not label:
            return
        font = _load_sharp_semibold(_CLOCK_DATE_SIZE_PX)
        patch, _pw, _ph = _text_patch_font(
            label,
            font=font,
            fill_rgb=(255, 255, 255),
        )
        _paste_label_above_circle_curved(
            out,
            patch,
            cx,
            widget_cy=float(cy),
            widget_r=float(_CLOCK_EXTERIOR_ACCENT_R),
            gap_px=_WIDGET_LABEL_BASELINE_GAP_PX,
        )

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
    ) -> None:
        """Volume dB centered in the ring; audio config baseline above the exterior."""
        st = self._state
        vol = _receiver_volume_display_line(st.volume)
        show_vol = bool(vol) and vol.strip().lower() not in ("mute", "muted")
        cfg = receiver_audio_config_display_line(st.incoming, st.config)
        if show_vol:
            vol_p, _, _ = _volume_readout_patch(vol, inner_r=_RING_INNER_R)
            _paste_centered(out, vol_p, cx, cy)
        if cfg:
            cfg_label = cfg.upper()
            # Keep config inside the design frame for zone1/zone3 columns.
            max_w = int(
                max(
                    80,
                    min(
                        240,
                        2.0 * min(float(cx), float(DESIGN_W) - float(cx)) - 8.0,
                    ),
                )
            )
            cfg_p, _cw, _ch = _text_patch_digital7(
                cfg_label,
                size_px=_AUDIO_CFG_SIZE_PX,
                max_width_px=max_w,
                fill_rgb=(255, 255, 255),
            )
            _paste_label_above_circle_curved(
                out,
                cfg_p,
                cx,
                widget_cy=float(cy),
                widget_r=float(_RING_OUTER_R),
                gap_px=_WIDGET_LABEL_BASELINE_GAP_PX,
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
        start = 0
        for z in range(1, int(cast_zone)):
            if assignments[z - 1] == "cast_info":
                start += 3
        cast = list(self._state.cast or [])[start : start + 3]
        while len(cast) < 3:
            cast.append(("", ""))
        cols = _CAST_COLS_Z5 if int(cast_zone) == 5 else _CAST_COLS_Z4
        font_actor = _load_digital7(24)
        font_char = _load_digital7(18)
        for i, (center_x, actor_y, char_y) in enumerate(cols):
            actor, character = cast[i] if i < len(cast) else ("", "")
            actor = str(actor or "").strip()
            character = str(character or "").strip()
            # Keep each column inside the frame (zone3 column is near the right edge).
            max_w = int(
                max(
                    80,
                    min(
                        float(_CAST_COL_W),
                        2.0 * min(float(center_x), float(DESIGN_W) - float(center_x)) - 8.0,
                    ),
                )
            )
            if actor:
                label = actor.upper()
                ap, _aw, _ah = _text_patch_digital7(
                    label,
                    size_px=24,
                    max_width_px=max_w,
                )
                _paste_baseline_centered(
                    out,
                    ap,
                    center_x,
                    float(actor_y),
                    bbox_top=_font_bbox_top(label, font_actor),
                    pad=_CAST_TEXT_PAD,
                )
            if character:
                label = character.upper()
                cp, _cw, _ch = _text_patch_digital7(
                    label,
                    size_px=18,
                    max_width_px=max_w,
                )
                _paste_baseline_centered(
                    out,
                    cp,
                    center_x,
                    float(char_y),
                    bbox_top=_font_bbox_top(label, font_char),
                    pad=_CAST_TEXT_PAD,
                )

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
        out = _fallback_base_bgra()
        assignments = self._assignments()
        theme = np_theme_from_settings()
        if (
            self._poster_bgra is not None
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
        # Poster/album under SVG chrome so play button + accents sit on top.
        self._draw_poster(out)
        _paste_patch_bgra(out, self._render_svg_base(now), 0, 0)
        vol_zone = _zone_for_widget(assignments, "volume")
        if vol_zone is not None:
            vol_frac = self._volume_fraction_for_display()
            cx, cy = _zone_clock_center(int(vol_zone))
            _draw_circle_pair(
                out,
                cx=cx,
                cy=cy,
                fraction=vol_frac,
                show_accent=vol_frac > 1e-6,
                theme=theme,
            )
        for z in (1, 2, 3):
            if assignments[z - 1] != "now_playing":
                continue
            ncx, ncy = _zone_clock_center(z)
            self._draw_circular_now_playing(out, cx=ncx, cy=ncy, now=now)
        if assignments[4] == "now_playing":
            self._draw_status_bar(out)
        self._draw_clock_digital(out, now)
        self._draw_audio_level_labels(out)
        if vol_zone is not None:
            vcx, vcy = _zone_clock_center(int(vol_zone))
            self._draw_audio_group(out, cx=vcx, cy=vcy)
        if self.content_mode == _CONTENT_MODE_MUSIC:
            if _zone_for_widget(assignments, "poster") is not None:
                self._draw_track_titles(out)
        else:
            # Zone4 and zone5 both draw when assigned cast (zone5 fallback included).
            for z in (4, 5):
                if assignments[z - 1] == "cast_info":
                    self._draw_cast(out, cast_zone=z)
        return out

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
