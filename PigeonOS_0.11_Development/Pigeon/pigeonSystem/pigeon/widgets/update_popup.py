"""
GitHub update confirmation popup — ``settings_0.8/settings_update_popup.svg``.

Opened from pigeon device settings when **update** is activated. Layer ids match
the Illustrator artboard (including typos ``pop_uo_*`` / ``pop_)up_*``).
"""

from __future__ import annotations

import copy
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W
from pigeon.widgets.main_settings import (
    COLOR_UI_DEFAULT,
    MainSettingsState,
    SettingsTheme,
    _apply_accent_paint,
    _composite_bgra_over_bgra,
    _find_by_logical_id,
    _prune_display_none,
    _set_paint,
    _set_text_content,
    _set_visible,
)
from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra, viewbox_from_root

SVG_NS = "http://www.w3.org/2000/svg"

# Illustrator OCG / layer names (screenshot + update.ai).
ID_BUTTON_GROUP = "pop_up_update_button_group"
ID_BUTTON = "pop_up_update_button"
ID_ACCENT = "pop_up_update_accent"
ID_LATER_NOW_GROUP = "pop_up_update_later_and_now_group"
ID_LATER_GROUP = "pop_uo_update__later_group"  # typo: pop_uo + double underscore
ID_NOW_GROUP = "pop_uo_update_now_group"  # typo: pop_uo
ID_LATER_BUTTON = "pop_uo_update_later_button"
ID_LATER_ACCENT = "pop_uo_update_later_accent"
ID_LATER_TEXT = "pop_uo_update_later_text"
ID_NOW_BUTTON = "pop_uo_update_now_button"
ID_NOW_ACCENT = "pop_uo_update_now_accent"
ID_NOW_TEXT = "pop_uo_update_now_text"
ID_CHANGES_GROUP = "pop_up_update_changes_text"
ID_CURRENT_TO_NEW = "pop_up_update_current_to_new_group"
ID_CURRENT_TEXT = "pop_up_update_current_text"
ID_NEW_TEXT = "pop_up_update_new_text"
ID_ARROW = "pop_up_update_arrow_icon"
ID_PIGEONOS_GROUP = "pop_up_update_pigeonos_stroke_text_group"
ID_PIGEONOS_STROKE = "pop_up_update_pigeonos_stroke_text"
ID_PIGEONOS_FILL = "pop_up_update_pigeonos_fill_text"
ID_UPDATE_LABEL_GROUP = "pop_)up_update_update_text"  # typo: pop_)up
ID_UPDATE_LABEL = "pop_up_update_update_text"

DEFAULT_CHANGELOG = "bug fixes and optimizations."
UP_TO_DATE_CHANGELOG = "You're on the current version of PigeonOS"
PIGEONOS_LABEL = "PigeonOS"

_UPDATE_FOCUS_AVAILABLE: tuple[str, ...] = ("later", "now")
_UPDATE_FOCUS_CURRENT: tuple[str, ...] = ("now",)

_SVG_TREE_TEMPLATES: dict[tuple[str, int, int], ET.Element] = {}
_SVG_TREE_TEMPLATE_MAX = 4

# Geometry from 2026-07-27 Illustrator export — center controls when LATER hides.
# Card widened (290.424 → 450) so the up-to-date changelog fits at 18px.
_LATER_NOW_ROW = (313.104, 335.039, 494.502 - 313.104, 372.968 - 335.039)
_NOW_BTN = (435.066, 335.039, 59.436, 37.929)
_LATER_BTN = (322.664, 335.039, 59.436, 37.929)
_VERSION_BAND = (184.005, 180.0, 450.0, 40.0)
_CURRENT_DEFAULT_X = 340.0
_CURRENT_ORIGIN_Y = 201.255
_NEW_DEFAULT_X = 470.0
_NEW_ORIGIN_Y = 201.255
_NOW_TEXT_Y = 363.223
_LATER_TEXT_Y = 363.223
_LATER_DEFAULT_X = 352.382
_NOW_DEFAULT_X = 464.784
# Deselected LATER/NOW label (matches export fill #6E6E6E).
_CHOICE_TEXT_MUTED = "#6E6E6E"
_CHOICE_BUTTON_FILL = "#202020"

