"""
Now-playing zone widget selector — ``settings_0.8/pigeon_settings_preferences.svg``.

Opened from pigeon device settings when **prefs** / settings is activated.

Navigation A (zones): pick zone 1–5 or BACK (BACK → pigeon settings).
Navigation B (widgets): with a zone locked selected, cycle widgets available
for that zone; activate confirms and returns to zones. BACK still exits to
pigeon settings.
"""

from __future__ import annotations

import copy
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.widgets.main_settings import (
    MainSettingsState,
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
XLINK_NS = "http://www.w3.org/1999/xlink"

# Crop the Illustrator board to the 800×480 preferences artboard.
_PREFS_VIEWBOX = (339.0, 440.0, 800.0, 480.0)

_COLOR_BLACK = "#000000"
_COLOR_WHITE = "#FFFFFF"
_COLOR_GRAY = "#808080"  # 50% gray for unavailable chrome

# Defaults match current now-playing layout.
DEFAULT_ZONE_WIDGETS: tuple[str, str, str, str, str] = (
    "clock",
    "poster",
    "volume",
    "cast_info",
    "now_playing",
)

# Spec catalog — which widgets may occupy each zone.
ZONE_WIDGET_CATALOG: dict[int, tuple[str, ...]] = {
    1: ("audio_levels", "clock", "poster", "volume", "now_playing", "cast_info"),
    2: ("audio_levels", "clock", "poster", "volume", "now_playing", "cast_info"),
    3: ("audio_levels", "clock", "poster", "volume", "now_playing", "cast_info"),
    4: ("cast_info",),
    5: ("now_playing", "cast_info"),
}

# Selector chrome groups (navigation B), left → right in the SVG.
_WIDGET_SELECTOR_ORDER: tuple[str, ...] = (
    "audio_levels",
    "clock",
    "poster",
    "volume",
    "now_playing",
    "cast_info",
)

# Match settings_main EXIT geometry on the preferences artboard (viewBox +339,+440).
_BACK_BUTTON_D = (
    "M426.443,535H376.367c-5.278,0-9.557-4.279-9.557-9.557V507.628"
    "c0-5.831,4.727-10.557,10.557-10.557h49.075c5.278,0,9.557,4.279,9.557,9.557"
    "v18.814C436,530.721,431.721,535,426.443,535z"
)
_BACK_TEXT_X = 401.405  # button center (design 62.405 + 339)
_BACK_TEXT_Y = 525.7403  # same baseline as settings_main EXIT
_COLOR_EXIT_FILL = "#202020"
# COLOR label: group is translate(339,440); Pillow text overlay ignores parent
# transforms, so the <text> matrix must be absolute SVG (same as BACK).
_COLOR_TEXT_X = 1060.103  # pill center local 721.103 + 339
_COLOR_TEXT_Y = 803.3755  # baseline local 363.3755 + 440
_COLOR_TEXT_SIZE = "21"

_ZONE_NAV_ORDER: tuple[str, ...] = (
    "zone1",
    "zone2",
    "zone3",
    "zone4",
    "zone5",
    "color",  # after zone5; before BACK
    "exit",
)

_SVG_TREE_TEMPLATES: dict[tuple[str, int, int], ET.Element] = {}
_SVG_TREE_TEMPLATE_MAX = 4
# Structure base (theme+UI without live clock/NP overlays). Keyed without wall clock.
_PREFS_STRUCTURE_CACHE: dict[tuple[object, ...], tuple[np.ndarray, dict[str, object]]] = {}
_PREFS_STRUCTURE_CACHE_MAX = 24
# Dimmed theme plate per ui_hex (full-frame scrim is expensive on Pi).
_PREFS_DIMMED_BG_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_PREFS_DIMMED_BG_CACHE_MAX = 4
# Cover-fit + rounded poster patches per zone master.
_PREFS_POSTER_PATCH_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_PREFS_POSTER_PATCH_CACHE_MAX = 12


def default_preferences_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_PREFERENCES_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "pigeon_settings_preferences.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "pigeon_settings_preferences.svg"


def _layer_key(el: ET.Element) -> str:
    dn = (el.get("data-name") or "").strip()
    raw = dn if dn else (el.get("id") or "").strip()
    if not dn and raw:
        raw = re.sub(r"-\d+$", "", raw)
    return re.sub(r"\s+", "_", raw)


def _normalize_zone_widgets(
    values: tuple[str, ...] | list[str] | dict[int, str] | None,
) -> tuple[str, str, str, str, str]:
    base = list(DEFAULT_ZONE_WIDGETS)
    if isinstance(values, dict):
        for z, name in values.items():
            try:
                zi = int(z)
            except (TypeError, ValueError):
                continue
            if 1 <= zi <= 5 and str(name or "").strip():
                base[zi - 1] = str(name).strip()
    elif isinstance(values, (tuple, list)):
        for i, name in enumerate(list(values)[:5]):
            if str(name or "").strip():
                base[i] = str(name).strip()
    # Clamp to catalog.
    out: list[str] = []
    for i, name in enumerate(base):
        zone = i + 1
        catalog = ZONE_WIDGET_CATALOG.get(zone, ())
        out.append(name if name in catalog else DEFAULT_ZONE_WIDGETS[i])
    return (out[0], out[1], out[2], out[3], out[4])


def read_now_playing_zone_widgets() -> tuple[str, str, str, str, str]:
    try:
        from pigeon.app_state import read_app_state

        raw = read_app_state().get("now_playing_zone_widgets")
    except Exception:
        raw = None
    if isinstance(raw, dict):
        return _normalize_zone_widgets(raw)
    if isinstance(raw, list):
        return _normalize_zone_widgets(raw)
    return DEFAULT_ZONE_WIDGETS


def write_now_playing_zone_widgets(
    values: tuple[str, ...] | list[str] | dict[int, str],
    *,
    persist: bool = True,
) -> tuple[str, str, str, str, str]:
    """Normalize zone widgets; optionally persist to app state.

    Live widget-nav preview should pass ``persist=False`` so the Pi SD card is
    not rewritten on every Left/Right. Persist on activate / BACK / exit.
    """
    norm = _normalize_zone_widgets(values)
    if not persist:
        return norm
    try:
        from pigeon.app_state import write_app_state

        write_app_state(
            now_playing_zone_widgets={
                "1": norm[0],
                "2": norm[1],
                "3": norm[2],
                "4": norm[3],
                "5": norm[4],
            }
        )
    except Exception:
        pass
    return norm


def zone0_date_align(
    assignments: tuple[str, ...] | list[str] | dict[int, str] | None = None,
) -> str | None:
    """Where the zone0 date header sits on now-playing.

    - default → ``left``
    - zone1 poster → ``center``
    - zone1 + zone2 poster → ``right``
    - zone1 + zone2 + zone3 poster → ``None`` (header off)

    Preferences keeps this logic but does not display the header.
    """
    a = _normalize_zone_widgets(assignments)
    p1 = a[0] == "poster"
    p2 = a[1] == "poster"
    p3 = a[2] == "poster"
    if p1 and p2 and p3:
        return None
    if p1 and p2:
        return "right"
    if p1:
        return "center"
    return "left"


def preferences_zone_focus_ring() -> tuple[str, ...]:
    return _ZONE_NAV_ORDER


def preferences_widget_focus_ring(zone: int) -> tuple[str, ...]:
    """BACK + widgets available for ``zone`` (selector order)."""
    z = int(zone)
    available = set(ZONE_WIDGET_CATALOG.get(z, ()))
    ring: list[str] = ["exit"]
    for wid in _WIDGET_SELECTOR_ORDER:
        if wid in available:
            ring.append(wid)
    return tuple(ring)


def _strip_embedded_poster_images(root: ET.Element) -> int:
    """Remove ``poster_tmdb`` ``<image>`` nodes (≈2MB base64). Redrawn after raster."""
    parent_map = {c: p for p in root.iter() for c in p}
    removed = 0
    for el in list(root.iter()):
        if not el.tag.endswith("image"):
            continue
        key = _layer_key(el)
        eid = (el.get("id") or "").strip()
        if key == "poster_tmdb" or eid.startswith("poster_tmdb"):
            parent = parent_map.get(el)
            if parent is not None:
                try:
                    parent.remove(el)
                    removed += 1
                except (ValueError, TypeError):
                    _set_visible(el, False)
            else:
                _set_visible(el, False)
    return removed


def _svg_tree_from_path(path: Path) -> ET.Element:
    try:
        st = path.stat()
        key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(path), 0, 0)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        root = ET.parse(path).getroot()
        x, y, w, h = _PREFS_VIEWBOX
        root.set("viewBox", f"{x} {y} {w} {h}")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        # Drop embedded posters once so every deepcopy stays ~100KB, not ~2MB.
        _strip_embedded_poster_images(root)
        if len(_SVG_TREE_TEMPLATES) >= _SVG_TREE_TEMPLATE_MAX:
            _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _paint_subtree(
    el: ET.Element | None,
    *,
    fill: str | None = None,
    stroke: str | None = None,
) -> None:
    if el is None:
        return
    tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
    if tag in ("path", "polygon", "polyline", "circle", "ellipse", "rect"):
        kwargs: dict[str, str] = {}
        if fill is not None:
            kwargs["fill"] = fill
        if stroke is not None:
            kwargs["stroke"] = stroke
        if kwargs:
            _set_paint(el, **kwargs)
    for child in list(el):
        _paint_subtree(child, fill=fill, stroke=stroke)


def _paint_text(el: ET.Element | None, color: str) -> None:
    if el is None:
        return
    nodes = [el] if el.tag.endswith("text") else [
        n for n in el.iter() if n.tag.endswith("text") or n.tag.endswith("tspan")
    ]
    if not nodes:
        nodes = [el]
    for node in nodes:
        _set_paint(node, fill=color)


