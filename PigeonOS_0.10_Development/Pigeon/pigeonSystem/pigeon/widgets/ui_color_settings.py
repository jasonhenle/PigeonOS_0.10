"""
System color customizer — ``settings_0.8/settings_pigeon_ui_color.svg``.

Opened from preferences when **color** is activated. Two-phase navigation
mirrors preferences (classes → swatches within a class). BACK returns to
preferences with focus on the color control.
"""

from __future__ import annotations

import copy
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.widgets.main_settings import (
    COLOR_ACCENT_DEFAULT,
    COLOR_DESELECTED,
    COLOR_SELECTED,
    COLOR_UI_DEFAULT,
    MainSettingsState,
    SettingsTheme,
    _composite_bgra_over_bgra,
    _disable_embedded_settings_background_layers,
    _draw_container_background_bgra,
    _find_by_logical_id,
    _prune_display_none,
    _set_paint,
    _set_text_content,
    _set_visible,
)

SVG_NS = "http://www.w3.org/2000/svg"

# Match preferences / pigeon artboard crop on the Illustrator board.
_UI_COLOR_VIEWBOX = (339.0, 440.0, 800.0, 480.0)

_COLOR_WHITE = "#FFFFFF"
_COLOR_TEXT_IDLE = "#919190"
_COLOR_BACK_FILL = "#202020"
_COLOR_BACK_STROKE = "#000000"

# Match preferences BACK / settings_main EXIT geometry (viewBox +339,+440).
_BACK_BUTTON_D = (
    "M426.443,535H376.367c-5.278,0-9.557-4.279-9.557-9.557V507.628"
    "c0-5.831,4.727-10.557,10.557-10.557h49.075c5.278,0,9.557,4.279,9.557,9.557"
    "v18.814C436,530.721,431.721,535,426.443,535z"
)
_BACK_TEXT_X = 401.405
_BACK_TEXT_Y = 525.7403

# Class focus ring (phase A).
_CLASS_NAV_ORDER: tuple[str, ...] = ("accent", "ui", "button", "back")


@dataclass(frozen=True)
class _Swatch:
    key: str
    group_id: str
    swatch_id: str
    icon_id: str
    hex: str


# Left → right on the artboard. White / gray / black are last in each row.
_ACCENT_SWATCHES: tuple[_Swatch, ...] = (
    _Swatch("lightred", "lightred_swatch_group", "lightred_swatch", "lightred_swatch_icon", "#FF8383"),
    _Swatch("lightorange", "lightorange_group", "orange_swatch-2", "orange_swatch_icon-2", "#FFD985"),
    _Swatch("lightyellow", "lightyellow_swatch_group", "lightyellow_swatch", "lightyellow_swatch_icon", "#FFF87D"),
    _Swatch("lightgreen", "lightgreen_swatch_group", "lightgreen_swatch", "lightgreen_swatch_icon", "#ACFF7B"),
    _Swatch("lightblue", "lightblue_swatch_group", "lightblue_swatch", "lightblue_swatch_icon", "#7196FF"),
    _Swatch("lightpurple", "lightpurple_swatch_group", "lightpurple_swatch", "lightpurple_swatch_icon", "#C27EFC"),
    _Swatch("white", "white_swatch_group", "white_swatch", "white_swatch_icon", "#FFFFFF"),
)

_UI_SWATCHES: tuple[_Swatch, ...] = (
    # Keep brand red as the selectable "red" so existing UI-brand protection matches.
    _Swatch("red", "red_swatch_group", "red_swatch_button", "red_swatch_icon", COLOR_UI_DEFAULT),
    _Swatch("orange", "orange_Swatch_group", "orange_swatch", "orange_swatch_icon", "#FFB600"),
    _Swatch("yellow", "yellow_swatch_group", "yellow_swatch", "yellow_swatch_icon", "#FFF800"),
    _Swatch("green", "green_swatch_group", "green_swatch", "green_swatch_icon", "#58FF00"),
    _Swatch("blue", "blue_swatch_group", "blue_swatch", "blue_swatch_icon", "#0037FF"),
    _Swatch("purple", "purple_swatch_group", "purple_swatch", "purple_swatch_icon", "#9500FF"),
    _Swatch("gray", "gray_swatch_group", "gray_swatch", "gray_swatch_icon", "#777777"),
)

