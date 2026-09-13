"""1280×800 now-playing zone geometry and display-fit helpers.

Zone coordinates: origin is the top-left of the design canvas (0, 0), x right, y down.
The spec listed ``y, x`` pairs (vertical first). Values below are stored as ``(x, y)``.

Portrait slots 1–3 use the widget artboard (~398×488), not the 295-wide inner-disc
measurement — 44.5 + 398 = 442.5 and 442.5 + 398 ≈ 840, matching the column origins.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from pigeon.compositing import scale_uniform_letterbox
from pigeon.design import DESIGN_H, DESIGN_W, LEGACY_DESIGN_H, LEGACY_DESIGN_W

# Widget artboard for zone 1–3 SVGs (viewBox ≈ 398×488).
_ZONE_PORTRAIT_W = 398.0
_ZONE_PORTRAIT_H = 488.0

# Red marker for screens that still need a 1280×800 rebuild.
LEGACY_UPDATE_MARK_PX = 18
LEGACY_UPDATE_MARK_BGR = (0, 0, 255)


@dataclass(frozen=True)
class NowPlayingZone:
    index: int
    x: float
    y: float
    w: float
    h: float
    restrictions: tuple[int, ...]

    @property
    def xywh(self) -> tuple[int, int, int, int]:
        return (
            int(round(self.x)),
            int(round(self.y)),
            int(round(self.w)),
            int(round(self.h)),
        )


# index → zone. Restrictions: if this zone is on, listed zones must be off.
NOW_PLAYING_ZONES: dict[int, NowPlayingZone] = {
    1: NowPlayingZone(1, 44.5, 34.0, _ZONE_PORTRAIT_W, _ZONE_PORTRAIT_H, (6, 8)),
    2: NowPlayingZone(2, 442.5, 34.0, _ZONE_PORTRAIT_W, _ZONE_PORTRAIT_H, (6, 7, 8)),
    3: NowPlayingZone(3, 840.0, 34.0, _ZONE_PORTRAIT_W, _ZONE_PORTRAIT_H, (7, 8)),
    4: NowPlayingZone(4, 44.5, 525.5, 1190.0, 130.0, (9,)),
    5: NowPlayingZone(5, 44.5, 636.0, 1190.0, 130.0, (9,)),
    6: NowPlayingZone(6, 44.5, 34.0, 793.0, _ZONE_PORTRAIT_H, (1, 2, 8)),
    7: NowPlayingZone(7, 442.5, 34.0, 793.0, _ZONE_PORTRAIT_H, (2, 3, 8)),
    8: NowPlayingZone(8, 44.5, 34.0, 1190.0, 109.0, (1, 2, 3, 6, 7)),
    # Idle clock saver: whole design frame (digital clocksaver / centered clock widget).
    9: NowPlayingZone(9, 0.0, 0.0, float(DESIGN_W), float(DESIGN_H), (1, 2, 3, 4, 5, 6, 7, 8)),
}

# Widget-local geometry (SVG viewBox space) for zones 1–3.
CLOCK_LOCAL_CX = 200.0
CLOCK_LOCAL_CY = 288.11
VOLUME_LOCAL_CX = 199.55
VOLUME_LOCAL_CY = 288.52
VOLUME_OUTER_R = 198.45
VOLUME_INNER_R = 162.46

# 2×3 poster mask in 398×488 viewBox (rounded rect from poster_mask path).
POSTER_2X3_LOCAL = (42.68, 6.34, 315.26, 472.90, 15.98)
# 1×1 album mask.
POSTER_1X1_LOCAL = (42.69, 85.16, 315.26, 315.26, 26.47)

# 16×9 poster across zone 6 (zones 1+2) or zone 7 (zones 2+3). YouTube uses
# zone 6 so volume can stay in portrait column 3. Side insets match 2×3.
# Corner radius follows widget_np_0*_16x9.
DEFAULT_16X9_POSTER_ZONE = 6
# Forced now-playing layout while YouTube is the foreground app.
# Zone 4 stays unassigned as a widget: video title is drawn into that strip
# the same way music draws track / artist / album.
YOUTUBE_ZONE_WIDGETS: tuple[str, str, str, str, str] = (
    "",
    "",
    "volume",
    "",
    "status_bar",
)
POSTER_16X9_ASPECT_MIN = 1.4  # wider than 4:3 → treat as landscape/16×9
_POSTER_16X9_INSET_L = POSTER_2X3_LOCAL[0]
_POSTER_16X9_INSET_R = _ZONE_PORTRAIT_W - POSTER_2X3_LOCAL[0] - POSTER_2X3_LOCAL[2]
POSTER_16X9_VIEW_W = float(NOW_PLAYING_ZONES[6].w)
POSTER_16X9_VIEW_H = float(NOW_PLAYING_ZONES[6].h)
_POSTER_16X9_W = POSTER_16X9_VIEW_W - _POSTER_16X9_INSET_L - _POSTER_16X9_INSET_R
_POSTER_16X9_H = _POSTER_16X9_W * 9.0 / 16.0
# Same vertical center as the zone-3 volume disc (not the geometric mid of zone 6).
_POSTER_16X9_Y = min(
    max(0.0, VOLUME_LOCAL_CY - _POSTER_16X9_H / 2.0),
    POSTER_16X9_VIEW_H - _POSTER_16X9_H,
)
# 8px radius on the 267×150 widget art → ~21.3px at the 16×9 slot width.
_POSTER_16X9_RX = 8.0 * (_POSTER_16X9_W / 267.0)
POSTER_16X9_LOCAL = (
    _POSTER_16X9_INSET_L,
    _POSTER_16X9_Y,
    _POSTER_16X9_W,
    _POSTER_16X9_H,
    _POSTER_16X9_RX,
)
_16X9_RELOCATE_PRIORITY = (
    "clock",
    "volume",
    "audio_levels",
    "tt_countdown",
    "tt_countdown_16x9",
    "now_playing",
    "cast_info",
)

# Cast text baselines in 399×488 viewBox (actor then character, rows 1–5).
CAST_LOCAL_ROWS: tuple[tuple[float, float, float], ...] = (
    (93.5, 42.34, 75.82),
    (93.5, 142.49, 175.98),
    (93.5, 243.25, 276.74),
    (93.5, 343.52, 377.00),
    (93.5, 444.15, 477.64),
)
CAST_CHAR_LOCAL_X = 136.11
CAST_ACTOR_SIZE_PX = 45
CAST_CHAR_SIZE_PX = 33
# Strip (zones 4–5) uses the same type as portrait, stacked actor over character.
CAST_STRIP_ACTOR_SIZE_PX = CAST_ACTOR_SIZE_PX
CAST_STRIP_CHAR_SIZE_PX = CAST_CHAR_SIZE_PX
CAST_NAMES_PER_ZONE = 5
CAST_NAMES_PER_STRIP = 3
CAST_VIEW_W = 399.0
CAST_VIEW_H = 488.0

# Horizontal cast strips (zones 4–5): viewBox 974.01×73.66, 3 columns.
CAST_STRIP_VIEW_W = 974.01
CAST_STRIP_VIEW_H = 73.66
# (actor_x, actor_baseline_y, character_x, character_baseline_y)
CAST_STRIP_COLS: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 36.53, 42.61, 70.01),
    (382.3, 36.53, 424.91, 70.01),
    (764.59, 36.53, 807.21, 70.01),
)
# Actor over character, both center-aligned in each portrait-aligned column.
CAST_STRIP_STACK_GAP_PX = 8

# Zone 5 status bar. Illustrator artboard is 1190×334.78 with empty space below
# the used cluster (~y 37..167). Crop to 1190×130 so it maps 1:1 onto zone 5.
STATUS_BAR_VIEW_W = 1190.0
STATUS_BAR_VIEW_H = 130.0
STATUS_BAR_VIEW_Y0 = 36.5
# remaining_icon rounded rect in full artboard space, then shifted by VIEW_Y0.
STATUS_BAR_TRACK = (68.62, 10.16, 1066.81, 65.49, 13.07)
# Digital clock saver: same zone-5 track, stepped into 60 second cells.
CLOCK_SAVER_SECONDS_SEGMENTS = 60
CLOCK_SAVER_SECONDS_GAP_PX = 3.0
STATUS_BAR_SERVICE_LOCAL = (3.1, 122.01)
# Authored mid-bar X is unused: elapsed parks at SERVICE_LOCAL and rides the fill.
STATUS_BAR_ELAPSED_LOCAL = (574.31, 122.01)
STATUS_BAR_REMAINING_LOCAL = (1067.28, 122.01)
STATUS_BAR_PAUSED_LOCAL = (489.05, 58.29)
STATUS_BAR_TIME_SIZE_PX = 39
STATUS_BAR_PAUSED_SIZE_PX = 58
# Gap between the service name and the traveling elapsed readout.
STATUS_BAR_LABEL_GAP_PX = 24.0
# Keep elapsed just short of the remaining label before it fades away.
STATUS_BAR_ELAPSED_COLLIDE_GAP_PX = 16.0
# Fade elapsed over this many extra pixels of playhead travel.
STATUS_BAR_ELAPSED_FADE_PX = 56.0
# Crossfade service in once traveling elapsed has cleared it.
STATUS_BAR_HANDOFF_S = 0.45

# Current-time readout hangs from the top of the frame and may overlap
# the widget row slightly so the type can read larger than the 34px margin.
NP_HEADER_CLOCK_BASELINE_Y = 34.0
NP_HEADER_CLOCK_SIZE_PX = 64
NP_HEADER_CLOCK_TOP_PAD_PX = 6.0
NP_HEADER_CLOCK_BG_PAD_X = 22.0
NP_HEADER_CLOCK_BG_PAD_Y = 10.0
NP_HEADER_CLOCK_BG_RADIUS = 16.0
NP_HEADER_CLOCK_BG_OPACITY = 1.0

# Clock widget viewBox + header / digital baselines (widget-local).
CLOCK_VIEW_W = 400.0
CLOCK_VIEW_H = 488.11
CLOCK_DAY_LOCAL = (24.53, 67.46)
CLOCK_MONTH_DATE_LOCAL = (213.63, 67.46)
CLOCK_HEADER_SIZE_PX = 55
CLOCK_DIGITAL_SIZE_PX = 61
# Black oval in ``clock_center_container`` (not the exterior disc).
CLOCK_DIGITAL_LOCAL = (199.24, 289.29)
# Small downward optical nudge after ink-centering on the oval.
CLOCK_DIGITAL_NUDGE = (0.0, 1.0)

# Volume widget viewBox + text (widget-local).
VOLUME_VIEW_W = 398.0
VOLUME_VIEW_H = 488.0
VOLUME_FORMAT_LOCAL = (98.15, 67.46)
VOLUME_SOURCE_LOCAL = (125.39, 214.75)
VOLUME_VALUE_LOCAL = (63.59, 324.9)
VOLUME_SCALE_LOCAL = (152.28, 420.28)
VOLUME_FORMAT_SIZE_PX = 48
VOLUME_SOURCE_SIZE_PX = 48
VOLUME_VALUE_SIZE_PX = 125
VOLUME_SCALE_SIZE_PX = 100

# TT + countdown widget (zones 1–3): TMDb title treatment above a live
# remaining-time countdown. The ``divider`` rect in the SVG is a guide only —
# it is never rendered. The TT hangs its bottom edge on the divider's top
# edge; the countdown hangs its top line from the divider's bottom edge.
# Both are horizontally centered on the divider.
TT_COUNTDOWN_VIEW_W = 398.0
TT_COUNTDOWN_VIEW_H = 488.0
TT_COUNTDOWN_DIVIDER_LOCAL = (41.37, 310.0, 315.26, 40.0)  # x, y, w, h
TT_COUNTDOWN_TT_INSET_X = 12.0
TT_COUNTDOWN_TT_TOP_Y = 34.0
TT_COUNTDOWN_TEXT_SIZE_PX = 80
# Inset used when a tall 16×9 TT sits beside the countdown.
TT_COUNTDOWN_BG_PAD = 25.0
# TT art darker than this luminance is recolored pure white before display.
TT_COUNTDOWN_DARK_TT_LUMINANCE_MAX = 0.25

# Skeleton shimmer for widgets still waiting on TMDb / artwork.
WIDGET_SHIMMER_PERIOD_S = 1.35
WIDGET_SHIMMER_STEPS = 48
WIDGET_SHIMMER_BAND_FRAC = 0.42
WIDGET_SHIMMER_OPACITY = 0.92
WIDGET_SHIMMER_RADIUS = 18.0


def widget_shimmer_phase(now_mono: float | None = None) -> float:
    """0..1 sweep position for the loading shimmer."""
    t = time.monotonic() if now_mono is None else float(now_mono)
    return (t / max(0.05, float(WIDGET_SHIMMER_PERIOD_S))) % 1.0


def widget_shimmer_step(now_mono: float | None = None) -> int:
    """Quantized shimmer frame so the NP cache can animate without a full rebuild."""
    return int(widget_shimmer_phase(now_mono) * WIDGET_SHIMMER_STEPS) % int(
        WIDGET_SHIMMER_STEPS
    )


def tt_countdown_tt_box() -> tuple[float, float, float, float]:
    """Widget-local (x, y, w, h) box the title treatment must fit inside.

    Full widget width (minus a small inset); bottom edge sits on the divider's
    top edge.
    """
    _dx, div_y, _dw, _dh = TT_COUNTDOWN_DIVIDER_LOCAL
    x = TT_COUNTDOWN_TT_INSET_X
    y = TT_COUNTDOWN_TT_TOP_Y
    w = TT_COUNTDOWN_VIEW_W - TT_COUNTDOWN_TT_INSET_X * 2.0
    h = div_y - y
    return (x, y, w, h)


def tt_countdown_time_anchor() -> tuple[float, float]:
    """Widget-local (center_x, top_y) anchor for the countdown text.

    Centered on the divider; the text's top line starts at the divider's
    bottom edge.
    """
    div_x, div_y, div_w, div_h = TT_COUNTDOWN_DIVIDER_LOCAL
    return (div_x + div_w / 2.0, div_y + div_h)


# Wide TT + countdown (zone 6 or 7, 793×488). Asset: widget_np_06-07_tt_countdown.svg
# (covers slots 6 and 7). Four divider guides are never rendered:
#   top_divider        TT top sits on the bottom of this bar after the group lift
#   left_divider       landscape TT left sits on its right side
#   right_divider      landscape TT right sits on its left side
#   horizontal_divider seats landscape TT bottom / TRT top; the group then lifts
#                      so it rests just under top_divider
TT_COUNTDOWN_16X9_VIEW_W = 793.0
TT_COUNTDOWN_16X9_VIEW_H = 488.0
TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL = (238.87, 24.0, 315.26, 20.0)
TT_COUNTDOWN_16X9_LEFT_DIVIDER_LOCAL = (90.0, 44.0, 20.0, 286.0)
TT_COUNTDOWN_16X9_RIGHT_DIVIDER_LOCAL = (683.0, 44.0, 20.0, 286.0)
TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL = (238.87, 330.0, 315.26, 40.0)
TT_COUNTDOWN_16X9_WIDGET = "tt_countdown_16x9"


def tt_countdown_16x9_tt_box() -> tuple[float, float, float, float]:
    """Widget-local (x, y, w, h) max landscape TT bounds from the divider guides.

    - left edge  = right side of ``left_divider``
    - right edge = left side of ``right_divider``
    - top edge   = bottom of ``top_divider``
    - bottom     = top of ``horizontal_divider``

    Landscape TTs width-fit this box. The seated TT+TRT group then shifts so
    the pair is vertically centered in the widget — see
    ``tt_countdown_16x9_content_lift``.
    """
    lx, _ly, lw, _lh = TT_COUNTDOWN_16X9_LEFT_DIVIDER_LOCAL
    rx, _ry, _rw, _rh = TT_COUNTDOWN_16X9_RIGHT_DIVIDER_LOCAL
    _tx, ty, _tw, th = TT_COUNTDOWN_16X9_TOP_DIVIDER_LOCAL
    _hx, hy, _hw, _hh = TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL
    x = lx + lw
    y = ty + th
    w = rx - x
    h = hy - y
    return (x, y, w, h)


def tt_countdown_16x9_tt_is_portrait(src_w: float, src_h: float) -> bool:
    """True when width-fitting the landscape box would overflow its height."""
    if float(src_w) <= 0.0 or float(src_h) <= 0.0:
        return False
    _x, _y, box_w, box_h = tt_countdown_16x9_tt_box()
    return (float(src_h) / float(src_w)) * box_w > box_h + 0.5


def tt_countdown_16x9_portrait_rects(
    src_w: float,
    src_h: float,
    trt_w: float,
    trt_h: float,
    *,
    pad: float = TT_COUNTDOWN_BG_PAD,
) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
    """Side-by-side layout for a tall TT: left-aligned art, right-aligned TRT.

    TT fills the widget height when it can, shrinking only enough to leave
    room for the countdown beside it. TRT is vertically centered on the TT.
    """
    view_w, view_h = TT_COUNTDOWN_16X9_VIEW_W, TT_COUNTDOWN_16X9_VIEW_H
    inset = max(0.0, float(pad))
    gap = 20.0
    trt_w = max(0.0, float(trt_w))
    trt_h = max(0.0, float(trt_h))
    max_h = max(1.0, view_h - 2.0 * inset)
    max_w = max(1.0, view_w - 2.0 * inset - gap - trt_w)
    sw = max(1e-6, float(src_w))
    sh = max(1e-6, float(src_h))
    scale = max_h / sh
    if sw * scale > max_w:
        scale = max_w / sw
    tw = sw * scale
    th = sh * scale
    tt_x = inset
    tt_y = (view_h - th) / 2.0
    trt_x = view_w - inset - trt_w
    trt_y = tt_y + th / 2.0 - trt_h / 2.0
    return (tt_x, tt_y, tw, th), (trt_x, trt_y, trt_w, trt_h)


def tt_countdown_centered_art_rect(
    src_w: float,
    src_h: float,
    *,
    view_w: float,
    view_h: float,
    pad: float = TT_COUNTDOWN_BG_PAD,
) -> tuple[float, float, float, float]:
    """Widget-local (x, y, w, h) for art fitted and centered in the view.

    Music album art uses this instead of the landscape TT box + TRT pair.
    """
    inset = max(0.0, float(pad))
    max_w = max(1.0, float(view_w) - 2.0 * inset)
    max_h = max(1.0, float(view_h) - 2.0 * inset)
    sw = max(1e-6, float(src_w))
    sh = max(1e-6, float(src_h))
    scale = min(max_w / sw, max_h / sh)
    tw = sw * scale
    th = sh * scale
    x = (float(view_w) - tw) / 2.0
    y = (float(view_h) - th) / 2.0
    return (x, y, tw, th)


def tt_countdown_content_center_shift(
    *,
    tt_height: float,
    trt_height: float = 0.0,
    seat_y: float,
    gap: float,
    view_h: float,
) -> float:
    """Upward shift that vertically centers the TT+TRT group in the card.

    Content is first seated on the horizontal divider (TT bottom at ``seat_y``,
    TRT top at ``seat_y + gap``), then this delta is subtracted from both Ys.
    """
    tt_h = max(0.0, float(tt_height))
    trt_h = max(0.0, float(trt_height))
    g = max(0.0, float(gap)) if trt_h > 0.0 else 0.0
    group_top = float(seat_y) - tt_h
    group_h = tt_h + g + trt_h
    if group_h <= 0.0:
        return 0.0
    desired_top = (float(view_h) - group_h) / 2.0
    return group_top - desired_top


def tt_countdown_portrait_content_lift(
    tt_height: float, *, trt_height: float = 0.0
) -> float:
    """Center-shift for the portrait TT + countdown card."""
    _dx, seat_y, _dw, gap = TT_COUNTDOWN_DIVIDER_LOCAL
    return tt_countdown_content_center_shift(
        tt_height=tt_height,
        trt_height=trt_height,
        seat_y=seat_y,
        gap=gap,
        view_h=TT_COUNTDOWN_VIEW_H,
    )


def tt_countdown_16x9_content_lift(
    tt_height: float, *, trt_height: float = 0.0
) -> float:
    """Shift the seated landscape group so TT+TRT are vertically centered."""
    _hx, seat_y, _hw, gap = TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL
    return tt_countdown_content_center_shift(
        tt_height=tt_height,
        trt_height=trt_height,
        seat_y=seat_y,
        gap=gap,
        view_h=TT_COUNTDOWN_16X9_VIEW_H,
    )


def layout_shows_tt_countdown_and_volume(
    assignments: tuple[str, ...] | list[str],
) -> bool:
    """True when the live layout has both a TT countdown and a volume disc."""
    names = [str(n or "").strip() for n in list(assignments)[:5]]
    has_vol = "volume" in names
    has_tt = any(n in ("tt_countdown", TT_COUNTDOWN_16X9_WIDGET) for n in names)
    return has_vol and has_tt


def tt_countdown_volume_align_dy(
    *,
    plate_top: float,
    plate_bottom: float,
    volume_cy: float,
    zone_top: float,
    zone_bottom: float,
) -> float:
    """Y delta that centers the TT+TRT group on the volume disc, clamped to the zone."""
    top = float(plate_top)
    bottom = float(plate_bottom)
    height = bottom - top
    if height <= 0.0:
        return 0.0
    desired_top = float(volume_cy) - height / 2.0
    max_top = float(zone_bottom) - height
    min_top = float(zone_top)
    if max_top < min_top:
        return 0.0
    clamped_top = min(max(desired_top, min_top), max_top)
    return clamped_top - top


def tt_countdown_16x9_time_anchor(*, lift: float = 0.0) -> tuple[float, float]:
    """Widget-local (center_x, top_y) for the TRT under the horizontal divider.

    ``lift`` is the upward group shift from ``tt_countdown_16x9_content_lift``.
    """
    hx, hy, hw, hh = TT_COUNTDOWN_16X9_HORIZONTAL_DIVIDER_LOCAL
    return (hx + hw / 2.0, hy + hh - max(0.0, float(lift)))


def tt_countdown_16x9_zone(assignments: tuple[str, ...] | list[str]) -> int | None:
    """Wide slot for a ``tt_countdown_16x9`` assignment: 6 (slots 1–2) or 7 (slot 3)."""
    zones = [str(n or "").strip() for n in list(assignments)[:3]]
    for i, name in enumerate(zones):
        if name == TT_COUNTDOWN_16X9_WIDGET:
            return 7 if i == 2 else 6
    return None


def apply_tt_countdown_16x9_override(
    assignments: tuple[str, ...] | list[str],
) -> tuple[str, str, str, str, str]:
    """Blank the portrait slots covered by a wide ``tt_countdown_16x9``.

    The marker stays in its stored slot (1–3); the sibling portrait zone the
    wide slot spans (zone 6 → 1+2, zone 7 → 2+3) is cleared so nothing draws
    underneath.
    """
    zones = [str(n or "").strip() for n in list(assignments)[:5]]
    while len(zones) < 5:
        zones.append("")
    wide = tt_countdown_16x9_zone(zones)
    if wide is not None:
        covered = (1, 2) if wide == 6 else (2, 3)
        keep = next(
            i for i in range(3) if zones[i] == TT_COUNTDOWN_16X9_WIDGET
        )
        for z in covered:
            if (z - 1) != keep:
                zones[z - 1] = ""
        # Only one wide card — drop later copies so zone 6 and 7 never fight.
        for i, name in enumerate(zones):
            if i != keep and name == TT_COUNTDOWN_16X9_WIDGET:
                zones[i] = ""
    return (zones[0], zones[1], zones[2], zones[3], zones[4])


def tabular_time_layout(
    text: str,
    *,
    digit_cell_w: float,
    char_widths: dict[str, float],
) -> tuple[tuple[tuple[str, float, float], ...], float]:
    """Per-character cells for a fixed-width countdown readout.

    Every digit occupies an equal-width cell (``digit_cell_w``, normally the
    widest digit's advance) so the readout never jitters as values change.
    Non-digits (``:``, ``-``) keep their natural width. Returns
    ``((char, cell_x, cell_w), ...)`` plus the total width; glyphs should be
    drawn centered inside their cell.
    """
    cells: list[tuple[str, float, float]] = []
    x = 0.0
    for ch in str(text or ""):
        natural = float(char_widths.get(ch, digit_cell_w))
        cell_w = float(digit_cell_w) if ch.isdigit() else natural
        cells.append((ch, x, cell_w))
        x += cell_w
    return tuple(cells), x


WIDGET_FILENAMES: dict[str, str] = {
    "clock": "widget_np_01-02-03_clock.svg",
    "volume": "widget_np_01-02-03_volume.svg",
    "cast_info": "widget_np_01-02-03_cast_info.svg",
    "cast_info_z4": "widget_np_04_cast_info.svg",
    "cast_info_z5": "widget_np_05_cast_info.svg",
    "status_bar": "widget_np_05_status_bar.svg",
    "tt_countdown": "widget_np_01-02-03_tt_countdown.svg",
    "tt_countdown_16x9": "widget_np_06-07_tt_countdown.svg",
    "play": "widget_np_01-02-03_play.svg",
    "poster_2x3": "widget_np_01-02-03_poster_2x3.svg",
    "poster_1x1": "widget_np_01-02-03_poster_1x1.svg",
    "poster_16x9": "widget_np_06_16x9.svg",
    "poster_16x9_z6": "widget_np_06_16x9.svg",
    "poster_16x9_z7": "widget_np_07_16x9.svg",
}


def canonical_zone_widget(zone: int, name: str) -> str:
    """Zone 5 bar is ``status_bar``; zones 1–3 circular NP stays ``now_playing``."""
    n = str(name or "").strip()
    z = int(zone)
    if n in ("now_playing", "status_bar"):
        return "status_bar" if z == 5 else "now_playing"
    return n


def is_status_bar_widget(name: str, zone: int) -> bool:
    return canonical_zone_widget(zone, name) == "status_bar"


def widget_filename(widget_key: str, zone: int | None = None) -> str:
    """Resolve a now-playing SVG name, including zone-specific cast / status-bar art."""
    z = int(zone) if zone is not None else 0
    key = canonical_zone_widget(z, str(widget_key or "").strip())
    if key == "cast_info" and z == 4:
        return WIDGET_FILENAMES["cast_info_z4"]
    if key == "cast_info" and z == 5:
        return WIDGET_FILENAMES["cast_info_z5"]
    if key in ("status_bar", "now_playing"):
        return WIDGET_FILENAMES["status_bar"]
    if key == "poster_16x9":
        if z == 7:
            return WIDGET_FILENAMES["poster_16x9_z7"]
        return WIDGET_FILENAMES["poster_16x9_z6"]
    return WIDGET_FILENAMES.get(key, "")


def cast_names_for_zone(zone: int) -> int:
    """Portrait slots hold 5 names; zone 4/5 strips hold 3."""
    return CAST_NAMES_PER_STRIP if int(zone) in (4, 5) else CAST_NAMES_PER_ZONE


def strip_cast_columns(zone: NowPlayingZone) -> tuple[tuple[float, float], ...]:
    """Three strip slots aligned to portrait zones 1–3, clipped to ``zone``."""
    x0 = float(zone.x)
    x1 = float(zone.x) + float(zone.w)
    cols: list[tuple[float, float]] = []
    for idx in (1, 2, 3):
        portrait = NOW_PLAYING_ZONES[idx]
        cx0 = max(x0, float(portrait.x))
        cx1 = min(x1, float(portrait.x) + float(portrait.w))
        if cx1 - cx0 >= 40.0:
            cols.append((cx0, cx1 - cx0))
    if len(cols) == 3:
        return tuple(cols)
    inset = 12.0
    col_w = (float(zone.w) - inset * 2.0) / 3.0
    return tuple((x0 + inset + col_w * i, col_w) for i in range(3))


def zone_fits_canvas(zone: NowPlayingZone, *, width: int = DESIGN_W, height: int = DESIGN_H) -> bool:
    x, y, w, h = zone.xywh
    return x >= 0 and y >= 0 and x + w <= int(width) + 1 and y + h <= int(height) + 1


def occupied_zone_indexes(assignments: tuple[str, ...] | list[str]) -> set[int]:
    """1-based indexes whose assignment is a non-empty widget name."""
    out: set[int] = set()
    for i, name in enumerate(assignments):
        if str(name or "").strip():
            out.add(i + 1)
    return out


def service_requests_16x9_poster(service_name: str | None) -> bool:
    """True when the foreground app should show landscape/16×9 poster art (YouTube)."""
    return "youtube" in str(service_name or "").strip().lower()


def poster_image_is_16x9(poster_bgra: np.ndarray | None) -> bool:
    """True when artwork is landscape (wider than ``POSTER_16X9_ASPECT_MIN``)."""
    if poster_bgra is None:
        return False
    arr = np.asarray(poster_bgra)
    if arr.ndim < 2 or arr.size == 0:
        return False
    h, w = int(arr.shape[0]), int(arr.shape[1])
    if h < 1 or w < 1:
        return False
    return (w / float(h)) >= float(POSTER_16X9_ASPECT_MIN)


def wants_16x9_poster(
    *,
    service_name: str | None = None,
    poster_bgra: np.ndarray | None = None,
    content_mode: str | None = None,
) -> bool:
    """Whether live now-playing should use the zone-6 16×9 YouTube thumbnail.

    Other services keep poster art in the portrait 2×3 slot even when the
    bitmap itself is landscape.
    """
    del poster_bgra
    mode = str(content_mode or "video").strip().lower()
    if mode == "music":
        return False
    return service_requests_16x9_poster(service_name)


def apply_16x9_poster_override(
    assignments: tuple[str, ...] | list[str],
    *,
    zone: int = DEFAULT_16X9_POSTER_ZONE,
) -> tuple[str, str, str, str, str]:
    """Runtime layout: 16×9 poster occupies zone 6 or 7; prefs are not persisted.

    Zone 6 (default, YouTube) spans portrait slots 1+2 so volume can stay in
    column 3. Zone 7 spans 2+3. The portrait ``poster`` assignment is removed.
    Other widgets displaced by the wide slot move into remaining empty
    portrait columns (clock before volume).
    """
    z = int(zone)
    if z not in (6, 7):
        z = DEFAULT_16X9_POSTER_ZONE
    zones = [str(n or "").strip() for n in list(assignments)[:5]]
    while len(zones) < 5:
        zones.append("")
    spec = NOW_PLAYING_ZONES.get(z)
    blocked_portrait = {
        int(i) for i in (spec.restrictions if spec is not None else ()) if 1 <= int(i) <= 3
    }
    displaced: list[str] = []
    for i, name in enumerate(zones):
        if not name:
            continue
        if name == "poster":
            zones[i] = ""
            continue
        if (i + 1) in blocked_portrait:
            displaced.append(name)
            zones[i] = ""
    empty_portrait = [
        i for i in range(3) if not zones[i] and (i + 1) not in blocked_portrait
    ]
    seen = {n for n in zones if n}

    def _prio(name: str) -> int:
        try:
            return _16X9_RELOCATE_PRIORITY.index(name)
        except ValueError:
            return len(_16X9_RELOCATE_PRIORITY)

    for name in sorted(displaced, key=_prio):
        if name in seen or not empty_portrait:
            continue
        i = empty_portrait.pop(0)
        zones[i] = name
        seen.add(name)
    return (zones[0], zones[1], zones[2], zones[3], zones[4])


def apply_zone_restrictions(occupied: set[int]) -> set[int]:
    """Drop zones that conflict with an already-occupied restricted partner.

    Earlier (lower-index) occupied zones win; a later zone that lists an earlier
    occupied zone in its restrictions is disabled.
    """
    enabled = set(occupied)
    for idx in sorted(occupied):
        if idx not in enabled:
            continue
        zone = NOW_PLAYING_ZONES.get(idx)
        if zone is None:
            continue
        for other in zone.restrictions:
            enabled.discard(int(other))
    return enabled


def design_xy_from_local(
    zone: NowPlayingZone,
    local_x: float,
    local_y: float,
    *,
    view_w: float = _ZONE_PORTRAIT_W,
    view_h: float = _ZONE_PORTRAIT_H,
) -> tuple[float, float]:
    sx = float(zone.w) / max(1.0, float(view_w))
    sy = float(zone.h) / max(1.0, float(view_h))
    return zone.x + local_x * sx, zone.y + local_y * sy


def contain_scale(zone: NowPlayingZone, *, view_w: float, view_h: float) -> float:
    return min(
        float(zone.w) / max(1.0, float(view_w)),
        float(zone.h) / max(1.0, float(view_h)),
    )


def design_xy_contain(
    zone: NowPlayingZone,
    local_x: float,
    local_y: float,
    *,
    view_w: float,
    view_h: float,
) -> tuple[float, float]:
    """Map widget-local coords with uniform scale-to-fit, centered in the zone."""
    s = contain_scale(zone, view_w=view_w, view_h=view_h)
    ox = zone.x + (float(zone.w) - float(view_w) * s) * 0.5
    oy = zone.y + (float(zone.h) - float(view_h) * s) * 0.5
    return ox + float(local_x) * s, oy + float(local_y) * s


def design_rect_from_local(
    zone: NowPlayingZone,
    local: tuple[float, float, float, float, float],
    *,
    view_w: float = _ZONE_PORTRAIT_W,
    view_h: float = _ZONE_PORTRAIT_H,
) -> tuple[int, int, int, int, int]:
    lx, ly, lw, lh, rx = local
    x0, y0 = design_xy_from_local(zone, lx, ly, view_w=view_w, view_h=view_h)
    x1, y1 = design_xy_from_local(zone, lx + lw, ly + lh, view_w=view_w, view_h=view_h)
    sx = float(zone.w) / max(1.0, float(view_w))
    return (
        int(round(x0)),
        int(round(y0)),
        max(1, int(round(x1 - x0))),
        max(1, int(round(y1 - y0))),
        max(1, int(round(rx * sx))),
    )


def status_bar_playhead_x(
    *,
    track_x: float,
    track_w: float,
    progress: float,
) -> float:
    """X of the played / unplayed transition."""
    vis = max(0.0, min(float(track_w), float(progress) * float(track_w)))
    return float(track_x) + vis


def status_bar_elapsed_center_x(
    *,
    track_x: float,
    track_w: float,
    progress: float,
    elapsed_w: float,
    remaining_left_x: float | None = None,
    gap: float = STATUS_BAR_ELAPSED_COLLIDE_GAP_PX,
) -> float:
    """Elapsed stays centered on the playhead until just before remaining."""
    center = status_bar_playhead_x(
        track_x=track_x, track_w=track_w, progress=progress
    )
    half = max(0.0, float(elapsed_w)) * 0.5
    if remaining_left_x is not None and half > 0.0:
        center = min(center, float(remaining_left_x) - float(gap) - half)
    return center


def status_bar_elapsed_left_x(
    *,
    track_x: float,
    track_w: float,
    progress: float,
    elapsed_w: float,
    remaining_left_x: float | None = None,
    gap: float = STATUS_BAR_ELAPSED_COLLIDE_GAP_PX,
    park_x: float | None = None,
) -> float:
    """Left edge of elapsed centered on the playhead."""
    _ = park_x
    return status_bar_elapsed_center_x(
        track_x=track_x,
        track_w=track_w,
        progress=progress,
        elapsed_w=elapsed_w,
        remaining_left_x=remaining_left_x,
        gap=gap,
    ) - max(0.0, float(elapsed_w)) * 0.5


def status_bar_elapsed_opacity(
    *,
    track_x: float,
    track_w: float,
    progress: float,
    elapsed_w: float,
    remaining_left_x: float | None,
    gap: float = STATUS_BAR_ELAPSED_COLLIDE_GAP_PX,
    fade_px: float = STATUS_BAR_ELAPSED_FADE_PX,
) -> float:
    """1 while there is room; 0 once the playhead would collide with remaining."""
    if remaining_left_x is None or float(elapsed_w) <= 0.0:
        return 1.0
    playhead = status_bar_playhead_x(
        track_x=track_x, track_w=track_w, progress=progress
    )
    half = float(elapsed_w) * 0.5
    room = float(remaining_left_x) - float(gap) - (playhead + half)
    fade = max(1.0, float(fade_px))
    if room >= fade:
        return 1.0
    if room <= 0.0:
        return 0.0
    return room / fade


def now_playing_header_clock_text(now) -> str:
    """Top-of-NP clock: ``10:23PM`` (no leading zero, no space)."""
    hour = int(getattr(now, "hour", 0)) % 12
    if hour == 0:
        hour = 12
    minute = int(getattr(now, "minute", 0))
    suffix = "AM" if int(getattr(now, "hour", 0)) < 12 else "PM"
    return f"{hour}:{minute:02d}{suffix}"


def header_clock_center_x(
    assignments: tuple[str, ...] | list[str] | None = None,
) -> float:
    """Horizontal center for the NP header clock.

    Aligns with wide TT / album art (zone 6 or 7) when that card is on;
    otherwise the frame midpoint.
    """
    wide = tt_countdown_16x9_zone(assignments or ())
    if wide in (6, 7):
        z = NOW_PLAYING_ZONES[int(wide)]
        return float(z.x) + float(z.w) * 0.5
    return float(DESIGN_W) * 0.5


def status_bar_service_has_room(
    *,
    service_x: float,
    service_w: float,
    elapsed_x: float,
    gap: float = STATUS_BAR_LABEL_GAP_PX,
) -> bool:
    """True when traveling elapsed is far enough right for the service name to fit."""
    if service_w <= 0:
        return False
    return float(elapsed_x) >= float(service_x) + float(service_w) + float(gap)


def clock_saver_seconds_filled(second: int) -> int:
    """How many of the 60 cells are on for clock second ``0..59``.

    The current second's cell fills on the tick and stays until the next
    second. ``:00`` shows 1 cell; ``:59`` shows all 60.
    """
    return (int(second) % 60) + 1


def clock_saver_seconds_track_rect() -> tuple[int, int, int, int, int]:
    """Design-space ``(x, y, w, h, radius)`` of the NP zone-5 status track."""
    return design_rect_from_local(
        NOW_PLAYING_ZONES[5],
        STATUS_BAR_TRACK,
        view_w=STATUS_BAR_VIEW_W,
        view_h=STATUS_BAR_VIEW_H,
    )


def clock_saver_seconds_segment_rects(
    track_xywh: tuple[float, float, float, float],
    *,
    count: int = CLOCK_SAVER_SECONDS_SEGMENTS,
    gap: float = CLOCK_SAVER_SECONDS_GAP_PX,
) -> list[tuple[int, int, int, int]]:
    """``count`` equal cells across *track_xywh* with a fixed gap between them."""
    x, y, w, h = (float(v) for v in track_xywh)
    n = max(1, int(count))
    g = max(0.0, float(gap))
    usable = float(w) - g * (n - 1)
    if usable < n:
        g = 0.0
        usable = float(w)
    seg = usable / float(n)
    yi = int(round(y))
    hi = max(1, int(round(h)))
    xs = [x + i * (seg + g) for i in range(n)]
    rects: list[tuple[int, int, int, int]] = []
    for i, x0 in enumerate(xs):
        x1 = (x + w) if i == n - 1 else xs[i + 1] - g
        rects.append(
            (
                int(round(x0)),
                yi,
                max(1, int(round(x1) - round(x0))),
                hi,
            )
        )
    return rects


def status_bar_handoff_alphas(t: float) -> tuple[float, float, float]:
    """Opacities for (parked elapsed, service, traveling elapsed). ``t`` is 0..1."""
    u = max(0.0, min(1.0, float(t)))
    return (1.0 - u, u, u)


def display_fit_scale(display_w: int, display_h: int) -> float:
    """
    Uniform scale so the 1280×800 UI never crops.

    Wider-than-design aspect → scale by height (pillarbox).
    Narrower aspect → scale by width (letterbox).
    """
    dw = max(1, int(display_w))
    dh = max(1, int(display_h))
    design_aspect = float(DESIGN_W) / float(DESIGN_H)
    display_aspect = float(dw) / float(dh)
    if display_aspect >= design_aspect:
        return float(dh) / float(DESIGN_H)
    return float(dw) / float(DESIGN_W)


def letterbox_legacy_ui(image: np.ndarray) -> np.ndarray:
    """Fit an 800×480 leftover screen into the 1280×800 canvas without cropping."""
    if image is None or image.size == 0:
        ch = 3
        return np.zeros((DESIGN_H, DESIGN_W, ch), dtype=np.uint8)
    src = image
    if int(src.shape[1]) != int(LEGACY_DESIGN_W) or int(src.shape[0]) != int(LEGACY_DESIGN_H):
        # Already at design size or another raster — contain-fit into design.
        return scale_uniform_letterbox(src, int(DESIGN_W), int(DESIGN_H))
    return scale_uniform_letterbox(src, int(DESIGN_W), int(DESIGN_H))


def is_native_design_frame(image: np.ndarray) -> bool:
    """True when *image* is already the 1280×800 design canvas."""
    if image is None or image.size == 0 or image.ndim < 2:
        return False
    return int(image.shape[1]) == int(DESIGN_W) and int(image.shape[0]) == int(DESIGN_H)


def stamp_legacy_update_mark(image: np.ndarray, *, px: int = LEGACY_UPDATE_MARK_PX) -> None:
    """Opaque red square at (0, 0) — screens that still need a 1280×800 rebuild."""
    if image is None or image.size == 0:
        return
    n = max(4, int(px))
    h, w = int(image.shape[0]), int(image.shape[1])
    n = min(n, h, w)
    image[0:n, 0:n, 0] = LEGACY_UPDATE_MARK_BGR[0]
    image[0:n, 0:n, 1] = LEGACY_UPDATE_MARK_BGR[1]
    image[0:n, 0:n, 2] = LEGACY_UPDATE_MARK_BGR[2]
    if image.ndim == 3 and image.shape[2] >= 4:
        image[0:n, 0:n, 3] = 255
