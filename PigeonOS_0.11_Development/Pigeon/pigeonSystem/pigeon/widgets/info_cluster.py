"""Top-right ``infoCluster``: stacked time, audio format, volume, and (brief) location.

The **clock** 5×1 span is top-right anchored at grid ``(row, col) = (1, 18)`` via
:class:`~pigeon.widgets.clock_calendar.ClockCalendarWidget` / ``rect_for_span_top_right_at_cell``.
Stacked text shares the span’s **outer** right pixel ``wx + ww`` (the same corner as the grid
anchor). Rows/columns use :func:`pigeon.grid_overlay.overlay_grid_metrics` (same as the cyan
debug overlay), not :func:`pigeon.design.get_grid_geometry`, so the cluster lines up with the
grid you see on screen.
"""

from __future__ import annotations

from pigeon.design import DESIGN_H, DESIGN_W, GRID_COLS, GRID_ROWS
from pigeon.widgets.clock_calendar import ClockCalendarWidget
from pigeon.widgets.playback_overlay import (
    _receiver_audio_display_line,
    _receiver_volume_display_line,
    _text_patch_bgra,
)
from pigeon.grid_overlay import overlay_grid_metrics
from pigeon.widgets.status_bar import DesignPatch

PATCH_LAYER_INFO_CLUSTER = "info_cluster"

# Clock 5×1 span: **top-right** corner at grid row 1 (overlay grid; ``col_right_1based`` convention).
# ``18`` matches the legacy “column 17 right edge” line (``x = x0 + 17 * cell`` on the overlay grid).
INFO_CLUSTER_CLOCK_ROW_1BASED = 1.0
INFO_CLUSTER_CLOCK_COL_RIGHT_1BASED = 18.0

# Nudge the clock patch down so line 1 clears the window top (design px).
_INFO_CLUSTER_CLOCK_TOP_PAD_PX = 16

# Vertical gaps between stacked rows (design px). Tighter under the clock pulls lines 2–4 up slightly.
_INFO_CLUSTER_GAP_AFTER_CLOCK_PX = 1
_INFO_CLUSTER_GAP_BETWEEN_TEXT_PX = 2
# One px tighter than audio→volume so line 4 doesn’t read “drifted” below line 3 (still overlap-safe).
_INFO_CLUSTER_GAP_VOL_TO_LOC_PX = 1

# Stacked text band height (fraction of overlay cell). Must leave room for glyphs without shrinking to specks.
_INFO_CLUSTER_STACK_LINE_H_FR = 0.72

# Lines 2–3: fit-box scale for ``_text_patch_bgra`` (still below line 1 clock; avoid ~0.4 which reads microscopic).
_INFO_CLUSTER_AUDIO_FIT_BOX_SCALE = 1.0
_INFO_CLUSTER_VOLUME_FIT_BOX_SCALE = 1.0
# Line 4: same ballpark; slightly softer so location stays a touch quieter than format/volume.
_INFO_CLUSTER_LOCATION_FIT_BOX_SCALE = 0.80

# Fraction of each stacked row height used for font fitting (rest is vertical breathing room).
_INFO_CLUSTER_TEXT_FIT_MAX_FR = 0.96

# ``ClockCalendarWidget.anchor_col_right`` == ``rect_for_span_top_right_at_cell(..., col_right_1based=…)``.
INFO_CLUSTER_COL_RIGHT = float(INFO_CLUSTER_CLOCK_COL_RIGHT_1BASED)

# Vertical anchors for stacked text when the clock is absent; with a clock, indices ≥1
# are used (typically 2.5, 3.5, 4.5).
INFO_CLUSTER_LINE_ROWS: tuple[float, float, float, float] = (1.5, 2.5, 3.5, 4.5)

# Text rows may span up to this many cells leftward from the shared right edge.
_INFO_TEXT_CELLS_WIDE_CAP = 17

# Match ``AudioConfig`` playback line; volume uses the same ink as audio (info cluster only).
_PLAYBACK_SETTING_RGBA = (235, 238, 244, 210)
_VOLUME_RGBA = _PLAYBACK_SETTING_RGBA


def _compact_cluster_slots(
    *,
    include_clock: bool,
    audio_config: str,
    volume: str,
    location: str,
    location_alpha: float,
) -> list[tuple[str, str]]:
    """Return up to four ``(kind, text)`` entries in display order; ``kind`` is ``clock|audio|vol|loc``."""
    a = _receiver_audio_display_line(audio_config)
    v = _receiver_volume_display_line(volume)
    loc = (location or "").strip()
    loc_ok = bool(loc) and float(location_alpha) > 1e-6

    out: list[tuple[str, str]] = []
    if include_clock:
        out.append(("clock", ""))
    if a:
        out.append(("audio", str(a)))
    if v:
        out.append(("vol", str(v)))
    if loc_ok:
        out.append(("loc", loc))
    return out[:4]


def _info_cluster_overlay_cell() -> tuple[float, float, float]:
    """``(cell_f, x0, y0)`` matching the debug grid overlay (fit-to-height, centered in X)."""
    cell_f, x0, y0, _gw, _gh, _cr = overlay_grid_metrics(
        DESIGN_W, DESIGN_H, GRID_ROWS, GRID_COLS
    )
    return (float(cell_f), float(x0), float(y0))


def _pixel_x_right_info_cluster_fallback() -> int:
    """Shared right edge when no clock patch (overlay grid, same convention as ``ClockCalendarWidget``)."""
    cell_f, x0, _y0 = _info_cluster_overlay_cell()
    x_right = float(x0) + (float(INFO_CLUSTER_CLOCK_COL_RIGHT_1BASED) - 1.0) * cell_f
    return int(round(x_right))


