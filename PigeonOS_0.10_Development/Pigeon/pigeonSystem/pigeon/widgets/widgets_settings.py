"""Now-play widgets page — ``settings_pigeon_widgets.svg``.

Opened from settings_pigeon NOW PLAY. Header labels sit above the menu plate;
zone slots sit on the plate. Preview (tile focused) shows only the labels.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
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
_COLOR_ZONE_IDLE = "#202020"
_COLOR_ZONE_FILL = "#1C1C1C"
# Header labels start at board x=747.84 → canvas ~166, which collides with BACK.
_LABELS_SHIFT_X = 56.0

# Same board crop as settings_pigeon so labels/zones land on the 1280 canvas.
_WIDGETS_VIEWBOX = (
    SETTINGS_CANVAS_ORIGIN[0],
    SETTINGS_CANVAS_ORIGIN[1] - SETTINGS_BACKGROUND_SHIFT_Y,
    float(DESIGN_W),
    float(DESIGN_H),
)

# Left → right in the export, then BACK.
WIDGET_FOCUS_IDS: tuple[str, ...] = (
    "artwork",
    "volume",
    "status",
    "info",
    "clock",
    "weather",
)

_WIDGET_LABEL_IDS: dict[str, str] = {
    "artwork": "widget_labels_artwork_text",
    "volume": "widget_labels_volume_text",
    "status": "widget_labels_status_text",
    "info": "widget_labels_info_text",
    "clock": "widget_labels_clock_text",
    "weather": "widget_labels_weather_text",
}

# Home zone shape for each header widget (weather has no slot yet).
_WIDGET_ZONE: dict[str, str] = {
    "artwork": "zone6",
    "clock": "zone6",
    "volume": "zone3",
    "info": "zone4",
    "status": "zone5",
}

_ZONE_SHAPE_IDS: dict[str, str] = {
    "zone6": "zone6_shape",
    "zone3": "zone3_shape",
    "zone4": "zone4_shape",
    "zone5": "zone5_shape",
}

# Persist mapping: header widget → now-playing zone slots (1–5).
_WIDGET_ZONE_ASSIGN: dict[str, dict[int, str]] = {
    "artwork": {1: "tt_countdown_16x9", 2: ""},
    "clock": {1: "clock", 2: ""},
    "volume": {3: "volume"},
    "info": {4: "cast_info"},
    "status": {5: "status_bar"},
}


def widgets_focus_ring() -> tuple[str, ...]:
    return WIDGET_FOCUS_IDS + ("pigeon_back",)


def assigned_widget_ids(state: MainSettingsState) -> frozenset[str]:
    """Header widgets that match the current persisted now-playing layout."""
    zones = tuple(getattr(state, "preferences_zone_widgets", ()) or ())
    if not bool(getattr(state, "show_widgets", False)):
        try:
            from pigeon.widgets.preferences_settings import read_now_playing_zone_widgets

            zones = tuple(read_now_playing_zone_widgets())
        except Exception:
            pass
    assigned: set[str] = set()
    z1 = str(zones[0] if len(zones) > 0 else "")
    if z1 in ("tt_countdown_16x9", "tt_countdown", "poster"):
        assigned.add("artwork")
    elif z1 == "clock":
        assigned.add("clock")
    if len(zones) > 2 and zones[2] == "volume":
        assigned.add("volume")
    if len(zones) > 3 and zones[3] in ("cast_info",):
        assigned.add("info")
    if len(zones) > 4 and zones[4] in ("status_bar", "now_playing"):
        assigned.add("status")
    return frozenset(assigned)


def apply_widget_assignment(state: MainSettingsState, widget_id: str) -> bool:
    """Write the focused widget into its home zone. Returns False if unmapped."""
    patch = _WIDGET_ZONE_ASSIGN.get(widget_id)
    if not patch:
        return False
    from pigeon.widgets.preferences_settings import (
        DEFAULT_ZONE_WIDGETS,
        write_now_playing_zone_widgets,
    )

    current = list(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    while len(current) < 5:
        current.append("")
    for zone, key in patch.items():
        current[zone - 1] = key
    state.preferences_zone_widgets = (
        current[0],
        current[1],
        current[2],
        current[3],
        current[4],
    )
    write_now_playing_zone_widgets(state.preferences_zone_widgets)
    return True


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


def apply_widgets_svg_state(
    root: ET.Element, state: MainSettingsState, *, preview: bool = False
) -> None:
    focused = "" if preview else str(getattr(state, "widgets_focused_id", "") or "")
    assigned = assigned_widget_ids(state)
    for wid, lid in _WIDGET_LABEL_IDS.items():
        el = _find_by_logical_id(root, lid)
        _nudge_translate(el, _LABELS_SHIFT_X)
        if preview:
            color = _COLOR_WHITE if wid in assigned else _COLOR_IDLE
        elif wid == focused:
            color = _COLOR_WHITE
        else:
            color = _COLOR_IDLE
        _paint_text(el, color)

    highlight_zone = "" if preview else _WIDGET_ZONE.get(focused, "")
    for zone, gid in _ZONE_SHAPE_IDS.items():
        rect = _shape_rect(_find_by_logical_id(root, gid))
        if rect is None:
            continue
        on = zone == highlight_zone
        _set_paint(
            rect,
            fill=_COLOR_ZONE_FILL,
            stroke=_COLOR_WHITE if on else _COLOR_ZONE_IDLE,
        )
        rect.set("stroke-width", "7" if on else "2")
        rect.set("stroke-miterlimit", "10")

    if preview:
        _set_visible(_find_by_logical_id(root, "zone_shapes_group"), False)
        _set_visible(_find_by_logical_id(root, "zone_labels_group"), False)


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
    return rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )


__all__ = [
    "WIDGET_FOCUS_IDS",
    "apply_widget_assignment",
    "apply_widgets_svg_state",
    "assigned_widget_ids",
    "render_widgets_page_bgra",
    "widgets_focus_ring",
]
