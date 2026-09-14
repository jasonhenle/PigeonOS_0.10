"""Now-play widgets page — ``settings_pigeon_widgets.svg``.

Opened from settings_pigeon NOW PLAY.

Navigation A (zones): cycle the four zone shapes, then BACK.
Navigation B (widgets): after a zone is activated, cycle that zone's widget
labels. Label focus changes the widget *in the activated zone* only.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.settings_layout import SETTINGS_BACKGROUND_SHIFT_Y, SETTINGS_CANVAS_ORIGIN
from pigeon.widgets.main_settings import (
    MainSettingsState,
    _find_by_logical_id,
    _prune_display_none,
    _set_paint,
    _set_visible,
)

_COLOR_WHITE = "#FFFFFF"
_COLOR_IDLE = "#D60000"
_COLOR_UNAVAILABLE = "#5A5A5A"
_COLOR_ZONE_IDLE = "#202020"
# Header labels start at board x=747.84 → canvas ~166, which collides with BACK.
_LABELS_SHIFT_X = 56.0

# Same board crop as settings_pigeon so labels/zones land on the 1280 canvas.
_WIDGETS_VIEWBOX = (
    SETTINGS_CANVAS_ORIGIN[0],
    SETTINGS_CANVAS_ORIGIN[1] - SETTINGS_BACKGROUND_SHIFT_Y,
    float(DESIGN_W),
    float(DESIGN_H),
)

# Header labels, left → right in the export.
WIDGET_FOCUS_IDS: tuple[str, ...] = (
    "artwork",
    "volume",
    "status",
    "info",
    "clock",
    "weather",
)

# Plate zones, reading order, then BACK.
ZONE_FOCUS_IDS: tuple[str, ...] = ("zone6", "zone3", "zone4", "zone5")

_WIDGET_LABEL_IDS: dict[str, str] = {
    "artwork": "widget_labels_artwork_text",
    "volume": "widget_labels_volume_text",
    "status": "widget_labels_status_text",
    "info": "widget_labels_info_text",
    "clock": "widget_labels_clock_text",
    "weather": "widget_labels_weather_text",
}

_ZONE_SHAPE_IDS: dict[str, str] = {
    "zone6": "zone6_shape",
    "zone3": "zone3_shape",
    "zone4": "zone4_shape",
    "zone5": "zone5_shape",
}

# SVG board rects (x, y, w, h) from settings_pigeon_widgets.svg.
_ZONE_SHAPE_SVG: dict[str, tuple[float, float, float, float]] = {
    "zone6": (966.4, 960.88, 385.91, 248.8),
    "zone3": (1359.35, 960.88, 192.3, 248.8),
    "zone4": (966.81, 1222.23, 582.23, 63.14),
    "zone5": (966.4, 1298.16, 582.23, 68.16),
}

# Each zone has its own list. Clock / weather / info can appear in more than one.
ZONE_WIDGET_LISTS: dict[str, tuple[str, ...]] = {
    "zone6": ("artwork", "clock", "weather"),
    "zone3": ("volume", "clock"),
    "zone4": ("info", "weather"),
    "zone5": ("status", "info"),
}

_DEFAULT_WIDGET_BY_ZONE: dict[str, str] = {
    "zone6": "artwork",
    "zone3": "volume",
    "zone4": "info",
    "zone5": "status",
}

_ARTWORK_KEYS = frozenset(("tt_countdown_16x9", "tt_countdown", "poster"))
_DEMO_CAST: tuple[tuple[str, str], ...] = (
    ("bill murray", "bob wiley"),
    ("richard dreyfuss", "leo marvin"),
    ("julie hagerty", "fey marvin"),
)
_DEMO_TITLE = "THE TITLE"
_DEMO_REMAINING = "-1:30:00"
_DEMO_ELAPSED = "0:30:00"
_DEMO_PROGRESS = 30.0 / 120.0
_DEMO_VOLUME = "22.5"
_DEMO_VOLUME_FRAC = 0.72
_ZONE_INSET = 8.0


def widgets_focus_ring(state: MainSettingsState | None = None) -> tuple[str, ...]:
    nav = str(getattr(state, "widgets_nav", "zones") or "zones") if state else "zones"
    if state is not None and nav == "widgets":
        zone = str(getattr(state, "widgets_active_zone", "") or "")
        catalog = ZONE_WIDGET_LISTS.get(zone, ())
        return catalog + ("pigeon_back",)
    return ZONE_FOCUS_IDS + ("pigeon_back",)


def zone_catalog(zone_id: str) -> tuple[str, ...]:
    return ZONE_WIDGET_LISTS.get(str(zone_id or ""), ())


def _zone_tuple(state: MainSettingsState | None) -> tuple[str, str, str, str, str]:
    from pigeon.widgets.preferences_settings import DEFAULT_ZONE_WIDGETS

    raw = getattr(state, "preferences_zone_widgets", None) if state is not None else None
    zones = list(raw or DEFAULT_ZONE_WIDGETS)
    while len(zones) < 5:
        zones.append("")
    return (
        str(zones[0] or ""),
        str(zones[1] or ""),
        str(zones[2] or ""),
        str(zones[3] or ""),
        str(zones[4] or ""),
    )


def widget_id_for_zone(state: MainSettingsState | None, zone_id: str) -> str:
    """Header widget currently assigned to ``zone_id``."""
    z1, _z2, z3, z4, z5 = _zone_tuple(state)
    if zone_id == "zone6":
        if z1 == "clock":
            return "clock"
        if z1 == "weather":
            return "weather"
        if z1 in _ARTWORK_KEYS or not z1:
            return "artwork"
        return "artwork"
    if zone_id == "zone3":
        return "clock" if z3 == "clock" else "volume"
    if zone_id == "zone4":
        return "weather" if z4 == "weather" else "info"
    if zone_id == "zone5":
        return "info" if z5 == "cast_info" else "status"
    return _DEFAULT_WIDGET_BY_ZONE.get(zone_id, "")


def assigned_widget_ids(state: MainSettingsState) -> frozenset[str]:
    """Header widgets currently sitting in any of the four zones."""
    assigned: set[str] = set()
    for zone in ZONE_FOCUS_IDS:
        wid = widget_id_for_zone(state, zone)
        if wid:
            assigned.add(wid)
    return frozenset(assigned)


def ensure_default_zone_widgets(
    state: MainSettingsState,
) -> tuple[str, str, str, str, str]:
    """Fill empty now-playing slots with the four default widgets."""
    from pigeon.widgets.preferences_settings import (
        DEFAULT_ZONE_WIDGETS,
        write_now_playing_zone_widgets,
    )

    current = list(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    while len(current) < 5:
        current.append("")
    changed = False
    if not str(current[0] or "").strip():
        current[0] = DEFAULT_ZONE_WIDGETS[0]
        changed = True
    if not str(current[2] or "").strip():
        current[2] = DEFAULT_ZONE_WIDGETS[2]
        changed = True
    if not str(current[3] or "").strip():
        current[3] = DEFAULT_ZONE_WIDGETS[3]
        changed = True
    if not str(current[4] or "").strip():
        current[4] = DEFAULT_ZONE_WIDGETS[4]
        changed = True
    out = (
        str(current[0] or ""),
        str(current[1] or ""),
        str(current[2] or ""),
        str(current[3] or ""),
        str(current[4] or ""),
    )
    state.preferences_zone_widgets = out
    if changed:
        write_now_playing_zone_widgets(out)
    return out


def apply_widget_assignment(
    state: MainSettingsState,
    widget_id: str,
    *,
    zone_id: str | None = None,
    persist: bool = True,
) -> bool:
    """Write ``widget_id`` into the activated zone. Returns False if unmapped."""
    from pigeon.widgets.preferences_settings import (
        DEFAULT_ZONE_WIDGETS,
        write_now_playing_zone_widgets,
    )

    zone = str(zone_id or getattr(state, "widgets_active_zone", "") or "")
    catalog = ZONE_WIDGET_LISTS.get(zone, ())
    if widget_id not in catalog:
        return False
    current = list(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    while len(current) < 5:
        current.append("")
    if zone == "zone6":
        if widget_id == "artwork":
            current[0], current[1] = "tt_countdown_16x9", ""
        elif widget_id == "clock":
            current[0], current[1] = "clock", ""
        elif widget_id == "weather":
            current[0], current[1] = "weather", ""
        else:
            return False
    elif zone == "zone3":
        current[2] = "clock" if widget_id == "clock" else "volume"
    elif zone == "zone4":
        current[3] = "weather" if widget_id == "weather" else "cast_info"
    elif zone == "zone5":
        current[4] = "cast_info" if widget_id == "info" else "status_bar"
    else:
        return False
    state.preferences_zone_widgets = (
        current[0],
        current[1],
        current[2],
        current[3],
        current[4],
    )
    write_now_playing_zone_widgets(
        state.preferences_zone_widgets, persist=persist
    )
    return True


def persist_widgets_layout(state: MainSettingsState) -> None:
    from pigeon.widgets.preferences_settings import write_now_playing_zone_widgets

    write_now_playing_zone_widgets(
        _zone_tuple(state), persist=True
    )


def _paint_text(el: ET.Element | None, fill: str) -> None:
    if el is None:
        return
    nodes = [el] if el.tag.endswith("text") else [
        n for n in el.iter() if n.tag.endswith("text") or n.tag.endswith("tspan")
    ]
    if not nodes:
        nodes = [el]
    for node in nodes:
        _set_paint(node, fill=fill, stroke="none")


_TRANSLATE_RE = re.compile(
    r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)",
    re.IGNORECASE,
)


def _nudge_translate(el: ET.Element | None, dx: float, dy: float = 0.0) -> None:
    """Shift a text node's translate() so Pillow (which ignores parent xforms) moves."""
    if el is None:
        return
    raw = el.get("transform") or ""
    match = _TRANSLATE_RE.search(raw)
    if match is None:
        el.set("transform", f"translate({dx:.2f} {dy:.2f})")
        return
    x = float(match.group(1)) + dx
    y = float(match.group(2) or 0.0) + dy
    el.set("transform", _TRANSLATE_RE.sub(f"translate({x:.2f} {y:.2f})", raw, count=1))