# Progress bar (above LATER/NOW row, same horizontal span as the button group).
_PROGRESS_X = 322.664
_PROGRESS_Y = 318.0
_PROGRESS_W = 494.502 - 322.664
_PROGRESS_H = 12.0
_PROGRESS_RX = 6
_PROGRESS_TRACK_BGR = (32, 32, 32)
_PROGRESS_FILL_BGR = (255, 255, 255)


def default_update_popup_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_UPDATE_POPUP_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "settings_update_popup.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "settings_update_popup.svg"


def update_popup_focus_ring(*, update_available: bool) -> tuple[str, ...]:
    return _UPDATE_FOCUS_AVAILABLE if update_available else _UPDATE_FOCUS_CURRENT


def _svg_tree_from_path(path: Path) -> ET.Element:
    try:
        st = path.stat()
        key = (str(path.resolve()), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        key = (str(path), 0, 0)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        root = ET.parse(path).getroot()
        root.set("viewBox", "0 0 800 480")
        root.set("width", str(DESIGN_W))
        root.set("height", str(DESIGN_H))
        if len(_SVG_TREE_TEMPLATES) >= _SVG_TREE_TEMPLATE_MAX:
            _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def _sync_rect_geometry(dst: ET.Element | None, src: ET.Element | None) -> None:
    """Keep accent stroke geometry identical to the button fill rect."""
    if dst is None or src is None:
        return
    for attr in ("x", "y", "width", "height", "rx", "ry"):
        val = src.get(attr)
        if val is not None:
            dst.set(attr, val)


def _set_text_translate(
    el: ET.Element | None,
    *,
    x: float,
    y: float,
    anchor: str = "middle",
) -> None:
    if el is None:
        return
    texts = [el] if el.tag.endswith("text") else [n for n in el.iter() if n.tag.endswith("text")]
    for node in texts:
        node.set("transform", f"translate({x:.3f} {y:.3f})")
        node.set("text-anchor", anchor)


def _format_version(ver: str) -> str:
    v = (ver or "").strip()
    if not v:
        return ""
    if v.lower().startswith("v"):
        return v
    return v


def _sync_pigeonos_text(root: ET.Element, label: str) -> None:
    stroke = _find_by_logical_id(root, ID_PIGEONOS_STROKE)
    fill = _find_by_logical_id(root, ID_PIGEONOS_FILL)
    _set_text_content(stroke, label)
    _set_text_content(fill, label)


def _paint_choice_label(
    text: ET.Element | None,
    *,
    selected: bool,
    theme: SettingsTheme,
) -> None:
    """Black buttons: white label when focused, grey when not."""
    if text is None:
        return
    color = theme.selected if selected else _CHOICE_TEXT_MUTED
    nodes = [text] if text.tag.endswith("text") else [
        n for n in text.iter() if n.tag.endswith("text")
    ]
    if not nodes:
        nodes = [text]
    for node in nodes:
        _set_paint(node, fill=color)


def _apply_choice_chrome(
    root: ET.Element,
    *,
    focused: str,
    theme: SettingsTheme,
    later_enabled: bool,
) -> None:
    for choice, button_id, accent_id, group_id, text_id in (
        ("later", ID_LATER_BUTTON, ID_LATER_ACCENT, ID_LATER_GROUP, ID_LATER_TEXT),
        ("now", ID_NOW_BUTTON, ID_NOW_ACCENT, ID_NOW_GROUP, ID_NOW_TEXT),
    ):
        enabled = choice == "now" or later_enabled
        selected = enabled and focused == choice
        btn = _find_by_logical_id(root, button_id)
        accent = _find_by_logical_id(root, accent_id)
        group = _find_by_logical_id(root, group_id)
        text = _find_by_logical_id(root, text_id)
        if not enabled:
            _set_visible(group, False)
            continue
        _set_visible(group, True)
        # Both choices stay dark; focus is label color (not white fill).
        if btn is not None:
            targets = [btn]
            targets.extend(
                n
                for n in btn.iter()
                if n is not btn and (n.tag.endswith("rect") or n.tag.endswith("path"))
            )
            for node in targets:
                if node.tag.endswith("rect") or node.tag.endswith("path") or node is btn:
                    if node.tag.endswith("rect") or node.tag.endswith("path"):
                        _set_paint(
                            node,
                            fill=_CHOICE_BUTTON_FILL,
                            stroke="#000000",
                        )
        _paint_choice_label(text, selected=selected, theme=theme)
        if accent is not None:
            # Thin white outline on both (matches export).
            _set_paint(accent, fill="none", stroke=theme.selected)
            _sync_rect_geometry(accent, btn)


def _layout_available(root: ET.Element, state: MainSettingsState) -> None:
    current = _format_version(state.update_local_version or state.version_string)
    remote = _format_version(state.update_remote_version or "")
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL_GROUP), True)
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL), True)
    _set_visible(_find_by_logical_id(root, ID_ARROW), True)
    _set_visible(_find_by_logical_id(root, ID_NEW_TEXT), True)
    _set_visible(_find_by_logical_id(root, ID_LATER_GROUP), True)
    now_group = _find_by_logical_id(root, ID_NOW_GROUP)
    if now_group is not None:
        # Clear any up-to-date centering transform (buttons are paths).
        if "transform" in now_group.attrib:
            del now_group.attrib["transform"]
    _set_text_content(_find_by_logical_id(root, ID_CURRENT_TEXT), current)
    _set_text_content(_find_by_logical_id(root, ID_NEW_TEXT), remote or "—")
    _set_text_translate(
        _find_by_logical_id(root, ID_CURRENT_TEXT),
        x=_CURRENT_DEFAULT_X,
        y=_CURRENT_ORIGIN_Y,
    )
    _set_text_translate(
        _find_by_logical_id(root, ID_NEW_TEXT),
        x=_NEW_DEFAULT_X,
        y=_NEW_ORIGIN_Y,
    )
    _set_text_translate(
        _find_by_logical_id(root, ID_LATER_TEXT),
        x=_LATER_DEFAULT_X,
        y=_LATER_TEXT_Y,
    )
    now_text = _find_by_logical_id(root, ID_NOW_TEXT)
    _set_text_content(now_text, "NOW")
    _set_text_translate(
        now_text,
        x=_NOW_DEFAULT_X,
        y=_NOW_TEXT_Y,
    )
    notes = (state.update_changelog or "").strip() or DEFAULT_CHANGELOG
    _set_text_content(_find_by_logical_id(root, ID_CHANGES_GROUP), notes)


