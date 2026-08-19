"""
Pigeon 0.8 settings keyboards — SVG overlays shared by main_settings text entry.

Layouts (``pigeonAssets/settings_0.8/``):
  - keyboard_qwerty_lower / keyboard_qwerty_upper
  - keyboard_symbolic
  - keyboard_numeric_all  (ids: keyboard_numeric_full_*)
  - keyboard_numeric_pin
  - keyboard_bottom_row   (shared ABC / abc / sym / space / delete / cancel / go)

Navigation is linear Left/Right. Physical Spacebar activates the focused key.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

from pigeon.compositing import alpha_blend_bgra_over_bgr
from pigeon.widgets.main_settings import (
    DESIGN_H,
    DESIGN_W,
    SettingsTheme,
    _BUTTON_FILL_CANDIDATES,
    _apply_button_fill,
    _apply_contrast_paint,
    _find_by_logical_id,
    _normalize_logical,
    _parent_map,
    _prune_display_none,
    _rewrite_style_prop,
    _set_paint,
    _set_text_content,
    _set_visible,
    keyboard_svg_path,
)
from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra, viewbox_from_root

# Illustrator keyboard buttons ship as near-black, not theme deselected.
_KB_BUTTON_EXTRA = frozenset({"#231f20", "#231f20ff"})
_KB_STROKE_WIDTH = "3"


class KeyboardMode(str, Enum):
    QWERTY_LOWER = "qwerty_lower"
    QWERTY_UPPER = "qwerty_upper"
    SYMBOLIC = "symbolic"
    NUMERIC_ALL = "numeric_all"
    NUMERIC_PIN = "numeric_pin"
    NUMERIC_IP = "numeric_ip"
    YES_NO = "yes_no"


class KeyAction(str, Enum):
    CHAR = "char"
    SPACE = "space"
    DELETE = "delete"
    SHIFT = "shift"
    MODE_ABC = "mode_abc"  # uppercase
    MODE_ABC_LOWER = "mode_abc_lower"
    MODE_SYM = "mode_sym"
    MODE_123 = "mode_123"
    CANCEL = "cancel"
    GO = "go"
    YES = "yes"
    NO = "no"


@dataclass(frozen=True)
class KeySpec:
    """One focusable key: button layer + optional contrast icon layers."""

    button_id: str
    action: KeyAction
    char: str = ""
    icon_ids: tuple[str, ...] = ()


_MODE_SVG: dict[KeyboardMode, str] = {
    KeyboardMode.QWERTY_LOWER: "keyboard_qwerty_lower.svg",
    KeyboardMode.QWERTY_UPPER: "keyboard_qwerty_upper.svg",
    KeyboardMode.SYMBOLIC: "keyboard_symbolic.svg",
    KeyboardMode.NUMERIC_ALL: "keyboard_numeric_all.svg",
    KeyboardMode.NUMERIC_PIN: "keyboard_numeric_pin.svg",
    KeyboardMode.NUMERIC_IP: "keyboard_numeric_ip.svg",
    KeyboardMode.YES_NO: "keyboard_yes_no.svg",
}

_BOTTOM_ROW_SVG = "keyboard_bottom_row.svg"

# Bottom-row strip on the 800×480 artboard.
_BOTTOM_ROW_X = 37  # (800 - 725) / 2
_BOTTOM_ROW_Y = 378

# Self-contained PIN pad (compact crop). IP uses full 800×480 artboard placement.
_INTEGRATED_PAD_TOP_Y = 225

# Linear focus order — mode keys differ for uppercase-only fields (Digital-7).
_BOTTOM_ROW_TAIL: tuple[KeySpec, ...] = (
    KeySpec(
        "keyboard_qwerty_space_button",
        KeyAction.SPACE,
        char=" ",
    ),
    KeySpec(
        "keyboard_bottom_row_delete_button",
        KeyAction.DELETE,
        icon_ids=("keyboard_qwerty_upper_delete_icon",),
    ),
    KeySpec(
        "keyboard_qwerty_cancel_button",
        KeyAction.CANCEL,
        icon_ids=("keyboard_qwerty_cancel_icon",),
    ),
    KeySpec(
        "keyboard_qwerty_go_button",
        KeyAction.GO,
        icon_ids=("keyboard_qwerty_go_icon",),
    ),
)

_BOTTOM_ROW_NETWORK: tuple[KeySpec, ...] = (
    KeySpec(
        "keyboard_bottom_row_button1_buton",
        KeyAction.MODE_ABC,
        icon_ids=("keyboard_bottom_row_button1_ABC_icon-2",),
    ),
    KeySpec(
        "keyboard_bottom_row_button2_button",
        KeyAction.MODE_123,
        icon_ids=("keyboard_bottom_row_button2_abc",),
    ),
    KeySpec(
        "keyboard_bottom_row_button3_button",
        KeyAction.MODE_SYM,
        icon_ids=("keyboard_bottom_row_button3_sym_icon",),
    ),
) + _BOTTOM_ROW_TAIL

# Back-compat alias used in tests / exports.
_BOTTOM_ROW_KEYS = _BOTTOM_ROW_NETWORK

# Uppercase-only: no button1; button2 → numeric, button3 → symbolic.
_BOTTOM_ROW_UPPERCASE: tuple[KeySpec, ...] = (
    KeySpec(
        "keyboard_bottom_row_button2_button",
        KeyAction.MODE_123,
        icon_ids=("keyboard_bottom_row_button2_abc",),
    ),
    KeySpec(
        "keyboard_bottom_row_button3_button",
        KeyAction.MODE_SYM,
        icon_ids=("keyboard_bottom_row_button3_sym_icon",),
    ),
) + _BOTTOM_ROW_TAIL


@dataclass
class KeyboardState:
    """Live text-entry keyboard overlay state."""

    mode: KeyboardMode = KeyboardMode.QWERTY_LOWER
    focus_index: int = 0
    buffer: str = ""
    initial_text: str = ""
    target: str = ""  # e.g. "location", "network", "pin"
    theme: SettingsTheme = field(default_factory=SettingsTheme)
    supports_lowercase: bool = True
    password_mask: bool = False
    # Cached focus ring for the active mode (char keys + bottom row).
    focus_ring: tuple[KeySpec, ...] = field(default_factory=tuple)
    include_bottom_row: bool = True

    def rebuild_focus_ring(self, *, assets_dir: Path | str | None = None) -> None:
        if self.mode == KeyboardMode.YES_NO:
            self.focus_ring = discover_yes_no_keys(assets_dir=assets_dir)
            self.include_bottom_row = False
        elif self.mode == KeyboardMode.NUMERIC_IP:
            self.focus_ring = discover_integrated_pad_keys(
                KeyboardMode.NUMERIC_IP, assets_dir=assets_dir
            )
            self.include_bottom_row = False
        elif self.mode == KeyboardMode.NUMERIC_PIN:
            self.focus_ring = discover_integrated_pad_keys(self.mode, assets_dir=assets_dir)
            self.include_bottom_row = False
        else:
            char_keys = discover_char_keys(self.mode, assets_dir=assets_dir)
            # Digital-7 uppercase-only fields drop Shift; Wi‑Fi password keeps it.
            if (
                self.mode == KeyboardMode.QWERTY_UPPER
                and not self.supports_lowercase
                and not self.password_mask
            ):
                char_keys = tuple(k for k in char_keys if k.action != KeyAction.SHIFT)
            bottom = _BOTTOM_ROW_NETWORK if self.supports_lowercase else _BOTTOM_ROW_UPPERCASE
            if self.include_bottom_row:
                self.focus_ring = tuple(char_keys) + bottom
            else:
                self.focus_ring = tuple(char_keys)
        if not self.focus_ring:
            fallback = _BOTTOM_ROW_NETWORK if self.supports_lowercase else _BOTTOM_ROW_UPPERCASE
            self.focus_ring = fallback
        self.focus_index = int(self.focus_index) % len(self.focus_ring)

    @property
    def focused(self) -> KeySpec:
        if not self.focus_ring:
            self.rebuild_focus_ring()
        return self.focus_ring[int(self.focus_index) % len(self.focus_ring)]

    def navigate(self, *, forward: bool = True) -> None:
        if not self.focus_ring:
            self.rebuild_focus_ring()
        n = len(self.focus_ring)
        step = 1 if forward else -1
        self.focus_index = (int(self.focus_index) + step) % n

    def set_mode(self, mode: KeyboardMode, *, assets_dir: Path | str | None = None) -> None:
        if mode == self.mode:
            return
        prev_action: KeyAction | None = None
        prev_button = ""
        prev_char = ""
        if self.focus_ring:
            prev = self.focused
            prev_action = prev.action
            prev_button = prev.button_id
            prev_char = prev.char
        self.mode = mode
        self.rebuild_focus_ring(assets_dir=assets_dir)
        if prev_action is not None:
            for i, key in enumerate(self.focus_ring):
                if key.action != prev_action:
                    continue
                if prev_action == KeyAction.CHAR and key.char != prev_char:
                    continue
                self.focus_index = i
                return
            if prev_button:
                for i, key in enumerate(self.focus_ring):
                    if key.button_id == prev_button:
                        self.focus_index = i
                        return
        if mode in (KeyboardMode.NUMERIC_ALL, KeyboardMode.NUMERIC_PIN):
            focus_numeric_one(self, assets_dir=assets_dir)


def _button_xy(el: ET.Element) -> tuple[float, float]:
    """Sort key: top→bottom, left→right."""
    x = el.get("x")
    y = el.get("y")
    if x is not None and y is not None:
        try:
            return float(x), float(y)
        except ValueError:
            pass
    d = el.get("d") or ""
    m = re.search(r"[Mm]\s*([-\d.]+)[,\s]+([-\d.]+)", d)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass
    return (0.0, 0.0)


def _pair_icon_id(button_id: str) -> str:
    """Best-effort button → icon id (handles known typos in exports)."""
    if button_id == "keyboard_numeric_full__button":
        return "keyboard_numeric_full_1_icon"
    if button_id == "keyboard_numeric_full_0_button":
        return "keyboard_numeric_full__icon"
    if button_id.endswith("_button"):
        base = button_id[: -len("_button")]
        return f"{base}_icon"
    if button_id.endswith("_buton"):  # bottom-row typo
        return button_id.replace("_buton", "_icon")
    return button_id + "_icon"


def discover_char_keys(
    mode: KeyboardMode,
    *,
    assets_dir: Path | str | None = None,
) -> list[KeySpec]:
    """Build character-key specs from the mode SVG (sorted by position)."""
    path = keyboard_svg_path(_MODE_SVG[mode], assets_dir=assets_dir)
    if not path.is_file():
        return []
    root = ET.parse(path).getroot()

    # Index icon layers by normalized id for fuzzy pairing.
    icon_nodes: dict[str, ET.Element] = {}
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        if logical.endswith("_icon") or "_icon_" in logical or logical.endswith("_ico") or logical.endswith("_ico_n"):
            icon_nodes[logical] = el

    buttons: list[tuple[float, float, str, ET.Element]] = []
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        is_button = (
            logical.endswith("_button")
            or logical.endswith("_buton")
            or logical.startswith("symbolic_button_")
        )
        if not is_button:
            continue
        # Skip any bottom-row ids if they ever appear inside a char SVG.
        if "bottom_row" in logical or logical.startswith("keyboard_qwerty_space"):
            continue
        if logical.endswith(("_go_button", "_cancel_button", "_delete_button")):
            continue
        x, y = _button_xy(el)
        buttons.append((y, x, logical, el))

    buttons.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    keys: list[KeySpec] = []
    for _y, _x, logical, _el in buttons:
        if "shift" in logical.lower():
            shift_icons = (
                ("keyboard_qwerty_lower_shift_icon",)
                if mode == KeyboardMode.QWERTY_LOWER
                else ("keyboard_qwerty_upper_SHIFT_icon", "keyboard_qwerty_upper_SHIFT_icon-2")
            )
            keys.append(KeySpec(logical, KeyAction.SHIFT, icon_ids=shift_icons))
            continue

        # Symbolic: symbolic_button_X
        if logical.startswith("symbolic_button_"):
            ch = logical[len("symbolic_button_") :]
            icon_id = f"symbolic_icon_{ch}"
            # Prefer an existing icon node (may carry AI uniqueness suffix).
            resolved = icon_id
            for cand, node in icon_nodes.items():
                if cand == icon_id or cand.startswith(icon_id + "_"):
                    resolved = cand
                    break
            keys.append(KeySpec(logical, KeyAction.CHAR, char=ch, icon_ids=(resolved,)))
            continue

        icon_candidates = [_pair_icon_id(logical)]
        # Export quirks.
        if logical.startswith("_"):
            # e.g. ``_keyboard_qwerty_lower_b_button``
            icon_candidates.insert(0, _pair_icon_id(logical.lstrip("_")))
        if logical == "keyboard_qwerty_q_button":
            icon_candidates = ["keyboard_qwerty_lower_q_icon", "keyboard_qwerty_q_icon"]
        if mode == KeyboardMode.QWERTY_UPPER and logical.endswith("_button"):
            # Icons use uppercase letter: …_Q_icon not …_q_icon
            base = logical[: -len("_button")]
            # keyboard_qwerty_upper_q → keyboard_qwerty_upper_Q_icon
            parts = base.rsplit("_", 1)
            if len(parts) == 2 and len(parts[1]) == 1:
                icon_candidates.insert(0, f"{parts[0]}_{parts[1].upper()}_icon")
        if logical == "keyboard_numeric_full__button":
            icon_candidates = ["keyboard_numeric_full_1_icon"]
        elif logical == "keyboard_numeric_full_0_button":
            icon_candidates = ["keyboard_numeric_full__icon"]
        if logical.endswith("_2_button"):
            icon_candidates.append(logical.replace("_button", "_ico"))
        if logical.endswith("_3_button"):
            icon_candidates.append(logical.replace("_button", "_ico_n"))

        ch = ""
        resolved_icon = icon_candidates[0]
        for cand in icon_candidates:
            # Exact or prefix match against indexed icons.
            hit = icon_nodes.get(cand)
            if hit is None:
                for iid, node in icon_nodes.items():
                    if iid == cand or iid.startswith(cand + "_"):
                        hit = node
                        cand = iid
                        break
            if hit is not None:
                text = "".join(hit.itertext()).strip()
                if text:
                    ch = text
                    resolved_icon = cand
                    break
                # Uppercase icons may be path glyphs with empty text — derive from id.
                m = re.search(r"_([A-Za-z0-9])_icon", cand)
                if m:
                    ch = m.group(1)
                    resolved_icon = cand
                    break

        # Last resort: single letter from button id (…_b_button / …_B_button).
        if not ch:
            m = re.search(r"_([A-Za-z0-9])_button$", logical.lstrip("_"))
            if m:
                ch = m.group(1)

        if not ch and mode in (KeyboardMode.NUMERIC_ALL, KeyboardMode.NUMERIC_PIN):
            m = re.search(r"_(\d)_button$", logical)
            if m:
                ch = m.group(1)
            elif "full__button" in logical:
                ch = "1"
            elif logical.endswith("_0_button"):
                ch = "0"

        if mode == KeyboardMode.QWERTY_UPPER and ch:
            ch = ch.upper()
        elif mode == KeyboardMode.QWERTY_LOWER and ch:
            ch = ch.lower()

        keys.append(
            KeySpec(
                logical,
                KeyAction.CHAR,
                char=ch,
                icon_ids=(resolved_icon,),
            )
        )
    return keys


def discover_yes_no_keys(*, assets_dir: Path | str | None = None) -> tuple[KeySpec, ...]:
    """Build focus ring for the WiFi logout confirmation pad."""
    path = keyboard_svg_path(_MODE_SVG[KeyboardMode.YES_NO], assets_dir=assets_dir)
    if not path.is_file():
        return ()
    root = ET.parse(path).getroot()
    icon_nodes: dict[str, ET.Element] = {}
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        if logical.endswith("_icon"):
            icon_nodes[logical] = el

    buttons: list[tuple[float, float, str]] = []
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        if not logical.endswith("_button"):
            continue
        x, y = _button_xy(el)
        buttons.append((y, x, logical))

    buttons.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    keys: list[KeySpec] = []
    for _y, _x, logical in buttons:
        icon_id = _pair_icon_id(logical)
        icon_ids = tuple(
            cand
            for cand in (icon_id, logical.replace("_button", "_icon"))
            if cand in icon_nodes or any(k.startswith(cand) for k in icon_nodes)
        )
        if logical.endswith("_yes_button") or "_yes_button" in logical:
            keys.append(KeySpec(logical, KeyAction.YES, icon_ids=icon_ids))
        elif logical.endswith("_no_button") or "_no_button" in logical:
            keys.append(KeySpec(logical, KeyAction.NO, icon_ids=icon_ids))
    return tuple(keys)


def discover_integrated_pad_keys(
    mode: KeyboardMode,
    *,
    assets_dir: Path | str | None = None,
) -> tuple[KeySpec, ...]:
    """Build focus ring for self-contained pads (IP / PIN) with no bottom-row SVG."""
    path = keyboard_svg_path(_MODE_SVG[mode], assets_dir=assets_dir)
    if not path.is_file():
        return ()
    root = ET.parse(path).getroot()
    icon_nodes: dict[str, ET.Element] = {}
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        if logical.endswith("_icon"):
            icon_nodes[logical] = el

    buttons: list[tuple[float, float, str]] = []
    for el in root.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        logical = _normalize_logical(raw)
        if not logical.endswith("_button"):
            continue
        x, y = _button_xy(el)
        buttons.append((y, x, logical))

    buttons.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    keys: list[KeySpec] = []
    for _y, _x, logical in buttons:
        icon_id = _pair_icon_id(logical)
        icon_ids = tuple(
            cand
            for cand in (icon_id, logical.replace("_button", "_icon"))
            if cand in icon_nodes or any(k.startswith(cand) for k in icon_nodes)
        )
        if "cancel" in logical:
            keys.append(KeySpec(logical, KeyAction.CANCEL, icon_ids=icon_ids))
        elif "delete" in logical:
            keys.append(KeySpec(logical, KeyAction.DELETE, icon_ids=icon_ids))
        elif logical.endswith("_go_button") or "_go_button" in logical:
            keys.append(KeySpec(logical, KeyAction.GO, icon_ids=icon_ids))
        elif "dot" in logical:
            keys.append(KeySpec(logical, KeyAction.CHAR, char=".", icon_ids=icon_ids))
        else:
            ch = ""
            for part in logical.split("_"):
                if len(part) == 1 and part.isdigit():
                    ch = part
                    break
            keys.append(KeySpec(logical, KeyAction.CHAR, char=ch, icon_ids=icon_ids))
    return tuple(keys)


def _paint_kb_button_shape(
    node: ET.Element,
    *,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    """Flat key fill — accent outlines are turned off on keyboards."""
    fill = theme.selected if selected else theme.deselected
    _set_paint(node, fill=fill, stroke="none")
    style = node.get("style") or ""
    style = _rewrite_style_prop(style, "stroke-width", "0")
    if style:
        node.set("style", style)


def apply_keyboard_selection(
    root: ET.Element,
    *,
    focused_button_id: str,
    theme: SettingsTheme,
    button_ids: set[str],
    icon_ids_by_button: dict[str, tuple[str, ...]] | None = None,
    muted_deselected: bool = False,
) -> None:
    """Recolor every known button; contrast paint on paired icons/text."""
    from pigeon.widgets.main_settings import _iter_style_fill_stroke, _set_paint

    icon_map = icon_ids_by_button or {}
    fill_ok = set(_BUTTON_FILL_CANDIDATES) | _KB_BUTTON_EXTRA | {
        theme.selected.lower(),
        theme.deselected.lower(),
        theme.inactive.lower(),
        "#ffffff",
        "#fff",
    }

    # Parent lookup for sibling icon contrast (PIN / numeric groups).
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    for logical in button_ids:
        selected = logical == focused_button_id
        el = _find_by_logical_id(root, logical)
        if el is None:
            continue
        fill = theme.selected if selected else theme.deselected
        for node in el.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in ("path", "rect", "polygon", "circle", "ellipse"):
                continue
            nid = _normalize_logical(node.get("id") or "")
            if nid.endswith("_accent"):
                continue
            cur_fill, _ = _iter_style_fill_stroke(node)
            if cur_fill in ("none", "transparent"):
                continue
            if cur_fill is None or cur_fill in fill_ok:
                if tag == "rect":
                    try:
                        rx = float(node.get("rx") or 0.0)
                    except (TypeError, ValueError):
                        rx = 0.0
                    if rx >= 8.0:
                        fill = theme.selected if selected else theme.deselected
                        _set_paint(node, fill=fill, stroke="none")
                        continue
                _paint_kb_button_shape(node, selected=selected, theme=theme)
        _apply_button_fill(el, selected=selected, theme=theme)

        icons = list(icon_map.get(logical, ()))
        paired_icon = _pair_icon_id(logical)
        if paired_icon not in icons:
            icons.append(paired_icon)
        seen_icons: set[str] = set()
        for icon_logical in icons:
            if icon_logical in seen_icons:
                continue
            seen_icons.add(icon_logical)
            icon_el = _find_by_logical_id(root, icon_logical)
            if icon_el is not None:
                _apply_contrast_paint(
                    icon_el,
                    selected=selected,
                    theme=theme,
                    muted_deselected=muted_deselected,
                )

        # Grouped layouts (PIN / numeric): icon is a sibling under the same parent.
        expected_icons = seen_icons
        parent = parents.get(el)
        if parent is not None:
            for child in parent:
                cid = _normalize_logical(child.get("id") or "")
                if cid in expected_icons:
                    _apply_contrast_paint(
                        child,
                        selected=selected,
                        theme=theme,
                        muted_deselected=muted_deselected,
                    )


def _bottom_row_icons(root: ET.Element, group_logical: str, *icon_logicals: str) -> list[ET.Element]:
    """Return icon/text nodes under one bottom-row button group."""
    group = _find_by_logical_id(root, group_logical)
    if group is None:
        return []
    want = {_normalize_logical(name) for name in icon_logicals}
    hits: list[ET.Element] = []
    for el in group.iter():
        raw = el.get("id") or ""
        if not raw:
            continue
        if _normalize_logical(raw) in want:
            hits.append(el)
    return hits


# Bottom-row artboard + margin for PyMuPDF stroke rasterization (SVG user units).
_BOTTOM_ROW_CONTENT_VB = (0.0, 0.0, 725.4, 42.15)
# Half of the 3px keyboard stroke plus anti-alias slack.
_BOTTOM_ROW_STROKE_PAD_SVG = float(_KB_STROKE_WIDTH) * 0.5 + 1.5
# Integrated numeric pads use large corner radii (rx≈19); need extra margin for PyMuPDF.
_INTEGRATED_PAD_STROKE_PAD_SVG = 8.0


@dataclass(frozen=True)
class _BottomRowLayout:
    padded_vb: tuple[float, float, float, float]
    out_w: int
    out_h: int
    pad_px: int
    content_w: int
    content_h: int


def _bottom_row_layout() -> _BottomRowLayout:
    """Map content 1:1 to legacy pixels; add transparent margin for uncropped strokes."""
    vb_x, vb_y, vb_w, vb_h = _BOTTOM_ROW_CONTENT_VB
    pad = _BOTTOM_ROW_STROKE_PAD_SVG
    content_w = int(round(725 * (DESIGN_W / 800)))
    content_h = max(1, int(round(vb_h * content_w / vb_w)))
    px_per_unit = content_w / vb_w
    pad_px = max(1, int(math.ceil(pad * px_per_unit)))
    out_w = content_w + 2 * pad_px
    out_h = content_h + 2 * pad_px
    padded_vb = (vb_x - pad, vb_y - pad, vb_w + 2.0 * pad, vb_h + 2.0 * pad)
    return _BottomRowLayout(padded_vb, out_w, out_h, pad_px, content_w, content_h)


def _integrated_pad_layout(
    vb: tuple[float, float, float, float],
    *,
    content_w: int | None = None,
) -> _BottomRowLayout:
    """Padded raster layout for compact numeric / yes-no pads."""
    vb_x, vb_y, vb_w, vb_h = vb
    pad = _INTEGRATED_PAD_STROKE_PAD_SVG
    if content_w is None:
        content_w = max(1, int(round(vb_w * (DESIGN_W / 800.0))))
    content_h = max(1, int(round(vb_h * content_w / max(vb_w, 1.0))))
    px_per_unit = content_w / max(vb_w, 1.0)
    pad_px = max(1, int(math.ceil(pad * px_per_unit)))
    out_w = content_w + 2 * pad_px
    out_h = content_h + 2 * pad_px
    padded_vb = (vb_x - pad, vb_y - pad, vb_w + 2.0 * pad, vb_h + 2.0 * pad)
    return _BottomRowLayout(padded_vb, out_w, out_h, pad_px, content_w, content_h)


def _blit_bottom_row(canvas: np.ndarray, row: np.ndarray, *, dest_x: int, dest_y: int) -> None:
    """Alpha-blend a padded bottom-row strip onto the keyboard canvas without cropping strokes."""
    rh, rw = row.shape[:2]
    src_x0 = 0
    src_y0 = 0
    dest_x0 = dest_x
    dest_y0 = dest_y
    if dest_x0 < 0:
        src_x0 = -dest_x0
        dest_x0 = 0
    if dest_y0 < 0:
        src_y0 = -dest_y0
        dest_y0 = 0
    dest_x1 = min(DESIGN_W, dest_x0 + rw - src_x0)
    dest_y1 = min(DESIGN_H, dest_y0 + rh - src_y0)
    out_w = dest_x1 - dest_x0
    out_h = dest_y1 - dest_y0
    if out_w <= 0 or out_h <= 0:
        return
    src_x1 = src_x0 + out_w
    src_y1 = src_y0 + out_h
    region = canvas[dest_y0:dest_y1, dest_x0:dest_x1]
    strip = row[src_y0:src_y1, src_x0:src_x1]
    base_bgr = region[:, :, :3]
    blended = alpha_blend_bgra_over_bgr(base_bgr, strip)
    alpha = strip[:, :, 3:4].astype(np.float32) / 255.0
    out_a = np.clip(
        alpha * 255.0 + (1.0 - alpha) * region[:, :, 3:4].astype(np.float32),
        0,
        255,
    ).astype(np.uint8)
    canvas[dest_y0:dest_y1, dest_x0:dest_x1, :3] = blended
    canvas[dest_y0:dest_y1, dest_x0:dest_x1, 3:4] = out_a

_PATH_TOKENS_RE = re.compile(r"[a-zA-Z]|[-+]?(?:\d*\.\d+|\d+)")


def _path_bbox(d: str) -> tuple[float, float, float, float]:
    """Loose SVG path bbox for Illustrator-export pill buttons (M/h/v/c only)."""
    tokens = _PATH_TOKENS_RE.findall(d or "")
    idx = 0
    x = y = 0.0
    xs: list[float] = []
    ys: list[float] = []
    cmd = ""

    def _read() -> float:
        nonlocal idx
        value = float(tokens[idx])
        idx += 1
        return value

    while idx < len(tokens):
        token = tokens[idx]
        if token.isalpha():
            cmd = token
            idx += 1
            continue
        rel = cmd.islower()
        if cmd in ("M", "m"):
            nx, ny = _read(), _read()
            x, y = ((x + nx, y + ny) if rel else (nx, ny))
            xs.append(x)
            ys.append(y)
            cmd = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            nx, ny = _read(), _read()
            x, y = ((x + nx, y + ny) if rel else (nx, ny))
            xs.append(x)
            ys.append(y)
        elif cmd in ("H", "h"):
            nx = _read()
            x = (x + nx) if rel else nx
            xs.append(x)
            ys.append(y)
        elif cmd in ("V", "v"):
            ny = _read()
            y = (y + ny) if rel else ny
            xs.append(x)
            ys.append(y)
        elif cmd in ("C", "c"):
            vals = [_read() for _ in range(6)]
            if rel:
                x += vals[4]
                y += vals[5]
            else:
                x, y = vals[4], vals[5]
            xs.append(x)
            ys.append(y)
        else:
            idx += 1
    if not xs or not ys:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _bottom_row_button_center(root: ET.Element, button_path_id: str) -> tuple[float, float]:
    path = _find_by_logical_id(root, button_path_id)
    if path is None:
        return 0.0, 0.0
    x0, y0, x1, y1 = _path_bbox(path.get("d") or "")
    return (x0 + x1) * 0.5, (y0 + y1) * 0.5


def _layout_bottom_row_label(
    root: ET.Element,
    text_id: str,
    button_path_id: str,
    text: str,
    *,
    font_size: str = "20",
) -> None:
    """Center a bottom-row mode label inside its pill button."""
    el = _find_by_logical_id(root, text_id)
    if el is None:
        return
    cx, cy = _bottom_row_button_center(root, button_path_id)
    el.set("font-size", font_size)
    el.set("text-anchor", "middle")
    el.set("dominant-baseline", "middle")
    el.set("transform", f"translate({cx:.2f} {cy:.2f})")
    _set_text_content(el, text)
    for tspan in el.iter():
        if tspan.tag.endswith("tspan"):
            tspan.set("x", "0")
            tspan.set("y", "0")


def _remove_bottom_row_button1(root: ET.Element) -> None:
    """Drop button1 from the SVG tree (PyMuPDF ignores ``display:none``)."""
    btn1 = _find_by_logical_id(root, "keyboard_bottom_row_button1")
    if btn1 is None:
        return
    parents = _parent_map(root)
    parent = parents.get(btn1)
    if parent is not None:
        parent.remove(btn1)


def _remove_qwerty_shift_key(root: ET.Element) -> None:
    """Drop the shift key from uppercase QWERTY (Digital-7 fields never need it)."""
    for logical in ("keyboard_qwerty_upper_shift_button", "keyboard_qwerty_upper_SHIFT_icon"):
        el = _find_by_logical_id(root, logical)
        if el is None:
            continue
        parents = _parent_map(root)
        parent = parents.get(el)
        if parent is not None:
            parent.remove(el)


def apply_bottom_row_mode_icons(
    root: ET.Element,
    mode: KeyboardMode,
    *,
    uppercase_only: bool = False,
) -> None:
    """Show one mode label per bottom-row button (never two labels on the same key)."""
    btn1_group = _find_by_logical_id(root, "keyboard_bottom_row_button1")
    btn1_abc = _bottom_row_icons(
        root,
        "keyboard_bottom_row_button1",
        "keyboard_bottom_row_button1_ABC_icon-2",
        "keyboard_bottom_row_button1_ABC_icon",
    )
    btn1_123 = _bottom_row_icons(root, "keyboard_bottom_row_button1", "keyboard_bottom_row_button1_123_icon")
    btn2_abc = _bottom_row_icons(root, "keyboard2", "keyboard_bottom_row_button2_abc")
    btn2_abc_dup = _bottom_row_icons(root, "keyboard2", "keyboard_bottom_row_button1_ABC_icon")
    btn3_sym = _bottom_row_icons(root, "keyboard3", "keyboard_bottom_row_button3_sym_icon")
    btn3_123 = _bottom_row_icons(root, "keyboard3", "keyboard_bottom_row_button3_123_icon")

    for el in btn1_abc + btn1_123 + btn2_abc + btn2_abc_dup + btn3_sym + btn3_123:
        _set_visible(el, False)

    if uppercase_only:
        _remove_bottom_row_button1(root)
        _layout_bottom_row_label(
            root,
            "keyboard_bottom_row_button2_abc",
            "keyboard_bottom_row_button2_button",
            "123",
        )
        for el in btn2_abc:
            _set_visible(el, True)
        _layout_bottom_row_label(
            root,
            "keyboard_bottom_row_button3_sym_icon",
            "keyboard_bottom_row_button3_button",
            "sym",
            font_size="25",
        )
        for el in btn3_sym:
            _set_visible(el, True)
        return

    _set_visible(btn1_group, True)

    for el in btn1_123 + btn2_abc_dup + btn3_123:
        _set_visible(el, False)

    _layout_bottom_row_label(
        root,
        "keyboard_bottom_row_button2_abc",
        "keyboard_bottom_row_button2_button",
        "123",
    )
    _layout_bottom_row_label(
        root,
        "keyboard_bottom_row_button3_sym_icon",
        "keyboard_bottom_row_button3_button",
        "sym",
        font_size="25",
    )

    for el in btn2_abc:
        _set_visible(el, True)
    for el in btn3_sym:
        _set_visible(el, True)

    if mode == KeyboardMode.QWERTY_LOWER:
        case_label = "ABC"
    elif mode == KeyboardMode.QWERTY_UPPER:
        case_label = "abc"
    else:
        case_label = "ABC"

    btn1_label = _find_by_logical_id(root, "keyboard_bottom_row_button1_ABC_icon-2")
    if btn1_label is None:
        btn1_label = _find_by_logical_id(root, "keyboard_bottom_row_button1_ABC_icon")
    label_id = _normalize_logical(btn1_label.get("id") or "") if btn1_label is not None else ""
    if not label_id:
        label_id = "keyboard_bottom_row_button1_ABC_icon-2"
    _layout_bottom_row_label(
        root,
        label_id,
        "keyboard_bottom_row_button1_buton",
        case_label,
        font_size="22",
    )
    for el in btn1_abc[:1] or btn1_abc:
        _set_visible(el, True)


def _fit_full_artboard(root: ET.Element) -> None:
    """Match main_settings: native 800×480 artboard."""
    root.set("viewBox", "0 0 800 480")
    root.set("width", str(DESIGN_W))
    root.set("height", str(DESIGN_H))


def _center_integrated_pad_labels(root: ET.Element) -> None:
    """Center key labels inside their pill buttons (PyMuPDF baseline quirks)."""
    for el in root.iter():
        if not el.tag.endswith("rect"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if not logical.endswith("_button"):
            continue
        cx = float(el.get("x", 0)) + float(el.get("width", 0)) * 0.5
        cy = float(el.get("y", 0)) + float(el.get("height", 0)) * 0.5
        icon_el = _find_by_logical_id(root, logical.replace("_button", "_icon"))
        if icon_el is None:
            continue
        icon_el.set("text-anchor", "middle")
        icon_el.set("dominant-baseline", "middle")
        icon_el.set("alignment-baseline", "middle")
        icon_el.set("transform", f"translate({cx:.2f} {cy:.2f})")
        for tspan in icon_el.iter():
            if tspan.tag.endswith("tspan"):
                tspan.set("x", "0")
                tspan.set("y", "0")


def _rasterize_keyboard_chars(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
) -> np.ndarray:
    path = keyboard_svg_path(_MODE_SVG[state.mode], assets_dir=assets_dir)
    if not path.is_file():
        return np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)

    root = ET.parse(path).getroot()
    if (
        state.mode == KeyboardMode.QWERTY_UPPER
        and not state.supports_lowercase
        and not state.password_mask
    ):
        _remove_qwerty_shift_key(root)
    pad_mode = state.mode in (KeyboardMode.NUMERIC_PIN, KeyboardMode.YES_NO)
    full_ip = state.mode == KeyboardMode.NUMERIC_IP
    button_ids: set[str] = set()
    icon_map: dict[str, tuple[str, ...]] = {}
    for k in state.focus_ring:
        include = pad_mode or full_ip or k.action in (KeyAction.CHAR, KeyAction.SHIFT)
        if not include:
            continue
        button_ids.add(k.button_id)
        if k.icon_ids:
            icon_map[k.button_id] = k.icon_ids

    focused = state.focused.button_id
    char_focus = focused if focused in button_ids else ""
    apply_keyboard_selection(
        root,
        focused_button_id=char_focus,
        theme=state.theme,
        button_ids=button_ids,
        icon_ids_by_button=icon_map,
        muted_deselected=(state.mode == KeyboardMode.YES_NO),
    )

    # Compact cropped pads (PIN / yes-no). IP uses the full 800×480 artboard
    # so it lines up with keyboard_numeric_all / Illustrator placement.
    if pad_mode:
        _center_integrated_pad_labels(root)
        vb = viewbox_from_root(root)
        # Design scale (same as IP / numeric / yes-no) — do not upscale the compact PIN crop.
        content_w = max(1, int(round(vb[2] * (DESIGN_W / 800.0))))
        layout = _integrated_pad_layout(vb, content_w=content_w)
        root.set("overflow", "visible")
        pad = rasterize_settings_svg_bgra(
            root,
            width=layout.out_w,
            height=layout.out_h,
            view_box=layout.padded_vb,
            font_mode="keyboard",
        )
        canvas = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
        dest_x = max(0, (DESIGN_W - layout.out_w) // 2)
        if state.mode == KeyboardMode.YES_NO:
            dest_y = max(0, (DESIGN_H - layout.out_h) // 2)
        else:
            dest_y = int(_INTEGRATED_PAD_TOP_Y * (DESIGN_H / 480)) - layout.pad_px
        _blit_bottom_row(canvas, pad, dest_x=dest_x, dest_y=dest_y)
        return canvas

    if full_ip:
        _center_integrated_pad_labels(root)

    _fit_full_artboard(root)
    return rasterize_settings_svg_bgra(
        root, width=DESIGN_W, height=DESIGN_H, font_mode="keyboard"
    )


def _rasterize_bottom_row(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
) -> np.ndarray | None:
    if not state.include_bottom_row:
        return None
    path = keyboard_svg_path(_BOTTOM_ROW_SVG, assets_dir=assets_dir)
    if not path.is_file():
        return None
    root = ET.parse(path).getroot()
    apply_bottom_row_mode_icons(root, state.mode, uppercase_only=not state.supports_lowercase)

    bottom = _BOTTOM_ROW_NETWORK if state.supports_lowercase else _BOTTOM_ROW_UPPERCASE
    button_ids = {k.button_id for k in bottom}
    icon_map = {k.button_id: k.icon_ids for k in bottom if k.icon_ids}
    focused = state.focused.button_id
    row_focus = focused if focused in button_ids else ""
    apply_keyboard_selection(
        root,
        focused_button_id=row_focus,
        theme=state.theme,
        button_ids=button_ids,
        icon_ids_by_button=icon_map,
    )
    _prune_display_none(root)

    layout = _bottom_row_layout()
    root.set(
        "viewBox",
        f"{layout.padded_vb[0]} {layout.padded_vb[1]} {layout.padded_vb[2]} {layout.padded_vb[3]}",
    )
    root.set("overflow", "visible")
    return rasterize_settings_svg_bgra(
        root,
        width=layout.out_w,
        height=layout.out_h,
        view_box=layout.padded_vb,
        font_mode="keyboard",
    )


def render_keyboard_bgra(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    """Composite character keys + bottom row → 800×480 BGRA."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)

    canvas = _rasterize_keyboard_chars(state, assets_dir=assets_dir)
    row = _rasterize_bottom_row(state, assets_dir=assets_dir)
    if row is not None and row.size:
        layout = _bottom_row_layout()
        dest_x = int(_BOTTOM_ROW_X * (DESIGN_W / 800)) - layout.pad_px
        dest_y = int(_BOTTOM_ROW_Y * (DESIGN_H / 480)) - layout.pad_px
        _blit_bottom_row(canvas, row, dest_x=dest_x, dest_y=dest_y)
    return canvas