_BUTTON_SWATCHES: tuple[_Swatch, ...] = (
    _Swatch("darkred", "darkred_swatch_group", "darkred_swatch", "darkred_swatch_icon", "#7F0000"),
    _Swatch("darkorange", "darkorange_swatch_group", "darkorange_swatch", "darkorange_swatch_icon", "#825A00"),
    _Swatch("darkyellow", "darkyellow_swatch_group", "darkyellow_swatch", "darkyellow_swatch_icon", "#878000"),
    _Swatch("darkgreen", "darkgreen_swatch_group", "darkgreen_swatch", "darkgreen_swatch_icon", "#2F7F00"),
    _Swatch("darkblue", "darkblue_swatch_group", "darkblue_swatch", "darkblue_swatch_icon", "#03237F"),
    _Swatch("darkpurple", "darkpurple_swatch_group", "darkpurple_swatch", "darkpurple_swatch_icon", "#490089"),
    _Swatch("black", "black_swatch_group", "black_swatch", "black_swatch_icon", "#202020"),
)

_CLASS_SWATCHES: dict[str, tuple[_Swatch, ...]] = {
    "accent": _ACCENT_SWATCHES,
    "ui": _UI_SWATCHES,
    "button": _BUTTON_SWATCHES,
}

_DEFAULT_KEYS: dict[str, str] = {
    "accent": "white",
    "ui": "red",
    "button": "black",
}

_TEXT_IDS: dict[str, str] = {
    "accent": "accent_text",
    "ui": "ui_text",
    "button": "button_text",
}
# Class labels share one right edge (just left of the first swatch column @ 540.97).
_CLASS_LABEL_RIGHT_X = 528.0
_CLASS_LABEL_SIZE = "24"
_CLASS_LABEL_Y: dict[str, float] = {
    "accent": 650.47,
    "ui": 731.17,
    "button": 811.31,
}

# All swatch hexes — used by main_settings paint swaps so theme changes stick.
THEME_SWATCH_HEXES: frozenset[str] = frozenset(
    s.hex.lower()
    for row in (_ACCENT_SWATCHES, _UI_SWATCHES, _BUTTON_SWATCHES)
    for s in row
) | frozenset(
    {
        COLOR_ACCENT_DEFAULT.lower(),
        COLOR_UI_DEFAULT.lower(),
        COLOR_DESELECTED.lower(),
        COLOR_SELECTED.lower(),
        "#ffffff",
        "#fff",
        "#000013",
        "#000000",
        "#202020",
        "white",
        "black",
        "red",
    }
)

UI_SWATCH_HEXES: frozenset[str] = frozenset(
    s.hex.lower() for s in _UI_SWATCHES
) | frozenset({COLOR_UI_DEFAULT.lower(), "#ff0013", "red"})

_SVG_TREE_TEMPLATES: dict[tuple[str, int, int], ET.Element] = {}
_SVG_TREE_TEMPLATE_MAX = 4
_THEME_BG_CACHE: dict[tuple[str, str, int, int], np.ndarray] = {}
_THEME_BG_CACHE_MAX = 4


def default_ui_color_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_UI_COLOR_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "settings_pigeon_ui_color.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "settings_pigeon_ui_color.svg"


def ui_color_class_focus_ring() -> tuple[str, ...]:
    return _CLASS_NAV_ORDER


def ui_color_swatch_focus_ring(color_class: str) -> tuple[str, ...]:
    return tuple(s.key for s in _CLASS_SWATCHES.get(color_class, ()))


def _swatch_by_key(color_class: str, key: str) -> _Swatch | None:
    for s in _CLASS_SWATCHES.get(color_class, ()):
        if s.key == key:
            return s
    return None


def hex_for_color_key(color_class: str, key: str) -> str:
    sw = _swatch_by_key(color_class, key)
    if sw is not None:
        return sw.hex
    return {
        "accent": COLOR_ACCENT_DEFAULT,
        "ui": COLOR_UI_DEFAULT,
        "button": "#202020",
    }.get(color_class, COLOR_SELECTED)


def read_ui_color_keys() -> dict[str, str]:
    try:
        from pigeon.app_state import read_app_state

        raw = read_app_state().get("settings_ui_colors")
    except Exception:
        raw = None
    out = dict(_DEFAULT_KEYS)
    if isinstance(raw, dict):
        for cls, default in _DEFAULT_KEYS.items():
            key = str(raw.get(cls) or default)
            if _swatch_by_key(cls, key) is None:
                key = default
            out[cls] = key
    return out