def _layout_up_to_date(root: ET.Element, state: MainSettingsState) -> None:
    current = _format_version(state.update_local_version or state.version_string)
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL_GROUP), False)
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL), False)
    _set_visible(_find_by_logical_id(root, ID_ARROW), False)
    _set_visible(_find_by_logical_id(root, ID_NEW_TEXT), False)
    _set_visible(_find_by_logical_id(root, ID_LATER_GROUP), False)

    # Center current version in the version band (current→new row rectangle).
    vx, _vy, vw, _vh = _VERSION_BAND
    _set_text_translate(
        _find_by_logical_id(root, ID_CURRENT_TEXT),
        x=vx + vw / 2.0,
        y=_CURRENT_ORIGIN_Y,
    )
    _set_text_content(_find_by_logical_id(root, ID_CURRENT_TEXT), current)

    # Center OK (shared now-button layer) within the later+now row bounds.
    # Buttons are paths — nudge the whole NOW group rather than rewriting x/y.
    rx, _ry, rw, _rh = _LATER_NOW_ROW
    _nw = _NOW_BTN[2]
    cx = rx + (rw - _nw) / 2.0
    dx = cx - _NOW_BTN[0]
    now_group = _find_by_logical_id(root, ID_NOW_GROUP)
    if now_group is not None:
        now_group.set("transform", f"translate({dx:.3f} 0)")
    now_text = _find_by_logical_id(root, ID_NOW_TEXT)
    _set_text_content(now_text, "OK")
    # Pillow text overlay ignores parent ``<g transform>`` — use the absolute
    # centered-button midpoint, not the pre-nudge NOW label x.
    _set_text_translate(
        now_text,
        x=cx + _nw / 2.0,
        y=_NOW_TEXT_Y,
    )

    notes = (state.update_changelog or "").strip() or UP_TO_DATE_CHANGELOG
    _set_text_content(_find_by_logical_id(root, ID_CHANGES_GROUP), notes)


