"""
Pigeon device settings — ``settings_0.8/settings_pigeon.svg``.

Opened from main settings box1. Selectable tiles 1–5 + BACK; tiles 6–9
(WIFI / METADATA / HDMI / AUDIO) toggle those data sources on and off.
Uses the shared settings theme background (SVG ``background`` / menu
containers are disabled).
"""

from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.settings_layout import SETTINGS_BACKGROUND_SHIFT_Y, SETTINGS_CANVAS_ORIGIN
from pigeon.version import version_string
from pigeon.widgets.main_settings import (
    MainSettingsState,
    _composite_bgra_over_bgra,
    _disable_embedded_settings_background_layers,
    _draw_container_background_bgra,
    _find_by_logical_id,
    _prune_display_none,
    _set_paint,
    _set_text_content,
    _set_visible,
)

# Crop Illustrator board so the menu panel aligns with the shared theme mask.
# ViewBox y is nudged with the settings plate so tiles sit on the shifted background.
_PIGEON_VIEWBOX = (
    SETTINGS_CANVAS_ORIGIN[0],
    SETTINGS_CANVAS_ORIGIN[1] - SETTINGS_BACKGROUND_SHIFT_Y,
    1280.0,
    800.0,
)

_COLOR_BLACK = "#000000"
_COLOR_WHITE = "#FFFFFF"
_COLOR_TEXT_OFF = "#808080"
_COLOR_BACK_FILL = "#202020"
_COLOR_STATUS_OK = "#0DFF00"
_COLOR_STATUS_BAD = "#FF0013"
_WIFI_RING_STROKE = "#E2E2E2"
# Focused COLOR / OPTIONS tiles show their header widget at half strength until enter.
_OVERLAY_PREVIEW_OPACITY = 0.5

# Color tile gradient (SVG user units on the 2365×2422 board). PyMuPDF ignores clip-path.
# clippath-4 in the 1280 export: rounded square over the rainbow image.
_COLOR_CLIP_SVG = (683.18, 916.95, 149.79, 149.79, 16.35)  # x,y,w,h,rx
_COLOR_IMG_TRANSFORM_SVG = (0.76, 0.76, 675.22, 908.94)  # sx,sy,tx,ty
_COLOR_IMG_SIZE_SVG = (218.0, 218.0)
_COLOR_DOT_SVG = (758.31, 991.85, 25.16)  # cx,cy,r

# WiFi rings (SVG). PyMuPDF drops clip-path — redraw with button ∩ triangle fan.
_WIFI_BUTTON_SVG = (870.89, 1165.09, 149.68, 149.68, 10.35)  # x,y,w,h,rx
_WIFI_CENTER_SVG = (946.58, 1311.87)
_WIFI_RADII_SVG = (46.6, 73.83, 97.71)
_WIFI_STROKE_SVG = 7.0
# ``clippath`` polygon — downward fan that shapes the arcs into a wifi wedge.
_WIFI_FAN_POLYGON_SVG: tuple[tuple[float, float], ...] = (
    (946.58, 1200.24),
    (990.77, 1199.90),
    (968.38, 1238.00),
    (946.58, 1276.44),
    (924.78, 1238.00),
    (902.39, 1199.90),
)

# Update icon: white bar + gray fill clipped by ``clippath-1`` (PyMuPDF drops it).
_UPDATE_BAR_SVG = (1648.85, 982.31, 106.5, 21.07, 6.67)  # x,y,w,h,rx
_UPDATE_CLIP_SVG = (1723.7, 958.72, 90.54, 62.39)  # x,y,w,h
_UPDATE_STROKE_SVG = 2.0
_UPDATE_GRAY = (0x4A, 0x4A, 0x4A)  # BGR

# Reset refresh arcs: same circle, two rotated rects keep the visible strokes.
_RESET_CIRCLE_SVG = (1515.05, 992.84, 46.46)  # cx, cy, r
_RESET_STROKE_SVG = 8.0
# (x, y, w, h, rotate_deg, translate_x, translate_y) — SVG apply rotate then translate.
_RESET_EXCLUDE_RECTS: tuple[tuple[float, float, float, float, float, float, float], ...] = (
    (1326.96, 873.75, 232.17, 140.01, -45.0, -244.6777, 1296.8069),
    (1468.09, 971.91, 232.17, 140.01, 135.0, 3441.101, 658.4903),
)

XLINK_NS = "http://www.w3.org/1999/xlink"

# Focus ring: BACK + tiles 1–9 (actions 1–5, source toggles 6–9).
_PIGEON_FOCUS_RING: tuple[str, ...] = (
    "pigeon_back",
    "color_button",
    "info_button",
    "general_button",
    "reset_button",
    "update_button",
    "wifi_button",
    "metadata_button",
    "hdmi_button",
    "audio_button",
)

# Legacy aliases from the old 12-tile Pillow grid.
_FOCUS_ALIASES: dict[str, str] = {
    "prefs_button": "general_button",
    "colors_button": "color_button",
    "color_button": "color_button",
}

