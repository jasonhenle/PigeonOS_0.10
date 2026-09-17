"""Idle clock saver: SVG clock + weather chrome with cycling color.

Layout from ``pigeonAssets/clocksaver.svg``:
  • ``tday_month_year_text`` / ``today_month_year_text`` — Sharp Sans Bold date
  • ``hhmmss_text`` — Digital-7 HH:MM:SS
  • Digital only: centered volume line + Digital-7 level between temps and HH:MM:SS
  • ``high_temp`` / ``low_temp`` — daily °F for the configured ZIP
  • ``degrees_left_stroke`` / ``degrees_rifght_stroke`` — stroke-only ° marks
  • Digital only: zone-5 NP status track split into 60 second cells
"""

from __future__ import annotations

import copy
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.design import (
    DESIGN_H,
    DESIGN_W,
    LEGACY_DESIGN_H,
    LEGACY_DESIGN_W,
    GRID_COLS,
    get_grid_geometry,
    legacy_fit_origin,
    legacy_fit_scale,
)
from pigeon.font_paths import (
    resolve_digital7_font,
    resolve_ui_font_bold,
)
from pigeon.weather import DEFAULT_WEATHER_ZIP, ensure_weather
from pigeon.widgets.clock_calendar import _resolve_display_time

# Large saver time band (legacy callers); full-frame SVG is preferred now.
_CLOCK_SAVER_TIME_ROW_TOP_1BASED = 2
_CLOCK_SAVER_TIME_ROW_END_1BASED = 7

_TIME_FORMAT_24 = "%H:%M:%S"
_TIME_FORMAT_12 = "%I:%M:%S"
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
# Weather cluster only — date stays put; Digital-7 sits on the seconds bar.
# Sized to fill the band under the date while leaving room for the volume line.
_WEATHER_SCALE = 1.90
_WEATHER_ICON_TOP_SVG = 324.51  # legacy lower bound for older mid-band checks
_HHMMSS_MID_Y_SVG = (_DATE_BASELINE_Y_SVG + _WEATHER_ICON_TOP_SVG) * 0.5
_HHMMSS_FONT_SIZE_SVG = 231.0
# Gap from Digital-7 baseline down to the zone-5 seconds track.
_HHMMSS_GAP_ABOVE_BAR_PX = 12
# Baked ° / number relationship from clocksaver.svg (high_temp ↔ degrees_left).
_TEMP_BASELINE_BELOW_DEG_SVG = 338.18 - 310.7
_TEMP_END_LEFT_OF_DEG_SVG = 2.75
_TEMP_FONT_SIZE_SVG = 31.93
_HHMMSS_CHAR_SET = "0123456789:"
# Digital-7 “2-pixel” glyphs — only use the middle column of a matching cell.
# Digits (including ``1``) still occupy a full matching cell so the clock width
# does not shrink. Only the colon slot is half-width.
_HHMMSS_SKINNY_CHARS = frozenset({":"})
# Worst-case ``HH:MM:SS`` width in matching cells: 6 full digits + 2 half-colons.
_HHMMSS_MATCHING_UNITS = 7.0
# Slight horizontal inset so the block stays inside the plate.
_HHMMSS_SIDE_PAD_PX = 28
# Max fraction of design width the time block may occupy.
_HHMMSS_MAX_WIDTH_FRAC = 0.92

# Volume line between weather and HH:MM:SS — width grows from the center with level.
# Idle saver uses a larger Digital-7 size to fill the band under the temps.
_VOLUME_FONT_SIZE_PX = 72
_CLOCK_SAVER_VOLUME_FONT_SIZE_PX = 110
_VOLUME_LINE_H_PX = 8
_CLOCK_SAVER_VOLUME_LINE_H_PX = 12
_VOLUME_GAP_ABOVE_TIME_PX = 20
_VOLUME_TEXT_PAD_X_PX = 24
# Keep weather / volume / time from kissing (Digital-7 hangs below ``mm``).
_VOLUME_CLEARANCE_PX = 12


def _color_ink_mask(arr: np.ndarray, *, thresh: int = 40) -> np.ndarray:
    if arr.ndim != 3 or arr.shape[2] < 3:
        return np.zeros(arr.shape[:2], dtype=bool)
    return arr[:, :, :3].max(axis=2) > int(thresh)