def _layout_applying(root: ET.Element, state: MainSettingsState) -> None:
    """Version band + status copy; hide LATER/NOW while the progress bar draws."""
    current = _format_version(state.update_local_version or state.version_string)
    remote = _format_version(state.update_remote_version or "")
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL_GROUP), True)
    _set_visible(_find_by_logical_id(root, ID_UPDATE_LABEL), True)
    _set_visible(_find_by_logical_id(root, ID_ARROW), bool(remote))
    _set_visible(_find_by_logical_id(root, ID_NEW_TEXT), bool(remote))
    _set_visible(_find_by_logical_id(root, ID_LATER_GROUP), False)
    _set_visible(_find_by_logical_id(root, ID_NOW_GROUP), False)
    _set_text_content(_find_by_logical_id(root, ID_CURRENT_TEXT), current)
    if remote:
        _set_text_content(_find_by_logical_id(root, ID_NEW_TEXT), remote)
        _set_text_translate(
            _find_by_logical_id(root, ID_CURRENT_TEXT),
            x=_CURRENT_DEFAULT_X,
            y=_CURRENT_ORIGIN_Y,
        )
        _set_text_translate(
            _find_by_logical_id(root, ID_NEW_TEXT),
            x=_NEW_DEFAULT_X,
            y=_NEW_ORIGIN_Y,
        )
    else:
        vx, _vy, vw, _vh = _VERSION_BAND
        _set_text_translate(
            _find_by_logical_id(root, ID_CURRENT_TEXT),
            x=vx + vw / 2.0,
            y=_CURRENT_ORIGIN_Y,
        )
    status = (state.update_changelog or "").strip() or "Downloading…"
    pct = int(round(max(0.0, min(1.0, float(state.update_progress))) * 100.0))
    if pct > 0 and "%" not in status:
        status = f"{status}  {pct}%"
    _set_text_content(_find_by_logical_id(root, ID_CHANGES_GROUP), status[:96])