def write_ui_color_keys(
    keys: dict[str, str],
    *,
    persist: bool = True,
) -> dict[str, str]:
    out = dict(_DEFAULT_KEYS)
    for cls, default in _DEFAULT_KEYS.items():
        key = str(keys.get(cls) or default)
        if _swatch_by_key(cls, key) is None:
            key = default
        out[cls] = key
    if persist:
        try:
            from pigeon.app_state import write_app_state

            write_app_state(settings_ui_colors=out)
        except Exception:
            pass
    return out


def theme_from_color_keys(keys: dict[str, str], *, base: SettingsTheme | None = None) -> SettingsTheme:
    b = base or SettingsTheme()
    return SettingsTheme(
        ui=hex_for_color_key("ui", keys.get("ui", "red")),
        selected=b.selected or COLOR_SELECTED,
        deselected=hex_for_color_key("button", keys.get("button", "black")),
        inactive=b.inactive,
        accent=hex_for_color_key("accent", keys.get("accent", "white")),
    )


def apply_color_keys_to_state(
    state: MainSettingsState,
    keys: dict[str, str],
    *,
    persist: bool = False,
) -> None:
    norm = write_ui_color_keys(keys, persist=persist)
    state.ui_color_accent_key = norm["accent"]
    state.ui_color_ui_key = norm["ui"]
    state.ui_color_button_key = norm["button"]
    state.theme = theme_from_color_keys(norm, base=state.theme)


def load_persisted_theme_into_state(state: MainSettingsState) -> None:
    """Apply saved accent/ui/button keys onto ``state.theme`` (startup / open)."""
    apply_color_keys_to_state(state, read_ui_color_keys(), persist=False)


def _svg_tree_from_path(path: Path) -> ET.Element:
    try:
        st = path.stat()
        key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(path), 0, 0)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        root = ET.parse(path).getroot()
        x, y, w, h = _UI_COLOR_VIEWBOX
        root.set("viewBox", f"{x} {y} {w} {h}")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        if len(_SVG_TREE_TEMPLATES) >= _SVG_TREE_TEMPLATE_MAX:
            _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _paint_text(el: ET.Element | None, color: str) -> None:
    if el is None:
        return
    for node in el.iter():
        if node.tag.endswith("text") or node.tag.endswith("tspan"):
            _set_paint(node, fill=color)


def _sync_back_button(root: ET.Element, *, selected: bool) -> None:
    group = _find_by_logical_id(root, "back_group")
    if group is None:
        return
    button = _find_by_logical_id(group, "back_button")
    accent = _find_by_logical_id(group, "back_accent")
    text = _find_by_logical_id(group, "back_text")
    # Force preferences EXIT geometry — color-page export sits too high/left.
    for path_el in (button, accent):
        if path_el is None:
            continue
        if path_el.tag.endswith("path"):
            path_el.set("d", _BACK_BUTTON_D)
        else:
            for child in path_el.iter():
                if child is path_el:
                    continue
                if child.tag.endswith("path"):
                    child.set("d", _BACK_BUTTON_D)
    if selected:
        if button is not None:
            _set_paint(button, fill=_COLOR_WHITE, stroke=_COLOR_BACK_STROKE)
        if accent is not None:
            _set_paint(accent, fill="none", stroke=_COLOR_WHITE)
        _paint_text(text, _COLOR_BACK_STROKE)
    else:
        if button is not None:
            _set_paint(button, fill=_COLOR_BACK_FILL, stroke=_COLOR_BACK_STROKE)
        if accent is not None:
            _set_paint(accent, fill="none", stroke=_COLOR_WHITE)
        _paint_text(text, _COLOR_WHITE)
    if text is not None:
        text.set("transform", f"translate({_BACK_TEXT_X:.4f} {_BACK_TEXT_Y:.4f})")
        text.set("text-anchor", "middle")
        text.set("font-family", "Digital-7, Digital-7")
        text.set("font-size", "29")
        _set_text_content(text, "BACK")


def _set_icon_visible(root: ET.Element, icon_id: str, visible: bool) -> None:
    el = _find_by_logical_id(root, icon_id)
    if el is None:
        return
    _set_visible(el, visible)
    # lightblue icon is a group wrapping the circle — clear nested display too.
    if visible:
        for node in el.iter():
            if node is el:
                continue
            style = node.get("style") or ""
            if "display:" in style or "display" in node.attrib:
                _set_visible(node, True)