# (focus_id, text_group logical id, text_button id, text id)
_SELECTABLE_TILES: tuple[tuple[str, str, str, str], ...] = (
    (
        "color_button",
        "settings_pigeon_01_text_group",
        "settings_pigeon_version_color_button",
        "settings_pigeon_version_color_text",
    ),
    (
        "info_button",
        "settings_pigeon_02_info_text_group",
        "settings_pigeon_02_info_button",
        "settings_pigeon_02_info_text",
    ),
    (
        "general_button",
        "settings_pigeon_03_general_text_group",
        "settings_pigeon_03_general_box_button",
        "settings_pigeon_03_general_text",
    ),
    (
        "reset_button",
        "settings_pigeon_04_reset_text_group",
        "settings_pigeon_04_reset_text_button",
        "settings_pigeon_04_reset_text",
    ),
    (
        "update_button",
        "settings_pigeon_05_update_text_group",
        "settings_pigeon_05_update_button",
        "settings_pigeon_05_update_text",
    ),
    (
        "wifi_button",
        "settings_pigeon_06_wifi_text_group",
        "settings_pigeon_06_wifi_button-2",
        "settings_pigeon_06_wifi_text",
    ),
    (
        "metadata_button",
        "settings_pigeon_07_metadata_text_group",
        "settings_pigeon_07_metadata_button-2",
        "settings_pigeon_07_metadata_text",
    ),
    (
        "hdmi_button",
        "settings_pigeon_08_hdmi_text_group",
        "settings_pigeon_09_hdmi_button-2",
        "settings_pigeon_09_hdmi_text_text",
    ),
    (
        "audio_button",
        "settings_pigeon_09_audio_text_group",
        "settings_pigeon_09_audio_text_button",
        "settings_pigeon_09_audio_text_text",
    ),
)

_STATUS_ICONS: tuple[tuple[str, str], ...] = (
    ("wifi", "settings_pigeon_06_wifi_status_icon"),
    ("metadata", "settings_pigeon_07_metadata_status_icon"),
    ("hdmi", "settings_pigeon_08_hdmi_status_icon"),
    ("audio", "settings_pigeon_09_audio_status_icon"),
)

_SOURCE_TILE_KINDS: dict[str, str] = {
    "wifi_button": "wifi",
    "metadata_button": "metadata",
    "hdmi_button": "hdmi",
    "audio_button": "audio",
}

_UPDATE_BADGE_ID = "settings_pigeon_05_update_icon-2"

_SVG_TREE_TEMPLATES: dict[tuple[str, int], ET.Element] = {}
_SVG_TREE_TEMPLATE_MAX = 4
_THEME_BG_CACHE: dict[tuple[str, str, int, int], np.ndarray] = {}
_THEME_BG_CACHE_MAX = 8
_COLOR_GRAD_CACHE: dict[tuple[str, int, int], np.ndarray] = {}


def default_pigeon_settings_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_PIGEON_SETTINGS_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    from pigeon.settings_layout import settings_widget_path

    native = settings_widget_path("pigeon_page", assets_dir=assets_dir)
    if native.is_file():
        return native
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "settings_pigeon.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "settings_pigeon.svg"


def pigeon_focus_ring() -> tuple[str, ...]:
    return _PIGEON_FOCUS_RING


def normalize_pigeon_focus_id(focus_id: str) -> str:
    fid = str(focus_id or "").strip()
    return _FOCUS_ALIASES.get(fid, fid)


def _paint_text(el: ET.Element | None, color: str) -> None:
    if el is None:
        return
    nodes = [el] if el.tag.endswith("text") else [
        n for n in el.iter() if n.tag.endswith("text") or n.tag.endswith("tspan")
    ]
    if not nodes:
        nodes = [el]
    for node in nodes:
        _set_paint(node, fill=color)


def _paint_button(el: ET.Element | None, *, fill: str, stroke: str = _COLOR_BLACK) -> None:
    if el is None:
        return
    _set_paint(el, fill=fill, stroke=stroke)


def _sync_back_button(root: ET.Element, *, selected: bool) -> None:
    group = _find_by_logical_id(root, "settings_pigeon_back_group")
    button = _find_by_logical_id(group or root, "settings_pigeon_back_button")
    accent = _find_by_logical_id(group or root, "settings_pigeon_back_accent")
    text = _find_by_logical_id(group or root, "settings_pigeon_back_text")
    if selected:
        _paint_button(button, fill=_COLOR_WHITE, stroke=_COLOR_BLACK)
        if accent is not None:
            _set_paint(accent, fill="none", stroke=_COLOR_BLACK)
        _paint_text(text, _COLOR_BLACK)
    else:
        _paint_button(button, fill=_COLOR_BACK_FILL, stroke=_COLOR_BLACK)
        if accent is not None:
            _set_paint(accent, fill="none", stroke=_COLOR_WHITE)
        _paint_text(text, _COLOR_WHITE)


def _sync_selectable_tile(
    root: ET.Element,
    focus_id: str,
    *,
    selected: bool,
    source_on: bool = True,
    dimmed: bool = False,
) -> None:
    for fid, _tg, button_id, text_id in _SELECTABLE_TILES:
        if fid != focus_id:
            continue
        button = _find_by_logical_id(root, button_id)
        text = _find_by_logical_id(root, text_id)
        label_dimmed = dimmed or not source_on
        if selected:
            _paint_button(button, fill=_COLOR_WHITE, stroke=_COLOR_BLACK)
            _paint_text(text, _COLOR_TEXT_OFF if label_dimmed else _COLOR_BLACK)
        else:
            _paint_button(button, fill=_COLOR_BLACK, stroke=_COLOR_BLACK)
            _paint_text(text, _COLOR_TEXT_OFF if label_dimmed else _COLOR_WHITE)
        return