def build_info_cluster_design_patches(
    *,
    clock_widget: ClockCalendarWidget | None,
    audio_config: str,
    volume: str,
    location: str,
    location_alpha: float,
    shadow_bgr: tuple[int, int, int] | None,
) -> list[DesignPatch]:
    """Rasterize the cluster for the current design canvas (800×480)."""
    slots = _compact_cluster_slots(
        include_clock=clock_widget is not None,
        audio_config=audio_config,
        volume=volume,
        location=location,
        location_alpha=location_alpha,
    )
    if not slots:
        return []

    cell_f, gx0, gy0 = _info_cluster_overlay_cell()
    x_right_col = _pixel_x_right_info_cluster_fallback()

    use_text_stack = (
        clock_widget is not None
        and bool(slots)
        and str(slots[0][0]) == "clock"
    )
    stack_next_y: float | None = None
    stack_line_h: int | None = None

    blits: list[DesignPatch] = []
    x_align_px: int | None = None
    line_cursor = 0

    slot_list = list(slots)
    for si, (kind, text) in enumerate(slot_list):
        if kind == "clock" and clock_widget is not None:
            if shadow_bgr is not None:
                clock_widget.set_shadow_accent_bgr(shadow_bgr)
            wx, wy, ww, wh = clock_widget.design_rect()
            wy = int(wy) + int(_INFO_CLUSTER_CLOCK_TOP_PAD_PX)
            wy = max(0, min(int(wy), int(DESIGN_H) - int(wh)))
            bgra = clock_widget.bgra_patch()
            blits.append(
                DesignPatch(
                    x=int(wx),
                    y=int(wy),
                    w=int(ww),
                    h=int(wh),
                    bgra=bgra,
                    layer=PATCH_LAYER_INFO_CLUSTER,
                )
            )
            # Grid (1, 18) anchors the **span’s top-right**; text columns use that outer edge.
            x_align_px = int(int(wx) + int(ww))
            stack_next_y = float(int(wy) + int(wh) + int(_INFO_CLUSTER_GAP_AFTER_CLOCK_PX))
            stack_line_h = max(12, int(round(float(cell_f) * float(_INFO_CLUSTER_STACK_LINE_H_FR))))
            line_cursor = 1
            continue

        if x_align_px is None:
            x_align_px = int(x_right_col)

        row = float(INFO_CLUSTER_LINE_ROWS[line_cursor])

        xr = int(x_align_px)
        x0g = int(round(gx0))
        max_ww = max(8, int(round(float(xr) - gx0)))
        ww_i = min(int(round(float(_INFO_TEXT_CELLS_WIDE_CAP) * cell_f)), max_ww)
        ww_i = max(8, ww_i)
        wx_i = xr - ww_i
        wx_i = max(x0g, min(wx_i, int(DESIGN_W) - ww_i))
        ww_i = max(8, xr - wx_i)

        if use_text_stack and stack_next_y is not None and stack_line_h is not None:
            wh = int(stack_line_h)
            wy = int(max(0, min(int(DESIGN_H) - int(wh), int(round(stack_next_y)))))
            nxt = slot_list[si + 1] if si + 1 < len(slot_list) else None
            nxt_k = str(nxt[0]) if nxt is not None else ""
            if str(kind) == "vol" and nxt_k == "loc":
                gap_down = int(_INFO_CLUSTER_GAP_VOL_TO_LOC_PX)
            else:
                gap_down = int(_INFO_CLUSTER_GAP_BETWEEN_TEXT_PX)
            stack_next_y = float(int(wy) + int(wh) + gap_down)
        else:
            # One full grid row tall per line when there is no clock stack.
            wh = int(max(10, round(cell_f)))
            wy = int(round(float(gy0) + (float(row) - 1.0) * cell_f))
            wy = max(0, min(wy, int(DESIGN_H) - wh))
        line_cursor += 1

        _fit_h = max(6, int(round(float(_INFO_CLUSTER_TEXT_FIT_MAX_FR) * float(wh))))
        if kind == "audio":
            fill = _PLAYBACK_SETTING_RGBA
            bgra = _text_patch_bgra(
                text,
                int(ww_i),
                int(wh),
                align="right",
                fill_rgba=fill,
                fit_max_h=_fit_h,
                edge_pad_px=0,
                fit_box_scale=float(_INFO_CLUSTER_AUDIO_FIT_BOX_SCALE),
            )
        elif kind == "vol":
            bgra = _text_patch_bgra(
                text,
                int(ww_i),
                int(wh),
                align="right",
                fill_rgba=_VOLUME_RGBA,
                fit_max_h=_fit_h,
                edge_pad_px=0,
                fit_box_scale=float(_INFO_CLUSTER_VOLUME_FIT_BOX_SCALE),
            )
        else:
            a = int(round(255.0 * max(0.0, min(1.0, float(location_alpha)))))
            bgra = _text_patch_bgra(
                text,
                int(ww_i),
                int(wh),
                align="right",
                fill_rgba=(255, 255, 255, max(0, min(255, a))),
                fit_max_h=_fit_h,
                edge_pad_px=0,
                fit_box_scale=float(_INFO_CLUSTER_LOCATION_FIT_BOX_SCALE),
            )
        blits.append(
            DesignPatch(
                x=int(wx_i),
                y=int(wy),
                w=int(ww_i),
                h=int(wh),
                bgra=bgra,
                layer=PATCH_LAYER_INFO_CLUSTER,
            )
        )
    return blits