def _shape_rect(group: ET.Element | None) -> ET.Element | None:
    if group is None:
        return None
    if group.tag.endswith("rect"):
        return group
    for node in group.iter():
        if node.tag.endswith("rect"):
            return node
    return None


def _svg_to_canvas_rect(
    x: float, y: float, w: float, h: float
) -> tuple[int, int, int, int]:
    vx, vy, vw, vh = _WIDGETS_VIEWBOX
    cx = (float(x) - vx) * DESIGN_W / vw
    cy = (float(y) - vy) * DESIGN_H / vh
    cw = float(w) * DESIGN_W / vw
    ch = float(h) * DESIGN_H / vh
    return (
        int(round(cx)),
        int(round(cy)),
        max(1, int(round(cw))),
        max(1, int(round(ch))),
    )


def zone_canvas_rect(zone_id: str) -> tuple[int, int, int, int]:
    svg = _ZONE_SHAPE_SVG.get(zone_id)
    if svg is None:
        return (0, 0, 1, 1)
    return _svg_to_canvas_rect(*svg)


def apply_widgets_svg_state(
    root: ET.Element, state: MainSettingsState, *, preview: bool = False
) -> None:
    nav = str(getattr(state, "widgets_nav", "zones") or "zones")
    focused = "" if preview else str(getattr(state, "widgets_focused_id", "") or "")
    active_zone = "" if preview else str(getattr(state, "widgets_active_zone", "") or "")
    assigned = assigned_widget_ids(state)
    catalog = ZONE_WIDGET_LISTS.get(active_zone, ()) if nav == "widgets" else ()

    for wid, lid in _WIDGET_LABEL_IDS.items():
        el = _find_by_logical_id(root, lid)
        _nudge_translate(el, _LABELS_SHIFT_X)
        if preview:
            color = _COLOR_WHITE if wid in assigned else _COLOR_IDLE
        elif nav == "widgets":
            if wid not in catalog:
                color = _COLOR_UNAVAILABLE
            elif wid == focused:
                color = _COLOR_WHITE
            elif wid == widget_id_for_zone(state, active_zone):
                color = _COLOR_WHITE
            else:
                color = _COLOR_IDLE
        else:
            color = _COLOR_WHITE if wid in assigned else _COLOR_IDLE
        _paint_text(el, color)

    highlight_zone = ""
    if not preview:
        if nav == "widgets" and active_zone:
            highlight_zone = active_zone
        elif focused in _ZONE_SHAPE_IDS:
            highlight_zone = focused
    for zone, gid in _ZONE_SHAPE_IDS.items():
        rect = _shape_rect(_find_by_logical_id(root, gid))
        if rect is None:
            continue
        on = zone == highlight_zone
        _set_paint(
            rect,
            fill="none",
            stroke=_COLOR_WHITE if on else _COLOR_ZONE_IDLE,
        )
        rect.set("stroke-width", "7" if on else "2")
        rect.set("stroke-miterlimit", "10")

    # Zone names are redrawn in Pillow so they follow the current widget.
    _set_visible(_find_by_logical_id(root, "zone_labels_group"), False)
    if preview:
        _set_visible(_find_by_logical_id(root, "zone_shapes_group"), False)


