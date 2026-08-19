"""Idle clock saver: SVG clock + weather chrome with cycling color.

Layout from ``pigeonAssets/clocksaver.svg``:
  • ``tday_month_year_text`` / ``today_month_year_text`` — Sharp Sans Bold date
  • ``hhmmss_text`` — Digital-7 HH:MM:SS
  • ``high_temp`` / ``low_temp`` — daily °F for the configured ZIP
  • ``degrees_left_stroke`` / ``degrees_rifght_stroke`` — stroke-only ° marks
"""

from __future__ import annotations

import copy
import os
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.design import DESIGN_H, DESIGN_W, GRID_COLS, get_grid_geometry
from pigeon.font_paths import (
    resolve_digital7_font,
    resolve_ui_font_bold,
)
from pigeon.weather import DEFAULT_WEATHER_ZIP, ensure_weather
from pigeon.widgets.clock_calendar import _resolve_display_time

# Large saver time band (legacy callers); full-frame SVG is preferred now.
_CLOCK_SAVER_TIME_ROW_TOP_1BASED = 2
_CLOCK_SAVER_TIME_ROW_END_1BASED = 7

_TIME_FORMAT = "%H:%M:%S"
_DATE_FORMAT = "%A, %B %-d" if os.name != "nt" else "%A, %B %#d"

_TIME_COLOR_SEGMENT_S = 10.0
_TRANSLATE_RE = re.compile(
    r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)",
    re.IGNORECASE,
)

_SVG_TREE_TEMPLATES: dict[tuple[str, int], ET.Element] = {}
_EMPTY_PATCH = np.zeros((1, 1, 4), dtype=np.uint8)

# Artboard center for centered date / time labels (clocksaver.svg viewBox).
_ARTBOARD_CX = 805.89 * 0.5
_ARTBOARD_W = 805.89
_ARTBOARD_H = 481.0
# Date baseline; weather sits just beneath it (centered). Digital-7 mid-line
# stays on the original band (do not follow the relocated weather).
_DATE_BASELINE_Y_SVG = 91.98
_WEATHER_GAP_BELOW_DATE_SVG = 24.0
# Weather cluster only — date / Digital-7 stay put.
_WEATHER_SCALE = 0.85  # 15% smaller
_WEATHER_ICON_TOP_SVG = 324.51  # legacy lower bound for time mid only
_HHMMSS_MID_Y_SVG = (_DATE_BASELINE_Y_SVG + _WEATHER_ICON_TOP_SVG) * 0.5
_HHMMSS_FONT_SIZE_SVG = 231.0
# Baked ° / number relationship from clocksaver.svg (high_temp ↔ degrees_left).
_TEMP_BASELINE_BELOW_DEG_SVG = 338.18 - 310.7
_TEMP_END_LEFT_OF_DEG_SVG = 2.75
_TEMP_FONT_SIZE_SVG = 31.93
_HHMMSS_CHAR_SET = "0123456789:"
# Digital-7 “2-pixel” glyphs — only use the middle column of a matching cell.
_HHMMSS_SKINNY_CHARS = frozenset({"1", ":"})
# Worst-case ``HH:MM:SS`` width in matching cells: 6 full digits + 2 half-colons.
_HHMMSS_MATCHING_UNITS = 7.0
# Slight horizontal inset so the block stays inside the plate.
_HHMMSS_SIDE_PAD_PX = 28
# Max fraction of design width the time block may occupy.
_HHMMSS_MAX_WIDTH_FRAC = 0.92


