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
_COLOR_ZONE_FILL = "#000000"
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
    "zone3": ("volume", "clock", "artwork"),
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
_ZONE_INSET = 12.0
_WIDGET_FIT = 0.90


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
        if z1 in ("clock", "clock_16x9"):
            return "clock"
        if z1 == "weather":
            return "weather"
        if z1 in _ARTWORK_KEYS or not z1:
            return "artwork"
        return "artwork"
    if zone_id == "zone3":
        if z3 == "clock":
            return "clock"
        if z3 == "poster":
            return "artwork"
        return "volume"
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
        write_now_playing_zone_widgets(out, content_mode="music")
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
            current[0], current[1] = "clock_16x9", ""
        elif widget_id == "weather":
            current[0], current[1] = "weather", ""
        else:
            return False
    elif zone == "zone3":
        if widget_id == "clock":
            current[2] = "clock"
        elif widget_id == "artwork":
            current[2] = "poster"
        else:
            current[2] = "volume"
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
    if persist:
        write_now_playing_zone_widgets(
            state.preferences_zone_widgets, persist=True, content_mode="music"
        )
    return True


def persist_widgets_layout(state: MainSettingsState) -> None:
    from pigeon.widgets.preferences_settings import write_now_playing_zone_widgets

    zones = _zone_tuple(state)
    write_now_playing_zone_widgets(zones, persist=True)
    write_now_playing_zone_widgets(zones, persist=True, content_mode="music")


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
            fill=_COLOR_ZONE_FILL,
            stroke=_COLOR_WHITE if on else _COLOR_ZONE_IDLE,
        )
        rect.set("fill-opacity", "1")
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


def _contain_paste(
    canvas: np.ndarray,
    patch: np.ndarray | None,
    box: tuple[int, int, int, int],
    *,
    fit: float = _WIDGET_FIT,
) -> None:
    """Letterbox ``patch`` into ``box``, shrunk so the black well still shows."""
    if patch is None or patch.size == 0 or patch.ndim < 3:
        return
    import cv2

    from pigeon.compositing import cv_resize_interp
    from pigeon.widgets.view_circles import _ink_crop_bgra, _paste_patch_bgra

    cropped = _ink_crop_bgra(patch, pad=2)
    if cropped is None or cropped.size == 0:
        return
    x, y, w, h = box
    fw = max(1, int(round(float(w) * max(0.2, min(1.0, float(fit))))))
    fh = max(1, int(round(float(h) * max(0.2, min(1.0, float(fit))))))
    ph, pw = int(cropped.shape[0]), int(cropped.shape[1])
    if pw < 1 or ph < 1:
        return
    scale = min(fw / float(pw), fh / float(ph))
    nw = max(1, int(round(pw * scale)))
    nh = max(1, int(round(ph * scale)))
    resized = cv2.resize(
        cropped, (nw, nh), interpolation=cv_resize_interp(pw, ph, nw, nh)
    )
    ox = int(x) + (int(w) - nw) // 2
    oy = int(y) + (int(h) - nh) // 2
    _paste_patch_bgra(canvas, resized, ox, oy)


def _preview_is_live(state: MainSettingsState | None) -> bool:
    return bool(state is not None and getattr(state, "preferences_live_content", False))


def _preview_text(
    state: MainSettingsState | None,
    attr: str,
    demo: str,
) -> str:
    raw = str(getattr(state, attr, None) or "").strip() if state is not None else ""
    if raw:
        return raw
    return "" if _preview_is_live(state) else demo


def _preview_progress(state: MainSettingsState | None) -> float:
    raw = getattr(state, "preferences_np_progress", None) if state is not None else None
    if raw is not None:
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            pass
    return 0.0 if _preview_is_live(state) else _DEMO_PROGRESS


def _preview_volume_text(state: MainSettingsState | None) -> str:
    from pigeon.widgets.playback_overlay import volume_widget_value_text

    if _preview_is_live(state):
        return volume_widget_value_text(
            getattr(state, "preferences_volume", None) or ""
        )
    raw = str(getattr(state, "preferences_volume", None) or "").strip() if state else ""
    if raw:
        return volume_widget_value_text(raw)
    return _DEMO_VOLUME