def _inner_rect(xywh: tuple[int, int, int, int], inset: float = _ZONE_INSET) -> tuple[int, int, int, int]:
    x, y, w, h = xywh
    pad = max(2, int(round(inset)))
    return (x + pad, y + pad, max(1, w - 2 * pad), max(1, h - 2 * pad))


def _weather_label() -> str:
    try:
        from pigeon.weather import cached_weather_temps

        temps = cached_weather_temps()
    except Exception:
        temps = None
    if temps is None:
        return "72 / 58"
    return f"{int(temps.high_f)} / {int(temps.low_f)}"


def _draw_artwork_demo(out: np.ndarray, box: tuple[int, int, int, int]) -> None:
    from pigeon.widgets.view_circles import (
        _load_sharp_semibold,
        _paste_centered,
        _text_patch_font,
        _tt_countdown_time_patch,
    )

    x, y, w, h = box
    cx = x + w * 0.5
    cy = y + h * 0.5
    title_px = max(14, int(round(min(w, h) * 0.14)))
    time_px = max(16, int(round(min(w, h) * 0.18)))
    title, tw, th = _text_patch_font(
        _DEMO_TITLE,
        font=_load_sharp_semibold(title_px),
        fill_rgb=(255, 255, 255),
    )
    time_p = _tt_countdown_time_patch(_DEMO_REMAINING, size_px=time_px)
    gap = max(6, int(round(h * 0.06)))
    stack = th + gap + (int(time_p.shape[0]) if time_p is not None else 0)
    top = cy - stack * 0.5
    _paste_centered(out, title, cx, top + th * 0.5)
    if time_p is not None and time_p.size:
        _paste_centered(out, time_p, cx, top + th + gap + time_p.shape[0] * 0.5)


