"""Clock widget: **5×1** design-grid cell, **top-right** anchored at grid (row, col_right).

``CLOCK_WIDGET_ROW`` matches ``playback_overlay.AudioConfig.badge_row`` (0.5) so the clock sits on the
same horizontal band as the streaming app logo badge. Time + ``am|pm`` are right-aligned in the span.
Drop shadow uses the same BGR accent as the now-playing progress bar (``set_shadow_accent_bgr``).
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr, scale_bgra_rgb
from pigeon.design import DESIGN_H, DESIGN_W, GRID_COLS, GRID_ROWS, rect_for_span_top_right_at_cell
from pigeon.grid_overlay import overlay_grid_metrics
from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_medium, resolve_ui_font_regular

_SPAN = (5, 1)

# Top-right of the 5×1 span; row matches streaming badge (``AudioConfig.badge_row`` in playback_overlay).
CLOCK_WIDGET_ROW = 0.5
CLOCK_WIDGET_COL_RIGHT = 15.0

_DEFAULT_SHADOW_ACCENT_BGR = (40, 140, 255)

DEFAULT_FAKE_TIME_STRING = "2020-11-30 12:55:00"

_TEXT_WHITE = (255, 255, 255, 255)
_SHADOW_ALPHA = 110


def clock_widget_design_rect() -> tuple[int, int, int, int]:
    """Pixel rectangle (x, y, w, h) for the clock’s 5×1 span (matches live composite placement)."""
    return rect_for_span_top_right_at_cell(
        _SPAN[0],
        _SPAN[1],
        row_1based=CLOCK_WIDGET_ROW,
        col_right_1based=float(CLOCK_WIDGET_COL_RIGHT),
    )


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l, b - t


def _load_font(path: str | None, size: int) -> ImageFont.ImageFont:
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def _time_12h_no_leading_zero(now: datetime) -> str:
    h12 = now.hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{now.strftime('%M')}"


def _resolve_display_time() -> datetime:
    raw = (os.environ.get("PIGEON_FAKE_TIME") or os.environ.get("PIGEON_FAKE_DATETIME") or "").strip()
    if not raw:
        return datetime.now()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        pass
    return datetime.now()


def _fit_segmented_fonts(
    medium_path: str | None,
    bold_path: str | None,
    left: str,
    mid: str,
    right: str,
    draw: ImageDraw.ImageDraw,
    max_w: int,
    max_h: int,
    min_sz: int = 6,
) -> tuple[ImageFont.ImageFont, ImageFont.ImageFont]:
    """One point size; ``mid`` uses bold, ``left``/``right`` use medium (or bold path if no medium)."""
    med_p = medium_path if medium_path else bold_path
    lo, hi = min_sz, max(max_h * 3, max_w, 120)
    best_pair: tuple[ImageFont.ImageFont, ImageFont.ImageFont] | None = None
    while lo <= hi:
        mid_sz = (lo + hi) // 2
        f_med = _load_font(med_p, mid_sz)
        f_bold = _load_font(bold_path, mid_sz)
        tw = (
            (_text_size(draw, left, f_med)[0] if left else 0)
            + _text_size(draw, mid, f_bold)[0]
            + _text_size(draw, right, f_med)[0]
        )
        th = max(
            _text_size(draw, left, f_med)[1] if left else 0,
            _text_size(draw, mid, f_bold)[1],
            _text_size(draw, right, f_med)[1],
        )
        if tw <= max_w and th <= max_h:
            best_pair = (f_med, f_bold)
            lo = mid_sz + 1
        else:
            hi = mid_sz - 1
    if best_pair is None:
        sz = min_sz
        return _load_font(med_p, sz), _load_font(bold_path, sz)
    return best_pair


def design_clock_calendar_medium_font_point_size() -> int:
    """PIL medium-face point size after fitting the clock line in the 5×1 patch."""
    _wx, _wy, w0, h0 = clock_widget_design_rect()
    w, h = max(1, w0), max(1, h0)
    now = _resolve_display_time()
    left = ""
    mid = _time_12h_no_leading_zero(now)
    right = f" {now.strftime('%p').lower()}"

    bold_path = resolve_ui_font_bold()
    medium_path = resolve_ui_font_medium() or resolve_ui_font_regular()

    pad = max(2, int(min(w, h) * 0.04))
    inner_w = max(1, w - 2 * pad)
    # Reserve headroom for ascenders + shadow; ``_text_size`` fitting is tighter than ``textbbox(..., anchor=)``.
    v_slack = max(3, int(round(h * 0.14)))
    inner_h = max(4, h - 2 * pad - v_slack)

    scratch = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(scratch)
    f_med, _f_bold = _fit_segmented_fonts(
        medium_path, bold_path, left, mid, right, draw, inner_w, inner_h
    )
    sz = getattr(f_med, "size", None)
    if isinstance(sz, (int, float)) and int(sz) > 0:
        return int(sz)
    return 12


class ClockCalendarWidget:
    def __init__(
        self,
        *,
        anchor_row: float = CLOCK_WIDGET_ROW,
        anchor_col_right: float = CLOCK_WIDGET_COL_RIGHT,
        placement: Literal["design", "overlay"] = "design",
    ) -> None:
        self._anchor_row = float(anchor_row)
        self._anchor_col_right = float(anchor_col_right)
        self._placement: Literal["design", "overlay"] = placement
        self._shadow_accent_bgr: tuple[int, int, int] = _DEFAULT_SHADOW_ACCENT_BGR

    def design_rect(self) -> tuple[int, int, int, int]:
        if self._placement == "overlay":
            cell_f, x0, y0, _gw, _gh, _ = overlay_grid_metrics(
                DESIGN_W, DESIGN_H, GRID_ROWS, GRID_COLS
            )
            x_right = float(x0) + (float(self._anchor_col_right) - 1.0) * float(cell_f)
            w = int(max(1, round(float(_SPAN[0]) * float(cell_f))))
            h = int(max(1, round(float(_SPAN[1]) * float(cell_f))))
            x = int(round(x_right - float(w)))
            y = int(round(float(y0) + (float(self._anchor_row) - 1.0) * float(cell_f)))
            x = max(0, min(x, int(DESIGN_W) - w))
            y = max(0, min(y, int(DESIGN_H) - h))
            return (x, y, w, h)
        return rect_for_span_top_right_at_cell(
            _SPAN[0],
            _SPAN[1],
            row_1based=self._anchor_row,
            col_right_1based=self._anchor_col_right,
        )

    @property
    def grid_span(self) -> tuple[int, int]:
        return _SPAN

    @property
    def grid_anchor(self) -> tuple[float, float]:
        """Top-right grid coordinate (row, col) for tooling; prefer ``design_rect()`` for pixels."""
        return (self._anchor_row, self._anchor_col_right)

    def set_shadow_accent_bgr(self, bgr: tuple[int, int, int] | None) -> None:
        if bgr is None or len(bgr) != 3:
            self._shadow_accent_bgr = _DEFAULT_SHADOW_ACCENT_BGR
            return
        self._shadow_accent_bgr = (int(bgr[0]), int(bgr[1]), int(bgr[2]))

    def _clock_segments(self, now: datetime) -> tuple[str, str, str]:
        time_s = _time_12h_no_leading_zero(now)
        ampm_s = now.strftime("%p").lower()
        left = ""
        mid = time_s
        right = f" {ampm_s}"
        return left, mid, right

    def _rgba_clock_content(self, w: int, h: int) -> Image.Image:
        now = _resolve_display_time()
        left, mid, right = self._clock_segments(now)

        bold_path = resolve_ui_font_bold()
        medium_path = resolve_ui_font_medium() or resolve_ui_font_regular()

        pad = max(2, int(min(w, h) * 0.04))
        inner_w = max(1, w - 2 * pad)
        v_slack = max(3, int(round(h * 0.14)))
        inner_h = max(4, h - 2 * pad - v_slack)

        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        f_med, f_bold = _fit_segmented_fonts(
            medium_path, bold_path, left, mid, right, draw, inner_w, inner_h
        )

        def _line_bbox_at_right(by: int, x_right: int) -> tuple[int, int, int, int]:
            xr = x_right
            ul, ut = 999999, 999999
            dr, db = -999999, -999999
            for text, font in ((right, f_med), (mid, f_bold), (left, f_med)):
                if not text:
                    continue
                bb = draw.textbbox((xr, by), text, font=font, anchor="rs")
                ul, ut = min(ul, bb[0]), min(ut, bb[1])
                dr, db = max(dr, bb[2]), max(db, bb[3])
                xr = bb[0]
            return ul, ut, dr, db

        x_right = w - pad
        baseline_y = h // 2
        ul, ut, dr, db = _line_bbox_at_right(baseline_y, x_right)
        cy = (ut + db) // 2
        baseline_y += h // 2 - cy
        # Slightly inset vertical band so ascenders / descenders / shadow stay inside the patch.
        band_top = pad + max(1, int(round(h * 0.06)))
        band_bot = h - pad - max(1, int(round(h * 0.06)))
        for _ in range(10):
            ul, ut, dr, db = _line_bbox_at_right(baseline_y, x_right)
            moved = False
            if db > band_bot:
                baseline_y -= db - band_bot
                moved = True
            if ut < band_top:
                baseline_y += band_top - ut
                moved = True
            if not moved:
                break

        b_bgr, g_bgr, r_bgr = self._shadow_accent_bgr
        shadow_rgb = (int(r_bgr), int(g_bgr), int(b_bgr))
        shadow_fill = shadow_rgb + (_SHADOW_ALPHA,)
        off = max(1, int(round(min(w, h) * 0.018)))

        x = x_right
        for text, font in ((right, f_med), (mid, f_bold), (left, f_med)):
            if not text:
                continue
            draw.text((x + off, baseline_y + off), text, font=font, fill=shadow_fill, anchor="rs")
            draw.text((x, baseline_y), text, font=font, fill=_TEXT_WHITE, anchor="rs")
            bb = draw.textbbox((x, baseline_y), text, font=font, anchor="rs")
            x = bb[0]
        return img

    def bgra_patch(self) -> np.ndarray:
        wx, wy, w, h = self.design_rect()
        rgba = np.asarray(self._rgba_clock_content(w, h))
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    def render(self, canvas_bgr: np.ndarray, *, rgb_scale: float = 1.0) -> None:
        wx, wy, w, h = self.design_rect()
        roi = canvas_bgr[wy : wy + h, wx : wx + w]
        rgba = np.asarray(self._rgba_clock_content(w, h))
        bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
        if rgb_scale < 0.999:
            bgra = scale_bgra_rgb(bgra, rgb_scale)
        roi[:] = alpha_blend_bgra_over_bgr(roi, bgra)
