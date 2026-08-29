"""
Settings keyboards — SVG overlays shared by main_settings text entry.

Native 1280 layouts (``pigeonAssets/settings/keyboard/``):
  - keyboard_lower / keyboard_upper
  - keyboard_numeric / keyboard_symbolic / keyboard_ip
  - keyboard_bottom_row  (shared cancel / SYM / 123 / space / delete / enter)
  - keyboard_pin         (no bottom row)

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
from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.widgets.main_settings import (
    COLOR_SELECTED,
    COLOR_UI_DEFAULT,
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
    KeyboardMode.QWERTY_LOWER: "keyboard_lower.svg",
    KeyboardMode.QWERTY_UPPER: "keyboard_upper.svg",
    KeyboardMode.SYMBOLIC: "keyboard_symbolic.svg",
    KeyboardMode.NUMERIC_ALL: "keyboard_numeric.svg",
    KeyboardMode.NUMERIC_PIN: "keyboard_pin.svg",
    KeyboardMode.NUMERIC_IP: "keyboard_ip.svg",
    KeyboardMode.YES_NO: "keyboard_yes_no.svg",
}

_BOTTOM_ROW_SVG = "keyboard_bottom_row.svg"

# Shared lower / numeric / bottom-row artboard (centered between box 1 and the NP bar).
_CLUSTER_VB = (0.0, 0.0, 1202.99, 298.66)
_CLUSTER_KEY_ROW_Y = 228.49
_CLUSTER_GAP_AFTER_BOX1 = 12.0
_CLUSTER_GAP_ABOVE_STATUS = 8.0
# Inset the whole cluster so Q/P/cancel/enter don't kiss the plate edge.
_CLUSTER_NARROW_PX = 10.0
_CLUSTER_SCALE = (_CLUSTER_VB[2] - _CLUSTER_NARROW_PX) / _CLUSTER_VB[2]
# Top-left of ``lower_q`` in keyboard_lower.svg — register every QWERTY board here.
_CLUSTER_Q_XY = (57.04, 0.0)

def _is_numeric_mode(mode: KeyboardMode) -> bool:
    return mode in (
        KeyboardMode.NUMERIC_ALL,
        KeyboardMode.NUMERIC_PIN,
        KeyboardMode.NUMERIC_IP,
    )


def _bottom_row_mode_labels(mode: KeyboardMode) -> tuple[str, str]:
    """Labels for the two mode keys (lower_SYM, lower_123)."""
    if _is_numeric_mode(mode):
        return "SYM", "abc"
    if mode == KeyboardMode.SYMBOLIC:
        return "abc", "123"
    return "SYM", "123"


def _action_for_mode_label(label: str) -> KeyAction:
    if label.upper() == "SYM":
        return KeyAction.MODE_SYM
    if label == "123":
        return KeyAction.MODE_123
    return KeyAction.MODE_ABC


def _bottom_row_keys(mode: KeyboardMode) -> tuple[KeySpec, ...]:
    left, right = _bottom_row_mode_labels(mode)
    return (
        KeySpec("lower_cancel", KeyAction.CANCEL),
        KeySpec("lower_SYM", _action_for_mode_label(left)),
        KeySpec("lower_123", _action_for_mode_label(right)),
        KeySpec("lower_space", KeyAction.SPACE, char=" "),
        KeySpec("lower_del", KeyAction.DELETE, icon_ids=("delete",)),
        KeySpec("lower_enter", KeyAction.GO),
    )


# Visual left→right on lowercase: cancel, SYM, 123, space, delete, enter.
_BOTTOM_ROW_NETWORK: tuple[KeySpec, ...] = _bottom_row_keys(KeyboardMode.QWERTY_LOWER)
_BOTTOM_ROW_KEYS = _BOTTOM_ROW_NETWORK
_BOTTOM_ROW_TAIL = _BOTTOM_ROW_NETWORK[3:]
_BOTTOM_ROW_UPPERCASE: tuple[KeySpec, ...] = _bottom_row_keys(KeyboardMode.QWERTY_UPPER)

_IP_HIDDEN_IDS: tuple[str, ...] = (
    "numeric_template",
)


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
            if not self.focus_ring:
                self.focus_ring = (
                    KeySpec("keyboard_yes_no_yes_button", KeyAction.YES),
                    KeySpec("keyboard_yes_no_no_button", KeyAction.NO),
                )
            self.include_bottom_row = False
        elif self.mode == KeyboardMode.NUMERIC_IP:
            self.focus_ring = discover_integrated_pad_keys(
                KeyboardMode.NUMERIC_IP, assets_dir=assets_dir
            )
            self.include_bottom_row = False
        elif self.mode == KeyboardMode.NUMERIC_PIN:
            pad = [
                k
                for k in discover_integrated_pad_keys(self.mode, assets_dir=assets_dir)
                if k.action not in (KeyAction.CANCEL, KeyAction.DELETE, KeyAction.GO)
            ]
            self.focus_ring = tuple(pad) + _bottom_row_keys(self.mode)
            self.include_bottom_row = True
        else:
            char_keys = discover_char_keys(self.mode, assets_dir=assets_dir)
            # Digital-7 uppercase-only fields drop Shift; Wi‑Fi password keeps it.
            if (
                self.mode == KeyboardMode.QWERTY_UPPER
                and not self.supports_lowercase
                and not self.password_mask
            ):
                char_keys = tuple(k for k in char_keys if k.action != KeyAction.SHIFT)
            if self.include_bottom_row:
                self.focus_ring = tuple(char_keys) + _bottom_row_keys(self.mode)
            else:
                self.focus_ring = tuple(char_keys)
        if not self.focus_ring:
            self.focus_ring = _bottom_row_keys(self.mode)
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
    """Sort key: top→bottom, left→right. Walks descendants for group-based keys."""
    for node in el.iter():
        x = node.get("x")
        y = node.get("y")
        if x is not None and y is not None:
            try:
                return float(x), float(y)
            except ValueError:
                pass
        d = node.get("d") or ""
        m = re.search(r"[Mm]\s*([-\d.]+)[,\s]+([-\d.]+)", d)
        if m:
            try:
                return float(m.group(1)), float(m.group(2))
            except ValueError:
                pass
    return (0.0, 0.0)


def _group_prefix_for_mode(mode: KeyboardMode) -> str:
    if mode == KeyboardMode.QWERTY_LOWER:
        return "lower_"
    if mode == KeyboardMode.QWERTY_UPPER:
        return "upper_"
    if mode == KeyboardMode.NUMERIC_ALL:
        return "num_"
    return ""


def discover_group_keys(
    mode: KeyboardMode,
    root: ET.Element,
) -> list[KeySpec]:
    """Build character-key specs from unlabeled 1280 group exports."""
    prefix = _group_prefix_for_mode(mode)
    if not prefix:
        return []
    skip = {
        "lower_enter",
        "lower_del",
        "lower_space",
        "lower_123",
        "lower_sym",
        "lower_cancel",
        "delete",
    }
    seen: set[tuple[float, float]] = set()
    found: list[tuple[float, float, KeySpec]] = []
    for el in root.iter():
        if not el.tag.endswith("g"):
            continue
        logical = _normalize_logical(el.get("id") or "")
        if not logical.startswith(prefix) or logical in skip:
            continue
        x, y = _button_xy(el)
        pos = (round(x, 1), round(y, 1))
        if pos in seen or pos == (0.0, 0.0):
            continue
        seen.add(pos)
        text = "".join(el.itertext()).strip()
        if "shift" in logical.lower():
            found.append((y, x, KeySpec(logical, KeyAction.SHIFT)))
            continue
        ch = text[:1] if text else ""
        if not ch:
            m = re.search(r"_([A-Za-z0-9])(?:-\d+)?$", logical)
            if m:
                ch = m.group(1)
        if mode == KeyboardMode.QWERTY_UPPER and ch:
            ch = ch.upper()
        elif mode == KeyboardMode.QWERTY_LOWER and ch:
            ch = ch.lower()
        found.append((y, x, KeySpec(logical, KeyAction.CHAR, char=ch)))
    found.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    return [spec for _y, _x, spec in found]


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
    grouped = discover_group_keys(mode, root)
    if grouped:
        return grouped

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

        # Symbolic: symbolic_button_X (or symbolic_button__-28 with the glyph on the icon).
        if logical.startswith("symbolic_button_"):
            suffix = logical[len("symbolic_button_") :]
            icon_id = f"symbolic_icon_{suffix}"
            resolved = icon_id
            ch = suffix if len(suffix) == 1 else ""
            for cand, node in icon_nodes.items():
                if cand == icon_id or cand.startswith(icon_id + "_"):
                    resolved = cand
                    text = "".join(node.itertext()).strip()
                    if text:
                        ch = text[0]
                    break
            if not ch:
                for cand, node in icon_nodes.items():
                    if cand.replace("symbolic_icon_", "") == suffix:
                        resolved = cand
                        text = "".join(node.itertext()).strip()
                        if text:
                            ch = text[0]
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


def _discover_ip_group_keys(root: ET.Element) -> list[KeySpec]:
    """0–9 / pair / dot from the 1280 IP pad (skip template + built-in cancel/delete)."""
    skip = set(_IP_HIDDEN_IDS)
    seen: set[tuple[float, float]] = set()
    found: list[tuple[float, float, KeySpec]] = []
    for el in root:
        logical = _normalize_logical(el.get("id") or "")
        if not logical.startswith("numeric_") or logical in skip:
            continue
        x, y = _button_xy(el)
        pos = (round(x, 1), round(y, 1))
        if pos in seen:
            continue
        seen.add(pos)
        text = "".join(el.itertext()).strip()
        if "pair" in logical:
            found.append((y, x, KeySpec(logical, KeyAction.GO)))
            continue
        if "cancel" in logical:
            found.append((y, x, KeySpec(logical, KeyAction.CANCEL)))
            continue
        if "delete" in logical:
            found.append((y, x, KeySpec(logical, KeyAction.DELETE)))
            continue
        ch = text[:1] if text else ""
        if ch == "x" and "delete" in logical:
            continue
        if not ch and ("." in logical or logical.endswith("_.")):
            ch = "."
        found.append((y, x, KeySpec(logical, KeyAction.CHAR, char=ch)))
    found.sort(key=lambda t: (round(t[0], 1), round(t[1], 1)))
    return [spec for _y, _x, spec in found]


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
    if mode == KeyboardMode.NUMERIC_IP:
        return tuple(_discover_ip_group_keys(root))
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
        elif "pair" in logical or logical.endswith("_go_button") or "_go_button" in logical:
            keys.append(KeySpec(logical, KeyAction.GO, icon_ids=icon_ids))
        elif "dot" in logical or logical.endswith("._button") or "numeric_._button" in logical:
            keys.append(KeySpec(logical, KeyAction.CHAR, char=".", icon_ids=icon_ids))
        else:
            ch = ""
            for iid in icon_ids:
                node = icon_nodes.get(iid)
                if node is None:
                    continue
                text = "".join(node.itertext()).strip()
                if text:
                    ch = text[0]
                    break
            if not ch:
                for part in logical.split("_"):
                    if len(part) == 1 and (part.isdigit() or part == "."):
                        ch = part
                        break
            keys.append(KeySpec(logical, KeyAction.CHAR, char=ch, icon_ids=icon_ids))
    return tuple(keys)


def _keyboard_idle_fill(theme: SettingsTheme) -> str:
    """Idle key chrome uses the UI color in place of black."""
    return str(theme.ui or COLOR_UI_DEFAULT)


def _keyboard_paint_theme(theme: SettingsTheme) -> SettingsTheme:
    idle = _keyboard_idle_fill(theme)
    return SettingsTheme(
        ui=idle,
        selected=theme.selected or COLOR_SELECTED,
        deselected=idle,
        inactive=theme.inactive,
        accent=theme.accent,
    )


def _paint_kb_button_shape(
    node: ET.Element,
    *,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    """Flat key fill — accent outlines are turned off on keyboards."""
    fill = theme.selected if selected else _keyboard_idle_fill(theme)
    _set_paint(node, fill=fill, stroke="none")
    style = node.get("style") or ""
    style = _rewrite_style_prop(style, "stroke-width", "0")
    if style:
        node.set("style", style)


def _is_key_pill(node: ET.Element) -> bool:
    """True for the rounded key body, false for shift/delete glyphs inside it."""
    tag = node.tag.rsplit("}", 1)[-1]
    if tag == "path":
        return True
    if tag != "rect":
        return False
    try:
        return float(node.get("rx") or 0.0) >= 8.0
    except (TypeError, ValueError):
        return False


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

    theme = _keyboard_paint_theme(theme)
    icon_map = icon_ids_by_button or {}
    idle = _keyboard_idle_fill(theme)
    fill_ok = set(_BUTTON_FILL_CANDIDATES) | _KB_BUTTON_EXTRA | {
        theme.selected.lower(),
        theme.deselected.lower(),
        idle.lower(),
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
        delete_nodes: set[int] = set()
        for node in el.iter():
            if _normalize_logical(node.get("id") or "") == "delete":
                delete_nodes.update(id(n) for n in node.iter())
        fill = theme.selected if selected else idle
        glyph_nodes: list[ET.Element] = []
        for node in el.iter():
            tag = node.tag.rsplit("}", 1)[-1]
            if tag not in ("path", "rect", "polygon", "circle", "ellipse"):
                continue
            if id(node) in delete_nodes:
                continue
            nid = _normalize_logical(node.get("id") or "")
            if nid.endswith("_accent") or nid == "delete":
                continue
            cur_fill, _ = _iter_style_fill_stroke(node)
            if cur_fill in ("none", "transparent"):
                continue
            if not _is_key_pill(node):
                glyph_nodes.append(node)
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
        for glyph in glyph_nodes:
            _apply_contrast_paint(
                glyph,
                selected=selected,
                theme=theme,
                muted_deselected=muted_deselected,
            )
        # Skip _apply_button_fill — it would recolor the delete glyph as a key.

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
        # 1280 group exports often leave labels unnamed — contrast-paint text in the group.
        for node in el.iter():
            if node.tag.endswith("text") or node.tag.endswith("tspan"):
                _apply_contrast_paint(
                    node,
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
    for logical in (
        "keyboard_qwerty_upper_shift_button",
        "keyboard_qwerty_upper_SHIFT_icon",
        "upper_SHIFT",
        "lower_shift",
    ):
        el = _find_by_logical_id(root, logical)
        if el is None:
            continue
        parents = _parent_map(root)
        parent = parents.get(el)
        if parent is not None:
            parent.remove(el)


def _remove_logical(root: ET.Element, logical: str) -> None:
    el = _find_by_logical_id(root, logical)
    if el is None:
        return
    parents = _parent_map(root)
    parent = parents.get(el)
    if parent is not None:
        parent.remove(el)


def _set_native_key_label(root: ET.Element, group_id: str, label: str) -> None:
    """Replace the label inside a 1280 bottom-row group and center it on the pill."""
    group = _find_by_logical_id(root, group_id)
    if group is None:
        return
    rect = None
    text_el = None
    for el in group.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "rect" and rect is None:
            rect = el
        elif tag == "text":
            text_el = el
    if text_el is None:
        return
    if rect is not None:
        cx = float(rect.get("x", 0)) + float(rect.get("width", 0)) * 0.5
        cy = float(rect.get("y", 0)) + float(rect.get("height", 0)) * 0.5
        text_el.set("text-anchor", "middle")
        text_el.set("dominant-baseline", "middle")
        text_el.set("alignment-baseline", "middle")
        text_el.set("transform", f"translate({cx:.2f} {cy:.2f})")
    tspans = [c for c in list(text_el) if c.tag.endswith("tspan")]
    if tspans:
        tspans[0].text = label
        tspans[0].set("x", "0")
        tspans[0].set("y", "0")
        tspans[0].attrib.pop("letter-spacing", None)
        for extra in tspans[1:]:
            extra.text = ""
            extra.set("x", "0")
            extra.set("y", "0")
    else:
        text_el.text = label


def _apply_native_bottom_row_labels(root: ET.Element, mode: KeyboardMode) -> None:
    if _find_by_logical_id(root, "lower_enter") is None:
        return
    left, right = _bottom_row_mode_labels(mode)
    _set_native_key_label(root, "lower_SYM", left)
    _set_native_key_label(root, "lower_123", right)


def apply_bottom_row_mode_icons(
    root: ET.Element,
    mode: KeyboardMode,
    *,
    uppercase_only: bool = False,
) -> None:
    """Show one mode label per bottom-row button (never two labels on the same key)."""
    if _find_by_logical_id(root, "lower_enter") is not None:
        return
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
    """Match main_settings: native 800×480 artboard, letterboxed into design."""
    from pigeon.design import LEGACY_DESIGN_H, LEGACY_DESIGN_W

    root.set("viewBox", "0 0 800 480")
    root.set("width", str(LEGACY_DESIGN_W))
    root.set("height", str(LEGACY_DESIGN_H))


def _center_group_label(group: ET.Element) -> None:
    """Center the label that already lives in this key group onto its pill."""
    pill: ET.Element | None = None
    text_el: ET.Element | None = None
    best_area = -1.0
    for node in group.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "text" and text_el is None:
            text_el = node
        if tag == "rect":
            try:
                w = float(node.get("width") or 0)
                h = float(node.get("height") or 0)
                x = float(node.get("x") or 0)
                y = float(node.get("y") or 0)
            except ValueError:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                pill = node
        elif tag == "path":
            x0, y0, x1, y1 = _path_bbox(node.get("d") or "")
            area = max(0.0, (x1 - x0) * (y1 - y0))
            if area > best_area:
                best_area = area
                pill = node
    if pill is None or text_el is None:
        return
    tag = pill.tag.rsplit("}", 1)[-1]
    if tag == "rect":
        cx = float(pill.get("x", 0)) + float(pill.get("width", 0)) * 0.5
        cy = float(pill.get("y", 0)) + float(pill.get("height", 0)) * 0.5
    else:
        x0, y0, x1, y1 = _path_bbox(pill.get("d") or "")
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    text_el.set("text-anchor", "middle")
    text_el.set("dominant-baseline", "middle")
    text_el.set("alignment-baseline", "middle")
    text_el.set("transform", f"translate({cx:.2f} {cy:.2f})")
    for tspan in text_el.iter():
        if tspan.tag.endswith("tspan"):
            tspan.set("x", "0")
            tspan.set("y", "0")


def _center_integrated_pad_labels(root: ET.Element) -> None:
    """Center each pad key's own label; never look up another key's icon by id."""
    for group in root:
        if not group.tag.endswith("g"):
            continue
        logical = _normalize_logical(group.get("id") or "")
        if logical in _IP_HIDDEN_IDS:
            continue
        _center_group_label(group)


def _cluster_wh() -> tuple[float, float]:
    return (_CLUSTER_VB[2] * _CLUSTER_SCALE, _CLUSTER_VB[3] * _CLUSTER_SCALE)


def _cluster_xy() -> tuple[int, int]:
    """Center the scaled key cluster between box 1 and the NP status-bar track."""
    from pigeon.np_layout import NOW_PLAYING_ZONES, STATUS_BAR_TRACK
    from pigeon.settings_layout import DUAL_SLOT_A, dual_slot_design

    cw, ch = _cluster_wh()
    x = int(round((DESIGN_W - cw) * 0.5))
    box = dual_slot_design(DUAL_SLOT_A)
    band_top = float(box[1] + box[3]) + _CLUSTER_GAP_AFTER_BOX1
    track_top = float(NOW_PLAYING_ZONES[5].y) + float(STATUS_BAR_TRACK[1])
    band_bottom = track_top - _CLUSTER_GAP_ABOVE_STATUS
    y = band_top + (band_bottom - band_top - ch) * 0.5
    if y + ch > band_bottom:
        y = band_bottom - ch
    if y < band_top:
        y = band_top
    return x, int(round(y))


def _key_origin(group: ET.Element) -> tuple[float, float]:
    """Top-left of the largest pill in a key group."""
    best_area = -1.0
    origin = (0.0, 0.0)
    for node in group.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "rect":
            try:
                x = float(node.get("x") or 0)
                y = float(node.get("y") or 0)
                w = float(node.get("width") or 0)
                h = float(node.get("height") or 0)
            except ValueError:
                continue
            area = w * h
            if area > best_area:
                best_area = area
                origin = (x, y)
        elif tag == "path":
            x0, y0, x1, y1 = _path_bbox(node.get("d") or "")
            area = max(0.0, (x1 - x0) * (y1 - y0))
            if area > best_area:
                best_area = area
                origin = (x0, y0)
    return origin


def _align_qwerty_to_cluster(root: ET.Element) -> None:
    """Crop every QWERTY board to the shared cluster, with Q on ``lower_q``."""
    q = _find_by_logical_id(root, "lower_q")
    if q is None:
        q = _find_by_logical_id(root, "upper_Q")
    if q is None:
        q = _find_by_logical_id(root, "upper_q")
    if q is None:
        return
    qx, qy = _key_origin(q)
    vx = qx - _CLUSTER_Q_XY[0]
    vy = qy - _CLUSTER_Q_XY[1]
    root.set("viewBox", f"{vx} {vy} {_CLUSTER_VB[2]} {_CLUSTER_VB[3]}")


def _rasterize_svg_patch(root: ET.Element) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    vb = viewbox_from_root(root)
    w = max(1, int(round(vb[2])))
    h = max(1, int(round(vb[3])))
    root.set("viewBox", f"{vb[0]} {vb[1]} {vb[2]} {vb[3]}")
    patch = rasterize_settings_svg_bgra(root, width=w, height=h, font_mode="keyboard")
    return patch, vb


def _pad_content_viewbox(root: ET.Element) -> tuple[float, float, float, float]:
    """Tight crop around remaining pad shapes (IP artboard is a full 1280 screen)."""
    min_x = min_y = 1e9
    max_x = max_y = -1e9
    found = False
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "rect":
            try:
                x = float(node.get("x") or 0)
                y = float(node.get("y") or 0)
                w = float(node.get("width") or 0)
                h = float(node.get("height") or 0)
            except ValueError:
                continue
            found = True
            min_x, min_y = min(min_x, x), min(min_y, y)
            max_x, max_y = max(max_x, x + w), max(max_y, y + h)
        elif tag == "path":
            x0, y0, x1, y1 = _path_bbox(node.get("d") or "")
            if x1 <= x0 or y1 <= y0:
                continue
            found = True
            min_x, min_y = min(min_x, x0), min(min_y, y0)
            max_x, max_y = max(max_x, x1), max(max_y, y1)
    if not found:
        return viewbox_from_root(root)
    pad = 4.0
    return (min_x - pad, min_y - pad, max_x - min_x + 2.0 * pad, max_y - min_y + 2.0 * pad)


def _scale_keyboard_patch(patch: np.ndarray) -> np.ndarray:
    """Uniform inset so the cluster does not touch the plate edge."""
    if patch.size == 0:
        return patch
    h, w = patch.shape[:2]
    nw = max(1, int(round(w * _CLUSTER_SCALE)))
    nh = max(1, int(round(h * _CLUSTER_SCALE)))
    if nw == w and nh == h:
        return patch
    return cv2.resize(patch, (nw, nh), interpolation=cv2.INTER_AREA)


def _place_in_cluster(
    canvas: np.ndarray,
    patch: np.ndarray,
    vb: tuple[float, float, float, float],
    mode: KeyboardMode,
) -> None:
    patch = _scale_keyboard_patch(patch)
    cx, cy = _cluster_xy()
    cw, _ch = _cluster_wh()
    dest_x = cx + int(round((cw - patch.shape[1]) * 0.5))
    if mode == KeyboardMode.SYMBOLIC:
        dest_y = cy + int(round((_CLUSTER_KEY_ROW_Y - 434.33) * _CLUSTER_SCALE))
    else:
        dest_y = cy
    _blit_bottom_row(canvas, patch, dest_x=dest_x, dest_y=dest_y)


_SVG_BYTES: dict[str, bytes] = {}
_IDLE_KB_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_IDLE_CHARS_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_IDLE_ROW_CACHE: dict[tuple[object, ...], np.ndarray | None] = {}
_FOCUS_PATCH_CACHE: dict[tuple[object, ...], tuple[int, int, np.ndarray]] = {}
_LAST_FULL_CACHE: dict[tuple[object, ...], np.ndarray] = {}
_IDLE_KB_CACHE_MAX = 12
_FOCUS_PATCH_CACHE_MAX = 320
_LAST_FULL_CACHE_MAX = 4


def _keyboard_svg_root(path: Path) -> ET.Element:
    key = str(path)
    data = _SVG_BYTES.get(key)
    if data is None:
        data = path.read_bytes()
        _SVG_BYTES[key] = data
    return ET.fromstring(data)


def _keyboard_layout_key(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
) -> tuple[object, ...]:
    th = state.theme
    return (
        state.mode,
        str(Path(assets_dir) if assets_dir is not None else ""),
        str(th.ui or ""),
        str(th.selected or ""),
        str(th.deselected or ""),
        str(th.inactive or ""),
        str(th.accent or ""),
        bool(state.supports_lowercase),
        bool(state.password_mask),
        bool(state.include_bottom_row),
        str(state.target or ""),
    )


def _bottom_button_ids(state: KeyboardState) -> set[str]:
    if not state.include_bottom_row:
        return set()
    return {k.button_id for k in _bottom_row_keys(state.mode)}


def _blit_row_on_canvas(canvas: np.ndarray, row: np.ndarray | None) -> None:
    if row is None or not row.size:
        return
    cx, cy = _cluster_xy()
    cw, _ch = _cluster_wh()
    dest_x = cx + int(round((cw - row.shape[1]) * 0.5))
    _blit_bottom_row(canvas, row, dest_x=dest_x, dest_y=cy)


def _compose_adjacent_from_idle(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
    layout: tuple[object, ...],
    idle: np.ndarray,
    focused_id: str,
) -> np.ndarray | None:
    """Redraw only the chars layer or the bottom-row layer that contains focus."""
    if not focused_id:
        return idle
    bottom_ids = _bottom_button_ids(state)
    if focused_id in bottom_ids:
        chars = _IDLE_CHARS_CACHE.get(layout)
        if chars is None:
            return None
        row = _rasterize_bottom_row(
            state, assets_dir=assets_dir, focused_button_id=focused_id
        )
        out = chars.copy()
        _blit_row_on_canvas(out, row)
        return out
    chars = _rasterize_keyboard_chars(
        state, assets_dir=assets_dir, focused_button_id=focused_id
    )
    row = _IDLE_ROW_CACHE.get(layout)
    if row is None and layout not in _IDLE_ROW_CACHE:
        return None
    out = chars
    _blit_row_on_canvas(out, row)
    return out


def _store_idle_keyboard(key: tuple[object, ...], frame: np.ndarray) -> None:
    if key in _IDLE_KB_CACHE:
        return
    _IDLE_KB_CACHE[key] = frame
    if len(_IDLE_KB_CACHE) > _IDLE_KB_CACHE_MAX:
        oldest = next(iter(_IDLE_KB_CACHE))
        if oldest != key:
            _IDLE_KB_CACHE.pop(oldest, None)


def _store_focus_patch(
    key: tuple[object, ...], y: int, x: int, patch: np.ndarray
) -> None:
    if key in _FOCUS_PATCH_CACHE:
        return
    _FOCUS_PATCH_CACHE[key] = (int(y), int(x), patch)
    if len(_FOCUS_PATCH_CACHE) > _FOCUS_PATCH_CACHE_MAX:
        oldest = next(iter(_FOCUS_PATCH_CACHE))
        if oldest != key:
            _FOCUS_PATCH_CACHE.pop(oldest, None)


def _diff_focus_patch(
    idle: np.ndarray, focused: np.ndarray
) -> tuple[int, int, np.ndarray]:
    """Tight bbox of pixels that change when a key is selected."""
    if idle.shape != focused.shape:
        return (0, 0, focused)
    diff = np.any(idle != focused, axis=2)
    if not np.any(diff):
        return (0, 0, focused[0:1, 0:1].copy())
    ys = np.flatnonzero(np.any(diff, axis=1))
    xs = np.flatnonzero(np.any(diff, axis=0))
    y0 = max(0, int(ys[0]) - 1)
    y1 = min(int(focused.shape[0]), int(ys[-1]) + 2)
    x0 = max(0, int(xs[0]) - 1)
    x1 = min(int(focused.shape[1]), int(xs[-1]) + 2)
    return (y0, x0, focused[y0:y1, x0:x1].copy())


def _stamp_focus_patch(
    idle: np.ndarray, y: int, x: int, patch: np.ndarray
) -> np.ndarray:
    out = idle.copy()
    h, w = patch.shape[:2]
    y1 = min(out.shape[0], y + h)
    x1 = min(out.shape[1], x + w)
    if y1 <= y or x1 <= x:
        return out
    out[y:y1, x:x1] = patch[: y1 - y, : x1 - x]
    return out


def _store_last_full(key: tuple[object, ...], frame: np.ndarray) -> None:
    _LAST_FULL_CACHE.pop(key, None)
    _LAST_FULL_CACHE[key] = frame
    if len(_LAST_FULL_CACHE) > _LAST_FULL_CACHE_MAX:
        oldest = next(iter(_LAST_FULL_CACHE))
        _LAST_FULL_CACHE.pop(oldest, None)


def clear_keyboard_render_caches() -> None:
    """Drop idle/focus bitmaps (tests / theme reloads). SVG bytes stay cached."""
    _IDLE_KB_CACHE.clear()
    _IDLE_CHARS_CACHE.clear()
    _IDLE_ROW_CACHE.clear()
    _FOCUS_PATCH_CACHE.clear()
    _LAST_FULL_CACHE.clear()


def _resolved_focus_button_id(
    state: KeyboardState, focused_button_id: str | None
) -> str:
    if focused_button_id is not None:
        return str(focused_button_id)
    if not state.focus_ring:
        return ""
    return state.focused.button_id


def _rasterize_keyboard_chars(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
    focused_button_id: str | None = None,
) -> np.ndarray:
    canvas = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    path = keyboard_svg_path(_MODE_SVG[state.mode], assets_dir=assets_dir)
    if not path.is_file():
        return canvas

    root = _keyboard_svg_root(path)
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

    focused = _resolved_focus_button_id(state, focused_button_id)
    char_focus = focused if focused in button_ids else ""
    apply_keyboard_selection(
        root,
        focused_button_id=char_focus,
        theme=state.theme,
        button_ids=button_ids,
        icon_ids_by_button=icon_map,
        muted_deselected=(state.mode == KeyboardMode.YES_NO),
    )

    if pad_mode:
        _center_integrated_pad_labels(root)
        vb = viewbox_from_root(root)
        content_w = max(1, int(round(vb[2])))
        layout = _integrated_pad_layout(vb, content_w=content_w)
        root.set("overflow", "visible")
        pad = rasterize_settings_svg_bgra(
            root,
            width=layout.out_w,
            height=layout.out_h,
            view_box=layout.padded_vb,
            font_mode="keyboard",
        )
        pad = _scale_keyboard_patch(pad)
        dest_x = max(0, (DESIGN_W - pad.shape[1]) // 2)
        if state.mode == KeyboardMode.YES_NO:
            dest_y = max(0, (DESIGN_H - pad.shape[0]) // 2)
        else:
            dest_y = int(_cluster_xy()[1]) - int(round(layout.pad_px * _CLUSTER_SCALE))
        _blit_bottom_row(canvas, pad, dest_x=dest_x, dest_y=dest_y)
        return canvas

    if full_ip:
        for hid in _IP_HIDDEN_IDS:
            _remove_logical(root, hid)
        _center_integrated_pad_labels(root)
        vx, vy, vw, vh = _pad_content_viewbox(root)
        root.set("viewBox", f"{vx} {vy} {vw} {vh}")
        patch, vb = _rasterize_svg_patch(root)
        _place_in_cluster(canvas, patch, vb, state.mode)
        return canvas

    if state.mode in (KeyboardMode.QWERTY_LOWER, KeyboardMode.QWERTY_UPPER):
        _align_qwerty_to_cluster(root)
    patch, vb = _rasterize_svg_patch(root)
    _place_in_cluster(canvas, patch, vb, state.mode)
    return canvas


def _rasterize_bottom_row(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
    focused_button_id: str | None = None,
) -> np.ndarray | None:
    if not state.include_bottom_row:
        return None
    path = keyboard_svg_path(_BOTTOM_ROW_SVG, assets_dir=assets_dir)
    if not path.is_file():
        return None
    root = _keyboard_svg_root(path)
    apply_bottom_row_mode_icons(root, state.mode, uppercase_only=not state.supports_lowercase)
    _apply_native_bottom_row_labels(root, state.mode)

    bottom = _bottom_row_keys(state.mode)
    button_ids = {k.button_id for k in bottom}
    icon_map = {k.button_id: k.icon_ids for k in bottom if k.icon_ids}
    focused = _resolved_focus_button_id(state, focused_button_id)
    row_focus = focused if focused in button_ids else ""
    apply_keyboard_selection(
        root,
        focused_button_id=row_focus,
        theme=state.theme,
        button_ids=button_ids,
        icon_ids_by_button=icon_map,
    )
    _prune_display_none(root)
    patch, _vb = _rasterize_svg_patch(root)
    return _scale_keyboard_patch(patch)


def _composite_keyboard_layers(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None,
    focused_button_id: str,
) -> np.ndarray:
    canvas = _rasterize_keyboard_chars(
        state, assets_dir=assets_dir, focused_button_id=focused_button_id
    )
    row = _rasterize_bottom_row(
        state, assets_dir=assets_dir, focused_button_id=focused_button_id
    )
    if row is not None and row.size:
        cx, cy = _cluster_xy()
        cw, _ch = _cluster_wh()
        dest_x = cx + int(round((cw - row.shape[1]) * 0.5))
        dest_y = cy
        _blit_bottom_row(canvas, row, dest_x=dest_x, dest_y=dest_y)
    return canvas


def warm_keyboard_idle(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    """Ensure the unselected keyboard bitmap is cached; return it (not a copy)."""
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)
    layout = _keyboard_layout_key(state, assets_dir=assets_dir)
    idle = _IDLE_KB_CACHE.get(layout)
    if idle is None:
        chars = _rasterize_keyboard_chars(
            state, assets_dir=assets_dir, focused_button_id=""
        )
        row = _rasterize_bottom_row(
            state, assets_dir=assets_dir, focused_button_id=""
        )
        _IDLE_CHARS_CACHE[layout] = chars
        _IDLE_ROW_CACHE[layout] = row
        idle = chars if row is None else chars.copy()
        _blit_row_on_canvas(idle, row)
        _store_idle_keyboard(layout, idle)
    return idle


def keyboard_overlay_cached(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None = None,
) -> bool:
    """True when Left/Right can reuse idle + a focus patch (no SVG raster)."""
    if not state.focus_ring:
        return False
    layout = _keyboard_layout_key(state, assets_dir=assets_dir)
    if layout not in _IDLE_KB_CACHE:
        return False
    focused_id = state.focused.button_id
    if not focused_id:
        return True
    return (layout + (focused_id,)) in _FOCUS_PATCH_CACHE


def render_keyboard_bgra(
    state: KeyboardState,
    *,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    """Composite character keys + shared bottom row onto the 1280×800 canvas.

    Left/Right reuses an idle raster plus a small selected-key patch so navigation
    does not re-run PyMuPDF on the full SVG.
    """
    if not state.focus_ring:
        state.rebuild_focus_ring(assets_dir=assets_dir)

    layout = _keyboard_layout_key(state, assets_dir=assets_dir)
    focused_id = state.focused.button_id if state.focus_ring else ""
    full_key = layout + (focused_id,)
    idle = _IDLE_KB_CACHE.get(layout)
    cached_full = _LAST_FULL_CACHE.get(full_key)
    if cached_full is not None:
        if idle is not None and focused_id and full_key not in _FOCUS_PATCH_CACHE:
            y, x, patch = _diff_focus_patch(idle, cached_full)
            _store_focus_patch(full_key, y, x, patch)
        return cached_full
    if idle is not None:
        if not focused_id:
            return idle
        hit = _FOCUS_PATCH_CACHE.get(full_key)
        if hit is not None:
            y, x, patch = hit
            out = _stamp_focus_patch(idle, y, x, patch)
            _store_last_full(full_key, out)
            return out
        adjacent = _compose_adjacent_from_idle(
            state,
            assets_dir=assets_dir,
            layout=layout,
            idle=idle,
            focused_id=focused_id,
        )
        if adjacent is not None:
            y, x, patch = _diff_focus_patch(idle, adjacent)
            _store_focus_patch(full_key, y, x, patch)
            _store_last_full(full_key, adjacent)
            return adjacent

    focused = _composite_keyboard_layers(
        state, assets_dir=assets_dir, focused_button_id=focused_id
    )
    _store_last_full(full_key, focused)
    if idle is None:
        if not focused_id:
            _store_idle_keyboard(layout, focused)
        return focused
    if focused_id:
        y, x, patch = _diff_focus_patch(idle, focused)
        _store_focus_patch(full_key, y, x, patch)
    return focused


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
        numeric = _is_numeric_mode(state.mode)
        if not state.supports_lowercase:
            if numeric:
                state.set_mode(KeyboardMode.QWERTY_UPPER, assets_dir=assets_dir)
            else:
                state.set_mode(KeyboardMode.NUMERIC_ALL, assets_dir=assets_dir)
        elif numeric:
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
    "clear_keyboard_render_caches",
    "discover_char_keys",
    "discover_integrated_pad_keys",
    "discover_yes_no_keys",
    "focus_first_letter",
    "focus_yes_no_yes",
    "focus_keyboard_go",
    "focus_numeric_one",
    "keyboard_overlay_cached",
    "open_keyboard",
    "render_keyboard_bgra",
    "warm_keyboard_idle",
]