def _draw_disc_widget(
    out: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    kind: str,
    assets_dir: Path | str | None,
) -> None:
    from pigeon.np_layout import (
        CLOCK_LOCAL_CX,
        CLOCK_LOCAL_CY,
        CLOCK_VIEW_H,
        CLOCK_VIEW_W,
        VOLUME_INNER_R,
        VOLUME_LOCAL_CX,
        VOLUME_LOCAL_CY,
        VOLUME_OUTER_R,
        VOLUME_VIEW_H,
        VOLUME_VIEW_W,
    )
    from pigeon.widgets.view_circles import (
        _clock_hhmm,
        _draw_progress_ring,
        _paste_centered,
        _paste_patch_bgra,
        _rasterize_named_widget,
        _volume_readout_patch,
        np_theme_from_settings,
    )

    x, y, w, h = box
    if kind == "volume":
        vw, vh = VOLUME_VIEW_W, VOLUME_VIEW_H
        lcx, lcy = VOLUME_LOCAL_CX, VOLUME_LOCAL_CY
    else:
        vw, vh = CLOCK_VIEW_W, CLOCK_VIEW_H
        lcx, lcy = CLOCK_LOCAL_CX, CLOCK_LOCAL_CY
    scale = min(w / vw, h / vh)
    dw = max(8, int(round(vw * scale)))
    dh = max(8, int(round(vh * scale)))
    ox = x + (w - dw) // 2
    oy = y + (h - dh) // 2
    now = datetime.now()
    th = np_theme_from_settings()
    try:
        patch = _rasterize_named_widget(
            assets_dir=assets_dir,
            widget_key=kind,
            dest_w=dw,
            dest_h=dh,
            now=now,
            theme=th,
            zone=3 if kind == "volume" else 1,
        )
    except Exception:
        patch = None
    if patch is not None and patch.size:
        _paste_patch_bgra(out, patch, ox, oy)
    cx = ox + lcx * scale
    cy = oy + lcy * scale
    if kind == "volume":
        outer = VOLUME_OUTER_R * scale
        inner = VOLUME_INNER_R * scale
        _draw_progress_ring(
            out,
            cx=cx,
            cy=cy,
            outer_r=outer,
            inner_r=inner,
            fraction=_DEMO_VOLUME_FRAC,
            fill_bgr=th.ui_bgr,
            fill_opacity=1.0,
            stroke=0,
        )
        vol_p, _, _ = _volume_readout_patch(
            _DEMO_VOLUME,
            inner_r=inner,
            max_size_px=max(12, int(round(inner * 0.9))),
        )
        _paste_centered(out, vol_p, cx, cy)
        return
    # Clock: digital readout in the disc if the analog SVG did not include it.
    from pigeon.widgets.view_circles import _text_patch_digital7

    time_p, _, _ = _text_patch_digital7(
        _clock_hhmm(now),
        size_px=max(12, int(round(min(dw, dh) * 0.16))),
    )
    _paste_centered(out, time_p, cx, cy)