def _anchor_mm_extents(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> tuple[int, int, int]:
    """``(above, below, width)`` relative to an ``anchor='mm'`` origin."""
    tb = draw.textbbox((0, 0), text, font=font, anchor="mm")
    above = max(0, -int(tb[1]))
    below = max(0, int(tb[3]))
    width = max(1, int(tb[2] - tb[0]))
    return above, below, width


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


def _place_weather_under_date(root: ET.Element) -> float:
    """Center the weather cluster under the date and scale it by ``_WEATHER_SCALE``.

    Shapes (°, slash) honor the weather ``<g transform>``. Pillow text overlay
    ignores parent group transforms, so temp labels get absolute translate +
    scaled ``font-size`` baked in.

    Returns the cluster's lowest artboard Y after placement (for the volume line).
    """
    fallback = _DATE_BASELINE_Y_SVG + _WEATHER_GAP_BELOW_DATE_SVG + 48.0
    weather = _find_by_logical_id(root, "weather")
    if weather is None:
        return fallback
    bounds = _weather_local_bounds(weather)
    if bounds is None:
        return fallback
    min_x, min_y, max_x, max_y = bounds
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
    return target_top + max(0.0, max_y - min_y) * s


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


def clock_saver_weather_bottom_svg(root: ET.Element) -> float:
    """Lowest SVG-space Y of the high/low temp numerals (after placement)."""
    bottom = 0.0
    for lid in ("high_temp", "low_temp"):
        el = _find_by_logical_id(root, lid)
        if el is None:
            continue
        _tx, ty = _parse_translate_xy(el)
        fs = _svg_float(el, "font-size", _TEMP_FONT_SIZE_SVG)
        bottom = max(bottom, ty + fs * 0.25)
    if bottom <= 0.0:
        return _DATE_BASELINE_Y_SVG + _WEATHER_GAP_BELOW_DATE_SVG + 48.0
    return bottom


def clock_saver_svg_y_to_design_y(y_svg: float, canvas_h: int = DESIGN_H) -> float:
    """Map clocksaver.svg artboard Y onto the letterboxed design canvas."""
    y_legacy = float(y_svg) * (float(LEGACY_DESIGN_H) / _ARTBOARD_H)
    _ox, oy = legacy_fit_origin()
    # ``legacy_fit_origin`` assumes DESIGN_H; scale if the canvas differs.
    s = legacy_fit_scale() * (float(canvas_h) / float(DESIGN_H))
    oy = oy * (float(canvas_h) / float(DESIGN_H))
    return oy + y_legacy * s


def _date_label(now) -> str:
    # Prefer "Monday, August 18" (no leading zero on day).
    try:
        return now.strftime(_DATE_FORMAT)
    except ValueError:
        return now.strftime("%A, %B %d").replace(" 0", " ")


def _time_label(now) -> str:
    try:
        from pigeon.widgets.options_settings import clock_uses_24h

        use_24 = bool(clock_uses_24h())
    except Exception:
        use_24 = False
    if use_24:
        return now.strftime(_TIME_FORMAT_24)
    label = now.strftime(_TIME_FORMAT_12)
    return label[1:] if label.startswith("0") else label


def _format_temp_f(temp_f: int) -> str:
    try:
        from pigeon.widgets.options_settings import temp_uses_celsius

        if temp_uses_celsius():
            return str(int(round((float(temp_f) - 32.0) * 5.0 / 9.0)))
    except Exception:
        pass
    return str(int(temp_f))


def _svg_tree_from_path(path: Path) -> ET.Element:
    path = Path(path)
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        tree = ET.parse(path)
        root = tree.getroot()
        # Fit Illustrator artboard; leftover screens letterbox into 1280×800.
        root.set("viewBox", "0 0 805.89 481")
        root.set("width", str(LEGACY_DESIGN_W))
        root.set("height", str(LEGACY_DESIGN_H))
        _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _apply_clock_saver_svg_state(
    root: ET.Element,
    *,
    color_hex: str,
    include_weather: bool = True,
    refresh_weather: bool = True,
) -> float:
    now = _resolve_display_time()
    date_el = _find_by_logical_id(
        root, "today_month_year_text", "tday_month_year_text"
    )
    time_el = _find_by_logical_id(root, "hhmmss_text")
    high_el = _find_by_logical_id(root, "high_temp")
    low_el = _find_by_logical_id(root, "low_temp")

    date_text = _date_label(now)

    if date_el is not None:
        _set_flat_text(date_el, date_text)
        _set_translate(date_el, _ARTBOARD_CX, _parse_translate_y(date_el) or 91.98)
        date_el.set("text-anchor", "middle")
        date_el.set("font-family", "SharpSans-Bold, 'Sharp Sans'")
        date_el.set("font-weight", "700")
    if time_el is not None:
        # Drawn in Pillow with fixed-width cells (Digital-7 glyphs are not tabular).
        _set_visible(time_el, False)

    weather_bottom_svg = 0.0
    if include_weather:
        if refresh_weather:
            temps = ensure_weather(zip_code=DEFAULT_WEATHER_ZIP)
        else:
            from pigeon.weather import cached_weather_temps

            temps = cached_weather_temps()
        high_s = _format_temp_f(temps.high_f) if temps is not None else "--"
        low_s = _format_temp_f(temps.low_f) if temps is not None else "--"
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
        weather_bottom_svg = _place_weather_under_date(root)
    else:
        _set_visible(_find_by_logical_id(root, "weather"), False)

    # Hide baked black plate — callers already paint a black stage.
    _set_visible(_find_by_logical_id(root, "Layer_1", "layer_1"), False)
    for el in list(root):
        if el.tag.endswith("rect"):
            fill = (el.get("fill") or "").strip().lower()
            if fill in ("#040603", "#000", "#000000"):
                _set_visible(el, False)

    _paint_cycle_color(root, color_hex)
    if include_weather:
        _ensure_degree_strokes(root, color_hex)
    return weather_bottom_svg


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

    Most Digital-7 digits fill a 1×2 faux-pixel rectangle. The colon only
    uses the middle column; layout crops 25% off each side of its matching
    cell so it advances at half width. Digit ``1`` stays in a full cell.
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

    Every digit, including skinny ``1``, keeps the matching width so the
    overall ``HH:MM:SS`` block does not resize. Only ``:`` is half-width.
    """
    w = max(1, int(matching_w))
    if ch == ":":
        return max(1, int(round(0.5 * w)))
    return w


def _hhmmss_scaffold_width(matching_w: int) -> int:
    """Reserved ``HH:MM:SS`` width: six digit cells + two half-width colons."""
    mw = max(1, int(matching_w))
    return 6 * mw + 2 * _hhmmss_advance(mw, ":")


def _hhmmss_block_width(matching_w: int, text: str) -> int:
    return sum(_hhmmss_advance(matching_w, ch) for ch in text)


def _parse_hhmmss_pairs(time_text: str) -> tuple[str, str, str]:
    """Split a clock string into ``(HH, MM, SS)`` groups on colons.

    12-hour labels drop the leading hour zero (``3:11:10``). Packing raw
    digits used to turn that into ``31:11:00``.
    """
    s = str(time_text or "").strip() or "00:00:00"
    parts = [p.strip() for p in s.split(":") if p.strip() != ""]
    if not parts:
        return "00", "00", "00"

    def _group(part: str, *, min_width: int) -> str:
        d = "".join(c for c in part if c.isdigit())
        if not d:
            return "0" * min_width
        if min_width <= 1:
            return d
        return d[-min_width:].zfill(min_width)

    hh = _group(parts[0], min_width=1)
    mm = _group(parts[1], min_width=2) if len(parts) > 1 else "00"
    ss = _group(parts[2], min_width=2) if len(parts) > 2 else "00"
    return hh, mm, ss


def _hhmmss_pair_width(matching_w: int, pair: str) -> int:
    return sum(_hhmmss_advance(matching_w, ch) for ch in pair)


def _pair_cell_glyphs(pair: str, *, pad: str = "left") -> tuple[str, str]:
    """Two matching-cell glyphs. A one-digit hour pads on the left (ones against the colon)."""
    glyphs = [ch for ch in str(pair or "") if ch != " "]
    if len(glyphs) >= 2:
        return glyphs[-2], glyphs[-1]
    if len(glyphs) == 1:
        return ("", glyphs[0]) if pad == "left" else (glyphs[0], "")
    return "", ""


def _hhmmss_locked_scaffold(
    matching_w: int, canvas_w: int, origin_x: int = 0
) -> tuple[
    tuple[tuple[int, int], tuple[int, int], tuple[int, int]],
    tuple[int, int],
]:
    """Fixed colon anchors and HH/MM/SS bands from a full-digit scaffold.

    Scaffold (centered on ``canvas_w``)::

        [HH slot = 2 full][colon half][MM slot][colon half][SS slot]

    Colons stay at absolute X forever. Each pair occupies two matching-width
    cells; a one-digit 12-hour hour sits in the right cell. Colons are centered
    in the half-cells so they keep a gap from the digits.
    """
    mw = max(1, int(matching_w))
    colon_w = _hhmmss_advance(mw, ":")
    pair_slot = 2 * mw
    block_w = _hhmmss_scaffold_width(mw)
    block_x = int(origin_x) + (int(canvas_w) - block_w) // 2
    c0 = block_x + pair_slot
    c1 = c0 + colon_w + pair_slot
    regions = (
        (block_x, c0),
        (c0 + colon_w, c1),
        (c1 + colon_w, block_x + block_w),
    )
    # Left edge of each locked colon slot (hour ones-digit lives in the right cell).
    colon_cx = (c0, c1)
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


_INK_TILE_CACHE: dict[
    tuple[str, int, tuple[int, int, int, int]], tuple[Image.Image, float]
] = {}


def _digital7_ink_rgba(
    ch: str, font: ImageFont.ImageFont, color: tuple[int, int, int, int]
) -> tuple[Image.Image, float]:
    """Rasterize one Digital-7 glyph and crop to visible ink.

    The float is the ``anchor="ms"`` point's Y inside the cropped tile so
    glyphs keep the old baseline while shifting horizontally by ink.
    """
    glyph = str(ch or "")
    empty = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    if not glyph:
        return empty, 0.5
    size = int(getattr(font, "size", 0) or 0)
    key = (glyph, size, color)
    hit = _INK_TILE_CACHE.get(key)
    if hit is not None:
        return hit
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    pdraw = ImageDraw.Draw(probe)
    l, t, r, b = pdraw.textbbox((0, 0), glyph, font=font, anchor="ms")
    pad = 8
    w = max(1, int(r - l) + 2 * pad)
    h = max(1, int(b - t) + 2 * pad)
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ox = pad - int(l)
    oy = pad - int(t)
    ImageDraw.Draw(im).text(
        (ox, oy), glyph, font=font, fill=color, anchor="ms"
    )
    arr = np.asarray(im)
    ys, xs = np.where(arr[:, :, 3] > 8)
    if xs.size == 0:
        tile, ms_y = im, float(oy)
    else:
        ix0 = int(xs.min())
        iy0 = int(ys.min())
        tile = im.crop(
            (ix0, iy0, int(xs.max()) + 1, int(ys.max()) + 1)
        )
        ms_y = float(oy - iy0)
    if len(_INK_TILE_CACHE) >= 96:
        _INK_TILE_CACHE.clear()
    _INK_TILE_CACHE[key] = (tile, ms_y)
    return tile, ms_y


def _paste_ink_glyph(
    img: Image.Image,
    ch: str,
    *,
    cy: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
    left: int | None = None,
    right: int | None = None,
    cx: float | None = None,
) -> None:
    tile, ms_y = _digital7_ink_rgba(ch, font, color)
    tw, _th = tile.size
    if right is not None:
        x = int(right) - tw
    elif left is not None:
        x = int(left)
    elif cx is not None:
        x = int(round(float(cx) - tw / 2.0))
    else:
        return
    y = int(round(float(cy) - ms_y))
    if img.mode != "RGBA":
        converted = img.convert("RGBA")
        converted.paste(tile, (x, y), tile)
        img.paste(converted)
        return
    img.paste(tile, (x, y), tile)


def _hhmmss_display_text(time_text: str) -> str:
    hh, mm, ss = _parse_hhmmss_pairs(time_text)
    return f"{hh}:{mm}:{ss}"


def _hhmmss_gap_px(
    matching_w: int, font: ImageFont.ImageFont, color: tuple[int, int, int, int]
) -> int:
    """Ink-to-ink tracking: the air between two ``0``s in adjacent matching cells."""
    ink0, _ = _digital7_ink_rgba("0", font, color)
    return max(2, int(matching_w) - int(ink0.size[0]))


def _hhmmss_ink_layout(
    text: str,
    *,
    matching_w: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
    canvas_w: int,
    origin_x: int = 0,
) -> tuple[list[tuple[str, int, int]], int]:
    """Left/right ink edges for each clock glyph, centered as one run."""
    glyphs = [ch for ch in str(text or "")]
    gap = _hhmmss_gap_px(matching_w, font, color)
    if not glyphs:
        return [], gap
    widths = [_digital7_ink_rgba(ch, font, color)[0].size[0] for ch in glyphs]
    total = sum(widths) + gap * (len(glyphs) - 1)
    x = origin_x + (int(canvas_w) - total) // 2
    slots: list[tuple[str, int, int]] = []
    for ch, w in zip(glyphs, widths):
        slots.append((ch, x, x + w))
        x += w + gap
    return slots, gap


def _draw_hhmmss_matching_run(
    img: Image.Image,
    *,
    text: str,
    matching_w: int,
    cy: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
    canvas_w: int,
    origin_x: int = 0,
) -> None:
    hh, mm, ss = _parse_hhmmss_pairs(text)
    regions, colon_lefts = _hhmmss_locked_scaffold(
        matching_w, canvas_w, origin_x=origin_x
    )
    for pair, region in zip((hh, mm, ss), regions):
        _draw_pair_in_region(
            img,
            pair=pair,
            region=region,
            matching_w=matching_w,
            cy=cy,
            font=font,
            color=color,
            align="center",
        )
    colon_w = _hhmmss_advance(matching_w, ":")
    for slot_left in colon_lefts:
        _paste_ink_glyph(
            img,
            ":",
            cx=float(slot_left) + float(colon_w) * 0.5,
            cy=cy,
            font=font,
            color=color,
        )


def _draw_pair_in_region(
    img: Image.Image,
    *,
    pair: str,
    region: tuple[int, int],
    matching_w: int,
    cy: int,
    font: ImageFont.ImageFont,
    color: tuple[int, int, int, int],
    pad: str = "left",
    align: str = "center",
) -> None:
    """Place a pair into two matching-width cells spanning ``region``.

    Glyphs are cropped to ink so a 12-hour hour sits in the ones cell. The
    colon is centered in its own half-cell so the pair does not kiss.
    """
    left, _right = region
    mw = max(1, int(matching_w))
    tens, ones = _pair_cell_glyphs(pair, pad=pad)
    for i, glyph in enumerate((tens, ones)):
        if not glyph:
            continue
        cell_left = left + i * mw
        if align == "right":
            _paste_ink_glyph(
                img, glyph, right=cell_left + mw, cy=cy, font=font, color=color
            )
        else:
            _paste_ink_glyph(
                img,
                glyph,
                cx=cell_left + mw / 2.0,
                cy=cy,
                font=font,
                color=color,
            )


def clock_saver_volume_label(raw: object | None) -> str:
    """Digital-7 readout for the saver volume gap. Empty when nothing to show."""
    from pigeon.widgets.playback_overlay import volume_widget_value_text

    label = volume_widget_value_text(raw)
    if not label:
        return ""
    if label.strip().lower() in ("mute", "muted"):
        return "MUTE"
    return label


def clock_saver_volume_line_visible(raw: object | None) -> bool:
    """True when the saver should paint the volume arms (not mute / empty / off)."""
    label = clock_saver_volume_label(raw)
    return bool(label) and label != "MUTE"


def step_clock_saver_volume(
    raw: object,
    action: str,
    *,
    unmute_to: object | None = None,
) -> str:
    """Optimistic next volume string after ``volume_up`` / ``volume_down`` / ``mute_toggle``.

    Denon-style dB readouts step 0.5; bare 0–100 levels step 1. ``MUTE`` stays
    muted on down, and unmute uses ``unmute_to`` when provided.
    """
    act = str(action or "").strip().lower()
    label = clock_saver_volume_label(raw)
    src = str(raw or "").strip()
    if act == "mute_toggle":
        if label == "MUTE":
            restored = clock_saver_volume_label(unmute_to) if unmute_to is not None else ""
            if restored and restored != "MUTE":
                return str(unmute_to).strip()
            return "-80.0 dB"
        return "MUTE"
    if act not in ("volume_up", "volume_down"):
        return src
    delta = 1 if act == "volume_up" else -1
    if label == "MUTE":
        if delta < 0:
            return "MUTE"
        restored = clock_saver_volume_label(unmute_to) if unmute_to is not None else ""
        if restored and restored != "MUTE":
            return step_clock_saver_volume(unmute_to, act)
        return "-80.0 dB"
    db_src = bool(re.search(r"dB", src, flags=re.I) or (label[:1] in "+-"))
    if db_src:
        try:
            db = float(re.sub(r"[^0-9.+-]", "", label) or "0")
        except ValueError:
            db = -80.0
        db = max(-80.0, min(0.0, db + (0.5 * delta)))
        return f"{db:.1f} dB"
    digits = re.fullmatch(r"\d{1,3}", label)
    if digits:
        n = max(0, min(100, int(label) + delta))
        return str(n)
    return src


def _volume_levels_match(a: object, b: object) -> bool:
    la = clock_saver_volume_label(a)
    lb = clock_saver_volume_label(b)
    return bool(la) and la == lb


VOLUME_LINE_HOLD_S = 3.0
VOLUME_LINE_FADE_S = 0.75


def volume_line_fade_opacity(
    now: float,
    last_change_mono: float,
    *,
    hold_s: float = VOLUME_LINE_HOLD_S,
    fade_s: float = VOLUME_LINE_FADE_S,
) -> float:
    """1 while the volume arms should stay up, then a smooth fade to 0.

    The numeric readout is independent — callers keep drawing the dB label.
    """
    if float(last_change_mono) <= 0.0:
        return 0.0
    age = float(now) - float(last_change_mono)
    hold = max(0.0, float(hold_s))
    fade = max(0.05, float(fade_s))
    if age < hold:
        return 1.0
    t = (age - hold) / fade
    if t >= 1.0:
        return 0.0
    t = max(0.0, min(1.0, t))
    return 1.0 - (t * t * (3.0 - 2.0 * t))


class VolumeLineReveal:
    """Show volume arms for 3s after the AVR level changes, then fade them out."""

    def __init__(
        self,
        *,
        hold_s: float = VOLUME_LINE_HOLD_S,
        fade_s: float = VOLUME_LINE_FADE_S,
    ) -> None:
        self.last_change_mono = 0.0
        self.last_label = ""
        self.hold_s = float(hold_s)
        self.fade_s = float(fade_s)

    def note(self, raw: object, *, now: float | None = None) -> None:
        label = clock_saver_volume_label(raw)
        if not label:
            return
        if not self.last_label:
            self.last_label = label
            return
        if label == self.last_label:
            return
        self.last_label = label
        self.last_change_mono = time.monotonic() if now is None else float(now)

    def opacity(self, now: float | None = None) -> float:
        t = time.monotonic() if now is None else float(now)
        return volume_line_fade_opacity(
            t, self.last_change_mono, hold_s=self.hold_s, fade_s=self.fade_s
        )

    def fading(self, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else float(now)
        if self.last_change_mono <= 0.0:
            return False
        age = t - self.last_change_mono
        return self.hold_s <= age < (self.hold_s + self.fade_s)


class ClockSaverVolumeHold:
    """Last shown saver volume. Knob steps win until the AVR poll catches up."""

    def __init__(self, *, grace_s: float = 2.0) -> None:
        self.hold = ""
        self.pre_mute = ""
        self.before_nudge = ""
        self.nudge_until = 0.0
        self.grace_s = float(grace_s)

    def in_nudge_grace(self, now: float | None = None) -> bool:
        t = time.monotonic() if now is None else float(now)
        return bool(self.hold) and t < float(self.nudge_until or 0.0)

    def clear(self) -> None:
        self.hold = ""
        self.before_nudge = ""
        self.nudge_until = 0.0

    def is_stale_poll(self, raw: object, *, now: float | None = None) -> bool:
        """True when a poll still reports the pre-knob level during grace."""
        if not self.in_nudge_grace(now):
            return False
        s = str(raw or "").strip()
        if not s or not clock_saver_volume_label(s):
            return True
        return _volume_levels_match(s, self.before_nudge)

    def remember(
        self,
        raw: object,
        *,
        source: str = "poll",
        now: float | None = None,
        hold_s: float | None = None,
    ) -> str:
        s = str(raw or "").strip()
        label = clock_saver_volume_label(s) if s else ""
        t = time.monotonic() if now is None else float(now)
        if source == "nudge" and label:
            prev = str(self.hold or "")
            if clock_saver_volume_label(prev) != "MUTE":
                self.before_nudge = prev
            if label != "MUTE":
                self.pre_mute = s
            self.hold = s
            grace = float(hold_s) if hold_s is not None else self.grace_s
            self.nudge_until = t + max(0.2, grace)
            return s
        if not label:
            return str(self.hold or "")
        if self.in_nudge_grace(t) and _volume_levels_match(s, self.before_nudge):
            return str(self.hold or s)
        if label != "MUTE":
            self.pre_mute = s
        self.hold = s
        if source == "poll":
            self.nudge_until = 0.0
        return s

    def pick(
        self,
        candidates: list[object],
        *,
        now: float | None = None,
        receiver_off: bool = False,
    ) -> str:
        t = time.monotonic() if now is None else float(now)
        if receiver_off:
            self.clear()
            return ""
        live = ""
        for raw in candidates:
            s = str(raw or "").strip()
            if s and clock_saver_volume_label(s):
                live = s
                break
        if self.in_nudge_grace(t):
            if not live or _volume_levels_match(live, self.before_nudge):
                return str(self.hold or live)
            return self.remember(live, source="poll", now=t)
        if live:
            return self.remember(live, source="poll", now=t)
        return str(self.hold or "")

    def display_line(self) -> str:
        """Last remembered saver volume. Compose must use this, not ``pick``.

        ``pick(candidates)`` treats the first labeled cache entry as a live
        poll and overwrites ``hold``. Clock-saver compose used to do that
        every frame, so the Digital-7 number stayed on a stale ``effective``
        while ``VolumeLineReveal`` still showed the arms for the new level.
        """
        s = str(self.hold or "").strip()
        return s if s and clock_saver_volume_label(s) else ""


def clock_saver_volume_line_geometry(
    *,
    fraction: float,
    canvas_w: int,
    text_w: int,
    max_width: int,
    gap_pad_x: int = _VOLUME_TEXT_PAD_X_PX,
) -> tuple[int, int, int, int]:
    """Centered volume bar.

    Returns ``(line_left, line_right, gap_left, gap_right)``. Arms occupy
    ``[line_left, gap_left)`` and ``[gap_right, line_right)``. At fraction 0
    the span equals the text gap, so no arms are drawn.
    """
    cx = int(canvas_w) // 2
    frac = max(0.0, min(1.0, float(fraction)))
    pad = max(0, int(gap_pad_x))
    gap = max(int(text_w) + 2 * pad, 1)
    if gap % 2:
        gap += 1
    max_span = max(gap, int(max_width))
    if max_span % 2:
        max_span += 1
    span = gap + int(round((max_span - gap) * frac))
    if span % 2:
        span += 1
    line_left = cx - span // 2
    line_right = line_left + span
    gap_left = cx - gap // 2
    gap_right = gap_left + gap
    return line_left, line_right, gap_left, gap_right


def _draw_hhmmss_fixed_cells(
    bgra: np.ndarray,
    time_text: str,
    *,
    color: tuple[int, int, int, int],
    volume: object | None = None,
    weather_bottom: float | None = None,
    line_opacity: float = 1.0,
) -> None:
    """Paint ``HH:MM:SS`` in locked matching-width cells.

    Every digit, including ``1``, occupies the width of ``0``. A 12-hour hour
    sits in the ones cell so the colon anchors never move.
    """
    label = _hhmmss_display_text(time_text)
    font_path = resolve_digital7_font() or resolve_ui_font_bold()
    canvas_h, canvas_w = int(bgra.shape[0]), int(bgra.shape[1])
    sy = float(canvas_w) / _ARTBOARD_W
    prefer_sz = max(24, int(round(_HHMMSS_FONT_SIZE_SVG * sy)))
    max_w = max(
        64,
        int(round(canvas_w * _HHMMSS_MAX_WIDTH_FRAC)) - 2 * _HHMMSS_SIDE_PAD_PX,
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
    if canvas_h == int(DESIGN_H):
        from pigeon.np_layout import clock_saver_seconds_track_rect

        _tx, bar_y, _tw, _th, _trx = clock_saver_seconds_track_rect()
        cy = int(bar_y) - int(_HHMMSS_GAP_ABOVE_BAR_PX)
    else:
        cy = int(round(_HHMMSS_MID_Y_SVG * float(canvas_h) / _ARTBOARD_H))

    pre_ink = _color_ink_mask(np.asarray(img)).copy()
    pre_rows = np.where(pre_ink.any(axis=1))[0]
    wx_bottom_px = (
        float(int(pre_rows.max()) + 1)
        if pre_rows.size
        else float(weather_bottom or 0.0)
    )

    _draw_hhmmss_matching_run(
        img,
        text=label,
        matching_w=matching_w,
        cy=cy,
        font=font,
        color=color,
        canvas_w=canvas_w,
    )

    post_ink = _color_ink_mask(np.asarray(img))
    new_rows = np.where((post_ink & ~pre_ink).any(axis=1))[0]
    time_box = draw.textbbox((0, int(cy)), "0", font=font, anchor="ms")
    time_top_px = int(new_rows.min()) if new_rows.size else int(time_box[1])

    _draw_clock_saver_volume_line(
        draw,
        volume=volume,
        color=color,
        font_path=font_path,
        canvas_w=canvas_w,
        time_cy=cy,
        time_font=font,
        weather_bottom=wx_bottom_px,
        time_top=time_top_px,
        line_opacity=line_opacity,
    )

    out = np.asarray(img)
    bgra[:, :, 0] = out[:, :, 2]
    bgra[:, :, 1] = out[:, :, 1]
    bgra[:, :, 2] = out[:, :, 0]
    bgra[:, :, 3] = out[:, :, 3]


def _draw_clock_saver_volume_line(
    draw: ImageDraw.ImageDraw,
    *,
    volume: object | None,
    color: tuple[int, int, int, int],
    font_path: str | None,
    canvas_w: int,
    time_cy: int,
    time_font: ImageFont.ImageFont,
    weather_bottom: float | None = None,
    time_top: int | None = None,
    line_opacity: float = 1.0,
) -> None:
    """Centered volume stroke with a Digital-7 level in the middle break."""
    label = clock_saver_volume_label(volume)
    if not label:
        return
    from pigeon.np_layout import clock_saver_seconds_track_rect
    from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

    time_box = draw.textbbox((0, int(time_cy)), "0", font=time_font, anchor="ms")
    time_limit = int(time_top) if time_top is not None else int(time_box[1])
    wx_bottom = float(weather_bottom) if weather_bottom is not None else 0.0
    clearance = max(
        8,
        int(round(_VOLUME_CLEARANCE_PX * float(canvas_w) / float(DESIGN_W))),
    )
    band_top = wx_bottom + clearance if wx_bottom > 0.0 else 0.0
    band_bot = float(time_limit - clearance)
    if band_bot <= band_top + 8:
        band_top = max(0.0, float(time_limit) - float(_VOLUME_GAP_ABOVE_TIME_PX) - 48.0)
        band_bot = float(time_limit) - float(_VOLUME_GAP_ABOVE_TIME_PX)
    avail = max(18.0, band_bot - band_top)

    prefer_sz = max(
        18,
        int(round(_CLOCK_SAVER_VOLUME_FONT_SIZE_PX * float(canvas_w) / float(DESIGN_W))),
    )
    vol_sz = prefer_sz
    vol_font = _load_font(font_path, vol_sz)
    above, below, text_w = _anchor_mm_extents(draw, label, vol_font)
    while vol_sz > 18 and (above + below) > avail:
        vol_sz -= 2
        vol_font = _load_font(font_path, vol_sz)
        above, below, text_w = _anchor_mm_extents(draw, label, vol_font)
    try:
        _tx, _ty, track_w, _th, _trx = clock_saver_seconds_track_rect()
        max_w = int(track_w)
    except Exception:
        max_w = int(round(float(canvas_w) * 0.83))
    line_l, line_r, gap_l, gap_r = clock_saver_volume_line_geometry(
        fraction=volume_fraction_from_display_line(volume),
        canvas_w=int(canvas_w),
        text_w=text_w,
        max_width=max_w,
    )
    line_h = max(
        2,
        int(
            round(
                _CLOCK_SAVER_VOLUME_LINE_H_PX
                * (float(vol_sz) / float(max(1, prefer_sz)))
                * float(canvas_w)
                / float(DESIGN_W)
            )
        ),
    )
    visual_h = max(above + below, line_h)
    mid = (band_top + band_bot) / 2.0
    # ``mm`` is not the visual center for Digital-7 (more ink hangs below).
    cy_vol = mid - (below - above) / 2.0
    if visual_h <= (band_bot - band_top):
        vis_top = cy_vol - above
        vis_bot = cy_vol + below
        if vis_top < band_top:
            cy_vol += band_top - vis_top
        elif vis_bot > band_bot:
            cy_vol -= vis_bot - band_bot
    else:
        cluster_bottom = time_limit - int(_VOLUME_GAP_ABOVE_TIME_PX)
        cy_vol = cluster_bottom - below
    arm_a = int(round(float(color[3]) * max(0.0, min(1.0, float(line_opacity)))))
    if arm_a > 0 and clock_saver_volume_line_visible(volume):
        line_y0 = int(round(cy_vol - line_h / 2.0))
        line_y1 = line_y0 + line_h - 1
        radius = max(1, line_h // 2)
        arm_color = (int(color[0]), int(color[1]), int(color[2]), arm_a)
        for x0, x1 in ((line_l, gap_l), (gap_r, line_r)):
            arm_w = int(x1) - int(x0)
            if arm_w < 2:
                continue
            draw.rounded_rectangle(
                [int(x0), line_y0, int(x1) - 1, line_y1],
                radius=radius,
                fill=arm_color,
            )
    draw.text((int(canvas_w) // 2, cy_vol), label, font=vol_font, fill=color, anchor="mm")


def clock_saver_volume_strip_bgra(
    volume: object | None,
    *,
    width: int,
    height: int,
    color: tuple[int, int, int, int] | None = None,
    line_opacity: float = 1.0,
) -> np.ndarray:
    """Digital-7 volume + arms sized to a now-playing strip (zone 4)."""
    w = max(1, int(width))
    h = max(1, int(height))
    out = np.zeros((h, w, 4), dtype=np.uint8)
    label = clock_saver_volume_label(volume)
    if not label:
        return out
    from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

    rgba = color if color is not None else (255, 255, 255, 255)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font_path = resolve_digital7_font() or resolve_ui_font_bold()
    vol_sz = max(
        18,
        min(
            int(round(h * 0.62)),
            int(round(_VOLUME_FONT_SIZE_PX * float(w) / float(DESIGN_W))),
        ),
    )
    vol_font = _load_font(font_path, vol_sz)
    tb = draw.textbbox((0, 0), label, font=vol_font)
    text_w = max(1, int(tb[2] - tb[0]))
    max_w = max(text_w + 8, int(round(w * 0.92)))
    line_l, line_r, gap_l, gap_r = clock_saver_volume_line_geometry(
        fraction=volume_fraction_from_display_line(volume),
        canvas_w=w,
        text_w=text_w,
        max_width=max_w,
    )
    line_h = max(2, int(round(_VOLUME_LINE_H_PX * float(w) / float(DESIGN_W))))
    cy_vol = h / 2.0
    arm_a = int(round(float(rgba[3]) * max(0.0, min(1.0, float(line_opacity)))))
    if arm_a > 0 and clock_saver_volume_line_visible(volume):
        line_y0 = int(round(cy_vol - line_h / 2.0))
        line_y1 = line_y0 + line_h - 1
        radius = max(1, line_h // 2)
        arm_color = (int(rgba[0]), int(rgba[1]), int(rgba[2]), arm_a)
        for x0, x1 in ((line_l, gap_l), (gap_r, line_r)):
            arm_w = int(x1) - int(x0)
            if arm_w < 2:
                continue
            draw.rounded_rectangle(
                [int(x0), line_y0, int(x1) - 1, line_y1],
                radius=radius,
                fill=arm_color,
            )
    draw.text((w // 2, cy_vol), label, font=vol_font, fill=rgba, anchor="mm")
    arr = np.asarray(img)
    out[:, :, 0] = arr[:, :, 2]
    out[:, :, 1] = arr[:, :, 1]
    out[:, :, 2] = arr[:, :, 0]
    out[:, :, 3] = arr[:, :, 3]
    return out


def _draw_clock_saver_seconds_bar(
    bgra: np.ndarray,
    now,
    *,
    fill_bgr: tuple[int, int, int],
) -> None:
    """Paint the zone-5 track as 60 stepped second cells. Analog callers skip this."""
    from pigeon.np_layout import (
        clock_saver_seconds_filled,
        clock_saver_seconds_segment_rects,
        clock_saver_seconds_track_rect,
    )
    from pigeon.widgets.view_circles import _draw_rounded_bar_bgra

    tx, ty, tw, th, trx = clock_saver_seconds_track_rect()
    rects = clock_saver_seconds_segment_rects((float(tx), float(ty), float(tw), float(th)))
    if not rects:
        return
    filled = clock_saver_seconds_filled(int(getattr(now, "second", 0)))
    seg_w = min(r[2] for r in rects)
    radius = max(1, min(int(trx), seg_w // 2, int(th) // 2))
    # Upcoming cells stay invisible — only elapsed seconds are painted.
    for i, (x, y, w, h) in enumerate(rects):
        if i >= filled:
            break
        _draw_rounded_bar_bgra(
            bgra,
            x=x,
            y=y,
            w=w,
            h=h,
            fill_bgr=fill_bgr,
            radius=radius,
            fill_opacity=1.0,
        )


def render_seconds_bar_widget_bgra(
    width: int,
    height: int,
    *,
    now=None,
    fill_bgr: tuple[int, int, int] | None = None,
) -> np.ndarray:
    """Clock-saver seconds cells, fitted into a strip well."""
    from datetime import datetime

    from pigeon.np_layout import (
        clock_saver_seconds_filled,
        clock_saver_seconds_segment_rects,
    )
    from pigeon.widgets.view_circles import _draw_rounded_bar_bgra, np_theme_from_settings

    w = max(32, int(width))
    h = max(12, int(height))
    out = np.zeros((h, w, 4), dtype=np.uint8)
    when = now if now is not None else datetime.now()
    pad_x = max(8, int(round(w * 0.03)))
    pad_y = max(4, int(round(h * 0.22)))
    tw = max(8, w - 2 * pad_x)
    th = max(4, h - 2 * pad_y)
    rects = clock_saver_seconds_segment_rects((float(pad_x), float(pad_y), float(tw), float(th)))
    if not rects:
        return out
    filled = clock_saver_seconds_filled(int(getattr(when, "second", 0)))
    color = fill_bgr
    if color is None:
        try:
            color = np_theme_from_settings().ui_bgr
        except Exception:
            color = (255, 255, 255)
    seg_w = min(r[2] for r in rects)
    radius = max(1, min(seg_w // 2, th // 2))
    for i, (x, y, rw, rh) in enumerate(rects):
        if i >= filled:
            break
        _draw_rounded_bar_bgra(
            out,
            x=x,
            y=y,
            w=rw,
            h=rh,
            fill_bgr=color,
            radius=radius,
            fill_opacity=1.0,
        )
    return out


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
    volume: object | None = None,
    line_opacity: float = 1.0,
    include_weather: bool = True,
    prefer_digital: bool = False,
) -> np.ndarray:
    """Full 1280×800 BGRA idle face: digital clocksaver or centered clock widget."""
    if not prefer_digital:
        try:
            from pigeon.widgets.options_settings import clock_widget_analog

            if clock_widget_analog():
                from pigeon.widgets.view_circles import render_centered_clock_widget_bgra

                return render_centered_clock_widget_bgra(
                    layer_opacity=layer_opacity,
                    assets_dir=assets_dir,
                )
        except Exception:
            pass
    path = (
        Path(svg_path)
        if svg_path is not None
        else default_clock_saver_svg_path(assets_dir)
    )
    color = _time_color_rgba(time.monotonic())
    color_hex = _rgba_to_hex(color)
    now = _resolve_display_time()
    time_text = _time_label(now)
    if not path.is_file():
        # Soft fallback: time-only band if art is missing.
        return _legacy_time_only_bgra(
            color=color,
            layer_opacity=layer_opacity,
            time_text=time_text,
            volume=volume,
            line_opacity=line_opacity,
        )

    root = _svg_tree_from_path(path)
    weather_bottom_svg = _apply_clock_saver_svg_state(
        root, color_hex=color_hex, include_weather=include_weather
    )
    from pigeon.np_layout import letterbox_legacy_ui
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    native = rasterize_settings_svg_bgra(
        root,
        width=LEGACY_DESIGN_W,
        height=LEGACY_DESIGN_H,
        font_mode="preferences",
    )
    framed = letterbox_legacy_ui(native)
    weather_bottom = (
        clock_saver_svg_y_to_design_y(weather_bottom_svg, int(framed.shape[0]))
        if include_weather
        else None
    )
    _draw_hhmmss_fixed_cells(
        framed,
        time_text,
        color=color,
        volume=volume,
        weather_bottom=weather_bottom,
        line_opacity=line_opacity,
    )
    _draw_clock_saver_seconds_bar(
        framed, now, fill_bgr=(int(color[2]), int(color[1]), int(color[0]))
    )
    return _apply_layer_opacity(framed, layer_opacity)


def render_clock_saver_weather_cluster_bgra(
    *,
    assets_dir: Path | str | None = None,
    svg_path: Path | str | None = None,
    preview: bool = False,
) -> np.ndarray:
    """High/low weather cluster from clocksaver.svg, without date or time."""
    path = (
        Path(svg_path)
        if svg_path is not None
        else default_clock_saver_svg_path(assets_dir)
    )
    if not path.is_file():
        return _EMPTY_PATCH.copy()
    color = _time_color_rgba(0.0 if preview else time.monotonic())
    root = _svg_tree_from_path(path)
    _apply_clock_saver_svg_state(
        root,
        color_hex=_rgba_to_hex(color),
        include_weather=True,
        refresh_weather=not preview,
    )
    _set_visible(_find_by_logical_id(root, "clock"), False)
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    return rasterize_settings_svg_bgra(
        root,
        width=LEGACY_DESIGN_W,
        height=LEGACY_DESIGN_H,
        font_mode="preferences",
    )


def render_clock_saver_face_bgra(
    *,
    width: int,
    height: int,
    include_weather: bool = False,
    include_seconds_bar: bool = True,
    when: datetime | None = None,
) -> np.ndarray:
    """Date + HH:MM:SS (+ optional seconds bar) sized to ``width``×``height``.

    Same type as the digital clock saver, composed for a widget well so the
    time is fully visible. Weather is omitted unless ``include_weather``.
    """
    w = max(32, int(width))
    h = max(24, int(height))
    out = np.zeros((h, w, 4), dtype=np.uint8)
    now = when if when is not None else _resolve_display_time()
    color = _time_color_rgba(0.0 if when is not None else time.monotonic())
    pad = max(4, int(round(min(w, h) * 0.05)))
    date_h = max(16, int(round(h * 0.16)))
    bar_h = max(6, int(round(h * 0.08))) if include_seconds_bar else 0
    weather_h = max(18, int(round(h * 0.18))) if include_weather else 0
    gap = max(3, int(round(h * 0.03)))
    y = pad
    date_cy = y + date_h // 2
    _draw_face_date(
        out,
        _date_label(now),
        color=color,
        cy=date_cy,
        max_w=max(24, w - 2 * pad),
        max_h=date_h,
    )
    y = pad + date_h + gap
    if include_weather:
        cluster = render_clock_saver_weather_cluster_bgra()
        _contain_patch(out, cluster, (pad, y, max(1, w - 2 * pad), weather_h))
        y += weather_h + gap
    time_bottom = h - pad - (bar_h + gap if bar_h else 0)
    time_h = max(16, time_bottom - y)
    _draw_face_hhmmss(
        out,
        _time_label(now),
        color=color,
        box=(pad, y, max(24, w - 2 * pad), time_h),
    )
    if bar_h:
        _draw_face_seconds_bar(
            out,
            now,
            fill_bgr=(int(color[2]), int(color[1]), int(color[0])),
            box=(pad, h - pad - bar_h, max(24, w - 2 * pad), bar_h),
        )
    return out


def _draw_face_date(
    bgra: np.ndarray,
    text: str,
    *,
    color: tuple[int, int, int, int],
    cy: int,
    max_w: int,
    max_h: int,
) -> None:
    label = str(text or "").strip()
    if not label or max_w < 8 or max_h < 8:
        return
    path = resolve_ui_font_bold()
    lo, hi = 8, max(10, int(max_h))
    best = _load_font(path, lo)
    probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
    while lo <= hi:
        mid = (lo + hi) // 2
        font = _load_font(path, mid)
        box = probe.textbbox((0, 0), label, font=font)
        tw = max(1, int(box[2] - box[0]))
        th = max(1, int(box[3] - box[1]))
        if tw <= max_w and th <= max_h:
            best = font
            lo = mid + 1
        else:
            hi = mid - 1
    rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
    img = Image.fromarray(rgba)
    draw = ImageDraw.Draw(img)
    draw.text((int(bgra.shape[1]) // 2, int(cy)), label, font=best, fill=color, anchor="mm")
    arr = np.asarray(img)
    bgra[:, :, 0] = arr[:, :, 2]
    bgra[:, :, 1] = arr[:, :, 1]
    bgra[:, :, 2] = arr[:, :, 0]
    bgra[:, :, 3] = arr[:, :, 3]


def _draw_face_hhmmss(
    bgra: np.ndarray,
    time_text: str,
    *,
    color: tuple[int, int, int, int],
    box: tuple[int, int, int, int],
) -> None:
    x, y, w, h = (int(v) for v in box)
    if w < 16 or h < 12:
        return
    font_path = resolve_digital7_font() or resolve_ui_font_bold()
    font = _fit_digital7_fixed_cells(
        font_path,
        _HHMMSS_MATCHING_UNITS,
        max_w=max(16, w - 4),
        max_h=max(12, h - 2),
        prefer_sz=max(16, h),
    )
    rgba = np.ascontiguousarray(bgra[:, :, [2, 1, 0, 3]])
    img = Image.fromarray(rgba)
    draw = ImageDraw.Draw(img)
    matching_w, _cell_h = _cell_metrics(draw, font, _HHMMSS_CHAR_SET)
    cy = y + h // 2
    _draw_hhmmss_matching_run(
        img,
        text=_hhmmss_display_text(time_text),
        matching_w=matching_w,
        cy=cy,
        font=font,
        color=color,
        canvas_w=w,
        origin_x=x,
    )
    arr = np.asarray(img)
    bgra[:, :, 0] = arr[:, :, 2]
    bgra[:, :, 1] = arr[:, :, 1]
    bgra[:, :, 2] = arr[:, :, 0]
    bgra[:, :, 3] = arr[:, :, 3]


def _draw_face_seconds_bar(
    bgra: np.ndarray,
    now,
    *,
    fill_bgr: tuple[int, int, int],
    box: tuple[int, int, int, int],
) -> None:
    from pigeon.np_layout import (
        CLOCK_SAVER_SECONDS_GAP_PX,
        CLOCK_SAVER_SECONDS_SEGMENTS,
        clock_saver_seconds_filled,
        clock_saver_seconds_segment_rects,
    )
    from pigeon.widgets.view_circles import _draw_rounded_bar_bgra

    x, y, w, h = (int(v) for v in box)
    if w < 8 or h < 3:
        return
    rects = clock_saver_seconds_segment_rects((float(x), float(y), float(w), float(h)))
    if not rects:
        n = int(CLOCK_SAVER_SECONDS_SEGMENTS)
        gap = max(1, min(int(CLOCK_SAVER_SECONDS_GAP_PX), h // 2))
        cell = max(1, (w - gap * (n - 1)) // n)
        rects = tuple(
            (x + i * (cell + gap), y, cell, h) for i in range(n)
        )
    filled = clock_saver_seconds_filled(int(getattr(now, "second", 0)))
    seg_w = min(r[2] for r in rects)
    radius = max(1, min(seg_w // 2, h // 2))
    for i, (sx, sy, sw, sh) in enumerate(rects):
        if i >= filled:
            break
        _draw_rounded_bar_bgra(
            bgra,
            x=int(sx),
            y=int(sy),
            w=int(sw),
            h=int(sh),
            fill_bgr=fill_bgr,
            radius=radius,
            fill_opacity=1.0,
            stroke=0,
        )


def _contain_patch(
    canvas: np.ndarray,
    patch: np.ndarray | None,
    box: tuple[int, int, int, int],
) -> None:
    if patch is None or patch.size == 0 or patch.ndim < 3:
        return
    import cv2

    from pigeon.compositing import cv_resize_interp
    from pigeon.widgets.view_circles import _ink_crop_bgra, _paste_patch_bgra

    cropped = _ink_crop_bgra(patch, pad=2)
    if cropped is None or cropped.size == 0:
        return
    x, y, w, h = (int(v) for v in box)
    ph, pw = int(cropped.shape[0]), int(cropped.shape[1])
    if pw < 1 or ph < 1 or w < 1 or h < 1:
        return
    scale = min(w / float(pw), h / float(ph))
    nw = max(1, int(round(pw * scale)))
    nh = max(1, int(round(ph * scale)))
    resized = cv2.resize(
        cropped, (nw, nh), interpolation=cv_resize_interp(pw, ph, nw, nh)
    )
    _paste_patch_bgra(
        canvas, resized, x + (w - nw) // 2, y + (h - nh) // 2
    )


def _legacy_time_only_bgra(
    *,
    color: tuple[int, int, int, int],
    layer_opacity: float,
    time_text: str | None = None,
    volume: object | None = None,
    line_opacity: float = 1.0,
) -> np.ndarray:
    bgra = np.zeros((LEGACY_DESIGN_H, LEGACY_DESIGN_W, 4), dtype=np.uint8)
    when = _resolve_display_time()
    text = time_text or _time_label(when)
    from pigeon.np_layout import letterbox_legacy_ui

    framed = letterbox_legacy_ui(bgra)
    _draw_hhmmss_fixed_cells(
        framed, text, color=color, volume=volume, line_opacity=line_opacity
    )
    _draw_clock_saver_seconds_bar(
        framed, when, fill_bgr=(int(color[2]), int(color[1]), int(color[0]))
    )
    return _apply_layer_opacity(framed, layer_opacity)


def clock_saver_composite_bgra(
    *,
    shadow_bgr: tuple[int, int, int] | None,
    layer_opacity: float = 1.0,
    time_layer_opacity: float | None = None,
    date_layer_opacity: float | None = None,
    date_anchor_row: int | None = None,
    date_anchor_col: int | None = None,
    volume: object | None = None,
    line_opacity: float = 1.0,
) -> tuple[
    tuple[np.ndarray, tuple[int, int, int, int]],
    tuple[np.ndarray, tuple[int, int, int, int]],
]:
    """
    Full-frame idle face (zone 9).

    Digital options → color-cycled digital clocksaver; analog → centered NP
    clock widget (with digital HH:MM kept on). Returns ``(full_frame, empty_date)``
    so existing compose call sites that blit both packs keep working.
    ``shadow_bgr`` / date anchors are unused for the digital SVG path.
    """
    _ = (shadow_bgr, date_layer_opacity, date_anchor_row, date_anchor_col)
    t_op = float(layer_opacity if time_layer_opacity is None else time_layer_opacity)
    frame = render_clock_saver_bgra(
        layer_opacity=t_op, volume=volume, line_opacity=line_opacity
    )
    full_rect = (0, 0, int(DESIGN_W), int(DESIGN_H))
    return (frame, full_rect), (_EMPTY_PATCH.copy(), (0, 0, 1, 1))


def clear_clock_saver_render_caches() -> None:
    _SVG_TREE_TEMPLATES.clear()