def activate_key(state: KeyboardState, *, assets_dir: Path | str | None = None) -> str:
    """
    Apply the focused key. Returns an action token:
      - ``typing`` — buffer changed
      - ``cancel`` — discard / close
      - ``go`` — commit buffer / close
      - ``mode:<name>`` — layout switched
    """
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    key = state.focused
    act = key.action

    if act == KeyAction.CHAR:
        if key.char:
            ch = key.char
            if state.target == "pin":
                if not ch.isdigit():
                    return "typing"
                cur = "".join(c for c in state.buffer if c.isdigit())
                if len(cur) >= 4:
                    return "typing"
                ch = ch
            elif state.target == "device_ip":
                if ch not in "0123456789.":
                    return "typing"
            elif not state.supports_lowercase:
                ch = ch.upper()
            state.buffer += ch
        return "typing"
    if act == KeyAction.SPACE:
        state.buffer += " "
        return "typing"
    if act == KeyAction.DELETE:
        state.buffer = state.buffer[:-1]
        return "typing"
    if act == KeyAction.SHIFT:
        if not state.supports_lowercase:
            return "typing"
        if state.mode == KeyboardMode.QWERTY_LOWER:
            state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
        else:
            state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
        return f"mode:{state.mode.value}"
    if act == KeyAction.MODE_ABC:
        if state.supports_lowercase:
            if state.mode == KeyboardMode.QWERTY_LOWER:
                state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
            elif state.mode == KeyboardMode.QWERTY_UPPER:
                state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
            elif state.mode in (KeyboardMode.NUMERIC_ALL, KeyboardMode.NUMERIC_PIN):
                state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
            else:
                state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
        else:
            state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
        return f"mode:{state.mode.value}"
    if act == KeyAction.MODE_ABC_LOWER:
        state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
        return f"mode:{state.mode.value}"
    if act == KeyAction.MODE_SYM:
        if not state.supports_lowercase:
            if state.mode == KeyboardMode.SYMBOLIC:
                state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
            else:
                state.set_mode(KeyboardMode.SYMBOLIC, assets_dir=assets_dir)
        elif state.mode == KeyboardMode.SYMBOLIC:
            state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
        else:
            state.set_mode(KeyboardMode.SYMBOLIC, assets_dir=assets_dir)
        return f"mode:{state.mode.value}"
    if act == KeyAction.MODE_123:
        if not state.supports_lowercase:
            if state.mode == KeyboardMode.NUMERIC_ALL:
                state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
            else:
                state.set_mode(KeyboardMode.NUMERIC_ALL, assets_dir=assets_dir)
        elif state.mode == KeyboardMode.NUMERIC_ALL:
            state.set_mode(KeyboardMode.QWERTY_LOWER, assets_dir=assets_dir)
        else:
            state.set_mode(KeyboardMode.NUMERIC_ALL, assets_dir=assets_dir)
        return f"mode:{state.mode.value}"
    if act == KeyAction.CANCEL:
        state.buffer = state.initial_text
        return "cancel"
    if act == KeyAction.YES:
        return "yes"
    if act == KeyAction.NO:
        return "no"
    if act == KeyAction.GO:
        return "go"
    return "typing"