def _preview_volume_fraction(state: MainSettingsState | None) -> float:
    if _preview_is_live(state):
        raw = getattr(state, "preferences_volume_fraction", None)
        if raw is not None:
            try:
                return max(0.0, min(1.0, float(raw)))
            except (TypeError, ValueError):
                pass
        vol = str(getattr(state, "preferences_volume", None) or "")
        if vol:
            try:
                from pigeon.widgets.playback_overlay import (
                    volume_fraction_from_display_line,
                )

                return float(volume_fraction_from_display_line(vol))
            except Exception:
                pass
        return 0.0
    return _DEMO_VOLUME_FRAC


def _preview_cast(state: MainSettingsState | None) -> list[tuple[str, str]]:
    if _preview_is_live(state):
        return list(getattr(state, "preferences_cast", None) or ())
    rows = getattr(state, "preferences_cast", None) if state is not None else None
    if rows:
        return list(rows)
    return list(_DEMO_CAST)


def _poster_source_bgra(
    state: MainSettingsState | None,
    assets_dir: Path | str | None,
) -> np.ndarray | None:
    """Live TMDb / album art when playing; otherwise the prefs demo poster."""
    live = getattr(state, "preferences_poster_bgra", None) if state is not None else None
    if live is not None and getattr(live, "size", 0):
        return live
    try:
        from pigeon.widgets.preferences_settings import (
            _prefs_poster_masters,
            default_preferences_svg_path,
        )

        masters = _prefs_poster_masters(default_preferences_svg_path(assets_dir))
        for zone in (3, 2, 1):
            src = masters.get(zone)
            if src is not None and getattr(src, "size", 0):
                return src
    except Exception:
        return None
    return None


def _render_poster_artwork_widget_bgra(
    state: MainSettingsState | None,
    assets_dir: Path | str | None,
) -> np.ndarray:
    """Portrait 2×3 (or music 1×1) TMDb poster with rounded corners."""
    from pigeon.np_layout import NOW_PLAYING_ZONES, POSTER_1X1_LOCAL, POSTER_2X3_LOCAL
    from pigeon.widgets.view_circles import (
        _cover_fit_bgra,
        _paste_patch_bgra,
        _rounded_rect_mask,
    )

    mode = str(getattr(state, "preferences_content_mode", None) or "video").lower()
    lx, ly, lw, lh, lrx = (
        POSTER_1X1_LOCAL if mode == "music" else POSTER_2X3_LOCAL
    )
    vw = int(round(NOW_PLAYING_ZONES[3].w))
    vh = int(round(NOW_PLAYING_ZONES[3].h))
    out = np.zeros((vh, vw, 4), dtype=np.uint8)
    src = _poster_source_bgra(state, assets_dir)
    if src is None or not getattr(src, "size", 0):
        return out
    tw = max(1, int(round(lw)))
    th = max(1, int(round(lh)))
    rx = max(1, int(round(lrx)))
    crop = _cover_fit_bgra(src, tw, th)
    if crop.ndim == 3 and crop.shape[2] == 3:
        import cv2

        crop = cv2.cvtColor(crop, cv2.COLOR_BGR2BGRA)
    mask = _rounded_rect_mask(tw, th, rx)
    patch = crop.copy()
    patch[:, :, 3] = np.minimum(patch[:, :, 3], mask)
    _paste_patch_bgra(out, patch, int(round(lx)), int(round(ly)))
    return out