def apply_ui_color_svg_state(root: ET.Element, state: MainSettingsState) -> None:
    nav = str(getattr(state, "ui_color_nav", "classes") or "classes")
    focused = str(getattr(state, "ui_color_focused_id", "") or "")
    active_class = str(getattr(state, "ui_color_active_class", "") or "")

    keys = {
        "accent": str(getattr(state, "ui_color_accent_key", "white") or "white"),
        "ui": str(getattr(state, "ui_color_ui_key", "red") or "red"),
        "button": str(getattr(state, "ui_color_button_key", "black") or "black"),
    }

    # Class label chrome: white when that class is the focus target (or active in swatch nav).
    for cls, text_id in _TEXT_IDS.items():
        text_el = _find_by_logical_id(root, text_id)
        if text_el is not None:
            # Right-align all three labels to the same edge (Illustrator left-nudged "button").
            y = _CLASS_LABEL_Y.get(cls, 0.0)
            text_el.set(
                "transform",
                f"translate({_CLASS_LABEL_RIGHT_X:.2f} {y:.2f})",
            )
            text_el.set("text-anchor", "end")
            text_el.set("font-family", "SharpSans-Semibold, 'Sharp Sans'")
            text_el.set("font-size", _CLASS_LABEL_SIZE)
            text_el.attrib.pop("style", None)
            # Drop Illustrator horizontal scale on button_text.
            for node in text_el.iter():
                if node.tag.endswith("text") or node.tag.endswith("tspan"):
                    node.attrib.pop("style", None)
        if nav == "swatches":
            active = active_class == cls
        else:
            active = focused == cls
        _paint_text(text_el, _COLOR_WHITE if active else _COLOR_TEXT_IDLE)

    # One icon visible per class — the currently chosen color.
    for cls, swatches in _CLASS_SWATCHES.items():
        chosen = keys.get(cls, _DEFAULT_KEYS[cls])
        # While browsing swatches for this class, the focused swatch is the preview.
        if nav == "swatches" and active_class == cls and focused in ui_color_swatch_focus_ring(cls):
            chosen = focused
        for sw in swatches:
            _set_icon_visible(root, sw.icon_id, sw.key == chosen)

    # Accent outline on the swatch container is turned off (back accent stays).
    _set_visible(_find_by_logical_id(root, "color_container_accent"), False)

    _sync_back_button(root, selected=(nav == "classes" and focused == "back"))

def _full_theme_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None,
    path: Path,
) -> np.ndarray:
    """Theme plate at full brightness (color page is not dimmed like preferences)."""
    ui_hex = str(state.theme.ui)
    adir = str(assets_dir if assets_dir is not None else path.parent.parent)
    key = (ui_hex, adir, int(DESIGN_W), int(DESIGN_H))
    cached = _THEME_BG_CACHE.get(key)
    if cached is not None:
        return cached
    bg_bgra = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    bg_bgra[:, :, 3] = 255
    _draw_container_background_bgra(bg_bgra, ui_hex=ui_hex, assets_dir=adir)
    if len(_THEME_BG_CACHE) >= _THEME_BG_CACHE_MAX:
        _THEME_BG_CACHE.clear()
    _THEME_BG_CACHE[key] = bg_bgra
    return bg_bgra


def render_ui_color_settings_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None = None,
    svg_path: Path | str | None = None,
) -> np.ndarray:
    path = Path(svg_path) if svg_path is not None else default_ui_color_svg_path(assets_dir)
    root = _svg_tree_from_path(path)
    apply_ui_color_svg_state(root, state)
    _disable_embedded_settings_background_layers(root)
    _prune_display_none(root)
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    ui_bgra = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )
    bg = _full_theme_bgra(state, assets_dir=assets_dir, path=path)
    return _composite_bgra_over_bgra(bg, ui_bgra)


def clear_ui_color_render_caches() -> None:
    _SVG_TREE_TEMPLATES.clear()
    _THEME_BG_CACHE.clear()


__all__ = [
    "THEME_SWATCH_HEXES",
    "UI_SWATCH_HEXES",
    "apply_color_keys_to_state",
    "apply_ui_color_svg_state",
    "clear_ui_color_render_caches",
    "default_ui_color_svg_path",
    "hex_for_color_key",
    "load_persisted_theme_into_state",
    "read_ui_color_keys",
    "render_ui_color_settings_bgra",
    "theme_from_color_keys",
    "ui_color_class_focus_ring",
    "ui_color_swatch_focus_ring",
    "write_ui_color_keys",
]
