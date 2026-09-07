"""Native 1280×800 settings-main compositor (PDF widget rebuild)."""

from __future__ import annotations

import copy
import math
import time
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.font_paths import (
    resolve_digital7_font,
    resolve_ui_font_extrabold,
    resolve_ui_font_extrabold_italic,
    resolve_ui_font_semibold,
)
from pigeon.local_ip import local_ipv4_address
from pigeon.settings_layout import (
    DUAL_SLOT_A,
    DUAL_SLOT_B,
    SETTINGS_MAIN_ZONES,
    dual_slot_design,
    settings_widget_path,
    zone_center_rect,
)
from pigeon.widgets.box_device_search import (
    _BOX_SCAN_MAX_DURATION_S,
    box_devices_with_special_rows,
    is_special_device_row,
)
from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

_COLOR_WHITE = "#FFFFFF"
_COLOR_BLACK = "#000000"
_COLOR_SLOT_OFF = "#000000"
_COLOR_COLUMN_OFF = "#1A1A1A"  # 90% black
_COLOR_UI = "#4EA6F7"
_ROW_LETTERS = ("a", "b", "c", "d", "e")

# widget_sm_01-b_network_name.svg — stroked ellipses clipped by a downward wedge.
_WIFI_CX_SVG = 66.13
_WIFI_CY_SVG = 79.26
_WIFI_RADII_SVG = (29.87, 47.32, 62.63)
_WIFI_STROKE_SVG = 7.0
_WIFI_CLIP_SVG = (
    (66.13, 7.7),
    (37.8, 7.49),
    (52.15, 31.91),
    (66.13, 56.55),
    (80.11, 31.91),
    (94.46, 7.49),
)

# widget_sm_02-03-04_list_a-e.svg / widget_sm_02-03-04_a-e_ip_and_device.svg
_LIST_CHROME_VB = (342.19, 324.53)
# Path start is the top straight after the 8.37 corner radius — that is the text inset.
_LIST_ROW_SVG: tuple[tuple[float, float, float, float], ...] = (
    (9.87, 62.08, 322.46, 36.71),
    (9.87, 103.47, 322.46, 36.71),
    (9.87, 144.86, 322.46, 36.71),
    (9.87, 186.25, 322.46, 36.71),
    (9.87, 227.65, 322.46, 36.71),
)
_LIST_DEVICE_SIZE_SVG = 32.0
_LIST_IP_SIZE_SVG = 19.0
# Device label starts at x=84.63 in widget_sm_02-03-04_a-e_ip_and_device.svg.
_LIST_NAME_X_SVG = 84.63
_LIST_PAGE_SIZE = 5
_LIST_STAR_R_SVG = 6.2
_WIFI_SCAN_MAX_DURATION_S = 55.0

# Per-widget SVG rasters (exit/dual/column/list) so a focus change does not
# re-run PyMuPDF on unchanged neighbors.
_WIDGET_RASTER_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_WIDGET_RASTER_CACHE_MAX = 48
_THEME_BG_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_THEME_BG_CACHE_MAX = 4
# Last composed settings-main frame — Left/Right only repaints dirty zones.
_LAST_MAIN: dict[str, object] = {
    "structure": None,
    "focus": None,
    "frame": None,
}

_FOCUS_LAYER: dict[str, int] = {
    "main_exit_button": 0,
    "main_dual_location_button": 1,
    "main_dual_network_button": 1,
    "main_box1_button": 2,
    "main_box2_button": 3,
    "main_box2_add_search_icon": 3,
    "main_box2_device_results": 3,
    "main_network_picker_button": 3,
    "main_box3_button": 4,
    "main_box3_device_results": 4,
}


def _hex_to_bgr(hex_color: str) -> tuple[int, int, int]:
    h = (hex_color or "").strip().lstrip("#")
    if len(h) != 6:
        return (0, 0, 255)
    return (int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16))


def _set_fill(el: ET.Element | None, color: str, *, stroke: str | None = None) -> None:
    if el is None:
        return
    el.set("fill", color)
    if stroke is not None:
        el.set("stroke", stroke)
    style = el.get("style") or ""
    parts = [p for p in style.split(";") if p.strip() and not p.strip().startswith("fill")]
    if stroke is not None:
        parts = [p for p in parts if not p.strip().startswith("stroke")]
        parts.append(f"stroke:{stroke}")
    parts.append(f"fill:{color}")
    el.set("style", ";".join(parts))


def _set_text(el: ET.Element | None, value: str) -> None:
    if el is None:
        return
    nodes = [el] if el.tag.endswith("text") else [n for n in el.iter() if n.tag.endswith("text")]
    if not nodes:
        nodes = [el]
    for node in nodes:
        tspans = [c for c in list(node) if c.tag.endswith("tspan")]
        if tspans:
            tspans[0].text = value
            for extra in tspans[1:]:
                extra.text = ""
        else:
            node.text = value


def _find(root: ET.Element, eid: str) -> ET.Element | None:
    for el in root.iter():
        if (el.get("id") or "") == eid:
            return el
    return None


def _hide(el: ET.Element | None) -> None:
    if el is None:
        return
    el.set("display", "none")
    style = el.get("style") or ""
    el.set("style", f"{style};display:none")


def _viewbox_wh(root: ET.Element) -> tuple[float, float]:
    raw = (root.get("viewBox") or "").replace(",", " ").split()
    if len(raw) == 4:
        return float(raw[2]), float(raw[3])
    return float(root.get("width") or 1), float(root.get("height") or 1)


@lru_cache(maxsize=32)
def _widget_template(path: str, mtime_ns: int) -> ET.Element:
    return ET.parse(path).getroot()


@lru_cache(maxsize=4)
def _png_bgra(path: str, mtime_ns: int) -> np.ndarray:
    im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    if im is None:
        return np.zeros((1, 1, 4), dtype=np.uint8)
    if im.ndim == 2:
        return cv2.cvtColor(im, cv2.COLOR_GRAY2BGRA)
    if im.shape[2] == 3:
        return cv2.cvtColor(im, cv2.COLOR_BGR2BGRA)
    return im


def _load_png(key: str, *, assets_dir: Path | str | None) -> np.ndarray:
    path = settings_widget_path(key, assets_dir=assets_dir)
    return _png_bgra(str(path.resolve()), path.stat().st_mtime_ns)


# widget_sm_02_pigeon.png — wordmark on top; baked IP leftover below.
_PIGEON_WORDMARK_H = 90
# widget_sm_02-03-04_container.svg — keep native aspect; do not stretch to the list slot.
_COLUMN_CARD_VB = (370.9, 238.21)
# Shared two-line card for zones 2 / 3 / 4 (name + IP).
_ZONE_NAME_SIZE = 77
_ZONE_IP_SIZE = 67
_ZONE_LINE_GAP = 12
_ZONE_TEXT_PAD = 18
# widget_sm_03_add_player.svg — Sharp Sans Semibold 26.96 / #202020.
_FINDING_SIZE_PX = 27
_FINDING_RGB = (32, 32, 32)


def _list_chrome_rect(zone_index: int) -> tuple[int, int, int, int]:
    return zone_center_rect(SETTINGS_MAIN_ZONES[zone_index], *_LIST_CHROME_VB)


def _list_rows_rect(zone_index: int) -> tuple[int, int, int, int]:
    """Canvas box covering the five search-result rows (excludes scroll arrows)."""
    lx, ly, lw, lh = _list_chrome_rect(zone_index)
    sy = lh / _LIST_CHROME_VB[1]
    first_y = _LIST_ROW_SVG[0][1]
    last = _LIST_ROW_SVG[-1]
    y = ly + int(round(first_y * sy))
    h = max(1, int(round((last[1] + last[3] - first_y) * sy)))
    return lx, y, lw, h