def _render_artwork_widget_bgra(
    state: MainSettingsState | None,
    assets_dir: Path | str | None,
) -> np.ndarray:
    """Same 16×9 ``tt_countdown`` draw path used on now-playing zone 6."""
    from pigeon.design import DESIGN_H, DESIGN_W
    from pigeon.np_layout import NOW_PLAYING_ZONES
    from pigeon.widgets.view_circles import ViewCirclesWidget

    assets = Path(assets_dir) if assets_dir is not None else Path(
        __file__
    ).resolve().parents[3] / "pigeonAssets"
    widget = ViewCirclesWidget(assets_dir=assets)
    remaining = _preview_text(state, "preferences_remaining_text", _DEMO_REMAINING.lstrip("-"))
    title = _preview_text(state, "preferences_song_title", _DEMO_TITLE)
    elapsed = _preview_text(state, "preferences_elapsed_text", _DEMO_ELAPSED)
    progress = _preview_progress(state)
    mode = str(getattr(state, "preferences_content_mode", None) or "video")
    tt = getattr(state, "preferences_tt_bgra", None)
    widget.update_state(
        progress=float(progress),
        elapsed_text=elapsed,
        remaining_text=remaining,
        volume_text=_preview_volume_text(state),
        has_now_playing=True,
        has_position=True,
        content_active=True,
        content_mode=mode,
        tt_bgra=tt if getattr(tt, "size", 0) else None,
        tt_title=title,
        paused=False,
    )
    frame = np.zeros((int(DESIGN_H), int(DESIGN_W), 4), dtype=np.uint8)
    widget._draw_tt_countdown(frame, zone=6, wide=True)
    zx, zy, zw, zh = NOW_PLAYING_ZONES[6].xywh
    return frame[zy : zy + zh, zx : zx + zw].copy()


def _render_volume_widget_bgra(
    assets_dir: Path | str | None,
    state: MainSettingsState | None = None,
) -> np.ndarray | None:
    from pigeon.np_layout import (
        VOLUME_INNER_R,
        VOLUME_LOCAL_CX,
        VOLUME_LOCAL_CY,
        VOLUME_OUTER_R,
        VOLUME_VIEW_H,
        VOLUME_VIEW_W,
    )
    from pigeon.widgets.view_circles import (
        _draw_progress_ring,
        _paste_centered,
        _paste_patch_bgra,
        _rasterize_named_widget,
        _volume_readout_patch,
        np_theme_from_settings,
    )

    vw = int(round(VOLUME_VIEW_W))
    vh = int(round(VOLUME_VIEW_H))
    out = np.zeros((vh, vw, 4), dtype=np.uint8)
    th = np_theme_from_settings()
    try:
        chrome = _rasterize_named_widget(
            assets_dir=assets_dir,
            widget_key="volume",
            dest_w=vw,
            dest_h=vh,
            now=datetime.now(),
            theme=th,
            zone=3,
        )
    except Exception:
        chrome = None
    if chrome is not None and chrome.size:
        _paste_patch_bgra(out, chrome, 0, 0)
    _draw_progress_ring(
        out,
        cx=VOLUME_LOCAL_CX,
        cy=VOLUME_LOCAL_CY,
        outer_r=VOLUME_OUTER_R,
        inner_r=VOLUME_INNER_R,
        fraction=_preview_volume_fraction(state),
        fill_bgr=th.ui_bgr,
        fill_opacity=1.0,
        stroke=0,
    )
    vol_p, _, _ = _volume_readout_patch(
        _preview_volume_text(state),
        inner_r=VOLUME_INNER_R,
        max_size_px=max(12, int(round(VOLUME_INNER_R * 0.9))),
    )
    _paste_centered(out, vol_p, VOLUME_LOCAL_CX, VOLUME_LOCAL_CY)
    return out


def _render_np_clock_widget_bgra(assets_dir: Path | str | None) -> np.ndarray | None:
    from pigeon.np_layout import CLOCK_VIEW_H, CLOCK_VIEW_W, NowPlayingZone
    from pigeon.widgets.view_circles import (
        _clock_include_digital_time,
        _draw_clock_labels_in_zone,
        _paste_patch_bgra,
        _rasterize_named_widget,
        np_theme_from_settings,
    )

    vw = int(round(CLOCK_VIEW_W))
    vh = int(round(CLOCK_VIEW_H))
    out = np.zeros((vh, vw, 4), dtype=np.uint8)
    now = datetime.now()
    try:
        chrome = _rasterize_named_widget(
            assets_dir=assets_dir,
            widget_key="clock",
            dest_w=vw,
            dest_h=vh,
            now=now,
            theme=np_theme_from_settings(),
            zone=None,
        )
    except Exception:
        chrome = None
    if chrome is not None and chrome.size:
        _paste_patch_bgra(out, chrome, 0, 0)
    zone = NowPlayingZone(1, 0.0, 0.0, float(vw), float(vh), ())
    _draw_clock_labels_in_zone(
        out, zone, now, include_digital_time=_clock_include_digital_time()
    )
    return out