def _draw_info_demo(out: np.ndarray, box: tuple[int, int, int, int]) -> None:
    from pigeon.widgets.view_circles import (
        _paste_centered,
        _text_patch_digital7,
    )

    x, y, w, h = box
    cols = 3
    col_w = w / float(cols)
    actor_px = max(11, int(round(h * 0.44)))
    max_w = max(40, int(col_w - 10))
    for i, (actor, _character) in enumerate(_DEMO_CAST[:cols]):
        cx = x + col_w * (i + 0.5)
        ap, _, _ = _text_patch_digital7(
            actor.upper(),
            size_px=actor_px,
            max_width_px=max_w,
            fill_rgb=(255, 255, 255),
        )
        _paste_centered(out, ap, cx, y + h * 0.5)


def _draw_status_demo(out: np.ndarray, box: tuple[int, int, int, int]) -> None:
    from pigeon.widgets.view_circles import (
        _draw_rounded_bar_bgra,
        _paste_centered,
        _text_patch_digital7,
        np_theme_from_settings,
        _COLOR_CHROME_BGR,
    )

    x, y, w, h = box
    th = np_theme_from_settings()
    bar_h = max(8, int(round(h * 0.28)))
    bar_y = y + int(round((h - bar_h) * 0.35))
    radius = max(2, bar_h // 2)
    _draw_rounded_bar_bgra(
        out,
        x=x,
        y=bar_y,
        w=w,
        h=bar_h,
        fill_bgr=_COLOR_CHROME_BGR,
        radius=radius,
        fill_opacity=0.35,
        stroke=0,
    )
    filled = max(4, int(round(w * _DEMO_PROGRESS)))
    _draw_rounded_bar_bgra(
        out,
        x=x,
        y=bar_y,
        w=filled,
        h=bar_h,
        fill_bgr=th.ui_bgr,
        radius=radius,
        fill_opacity=1.0,
        stroke=0,
    )
    tpx = max(10, int(round(h * 0.36)))
    left, _, _ = _text_patch_digital7(
        _DEMO_ELAPSED, size_px=tpx, fill_rgb=(255, 255, 255)
    )
    right, rw, _ = _text_patch_digital7(
        _DEMO_REMAINING, size_px=tpx, fill_rgb=(255, 255, 255)
    )
    ty = y + h * 0.82
    _paste_centered(out, left, x + left.shape[1] * 0.5 + 2, ty)
    _paste_centered(out, right, x + w - rw * 0.5 - 2, ty)


def _draw_weather_demo(out: np.ndarray, box: tuple[int, int, int, int]) -> None:
    from pigeon.widgets.view_circles import (
        _load_sharp_extrabold,
        _paste_centered,
        _text_patch_font,
    )

    x, y, w, h = box
    label = _weather_label()
    px = max(14, int(round(min(w * 0.18, h * 0.55))))
    patch, _tw, _th = _text_patch_font(
        label,
        font=_load_sharp_extrabold(px),
        fill_rgb=(255, 255, 255),
    )
    _paste_centered(out, patch, x + w * 0.5, y + h * 0.5)


def _draw_zone_label(
    out: np.ndarray, zone_id: str, widget_id: str, box: tuple[int, int, int, int]
) -> None:
    from pigeon.widgets.view_circles import (
        _load_sharp_extrabold,
        _paste_centered,
        _text_patch_font,
        _paste_patch_bgra,
    )

    label = str(widget_id or _DEFAULT_WIDGET_BY_ZONE.get(zone_id, "")).upper()
    if not label:
        return
    x, y, w, h = box
    px = 28 if zone_id in ("zone4", "zone5") else 32
    patch, pw, ph = _text_patch_font(
        label,
        font=_load_sharp_extrabold(px),
        fill_rgb=(255, 255, 255),
    )
    if zone_id in ("zone6", "zone3"):
        _paste_centered(out, patch, x + pw * 0.5, y - ph * 0.5 - 8)
        return
    # Strips: sit to the left of the zone, right-aligned into the gutter.
    lx = max(8, x - 12 - pw)
    ly = y + (h - ph) // 2
    _paste_patch_bgra(out, patch, int(lx), int(ly))


def _draw_zone_demos_bgra(
    out: np.ndarray,
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None,
) -> None:
    for zone in ZONE_FOCUS_IDS:
        box = _inner_rect(zone_canvas_rect(zone))
        wid = widget_id_for_zone(state, zone)
        _draw_zone_label(out, zone, wid, zone_canvas_rect(zone))
        if wid == "artwork":
            _draw_artwork_demo(out, box)
        elif wid in ("clock", "volume"):
            _draw_disc_widget(out, box, kind=wid, assets_dir=assets_dir)
        elif wid == "info":
            _draw_info_demo(out, box)
        elif wid == "status":
            _draw_status_demo(out, box)
        elif wid == "weather":
            _draw_weather_demo(out, box)


def render_widgets_page_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None = None,
    preview: bool = False,
) -> np.ndarray:
    from pigeon.settings_layout import settings_widget_path
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    path = settings_widget_path("widgets_page", assets_dir=assets_dir)
    root = copy.deepcopy(ET.parse(path).getroot())
    vx, vy, vw, vh = _WIDGETS_VIEWBOX
    root.set("viewBox", f"{vx} {vy} {vw} {vh}")
    root.set("width", str(DESIGN_W))
    root.set("height", str(DESIGN_H))
    apply_widgets_svg_state(root, state, preview=preview)
    _prune_display_none(root)
    frame = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )
    if not preview:
        _draw_zone_demos_bgra(frame, state, assets_dir=assets_dir)
    return frame


__all__ = [
    "WIDGET_FOCUS_IDS",
    "ZONE_FOCUS_IDS",
    "ZONE_WIDGET_LISTS",
    "apply_widget_assignment",
    "apply_widgets_svg_state",
    "assigned_widget_ids",
    "ensure_default_zone_widgets",
    "persist_widgets_layout",
    "render_widgets_page_bgra",
    "widget_id_for_zone",
    "widgets_focus_ring",
    "zone_canvas_rect",
    "zone_catalog",
]
