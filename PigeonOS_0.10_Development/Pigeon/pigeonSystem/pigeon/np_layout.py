"""1280×800 now-playing zone geometry and display-fit helpers.

Zone coordinates: origin is the top-left of the design canvas (0, 0), x right, y down.
The spec listed ``y, x`` pairs (vertical first). Values below are stored as ``(x, y)``.

Portrait slots 1–3 use the widget artboard (~398×488), not the 295-wide inner-disc
measurement — 44.5 + 398 = 442.5 and 442.5 + 398 ≈ 840, matching the column origins.
"""

from __future__ import annotations

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
STATUS_BAR_SERVICE_LOCAL = (3.1, 122.01)
STATUS_BAR_ELAPSED_LOCAL = (574.31, 122.01)
STATUS_BAR_REMAINING_LOCAL = (1067.28, 122.01)
STATUS_BAR_PAUSED_LOCAL = (489.05, 58.29)
STATUS_BAR_TIME_SIZE_PX = 39
STATUS_BAR_PAUSED_SIZE_PX = 58

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


def volume_readout_y_shift(
    *,
    has_source: bool,
    value_baseline_y: float = VOLUME_VALUE_LOCAL[1],
    value_h: float = 0.0,
    scale_baseline_y: float = VOLUME_SCALE_LOCAL[1],
    scale_h: float = 0.0,
    container_cy: float = VOLUME_LOCAL_CY,
) -> float:
    """Y delta that vertically centers value+scale in ``volume_container``.

    When ``volume_source_text`` is present, keep the authored layout (delta 0).
    Otherwise shift both baselines by the same amount so their combined ink box
    is centered on the disc — the gap between value and scale does not change.
    """
    if has_source:
        return 0.0
    tops: list[float] = []
    bottoms: list[float] = []
    if value_h > 0:
        tops.append(float(value_baseline_y) - float(value_h))
        bottoms.append(float(value_baseline_y))
    if scale_h > 0:
        tops.append(float(scale_baseline_y) - float(scale_h))
        bottoms.append(float(scale_baseline_y))
    if not tops:
        return 0.0
    mid = (min(tops) + max(bottoms)) / 2.0
    return float(container_cy) - mid


WIDGET_FILENAMES: dict[str, str] = {
    "clock": "widget_np_01-02-03_clock.svg",
    "volume": "widget_np_01-02-03_volume.svg",
    "cast_info": "widget_np_01-02-03_cast_info.svg",
    "cast_info_z4": "widget_np_04_cast_info.svg",
    "cast_info_z5": "widget_np_05_cast_info.svg",
    "status_bar": "widget_np_05_status_bar.svg",
    "play": "widget_np_01-02-03_play.svg",
    "poster_2x3": "widget_np_01-02-03_poster_2x3.svg",
    "poster_1x1": "widget_np_01-02-03_poster_1x1.svg",
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