def _render_zone6_clock_bgra(
    box: tuple[int, int, int, int],
) -> np.ndarray | None:
    from pigeon.widgets.clock_saver import render_clock_saver_face_bgra

    _x, _y, w, h = box
    try:
        return render_clock_saver_face_bgra(
            width=max(32, int(w)),
            height=max(24, int(h)),
            include_weather=False,
        )
    except Exception:
        return None


def _render_info_widget_bgra(state: MainSettingsState | None = None) -> np.ndarray:
    from pigeon.np_layout import (
        CAST_ACTOR_SIZE_PX,
        CAST_CHAR_SIZE_PX,
        CAST_STRIP_STACK_GAP_PX,
        CAST_STRIP_VIEW_H,
        CAST_STRIP_VIEW_W,
        NowPlayingZone,
        strip_cast_columns,
    )
    from pigeon.widgets.view_circles import _draw_stacked_cast_pair

    vw = int(round(CAST_STRIP_VIEW_W))
    vh = int(round(CAST_STRIP_VIEW_H))
    out = np.zeros((vh, vw, 4), dtype=np.uint8)
    zone = NowPlayingZone(4, 0.0, 0.0, float(vw), float(vh), ())
    mid_y = float(vh) * 0.5
    cast = _preview_cast(state)
    for i, (x0, col_w) in enumerate(strip_cast_columns(zone)):
        actor, character = cast[i] if i < len(cast) else ("", "")
        _draw_stacked_cast_pair(
            out,
            actor=actor,
            character=character,
            cx=float(x0) + float(col_w) * 0.5,
            max_width_px=max(48, int(col_w) - 24),
            actor_px=CAST_ACTOR_SIZE_PX,
            char_px=CAST_CHAR_SIZE_PX,
            gap_px=CAST_STRIP_STACK_GAP_PX,
            mid_y=mid_y,
        )
    return out


def _render_status_widget_bgra(state: MainSettingsState | None = None) -> np.ndarray:
    from pigeon.np_layout import (
        STATUS_BAR_ELAPSED_LOCAL,
        STATUS_BAR_REMAINING_LOCAL,
        STATUS_BAR_TIME_SIZE_PX,
        STATUS_BAR_TRACK,
        STATUS_BAR_VIEW_H,
        STATUS_BAR_VIEW_W,
        NowPlayingZone,
        design_rect_from_local,
        design_xy_from_local,
        status_bar_elapsed_left_x,
    )
    from pigeon.widgets.view_circles import (
        _COLOR_UNFILLED_BGR,
        _draw_rounded_bar_bgra,
        _look_chrome_rgb,
        _look_ink_rgb,
        _matching_hhmm_patch,
        _paste_patch_bgra,
        format_status_bar_timecode,
        np_theme_from_settings,
    )

    vw = int(round(STATUS_BAR_VIEW_W))
    vh = int(round(STATUS_BAR_VIEW_H))
    out = np.zeros((vh, vw, 4), dtype=np.uint8)
    zone = NowPlayingZone(5, 0.0, 0.0, float(vw), float(vh), ())
    tx, ty, tw, th, trx = design_rect_from_local(
        zone,
        STATUS_BAR_TRACK,
        view_w=STATUS_BAR_VIEW_W,
        view_h=STATUS_BAR_VIEW_H,
    )
    _draw_rounded_bar_bgra(
        out,
        x=tx,
        y=ty,
        w=tw,
        h=th,
        fill_bgr=_COLOR_UNFILLED_BGR,
        radius=trx,
        fill_opacity=1.0,
        stroke=0,
    )
    progress = _preview_progress(state)
    elapsed = format_status_bar_timecode(
        _preview_text(state, "preferences_elapsed_text", _DEMO_ELAPSED)
    )
    remaining = format_status_bar_timecode(
        _preview_text(state, "preferences_remaining_text", _DEMO_REMAINING),
        remaining=True,
    )
    vis_w = max(0, min(tw, int(round(float(tw) * progress))))
    if vis_w > 0:
        from pigeon.widgets.view_circles import _rounded_rect_mask

        mask = _rounded_rect_mask(tw, th, trx)
        fill = np.zeros((th, tw, 4), dtype=np.uint8)
        fill[:, :, :3] = np_theme_from_settings().ui_bgr
        fill[:, :, 3] = mask
        fill[:, vis_w:, 3] = 0
        _paste_patch_bgra(out, fill, tx, ty)
    et = _matching_hhmm_patch(
        elapsed, size_px=STATUS_BAR_TIME_SIZE_PX, fill_rgb=_look_chrome_rgb()
    )
    rt = _matching_hhmm_patch(
        remaining, size_px=STATUS_BAR_TIME_SIZE_PX, fill_rgb=_look_ink_rgb()
    )
    _, ey = design_xy_from_local(
        zone,
        STATUS_BAR_ELAPSED_LOCAL[0],
        STATUS_BAR_ELAPSED_LOCAL[1],
        view_w=STATUS_BAR_VIEW_W,
        view_h=STATUS_BAR_VIEW_H,
    )
    _, ry = design_xy_from_local(
        zone,
        STATUS_BAR_REMAINING_LOCAL[0],
        STATUS_BAR_REMAINING_LOCAL[1],
        view_w=STATUS_BAR_VIEW_W,
        view_h=STATUS_BAR_VIEW_H,
    )
    et_w = int(et.shape[1]) if et is not None and et.size else 0
    rt_w = int(rt.shape[1]) if rt is not None and rt.size else 0
    remaining_left = float(tx + tw) - float(rt_w) if rt_w > 0 else None
    elapsed_x = status_bar_elapsed_left_x(
        track_x=float(tx),
        track_w=float(tw),
        progress=progress,
        elapsed_w=float(et_w),
        remaining_left_x=remaining_left,
    )
    if et is not None and et.size:
        _paste_patch_bgra(
            out, et, int(round(elapsed_x)), int(round(ey - et.shape[0]))
        )
    if rt is not None and rt.size:
        _paste_patch_bgra(
            out, rt, int(round(float(tx + tw) - rt_w)), int(round(ry - rt.shape[0]))
        )
    return out