def _sync_hdmi_icon(root: ET.Element, *, dimmed: bool) -> None:
    """Gray the HDMI plug glyph when there is no live signal or the source is off."""
    group = _find_by_logical_id(root, "settings_pigeon_08_hdmi_icon_group")
    if group is None:
        return
    color = _COLOR_TEXT_OFF if dimmed else _COLOR_WHITE
    for el in group.iter():
        lid = str(el.get("id") or "").lower()
        if "button" in lid:
            continue
        if "hdmi_line" in lid or lid.endswith("_line"):
            _set_paint(el, fill=_COLOR_BLACK, stroke="none")
            continue
        if not el.tag.endswith(("path", "rect", "circle", "polygon")):
            continue
        fill = (el.get("fill") or "").strip().lower()
        if fill in ("none",):
            if el.get("stroke"):
                _set_paint(el, stroke=color)
            continue
        _set_paint(el, fill=color, stroke="none")


def _source_on(state: MainSettingsState, kind: str) -> bool:
    return bool(getattr(state, f"source_{kind}_on", True))


def _wifi_status_ok(state: MainSettingsState) -> bool:
    """Green when internet is allowed and a network is configured."""
    if not _source_on(state, "wifi"):
        return False
    return bool(getattr(state, "wifi_configured", False))


def _metadata_status_ok(state: MainSettingsState) -> bool:
    """Green when Apple TV / Roku metadata is allowed and a source is present."""
    if not _source_on(state, "metadata"):
        return False
    flagged = getattr(state, "pigeon_metadata_ok", None)
    if flagged is not None:
        return bool(flagged)
    try:
        from pigeon.app_state import read_saved_streaming_device

        return read_saved_streaming_device() is not None
    except Exception:
        return False


def _hdmi_device_present(state: MainSettingsState) -> bool:
    """True when HDMI can currently deliver a video frame to Pigeon."""
    try:
        from pigeon.hdmi_ocr import hdmi_capture_available

        present = hdmi_capture_available()
        state.pigeon_hdmi_ok = present
        return present
    except Exception:
        return bool(getattr(state, "pigeon_hdmi_ok", False))


def _hdmi_status_ok(state: MainSettingsState) -> bool:
    """Green when HDMI is enabled and a live signal can reach Pigeon."""
    if not _source_on(state, "hdmi"):
        return False
    return _hdmi_device_present(state)


def _audio_status_ok(state: MainSettingsState) -> bool:
    """Audio recognizer is not wired — LED stays red even when the tile is on."""
    return False


def _status_ok_for(kind: str, state: MainSettingsState) -> bool:
    if kind == "wifi":
        return _wifi_status_ok(state)
    if kind == "metadata":
        return _metadata_status_ok(state)
    if kind == "hdmi":
        return _hdmi_status_ok(state)
    if kind == "audio":
        return _audio_status_ok(state)
    return False


def _sync_status_icons(root: ET.Element, state: MainSettingsState) -> None:
    for kind, lid in _STATUS_ICONS:
        el = _find_by_logical_id(root, lid)
        if el is None:
            continue
        if not _source_on(state, kind):
            _set_visible(el, False)
            continue
        _set_visible(el, True)
        ok = _status_ok_for(kind, state)
        _set_paint(el, fill=_COLOR_STATUS_OK if ok else _COLOR_STATUS_BAD)


def _sync_update_badge(root: ET.Element, state: MainSettingsState) -> None:
    badge = _find_by_logical_id(root, _UPDATE_BADGE_ID)
    if badge is None:
        return
    show = bool(getattr(state, "update_available", False)) and not bool(
        getattr(state, "update_error", None)
    )
    _set_visible(badge, show)
    if show:
        _set_paint(badge, fill=_COLOR_STATUS_OK)


def _sync_version_text(root: ET.Element, state: MainSettingsState) -> None:
    ver = str(getattr(state, "version_string", "") or version_string()).strip()
    if ver.lower().startswith("v"):
        ver = ver[1:].lstrip()
    label = f"PIGEON V {ver}" if ver else "PIGEON"
    text = _find_by_logical_id(root, "settings_pigeon_version_text")
    if text is None:
        return
    # Prefer the nested text node when the group wraps it.
    if not text.tag.endswith("text"):
        nested = None
        for node in text.iter():
            if node is text:
                continue
            if node.tag.endswith("text"):
                nested = node
                break
        text = nested if nested is not None else text
    _set_text_content(text, label)
    try:
        from pigeon.widgets.options_settings import ui_ink_hex

        plate_ink = ui_ink_hex()
    except Exception:
        plate_ink = _COLOR_WHITE
    _paint_text(text, plate_ink)
    # Right-align inside the menu panel so the string never clips the right edge.
    vb_x, vb_y, _vb_w, _vb_h = _PIGEON_VIEWBOX
    text.set("transform", f"translate({vb_x + 1180.0:.2f} {vb_y + 159.0:.2f})")
    text.set("text-anchor", "end")
    text.attrib.pop("style", None)


def _sync_info_label(root: ET.Element) -> None:
    """Keep tile 02/03 labels in sync if art is re-exported (0815 wording)."""
    text = _find_by_logical_id(root, "settings_pigeon_02_info_text")
    if text is not None:
        _set_text_content(text, "NOW PLAY")
    widgets = _find_by_logical_id(root, "settings_pigeon_03_general_text")
    if widgets is not None:
        _set_text_content(widgets, "OPTIONS")