def _column_card_rect(zone_index: int) -> tuple[int, int, int, int]:
    """Column width, same vertical span as the five search-result rows."""
    zone = SETTINGS_MAIN_ZONES[zone_index]
    scale = min(zone.w / _COLUMN_CARD_VB[0], zone.h / _COLUMN_CARD_VB[1])
    w = max(1, int(round(_COLUMN_CARD_VB[0] * scale)))
    x = int(round(zone.x + (zone.w - w) * 0.5))
    _lx, y, _lw, h = _list_rows_rect(zone_index)
    return x, y, w, h


def _column_line_boxes(
    zone_index: int,
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    x, y, w, h = _column_card_rect(zone_index)
    return _paired_device_line_boxes(
        (
            x + _ZONE_TEXT_PAD,
            y + _ZONE_TEXT_PAD,
            max(8, w - 2 * _ZONE_TEXT_PAD),
            max(8, h - 2 * _ZONE_TEXT_PAD),
        )
    )


def _paired_device_line_boxes(
    box: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Fixed name/IP slots so zone 2 matches zones 3 and 4."""
    zx, zy, zw, zh = box
    total = _ZONE_NAME_SIZE + _ZONE_LINE_GAP + _ZONE_IP_SIZE
    y0 = zy + max(0, (zh - total) // 2)
    return (
        (zx, y0, zw, _ZONE_NAME_SIZE),
        (zx, y0 + _ZONE_NAME_SIZE + _ZONE_LINE_GAP, zw, _ZONE_IP_SIZE),
    )


def _tight_crop_logo(logo: np.ndarray) -> np.ndarray:
    if logo.size == 0:
        return logo
    vis = logo[:, :, :3].max(axis=2) > 20
    if logo.shape[2] >= 4:
        vis = vis | (logo[:, :, 3] > 20)
    ys, xs = np.where(vis)
    if ys.size == 0:
        return logo
    return logo[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]


def _contain_center_blit(
    canvas: np.ndarray,
    src: np.ndarray,
    box: tuple[int, int, int, int],
) -> None:
    """Contain-fit ``src`` into ``box`` and blit it centered."""
    zx, zy, zw, zh = box
    if zw < 1 or zh < 1 or src.size == 0:
        return
    lw, lh = int(src.shape[1]), int(src.shape[0])
    scale = min(zw / max(1, lw), zh / max(1, lh))
    ww = max(1, int(round(lw * scale)))
    hh = max(1, int(round(lh * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    scaled = cv2.resize(src, (ww, hh), interpolation=interp)
    _blit(canvas, scaled, zx + (zw - ww) // 2, zy + (zh - hh) // 2)


def _draw_zone2_pigeon(
    canvas: np.ndarray,
    box: tuple[int, int, int, int],
    ip: str,
    ink: tuple[int, int, int],
    *,
    assets_dir: Path | str | None,
    tt_bgra: np.ndarray | None = None,
) -> None:
    """Pigeon wordmark + IP, matching the predecessor zone-2 card.

    While content is playing, ``tt_bgra`` (TMDb title treatment) is contain-fitted
    and centered in the black column card instead of the name slot. Missing TT
    keeps the pigeon logo.
    """
    zx, zy, zw, zh = box
    if zw < 8 or zh < 8:
        return
    _name_box, ip_box = _paired_device_line_boxes(box)
    logo = None
    if tt_bgra is not None and getattr(tt_bgra, "size", 0):
        logo = np.asarray(tt_bgra)
        if logo.ndim == 3 and logo.shape[2] == 3:
            logo = cv2.cvtColor(logo, cv2.COLOR_BGR2BGRA)
    using_tt = logo is not None and getattr(logo, "size", 0) > 0
    if using_tt:
        _contain_center_blit(canvas, _tight_crop_logo(logo), box)
        return
    logo = _load_png("pigeon_logo", assets_dir=assets_dir)
    if logo.shape[0] > _PIGEON_WORDMARK_H:
        logo = logo[:_PIGEON_WORDMARK_H]
    logo = _tight_crop_logo(logo)
    lw, lh = int(logo.shape[1]), int(logo.shape[0])
    # Grow the wordmark through the space above the IP; keep a line gap.
    logo_h = max(16, int(ip_box[1]) - zy - _ZONE_LINE_GAP)
    scale = min((zw - 16) / max(1, lw), logo_h / max(1, lh))
    ww = max(1, int(round(lw * scale)))
    hh = max(1, int(round(lh * scale)))
    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    scaled = cv2.resize(logo, (ww, hh), interpolation=interp)
    _blit(
        canvas,
        scaled,
        zx + (zw - ww) // 2,
        zy + logo_h - hh,
    )
    if ip:
        _draw_centered_text(
            canvas,
            ip,
            box=ip_box,
            size=_ZONE_IP_SIZE,
            fill=ink,
            digital=True,
            fit=True,
        )


def _load_widget(key: str, *, assets_dir: Path | str | None) -> ET.Element:
    path = settings_widget_path(key, assets_dir=assets_dir)
    root = _widget_template(str(path.resolve()), path.stat().st_mtime_ns)
    return copy.deepcopy(root)


def _prune_hidden(root: ET.Element) -> None:
    """Drop display:none nodes — PyMuPDF ignores the attribute."""
    parents = {child: parent for parent in root.iter() for child in parent}
    for el in list(root.iter()):
        style = el.get("style") or ""
        hidden = el.get("display") == "none" or "display:none" in style.replace(" ", "").lower()
        if not hidden:
            continue
        parent = parents.get(el)
        if parent is not None:
            try:
                parent.remove(el)
            except ValueError:
                pass


def _raster(root: ET.Element, w: int, h: int) -> np.ndarray:
    _prune_hidden(root)
    return rasterize_settings_svg_bgra(root, width=max(1, w), height=max(1, h))


def _blit(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    if src is None or src.size == 0:
        return
    h, w = int(src.shape[0]), int(src.shape[1])
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(int(dst.shape[1]), x + w), min(int(dst.shape[0]), y + h)
    if x2 <= x1 or y2 <= y1:
        return
    sx0, sy0 = x1 - x, y1 - y
    patch = src[sy0 : sy0 + (y2 - y1), sx0 : sx0 + (x2 - x1)]
    roi = dst[y1:y2, x1:x2]
    if patch.shape[2] < 4:
        roi[:, :, :3] = patch[:, :, :3]
        roi[:, :, 3] = 255
        return
    a = patch[:, :, 3]
    opaque = a == 255
    if np.any(opaque):
        roi[opaque, :3] = patch[opaque, :3]
        roi[opaque, 3] = 255
    partial = (a > 0) & (a < 255)
    if np.any(partial):
        fg = patch[partial].astype(np.float32)
        bg = roi[partial].astype(np.float32)
        alpha = fg[:, 3:4] * (1.0 / 255.0)
        inv = 1.0 - alpha
        roi[partial, :3] = fg[:, :3] * alpha + bg[:, :3] * inv
        roi[partial, 3] = np.clip(fg[:, 3] + bg[:, 3] * inv[:, 0], 0, 255)


def _place_widget(
    canvas: np.ndarray,
    root: ET.Element,
    *,
    x: int,
    y: int,
    w: int,
    h: int,
    cache_key: tuple[object, ...] | None = None,
) -> None:
    if cache_key is not None:
        img = _WIDGET_RASTER_CACHE.get(cache_key)
        if img is None:
            img = _raster(root, w, h)
            if len(_WIDGET_RASTER_CACHE) >= _WIDGET_RASTER_CACHE_MAX:
                _WIDGET_RASTER_CACHE.pop(next(iter(_WIDGET_RASTER_CACHE)))
            _WIDGET_RASTER_CACHE[cache_key] = img
        _blit(canvas, img, x, y)
        return
    _blit(canvas, _raster(root, w, h), x, y)


def _place_in_zone(
    canvas: np.ndarray,
    root: ET.Element,
    zone_index: int,
    *,
    view_w: float | None = None,
    view_h: float | None = None,
    align: str = "top",
    cache_key: tuple[object, ...] | None = None,
) -> tuple[int, int, int, int]:
    zone = SETTINGS_MAIN_ZONES[zone_index]
    vw, vh = _viewbox_wh(root) if view_w is None else (view_w, view_h or view_w)
    x, y, w, h = zone_center_rect(zone, vw, vh)
    if align == "top":
        y = int(round(zone.y + 12.0))
    _place_widget(canvas, root, x=x, y=y, w=w, h=h, cache_key=cache_key)
    return x, y, w, h


def _load_font(
    size: int, *, digital: bool = True, italic: bool = False, semibold: bool = False
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    if digital:
        path = resolve_digital7_font()
    elif italic:
        path = resolve_ui_font_extrabold_italic() or resolve_ui_font_extrabold()
    elif semibold:
        path = resolve_ui_font_semibold() or resolve_ui_font_extrabold()
    else:
        path = resolve_ui_font_extrabold()
    try:
        return ImageFont.truetype(str(path), max(6, int(size)))
    except Exception:
        return ImageFont.load_default()


def _fit_font(
    text: str,
    *,
    max_w: int,
    max_h: int,
    start: int,
    digital: bool = True,
    italic: bool = False,
    semibold: bool = False,
    stroke_w: int = 0,
) -> tuple[ImageFont.FreeTypeFont | ImageFont.ImageFont, int, int, int, int]:
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    size = max(8, int(start))
    font = _load_font(size, digital=digital, italic=italic, semibold=semibold)
    tw = th = 0
    top = left = 0
    while size >= 8:
        font = _load_font(size, digital=digital, italic=italic, semibold=semibold)
        bbox = probe.textbbox((0, 0), text, font=font, stroke_width=stroke_w)
        left, top = bbox[0], bbox[1]
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        if tw <= max(8, max_w) and th <= max(8, max_h):
            break
        size -= 2
    return font, tw, th, top, left


def _draw_centered_text(
    canvas: np.ndarray,
    text: str,
    *,
    box: tuple[int, int, int, int],
    size: int,
    fill: tuple[int, int, int] = (0, 0, 0),
    digital: bool = True,
    italic: bool = False,
    stroke: tuple[int, int, int, int] | None = None,
    fit: bool = True,
    align: str = "center",
    semibold: bool = False,
) -> None:
    label = str(text or "")
    if not label:
        return
    x, y, w, h = box
    if w < 4 or h < 4:
        return
    stroke_w = int(stroke[3]) if stroke is not None else 0
    if fit:
        font, tw, th, top, left = _fit_font(
            label,
            max_w=w - 10,
            max_h=h - 4,
            start=size,
            digital=digital,
            italic=italic,
            semibold=semibold,
            stroke_w=stroke_w,
        )
    else:
        font = _load_font(size, digital=digital, italic=italic, semibold=semibold)
        probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
        bbox = probe.textbbox((0, 0), label, font=font, stroke_width=stroke_w)
        left, top = bbox[0], bbox[1]
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    img = Image.fromarray(cv2.cvtColor(canvas[y : y + h, x : x + w], cv2.COLOR_BGRA2RGBA))
    draw = ImageDraw.Draw(img)
    if align == "left":
        tx = 4 - left
    elif align == "right":
        tx = w - tw - 4 - left
    else:
        tx = (w - tw) // 2 - left
    ty = (h - th) // 2 - top
    kw: dict = {"font": font, "fill": fill + (255,)}
    if stroke is not None and stroke_w > 0:
        kw["stroke_width"] = stroke_w
        kw["stroke_fill"] = (stroke[0], stroke[1], stroke[2], 255)
    draw.text((tx, ty), label, **kw)
    canvas[y : y + h, x : x + w] = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)


def _draw_zone_lines(
    canvas: np.ndarray,
    zone_xywh: tuple[int, int, int, int],
    lines: list[tuple[str, int, bool, tuple[int, int, int], bool]],
    *,
    gap: int = 12,
) -> None:
    """Vertically center 1–2 fitted lines in a zone; each line is horizontally centered."""
    zx, zy, zw, zh = zone_xywh
    if zw < 8 or zh < 8:
        return
    prepared: list[tuple[str, int, bool, tuple[int, int, int], bool, int]] = []
    for text, start, digital, fill, italic in lines:
        label = str(text or "")
        if not label:
            continue
        _font, _tw, th, _top, _left = _fit_font(
            label,
            max_w=zw - 16,
            max_h=max(16, zh // max(1, len(lines)) - 4),
            start=start,
            digital=digital,
            italic=italic,
        )
        prepared.append((label, start, digital, fill, italic, th))
    if not prepared:
        return
    total_h = sum(p[5] for p in prepared) + gap * (len(prepared) - 1)
    y = zy + max(0, (zh - total_h) // 2)
    for label, start, digital, fill, italic, th in prepared:
        _draw_centered_text(
            canvas,
            label,
            box=(zx, y, zw, th + 4),
            size=start,
            fill=fill,
            digital=digital,
            italic=italic,
            stroke=(252, 168, 255, 4) if italic else None,
            fit=True,
        )
        y += th + gap


def _current_location_index(state) -> int:
    slots = tuple(getattr(state, "location_slots", ()) or ())
    if not slots:
        return 1
    try:
        from pigeon.app_state import read_current_location_id

        lid = str(read_current_location_id() or "").strip()
    except Exception:
        lid = ""
    for i, row in enumerate(slots[:3]):
        if str(row[0]) == lid:
            return i + 1
    return 1


def _location_field_boxes(
    loc_box: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Split dual slot A into the dots pill (left) and a fitted text box (right)."""
    x, y, w, h = loc_box
    icon_vw, icon_vh = 88.65, 31.01
    scale = min(max(18, h - 16) / icon_vh, max(40, w * 0.22) / icon_vw)
    iw = max(1, int(round(icon_vw * scale)))
    ih = max(1, int(round(icon_vh * scale)))
    ix = x + 10
    iy = y + max(0, (h - ih) // 2)
    tx = ix + iw + 10
    tw = max(8, x + w - tx - 18)
    return (ix, iy, iw, ih), (tx, y, tw, h)


def _paint_location_dots(root: ET.Element, index: int) -> None:
    """Asset look only: black pill + white dots. Selection never recolors this."""
    container = _find(root, "location_container_icon")
    if container is not None:
        _set_fill(container, _COLOR_BLACK, stroke="none")
    n = max(1, min(3, int(index or 1)))
    for i, eid in enumerate(("location_1_icon", "location_2_icon", "location_3_icon"), start=1):
        el = _find(root, eid)
        if el is None:
            continue
        if i <= n:
            _set_fill(el, _COLOR_WHITE, stroke=_COLOR_BLACK)
        else:
            _set_fill(el, "none", stroke=_COLOR_WHITE)


def _draw_location_column(
    canvas: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    slot_index: int,
    name: str,
    selected: bool,
    ui_hex: str,
    assets_dir: Path | str | None,
) -> None:
    from pigeon.widgets.main_settings import location_room_label

    zx, zy, zw, zh = box
    if zw < 8 or zh < 8:
        return
    label = location_room_label(name, slot_index=slot_index).upper()
    dots = _load_widget("location_icon", assets_dir=assets_dir)
    _paint_location_dots(dots, slot_index)
    dvw, dvh = 88.65, 31.01
    scale = min((zw * 0.58) / dvw, (zh * 0.20) / dvh)
    iw = max(1, int(round(dvw * scale)))
    ih = max(1, int(round(dvh * scale)))
    _place_widget(
        canvas,
        dots,
        x=zx + (zw - iw) // 2,
        y=zy + int(zh * 0.18),
        w=iw,
        h=ih,
    )
    _draw_centered_text(
        canvas,
        label,
        box=(zx + 10, zy + int(zh * 0.46), max(8, zw - 20), max(24, int(zh * 0.36))),
        size=48,
        fill=(0, 0, 0) if selected else (255, 255, 255),
        digital=True,
        fit=True,
    )


def _network_field_boxes(
    net_box: tuple[int, int, int, int],
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """Split dual slot B into the wifi fan (left) and fitted SSID text (right)."""
    x, y, w, h = net_box
    clip_w = 94.46 - 37.8
    clip_h = 56.55 - 7.49
    ih = max(24, h - 4)
    iw = max(24, int(round(ih * clip_w / max(clip_h, 1.0))))
    ix = x + 10
    iy = y + max(0, (h - ih) // 2)
    tx = ix + iw + 8
    tw = max(8, x + w - tx - 12)
    return (ix, iy, iw, ih), (tx, y, tw, h)


def _auth_code_box(loc_box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Full dual-slot A for the pairing prompt (no location dots)."""
    x, y, w, h = loc_box
    return (x + 14, y, max(8, w - 28), h)


def _pin_digits_box(net_box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    """Full dual-slot B for the pairing PIN (no wifi fan)."""
    x, y, w, h = net_box
    return (x + 16, y, max(8, w - 32), h)


def _draw_wifi_fan(
    canvas: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    level: int,
    color_bgr: tuple[int, int, int],
    none: bool,
) -> None:
    """Stroke the three wifi ellipses and clip them to the Illustrator wedge."""
    x, y, w, h = box
    if w < 8 or h < 8:
        return
    xs = [p[0] for p in _WIFI_CLIP_SVG]
    ys = [p[1] for p in _WIFI_CLIP_SVG]
    pad = _WIFI_STROKE_SVG
    src_x0 = min(xs) - pad
    src_y0 = min(ys) - pad
    src_w = max(xs) - min(xs) + 2 * pad
    src_h = max(ys) - min(ys) + 2 * pad
    scale = min(w / src_w, h / src_h)
    pw = max(1, int(round(src_w * scale)))
    ph = max(1, int(round(src_h * scale)))
    ox = x + (w - pw) // 2
    oy = y + (h - ph) // 2
    ss = 4
    pws, phs = pw * ss, ph * ss
    scale_ss = scale * ss

    def _pt(px: float, py: float) -> tuple[int, int]:
        return (
            int(round((px - src_x0) * scale_ss)),
            int(round((py - src_y0) * scale_ss)),
        )

    clip = np.zeros((phs, pws), dtype=np.uint8)
    cv2.fillPoly(
        clip,
        [np.array([_pt(px, py) for px, py in _WIFI_CLIP_SVG], dtype=np.int32)],
        255,
    )
    stroke = max(ss, int(round(_WIFI_STROKE_SVG * scale_ss)))
    rings = np.zeros((phs, pws, 4), dtype=np.uint8)
    cx, cy = _pt(_WIFI_CX_SVG, _WIFI_CY_SVG)
    show_n = 0 if none else max(0, min(3, int(level)))
    for i, radius_svg in enumerate(_WIFI_RADII_SVG):
        if i >= show_n:
            continue
        rr = max(1, int(round(radius_svg * scale_ss)))
        cv2.circle(rings, (cx, cy), rr, (*color_bgr, 255), stroke, lineType=cv2.LINE_AA)
    rings[clip == 0] = 0
    rings = cv2.resize(rings, (pw, ph), interpolation=cv2.INTER_AREA)
    _blit(canvas, rings, ox, oy)
    if none:
        _draw_centered_text(
            canvas,
            "!",
            box=box,
            size=max(18, h - 8),
            fill=(color_bgr[2], color_bgr[1], color_bgr[0]),
            digital=False,
        )


def _draw_yes_no_above_zone1(canvas: np.ndarray, keyboard) -> None:
    """YES/NO confirmation sits in the EXIT band, centered above the dual bar."""
    from pigeon.widgets.settings_keyboard import KeyAction

    z0 = SETTINGS_MAIN_ZONES[0]
    z1 = SETTINGS_MAIN_ZONES[1]
    pill_w, pill_h = 150, 56
    gap = 20
    total = pill_w * 2 + gap
    x = int(round(z1.x + (z1.w - total) * 0.5))
    y = int(round(z0.y + (z0.h - pill_h) * 0.5))
    try:
        act = getattr(getattr(keyboard, "focused", None), "action", None)
    except Exception:
        act = None
    yes_on = act != KeyAction.NO
    for label, selected, px in (("YES", yes_on, x), ("NO", not yes_on, x + pill_w + gap)):
        img = Image.new("RGBA", (pill_w, pill_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        fill = (255, 255, 255, 255) if selected else (26, 26, 26, 255)
        stroke = (0, 0, 0, 255)
        draw.rounded_rectangle(
            (1, 1, pill_w - 2, pill_h - 2),
            radius=pill_h // 2,
            fill=fill,
            outline=stroke,
            width=3,
        )
        patch = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
        _blit(canvas, patch, px, y)
        _draw_centered_text(
            canvas,
            label,
            box=(px, y, pill_w, pill_h),
            size=36,
            fill=(0, 0, 0) if selected else (255, 255, 255),
            digital=True,
            fit=True,
        )


def _exit_root(state, *, assets_dir, selected: bool, label: str) -> ET.Element:
    root = _load_widget("exit", assets_dir=assets_dir)
    btn = _find(root, "exit_button")
    if btn is not None:
        for el in btn.iter():
            if not el.tag.endswith("path"):
                continue
            fill = (el.get("fill") or "").strip()
            if fill == "none":
                continue
            _set_fill(el, _COLOR_WHITE if selected else "#202020")
    # SVG text is also redrawn by the rasterizer — hide it so Pillow is the only label.
    _hide(_find(root, "exit_text"))
    return root


def _star_points(cx: float, cy: float, r: float) -> list[tuple[float, float]]:
    pts: list[tuple[float, float]] = []
    for i in range(10):
        ang = math.radians(-90.0 + i * 36.0)
        rad = r if i % 2 == 0 else r * 0.40
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    return pts


def _draw_list_star(
    canvas: np.ndarray,
    cx: float,
    cy: float,
    r: float,
    fill: tuple[int, int, int],
) -> None:
    rad = max(4, int(round(r)))
    dim = rad * 2 + 4
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    ImageDraw.Draw(img).polygon(
        _star_points(dim / 2.0, dim / 2.0, float(rad)),
        fill=(*fill, 255),
    )
    _blit(
        canvas,
        cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA),
        int(round(cx - dim / 2.0)),
        int(round(cy - dim / 2.0)),
    )


def _row_is_current(name: str, ip: str, current: tuple[str, str] | None) -> bool:
    if current is None or is_special_device_row(name):
        return False
    cur_name, cur_ip = current
    have_ip = str(ip or "").strip()
    want_ip = str(cur_ip or "").strip()
    if have_ip and want_ip:
        return have_ip == want_ip
    return str(name or "").strip().lower() == str(cur_name or "").strip().lower()


def _column_list(
    canvas: np.ndarray,
    zone_index: int,
    rows: list[tuple[str, str]],
    *,
    scroll: int,
    selected_row: int,
    show_arrows: bool,
    assets_dir,
    current: tuple[str, str] | None = None,
) -> None:
    zone = SETTINGS_MAIN_ZONES[zone_index]
    chrome = _load_widget("list", assets_dir=assets_dir)
    if not show_arrows:
        _hide(_find(chrome, "navigation_arrow_group"))
    labels = rows[scroll : scroll + _LIST_PAGE_SIZE]
    for i, letter in enumerate(_ROW_LETTERS):
        cell = _find(chrome, f"sm_02-03-04-{letter}")
        if cell is None:
            continue
        if i >= len(labels):
            _hide(cell)
            continue
        on = i == int(selected_row)
        stroke = _chrome_stroke()
        if on:
            _set_fill(cell, _COLOR_WHITE, stroke=stroke)
        else:
            _set_fill(cell, _COLOR_COLUMN_OFF, stroke=stroke)
    x, y, w, h = zone_center_rect(zone, *_viewbox_wh(chrome))
    _place_widget(
        canvas,
        chrome,
        x=x,
        y=y,
        w=w,
        h=h,
        cache_key=("list", w, h, int(selected_row), len(labels), bool(show_arrows), _ui_bright()),
    )
    sx = w / _LIST_CHROME_VB[0]
    sy = h / _LIST_CHROME_VB[1]
    ip_size = max(8, int(round(_LIST_IP_SIZE_SVG * sy)))
    device_size = max(8, int(round(_LIST_DEVICE_SIZE_SVG * sy)))
    for i in range(len(_ROW_LETTERS)):
        if i >= len(labels):
            continue
        name, ip = labels[i]
        on = i == int(selected_row)
        ink = (0, 0, 0) if on else (255, 255, 255)
        rx, ry, rw, rh = _LIST_ROW_SVG[i]
        box = (
            int(round(x + rx * sx)),
            int(round(y + ry * sy)),
            max(8, int(round(rw * sx))),
            max(8, int(round(rh * sy))),
        )
        pad = max(6, int(round(10 * sx)))
        starred = _row_is_current(str(name), str(ip), current)
        star_r = max(4.0, _LIST_STAR_R_SVG * sy)
        if str(ip or "").strip():
            split = max(int(round(_LIST_NAME_X_SVG * sx)), int(round(box[2] * 0.36)))
            _draw_centered_text(
                canvas,
                str(ip),
                box=(box[0] + pad, box[1], max(8, split - pad), box[3]),
                size=ip_size,
                fill=ink,
                align="left",
            )
            _draw_centered_text(
                canvas,
                str(name or "").upper(),
                box=(box[0] + split, box[1], max(8, box[2] - split - pad), box[3]),
                size=device_size,
                fill=ink,
                align="right",
            )
            if starred:
                _draw_list_star(
                    canvas,
                    box[0] + split - star_r - 2,
                    box[1] + box[3] / 2.0,
                    star_r,
                    (0, 0, 0) if on else (255, 255, 255),
                )
        else:
            extra = int(round(star_r * 2 + 6)) if starred else 0
            _draw_centered_text(
                canvas,
                str(name or "").upper(),
                box=(box[0] + pad + extra, box[1], max(8, box[2] - 2 * pad - extra), box[3]),
                size=device_size,
                fill=ink,
            )
            if starred:
                _draw_list_star(
                    canvas,
                    box[0] + pad + star_r,
                    box[1] + box[3] / 2.0,
                    star_r,
                    (0, 0, 0) if on else (255, 255, 255),
                )


def _hide_embedded_search_icon(root: ET.Element) -> None:
    for el in root.iter():
        eid = (el.get("id") or "").replace("_x5F_", "_").lower()
        if eid.endswith("search_icon") or eid.endswith("searching_icon"):
            _hide(el)
            return


def _panel_shows_list(panel) -> bool:
    if panel is None:
        return False
    if bool(getattr(panel, "scanning", False)) or str(getattr(panel, "phase", "") or "") == "scanning":
        return False
    if bool(getattr(panel, "active", False)):
        return True
    return bool(getattr(panel, "phase", "") == "results")


def _column_shows_list(state, zone_index: int) -> bool:
    if bool(getattr(state, "show_location_picker", False)):
        return False
    if zone_index == 3:
        if bool(getattr(state, "show_network_picker", False)):
            return True
        return _panel_shows_list(state._box_panel(2))
    if zone_index == 4:
        return _panel_shows_list(state._box_panel(3))
    return False


def _scan_fraction(*, started_mono: float, angle_deg: float, duration_s: float) -> float:
    if started_mono > 0.0 and duration_s > 0.0:
        return max(0.0, min(1.0, (time.monotonic() - started_mono) / duration_s))
    return (float(angle_deg) % 360.0) / 360.0


def _draw_search_status_bar(
    canvas: np.ndarray,
    box: tuple[int, int, int, int],
    fraction: float,
    *,
    selected: bool,
) -> None:
    """UPDATE-style pill progress bar, inset so it stays inside ``box``."""
    x, y, w, h = box
    if w < 32 or h < 20:
        return
    pad_x = max(16, int(round(w * 0.12)))
    bar_w = max(24, w - 2 * pad_x)
    bar_h = max(12, min(22, int(round(h * 0.10))))
    if bar_h + 16 > h:
        bar_h = max(10, h - 16)
    bx = x + (w - bar_w) // 2
    by = y + (h - bar_h) // 2
    if bx < x or by < y or bx + bar_w > x + w or by + bar_h > y + h:
        bx = x + pad_x
        by = y + max(8, (h - bar_h) // 2)
        bar_w = min(bar_w, x + w - bx - pad_x)
    radius = max(4, bar_h // 2)
    frac = max(0.0, min(1.0, float(fraction)))
    if selected:
        track = (208, 208, 208, 255)
        fill = (32, 32, 32, 255)
    else:
        track = (74, 74, 74, 255)
        fill = (255, 255, 255, 255)
    img = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, bar_w - 1, bar_h - 1), radius=radius, fill=track)
    fill_w = max(0, int(round(bar_w * frac)))
    if fill_w > 0:
        mask = Image.new("L", (bar_w, bar_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, bar_w - 1, bar_h - 1), radius=radius, fill=255
        )
        layer = Image.new("RGBA", (bar_w, bar_h), (0, 0, 0, 0))
        ImageDraw.Draw(layer).rectangle((0, 0, fill_w - 1, bar_h - 1), fill=fill)
        img.paste(layer, (0, 0), mask)
    patch = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGBA2BGRA)
    _blit(canvas, patch, bx, by)


def _draw_finding_scan(
    canvas: np.ndarray,
    zone_index: int,
    *,
    label: str,
    fraction: float,
    selected: bool,
) -> None:
    """Sharp Sans 'finding …' label plus the scan bar (add-player widget)."""
    name_box, bar_box = _column_line_boxes(zone_index)
    _draw_centered_text(
        canvas,
        label,
        box=name_box,
        size=_FINDING_SIZE_PX,
        fill=_FINDING_RGB,
        digital=False,
        semibold=True,
        fit=True,
    )
    _draw_search_status_bar(canvas, bar_box, fraction, selected=selected)


def clear_settings_main_compose_cache() -> None:
    """Drop incremental frame / widget rasters (tests and theme reloads)."""
    _WIDGET_RASTER_CACHE.clear()
    _THEME_BG_CACHE.clear()
    _LAST_MAIN["structure"] = None
    _LAST_MAIN["focus"] = None
    _LAST_MAIN["frame"] = None


def _ui_bright() -> bool:
    try:
        from pigeon.widgets.options_settings import ui_is_bright

        return bool(ui_is_bright())
    except Exception:
        return False


def _chrome_stroke() -> str:
    return _COLOR_BLACK


def _settings_main_bg(
    *,
    ui_hex: str,
    assets_dir: Path | str | None,
) -> np.ndarray:
    from pigeon.widgets.settings_theme_background import (
        draw_settings_theme_background_bgra,
        settings_background_ui_hex,
    )

    tint = settings_background_ui_hex(ui_hex)
    key = (
        str(tint or "").lower(),
        "bright" if _ui_bright() else "std",
        str(assets_dir or ""),
        int(DESIGN_W),
        int(DESIGN_H),
    )
    cached = _THEME_BG_CACHE.get(key)
    if cached is not None:
        return cached
    canvas = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    canvas[:, :, 3] = 255
    draw_settings_theme_background_bgra(
        canvas,
        ui_hex=ui_hex,
        clip_mask=np.full((DESIGN_H, DESIGN_W), 255, dtype=np.uint8),
        assets_dir=assets_dir,
    )
    while len(_THEME_BG_CACHE) >= _THEME_BG_CACHE_MAX:
        _THEME_BG_CACHE.pop(next(iter(_THEME_BG_CACHE)))
    _THEME_BG_CACHE[key] = canvas
    return canvas


def _restore_layer(canvas: np.ndarray, bg: np.ndarray, zone_index: int) -> None:
    zone = SETTINGS_MAIN_ZONES[zone_index]
    x, y, w, h = (int(v) for v in zone.xywh)
    pad = 8
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(int(canvas.shape[1]), x + w + pad)
    y1 = min(int(canvas.shape[0]), y + h + pad)
    if x1 <= x0 or y1 <= y0:
        return
    canvas[y0:y1, x0:x1] = bg[y0:y1, x0:x1]


def _layer_for_focus(focused: str) -> int | None:
    return _FOCUS_LAYER.get(str(focused or ""))


def _settings_main_focus_sig(state) -> tuple[object, ...]:
    st = state
    focused = "" if st.keyboard_open else st.focused_id
    p2 = st._box_panel(2)
    p3 = st._box_panel(3)
    return (
        str(focused),
        int(getattr(st, "network_picker_row", 0) or 0),
        int(getattr(p2, "row", 0) or 0),
        int(getattr(p3, "row", 0) or 0),
    )


def _settings_main_structure_sig(
    state,
    *,
    assets_dir: Path | str | None,
    skip_text_entry: bool,
) -> tuple[object, ...]:
    st = state
    kb = st.keyboard
    kb_target = str(getattr(kb, "target", "") or "") if kb is not None else ""
    p2 = st._box_panel(2)
    p3 = st._box_panel(3)
    th = st.theme
    return (
        str(getattr(th, "ui", "") or ""),
        str(st.location_name or ""),
        str(st.selected_wifi_ssid or ""),
        str(getattr(st, "live_wifi_ssid", "") or ""),
        str(st.displayed_wifi_ssid() if hasattr(st, "displayed_wifi_ssid") else ""),
        bool(getattr(st, "wifi_logged_out", False)),
        bool(st.wifi_configured),
        bool(st.keyboard_open),
        kb_target,
        bool(skip_text_entry),
        bool(st.show_location_picker),
        bool(st.show_network_picker),
        bool(st.wifi_scanning),
        bool(st.wifi_connecting),
        bool(getattr(st, "location_switch_spinner_visible", lambda: False)()),
        bool(getattr(p2, "scanning", False)),
        bool(getattr(p3, "scanning", False)),
        str(getattr(p2, "phase", "") or ""),
        str(getattr(p3, "phase", "") or ""),
        int(getattr(p2, "scroll", 0) or 0),
        int(getattr(p3, "scroll", 0) or 0),
        int(getattr(st, "network_picker_scroll", 0) or 0),
        tuple(st.wifi_networks or ()),
        tuple(getattr(p2, "devices", ()) or ()),
        tuple(getattr(p3, "devices", ()) or ()),
        tuple(getattr(st, "location_slots", ()) or ()),
        None if getattr(p2, "picked", None) is None else tuple(p2.picked),
        None if getattr(p3, "picked", None) is None else tuple(p3.picked),
        str(assets_dir or ""),
        str(local_ipv4_address() or ""),
        int(getattr(st, "wifi_level", 0) or 0),
        0 if getattr(st, "zone2_tt_bgra", None) is None else 1,
        _ui_bright(),
    )


def _zone_focused(zone_index: int, focused: str) -> bool:
    if zone_index == 2:
        return focused == "main_box1_button"
    if zone_index == 3:
        return focused in {
            "main_box2_button",
            "main_box2_add_search_icon",
            "main_box2_device_results",
            "main_network_picker_button",
        }
    if zone_index == 4:
        return focused in {"main_box3_button", "main_box3_device_results"}
    return False


def _style_column_container(root: ET.Element, *, selected: bool) -> None:
    el = _find(root, "sm_container")
    stroke = _chrome_stroke()
    if selected:
        _set_fill(el, _COLOR_WHITE, stroke=stroke)
    else:
        _set_fill(el, _COLOR_COLUMN_OFF, stroke=stroke)


def _tint_add_art(root: ET.Element, color: str) -> None:
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in ("svg", "defs", "clipPath", "g"):
            continue
        fill = (el.get("fill") or "").strip()
        stroke = (el.get("stroke") or "").strip()
        style = el.get("style") or ""
        if "fill:" in style and "fill: none" not in style.lower() and "fill:none" not in style.lower():
            fill = fill or "#202020"
        if fill and fill != "none":
            _set_fill(el, color, stroke=color if stroke and stroke != "none" else stroke or None)
        elif stroke and stroke != "none":
            _set_fill(el, "none", stroke=color)


def _device_name_ip(state, box_num: int) -> tuple[str, str] | None:
    saved = state.saved_box_device(box_num)
    if saved is None:
        return None
    return str(saved[0] or "").strip(), str(saved[1] or "").strip()


def render_settings_main_1280_bgra(
    state,
    *,
    assets_dir: Path | str | None = None,
    skip_text_entry: bool = False,
) -> np.ndarray:
    """Compose settings main at DESIGN_W×DESIGN_H from the 1280×800 widgets.

    Left/Right only re-rasters the zone that lost focus and the zone that gained
    it (plus list-row neighbors in that column). Unchanged widgets stay put.
    """
    from pigeon.widgets.main_settings import _location_display_text, _network_field_text

    st = state
    try:
        st.refresh_network_ssid()
    except Exception:
        pass
    st.ensure_focus_ring()
    kb = st.keyboard
    kb_target = str(getattr(kb, "target", "") or "") if kb is not None else ""
    logout = bool(st.keyboard_open and kb_target == "wifi_logout")
    focused = "" if st.keyboard_open else st.focused_id
    ui_hex = str(getattr(st.theme, "ui", _COLOR_UI) or _COLOR_UI)
    hide_columns = bool(st.keyboard_open and kb_target not in ("", "wifi_logout"))
    structure = _settings_main_structure_sig(
        st, assets_dir=assets_dir, skip_text_entry=skip_text_entry
    )
    focus_sig = _settings_main_focus_sig(st)
    bg = _settings_main_bg(ui_hex=ui_hex, assets_dir=assets_dir)
    zones: set[int] | None = None
    last_frame = _LAST_MAIN.get("frame")
    can_patch = (
        isinstance(last_frame, np.ndarray)
        and last_frame.shape[:2] == (DESIGN_H, DESIGN_W)
        and _LAST_MAIN.get("structure") == structure
        and not logout
        and not hide_columns
        and not skip_text_entry
        and not (
            st.wifi_scanning
            or st.wifi_connecting
            or bool(getattr(st, "location_switch_spinner_visible", lambda: False)())
            or bool(getattr(st._box_panel(2), "scanning", False))
            or bool(getattr(st._box_panel(3), "scanning", False))
        )
    )
    if can_patch and _LAST_MAIN.get("focus") == focus_sig:
        return last_frame
    if can_patch:
        dirty: set[int] = set()
        old_focus = _LAST_MAIN.get("focus")
        old_id = str(old_focus[0]) if isinstance(old_focus, tuple) and old_focus else ""
        for fid in (old_id, str(focused)):
            layer = _layer_for_focus(fid)
            if layer is None:
                dirty = {0, 1, 2, 3, 4}
                break
            dirty.add(layer)
        canvas = last_frame.copy()
        for z in dirty:
            _restore_layer(canvas, bg, z)
        zones = dirty
    else:
        canvas = bg.copy()

    def _want(zone_index: int) -> bool:
        return zones is None or zone_index in zones

    exit_sel = focused == "main_exit_button"
    if _want(0):
        _place_in_zone(
            canvas,
            _exit_root(st, assets_dir=assets_dir, selected=exit_sel, label="EXIT"),
            0,
            align="center",
            cache_key=("exit", exit_sel),
        )
        _draw_centered_text(
            canvas,
            "EXIT",
            box=SETTINGS_MAIN_ZONES[0].xywh,
            size=46,
            fill=(0, 0, 0) if exit_sel else (255, 255, 255),
        )

    loc_picker = bool(st.show_location_picker)
    a_on = focused == "main_dual_location_button" or kb_target in ("location", "device_name")
    b_on = (
        logout
        or focused in ("main_dual_network_button", "main_network_picker_button")
        or kb_target in ("network", "pin", "device_ip")
    )
    if logout and _want(1):
        _draw_yes_no_above_zone1(canvas, kb)
    if _want(1):
        dual = _load_widget("dual", assets_dir=assets_dir)
        _set_fill(
            _find(dual, "dual_container"),
            "#202020",
            stroke=_chrome_stroke(),
        )
        _set_fill(_find(dual, "dual_button_container_a"), _COLOR_WHITE if a_on else _COLOR_SLOT_OFF)
        _set_fill(_find(dual, "dual_button_container_b"), _COLOR_WHITE if b_on else _COLOR_SLOT_OFF)
        zone1 = SETTINGS_MAIN_ZONES[1]
        zw, zh = int(round(zone1.w)), int(round(zone1.h))
        _place_widget(
            canvas,
            dual,
            x=int(round(zone1.x)),
            y=int(round(zone1.y)),
            w=zw,
            h=zh,
            cache_key=("dual", zw, zh, a_on, b_on, _ui_bright()),
        )

    loc_box = dual_slot_design(DUAL_SLOT_A)
    net_box = dual_slot_design(DUAL_SLOT_B)
    loc_idx = _current_location_index(st)
    loc_label = str(_location_display_text(st) or st.location_name or "").strip()
    kb_buffer = str(getattr(kb, "buffer", "") or "") if kb is not None else ""
    kb_initial = str(getattr(kb, "initial_text", "") or "") if kb is not None else ""
    loc_placeholder = bool(kb_target in ("location", "device_name") and not kb_buffer)
    if kb_target in ("location", "device_name"):
        loc_label = kb_buffer if kb_buffer else (kb_initial or loc_label)
    dots_box, loc_text_box = _location_field_boxes(loc_box)
    if _want(1) and not skip_text_entry:
        if kb_target == "pin":
            _draw_centered_text(
                canvas,
                "AUTHENTICATION CODE",
                box=_auth_code_box(loc_box),
                size=42,
                fill=(255, 255, 255),
                digital=True,
                fit=True,
            )
        else:
            dots = _load_widget("location_icon", assets_dir=assets_dir)
            _paint_location_dots(dots, loc_idx)
            _place_widget(canvas, dots, x=dots_box[0], y=dots_box[1], w=dots_box[2], h=dots_box[3])
            _draw_centered_text(
                canvas,
                loc_label.upper(),
                box=loc_text_box,
                size=48,
                fill=(128, 128, 128)
                if loc_placeholder
                else ((0, 0, 0) if a_on else (255, 255, 255)),
            )

    show_password = bool(st.keyboard_open and kb_target == "network") or bool(st.wifi_connecting)
    if _want(1) and not skip_text_entry:
        if kb_target == "device_ip":
            _wifi_box, net_text_box = _network_field_boxes(net_box)
            _draw_centered_text(
                canvas,
                kb_buffer.upper(),
                box=net_text_box,
                size=48,
                fill=(0, 0, 0) if b_on else (255, 255, 255),
            )
        elif kb_target == "pin":
            _draw_centered_text(
                canvas,
                kb_buffer,
                box=_pin_digits_box(net_box),
                size=48,
                fill=(0, 0, 0),
                digital=True,
                fit=True,
            )
        elif show_password:
            pw = _load_widget("password", assets_dir=assets_dir)
            stars = "*" * max(0, len(str(getattr(st, "pending_network_password", "") or getattr(kb, "buffer", "") or "")))
            _set_text(_find(pw, "password_text"), stars or "")
            lock = _find(pw, "lock")
            if lock is not None:
                for el in lock.iter():
                    eid = el.get("id") or ""
                    if eid == "lock_elipse":
                        _set_fill(el, "none", stroke=ui_hex)
                    elif eid == "lock_rectangle":
                        _set_fill(el, ui_hex, stroke="none")
            pw_w, pw_h = _viewbox_wh(pw)
            scale = min(net_box[2] / pw_w, net_box[3] / pw_h)
            ww, hh = max(1, int(round(pw_w * scale))), max(1, int(round(pw_h * scale)))
            _place_widget(
                canvas,
                pw,
                x=net_box[0] + (net_box[2] - ww) // 2,
                y=net_box[1] + (net_box[3] - hh) // 2,
                w=ww,
                h=hh,
            )
        elif logout:
            from pigeon.widgets.main_settings import _wifi_logout_instruction_text

            _draw_centered_text(
                canvas,
                _wifi_logout_instruction_text(st).upper(),
                box=(net_box[0] + 16, net_box[1], max(8, net_box[2] - 32), net_box[3]),
                size=36,
                fill=(0, 0, 0) if b_on else (255, 255, 255),
                digital=True,
                fit=True,
            )
        else:
            shown = st.displayed_wifi_ssid() if hasattr(st, "displayed_wifi_ssid") else str(st.selected_wifi_ssid or "").strip()
            none = not bool(shown)
            label = _network_field_text(st)
            if none:
                label = "CONNECT"
            elif label in (None, "CONNECTED"):
                label = shown or "CONNECTED"
            wifi_box, net_text_box = _network_field_boxes(net_box)
            _draw_wifi_fan(
                canvas,
                wifi_box,
                level=int(getattr(st, "wifi_level", 3) or 0),
                color_bgr=_hex_to_bgr(ui_hex),
                none=none,
            )
            _draw_centered_text(
                canvas,
                str(label or "").upper(),
                box=net_text_box,
                size=42,
                fill=(0, 0, 0) if b_on else (255, 255, 255),
            )

    column_boxes: dict[int, tuple[int, int, int, int]] = {}
    column_on = {z: _zone_focused(z, focused) for z in (2, 3, 4)}
    if not hide_columns:
        p2 = st._box_panel(2)
        p3 = st._box_panel(3)
        if bool(getattr(p2, "scanning", False)) or str(getattr(p2, "phase", "") or "") == "scanning":
            column_on[3] = True
        if bool(getattr(p3, "scanning", False)) or str(getattr(p3, "phase", "") or "") == "scanning":
            column_on[4] = True
    list_zones = {z: _column_shows_list(st, z) for z in (2, 3, 4)}
    for zone_index in (2, 3, 4):
        if hide_columns:
            column_boxes[zone_index] = SETTINGS_MAIN_ZONES[zone_index].xywh
            continue
        zone = SETTINGS_MAIN_ZONES[zone_index]
        if list_zones.get(zone_index):
            column_boxes[zone_index] = zone.xywh
            continue
        rect = _column_card_rect(zone_index)
        column_boxes[zone_index] = rect
        if not _want(zone_index):
            continue
        container = _load_widget("column_container", assets_dir=assets_dir)
        _style_column_container(container, selected=column_on[zone_index])
        _place_widget(
            canvas,
            container,
            x=rect[0],
            y=rect[1],
            w=rect[2],
            h=rect[3],
            cache_key=("col", rect[2], rect[3], column_on[zone_index], _ui_bright()),
        )

    def _column_text_box(zone_index: int) -> tuple[int, int, int, int]:
        x, y, w, h = column_boxes[zone_index]
        pad = _ZONE_TEXT_PAD
        return (x + pad, y + pad, max(8, w - 2 * pad), max(8, h - 2 * pad))

    def _column_text_rgb(zone_index: int) -> tuple[int, int, int]:
        return (0, 0, 0) if column_on[zone_index] else (255, 255, 255)

    ip = local_ipv4_address() or ""
    if not hide_columns and loc_picker:
        from pigeon.widgets.main_settings import location_room_label

        slots = list(getattr(st, "location_slots", ()) or ())
        for zone_index, slot_i in ((2, 1), (3, 2), (4, 3)):
            if not _want(zone_index):
                continue
            name = ""
            if slot_i - 1 < len(slots):
                name = str(slots[slot_i - 1][1] or "")
            _draw_location_column(
                canvas,
                _column_text_box(zone_index),
                slot_index=slot_i,
                name=location_room_label(name, slot_index=slot_i),
                selected=column_on[zone_index],
                ui_hex=ui_hex,
                assets_dir=assets_dir,
            )
    elif not hide_columns and _want(2):
        _draw_zone2_pigeon(
            canvas,
            _column_text_box(2),
            ip,
            _column_text_rgb(2),
            assets_dir=assets_dir,
            tt_bgra=getattr(st, "zone2_tt_bgra", None),
        )

    def _paint_add_tile(zone_index: int, label: str) -> None:
        ink = _column_text_rgb(zone_index)
        name_box, plus_box = _paired_device_line_boxes(_column_text_box(zone_index))
        _draw_centered_text(
            canvas,
            label,
            box=name_box,
            size=_ZONE_NAME_SIZE,
            fill=ink,
            digital=True,
            fit=True,
        )
        _draw_centered_text(
            canvas,
            "+",
            box=plus_box,
            size=_ZONE_IP_SIZE,
            fill=ink,
            digital=True,
            fit=True,
        )

    def _paint_add_or_device(zone_index: int, box_num: int, add_key: str) -> None:
        if loc_picker:
            return
        panel = st._box_panel(box_num)
        if st.show_network_picker and box_num == 2:
            names = list(st.wifi_networks or ())
            rows = [(n, "") for n in names]
            shown_net = (
                st.displayed_wifi_ssid()
                if hasattr(st, "displayed_wifi_ssid")
                else str(st.selected_wifi_ssid or "").strip()
            )
            _column_list(
                canvas,
                zone_index,
                rows,
                scroll=int(st.network_picker_scroll),
                selected_row=int(st.network_picker_row),
                show_arrows=len(rows) > _LIST_PAGE_SIZE,
                assets_dir=assets_dir,
                current=(shown_net, "") if shown_net else None,
            )
            return
        if bool(getattr(panel, "scanning", False)) or str(getattr(panel, "phase", "") or "") == "scanning":
            _draw_finding_scan(
                canvas,
                zone_index,
                label="finding players" if add_key == "add_player" else "finding audio",
                fraction=_scan_fraction(
                    started_mono=float(getattr(panel, "scan_started_mono", 0.0) or 0.0),
                    angle_deg=float(getattr(panel, "scan_angle_deg", 0.0) or 0.0),
                    duration_s=float(
                        getattr(panel, "scan_duration_s", 0.0) or _BOX_SCAN_MAX_DURATION_S
                    ),
                ),
                selected=column_on[zone_index],
            )
            return
        if panel.phase == "results" or panel.active:
            devices = box_devices_with_special_rows(tuple(panel.devices or ()))
            rows = [(str(n), str(ip or "")) for n, ip in devices]
            current = panel.picked or _device_name_ip(st, box_num)
            _column_list(
                canvas,
                zone_index,
                rows,
                scroll=int(panel.scroll),
                selected_row=int(panel.row),
                show_arrows=len(rows) > _LIST_PAGE_SIZE,
                assets_dir=assets_dir,
                current=current,
            )
            return
        paired = _device_name_ip(st, box_num)
        if paired:
            ink = _column_text_rgb(zone_index)
            name_box, ip_box = _paired_device_line_boxes(_column_text_box(zone_index))
            _draw_centered_text(
                canvas,
                paired[0].upper(),
                box=name_box,
                size=_ZONE_NAME_SIZE,
                fill=ink,
                digital=True,
                fit=True,
            )
            _draw_centered_text(
                canvas,
                paired[1],
                box=ip_box,
                size=_ZONE_IP_SIZE,
                fill=ink,
                digital=True,
                fit=True,
            )
            return
        _paint_add_tile(zone_index, "ADD PLAYER" if add_key == "add_player" else "ADD AUDIO")

    if not hide_columns:
        if _want(3):
            _paint_add_or_device(3, 2, "add_player")
        if _want(4):
            _paint_add_or_device(4, 3, "add_audio")

    loc_spin = bool(getattr(st, "location_switch_spinner_visible", lambda: False)())
    if (
        _want(3)
        and not hide_columns
        and (st.wifi_scanning or st.wifi_connecting or loc_spin)
    ):
        if st.wifi_scanning:
            started = float(getattr(st, "wifi_scan_started_mono", 0.0) or 0.0)
            angle = float(st.wifi_scan_angle_deg)
        elif st.wifi_connecting:
            started = float(getattr(st, "wifi_connect_started_mono", 0.0) or 0.0)
            angle = float(st.wifi_scan_angle_deg)
        else:
            started = float(getattr(st, "location_switch_started_mono", 0.0) or 0.0)
            angle = float(st.location_switch_angle_deg)
        _draw_search_status_bar(
            canvas,
            column_boxes[3],
            _scan_fraction(
                started_mono=started,
                angle_deg=angle,
                duration_s=_WIFI_SCAN_MAX_DURATION_S,
            ),
            selected=column_on[3],
        )

    if not logout and not hide_columns and not skip_text_entry:
        scanning = (
            st.wifi_scanning
            or st.wifi_connecting
            or bool(getattr(st, "location_switch_spinner_visible", lambda: False)())
            or bool(getattr(st._box_panel(2), "scanning", False))
            or bool(getattr(st._box_panel(3), "scanning", False))
        )
        if not scanning:
            _LAST_MAIN["structure"] = structure
            _LAST_MAIN["focus"] = focus_sig
            _LAST_MAIN["frame"] = canvas
    return canvas


def draw_settings_main_text_entry(bgra: np.ndarray, state) -> None:
    """Patch dual-bar field text onto cached chrome (keyboard overlay)."""
    from pigeon.widgets.main_settings import _location_display_text

    loc_box = dual_slot_design(DUAL_SLOT_A)
    net_box = dual_slot_design(DUAL_SLOT_B)
    kb = state.keyboard
    target = str(getattr(kb, "target", "") or "") if kb is not None else ""
    buffer = str(getattr(kb, "buffer", "") or "") if kb is not None else ""
    if state.wifi_connecting and not target:
        target = "network"
    if target == "wifi_logout":
        from pigeon.widgets.main_settings import _wifi_logout_instruction_text

        _draw_centered_text(
            bgra,
            _wifi_logout_instruction_text(state).upper(),
            box=(net_box[0] + 16, net_box[1], max(8, net_box[2] - 32), net_box[3]),
            size=36,
            fill=(0, 0, 0),
            digital=True,
            fit=True,
        )
        return
    if target == "pin":
        _draw_centered_text(
            bgra,
            "AUTHENTICATION CODE",
            box=_auth_code_box(loc_box),
            size=42,
            fill=(255, 255, 255),
            digital=True,
            fit=True,
        )
        _draw_centered_text(
            bgra,
            buffer,
            box=_pin_digits_box(net_box),
            size=48,
            fill=(0, 0, 0),
            digital=True,
            fit=True,
        )
        return
    loc_editing = target in ("location", "device_name")
    kb_initial = str(getattr(kb, "initial_text", "") or "") if kb is not None else ""
    if loc_editing or not target:
        if loc_editing and buffer:
            label = buffer.upper()
            loc_fill = (0, 0, 0)
        elif loc_editing:
            label = (kb_initial or str(_location_display_text(state) or "")).upper()
            loc_fill = (128, 128, 128)
        else:
            label = str(_location_display_text(state) or "").upper()
            loc_fill = (
                (0, 0, 0)
                if state.focused_id == "main_dual_location_button"
                else (255, 255, 255)
            )
        _dots_box, loc_text_box = _location_field_boxes(loc_box)
        _draw_centered_text(
            bgra,
            label,
            box=loc_text_box,
            size=48,
            fill=loc_fill,
        )
    if target in ("network", "pin", "device_ip"):
        shown = ("*" * len(buffer)) if target == "network" else buffer.upper()
        _wifi_box, net_text_box = _network_field_boxes(net_box)
        _draw_centered_text(bgra, shown, box=net_text_box, size=48, fill=(0, 0, 0))