def _render_weather_widget_bgra(assets_dir: Path | str | None) -> np.ndarray | None:
    from pigeon.widgets.clock_saver import render_clock_saver_weather_cluster_bgra

    try:
        return render_clock_saver_weather_cluster_bgra(assets_dir=assets_dir)
    except Exception:
        return None


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


def _widget_patch_for_zone(
    zone: str,
    widget_id: str,
    *,
    state: MainSettingsState,
    assets_dir: Path | str | None,
    box: tuple[int, int, int, int],
) -> np.ndarray | None:
    if zone == "zone6" and widget_id == "clock":
        return _render_zone6_clock_bgra(box)
    if widget_id == "artwork":
        if zone == "zone3":
            return _render_poster_artwork_widget_bgra(state, assets_dir)
        return _render_artwork_widget_bgra(state, assets_dir)
    if widget_id == "volume":
        return _render_volume_widget_bgra(assets_dir, state)
    if widget_id == "clock":
        return _render_np_clock_widget_bgra(assets_dir)
    if widget_id == "info":
        return _render_info_widget_bgra(state)
    if widget_id == "status":
        return _render_status_widget_bgra(state)
    if widget_id == "weather":
        return _render_weather_widget_bgra(assets_dir)
    return None


def _draw_zone_demos_bgra(
    out: np.ndarray,
    state: MainSettingsState,
    *,
    assets_dir: Path | str | None,
) -> None:
    from pigeon.widgets.view_circles import _paste_patch_bgra

    for zone in ZONE_FOCUS_IDS:
        box = _inner_rect(zone_canvas_rect(zone))
        wid = widget_id_for_zone(state, zone)
        _draw_zone_label(out, zone, wid, zone_canvas_rect(zone))
        patch = _widget_patch_for_zone(
            zone, wid, state=state, assets_dir=assets_dir, box=box
        )
        if zone == "zone6" and wid == "clock" and patch is not None:
            _paste_patch_bgra(out, patch, int(box[0]), int(box[1]))
            continue
        _contain_paste(out, patch, box)


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
