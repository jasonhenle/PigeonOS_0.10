"""Design-resolution shell per widget: full canvas + optional cropped preview for testing."""

from __future__ import annotations

import cv2
import numpy as np

from pigeon.compositing import cv_resize_interp
from pigeon.design import DESIGN_H, DESIGN_W, rect_for_span_at_cell, rect_for_span_from_origin
from pigeon.overlay import (
    blend_overlay_bgr,
    build_stage_overlay_source_bgra,
    build_widget_local_overlay_bgra,
)
from pigeon.widget_protocol import Widget


class WidgetShell:
    """
    Each widget is authored against the full design canvas. Content is anchored at the widget's
    ``grid_anchor`` cell if defined (e.g. ``ClockCalendarWidget``), else grid [1,1].

    For testing small widgets without staring at the full 19×8 sheet, use ``render_preview_crop``
    which crops to the pixel rectangle for ``widget.grid_span`` and optionally draws a local grid.
    """

    def __init__(self, widget: Widget) -> None:
        self._widget = widget

    @property
    def widget(self) -> Widget:
        return self._widget

    def blank_canvas_bgr(self) -> np.ndarray:
        return np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)

    def render_full(self) -> np.ndarray:
        """Full design BGR after widget draw."""
        canvas = self.blank_canvas_bgr()
        self._widget.render(canvas)
        return canvas

    def full_grid_overlay_bgra(self) -> np.ndarray:
        """19×8 grid over full design size (same as main Pigeon overlay)."""
        return build_stage_overlay_source_bgra()

    def composite_full_with_overlay(self, include_overlay: bool = True) -> np.ndarray:
        base = self.render_full()
        if not include_overlay:
            return base
        ov = self.full_grid_overlay_bgra()
        return blend_overlay_bgr(base, ov)

    def crop_rect_for_widget(self) -> tuple[int, int, int, int]:
        span_w, span_h = self._widget.grid_span
        dr = getattr(self._widget, "design_rect", None)
        if callable(dr):
            hit = dr()
            if isinstance(hit, tuple) and len(hit) == 4:
                return int(hit[0]), int(hit[1]), int(hit[2]), int(hit[3])
        anchor = getattr(self._widget, "grid_anchor", None)
        if anchor is not None:
            ar, ac = anchor
            return rect_for_span_at_cell(span_w, span_h, row_1based=ar, col_1based=ac)
        return rect_for_span_from_origin(span_w, span_h)

    def render_preview_crop(
        self,
        *,
        include_overlay: bool = True,
        local_grid: bool = True,
    ) -> np.ndarray:
        """
        Only the pixels for this widget's span (from [1,1]), for comfortable iteration.

        If ``local_grid``, overlay uses a rows×cols grid matching the span with local [row,col] labels.
        If ``include_overlay`` is False, no grid is drawn.
        """
        x, y, cw, ch = self.crop_rect_for_widget()
        base = self.render_full()[y : y + ch, x : x + cw].copy()

        if not include_overlay:
            return base

        span_w, span_h = self._widget.grid_span
        if local_grid:
            ov = build_widget_local_overlay_bgra(cw, ch, rows=span_h, cols=span_w)
        else:
            full_ov = self.full_grid_overlay_bgra()
            ov = full_ov[y : y + ch, x : x + cw].copy()

        return blend_overlay_bgr(base, ov)

    def render_preview_scaled(
        self,
        max_w: int,
        max_h: int,
        *,
        include_overlay: bool = True,
        local_grid: bool = True,
    ) -> np.ndarray:
        """Cropped preview scaled to fit max_w×max_h (letterboxed)."""
        crop = self.render_preview_crop(include_overlay=include_overlay, local_grid=local_grid)
        ch, cw = crop.shape[:2]
        scale = min(max_w / float(cw), max_h / float(ch))
        out_w = max(1, int(round(cw * scale)))
        out_h = max(1, int(round(ch * scale)))
        resized = cv2.resize(
            crop, (out_w, out_h), interpolation=cv_resize_interp(cw, ch, out_w, out_h)
        )
        canvas = np.zeros((max_h, max_w, 3), dtype=np.uint8)
        ox = (max_w - out_w) // 2
        oy = (max_h - out_h) // 2
        canvas[oy : oy + out_h, ox : ox + out_w] = resized
        return canvas