def _zone_group_parts(group: ET.Element | None) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    """Return (button, accent, text) for a selector_zoneN_group (ids may be missing)."""
    if group is None:
        return None, None, None
    kids = list(group)
    button = accent = text = None
    for child in kids:
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        key = _layer_key(child)
        if tag == "text" or "text" in key:
            text = child
        elif tag == "circle":
            fill = (child.get("fill") or "").strip().lower()
            if fill in ("none",) or "accent" in key:
                accent = child if accent is None else accent
            else:
                # Missing fill or solid → button.
                if button is None:
                    button = child
                else:
                    accent = child
    # Fallback by order: button, accent, text.
    if button is None and kids:
        button = kids[0]
    if accent is None and len(kids) > 1:
        accent = kids[1]
    if text is None and len(kids) > 2:
        text = kids[2]
    # Ensure Illustrator-unnamed zone 2–5 kids get stable data-names.
    zkey = _layer_key(group)
    m = re.fullmatch(r"selector_zone(\d)_group", zkey)
    if m:
        z = m.group(1)
        if button is not None and not (button.get("id") or button.get("data-name")):
            button.set("data-name", f"selector_zone{z}_button")
        if accent is not None and not (accent.get("id") or accent.get("data-name")):
            accent.set("data-name", f"selector_zone{z}_accent")
        if text is not None and not (text.get("id") or text.get("data-name")):
            text.set("data-name", f"selector_zone{z}_text")
    return button, accent, text


def _widget_group_parts(
    group: ET.Element | None,
) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    """Return (button path, accent path, text) inside a selector_*_group."""
    if group is None:
        return None, None, None
    button = accent = text = None
    # audio_levels nests one extra group.
    scope = group
    inner = None
    for child in list(group):
        if _layer_key(child) == "selector_audio_level_group":
            inner = child
            break
    if inner is not None:
        scope = inner
    for child in list(scope):
        key = _layer_key(child)
        tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if tag == "text" or "text" in key:
            text = child
        elif "accent" in key:
            accent = child
        elif "button" in key or tag == "path":
            if button is None:
                button = child
            elif accent is None:
                accent = child
    return button, accent, text


def _apply_zone_chrome(group: ET.Element | None, *, selected: bool) -> None:
    button, accent, text = _zone_group_parts(group)
    if selected:
        _paint_subtree(button, fill=_COLOR_WHITE, stroke=_COLOR_BLACK)
        _paint_text(text, _COLOR_BLACK)
    else:
        _paint_subtree(button, fill=_COLOR_BLACK, stroke=_COLOR_BLACK)
        _paint_text(text, _COLOR_WHITE)
    # Accent stays white stroke / no fill in both states.
    _paint_subtree(accent, fill="none", stroke=_COLOR_WHITE)


def _apply_widget_chrome(
    group: ET.Element | None,
    *,
    selected: bool,
    available: bool,
    idle_fill: str | None = None,
) -> None:
    button, accent, text = _widget_group_parts(group)
    rest_fill = idle_fill if idle_fill is not None else _COLOR_BLACK
    if not available:
        _paint_subtree(button, fill=rest_fill, stroke=_COLOR_BLACK)
        _paint_subtree(accent, fill="none", stroke=_COLOR_GRAY)
        _paint_text(text, _COLOR_GRAY)
        return
    if selected:
        _paint_subtree(button, fill=_COLOR_WHITE, stroke=_COLOR_BLACK)
        _paint_text(text, _COLOR_BLACK)
        _paint_subtree(accent, fill="none", stroke=_COLOR_WHITE)
        return
    _paint_subtree(button, fill=rest_fill, stroke=_COLOR_BLACK)
    _paint_text(text, _COLOR_WHITE)
    _paint_subtree(accent, fill="none", stroke=_COLOR_WHITE)


def _sync_preferences_back_button(root: ET.Element) -> None:
    """Match settings_main EXIT placement; label centered as BACK."""
    group = _find_by_logical_id(root, "selector_exit_group")
    button, accent, text = _widget_group_parts(group)
    for path_el in (button, accent):
        if path_el is not None:
            path_el.set("d", _BACK_BUTTON_D)
            if path_el is button and not (path_el.get("fill") or "").strip():
                path_el.set("fill", _COLOR_EXIT_FILL)
    if text is not None:
        text.set("transform", f"translate({_BACK_TEXT_X:.4f} {_BACK_TEXT_Y:.4f})")
        text.set("text-anchor", "middle")
        text.set("font-family", "Digital-7, Digital-7")
        text.set("font-size", "29")
        _set_text_content(text, "BACK")


def _prefs_color_group(root: ET.Element) -> ET.Element | None:
    return _find_by_logical_id(root, "settings_pigeon_preferences_color_group")


def _prefs_color_find(group: ET.Element | None, logical_id: str) -> ET.Element | None:
    """Find a descendant of the color group by decoded logical id."""
    from pigeon.widgets.main_settings import _normalize_logical

    if group is None:
        return None
    want = _normalize_logical(logical_id)
    for el in group.iter():
        if _normalize_logical(el.get("id") or "") == want:
            return el
    return _find_by_logical_id(group, logical_id)


def _apply_preferences_color_chrome(root: ET.Element, *, selected: bool) -> None:
    """Selected/deselected chrome for the prefs color control (nav after zone5).

    Graphic: ``color_button_prefs_selected`` / ``_deselected`` visibility toggle.
    Text pill:
      - ``…_color_button_interior_fill`` — black deselected, white selected
      - ``…_color_text_accent`` — path stroke: white deselected, black selected
      - ``…_color_text_accent_interior_stroke`` — outer stroke (white)
      - ``…_color_text`` — glyph fill: white deselected, black selected
    """
    group = _prefs_color_group(root)
    if group is None:
        return
    _set_visible(group, True)
    sel = _prefs_color_find(group, "color_button_prefs_selected")
    des = _prefs_color_find(group, "color_button_prefs_deselected")
    _set_visible(sel, bool(selected))
    _set_visible(des, not bool(selected))

    interior_fill = _prefs_color_find(
        group, "settings_pigeon_preferences_color_button_interior_fill"
    )
    text_accent = _prefs_color_find(
        group, "settings_pigeon_preferences_color_text_accent"
    )
    interior_stroke = _prefs_color_find(
        group, "settings_pigeon_preferences_color_text_accent_interior_stroke"
    ) or _prefs_color_find(
        group, "settings_pigeon_preferences_color_text_interior_stroke"
    )
    text_el = _prefs_color_find(group, "settings_pigeon_preferences_color_text")
    if text_el is not None:
        # Absolute coords so Pillow overlay (no parent transforms) lands in the pill.
        text_el.set(
            "transform",
            f"matrix(1 0 0 1 {_COLOR_TEXT_X:.4f} {_COLOR_TEXT_Y:.4f})",
        )
        text_el.set("text-anchor", "middle")
        text_el.set("font-family", "Digital-7, Digital-7")
        text_el.set("font-size", _COLOR_TEXT_SIZE)
        style = text_el.get("style") or ""
        style = re.sub(
            r"font-family\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE
        )
        style = re.sub(
            r"font-size\s*:\s*[^;]+;?\s*", "", style, flags=re.IGNORECASE
        )
        style = style.strip().rstrip(";")
        if style:
            text_el.set("style", style)
        else:
            text_el.attrib.pop("style", None)
        _set_text_content(text_el, "COLOR")

    if selected:
        if interior_fill is not None:
            _set_paint(interior_fill, fill=_COLOR_WHITE)
        if text_accent is not None:
            _set_paint(text_accent, fill="none", stroke=_COLOR_BLACK)
        if interior_stroke is not None:
            _set_paint(interior_stroke, fill="none", stroke=_COLOR_WHITE)
        _paint_text(text_el, _COLOR_BLACK)
    else:
        if interior_fill is not None:
            _set_paint(interior_fill, fill=_COLOR_BLACK)
        if text_accent is not None:
            _set_paint(text_accent, fill="none", stroke=_COLOR_WHITE)
        if interior_stroke is not None:
            _set_paint(interior_stroke, fill="none", stroke=_COLOR_WHITE)
        _paint_text(text_el, _COLOR_WHITE)


def _selector_pill_center(path_el: ET.Element | None) -> tuple[float, float] | None:
    """Center of the shared rounded selector pill path (Illustrator exit-button shape)."""
    if path_el is None:
        return None
    d = path_el.get("d") or ""
    m = re.match(
        r"[Mm]\s*([-\d.]+)\s*,\s*([-\d.]+)\s*[Hh]\s*([-\d.]+)",
        d.strip(),
    )
    if not m:
        return None
    mx = float(m.group(1))
    my = float(m.group(2))
    h = float(m.group(3))
    # Rounded pill: start is on the top edge after the left corner; right has
    # radius 9.56, then bottom span 50.08, then left radius 9.56.
    right = mx + h + 9.56
    left = right - 9.56 - 50.08 - 9.56
    top = my
    bottom = my + 9.56 + 18.81 + 9.56
    return ((left + right) / 2.0, (top + bottom) / 2.0)


@lru_cache(maxsize=8)
def _selector_label_font(size_px: int = 15):
    from PIL import ImageFont

    from pigeon.font_paths import resolve_ui_font_extrabold

    path = resolve_ui_font_extrabold()
    if path:
        try:
            return ImageFont.truetype(str(path), int(size_px))
        except OSError:
            pass
    return ImageFont.load_default()


_SELECTOR_LABELS: dict[str, tuple[str, ...]] = {
    "audio_levels": ("audio", "levels"),
    "clock": ("clock",),
    "poster": ("poster", "art"),
    "volume": ("volume",),
    "now_playing": ("now", "playing"),
    "cast_info": ("cast", "info"),
}
_SELECTOR_LABEL_SIZE_PX = 15
# Fixed ink-center pitch for every multi-line selector label (matches visual spacing).
_SELECTOR_LABEL_LINE_PITCH = 14.0
# Per-widget nudge (design px): negative = up. Tuned against prefs pill chrome.
_SELECTOR_LABEL_Y_NUDGE_PX: dict[str, float] = {
    "audio_levels": -2.0,
    "clock": -2.0,
    "poster": 0.0,
    "volume": -2.0,
    "now_playing": 0.0,
    "cast_info": -2.0,
}


def _clear_preferences_selector_label_texts(root: ET.Element) -> None:
    """Hide SVG label glyphs — labels are drawn with measured Pillow patches."""
    for wid in _SELECTOR_LABELS:
        group = _selector_group_for_widget(root, wid)
        _button, _accent, text = _widget_group_parts(group)
        if text is None:
            continue
        _set_visible(text, False)


