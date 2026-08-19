"""Canonical design resolution and 19×8 grid math (800×480).

152 boxes (19 × 8), square cells centered on canvas. Matches the live Tk window / composite cap.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Design canvas = nominal display / internal composite (performance target).
DESIGN_W = 800
DESIGN_H = 480

GRID_COLS = 19
GRID_ROWS = 8

# Cached (design-sized) full-width bottom gradient, keyed by
# (width, height, row_top×10, peak_row×10, zero_row×10, row_bottom×10, profile,
# bottom_opacity, gradient_bgr). Fractional rows (e.g. 6.5) are stored ×10 as ints.
_playback_lower_gradient_cache: dict[
    tuple[int, int, int, int, int, int, int, float, tuple[int, int, int]],
    tuple[int, int, int, int, np.ndarray],
] = {}


@dataclass(frozen=True)
class GridGeometry:
    cell: int
    x0: int
    y0: int
    grid_w: int
    grid_h: int
    cols: int
    rows: int


def get_grid_geometry(
    *,
    width: int = DESIGN_W,
    height: int = DESIGN_H,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> GridGeometry:
    """Square cells, grid centered in the canvas (may leave blank margin on sides or top/bottom)."""
    cell = min(width // cols, height // rows)
    grid_w = cell * cols
    grid_h = cell * rows
    x0 = (width - grid_w) // 2
    y0 = (height - grid_h) // 2
    return GridGeometry(
        cell=cell,
        x0=x0,
        y0=y0,
        grid_w=grid_w,
        grid_h=grid_h,
        cols=cols,
        rows=rows,
    )


def grid_anchor_top_left_for_centered_span(
    squares_wide: int,
    squares_tall: int,
    *,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> tuple[int, int]:
    """1-based (row, col) for the top-left cell of a span centered in the grid."""
    row = (rows - squares_tall) // 2 + 1
    col = (cols - squares_wide) // 2 + 1
    return row, col


def rect_for_span_at_cell(
    squares_wide: int | float,
    squares_tall: int | float,
    *,
    row_1based: int | float,
    col_1based: int | float,
    width: int = DESIGN_W,
    height: int = DESIGN_H,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> tuple[int, int, int, int]:
    """
    Pixel rectangle (x, y, w, h) for a widget whose **top-left** sits on grid cell [row, col] (1-based).

    squares_wide / squares_tall = span in grid cells (may be fractional, e.g. 1.5 rows).
    ``row_1based`` and ``col_1based`` may be fractional (half-cell nudges).
    """
    g = get_grid_geometry(width=width, height=height, cols=cols, rows=rows)
    x = int(round(g.x0 + (float(col_1based) - 1.0) * g.cell))
    y = int(round(g.y0 + (float(row_1based) - 1.0) * g.cell))
    w = int(round(float(squares_wide) * g.cell))
    h = int(round(float(squares_tall) * g.cell))
    y = max(0, min(y, height - h))
    x = max(0, min(x, width - w))
    return (x, y, w, h)


def rect_for_span_top_right_at_cell(
    squares_wide: int,
    squares_tall: int,
    *,
    row_1based: int | float,
    col_right_1based: float,
    width: int = DESIGN_W,
    height: int = DESIGN_H,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> tuple[int, int, int, int]:
    """
    Pixel rectangle (x, y, w, h) whose **top-right** aligns to grid coordinate (row, col_right_1based).

    ``col_right_1based`` may be fractional (e.g. 15.5 → half-cell); x = x0 + (col - 1) * cell - w.
    ``row_1based`` may be fractional (half-cell vertical nudge).
    """
    g = get_grid_geometry(width=width, height=height, cols=cols, rows=rows)
    x_right = g.x0 + (float(col_right_1based) - 1.0) * g.cell
    w = squares_wide * g.cell
    h = squares_tall * g.cell
    x = int(round(x_right - w))
    y = int(round(g.y0 + (float(row_1based) - 1.0) * g.cell))
    x = max(0, min(x, width - w))
    y = max(0, min(y, height - h))
    return (x, y, w, h)


def playback_lower_gradient_bgra(
    *,
    width: int = DESIGN_W,
    height: int = DESIGN_H,
    row_top_1based: float = 6.5,
    row_bottom_1based: int = 8,
    bottom_opacity: float = 0.7,
    gradient_bgr: tuple[int, int, int] = (0, 0, 0),
    gradation_end_row_1based: float = 8.0,
    alpha_zero_row_1based: float = 8.0,
) -> tuple[int, int, int, int, np.ndarray]:
    """
    Full-width bottom gradient in **design grid rows** (1-based, same origin as
    :func:`rect_for_span_at_cell`).

    **Default (single ramp):** tint is **fully off by row 6.5** and ramps **up** toward the
    bottom so it is **strongest at row 8** (bottom of the 8-row grid). Nothing is drawn
    above ``row_top_1based``. The patch ends at the bottom edge of ``row_bottom_1based``
    (default **8** → ``y = y0 + 8 * cell``).

    **Legacy tent (optional):** when ``row_top_1based < gradation_end_row_1based <
    alpha_zero_row_1based``, keep the older symmetric profile: ramp **up** to a peak at
    ``gradation_end_row_1based``, then ramp **down** to alpha **0** by
    ``alpha_zero_row_1based``.

    ``gradient_bgr`` default ``(0, 0, 0)`` is the black tint; ``(255, 255, 255)`` is the
    contrast-aware white tint from :mod:`pigeon.tmdb_tt_contrast`. ``bottom_opacity`` is the
    peak alpha (0.7 ≈ 178).

    Returns ``(x, y, w, h, bgra)`` with ``x=0``, ``w=width``.
    """
    bgr_key = (int(gradient_bgr[0]) & 0xFF, int(gradient_bgr[1]) & 0xFF, int(gradient_bgr[2]) & 0xFF)
    # Cache keys use ×10 ints so we can round-trip fractional row positions (e.g. 7.5, 8.0)
    # without float-equality issues while still hashing cleanly.
    top_key = int(round(float(row_top_1based) * 10.0))
    end_key = int(round(float(gradation_end_row_1based) * 10.0))
    zero_key = int(round(float(alpha_zero_row_1based) * 10.0))
    bottom_key = int(round(float(row_bottom_1based) * 10.0))
    use_tent = bool(
        float(row_top_1based)
        < float(gradation_end_row_1based)
        < float(alpha_zero_row_1based)
    )
    # ``2`` = single ramp through canvas bottom (profile changed vs legacy row-8-only endpoint).
    profile_key = 1 if use_tent else 2
    cache_key = (
        int(width),
        int(height),
        top_key,
        end_key,
        zero_key,
        bottom_key,
        profile_key,
        float(bottom_opacity),
        bgr_key,
    )
    hit = _playback_lower_gradient_cache.get(cache_key)
    if hit is not None:
        return hit

    g = get_grid_geometry(width=width, height=height)
    # Fractional rows are supported: e.g. row_top_1based=7.5 lands on the midpoint of row 7.
    y_top = int(round(g.y0 + (float(row_top_1based) - 1.0) * g.cell))
    y_top = max(0, min(int(height) - 1, y_top))

    patch: np.ndarray
    peak = int(round(float(bottom_opacity) * 255.0))
    peak = max(0, min(255, peak))
    peak_f = float(peak)

    if use_tent:
        # Bottom edge: alpha must reach 0 by this grid line (patch covers y_top .. y_end_px - 1).
        y_end_px = int(round(g.y0 + (float(alpha_zero_row_1based) - 1.0) * g.cell))
        y_end_px = max(y_top + 2, min(int(height), y_end_px))
        h_grad = max(1, y_end_px - y_top)
        y_peak = int(round(g.y0 + (float(gradation_end_row_1based) - 1.0) * g.cell))
        # Allow ``y_peak == y_top`` so a 2-row strip can be 0 → peak → 0 (all-up would miss the fade).
        y_peak = max(y_top, min(y_end_px - 2, y_peak))

        patch = np.zeros((h_grad, width, 4), dtype=np.uint8)
        patch[:, :, 0] = bgr_key[0]
        patch[:, :, 1] = bgr_key[1]
        patch[:, :, 2] = bgr_key[2]

        i = np.arange(h_grad, dtype=np.float64)
        yy = y_top + i
        den_up = float(max(1, y_peak - y_top))
        den_dn = float(max(1, (y_end_px - 1) - y_peak))
        m_up = yy <= float(y_peak)
        a = np.zeros(h_grad, dtype=np.float64)
        a[m_up] = (yy[m_up] - float(y_top)) / den_up * peak_f
        a[~m_up] = (1.0 - (yy[~m_up] - float(y_peak)) / den_dn) * peak_f
        alpha_col = np.clip(np.rint(a), 0, 255).astype(np.uint8)
        patch[:, :, 3] = alpha_col[:, np.newaxis]
    else:
        # Single ramp: alpha 0 at ``row_top_1based``, strongest at the **physical canvas bottom**.
        # The 8-row grid can sit letterboxed inside ``height``; extending to ``height`` removes the
        # bare strip below grid row 8 (bottom edge of the composite window).
        y_end_px = max(y_top + 2, int(height))
        h_grad = max(1, y_end_px - y_top)

        patch = np.zeros((h_grad, width, 4), dtype=np.uint8)
        patch[:, :, 0] = bgr_key[0]
        patch[:, :, 1] = bgr_key[1]
        patch[:, :, 2] = bgr_key[2]

        if h_grad <= 1:
            a = np.array([peak_f], dtype=np.float64)
        else:
            i = np.arange(h_grad, dtype=np.float64)
            a = i / float(h_grad - 1) * peak_f
        alpha_col = np.clip(np.rint(a), 0, 255).astype(np.uint8)
        patch[:, :, 3] = alpha_col[:, np.newaxis]
    result = (0, y_top, width, h_grad, patch)
    _playback_lower_gradient_cache[cache_key] = result
    return result


def rect_for_span_from_origin(
    squares_wide: int,
    squares_tall: int,
    *,
    width: int = DESIGN_W,
    height: int = DESIGN_H,
    cols: int = GRID_COLS,
    rows: int = GRID_ROWS,
) -> tuple[int, int, int, int]:
    """
    Pixel rectangle (x, y, w, h) for a widget anchored at grid cell [1, 1] (1-based).

    squares_wide = columns spanned, squares_tall = rows spanned (e.g. 6×3 → wide=6, tall=3).
    """
    return rect_for_span_at_cell(
        squares_wide,
        squares_tall,
        row_1based=1,
        col_1based=1,
        width=width,
        height=height,
        cols=cols,
        rows=rows,
    )