def _time_color_rgba(now_mono: float) -> tuple[int, int, int, int]:
    """Hold current color 10s, then 10s transition to the next; timeline starts on white."""
    import colorsys

    H = float(_TIME_COLOR_SEGMENT_S)
    t = max(0.0, float(now_mono))
    seg = int(t // H)
    u = (t % H) / H if H > 0 else 0.0

    def _rgb_block(k: int) -> tuple[float, float, float]:
        if k <= 0:
            return (1.0, 1.0, 1.0)
        hue = ((k - 1) * 0.3021688479) % 1.0
        return colorsys.hsv_to_rgb(hue, 0.72, 0.96)

    if seg % 2 == 0:
        r, g, b = _rgb_block(seg // 2)
        return (int(round(r * 255)), int(round(g * 255)), int(round(b * 255)), 255)
    a = _rgb_block(seg // 2)
    b = _rgb_block(seg // 2 + 1)
    r = a[0] + (b[0] - a[0]) * u
    g = a[1] + (b[1] - a[1]) * u
    bl = a[2] + (b[2] - a[2]) * u
    return (int(round(r * 255)), int(round(g * 255)), int(round(bl * 255)), 255)


def _rgba_to_hex(rgba: tuple[int, int, int, int]) -> str:
    return f"#{rgba[0]:02x}{rgba[1]:02x}{rgba[2]:02x}"


def default_clock_saver_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_CLOCK_SAVER_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "clocksaver.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "clocksaver.svg"


def _normalize_logical(raw_id: str) -> str:
    if not raw_id:
        return ""
    s = str(raw_id)
    for a, b in (
        ("_x5F_", "_"),
        ("_x2D_", "-"),
        ("%20", " "),
    ):
        s = s.replace(a, b)
    return s.strip().lower()


def _find_by_logical_id(root: ET.Element, *logical_ids: str) -> ET.Element | None:
    want = {_normalize_logical(x) for x in logical_ids if x}
    for el in root.iter():
        lid = _normalize_logical(el.get("id") or "")
        if lid in want:
            return el
        dname = _normalize_logical(el.get("data-name") or "")
        if dname in want:
            return el
    return None


def _set_flat_text(text_el: ET.Element | None, value: str) -> None:
    if text_el is None:
        return
    for child in list(text_el):
        text_el.remove(child)
    text_el.text = value


def _set_translate(el: ET.Element, x: float, y: float) -> None:
    el.set("transform", f"translate({x:.2f} {y:.2f})")


def _parse_translate_xy(el: ET.Element) -> tuple[float, float]:
    transform = el.get("transform") or ""
    match = _TRANSLATE_RE.search(transform)
    if match:
        return float(match.group(1)), float(match.group(2) or 0.0)
    return 0.0, 0.0


def _parse_translate_y(el: ET.Element) -> float:
    return _parse_translate_xy(el)[1]


def _svg_float(el: ET.Element, name: str, default: float = 0.0) -> float:
    raw = (el.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _weather_local_bounds(
    weather_el: ET.Element,
) -> tuple[float, float, float, float] | None:
    """Axis-aligned bounds of weather children in artboard space (pre-group xform)."""
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def _add(x0: float, y0: float, x1: float, y1: float) -> None:
        nonlocal min_x, min_y, max_x, max_y
        min_x = min(min_x, x0, x1)
        max_x = max(max_x, x0, x1)
        min_y = min(min_y, y0, y1)
        max_y = max(max_y, y0, y1)

    for el in weather_el.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag == "text":
            tx, ty = _parse_translate_xy(el)
            fs = _svg_float(el, "font-size", 32.0)
            label = "".join(el.itertext()).strip() or "88"
            # Sharp Sans digits are roughly square; pad for degree mark overhang.
            w = max(fs * 0.62 * len(label), fs)
            anchor = (el.get("text-anchor") or "start").strip().lower()
            if anchor == "end":
                _add(tx - w, ty - fs * 0.9, tx, ty + fs * 0.2)
            elif anchor in ("middle", "center"):
                _add(tx - w * 0.5, ty - fs * 0.9, tx + w * 0.5, ty + fs * 0.2)
            else:
                _add(tx, ty - fs * 0.9, tx + w, ty + fs * 0.2)
        elif tag == "circle":
            cx = _svg_float(el, "cx")
            cy = _svg_float(el, "cy")
            r = _svg_float(el, "r", 3.0)
            _add(cx - r, cy - r, cx + r, cy + r)
        elif tag == "line":
            _add(
                _svg_float(el, "x1"),
                _svg_float(el, "y1"),
                _svg_float(el, "x2"),
                _svg_float(el, "y2"),
            )
    if min_x == float("inf"):
        return None
    return min_x, min_y, max_x, max_y


def _anchor_temp_to_degree(
    text_el: ET.Element | None, degree_el: ET.Element | None
) -> None:
    """Right-align a temp label to its ° circle (numbers follow the slash marks)."""
    if text_el is None or degree_el is None:
        return
    cx = _svg_float(degree_el, "cx")
    cy = _svg_float(degree_el, "cy")
    text_el.set("text-anchor", "end")
    _set_translate(
        text_el,
        cx - _TEMP_END_LEFT_OF_DEG_SVG,
        cy + _TEMP_BASELINE_BELOW_DEG_SVG,
    )


def _place_weather_under_date(root: ET.Element) -> None:
    """Center the weather cluster under the date and scale it by ``_WEATHER_SCALE``.

    Shapes (°, slash) honor the weather ``<g transform>``. Pillow text overlay
    ignores parent group transforms, so temp labels get absolute translate +
    scaled ``font-size`` baked in.
    """
    weather = _find_by_logical_id(root, "weather")
    if weather is None:
        return
    bounds = _weather_local_bounds(weather)
    if bounds is None:
        return
    min_x, min_y, max_x, _max_y = bounds
    cx = (min_x + max_x) * 0.5
    target_top = _DATE_BASELINE_Y_SVG + _WEATHER_GAP_BELOW_DATE_SVG
    target_cx = _ARTBOARD_CX
    s = float(_WEATHER_SCALE)
    # Scale about the cluster's top-center, then park that point under the date.
    weather.set(
        "transform",
        (
            f"translate({target_cx:.2f} {target_top:.2f}) "
            f"scale({s}) "
            f"translate({-cx:.2f} {-min_y:.2f})"
        ),
    )
    for lid in ("high_temp", "low_temp"):
        el = _find_by_logical_id(root, lid)
        if el is None:
            continue
        tx, ty = _parse_translate_xy(el)
        _set_translate(
            el,
            target_cx + (tx - cx) * s,
            target_top + (ty - min_y) * s,
        )
        fs = _svg_float(el, "font-size", _TEMP_FONT_SIZE_SVG)
        el.set("font-size", f"{fs * s:.2f}")


def _set_visible(el: ET.Element | None, visible: bool) -> None:
    if el is None:
        return
    if visible:
        el.attrib.pop("display", None)
        style = el.get("style") or ""
        style = re.sub(r"display\s*:\s*none;?", "", style, flags=re.I).strip().strip(";")
        if style:
            el.set("style", style)
        elif "style" in el.attrib:
            el.attrib.pop("style")
    else:
        el.set("display", "none")


def _paint_cycle_color(root: ET.Element, color_hex: str) -> None:
    """Retint fills/strokes to the cycle color; keep knock-out blacks and empty fills."""
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1].lower()
        if tag in ("svg", "g", "defs", "clippath", "mask"):
            continue
        lid = _normalize_logical(el.get("id") or "")
        if lid in ("layer_1",) or lid.startswith("layer_"):
            continue
        fill = (el.get("fill") or "").strip().lower()
        stroke = (el.get("stroke") or "").strip().lower()
        # Background plate stays black / hidden separately.
        if tag == "rect" and fill in ("#040603", "#000", "#000000"):
            continue
        # Crescent cutout stays opaque black so the moon reads on any cycle color.
        if fill in ("#000", "#000000", "black") and stroke in ("", "none"):
            continue
        if fill and fill not in ("none", "transparent"):
            el.set("fill", color_hex)
        if stroke and stroke not in ("none", "transparent"):
            el.set("stroke", color_hex)
        if tag == "text":
            el.set("fill", color_hex)


def _ensure_degree_strokes(root: ET.Element, color_hex: str) -> None:
    """Force ° ellipses to stroke-only (no fill), matching the art direction."""
    for lid in (
        "degrees_left_stroke",
        "degrees_right_stroke",
        "degrees_rifght_stroke",  # Illustrator typo in export
    ):
        el = _find_by_logical_id(root, lid)
        if el is None:
            continue
        el.set("fill", "none")
        el.set("stroke", color_hex)
        if not (el.get("stroke-width") or "").strip():
            el.set("stroke-width", "3")


def _date_label(now) -> str:
    # Prefer "Monday, August 18" (no leading zero on day).
    try:
        return now.strftime(_DATE_FORMAT)
    except ValueError:
        return now.strftime("%A, %B %d").replace(" 0", " ")


def _svg_tree_from_path(path: Path) -> ET.Element:
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        tree = ET.parse(path)
        root = tree.getroot()
        # Fit Illustrator artboard to design canvas.
        root.set("viewBox", "0 0 805.89 481")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _apply_clock_saver_svg_state(root: ET.Element, *, color_hex: str) -> None:
    now = _resolve_display_time()
    date_el = _find_by_logical_id(
        root, "today_month_year_text", "tday_month_year_text"
    )
    time_el = _find_by_logical_id(root, "hhmmss_text")
    high_el = _find_by_logical_id(root, "high_temp")
    low_el = _find_by_logical_id(root, "low_temp")

    date_text = _date_label(now)
    temps = ensure_weather(zip_code=DEFAULT_WEATHER_ZIP)
    high_s = f"{temps.high_f}" if temps is not None else "--"
    low_s = f"{temps.low_f}" if temps is not None else "--"

    if date_el is not None:
        _set_flat_text(date_el, date_text)
        _set_translate(date_el, _ARTBOARD_CX, _parse_translate_y(date_el) or 91.98)
        date_el.set("text-anchor", "middle")
        date_el.set("font-family", "SharpSans-Bold, 'Sharp Sans'")
        date_el.set("font-weight", "700")
    if time_el is not None:
        # Drawn in Pillow with fixed-width cells (Digital-7 glyphs are not tabular).
        _set_visible(time_el, False)
    if high_el is not None:
        _set_flat_text(high_el, high_s)
    if low_el is not None:
        _set_flat_text(low_el, low_s)
    # Keep numbers locked to their ° marks / slash; then center the whole cluster.
    _anchor_temp_to_degree(
        high_el, _find_by_logical_id(root, "degrees_left_stroke")
    )
    _anchor_temp_to_degree(
        low_el,
        _find_by_logical_id(root, "degrees_right_stroke", "degrees_rifght_stroke"),
    )
    _place_weather_under_date(root)

    # Hide baked black plate — callers already paint a black stage.
    _set_visible(_find_by_logical_id(root, "Layer_1", "layer_1"), False)
    for el in list(root):
        if el.tag.endswith("rect"):
            fill = (el.get("fill") or "").strip().lower()
            if fill in ("#040603", "#000", "#000000"):
                _set_visible(el, False)

    _paint_cycle_color(root, color_hex)
    _ensure_degree_strokes(root, color_hex)


def _apply_layer_opacity(bgra: np.ndarray, op: float) -> np.ndarray:
    o = max(0.0, min(1.0, float(op)))
    if o >= 0.999:
        return bgra
    out = bgra.astype(np.float32)
    out[:, :, 3] *= o
    return np.clip(out, 0, 255).astype(np.uint8)


def _load_font(path: str | None, size: int) -> ImageFont.ImageFont:
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    return ImageFont.load_default()


def _char_bbox(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, ch: str
) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), ch, font=font)


def _cell_metrics(
    draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, char_set: str
) -> tuple[int, int]:
    """Matching cell pitch from digit ``0``; height from the full clock charset.

    Most Digital-7 digits fill a 1×2 faux-pixel rectangle. Skinny glyphs
    (``1``, ``:``) only use the middle column; layout crops 25% off each side
    of their matching cell so they advance at half width.
    """
    l0, _t0, r0, _b0 = _char_bbox(draw, font, "0")
    cell_w = max(1, r0 - l0)
    max_h = 1
    for ch in char_set:
        _l, t, _r, b = _char_bbox(draw, font, ch)
        max_h = max(max_h, b - t)
    return cell_w, max_h


def _hhmmss_advance(matching_w: int, ch: str) -> int:
    """Horizontal advance for one clock glyph in matching-cell units.

    Full digits keep the matching width. Two-pixel glyphs (``1``, ``:``) crop
    25% from each side of that cell, so they occupy 50% of the matching width.
    """
    w = max(1, int(matching_w))
    if ch in _HHMMSS_SKINNY_CHARS:
        return max(1, int(round(0.5 * w)))
    return w


def _hhmmss_block_width(matching_w: int, text: str) -> int:
    return sum(_hhmmss_advance(matching_w, ch) for ch in text)


def _parse_hhmmss_pairs(time_text: str) -> tuple[str, str, str]:
    """Split a clock string into ``(HH, MM, SS)`` digit pairs."""
    s = str(time_text or "").strip() or "00:00:00"
    if len(s) >= 8 and s[2] == ":" and s[5] == ":":
        return s[0:2], s[3:5], s[6:8]
    digits = [c for c in s if c.isdigit()]
    while len(digits) < 6:
        digits.append("0")
    d = "".join(digits[:6])
    return d[0:2], d[2:4], d[4:6]


def _hhmmss_pair_width(matching_w: int, pair: str) -> int:
    return sum(_hhmmss_advance(matching_w, ch) for ch in pair)


def _hhmmss_locked_scaffold(
    matching_w: int, canvas_w: int
) -> tuple[
    tuple[tuple[int, int], tuple[int, int], tuple[int, int]],
    tuple[int, int],
]:
    """Fixed colon anchors and HH/MM/SS bands from a full-digit scaffold.

    Scaffold (centered on ``canvas_w``)::

        [HH slot = 2 full][colon half][MM slot][colon half][SS slot]

    Colons stay at absolute X forever. Each digit pair is later centered as a
    group inside its ``[left, right)`` band.
    """
    mw = max(1, int(matching_w))
    colon_w = _hhmmss_advance(mw, ":")
    pair_slot = 2 * mw
    block_w = 3 * pair_slot + 2 * colon_w
    block_x = (int(canvas_w) - block_w) // 2
    c0 = block_x + pair_slot
    c1 = c0 + colon_w + pair_slot
    regions = (
        (block_x, c0),
        (c0 + colon_w, c1),
        (c1 + colon_w, block_x + block_w),
    )
    # Glyph center X for each locked colon (half-width slot).
    colon_cx = (c0 + colon_w // 2, c1 + colon_w // 2)
    return regions, colon_cx


def _fit_digital7_fixed_cells(
    path: str | None,
    matching_units: float,
    *,
    max_w: int,
    max_h: int,
    prefer_sz: int,
    min_sz: int = 12,
) -> ImageFont.ImageFont:
    """Largest Digital-7 size that fits ``matching_units`` matching cells in the box."""
    if max_w < 4 or max_h < 4 or matching_units <= 0:
        return _load_font(path, min_sz)
    lo, hi = min_sz, max(prefer_sz, max_h, 24)
    best = _load_font(path, min_sz)
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    while lo <= hi:
        mid = (lo + hi) // 2
        f = _load_font(path, mid)
        cw, ch = _cell_metrics(probe, f, _HHMMSS_CHAR_SET)
        if matching_units * cw <= max_w and ch <= max_h:
            best = f
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _draw_pair_in_region(
    draw: ImageDraw.ImageDraw,
    *,
    pair: str,
    region: tuple[int, int],
    matching_w: int,
    cy: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
) -> None:
    """Center a two-digit group horizontally inside ``region`` (colon boundaries)."""
    left, right = region
    span = max(1, right - left)
    pair_w = _hhmmss_pair_width(matching_w, pair)
    x = left + max(0, (span - pair_w) // 2)
    for glyph in pair:
        adv = _hhmmss_advance(matching_w, glyph)
        cx = x + adv // 2
        draw.text((cx, cy), glyph, font=font, fill=color, anchor="mm")
        x += adv


def _draw_hhmmss_fixed_cells(
    bgra: np.ndarray,
    time_text: str,
    *,
    color: tuple[int, int, int, int],
) -> None:
    """Paint ``HH:MM:SS`` as three pairs between two screen-locked colons.

    Matching cell widths still apply (skinny ``1`` / ``:`` at half width).
    Colon X positions come from a full-digit scaffold and never move. Each of
    HH, MM, and SS shifts as a group to stay centered in the band the colons
    (and outer edges) define.
    """
    pairs = _parse_hhmmss_pairs(time_text)
    font_path = resolve_digital7_font() or resolve_ui_font_bold()
    sy = float(DESIGN_H) / _ARTBOARD_H
    prefer_sz = max(24, int(round(_HHMMSS_FONT_SIZE_SVG * sy)))
    mid_y = int(round(_HHMMSS_MID_Y_SVG * sy))
    max_w = max(
        64,
        int(round(DESIGN_W * _HHMMSS_MAX_WIDTH_FRAC)) - 2 * _HHMMSS_SIDE_PAD_PX,
    )
    max_h = max(24, int(round(prefer_sz * 1.15)))
    font = _fit_digital7_fixed_cells(
        font_path,
        _HHMMSS_MATCHING_UNITS,
        max_w=max_w,
        max_h=max_h,
        prefer_sz=prefer_sz,
    )
    rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
    img = Image.fromarray(rgba)
    draw = ImageDraw.Draw(img)
    matching_w, _cell_h = _cell_metrics(draw, font, _HHMMSS_CHAR_SET)
    regions, colon_cx = _hhmmss_locked_scaffold(matching_w, DESIGN_W)
    cy = mid_y

    for region, pair in zip(regions, pairs):
        _draw_pair_in_region(
            draw,
            pair=pair,
            region=region,
            matching_w=matching_w,
            cy=cy,
            font=font,
            color=color,
        )
    for cx in colon_cx:
        draw.text((cx, cy), ":", font=font, fill=color, anchor="mm")

    out = np.asarray(img)
    bgra[:, :, 0] = out[:, :, 2]
    bgra[:, :, 1] = out[:, :, 1]
    bgra[:, :, 2] = out[:, :, 0]
    bgra[:, :, 3] = out[:, :, 3]


def clock_saver_time_design_rect() -> tuple[int, int, int, int]:
    """Pixel rect for large saver time: full grid width, rows 2 .. top of row 7."""
    g = get_grid_geometry()
    cell = g.cell
    x = g.x0
    w = GRID_COLS * cell
    r_top = int(_CLOCK_SAVER_TIME_ROW_TOP_1BASED)
    r_end = int(_CLOCK_SAVER_TIME_ROW_END_1BASED)
    r_top = max(1, r_top)
    r_end = max(r_top + 1, r_end)
    y = g.y0 + (r_top - 1) * cell
    h_time = max(1, (r_end - r_top) * cell)
    return (x, y, w, h_time)


def render_clock_saver_bgra(
    *,
    layer_opacity: float = 1.0,
    assets_dir: Path | str | None = None,
    svg_path: Path | str | None = None,
) -> np.ndarray:
    """Full 800×480 BGRA clock saver frame (transparent outside chrome)."""
    path = (
        Path(svg_path)
        if svg_path is not None
        else default_clock_saver_svg_path(assets_dir)
    )
    color = _time_color_rgba(time.monotonic())
    color_hex = _rgba_to_hex(color)
    time_text = _resolve_display_time().strftime(_TIME_FORMAT)
    if not path.is_file():
        # Soft fallback: time-only band if art is missing.
        return _legacy_time_only_bgra(
            color=color, layer_opacity=layer_opacity, time_text=time_text
        )

    root = _svg_tree_from_path(path)
    _apply_clock_saver_svg_state(root, color_hex=color_hex)
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    bgra = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )
    _draw_hhmmss_fixed_cells(bgra, time_text, color=color)
    return _apply_layer_opacity(bgra, layer_opacity)


def _legacy_time_only_bgra(
    *,
    color: tuple[int, int, int, int],
    layer_opacity: float,
    time_text: str | None = None,
) -> np.ndarray:
    bgra = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    text = time_text or _resolve_display_time().strftime(_TIME_FORMAT)
    _draw_hhmmss_fixed_cells(bgra, text, color=color)
    return _apply_layer_opacity(bgra, layer_opacity)


def clock_saver_composite_bgra(
    *,
    shadow_bgr: tuple[int, int, int] | None,
    layer_opacity: float = 1.0,
    time_layer_opacity: float | None = None,
    date_layer_opacity: float | None = None,
    date_anchor_row: int | None = None,
    date_anchor_col: int | None = None,
) -> tuple[
    tuple[np.ndarray, tuple[int, int, int, int]],
    tuple[np.ndarray, tuple[int, int, int, int]],
]:
    """
    Full-frame clock+weather saver.

    Returns ``(full_frame, empty_date)`` so existing compose call sites that
    blit both packs keep working. ``shadow_bgr`` / date anchors are unused
    (SVG art is flat color-cycled chrome).
    """
    _ = (shadow_bgr, date_layer_opacity, date_anchor_row, date_anchor_col)
    t_op = float(layer_opacity if time_layer_opacity is None else time_layer_opacity)
    frame = render_clock_saver_bgra(layer_opacity=t_op)
    full_rect = (0, 0, int(DESIGN_W), int(DESIGN_H))
    return (frame, full_rect), (_EMPTY_PATCH.copy(), (0, 0, 1, 1))


def clear_clock_saver_render_caches() -> None:
    _SVG_TREE_TEMPLATES.clear()
