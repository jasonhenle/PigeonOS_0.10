"""System-wide options bar — ``widget_sp_options.svg``.

Opened from settings_pigeon OPTIONS. Rasterizes the authored SVG 1:1 above
the menu plate, shifted right of BACK. Toggle knobs and selector visibility
are the only live mutations.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.settings_layout import (
    MENU_PLATE_XYWH,
    SETTINGS_CANVAS_ORIGIN,
    SETTINGS_MAIN_ZONES,
)
from pigeon.widgets.main_settings import (
    MainSettingsState,
    _find_by_logical_id,
    _prune_display_none,
    _set_paint,
    _set_visible,
)

_COLOR_WHITE = "#FFFFFF"
_COLOR_BLACK = "#000000"
_COLOR_UI = "#4EA6F7"
_COLOR_OPTION_IDLE = "#000000"
_COLOR_SWATCH_STROKE = "#000000"


def _paint_option_label(el: ET.Element | None, fill: str) -> None:
    """Paint a label group or text node (multi-line labels are groups of ``<text>``)."""
    if el is None:
        return
    if el.tag.endswith("text"):
        _set_paint(el, fill=fill, stroke="none")
        return
    for node in el.iter():
        if node.tag.endswith("text"):
            _set_paint(node, fill=fill, stroke="none")

# Fallback crop if the SVG has no measurable shapes.
_OPTIONS_BAR_BOARD = (682.7, 1017.01, 1077.38, 161.98)
_OPTIONS_BAR_GAP = 8.0
_OPTIONS_BACK_GAP = 12.0

# option A is the default (left knob, left label white).
_DEFAULTS: dict[str, Any] = {
    "time_format": "12",
    "clock_format": "digital",
    "temp_format": "f",
    "color_format": "color",
    "idle_standby": 60,
    "clock_saver": "on",
}

# (switch_index, persist key, option_a value, option_b value)
_SWITCHES: tuple[tuple[int, str, Any, Any], ...] = (
    (1, "time_format", "12", "24"),
    (2, "clock_format", "digital", "analog"),
    (3, "temp_format", "f", "c"),
    (4, "color_format", "color", "dark"),
    (5, "idle_standby", 60, 120),
    (6, "clock_saver", "on", "off"),
)

_STATE_KEY = "settings_options"


def options_focus_ring() -> tuple[str, ...]:
    return tuple(f"option_{n}" for n, _k, _a, _b in _SWITCHES) + ("pigeon_back",)


def _normalize(raw: object) -> dict[str, Any]:
    out = dict(_DEFAULTS)
    if not isinstance(raw, dict):
        return out
    tf = str(raw.get("time_format") or out["time_format"]).strip().lower()
    out["time_format"] = "24" if tf in ("24", "24h", "24-hour") else "12"
    cf = str(raw.get("clock_format") or out["clock_format"]).strip().lower()
    out["clock_format"] = "analog" if cf == "analog" else "digital"
    tmp = str(raw.get("temp_format") or out["temp_format"]).strip().lower()
    out["temp_format"] = "c" if tmp in ("c", "celsius", "celcius") else "f"
    col = str(raw.get("color_format") or out["color_format"]).strip().lower()
    out["color_format"] = (
        "dark"
        if col in ("dark", "bw", "b/w", "mono", "gray", "grey", "redmono", "red-mono")
        else "color"
    )
    try:
        idle = int(raw.get("idle_standby", out["idle_standby"]))
    except (TypeError, ValueError):
        idle = 60
    out["idle_standby"] = 120 if idle >= 90 else 60
    sv = str(raw.get("clock_saver") or out["clock_saver"]).strip().lower()
    out["clock_saver"] = "off" if sv in ("off", "0", "false", "no") else "on"
    return out


def read_options() -> dict[str, Any]:
    try:
        from pigeon.app_state import read_app_state

        return _normalize(read_app_state().get(_STATE_KEY))
    except Exception:
        return dict(_DEFAULTS)


def write_options(values: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
    out = _normalize(values)
    if persist:
        try:
            from pigeon.app_state import write_app_state

            write_app_state(**{_STATE_KEY: out})
        except Exception:
            pass
    return out


def options_snapshot(state: MainSettingsState | None = None) -> tuple[object, ...]:
    if state is not None:
        raw = getattr(state, "options_values", None)
        vals = _normalize(raw) if isinstance(raw, dict) else read_options()
    else:
        vals = read_options()
    return (
        vals["time_format"],
        vals["clock_format"],
        vals["temp_format"],
        vals["color_format"],
        int(vals["idle_standby"]),
        vals["clock_saver"],
    )


def toggle_option(
    index: int, values: dict[str, Any] | None = None, *, persist: bool = True
) -> dict[str, Any]:
    vals = _normalize(values if values is not None else read_options())
    for n, key, a_val, b_val in _SWITCHES:
        if n == int(index):
            vals[key] = a_val if vals.get(key) == b_val else b_val
            break
    return write_options(vals, persist=persist)


def load_options_into_state(state: MainSettingsState) -> None:
    state.options_values = read_options()


def _vals(state: MainSettingsState | None = None) -> dict[str, Any]:
    if state is not None:
        raw = getattr(state, "options_values", None)
        if isinstance(raw, dict) and raw:
            return _normalize(raw)
    return read_options()


def clock_uses_24h(state: MainSettingsState | None = None) -> bool:
    return _vals(state)["time_format"] == "24"


def clock_widget_analog(state: MainSettingsState | None = None) -> bool:
    """True when options clock format is analog (NP clock + idle saver face)."""
    return _vals(state)["clock_format"] == "analog"


def clock_saver_analog(state: MainSettingsState | None = None) -> bool:
    """True when idle zone-9 saver should show the centered analog clock widget."""
    return clock_widget_analog(state)


def temp_uses_celsius(state: MainSettingsState | None = None) -> bool:
    return _vals(state)["temp_format"] == "c"


def ui_is_monochrome(state: MainSettingsState | None = None) -> bool:
    try:
        from pigeon.widgets.ui_color_settings import current_ui_picker_key

        if current_ui_picker_key() == "dark":
            return True
        if current_ui_picker_key() == "bright":
            return False
    except Exception:
        pass
    return _vals(state)["color_format"] == "dark"


def ui_is_bright(state: MainSettingsState | None = None) -> bool:
    _ = state
    try:
        from pigeon.widgets.ui_color_settings import current_ui_picker_key

        return current_ui_picker_key() == "bright"
    except Exception:
        return False


def ui_paper_bgr() -> tuple[int, int, int]:
    """Frame / plate paper (BGR). Light mode is white; dark mode is black."""
    return (255, 255, 255) if ui_is_bright() else (0, 0, 0)


def ui_ink_rgb() -> tuple[int, int, int]:
    """Primary text on paper."""
    return (0, 0, 0) if ui_is_bright() else (255, 255, 255)


def ui_ink_hex() -> str:
    return "#000000" if ui_is_bright() else "#FFFFFF"


def ui_chrome_rgb() -> tuple[int, int, int]:
    """NP labels / Digital-7 / unplayed track. Gray in dark, black in light."""
    return (0, 0, 0) if ui_is_bright() else (147, 147, 147)


def ui_chrome_bgr() -> tuple[int, int, int]:
    return ui_chrome_rgb()


def ui_chrome_hex() -> str:
    return "#000000" if ui_is_bright() else "#939393"


def ui_idle_text_hex() -> str:
    """Deselected settings labels sitting on the plate."""
    return "#000000" if ui_is_bright() else "#919190"


def clock_saver_idle_s(state: MainSettingsState | None = None) -> float:
    return float(_vals(state)["idle_standby"])


def clock_saver_enabled(state: MainSettingsState | None = None) -> bool:
    return _vals(state)["clock_saver"] == "on"


def _switch_is_b(vals: dict[str, Any], key: str, b_value: Any) -> bool:
    return vals.get(key) == b_value


def _attr_float(el: ET.Element, name: str, default: float = 0.0) -> float:
    try:
        return float(el.get(name) or default)
    except ValueError:
        return default


def options_bar_board(root: ET.Element) -> tuple[float, float, float, float]:
    """Union of authored shape bounds, including selector stroke overflow."""
    min_x = min_y = 1e9
    max_x = max_y = -1e9
    found = False
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        pad = _attr_float(el, "stroke-width") * 0.5
        if tag == "rect":
            x = _attr_float(el, "x")
            y = _attr_float(el, "y")
            w = _attr_float(el, "width")
            h = _attr_float(el, "height")
        elif tag == "ellipse":
            cx = _attr_float(el, "cx")
            cy = _attr_float(el, "cy")
            rx = _attr_float(el, "rx")
            ry = _attr_float(el, "ry")
            x, y, w, h = cx - rx, cy - ry, rx * 2.0, ry * 2.0
        elif tag == "circle":
            cx = _attr_float(el, "cx")
            cy = _attr_float(el, "cy")
            r = _attr_float(el, "r")
            x, y, w, h = cx - r, cy - r, r * 2.0, r * 2.0
        else:
            continue
        found = True
        min_x = min(min_x, x - pad)
        min_y = min(min_y, y - pad)
        max_x = max(max_x, x + w + pad)
        max_y = max(max_y, y + h + pad)
    if not found or max_x <= min_x or max_y <= min_y:
        return _OPTIONS_BAR_BOARD
    return (min_x, min_y, max_x - min_x, max_y - min_y)


def options_bar_xy(board: tuple[float, float, float, float]) -> tuple[float, float]:
    """Place the authored board 1:1, shifted right of BACK."""
    vx, _vy, _vw, vh = board
    native_x = vx - SETTINGS_CANVAS_ORIGIN[0]
    back = SETTINGS_MAIN_ZONES[0]
    min_x = float(back.x) + float(back.w) + _OPTIONS_BACK_GAP
    x0 = max(native_x, min_x)
    y0 = max(0.0, MENU_PLATE_XYWH[1] - vh - _OPTIONS_BAR_GAP)
    return x0, y0


def apply_options_svg_state(
    root: ET.Element, state: MainSettingsState, *, preview: bool = False
) -> None:
    """Apply live toggle state without rewriting SVG geometry."""
    vals = _vals(state)
    ui_hex = str(getattr(getattr(state, "theme", None), "ui", "") or _COLOR_UI)
    focused = "" if preview else str(getattr(state, "options_focused_id", "") or "")
    for n, key, a_val, b_val in _SWITCHES:
        is_b = _switch_is_b(vals, key, b_val)
        selected = focused == f"option_{n}"
        selector = _find_by_logical_id(root, f"toggle_selector_{n}")
        _set_visible(selector, selected)
        if selector is not None and selected:
            _set_paint(selector, fill=_COLOR_WHITE, stroke=_COLOR_WHITE)
        swatch = _find_by_logical_id(root, f"red_swatch_button{n}")
        if swatch is not None:
            _set_paint(swatch, fill=ui_hex, stroke=_COLOR_SWATCH_STROKE)
        # Description chips removed in widget_sp_options2 art.
        opt_a = _find_by_logical_id(root, f"option_a_{n}")
        opt_b = _find_by_logical_id(root, f"option_b_{n}")
        _paint_option_label(opt_a, _COLOR_WHITE if not is_b else _COLOR_OPTION_IDLE)
        _paint_option_label(opt_b, _COLOR_WHITE if is_b else _COLOR_OPTION_IDLE)
        _set_visible(_find_by_logical_id(root, f"toggle_switch{n}_a"), not is_b)
        _set_visible(_find_by_logical_id(root, f"toggle_switch{n}_b"), is_b)


def render_options_bar_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None = None,
    preview: bool = False,
) -> np.ndarray:
    from pigeon.settings_layout import settings_widget_path
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    path = settings_widget_path("options_bar", assets_dir=assets_dir)
    root = copy.deepcopy(ET.parse(path).getroot())
    vx, vy, vw, vh = options_bar_board(root)
    pw = max(1, int(round(vw)))
    ph = max(1, int(round(vh)))
    root.set("viewBox", f"{vx} {vy} {vw} {vh}")
    root.set("width", str(pw))
    root.set("height", str(ph))
    apply_options_svg_state(root, state, preview=preview)
    _prune_display_none(root)
    patch = rasterize_settings_svg_bgra(
        root,
        width=pw,
        height=ph,
        font_mode="preferences",
    )
    canvas = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    x0, y0 = options_bar_xy((vx, vy, vw, vh))
    x0 = int(round(x0))
    y0 = int(round(y0))
    x1 = min(DESIGN_W, x0 + patch.shape[1])
    y1 = min(DESIGN_H, y0 + patch.shape[0])
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    x0 = max(0, x0)
    y0 = max(0, y0)
    if x1 > x0 and y1 > y0:
        canvas[y0:y1, x0:x1] = patch[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    return canvas


def apply_ui_mono_bgr(frame_bgr: np.ndarray) -> np.ndarray:
    """Map the frame to red-channel monochrome when the dark UI option is on."""
    if frame_bgr is None or frame_bgr.size == 0:
        return frame_bgr
    try:
        if not ui_is_monochrome():
            return frame_bgr
    except Exception:
        return frame_bgr
    from pigeon.compositing import bgr_to_red_monochrome_luma

    return bgr_to_red_monochrome_luma(frame_bgr)


def apply_ui_bright_bgr(frame_bgr: np.ndarray) -> np.ndarray:
    """Light mode is painted on the vector layers; no full-frame invert."""
    return frame_bgr


def apply_ui_look_bgr(frame_bgr: np.ndarray) -> np.ndarray:
    """Apply the picker look: red-mono dark. Light mode is assigned at paint time."""
    return apply_ui_mono_bgr(frame_bgr)


__all__ = [
    "apply_options_svg_state",
    "apply_ui_bright_bgr",
    "apply_ui_look_bgr",
    "apply_ui_mono_bgr",
    "clock_saver_analog",
    "clock_saver_enabled",
    "clock_saver_idle_s",
    "clock_uses_24h",
    "clock_widget_analog",
    "load_options_into_state",
    "options_bar_board",
    "options_bar_xy",
    "options_focus_ring",
    "options_snapshot",
    "read_options",
    "render_options_bar_bgra",
    "temp_uses_celsius",
    "toggle_option",
    "ui_chrome_bgr",
    "ui_chrome_hex",
    "ui_chrome_rgb",
    "ui_idle_text_hex",
    "ui_ink_hex",
    "ui_ink_rgb",
    "ui_is_bright",
    "ui_is_monochrome",
    "ui_paper_bgr",
    "write_options",
]