def focus_yes_no_yes(state: KeyboardState, *, assets_dir: Path | str | None = None) -> None:
    """Move focus to the YES key on the logout confirmation pad."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    for i, key in enumerate(state.focus_ring):
        if key.action == KeyAction.YES:
            state.focus_index = i
            return


def focus_numeric_one(state: KeyboardState, *, assets_dir: Path | str | None = None) -> None:
    """Move focus to the ``1`` key on numeric layouts."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    for i, key in enumerate(state.focus_ring):
        if key.action == KeyAction.CHAR and key.char == "1":
            state.focus_index = i
            return
    focus_first_letter(state, assets_dir=assets_dir)


def focus_keyboard_go(state: KeyboardState, *, assets_dir: Path | str | None = None) -> None:
    """Move focus to the GO key on the bottom row."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    for i, key in enumerate(state.focus_ring):
        if key.action == KeyAction.GO:
            state.focus_index = i
            return


def focus_first_letter(state: KeyboardState, *, assets_dir: Path | str | None = None) -> None:
    """Move focus to the top-left character key (``q`` / ``Q`` on QWERTY layouts)."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    want = "q" if state.supports_lowercase else "Q"
    for i, key in enumerate(state.focus_ring):
        if key.action == KeyAction.CHAR and key.char.lower() == want.lower():
            state.focus_index = i
            return
    for i, key in enumerate(state.focus_ring):
        if key.action == KeyAction.CHAR:
            state.focus_index = i
            return