def _draw_progress_bar_bgra(bgra: np.ndarray, *, fraction: float) -> None:
    """Opaque track + white fill inside the popup card (above the button row)."""
    if bgra is None or bgra.size == 0:
        return
    x = int(round(_PROGRESS_X))
    y = int(round(_PROGRESS_Y))
    w = max(1, int(round(_PROGRESS_W)))
    h = max(1, int(round(_PROGRESS_H)))
    frac = max(0.0, min(1.0, float(fraction)))
    track = np.zeros((h, w, 4), dtype=np.uint8)
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.rectangle(mask, (0, 0), (w - 1, h - 1), 255, -1)
    if _PROGRESS_RX > 0:
        # Soften corners via elliptical covers (simple rounded look).
        r = min(_PROGRESS_RX, h // 2, w // 2)
        mask[:] = 0
        cv2.rectangle(mask, (r, 0), (w - r - 1, h - 1), 255, -1)
        cv2.rectangle(mask, (0, r), (w - 1, h - r - 1), 255, -1)
        cv2.circle(mask, (r, r), r, 255, -1, lineType=cv2.LINE_AA)
        cv2.circle(mask, (w - r - 1, r), r, 255, -1, lineType=cv2.LINE_AA)
        cv2.circle(mask, (r, h - r - 1), r, 255, -1, lineType=cv2.LINE_AA)
        cv2.circle(mask, (w - r - 1, h - r - 1), r, 255, -1, lineType=cv2.LINE_AA)
    track[mask > 0, :3] = _PROGRESS_TRACK_BGR
    track[mask > 0, 3] = 255
    fill_w = max(0, int(round(w * frac)))
    if fill_w > 0:
        fill_mask = mask[:, :fill_w]
        track[:, :fill_w][fill_mask > 0, :3] = _PROGRESS_FILL_BGR
        track[:, :fill_w][fill_mask > 0, 3] = 255
    x0, y0 = max(0, x), max(0, y)
    x1 = min(int(bgra.shape[1]), x + w)
    y1 = min(int(bgra.shape[0]), y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sx0, sy0 = x0 - x, y0 - y
    roi = bgra[y0:y1, x0:x1]
    src = track[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
    alpha = src[:, :, 3:4].astype(np.float32) / 255.0
    roi[:, :, :3] = (
        src[:, :, :3].astype(np.float32) * alpha
        + roi[:, :, :3].astype(np.float32) * (1.0 - alpha)
    ).astype(np.uint8)
    if roi.shape[2] >= 4:
        roi[:, :, 3] = np.maximum(roi[:, :, 3], src[:, :, 3])


def apply_update_popup_svg_state(root: ET.Element, state: MainSettingsState) -> None:
    theme = state.theme
    ui = theme.ui or COLOR_UI_DEFAULT
    button = _find_by_logical_id(root, ID_BUTTON)
    accent = _find_by_logical_id(root, ID_ACCENT)
    if button is not None:
        _set_paint(button, fill=ui)
    if accent is not None:
        _apply_accent_paint(accent, theme.accent)
        _sync_rect_geometry(accent, button)

    _sync_pigeonos_text(root, PIGEONOS_LABEL)

    available = bool(state.update_available)
    if state.update_applying:
        _layout_applying(root, state)
        return
    if state.update_checking:
        # Always show the in-progress check UI (never stale LATER/NOW from cache).
        _layout_up_to_date(root, state)
        _set_text_content(
            _find_by_logical_id(root, ID_CHANGES_GROUP),
            (state.update_changelog or "").strip() or "Checking GitHub for updates…",
        )
        later_enabled = False
        focused = "now"
    elif state.update_error and not available:
        _layout_up_to_date(root, state)
        err = str(state.update_error).strip()
        _set_text_content(
            _find_by_logical_id(root, ID_CHANGES_GROUP),
            err[:96] if err else "Could not check for updates.",
        )
        later_enabled = False
        focused = "now"
    elif available:
        _layout_available(root, state)
        later_enabled = True
        ring = update_popup_focus_ring(update_available=True)
        idx = int(state.update_popup_focus_index) % len(ring)
        focused = ring[idx]
    else:
        _layout_up_to_date(root, state)
        later_enabled = False
        focused = "now"

    _apply_choice_chrome(
        root,
        focused=focused,
        theme=theme,
        later_enabled=later_enabled,
    )


def render_update_popup_bgra(
    state: MainSettingsState | None = None,
    *,
    svg_path: Path | str | None = None,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    if svg_path is not None:
        path = Path(svg_path)
    else:
        path = default_update_popup_svg_path(assets_dir)
    if not path.is_file():
        raise FileNotFoundError(f"update popup SVG not found: {path}")

    st = state if state is not None else MainSettingsState()
    root = _svg_tree_from_path(path)
    apply_update_popup_svg_state(root, st)
    _prune_display_none(root)
    vb = viewbox_from_root(root)
    frame = rasterize_settings_svg_bgra(
        root,
        width=DESIGN_W,
        height=DESIGN_H,
        view_box=vb,
        font_mode="update_popup",
    )
    if st.update_applying:
        _draw_progress_bar_bgra(frame, fraction=float(st.update_progress))
    return frame


def composite_update_popup_over_bgra(
    base_bgra: np.ndarray,
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None = None,
) -> np.ndarray:
    overlay = render_update_popup_bgra(state, assets_dir=assets_dir)
    return _composite_bgra_over_bgra(base_bgra, overlay)


__all__ = [
    "DEFAULT_CHANGELOG",
    "UP_TO_DATE_CHANGELOG",
    "apply_update_popup_svg_state",
    "composite_update_popup_over_bgra",
    "default_update_popup_svg_path",
    "render_update_popup_bgra",
    "update_popup_focus_ring",
]
