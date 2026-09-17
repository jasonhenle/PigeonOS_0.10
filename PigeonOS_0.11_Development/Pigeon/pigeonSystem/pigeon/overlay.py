"""
Grid overlay for Pigeon (single extension point as overlays grow).

Main stage: full 19×8 design grid at design resolution, then mapped to the display.
Widget shell: local grid on a cropped region or full-stage overlay for alignment checks.
"""

from __future__ import annotations

import numpy as np

from pigeon.compositing import alpha_blend_bgra_over_bgr, scale_height_and_center_crop
from pigeon.design import DESIGN_H, DESIGN_W, GRID_COLS, GRID_ROWS
from pigeon.grid_overlay import (
    build_absolute_grid_overlay_bgra,
    build_absolute_grid_overlay_view_five_c_bgra,
    build_grid_overlay_bgra,
)

# Static at design resolution — cached per stage mode.
_stage_overlay_bgra_cache: dict[str, np.ndarray] = {}


def build_stage_overlay_source_bgra(mode: str = "legacy_grid"):
    """Return cached design overlay for the requested stage mode."""
    global _stage_overlay_bgra_cache
    key = str(mode or "legacy_grid").strip().lower()
    hit = _stage_overlay_bgra_cache.get(key)
    if hit is not None:
        return hit
    if key == "absolute_lines":
        ov = build_absolute_grid_overlay_bgra(DESIGN_W, DESIGN_H, with_fractional_lines=False)
    elif key == "absolute_lines_fractional":
        ov = build_absolute_grid_overlay_bgra(DESIGN_W, DESIGN_H, with_fractional_lines=True)
    elif key == "absolute_lines_test":
        ov = build_absolute_grid_overlay_view_five_c_bgra(DESIGN_W, DESIGN_H)
    else:
        ov = build_grid_overlay_bgra(
            DESIGN_W, DESIGN_H, rows=GRID_ROWS, cols=GRID_COLS
        )
    _stage_overlay_bgra_cache[key] = ov
    return ov


def build_widget_local_overlay_bgra(width: int, height: int, rows: int, cols: int):
    """Grid overlay for a cropped widget or arbitrary rectangle (local [row,col] labels)."""
    return build_grid_overlay_bgra(width, height, rows=rows, cols=cols)


def blend_overlay_bgr(base_bgr, overlay_bgra):
    """Composite BGRA overlay onto BGR (same dimensions)."""
    return alpha_blend_bgra_over_bgr(base_bgr, overlay_bgra)


def stage_overlay_for_display(display_w: int, display_h: int):
    """
    Main UI: scale design overlay by height, center-crop to display (e.g. 800×480 window).
    """
    src = build_stage_overlay_source_bgra()
    return scale_height_and_center_crop(src, display_w, display_h)