def open_keyboard(
    *,
    target: str,
    initial_text: str = "",
    buffer: str = "",
    mode: KeyboardMode | None = None,
    theme: SettingsTheme | None = None,
    assets_dir: Path | str | None = None,
) -> KeyboardState:
    """Create a ready-to-navigate keyboard state for a text field."""
    supports_lower = target == "network"
    password_mask = target == "network"
    if mode is None:
        if target == "pin":
            mode = KeyboardMode.NUMERIC_PIN
        elif target == "device_ip":
            mode = KeyboardMode.NUMERIC_IP
        elif target == "wifi_logout":
            mode = KeyboardMode.YES_NO
        elif target == "location":
            mode = KeyboardMode.QWERTY_UPPER
        else:
            mode = KeyboardMode.QWERTY_LOWER
    st = KeyboardState(
        mode=mode,
        buffer=str(buffer or ""),
        initial_text=initial_text,
        target=target,
        theme=theme if theme is not None else SettingsTheme(),
        supports_lowercase=supports_lower,
        password_mask=password_mask,
    )
    st.rebuild_focus_ring(assets_dir=assets_dir)
    if target == "wifi_logout":
        focus_yes_no_yes(st, assets_dir=assets_dir)
    elif target in ("pin", "device_ip"):
        focus_numeric_one(st, assets_dir=assets_dir)
    else:
        focus_first_letter(st, assets_dir=assets_dir)
    return st


__all__ = [
    "KeyAction",
    "KeySpec",
    "KeyboardMode",
    "KeyboardState",
    "activate_key",
    "discover_char_keys",
    "discover_integrated_pad_keys",
    "discover_yes_no_keys",
    "focus_first_letter",
    "focus_yes_no_yes",
    "focus_keyboard_go",
    "focus_numeric_one",
    "open_keyboard",
    "render_keyboard_bgra",
]