def apply_pigeon_settings_svg_state(root: ET.Element, state: MainSettingsState) -> None:
    picker = bool(getattr(state, "show_ui_color", False))
    options = bool(getattr(state, "show_options", False))
    tile_focus = normalize_pigeon_focus_id(state.pigeon_focused_id)
    preview_overlay = (
        not picker
        and not options
        and tile_focus in ("color_button", "general_button")
    )
    focused = "" if picker or options else tile_focus
    if picker:
        for bid in (
            "settings_pigeon_back_group",
            "settings_pigeon_back_button",
            "settings_pigeon_back_accent",
            "settings_pigeon_back_text",
        ):
            _set_visible(_find_by_logical_id(root, bid), False)
    else:
        back_on = focused == "pigeon_back" or (
            options and str(getattr(state, "options_focused_id", "") or "") == "pigeon_back"
        )
        _sync_back_button(root, selected=back_on)
    hdmi_present = _hdmi_device_present(state)
    for fid, _tg, _b, _t in _SELECTABLE_TILES:
        kind = _SOURCE_TILE_KINDS.get(fid)
        source_on = _source_on(state, kind) if kind else True
        dimmed = kind == "hdmi" and (not source_on or not hdmi_present)
        _sync_selectable_tile(
            root,
            fid,
            selected=(focused == fid),
            source_on=source_on,
            dimmed=dimmed,
        )
    _sync_hdmi_icon(root, dimmed=(not _source_on(state, "hdmi") or not hdmi_present))
    _sync_info_label(root)
    _sync_status_icons(root, state)
    _sync_update_badge(root, state)
    if picker or options or preview_overlay:
        _set_visible(_find_by_logical_id(root, "settings_pigeon_version_text"), False)
    else:
        _sync_version_text(root, state)
    # Re-exports sometimes give the color tile a solid black fill that
    # covers the rainbow; the accent is a stroke-only rounded frame.
    accent = _find_by_logical_id(root, "settings_pigeon_01_color_box_accent")
    if accent is not None:
        _set_paint(accent, fill="none")


def _svg_to_px(x: float, y: float) -> tuple[float, float]:
    vb_x, vb_y, vb_w, vb_h = _PIGEON_VIEWBOX
    return ((x - vb_x) * DESIGN_W / vb_w, (y - vb_y) * DESIGN_H / vb_h)


def _svg_len_to_px(v: float) -> float:
    vb_w = _PIGEON_VIEWBOX[2]
    return float(v) * DESIGN_W / vb_w


def _hide_color_gradient_image(root: ET.Element) -> None:
    """Hide the rainbow <image> only when we will redraw it clipped."""
    grad = _find_by_logical_id(root, "settings_pigeon_01_color_box_gradient")
    if grad is None:
        return
    for el in grad.iter():
        if el.tag.endswith("image"):
            _set_visible(el, False)


def _hide_pymupdf_clip_victims(root: ET.Element, *, hide_color_image: bool) -> None:
    """Hide layers that rely on clip-path — PyMuPDF ignores those clips."""
    if hide_color_image:
        _hide_color_gradient_image(root)
    for eid in (
        "settings_pigeon_01_color_icon_black_dot",
        "settings_pigeon_01_color_icon_dot",
    ):
        dot = _find_by_logical_id(root, eid)
        if dot is not None:
            _set_visible(dot, False)
    # WiFi concentric rings (full circles without button clip).
    wifi_icon = _find_by_logical_id(root, "settings_pigeon_06_wifi_icon_group")
    if wifi_icon is not None:
        for el in wifi_icon.iter():
            if el.tag.endswith("circle"):
                _set_visible(el, False)
    # Update bar is redrawn with an even stroke; PyMuPDF also drops clippath-1.
    update_icon = _find_by_logical_id(root, "settings_pigeon_05_update_icon")
    if update_icon is not None:
        for el in update_icon.iter():
            if el is update_icon:
                continue
            if el.tag.endswith("rect"):
                _set_visible(el, False)
            cp = (el.get("clip-path") or "").strip().lower()
            if "clippath" in cp:
                _set_visible(el, False)
                for child in el.iter():
                    if child is el:
                        continue
                    if child.tag.endswith("rect"):
                        _set_visible(child, False)
    # Reset arcs: clippath-2/3 are subtractive and ignored by PyMuPDF.
    for eid in ("left_eplipse", "right_ellipse"):
        group = _find_by_logical_id(root, eid)
        if group is not None:
            _set_visible(group, False)