def _selector_label_fill_rgba(
    *,
    selected: bool,
    available: bool,
) -> tuple[int, int, int, int]:
    if not available:
        return (128, 128, 128, 255)
    if selected:
        return (0, 0, 0, 255)
    return (255, 255, 255, 255)


@lru_cache(maxsize=128)
def _sharp_text_patch(
    text: str,
    size_px: int,
    fill_rgba: tuple[int, int, int, int],
) -> tuple[np.ndarray, int, int]:
    """Tight BGRA patch for one Sharp Sans Extrabold line."""
    from PIL import Image, ImageDraw

    draw_text = str(text or "")
    if not draw_text:
        return np.zeros((1, 1, 4), dtype=np.uint8), 0, 0
    font = _selector_label_font(size_px)
    probe = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
    draw = ImageDraw.Draw(probe)
    left, top, right, bottom = draw.textbbox((0, 0), draw_text, font=font)
    tw, th = max(1, right - left), max(1, bottom - top)
    pad = 1
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text(
        (pad - left, pad - top),
        draw_text,
        font=font,
        fill=fill_rgba,
    )
    arr = np.asarray(img)
    import cv2

    return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA), tw + pad * 2, th + pad * 2


def _draw_preferences_selector_labels_bgra(
    bgra: np.ndarray,
    root: ET.Element,
    state: MainSettingsState,
) -> None:
    """Paint selector labels centered in their pills (fixed multi-line pitch)."""
    from pigeon.widgets.view_circles import _paste_patch_bgra

    nav = str(getattr(state, "preferences_nav", "zones") or "zones")
    active_zone = int(getattr(state, "preferences_active_zone", 0) or 0)
    focused = str(getattr(state, "preferences_focused_id", "") or "")
    available: set[str] = set()
    if nav == "widgets" and active_zone in ZONE_WIDGET_CATALOG:
        available = set(ZONE_WIDGET_CATALOG[active_zone])

    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    pitch = float(_SELECTOR_LABEL_LINE_PITCH)

    for wid, lines in _SELECTOR_LABELS.items():
        group = _selector_group_for_widget(root, wid)
        button, _accent, _text = _widget_group_parts(group)
        center = _selector_pill_center(button)
        if center is None:
            continue
        if nav == "zones":
            selected, is_avail = False, True
        else:
            selected = focused == wid
            is_avail = wid in available
        fill = _selector_label_fill_rgba(selected=selected, available=is_avail)
        patches = [
            _sharp_text_patch(line, _SELECTOR_LABEL_SIZE_PX, fill)
            for line in lines
        ]
        if not patches:
            continue

        cx_px = (center[0] - vb_x) * sx
        cy_px = (center[1] - vb_y) * sy + float(
            _SELECTOR_LABEL_Y_NUDGE_PX.get(wid, 0.0)
        )
        n = len(patches)
        # Same ink-center pitch for every pair (poster/art == audio/levels, etc.).
        first_center = cy_px - ((n - 1) * pitch) / 2.0
        for i, (patch, tw, th) in enumerate(patches):
            line_cy = first_center + i * pitch
            _paste_patch_bgra(
                bgra,
                patch,
                int(round(cx_px - tw / 2.0)),
                int(round(line_cy - th / 2.0)),
            )


_ZONE_NUMERAL_SIZE_PX = 21
_ZONE_BUTTON_STROKE_PX = 7
_ZONE_ACCENT_STROKE_PX = 2
# Black scrim between theme background and prefs chrome (80% opaque).
_PREFS_BACKGROUND_DIM_ALPHA = int(round(255 * 0.80))

# Prefs SVG clip/accent frames for zone 1–3 poster art (outer rounded rect).
# PyMuPDF ignores ``clip-path``, so images are redrawn with this mask after rasterize.
_PREFS_POSTER_FRAMES: dict[int, tuple[float, float, float, float, float]] = {
    # zone: (x, y, w, h, rx) in SVG user units
    1: (524.16, 562.09, 133.95, 200.93, 6.79),
    2: (691.26, 562.09, 133.95, 200.93, 6.79),
    3: (857.85, 562.09, 133.95, 200.93, 6.79),
}
_PREFS_POSTER_IMAGE_IDS: dict[int, tuple[str, ...]] = {
    1: ("poster_tmdb-3", "poster_tmdb"),
    2: ("poster_tmdb-2", "poster_tmdb"),
    3: ("poster_tmdb",),
}
_PREFS_POSTER_MASTER_CACHE: dict[tuple[str, int, int], dict[int, np.ndarray]] = {}


def _clear_preferences_zone_number_texts(root: ET.Element) -> None:
    """Hide SVG zone numerals — digits are drawn with measured Pillow patches."""
    for zone in (1, 2, 3, 4, 5):
        group = _find_by_logical_id(root, f"selector_zone{zone}_group")
        _button, _accent, text = _zone_group_parts(group)
        if text is None:
            continue
        _set_visible(text, False)


