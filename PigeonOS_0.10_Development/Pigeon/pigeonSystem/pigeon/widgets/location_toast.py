"""One-line location label on the main UI (design grid)."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.design import rect_for_span_top_right_at_cell
from pigeon.font_paths import resolve_ui_font_medium, resolve_ui_font_regular
from pigeon.widgets.clock_calendar import (
    design_clock_calendar_medium_font_point_size,
)

LOCATION_TOAST_FULL_S = 15.0
LOCATION_TOAST_FONT_SCALE_VS_CLOCK = 0.375
LOCATION_TOAST_FADE_S = 2.0

# Fixed location identifier anchor for all supported views.
# Top-right stack: last line below clock / playback / volume (~one cell below volume);
# right edge aligns with col 19 (same strip as clock / volume).
_LOCATION_TOAST_ROW = 3.5
_LOCATION_TOAST_COL_RIGHT = 19.0
_LOCATION_TOAST_STARTUP_TOP_LEFT_ROW = 9.0
_LOCATION_TOAST_STARTUP_TOP_LEFT_COL = 13.5  # x-left ≈ col_right − span (5 cells)

_SPAN = (5, 1)
_TEXT_WHITE = (255, 255, 255, 255)
_STROKE_W = 2
_STROKE_RGBA = (0, 0, 0, 255)
_SHADOW_ALPHA = 110
_DEFAULT_SHADOW_BGR = (40, 140, 255)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def _load_font(path: str, size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _ellipsize(text: str, draw: ImageDraw.ImageDraw, font: ImageFont.ImageFont, max_w: int) -> str:
    if _text_size(draw, text, font)[0] <= max_w:
        return text
    ell = "..."
    if _text_size(draw, ell, font)[0] > max_w:
        return ""
    lo, hi = 0, len(text)
    best = ""
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if _text_size(draw, cand, font)[0] <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best or ell


def _font_and_line_scaled_vs_clock(
    text: str,
    medium_path: str,
    inner_w: int,
    inner_h: int,
    draw: ImageDraw.ImageDraw,
) -> tuple[ImageFont.ImageFont, str]:
    base_px = design_clock_calendar_medium_font_point_size()
    target_px = max(6, int(round(base_px * LOCATION_TOAST_FONT_SCALE_VS_CLOCK)))
    line_raw = (text or "").strip() or "Location"
    min_sz = 6
    sz = target_px
    while sz >= min_sz:
        font = _load_font(medium_path, sz)
        line = _ellipsize(line_raw, draw, font, inner_w)
        l, t, r, b = draw.textbbox((0, 0), line, font=font, stroke_width=_STROKE_W)
        if (r - l) <= inner_w and (b - t) <= inner_h:
            return font, line
        sz -= 1
    font = _load_font(medium_path, min_sz)
    line = _ellipsize(line_raw, draw, font, inner_w)
    return font, line


def _toast_design_rect(
    *,
    col_right_offset_cells: float = 0.0,
    row_offset_cells: float = 0.0,
    startup_top_left: bool = False,
) -> tuple[int, int, int, int]:
    _ = startup_top_left  # all variants now share one fixed top-right anchor
    return rect_for_span_top_right_at_cell(
        int(_SPAN[0]),
        int(_SPAN[1]),
        row_1based=float(_LOCATION_TOAST_ROW) + float(row_offset_cells),
        col_right_1based=float(_LOCATION_TOAST_COL_RIGHT) + float(col_right_offset_cells),
    )


def location_toast_patch_bgra(
    text: str,
    *,
    alpha: float,
    shadow_bgr: tuple[int, int, int] | None,
    col_right_offset_cells: float = 0.0,
    row_offset_cells: float = 0.0,
    startup_top_left: bool = False,
) -> tuple[np.ndarray | None, tuple[int, int, int, int]]:
    """
    BGRA patch spanning 5×1 cells, top-right aligned with the clock column; text is right-aligned.

    ``col_right_offset_cells`` / ``row_offset_cells`` shift the anchor on the design grid (e.g. pigeonFull).
    ``startup_top_left`` pins the toast to the top-left startup slot.
    """
    a = max(0.0, min(1.0, float(alpha)))
    wx, wy, w, h = _toast_design_rect(
        col_right_offset_cells=col_right_offset_cells,
        row_offset_cells=row_offset_cells,
        startup_top_left=startup_top_left,
    )
    if a < 1e-6:
        return None, (wx, wy, w, h)

    pad = max(2, int(min(w, h) * 0.06))
    inner_w = max(1, w - 2 * pad)
    inner_h = max(1, h - 2 * pad)

    medium_path = resolve_ui_font_medium() or resolve_ui_font_regular()

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font, line = _font_and_line_scaled_vs_clock(text, medium_path, inner_w, inner_h, draw)

    tw, th = _text_size(draw, line, font)
    x_right = w
    y0 = max(pad, (h - th) // 2)

    acc = shadow_bgr if shadow_bgr and len(shadow_bgr) == 3 else _DEFAULT_SHADOW_BGR
    b_bgr, g_bgr, r_bgr = int(acc[0]), int(acc[1]), int(acc[2])
    shadow_rgb = (r_bgr, g_bgr, b_bgr)
    shadow_fill = shadow_rgb + (_SHADOW_ALPHA,)
    off = max(1, int(round(min(w, h) * 0.014)))

    draw.text((x_right + off, y0 + off), line, font=font, fill=shadow_fill, anchor="rt")
    draw.text(
        (x_right, y0),
        line,
        font=font,
        fill=_TEXT_WHITE,
        anchor="rt",
        stroke_width=_STROKE_W,
        stroke_fill=_STROKE_RGBA,
    )

    rgba = np.asarray(img)
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    if a < 0.999:
        bgra = bgra.astype(np.float32)
        bgra[:, :, 3] *= a
        bgra = np.clip(bgra, 0, 255).astype(np.uint8)
    return bgra, (wx, wy, w, h)