def _rounded_rect_mask(w: int, h: int, radius: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    ww, hh = max(1, int(w)), max(1, int(h))
    r = max(0, min(int(radius), ww // 2, hh // 2))
    img = Image.new("L", (ww, hh), 0)
    ImageDraw.Draw(img).rounded_rectangle((0, 0, ww - 1, hh - 1), radius=r, fill=255)
    return np.asarray(img, dtype=np.uint8)


def _paste_bgra(dst: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    if patch is None or patch.size == 0:
        return
    ph, pw = patch.shape[:2]
    x0, y0 = int(x), int(y)
    x1, y1 = x0 + pw, y0 + ph
    if x1 <= 0 or y1 <= 0 or x0 >= dst.shape[1] or y0 >= dst.shape[0]:
        return
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    dx1 = min(dst.shape[1], x1)
    dy1 = min(dst.shape[0], y1)
    if dx0 >= dx1 or dy0 >= dy1:
        return
    src = patch[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)]
    if src.shape[2] < 4:
        dst[dy0:dy1, dx0:dx1, :3] = src[:, :, :3]
        dst[dy0:dy1, dx0:dx1, 3] = 255
        return
    a = src[:, :, 3:4].astype(np.float32) / 255.0
    out = dst[dy0:dy1, dx0:dx1].astype(np.float32)
    out[:, :, :3] = out[:, :, :3] * (1.0 - a) + src[:, :, :3].astype(np.float32) * a
    out[:, :, 3] = np.maximum(out[:, :, 3], src[:, :, 3].astype(np.float32))
    dst[dy0:dy1, dx0:dx1] = np.clip(out, 0, 255).astype(np.uint8)


def _load_color_gradient_bgra(svg_path: Path) -> np.ndarray | None:
    import base64

    import cv2

    try:
        st = svg_path.stat()
        key = (str(svg_path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(svg_path), 0, 0)
    cached = _COLOR_GRAD_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    try:
        root = ET.parse(svg_path).getroot()
    except Exception:
        return None
    href = ""
    for el in root.iter():
        if not el.tag.endswith("image"):
            continue
        href = (
            el.get(f"{{{XLINK_NS}}}href")
            or el.get("href")
            or el.get("xlink:href")
            or ""
        )
        if href.startswith("data:image"):
            break
    if not href.startswith("data:image"):
        return None
    try:
        _header, b64 = href.split(",", 1)
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    except Exception:
        return None
    if bgr is None or bgr.size == 0:
        return None
    if bgr.ndim == 2:
        bgra = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGRA)
    elif bgr.shape[2] == 3:
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    else:
        bgra = bgr
    while len(_COLOR_GRAD_CACHE) >= 4:
        _COLOR_GRAD_CACHE.pop(next(iter(_COLOR_GRAD_CACHE)))
    _COLOR_GRAD_CACHE[key] = bgra
    return bgra.copy()


def _parse_translate_scale(transform: str) -> tuple[float, float, float, float]:
    """SVG ``translate(tx ty) scale(s)`` → (sx, sy, tx, ty)."""
    import re

    tx = ty = 0.0
    sx = sy = 1.0
    tm = re.search(
        r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)",
        transform or "",
        re.IGNORECASE,
    )
    if tm:
        tx = float(tm.group(1))
        ty = float(tm.group(2) or 0.0)
    sm = re.search(
        r"scale\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)",
        transform or "",
        re.IGNORECASE,
    )
    if sm:
        sx = float(sm.group(1))
        sy = float(sm.group(2) or sx)
    return sx, sy, tx, ty


def _color_clip_from_root(root: ET.Element) -> tuple[float, float, float, float, float]:
    for el in root.iter():
        eid = (el.get("id") or "").strip().lower()
        if eid != "clippath-4":
            continue
        for child in el.iter():
            if not child.tag.endswith("rect"):
                continue
            try:
                return (
                    float(child.get("x") or _COLOR_CLIP_SVG[0]),
                    float(child.get("y") or _COLOR_CLIP_SVG[1]),
                    float(child.get("width") or _COLOR_CLIP_SVG[2]),
                    float(child.get("height") or _COLOR_CLIP_SVG[3]),
                    float(child.get("rx") or child.get("ry") or _COLOR_CLIP_SVG[4]),
                )
            except ValueError:
                break
    accent = _find_by_logical_id(root, "settings_pigeon_01_color_box_accent")
    if accent is not None:
        try:
            return (
                float(accent.get("x") or _COLOR_CLIP_SVG[0]),
                float(accent.get("y") or _COLOR_CLIP_SVG[1]),
                float(accent.get("width") or _COLOR_CLIP_SVG[2]),
                float(accent.get("height") or _COLOR_CLIP_SVG[3]),
                float(accent.get("rx") or accent.get("ry") or _COLOR_CLIP_SVG[4]),
            )
        except ValueError:
            pass
    return _COLOR_CLIP_SVG


def _color_image_placement_from_root(
    root: ET.Element,
) -> tuple[float, float, float, float, float, float]:
    """Return (sx, sy, tx, ty, img_w, img_h) from the live SVG image."""
    sx, sy, tx, ty = _COLOR_IMG_TRANSFORM_SVG
    img_w, img_h = _COLOR_IMG_SIZE_SVG
    grad = _find_by_logical_id(root, "settings_pigeon_01_color_box_gradient")
    if grad is None:
        return sx, sy, tx, ty, img_w, img_h
    for el in grad.iter():
        if not el.tag.endswith("image"):
            continue
        try:
            img_w = float(el.get("width") or img_w)
            img_h = float(el.get("height") or img_h)
        except ValueError:
            pass
        parsed = _parse_translate_scale(el.get("transform") or "")
        if parsed != (1.0, 1.0, 0.0, 0.0) or el.get("transform"):
            sx, sy, tx, ty = parsed
        break
    return sx, sy, tx, ty, img_w, img_h


def _draw_color_icon_clipped(
    bgra: np.ndarray,
    svg_path: Path,
    root: ET.Element | None = None,
    master: np.ndarray | None = None,
) -> None:
    """Paint the rainbow tile with a rounded-rect clip (PyMuPDF can't)."""
    import cv2

    if root is None:
        try:
            root = ET.parse(svg_path).getroot()
        except Exception:
            root = ET.Element("svg")
    cx, cy, cw, ch, crx = _color_clip_from_root(root)
    clip_x0, clip_y0 = _svg_to_px(cx, cy)
    clip_x1, clip_y1 = _svg_to_px(cx + cw, cy + ch)
    clip_w = max(1, int(round(clip_x1 - clip_x0)))
    clip_h = max(1, int(round(clip_y1 - clip_y0)))
    radius = max(1, int(round(_svg_len_to_px(crx))))
    mask = _rounded_rect_mask(clip_w, clip_h, radius)

    if master is None:
        master = _load_color_gradient_bgra(svg_path)
    if master is None:
        return
    sx, sy, tx, ty, img_w, img_h = _color_image_placement_from_root(root)
    dest_x0, dest_y0 = tx, ty
    dest_w, dest_h = img_w * sx, img_h * sy
    x0, y0 = _svg_to_px(dest_x0, dest_y0)
    x1, y1 = _svg_to_px(dest_x0 + dest_w, dest_y0 + dest_h)
    pw = max(1, int(round(x1 - x0)))
    ph = max(1, int(round(y1 - y0)))
    scaled = cv2.resize(master, (pw, ph), interpolation=cv2.INTER_AREA)
    if scaled.ndim == 2:
        scaled = cv2.cvtColor(scaled, cv2.COLOR_GRAY2BGRA)
    elif scaled.shape[2] == 3:
        scaled = cv2.cvtColor(scaled, cv2.COLOR_BGR2BGRA)
    # Keep the PNG's own alpha. Forcing 255 flattened Illustrator's
    # transparent-on-black pixels into an opaque black tile.

    ix0 = int(round(clip_x0 - x0))
    iy0 = int(round(clip_y0 - y0))
    cell = np.zeros((clip_h, clip_w, 4), dtype=np.uint8)
    sx0 = max(0, ix0)
    sy0 = max(0, iy0)
    dx0 = max(0, -ix0)
    dy0 = max(0, -iy0)
    dw = min(clip_w - dx0, scaled.shape[1] - sx0)
    dh = min(clip_h - dy0, scaled.shape[0] - sy0)
    if dw > 0 and dh > 0:
        cell[dy0 : dy0 + dh, dx0 : dx0 + dw] = scaled[sy0 : sy0 + dh, sx0 : sx0 + dw]
    cell[:, :, 3] = np.minimum(cell[:, :, 3], mask)
    cell[mask == 0, :3] = 0
    _paste_bgra(bgra, cell, int(round(clip_x0)), int(round(clip_y0)))

    dcx, dcy, dr = _COLOR_DOT_SVG
    px, py = _svg_to_px(dcx, dcy)
    rr = max(1, int(round(_svg_len_to_px(dr))))
    cv2.circle(
        bgra,
        (int(round(px)), int(round(py))),
        rr,
        (0x33, 0x33, 0x33, 255),
        -1,
        lineType=cv2.LINE_AA,
    )


def _draw_wifi_icon_clipped(bgra: np.ndarray) -> None:
    """Paint WiFi arcs clipped to the rounded button ∩ triangle fan (clippath-6)."""
    import cv2

    bx, by, bw, bh, brx = _WIFI_BUTTON_SVG
    x0, y0 = _svg_to_px(bx, by)
    x1, y1 = _svg_to_px(bx + bw, by + bh)
    cw = max(1, int(round(x1 - x0)))
    ch = max(1, int(round(y1 - y0)))
    radius = max(1, int(round(_svg_len_to_px(brx))))
    button_clip = _rounded_rect_mask(cw, ch, radius)

    # Triangle/hex fan from Illustrator ``clippath-6`` (local to the button cell).
    fan = np.zeros((ch, cw), dtype=np.uint8)
    fan_pts = np.array(
        [
            (
                int(round(_svg_to_px(px, py)[0] - x0)),
                int(round(_svg_to_px(px, py)[1] - y0)),
            )
            for px, py in _WIFI_FAN_POLYGON_SVG
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(fan, [fan_pts], 255)
    clip = cv2.bitwise_and(button_clip, fan)

    cx_svg, cy_svg = _WIFI_CENTER_SVG
    cx, cy = _svg_to_px(cx_svg, cy_svg)
    stroke = max(2, int(round(_svg_len_to_px(_WIFI_STROKE_SVG))))
    color = (0xE2, 0xE2, 0xE2)

    rings = np.zeros((ch, cw, 4), dtype=np.uint8)
    lcx = int(round(cx - x0))
    lcy = int(round(cy - y0))
    for r_svg in _WIFI_RADII_SVG:
        rr = max(1, int(round(_svg_len_to_px(r_svg))))
        cv2.circle(rings, (lcx, lcy), rr, (*color, 255), stroke, lineType=cv2.LINE_AA)
    rings[:, :, 3] = np.minimum(rings[:, :, 3], clip)
    rings[clip == 0, :3] = 0
    _paste_bgra(bgra, rings, int(round(x0)), int(round(y0)))


def _draw_update_icon_clipped(bgra: np.ndarray) -> None:
    """Paint the update pill with a centered stroke, then the gray clip fill."""
    import cv2
    from PIL import Image, ImageDraw

    bx, by, bw, bh, brx = _UPDATE_BAR_SVG
    cx, cy, cw, ch = _UPDATE_CLIP_SVG
    stroke = max(2, int(round(_svg_len_to_px(_UPDATE_STROKE_SVG))))
    x0, y0 = _svg_to_px(bx, by)
    x1, y1 = _svg_to_px(bx + bw, by + bh)
    pw = max(1, int(round(x1 - x0)))
    ph = max(1, int(round(y1 - y0)))
    pad = stroke
    tw, th = pw + 2 * pad, ph + 2 * pad
    radius = max(1, int(round(_svg_len_to_px(brx))))
    img = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # Inset the outline so top and bottom strokes stay equal.
    box = (pad, pad, pad + pw - 1, pad + ph - 1)
    draw.rounded_rectangle(box, radius=radius, fill=(255, 255, 255, 255))
    draw.rounded_rectangle(
        box, radius=radius, outline=(255, 255, 255, 255), width=stroke
    )
    pill = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
    _paste_bgra(bgra, pill, int(round(x0)) - pad, int(round(y0)) - pad)

    inset = max(1, stroke)
    inner_w = max(1, pw - 2 * inset)
    inner_h = max(1, ph - 2 * inset)
    bar_mask = _rounded_rect_mask(inner_w, inner_h, max(1, radius - inset))
    clip_x0, clip_y0 = _svg_to_px(cx, cy)
    clip_x1, clip_y1 = _svg_to_px(cx + cw, cy + ch)
    # Clip rect is in bar space; shift into the inset inner fill.
    lx0 = int(round(clip_x0 - x0)) - inset
    ly0 = int(round(clip_y0 - y0)) - inset
    lx1 = int(round(clip_x1 - x0)) - inset
    ly1 = int(round(clip_y1 - y0)) - inset
    clip_mask = np.zeros((inner_h, inner_w), dtype=np.uint8)
    rx0 = max(0, min(inner_w, lx0))
    ry0 = max(0, min(inner_h, ly0))
    rx1 = max(0, min(inner_w, lx1))
    ry1 = max(0, min(inner_h, ly1))
    if rx1 > rx0 and ry1 > ry0:
        clip_mask[ry0:ry1, rx0:rx1] = 255
    mask = cv2.bitwise_and(bar_mask, clip_mask)
    if int(mask.max()) == 0:
        return
    patch = np.zeros((inner_h, inner_w, 4), dtype=np.uint8)
    patch[:, :, 0] = _UPDATE_GRAY[0]
    patch[:, :, 1] = _UPDATE_GRAY[1]
    patch[:, :, 2] = _UPDATE_GRAY[2]
    patch[:, :, 3] = mask
    patch[mask == 0, :3] = 0
    _paste_bgra(bgra, patch, int(round(x0)) + inset, int(round(y0)) + inset)


def _reset_exclude_poly_px(
    spec: tuple[float, float, float, float, float, float, float],
) -> np.ndarray:
    import math

    x, y, w, h, angle, tx, ty = spec
    corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    rad = math.radians(angle)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    pts = []
    for px, py in corners:
        rx = px * cos_a - py * sin_a + tx
        ry = px * sin_a + py * cos_a + ty
        pts.append(_svg_to_px(rx, ry))
    return np.array(pts, dtype=np.int32)


def _draw_reset_icon_clipped(bgra: np.ndarray) -> None:
    """Stroke two reset arcs; each circle is kept only inside its clip rect."""
    import cv2

    cx_svg, cy_svg, r_svg = _RESET_CIRCLE_SVG
    stroke = max(2, int(round(_svg_len_to_px(_RESET_STROKE_SVG))))
    pad = stroke + 4
    cx, cy = _svg_to_px(cx_svg, cy_svg)
    radius = max(1, int(round(_svg_len_to_px(r_svg))))
    dim = 2 * (radius + pad)
    lcx = radius + pad
    lcy = radius + pad
    origin_x = int(round(cx)) - lcx
    origin_y = int(round(cy)) - lcy
    color = (0xFF, 0xFF, 0xFF)
    combined = np.zeros((dim, dim, 4), dtype=np.uint8)
    for spec in _RESET_EXCLUDE_RECTS:
        ring = np.zeros((dim, dim, 4), dtype=np.uint8)
        cv2.circle(ring, (lcx, lcy), radius, (*color, 255), stroke, lineType=cv2.LINE_AA)
        keep = np.zeros((dim, dim), dtype=np.uint8)
        poly = _reset_exclude_poly_px(spec)
        poly[:, 0] -= origin_x
        poly[:, 1] -= origin_y
        cv2.fillPoly(keep, [poly], 255)
        ring[keep == 0, :] = 0
        vis = ring[:, :, 3] > combined[:, :, 3]
        combined[vis] = ring[vis]
    if int(combined[:, :, 3].max()) == 0:
        return
    _paste_bgra(bgra, combined, origin_x, origin_y)


def _svg_tree_from_path(path: Path) -> ET.Element:
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        tree = ET.parse(path)
        root = tree.getroot()
        x, y, w, h = _PIGEON_VIEWBOX
        root.set("viewBox", f"{x} {y} {w} {h}")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        if len(_SVG_TREE_TEMPLATES) >= _SVG_TREE_TEMPLATE_MAX:
            _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _full_theme_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None,
    path: Path,
) -> np.ndarray:
    ui_hex = str(getattr(state.theme, "ui", "#4EA6F7") or "#4EA6F7")
    adir = str(assets_dir if assets_dir is not None else path.parent.parent)
    key = (ui_hex, adir, int(DESIGN_W), int(DESIGN_H))
    cached = _THEME_BG_CACHE.get(key)
    if cached is not None:
        return cached
    bg_bgra = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    bg_bgra[:, :, 3] = 255
    _draw_container_background_bgra(bg_bgra, ui_hex=ui_hex, assets_dir=adir)
    while len(_THEME_BG_CACHE) >= _THEME_BG_CACHE_MAX:
        _THEME_BG_CACHE.pop(next(iter(_THEME_BG_CACHE)))
    _THEME_BG_CACHE[key] = bg_bgra
    return bg_bgra


def render_pigeon_settings_bgra(
    state: MainSettingsState | None = None,
    *,
    svg_path: Path | str | None = None,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    path = Path(svg_path) if svg_path is not None else default_pigeon_settings_svg_path(assets_dir)
    if not path.is_file():
        raise FileNotFoundError(f"pigeon settings SVG not found: {path}")
    st = state if state is not None else MainSettingsState()
    root = _svg_tree_from_path(path)
    apply_pigeon_settings_svg_state(root, st)
    color_master = _load_color_gradient_bgra(path)
    _hide_pymupdf_clip_victims(root, hide_color_image=color_master is not None)
    _disable_embedded_settings_background_layers(root)
    _prune_display_none(root)
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    ui_bgra = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )
    _draw_color_icon_clipped(ui_bgra, path, root=root, master=color_master)
    _draw_wifi_icon_clipped(ui_bgra)
    _draw_update_icon_clipped(ui_bgra)
    _draw_reset_icon_clipped(ui_bgra)
    bg = _full_theme_bgra(st, assets_dir=assets_dir, path=path)
    frame = _composite_bgra_over_bgra(bg, ui_bgra)
    from pigeon.settings_layout import SETTINGS_MAIN_ZONES
    from pigeon.widgets.settings_main_1280 import (
        _draw_centered_text,
        _exit_root,
        _place_widget,
    )

    picker = bool(getattr(st, "show_ui_color", False))
    options = bool(getattr(st, "show_options", False))
    focused = normalize_pigeon_focus_id(st.pigeon_focused_id)
    preview_picker = (
        not picker and not options and focused == "color_button"
    )
    preview_options = (
        not picker and not options and focused == "general_button"
    )
    if picker or preview_picker:
        from pigeon.widgets.ui_color_settings import render_ui_color_bar_bgra

        bar = render_ui_color_bar_bgra(
            st, assets_dir=assets_dir, preview=preview_picker
        )
        if preview_picker:
            bar = _scale_bgra_alpha(bar, _OVERLAY_PREVIEW_OPACITY)
        frame = _composite_bgra_over_bgra(frame, bar)
        if picker:
            return frame
    if options or preview_options:
        from pigeon.widgets.options_settings import render_options_bar_bgra

        bar = render_options_bar_bgra(
            st, assets_dir=assets_dir, preview=preview_options
        )
        if preview_options:
            bar = _scale_bgra_alpha(bar, _OVERLAY_PREVIEW_OPACITY)
        frame = _composite_bgra_over_bgra(frame, bar)
    back_sel = focused == "pigeon_back" or (
        options and str(getattr(st, "options_focused_id", "") or "") == "pigeon_back"
    )
    z0 = SETTINGS_MAIN_ZONES[0]
    back_box = (
        int(round(z0.x)),
        int(round(z0.y)),
        int(round(z0.w)),
        int(round(z0.h)),
    )
    _place_widget(
        frame,
        _exit_root(
            st,
            assets_dir=assets_dir,
            selected=back_sel,
            label="BACK",
        ),
        x=back_box[0],
        y=back_box[1],
        w=back_box[2],
        h=back_box[3],
    )
    _draw_centered_text(
        frame,
        "BACK",
        box=back_box,
        size=46,
        fill=(0, 0, 0) if back_sel else (255, 255, 255),
    )
    return frame


def _scale_bgra_alpha(bgra: np.ndarray, opacity: float) -> np.ndarray:
    """Return a copy with alpha multiplied by ``opacity`` (clamped to 0..1)."""
    if bgra is None or bgra.size == 0 or bgra.ndim < 3 or bgra.shape[2] < 4:
        return bgra
    o = max(0.0, min(1.0, float(opacity)))
    if o >= 0.999:
        return bgra
    out = bgra.copy()
    out[:, :, 3] = np.clip(
        out[:, :, 3].astype(np.float32) * o, 0.0, 255.0
    ).astype(np.uint8)
    return out


def clear_pigeon_settings_render_caches() -> None:
    _SVG_TREE_TEMPLATES.clear()
    _THEME_BG_CACHE.clear()
    _COLOR_GRAD_CACHE.clear()


def factory_reset_pigeon_persisted_state() -> None:
    """Erase persisted customizations / pairings and restore defaults on disk."""
    from pigeon.app_state import (
        clear_all_persisted_devices_and_targets,
        clear_last_apple_tv,
        clear_last_receiver,
        pop_app_state_keys,
    )
    from pigeon.media_folders import (
        pigeon_pulled_media_dir,
        pigeon_reformatted_media_dir,
        purge_directory_contents,
    )
    from pigeon.runtime_paths import pigeon_state_dir
    from pigeon.widgets.preferences_settings import (
        DEFAULT_ZONE_WIDGETS,
        write_now_playing_zone_widgets,
    )
    from pigeon.widgets.ui_color_settings import write_ui_color_keys

    clear_all_persisted_devices_and_targets()
    try:
        clear_last_apple_tv()
    except Exception:
        pass
    try:
        clear_last_receiver()
    except Exception:
        pass
    pop_app_state_keys(
        "settings_ui_colors",
        "now_playing_zone_widgets",
        "display_par_mode",
        "roku_ecp_base_url",
        "tmdb_quality_ok_count",
        "tmdb_quality_fail_count",
        "source_toggles",
        "settings_options",
    )
    write_ui_color_keys(
        {"accent": "white", "ui": "blue", "button": "black"},
        persist=True,
    )
    write_now_playing_zone_widgets(DEFAULT_ZONE_WIDGETS)
    try:
        purge_directory_contents(pigeon_pulled_media_dir())
    except Exception:
        pass
    try:
        purge_directory_contents(pigeon_reformatted_media_dir())
    except Exception:
        pass
    for name in ("pyatv_credentials",):
        try:
            p = pigeon_state_dir() / name
            if p.is_file():
                p.unlink()
        except OSError:
            pass


__all__ = [
    "apply_pigeon_settings_svg_state",
    "clear_pigeon_settings_render_caches",
    "default_pigeon_settings_svg_path",
    "factory_reset_pigeon_persisted_state",
    "normalize_pigeon_focus_id",
    "pigeon_focus_ring",
    "render_pigeon_settings_bgra",
]