def _draw_preferences_zone_numbers_bgra(
    bgra: np.ndarray,
    root: ET.Element,
    state: MainSettingsState,
) -> None:
    """Paint zone selector chrome + numerals above widget layers."""
    import cv2

    from pigeon.widgets.view_circles import _draw_filled_circle_bgra, _paste_patch_bgra

    nav = str(getattr(state, "preferences_nav", "zones") or "zones")
    active_zone = int(getattr(state, "preferences_active_zone", 0) or 0)
    focused = str(getattr(state, "preferences_focused_id", "") or "")
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)

    for zone in (1, 2, 3, 4, 5):
        group = _find_by_logical_id(root, f"selector_zone{zone}_group")
        button, _accent, _text = _zone_group_parts(group)
        if button is None:
            continue
        try:
            cx = float(button.get("cx") or 0.0)
            cy = float(button.get("cy") or 0.0)
            radius = float(button.get("r") or 19.99)
        except ValueError:
            continue
        if nav == "widgets" and active_zone == zone:
            selected = True
        elif nav == "zones":
            selected = focused == f"zone{zone}"
        else:
            selected = False
        cx_px = (cx - vb_x) * sx
        cy_px = (cy - vb_y) * sy
        r_px = radius * min(sx, sy)
        fill_bgr = (255, 255, 255) if selected else (0, 0, 0)
        _draw_filled_circle_bgra(
            bgra,
            cx=cx_px,
            cy=cy_px,
            r=r_px,
            fill_bgr=fill_bgr,
            stroke_bgr=(0, 0, 0),
            stroke=_ZONE_BUTTON_STROKE_PX,
        )
        # White accent ring on top of the button stroke (matches SVG accent).
        accent_size = int(round(r_px * 2)) + _ZONE_ACCENT_STROKE_PX * 2 + 4
        accent = np.zeros((accent_size, accent_size, 4), dtype=np.uint8)
        center = (accent_size // 2, accent_size // 2)
        cv2.circle(
            accent,
            center,
            max(1, int(round(r_px))),
            (255, 255, 255, 255),
            _ZONE_ACCENT_STROKE_PX,
            lineType=cv2.LINE_AA,
        )
        _paste_patch_bgra(
            bgra,
            accent,
            int(round(cx_px - accent_size / 2.0)),
            int(round(cy_px - accent_size / 2.0)),
        )
        digit_fill = (0, 0, 0, 255) if selected else (255, 255, 255, 255)
        patch, tw, th = _sharp_text_patch(
            str(zone),
            _ZONE_NUMERAL_SIZE_PX,
            digit_fill,
        )
        _paste_patch_bgra(
            bgra,
            patch,
            int(round(cx_px - tw / 2.0)),
            int(round(cy_px - th / 2.0)),
        )


def _hide_preferences_poster_images(root: ET.Element) -> None:
    """Drop SVG poster bitmaps — PyMuPDF ignores their rounded ``clip-path``."""
    for el in root.iter():
        if not el.tag.endswith("image"):
            continue
        key = _layer_key(el)
        eid = (el.get("id") or "").strip()
        if key == "poster_tmdb" or eid.startswith("poster_tmdb"):
            _set_visible(el, False)


# Color button gradient (design space). PyMuPDF ignores SVG clip-path, so the
# image is hidden before rasterize and redrawn with a rounded-rect alpha mask.
_COLOR_BTN_X = 696.091
_COLOR_BTN_Y = 271.014
_COLOR_BTN_W = 49.983
_COLOR_BTN_H = 49.983
_COLOR_BTN_RX = 10.35
_COLOR_BTN_IMG_TRANSFORM = (0.48, 0.48, 690.72, 265.92)  # sx, sy, tx, ty
_COLOR_BTN_CENTER = (721.09, 295.91, 8.91)  # cx, cy, half-size of inner chip
_COLOR_BTN_IMAGE_CACHE: dict[tuple[str, int, int], np.ndarray] = {}


def _hide_preferences_color_button_images(root: ET.Element) -> None:
    """Hide gradient ``<image>`` under the color control (redrawn after rasterize)."""
    group = _prefs_color_group(root)
    if group is None:
        return
    for el in group.iter():
        if el.tag.endswith("image"):
            _set_visible(el, False)


def _prefs_color_gradient_master(svg_path: Path) -> np.ndarray | None:
    """Decode the color-button gradient and enforce a rounded-rect alpha mask."""
    import base64

    import cv2
    from PIL import Image, ImageDraw

    try:
        st = svg_path.stat()
        key = (str(svg_path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(svg_path), 0, 0)
    cached = _COLOR_BTN_IMAGE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        root = ET.parse(svg_path).getroot()
    except Exception:
        return None
    href = ""
    for el in root.iter():
        if not el.tag.endswith("image"):
            continue
        try:
            w = float(el.get("width") or 0)
        except ValueError:
            w = 0.0
        # Posters are ~780 wide; the color swatch is 126.
        if w > 200:
            continue
        href = (
            el.get(f"{{{XLINK_NS}}}href")
            or el.get("href")
            or el.get("xlink:href")
            or ""
        )
        if href.startswith("data:image"):
            break
    if not href.startswith("data:image"):
        return None
    try:
        _header, b64 = href.split(",", 1)
        raw = base64.b64decode(b64)
        arr = np.frombuffer(raw, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    except Exception:
        return None
    if bgr is None or bgr.size == 0:
        return None
    if bgr.ndim == 2:
        bgra = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGRA)
    elif bgr.shape[2] == 3:
        bgra = cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    else:
        bgra = bgr
    sx, sy, tx, ty = _COLOR_BTN_IMG_TRANSFORM
    bx = (_COLOR_BTN_X - tx) / sx
    by = (_COLOR_BTN_Y - ty) / sy
    bw = _COLOR_BTN_W / sx
    bh = _COLOR_BTN_H / sy
    br = _COLOR_BTN_RX / min(sx, sy)
    ih, iw = int(bgra.shape[0]), int(bgra.shape[1])
    pil_mask = Image.new("L", (iw, ih), 0)
    ImageDraw.Draw(pil_mask).rounded_rectangle(
        [bx, by, bx + bw, by + bh], radius=br, fill=255
    )
    mask = np.asarray(pil_mask, dtype=np.uint8)
    out = bgra.copy()
    out[:, :, 3] = np.minimum(out[:, :, 3], mask)
    while len(_COLOR_BTN_IMAGE_CACHE) >= 4:
        _COLOR_BTN_IMAGE_CACHE.pop(next(iter(_COLOR_BTN_IMAGE_CACHE)))
    _COLOR_BTN_IMAGE_CACHE[key] = out
    return out


def _draw_preferences_color_button_bgra(
    bgra: np.ndarray,
    state: MainSettingsState,
    *,
    svg_path: Path,
) -> None:
    """Composite the selected color-button gradient with a hard rounded mask."""
    import cv2

    from pigeon.compositing import cv_resize_interp
    from pigeon.widgets.view_circles import _paste_patch_bgra, _rounded_rect_mask

    nav = str(getattr(state, "preferences_nav", "zones") or "zones")
    focused = str(getattr(state, "preferences_focused_id", "") or "")
    if not (nav == "zones" and focused == "color"):
        return
    master = _prefs_color_gradient_master(svg_path)
    if master is None or master.size == 0:
        return
    # Color-button geometry is authored in group-local units that already match
    # the 800×480 artboard after viewBox crop (group translate cancels vb origin).
    _vb_x, _vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    img_sx, img_sy, img_tx, img_ty = _COLOR_BTN_IMG_TRANSFORM
    x = int(round(img_tx * sx))
    y = int(round(img_ty * sy))
    tw = max(1, int(round(master.shape[1] * img_sx * sx)))
    th = max(1, int(round(master.shape[0] * img_sy * sy)))
    resized = cv2.resize(
        master,
        (tw, th),
        interpolation=cv_resize_interp(master.shape[1], master.shape[0], tw, th),
    )
    if resized.ndim == 3 and resized.shape[2] == 3:
        resized = cv2.cvtColor(resized, cv2.COLOR_BGR2BGRA)
    # Hard clip in frame space — resize can soften the master alpha mask.
    bx = int(round(_COLOR_BTN_X * sx))
    by = int(round(_COLOR_BTN_Y * sy))
    bw = max(1, int(round(_COLOR_BTN_W * sx)))
    bh = max(1, int(round(_COLOR_BTN_H * sy)))
    br = max(1, int(round(_COLOR_BTN_RX * min(sx, sy))))
    frame_mask = np.zeros((th, tw), dtype=np.uint8)
    ox, oy = bx - x, by - y
    local = _rounded_rect_mask(bw, bh, br)
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(tw, ox + bw), min(th, oy + bh)
    lx0, ly0 = x0 - ox, y0 - oy
    if x1 > x0 and y1 > y0:
        frame_mask[y0:y1, x0:x1] = local[ly0 : ly0 + (y1 - y0), lx0 : lx0 + (x1 - x0)]
    resized = resized.copy()
    resized[:, :, 3] = np.minimum(resized[:, :, 3], frame_mask)
    _paste_patch_bgra(bgra, resized, x, y)
    # Selected center chip (white rounded square + black edge).
    cx, cy, half = _COLOR_BTN_CENTER
    chip = max(2, int(round(half * 2 * min(sx, sy))))
    cx_px = int(round(cx * sx - chip / 2.0))
    cy_px = int(round(cy * sy - chip / 2.0))
    chip_rx = max(1, int(round(chip * 0.32)))
    chip_mask = _rounded_rect_mask(chip, chip, chip_rx)
    inset = max(1, int(round(chip * 0.12)))
    if chip > inset * 2:
        inner = _rounded_rect_mask(
            chip - inset * 2, chip - inset * 2, max(1, chip_rx - inset)
        )
        edge = chip_mask.astype(np.int16)
        edge[inset : inset + inner.shape[0], inset : inset + inner.shape[1]] -= (
            inner.astype(np.int16)
        )
        edge = np.clip(edge, 0, 255).astype(np.uint8)
        black = np.zeros((chip, chip, 4), dtype=np.uint8)
        black[:, :, 3] = edge
        fill = np.zeros((chip, chip, 4), dtype=np.uint8)
        fill[:, :, :3] = 255
        fill[inset : inset + inner.shape[0], inset : inset + inner.shape[1], 3] = inner
        _paste_patch_bgra(bgra, black, cx_px, cy_px)
        _paste_patch_bgra(bgra, fill, cx_px, cy_px)
    else:
        chip_patch = np.zeros((chip, chip, 4), dtype=np.uint8)
        chip_patch[:, :, :3] = 255
        chip_patch[:, :, 3] = chip_mask
        _paste_patch_bgra(bgra, chip_patch, cx_px, cy_px)


def _decode_svg_image_bgra(image_el: ET.Element) -> np.ndarray | None:
    href = (
        image_el.get(f"{{{XLINK_NS}}}href")
        or image_el.get("href")
        or image_el.get("xlink:href")
        or ""
    )
    if not href.startswith("data:image"):
        return None
    try:
        _header, b64 = href.split(",", 1)
    except ValueError:
        return None
    import base64

    import cv2

    raw = base64.b64decode(b64)
    arr = np.frombuffer(raw, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if bgr is None or bgr.size == 0:
        return None
    if bgr.ndim == 2:
        return cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGRA)
    if bgr.shape[2] == 3:
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2BGRA)
    if bgr.shape[2] == 4:
        return bgr
    return None


def _prefs_poster_masters(svg_path: Path) -> dict[int, np.ndarray]:
    """Cached zone→BGRA masters from embedded ``poster_tmdb`` images."""
    try:
        st = svg_path.stat()
        key = (str(svg_path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(svg_path), 0, 0)
    cached = _PREFS_POSTER_MASTER_CACHE.get(key)
    if cached is not None:
        return cached
    root = ET.parse(svg_path).getroot()
    by_id = {
        (el.get("id") or "").strip(): el
        for el in root.iter()
        if el.tag.endswith("image") and (el.get("id") or "").strip()
    }
    out: dict[int, np.ndarray] = {}
    for zone, ids in _PREFS_POSTER_IMAGE_IDS.items():
        for iid in ids:
            el = by_id.get(iid)
            if el is None:
                continue
            bgra = _decode_svg_image_bgra(el)
            if bgra is not None:
                out[zone] = bgra
                break
    while len(_PREFS_POSTER_MASTER_CACHE) >= 4:
        _PREFS_POSTER_MASTER_CACHE.pop(next(iter(_PREFS_POSTER_MASTER_CACHE)))
    _PREFS_POSTER_MASTER_CACHE[key] = out
    return out


# Prefs SVG volume-ring geometry (zones 1–3); maps 1:1 after viewBox crop.
_PREFS_NP_RING_OUTER_R = 76.52
_PREFS_NP_RING_INNER_R = 62.65
_PREFS_NP_CENTER_FILL_BGR = (0x23, 0x23, 0x23)
# Idle demo: 2:00:00 title with 1:30:00 remaining → 0:30:00 elapsed → 25%.
_PREFS_NP_DEMO_PROGRESS = 30.0 / 120.0
_PREFS_NP_TIME_SIZE_PX = 48
# Static volume preview: −22.5 dB on a −80..0 scale ≈ 72% of the ring.
_PREFS_VOLUME_DEMO_DB = "-22.5"
_PREFS_VOLUME_DEMO_CFG = "multi-in > auro3d"
_PREFS_VOLUME_SIZE_PX = 72
_PREFS_AUDIO_CFG_SIZE_PX = 42
# Cast columns in design space (prefs artboard after viewBox crop).
# Centers align with zone1/2/3 clock exteriors; baselines from SVG actor/character.
_PREFS_CAST_COLS_Z4: tuple[tuple[float, float, float], ...] = (
    (252.62, 347.53, 359.59),
    (419.72, 347.53, 359.59),
    (586.31, 347.53, 359.59),
)
_PREFS_CAST_COLS_Z5: tuple[tuple[float, float, float], ...] = (
    (252.62, 383.53, 395.59),
    (419.72, 383.53, 395.59),
    (586.31, 383.53, 395.59),
)
_PREFS_CAST_COL_W = 160
# Zone5 now-playing bar (design space from SVG remaining-icon bbox).
_PREFS_BAR_L = 196
_PREFS_BAR_R = 636
_PREFS_BAR_T = 374
_PREFS_BAR_H = 27
_PREFS_BAR_RX = 5
_PREFS_CTI_W = 5
_PREFS_BAR_TEXT_Y = 420
_PREFS_DEMO_CAST: tuple[tuple[str, str], ...] = (
    ("bill murray", "bob wiley"),
    ("richard dreyfuss", "leo marvin"),
    ("julie hagerty", "fey marvin"),
)


def _preferences_now_playing_progress(state: MainSettingsState) -> float:
    """Live progress when content is playing; otherwise the 2h / 1h30m demo."""
    raw = getattr(state, "preferences_np_progress", None)
    if raw is None:
        return float(_PREFS_NP_DEMO_PROGRESS)
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return float(_PREFS_NP_DEMO_PROGRESS)


def _collect_preferences_np_centers(
    root: ET.Element,
    state: MainSettingsState,
) -> dict[int, tuple[float, float]]:
    """SVG centers for zones 1–3 assigned ``now_playing`` (before prune)."""
    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    out: dict[int, tuple[float, float]] = {}
    for zone in (1, 2, 3):
        if assignments[zone - 1] != "now_playing":
            continue
        center = _prefs_volume_center(root, zone)
        if center is not None:
            out[zone] = center
    return out


def _collect_preferences_volume_centers(
    root: ET.Element,
    state: MainSettingsState,
) -> dict[int, tuple[float, float]]:
    """SVG centers for zones 1–3 assigned ``volume`` (pie redrawn after raster)."""
    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    out: dict[int, tuple[float, float]] = {}
    for zone in (1, 2, 3):
        if assignments[zone - 1] != "volume":
            continue
        center = _prefs_volume_center(root, zone)
        if center is not None:
            out[zone] = center
    return out


def _preferences_volume_fraction(state: MainSettingsState | None = None) -> float:
    """Live ring fill when playing; otherwise demo −22.5 dB."""
    if state is not None and getattr(state, "preferences_live_content", False):
        raw = getattr(state, "preferences_volume_fraction", None)
        if raw is not None:
            try:
                return max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                pass
        vol = str(getattr(state, "preferences_volume", None) or "")
        if vol:
            try:
                from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

                return float(volume_fraction_from_display_line(vol))
            except Exception:
                pass
    try:
        from pigeon.widgets.playback_overlay import volume_fraction_from_display_line

        return float(volume_fraction_from_display_line(_PREFS_VOLUME_DEMO_DB))
    except Exception:
        return 0.72


def _preferences_volume_lines(state: MainSettingsState) -> tuple[str, str]:
    """Volume + audio-config readout (live when playing, else demo)."""
    from pigeon.widgets.playback_overlay import (
        _receiver_volume_display_line,
        receiver_audio_config_display_line,
    )

    if getattr(state, "preferences_live_content", False):
        vol = _receiver_volume_display_line(
            getattr(state, "preferences_volume", None) or ""
        )
        cfg = receiver_audio_config_display_line(
            str(getattr(state, "preferences_incoming", None) or ""),
            str(getattr(state, "preferences_config", None) or ""),
        )
        return vol, cfg
    vol = _receiver_volume_display_line(_PREFS_VOLUME_DEMO_DB)
    cfg = receiver_audio_config_display_line("multi-in", "auro3d") or _PREFS_VOLUME_DEMO_CFG
    return vol, cfg


def _collect_preferences_clock_centers(
    root: ET.Element,
    state: MainSettingsState,
) -> dict[int, tuple[float, float]]:
    """SVG centers for zones 1–3 assigned ``clock`` (digital overlay after raster)."""
    from pigeon.widgets.view_circles import _find_by_key

    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    out: dict[int, tuple[float, float]] = {}
    for zone in (1, 2, 3):
        if assignments[zone - 1] != "clock":
            continue
        clock = _find_by_key(root, f"zone{zone}_clock_group")
        if clock is None:
            continue
        exterior = _find_by_key(clock, f"zone{zone}_clock_exterior_accent")
        try:
            cx = float((exterior.get("cx") if exterior is not None else None) or 0.0)
            cy = float((exterior.get("cy") if exterior is not None else None) or 0.0)
        except ValueError:
            continue
        if cx and cy:
            out[zone] = (cx, cy)
    return out


def _draw_preferences_clock_digitals_bgra(
    bgra: np.ndarray,
    centers: dict[int, tuple[float, float]],
    *,
    now: datetime | None = None,
) -> None:
    """Live HH:MM + date overlay for clock zones (structure bitmap keeps ticks frozen)."""
    from pigeon.widgets import view_circles as vc

    if not centers:
        return
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    when = now or datetime.now()
    hhmm = vc._clock_hhmm(when)
    time_p, _, _ = vc._text_patch_digital7(hhmm, size_px=_PREFS_NP_TIME_SIZE_PX)
    # No curved date on the preferences page — live now-playing keeps it.
    for _zone, center in centers.items():
        cx = (center[0] - vb_x) * sx
        cy = (center[1] - vb_y) * sy
        vc._paste_centered(bgra, time_p, cx, cy)


def _draw_preferences_circular_now_playing_bgra(
    bgra: np.ndarray,
    centers: dict[int, tuple[float, float]],
    state: MainSettingsState,
    *,
    now: datetime | None = None,
) -> None:
    """Zones 1–3 now_playing: progress ring + time-of-day (demo when idle)."""
    from pigeon.widgets import view_circles as vc

    if not centers:
        return
    pf = _preferences_now_playing_progress(state)
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    outer_r = float(_PREFS_NP_RING_OUTER_R) * min(sx, sy)
    inner_r = float(_PREFS_NP_RING_INNER_R) * min(sx, sy)
    hhmm = vc._clock_hhmm(now)

    for _zone, center in centers.items():
        cx = (center[0] - vb_x) * sx
        cy = (center[1] - vb_y) * sy
        np_th = vc.np_theme_from_settings()
        vc._draw_progress_ring(
            bgra,
            cx=cx,
            cy=cy,
            outer_r=outer_r,
            inner_r=inner_r,
            fraction=1.0,
            fill_bgr=vc._COLOR_CHROME_BGR,
            fill_opacity=0.42,
            stroke=0,
        )
        if pf > 1e-6:
            vc._draw_progress_ring(
                bgra,
                cx=cx,
                cy=cy,
                outer_r=outer_r,
                inner_r=inner_r,
                fraction=pf,
                fill_bgr=np_th.ui_bgr,
                fill_opacity=1.0,
                stroke=0,
            )
        vc._draw_progress_ring(
            bgra,
            cx=cx,
            cy=cy,
            outer_r=outer_r,
            inner_r=inner_r,
            fraction=1.0,
            fill_bgr=vc._COLOR_CHROME_BGR,
            fill_opacity=0.0,
            stroke=2,
            stroke_bgr=vc._COLOR_CHROME_BGR,
            stroke_opacity=0.85,
        )
        vc._draw_filled_circle_bgra(
            bgra,
            cx=cx,
            cy=cy,
            r=inner_r,
            fill_bgr=vc._COLOR_CENTER_BLACK_BGR,
            stroke_bgr=vc._COLOR_CHROME_BGR,
            stroke=2,
            fill_opacity=1.0,
        )
        time_p, _, _ = vc._text_patch_digital7(hhmm, size_px=_PREFS_NP_TIME_SIZE_PX)
        vc._paste_centered(bgra, time_p, cx, cy)


def _draw_preferences_volume_bgra(
    bgra: np.ndarray,
    centers: dict[int, tuple[float, float]],
    state: MainSettingsState | None = None,
) -> None:
    """Zones 1–3 volume: UI-color level pie + centered dB (no config label here)."""
    from pigeon.widgets import view_circles as vc

    if not centers:
        return
    st = state if state is not None else MainSettingsState()
    pf = _preferences_volume_fraction(st)
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    scale = min(sx, sy)
    outer_r = float(_PREFS_NP_RING_OUTER_R) * scale
    inner_r = float(_PREFS_NP_RING_INNER_R) * scale
    vol, _cfg = _preferences_volume_lines(st)
    np_th = vc.np_theme_from_settings()

    for _zone, center in centers.items():
        cx = (center[0] - vb_x) * sx
        cy = (center[1] - vb_y) * sy
        vc._draw_progress_ring(
            bgra,
            cx=cx,
            cy=cy,
            outer_r=outer_r,
            inner_r=inner_r,
            fraction=1.0,
            fill_bgr=vc._COLOR_CHROME_BGR,
            fill_opacity=0.42,
            stroke=0,
        )
        if pf > 1e-6:
            vc._draw_progress_ring(
                bgra,
                cx=cx,
                cy=cy,
                outer_r=outer_r,
                inner_r=inner_r,
                fraction=pf,
                fill_bgr=np_th.ui_bgr,
                fill_opacity=1.0,
                stroke=0,
            )
        vc._draw_progress_ring(
            bgra,
            cx=cx,
            cy=cy,
            outer_r=outer_r,
            inner_r=inner_r,
            fraction=1.0,
            fill_bgr=vc._COLOR_CHROME_BGR,
            fill_opacity=0.0,
            stroke=2,
            stroke_bgr=vc._COLOR_CHROME_BGR,
            stroke_opacity=0.85,
        )
        vc._draw_filled_circle_bgra(
            bgra,
            cx=cx,
            cy=cy,
            r=inner_r,
            fill_bgr=vc._COLOR_CENTER_BLACK_BGR,
            stroke_bgr=vc._COLOR_CHROME_BGR,
            stroke=2,
            fill_opacity=1.0,
        )
        if vol:
            vol_p, _, _ = vc._volume_readout_patch(
                vol,
                inner_r=inner_r,
                max_size_px=_PREFS_VOLUME_SIZE_PX,
            )
            vc._paste_centered(bgra, vol_p, cx, cy)
        # No curved audio-config label on the preferences page — live
        # now-playing keeps it.


def _prefs_poster_patch_for_zone(
    zone: int,
    src: np.ndarray,
    *,
    svg_path: Path,
) -> tuple[np.ndarray, int, int]:
    """Cached cover-fit + rounded patch and paste origin for ``zone``."""
    import cv2

    from pigeon.compositing import cv_resize_interp
    from pigeon.widgets.view_circles import _rounded_rect_mask

    frame = _PREFS_POSTER_FRAMES[zone]
    fx, fy, fw, fh, frx = frame
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    x = int(round((fx - vb_x) * sx))
    y = int(round((fy - vb_y) * sy))
    tw = max(1, int(round(fw * sx)))
    th = max(1, int(round(fh * sy)))
    rx = max(1, int(round(frx * min(sx, sy))))
    try:
        st = svg_path.stat()
        path_key = (str(svg_path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        path_key = (str(svg_path), 0, 0)
    cache_key = (path_key, int(zone), tw, th, rx, int(src.shape[0]), int(src.shape[1]))
    cached = _PREFS_POSTER_PATCH_CACHE.get(cache_key)
    if cached is not None:
        return cached, x, y
    sh, sw = src.shape[:2]
    scale = max(tw / float(sw), th / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(
        src,
        (nw, nh),
        interpolation=cv_resize_interp(sw, sh, nw, nh),
    )
    x0 = max(0, (nw - tw) // 2)
    y0 = max(0, (nh - th) // 2)
    crop = resized[y0 : y0 + th, x0 : x0 + tw]
    if crop.shape[0] != th or crop.shape[1] != tw:
        crop = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
    if crop.ndim == 3 and crop.shape[2] == 3:
        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
    mask = _rounded_rect_mask(tw, th, rx)
    patch = crop.copy()
    patch[:, :, 3] = np.minimum(patch[:, :, 3], mask)
    while len(_PREFS_POSTER_PATCH_CACHE) >= _PREFS_POSTER_PATCH_CACHE_MAX:
        _PREFS_POSTER_PATCH_CACHE.pop(next(iter(_PREFS_POSTER_PATCH_CACHE)))
    _PREFS_POSTER_PATCH_CACHE[cache_key] = patch
    return patch, x, y


def _draw_preferences_posters_bgra(
    bgra: np.ndarray,
    state: MainSettingsState,
    *,
    svg_path: Path,
) -> None:
    """Cover-fit poster art into each assigned zone frame with a rounded mask.

    Live playback uses the active TMDb / music artwork; idle keeps SVG demos.
    """
    from pigeon.widgets.view_circles import _paste_patch_bgra

    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    live = bool(getattr(state, "preferences_live_content", False))
    live_src = getattr(state, "preferences_poster_bgra", None)
    if live_src is not None and not isinstance(live_src, np.ndarray):
        live_src = None
    masters = None if live else _prefs_poster_masters(svg_path)

    for zone in _PREFS_POSTER_FRAMES:
        if assignments[zone - 1] != "poster":
            continue
        if live:
            src = live_src
        else:
            src = masters.get(zone) if masters else None
        if src is None or getattr(src, "size", 0) == 0:
            continue
        patch, x, y = _prefs_poster_patch_for_zone(zone, src, svg_path=svg_path)
        _paste_patch_bgra(bgra, patch, x, y)


def _hide_preferences_live_demo_texts(root: ET.Element) -> None:
    """Drop SVG demo cast / zone5 readout so live Pillow overlays are clean."""
    from pigeon.widgets.view_circles import _layer_key

    pending: list[ET.Element] = []
    for el in root.iter():
        key = _layer_key(el)
        eid = (el.get("id") or "").replace("_x5F_", "_")
        blob = f"{key} {eid}".lower()
        if any(
            s in blob
            for s in (
                "actor",
                "character",
                "zone5_now_playing_elapsed_text",
                "zone5_now_playing_remaining_text",
                "zone5_now_playing_service_text",
                "zone5_now_playing_paused_text",
                "zone5_now_playing_elapsed_icon",
                "zone5_now_playing_remaining_icon",
                "zone5_now_playing_cti_icon",
            )
        ):
            pending.append(el)
    for el in pending:
        _set_visible(el, False)


def _draw_preferences_cast_bgra(
    bgra: np.ndarray,
    state: MainSettingsState,
) -> None:
    """Redraw cast columns (Digital-7) for zone4 / zone5 cast_info."""
    from pigeon.widgets import view_circles as vc

    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    live = bool(getattr(state, "preferences_live_content", False))
    if live:
        all_cast = list(getattr(state, "preferences_cast", None) or ())
        named = sum(1 for actor, _role in all_cast if str(actor or "").strip())
        assignments = vc._effective_zone_widgets(
            has_position=getattr(state, "preferences_np_progress", None) is not None,
            cast_count=named,
            zone_widgets=assignments,
        )
    else:
        all_cast = list(_PREFS_DEMO_CAST)
    font_actor = vc._load_digital7(18)
    font_char = vc._load_digital7(14)
    start = 0
    for cast_zone in (1, 2, 3, 4, 5):
        if assignments[cast_zone - 1] != "cast_info":
            continue
        slice_cast = list(all_cast[start : start + 3])
        start += 3
        if cast_zone not in (4, 5):
            continue
        while len(slice_cast) < 3:
            slice_cast.append(("", ""))
        cols = _PREFS_CAST_COLS_Z5 if cast_zone == 5 else _PREFS_CAST_COLS_Z4
        for i, (center_x, actor_y, char_y) in enumerate(cols):
            actor, character = slice_cast[i] if i < len(slice_cast) else ("", "")
            actor = str(actor or "").strip()
            character = str(character or "").strip()
            max_w = int(
                max(
                    60,
                    min(
                        float(_PREFS_CAST_COL_W),
                        2.0 * min(float(center_x), float(DESIGN_W) - float(center_x)) - 8.0,
                    ),
                )
            )
            if actor:
                label = actor.upper()
                ap, _aw, _ah = vc._text_patch_digital7(
                    label,
                    size_px=18,
                    max_width_px=max_w,
                )
                vc._paste_baseline_centered(
                    bgra,
                    ap,
                    center_x,
                    float(actor_y),
                    bbox_top=vc._font_bbox_top(label, font_actor),
                    pad=2,
                )
            if character:
                label = character.upper()
                cp, _cw, _ch = vc._text_patch_digital7(
                    label,
                    size_px=14,
                    max_width_px=max_w,
                )
                vc._paste_baseline_centered(
                    bgra,
                    cp,
                    center_x,
                    float(char_y),
                    bbox_top=vc._font_bbox_top(label, font_char),
                    pad=2,
                )


def _draw_preferences_status_bar_bgra(
    bgra: np.ndarray,
    state: MainSettingsState,
) -> None:
    """Live zone5 progress bar + timecodes when content is playing."""
    from pigeon.widgets import view_circles as vc

    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    if getattr(state, "preferences_np_progress", None) is None:
        return
    if assignments[4] != "now_playing":
        return
    if not getattr(state, "preferences_live_content", False):
        return
    pf = _preferences_now_playing_progress(state)
    # When progress is unknown (LIVE / no duration), still show chrome at min width.
    bar_w = _PREFS_BAR_R - _PREFS_BAR_L
    if getattr(state, "preferences_np_progress", None) is None:
        elapsed_w = 4
    else:
        elapsed_w = max(4, int(round(pf * float(bar_w))))
    th = vc.np_theme_from_settings()
    vc._draw_rounded_bar_bgra(
        bgra,
        x=_PREFS_BAR_L,
        y=_PREFS_BAR_T,
        w=bar_w,
        h=_PREFS_BAR_H,
        fill_bgr=vc._COLOR_UNPLAYED_BGR,
        radius=_PREFS_BAR_RX,
        stroke_bgr=vc._COLOR_CHROME_BGR,
        stroke=2,
        fill_opacity=vc._CHROME_FILL_OPACITY,
        stroke_opacity=vc._ACCENT_STROKE_OPACITY,
    )
    if elapsed_w > 0:
        vc._draw_rounded_bar_bgra(
            bgra,
            x=_PREFS_BAR_L,
            y=_PREFS_BAR_T,
            w=elapsed_w,
            h=_PREFS_BAR_H,
            fill_bgr=th.ui_bgr,
            radius=_PREFS_BAR_RX,
            stroke_bgr=vc._COLOR_CHROME_BGR,
            stroke=2,
            fill_opacity=1.0,
            stroke_opacity=vc._ACCENT_STROKE_OPACITY,
        )
    cti_x = _PREFS_BAR_L + min(elapsed_w, bar_w) - _PREFS_CTI_W // 2
    cti_x = max(_PREFS_BAR_L, min(_PREFS_BAR_R - _PREFS_CTI_W, cti_x))
    cti = np.zeros((_PREFS_BAR_H, _PREFS_CTI_W, 4), dtype=np.uint8)
    cti[:, :, :3] = th.ui_bgr
    cti[:, :, 3] = 255
    vc._paste_patch_bgra(bgra, cti, int(cti_x), _PREFS_BAR_T)

    et = str(getattr(state, "preferences_elapsed_text", None) or "").strip()
    rt = str(getattr(state, "preferences_remaining_text", None) or "").strip()
    svc = str(getattr(state, "preferences_service_name", None) or "").strip()
    if rt:
        rt_p, rt_w, rt_h = vc._text_patch_digital7(rt, size_px=16)
        vc._paste_patch_bgra(
            bgra,
            rt_p,
            _PREFS_BAR_R - rt_w,
            _PREFS_BAR_TEXT_Y - rt_h // 2,
        )
    if et:
        et_p, et_w, et_h = vc._text_patch_digital7(et, size_px=16)
        et_x = int(round(cti_x + _PREFS_CTI_W / 2.0 - et_w / 2.0))
        vc._paste_patch_bgra(
            bgra, et_p, et_x, _PREFS_BAR_TEXT_Y - et_h // 2
        )
    if svc:
        svc_p, _sw, sh = vc._text_patch_digital7(svc.lower(), size_px=16)
        vc._paste_patch_bgra(
            bgra, svc_p, _PREFS_BAR_L, _PREFS_BAR_TEXT_Y - sh // 2
        )


def _draw_preferences_track_titles_bgra(
    bgra: np.ndarray,
    state: MainSettingsState,
) -> None:
    """Music titles under a poster zone when live music content is active."""
    from pigeon.widgets import view_circles as vc

    if not getattr(state, "preferences_live_content", False):
        return
    if str(getattr(state, "preferences_content_mode", "") or "") != "music":
        return
    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    poster_zone = next(
        (i + 1 for i, w in enumerate(assignments) if w == "poster"),
        None,
    )
    if poster_zone is None or poster_zone not in _PREFS_POSTER_FRAMES:
        return
    fx, fy, fw, fh, _frx = _PREFS_POSTER_FRAMES[poster_zone]
    vb_x, vb_y, vb_w, vb_h = _PREFS_VIEWBOX
    sx = DESIGN_W / max(vb_w, 1.0)
    sy = DESIGN_H / max(vb_h, 1.0)
    cx = (fx - vb_x + fw * 0.5) * sx
    base_y = (fy - vb_y + fh) * sy + 10.0
    rows = (
        (str(getattr(state, "preferences_song_title", None) or ""), 18),
        (str(getattr(state, "preferences_album_title", None) or ""), 14),
        (str(getattr(state, "preferences_artist_title", None) or ""), 14),
    )
    y = base_y
    for text, size in rows:
        label = text.strip()
        if not label:
            continue
        patch, _, th = vc._text_patch_digital7(
            label.upper(),
            size_px=size,
            max_width_px=int(round(fw * sx)),
        )
        vc._paste_centered(bgra, patch, cx, y + th * 0.5)
        y += th + 4.0


def _prefs_volume_group(root: ET.Element, zone: int) -> ET.Element | None:
    from pigeon.widgets.view_circles import _find_by_key

    return _find_by_key(root, f"zone{zone}_volume_group")


def _prefs_find_in_group(
    group: ET.Element | None,
    *,
    key_endswith: str,
) -> ET.Element | None:
    """Find a descendant by layer-key suffix (Illustrator often reuses zone1 ids)."""
    if group is None:
        return None
    from pigeon.widgets.view_circles import _layer_key

    want = key_endswith
    for el in group.iter():
        key = _layer_key(el)
        if key == want or key.endswith(want):
            return el
    return None


def _prefs_volume_center(root: ET.Element, zone: int) -> tuple[float, float] | None:
    group = _prefs_volume_group(root, zone)
    for suffix in (
        "volume_container",
        "volume_selected_button",
        "volume_deselected_buton",
    ):
        el = _prefs_find_in_group(group, key_endswith=suffix)
        if el is None:
            continue
        try:
            return float(el.get("cx") or 0.0), float(el.get("cy") or 0.0)
        except ValueError:
            continue
    return None


def _center_digital_text(
    text_el: ET.Element | None,
    *,
    cx: float,
    cy: float,
    label: str,
) -> None:
    if text_el is None:
        return
    # Prefer the nested <text> if this is a group wrapper.
    target = text_el
    if not text_el.tag.endswith("text"):
        nested = next((n for n in text_el.iter() if n.tag.endswith("text")), None)
        if nested is not None:
            target = nested
    target.set("transform", f"translate({cx:.4f} {cy:.4f})")
    target.set("text-anchor", "middle")
    target.set("dominant-baseline", "middle")
    _set_text_content(target, label)


def _apply_preferences_zone_dynamics(
    root: ET.Element,
    assignments: tuple[str, str, str, str, str],
    *,
    now: datetime | None = None,
) -> None:
    """Drive clock ticks / digital time; adapt volume vs circular now-playing.

    When nothing is playing the SVG keeps its static examples (volume level,
    cast, poster, zone5 bar). Clock always follows wall time.
    """
    from pigeon.widgets.view_circles import (
        _apply_clock_accent_fills,
        _apply_clock_ticks,
        _find_by_key,
    )

    dt = now if now is not None else datetime.now()
    _apply_clock_accent_fills(root)

    for zone in (1, 2, 3):
        widget = assignments[zone - 1]
        if widget == "clock":
            # Tick wedges bake into the structure bitmap; HH:MM is Pillow-overlaid
            # each second so the full SVG path need not re-run for wall clock.
            _apply_clock_ticks(root, zone, dt)
            digital = _find_by_key(root, f"zone{zone}_clock_digital_text")
            if digital is not None:
                _set_visible(digital, False)
            continue

        if widget not in ("volume", "now_playing"):
            continue

        vol_group = _prefs_volume_group(root, zone)
        center = _prefs_volume_center(root, zone)
        vol_text = _prefs_find_in_group(vol_group, key_endswith="volume_text")
        cfg_text = _prefs_find_in_group(
            vol_group, key_endswith="voume_audio_config_text"
        ) or _prefs_find_in_group(vol_group, key_endswith="audio_config_text")
        if widget == "now_playing":
            # Circular now-playing: pie + time are redrawn after rasterize.
            if cfg_text is not None:
                _set_visible(cfg_text, False)
            if vol_text is not None:
                _set_visible(vol_text, False)
            for suffix in (
                "volume_selected_button",
                "volume_deselected_buton",
                "volume_container",
            ):
                el = _prefs_find_in_group(vol_group, key_endswith=suffix)
                if el is not None:
                    _set_visible(el, False)
            continue

        # Volume widget — pie + readout are redrawn after rasterize (same as
        # circular now_playing). Hide static full-disc SVG chrome / demo text.
        if cfg_text is not None:
            _set_visible(cfg_text, False)
        if vol_text is not None:
            _set_visible(vol_text, False)
        for suffix in (
            "volume_selected_button",
            "volume_deselected_buton",
            "volume_container",
        ):
            el = _prefs_find_in_group(vol_group, key_endswith=suffix)
            if el is not None:
                _set_visible(el, False)


def _widget_preview_keys(zone: int, widget: str) -> tuple[str, ...]:
    """Layer keys under the preferences SVG that belong to ``widget`` in ``zone``."""
    z = int(zone)
    w = str(widget)
    if z == 4:
        return ("zone4_cast_info_group",) if w == "cast_info" else ()
    if z == 5:
        # Cast sits beside the bar under a shared outer ``zone5_now_playing_group``.
        # Toggle the inner bar / cast kids — not the outer container.
        if w == "now_playing":
            return ("zone5_now_playing_bar", "zone5_now_playing_group")
        if w == "cast_info":
            return ("zone5_cast_info_group", "zone5_cast_group")
        return ()
    if w == "audio_levels":
        return (f"zone{z}_audio_levels_group",)
    if w == "clock":
        return (f"zone{z}_clock_group",)
    if w == "volume":
        return (f"zone{z}_volume_group",)
    if w == "now_playing":
        # Zones 1–3 reuse the volume ring chrome for playback progress.
        return (f"zone{z}_volume_group",)
    if w == "cast_info":
        return (f"zone{z}_cast_info_group", f"zone{z}_cast_group")
    if w == "poster":
        return (
            f"zone{z}_poster_group",
            f"zone{z}_poster_art_group",
            f"zone{z}_poster_2x3",
            f"zone{z}_2x3_poster_group",
            f"zone{z}_album_art_1x1",
        )
    return ()


def _zone5_container_and_kids(
    root: ET.Element,
) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    """Return ``(outer, bar_group, cast_group)`` for zone5 preview layers.

    Current Illustrator export nests cast under the outer now-playing group::

        zone5_now_playing_group
          zone5_cast_info_group
          zone5_now_playing_group   (inner bar / CTI)
    """
    outer = _find_by_logical_id(root, "zone5_now_playing_group")
    if outer is None:
        return None, None, None
    bar = cast = None
    for child in list(outer):
        key = _layer_key(child)
        if key in ("zone5_cast_info_group", "zone5_cast_group"):
            cast = child
        elif "now_playing" in key:
            bar = child
    # Flat export fallback (siblings, not nested).
    if cast is None:
        cast = _find_by_logical_id(root, "zone5_cast_info_group") or _find_by_logical_id(
            root, "zone5_cast_group"
        )
    return outer, bar, cast


def _iter_zone_widget_layers(root: ET.Element, zone: int):
    """Yield direct widget layers for a zone (preferences preview)."""
    z = int(zone)
    if z in (1, 2, 3):
        parent = _find_by_logical_id(root, f"zone{z}_group")
        if parent is None:
            return
        for child in list(parent):
            yield child
        return
    if z == 4:
        el = _find_by_logical_id(root, "zone4_cast_info_group")
        if el is not None:
            yield el
        return
    if z == 5:
        _outer, bar, cast = _zone5_container_and_kids(root)
        if bar is not None:
            yield bar
        if cast is not None:
            yield cast


def _apply_zone0_date_preview(
    root: ET.Element,
    _assignments: tuple[str, str, str, str, str],
) -> None:
    """Hide zone0 date chrome on preferences (align logic still drives now-playing)."""
    header = _find_by_logical_id(root, "zone0_header_group")
    _set_visible(header, False)
    for name in ("left", "center", "right"):
        _set_visible(_find_by_logical_id(root, f"zone0_date_{name}_text"), False)


def _apply_zone_preview(root: ET.Element, zone: int, widget: str) -> None:
    z = int(zone)
    w = str(widget)
    if z == 5:
        outer, bar, cast = _zone5_container_and_kids(root)
        # Keep the outer wrapper visible so nested cast/bar can show.
        if outer is not None:
            _set_visible(outer, True)
        if bar is not None:
            # Inner bar group often reuses the now_playing name — show only for bar widget.
            _set_visible(bar, w == "now_playing")
        if cast is not None:
            _set_visible(cast, w == "cast_info")
        return

    want = set(_widget_preview_keys(z, w))
    for el in _iter_zone_widget_layers(root, z):
        key = _layer_key(el)
        show = key in want or any(key.startswith(prefix) for prefix in want)
        # Nested poster groups: show parent if any child key matches.
        if not show and "poster" in key and any("poster" in prefix for prefix in want):
            show = True
        _set_visible(el, show)


def _selector_group_for_widget(root: ET.Element, widget: str) -> ET.Element | None:
    names = {
        "audio_levels": "selector_audio_levels_group",
        "clock": "selector_clock_group",
        "poster": "selector_poster_art_group",
        "volume": "selector_volume_group",
        "now_playing": "selector_now_playing_group",
        "cast_info": "selector_cast_info_group",
        "exit": "selector_exit_group",
    }
    name = names.get(widget)
    if not name:
        return None
    return _find_by_logical_id(root, name)


def apply_preferences_svg_state(root: ET.Element, state: MainSettingsState) -> None:
    nav = str(getattr(state, "preferences_nav", "zones") or "zones")
    active_zone = int(getattr(state, "preferences_active_zone", 0) or 0)
    assignments = _normalize_zone_widgets(
        getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
    )
    live = bool(getattr(state, "preferences_live_content", False))
    if live:
        all_cast = list(getattr(state, "preferences_cast", None) or ())
        named = sum(1 for actor, _role in all_cast if str(actor or "").strip())
        from pigeon.widgets.view_circles import _effective_zone_widgets

        assignments = _effective_zone_widgets(
            has_position=getattr(state, "preferences_np_progress", None) is not None,
            cast_count=named,
            zone_widgets=assignments,
        )
    focused = str(getattr(state, "preferences_focused_id", "") or "")

    # Zone preview layers follow current assignments (live while browsing widgets).
    for zone in (1, 2, 3, 4, 5):
        _apply_zone_preview(root, zone, assignments[zone - 1])

    # Zone0 date: left / center / right / off from poster occupancy.
    _apply_zone0_date_preview(root, assignments)

    # Zone selector chrome.
    for zone in (1, 2, 3, 4, 5):
        group = _find_by_logical_id(root, f"selector_zone{zone}_group")
        if nav == "widgets" and active_zone == zone:
            selected = True  # locked selected while editing widgets
        elif nav == "zones":
            selected = focused == f"zone{zone}"
        else:
            selected = False
        _apply_zone_chrome(group, selected=selected)

    # Live clock ticks / digital time; volume keeps static example when idle.
    _apply_preferences_zone_dynamics(root, assignments)

    live = bool(getattr(state, "preferences_live_content", False))
    if live:
        # Cast + zone5 bar/text are redrawn after rasterize from live state.
        _hide_preferences_live_demo_texts(root)
    else:
        # Zone5 demo elapsed fill follows settings UI color.
        try:
            from pigeon.widgets.view_circles import _set_tick_paint, np_theme_from_settings

            elapsed = _find_by_logical_id(root, "zone5_now_playing_elapsed_icon")
            if elapsed is None:
                from pigeon.widgets.view_circles import _find_by_key

                elapsed = _find_by_key(root, "zone5_now_playing_elapsed_icon")
            if elapsed is not None:
                _set_tick_paint(elapsed, color=np_theme_from_settings().ui_hex)
        except Exception:
            pass

    # Widget selector chrome + BACK (settings_main EXIT twin).
    _sync_preferences_back_button(root)
    available: set[str] = set()
    if nav == "widgets" and active_zone in ZONE_WIDGET_CATALOG:
        available = set(ZONE_WIDGET_CATALOG[active_zone])

    for wid in _WIDGET_SELECTOR_ORDER:
        group = _selector_group_for_widget(root, wid)
        if nav == "zones":
            # Resting available look while choosing a zone.
            _apply_widget_chrome(group, selected=False, available=True)
        else:
            _apply_widget_chrome(
                group,
                selected=(focused == wid),
                available=(wid in available),
            )

    exit_group = _selector_group_for_widget(root, "exit")
    exit_selected = focused == "exit"
    _apply_widget_chrome(
        exit_group,
        selected=exit_selected,
        available=True,
        idle_fill=_COLOR_EXIT_FILL,
    )

    # Color control lives in zone navigation (zone5 → color → BACK).
    color_selected = nav == "zones" and focused == "color"
    _apply_preferences_color_chrome(root, selected=color_selected)

    # Labels / numerals / posters are Pillow-composited after rasterize
    # (SVG text metrics skew; PyMuPDF drops rounded clip-path on poster images).
    _clear_preferences_selector_label_texts(root)
    _clear_preferences_zone_number_texts(root)
    _hide_preferences_poster_images(root)
    _hide_preferences_color_button_images(root)


def _prefs_structure_cache_key(
    state: MainSettingsState,
    *,
    path: Path,
    assets_dir: Path | str | None,
) -> tuple[object, ...]:
    try:
        st_stat = path.stat()
        path_key = (str(path.resolve()), int(st_stat.st_mtime_ns), int(st_stat.st_size))
    except OSError:
        path_key = (str(path), 0, 0)
    th = state.theme
    return (
        path_key,
        str(assets_dir or ""),
        str(th.ui),
        str(th.selected),
        str(th.deselected),
        str(th.accent),
        str(getattr(state, "preferences_nav", "zones") or "zones"),
        int(getattr(state, "preferences_focus_index", 0) or 0),
        int(getattr(state, "preferences_active_zone", 0) or 0),
        tuple(
            _normalize_zone_widgets(
                getattr(state, "preferences_zone_widgets", None) or DEFAULT_ZONE_WIDGETS
            )
        ),
        # Minute bucket: second wedges refresh at most once/min without full nav.
        datetime.now().strftime("%H%M"),
        1 if getattr(state, "preferences_live_content", False) else 0,
        3,  # structure schema — live content hides cast/zone5 demos
    )


def _prefs_dimmed_theme_bgra(
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None,
    path: Path,
) -> np.ndarray:
    """Cached theme plate + 80% black scrim (full-frame partial alpha is costly on Pi)."""
    ui_hex = str(state.theme.ui)
    adir = str(assets_dir if assets_dir is not None else path.parent.parent)
    key = (ui_hex, adir, int(_PREFS_BACKGROUND_DIM_ALPHA), int(DESIGN_W), int(DESIGN_H))
    cached = _PREFS_DIMMED_BG_CACHE.get(key)
    if cached is not None:
        return cached
    bg_bgra = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    bg_bgra[:, :, 3] = 255
    _draw_container_background_bgra(
        bg_bgra,
        ui_hex=ui_hex,
        assets_dir=adir,
    )
    # Uniform black scrim: scale RGB in-place (avoids float32 composite of 800×480).
    a = float(_PREFS_BACKGROUND_DIM_ALPHA) / 255.0
    keep = 1.0 - a
    dimmed = bg_bgra.copy()
    dimmed[:, :, :3] = np.clip(
        dimmed[:, :, :3].astype(np.float32) * keep, 0, 255
    ).astype(np.uint8)
    while len(_PREFS_DIMMED_BG_CACHE) >= _PREFS_DIMMED_BG_CACHE_MAX:
        _PREFS_DIMMED_BG_CACHE.pop(next(iter(_PREFS_DIMMED_BG_CACHE)))
    _PREFS_DIMMED_BG_CACHE[key] = dimmed
    return dimmed


def _render_preferences_structure_bgra(
    state: MainSettingsState,
    *,
    path: Path,
    assets_dir: Path | str | None,
) -> tuple[np.ndarray, dict[str, object]]:
    """Full SVG raster + labels/chrome; posters/NP overlays drawn each frame."""
    root = _svg_tree_from_path(path)
    apply_preferences_svg_state(root, state)
    np_centers = _collect_preferences_np_centers(root, state)
    volume_centers = _collect_preferences_volume_centers(root, state)
    clock_centers = _collect_preferences_clock_centers(root, state)
    _disable_embedded_settings_background_layers(root)
    _prune_display_none(root)
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    ui_bgra = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        font_mode="preferences",
    )
    # Idle demo posters bake into the structure; live artwork is overlaid later.
    if not getattr(state, "preferences_live_content", False):
        _draw_preferences_posters_bgra(ui_bgra, state, svg_path=path)
    _draw_preferences_color_button_bgra(ui_bgra, state, svg_path=path)
    _draw_preferences_selector_labels_bgra(ui_bgra, root, state)
    _draw_preferences_zone_numbers_bgra(ui_bgra, root, state)
    dimmed_bg = _prefs_dimmed_theme_bgra(state, assets_dir=assets_dir, path=path)
    base = _composite_bgra_over_bgra(dimmed_bg, ui_bgra)
    meta: dict[str, object] = {
        "np_centers": np_centers,
        "volume_centers": volume_centers,
        "clock_centers": clock_centers,
        "svg_path": path,
    }
    return base, meta


def render_preferences_settings_bgra(
    state: MainSettingsState | None = None,
    *,
    svg_path: Path | str | None = None,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    if svg_path is not None:
        path = Path(svg_path)
    else:
        path = default_preferences_svg_path(assets_dir)
    if not path.is_file():
        raise FileNotFoundError(f"preferences SVG not found: {path}")

    st = state if state is not None else MainSettingsState()
    key = _prefs_structure_cache_key(st, path=path, assets_dir=assets_dir)
    cached = _PREFS_STRUCTURE_CACHE.get(key)
    if cached is None:
        base, meta = _render_preferences_structure_bgra(
            st, path=path, assets_dir=assets_dir
        )
        if len(_PREFS_STRUCTURE_CACHE) >= _PREFS_STRUCTURE_CACHE_MAX:
            # Drop oldest insertion.
            oldest = next(iter(_PREFS_STRUCTURE_CACHE))
            _PREFS_STRUCTURE_CACHE.pop(oldest, None)
        _PREFS_STRUCTURE_CACHE[key] = (base, meta)
    else:
        base, meta = cached

    out = base.copy()
    np_centers = meta.get("np_centers") or {}
    volume_centers = meta.get("volume_centers") or {}
    clock_centers = meta.get("clock_centers") or {}
    svg_path_meta = meta.get("svg_path")
    if getattr(st, "preferences_live_content", False) and isinstance(
        svg_path_meta, Path
    ):
        _draw_preferences_posters_bgra(out, st, svg_path=svg_path_meta)
    if isinstance(np_centers, dict) and np_centers:
        _draw_preferences_circular_now_playing_bgra(out, np_centers, st)
    if isinstance(volume_centers, dict) and volume_centers:
        _draw_preferences_volume_bgra(out, volume_centers, st)
    if isinstance(clock_centers, dict) and clock_centers:
        _draw_preferences_clock_digitals_bgra(out, clock_centers)
    if getattr(st, "preferences_live_content", False):
        _draw_preferences_cast_bgra(out, st)
        _draw_preferences_status_bar_bgra(out, st)
        _draw_preferences_track_titles_bgra(out, st)
    return out


def clear_preferences_render_caches() -> None:
    """Drop prefs structure / plate caches (theme asset reload, tests)."""
    _PREFS_STRUCTURE_CACHE.clear()
    _PREFS_DIMMED_BG_CACHE.clear()
    _PREFS_POSTER_PATCH_CACHE.clear()
    _COLOR_BTN_IMAGE_CACHE.clear()
    _sharp_text_patch.cache_clear()


__all__ = [
    "DEFAULT_ZONE_WIDGETS",
    "ZONE_WIDGET_CATALOG",
    "apply_preferences_svg_state",
    "default_preferences_svg_path",
    "preferences_widget_focus_ring",
    "preferences_zone_focus_ring",
    "read_now_playing_zone_widgets",
    "render_preferences_settings_bgra",
    "write_now_playing_zone_widgets",
    "zone0_date_align",
]
