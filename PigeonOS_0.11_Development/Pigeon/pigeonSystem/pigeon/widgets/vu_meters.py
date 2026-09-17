"""Zone-6 VU meters + bass oscilloscope.

Needles rotate around the authored ellipse centres:

* ``-90°`` (9 o'clock) = 0 (silence)
* ``+90°`` (3 o'clock) = −3 dB (meter full scale)

The oscilloscope traces bass energy inside the rounded well. With no
program it keeps a small left-to-right sine so the bar never looks frozen.
"""

from __future__ import annotations

import copy
import math
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

VIEW_W = 795.66
VIEW_H = 448.0
LEFT_PIVOT = (218.51, 223.55)
RIGHT_PIVOT = (579.17, 223.55)
SCOPE_X0 = 170.64
SCOPE_X1 = 631.1
SCOPE_Y = 321.33
SCOPE_BOX = (139.32, 251.0, 517.54, 140.66)
SCOPE_RADIUS = 25.22
# Keep the trace inside the black well (stroke + padding).
SCOPE_INSET = 16.0
NEEDLE_REST_DEG = -90.0
NEEDLE_FULL_DEG = 90.0
VU_SMOOTH_S = 0.28
_UI_DEFAULT = "#4EA6F7"

_NEEDLE_HIDE = (
    "oscilloscope_line",
    "left_semicircle_stroke_and_fill",
    "left_semicircle_stroke",
    "right_semicircle_stroke_and_fill",
    "right_semicircle_stroke",
    "oscilloscope_container_stroke_and_fill",
    "oscilloscope_container_stroke",
    "left_ellipse_rear",
    "right_ellipse_rear",
    "left_ellipse",
    "right_ellipse",
)
_CHROME_HIDE = (
    "oscilloscope_line",
    "left_needle_group",
    "right_needle_group",
    "left_ellipse",
    "right_ellipse",
)
_HUB_HIDE = (
    "oscilloscope_line",
    "oscilloscope_container_stroke_and_fill",
    "oscilloscope_container_stroke",
    "left_semicircle_stroke_and_fill",
    "left_semicircle_stroke",
    "right_semicircle_stroke_and_fill",
    "right_semicircle_stroke",
    "left_needle_group",
    "right_needle_group",
    "left_ellipse_rear",
    "right_ellipse_rear",
)

_svg_template: ET.Element | None = None
_svg_mtime = 0
_raster_cache: dict[tuple[object, ...], np.ndarray] = {}
_sprite_cache: dict[tuple[object, ...], tuple[np.ndarray, float, float, int, int]] = {}
_mask_cache: dict[tuple[int, int, int], np.ndarray] = {}
_needle_l = 0.0
_needle_r = 0.0
_needle_t = 0.0
_scope_peak = 0.08
_vu_face_key: tuple[object, ...] | None = None
_vu_face_bgr: np.ndarray | None = None


def needle_angle_deg(fill: float) -> float:
    """Map 0–1 meter fill onto the authored −90°…+90° sweep."""
    f = max(0.0, min(1.0, float(fill)))
    return NEEDLE_REST_DEG + (NEEDLE_FULL_DEG - NEEDLE_REST_DEG) * f


def opencv_needle_angle_deg(fill: float) -> float:
    """``cv2.getRotationMatrix2D`` is CCW-positive; SVG/user angles are clockwise-right."""
    return -needle_angle_deg(fill)


def idle_scope_wave(n: int, t: float) -> np.ndarray:
    """Repeating sines that travel left → right."""
    count = max(8, int(n))
    x = np.linspace(0.0, 1.0, count, endpoint=False)
    return (
        0.16 * np.sin(2.0 * np.pi * (6.0 * x - 0.55 * t))
        + 0.05 * np.sin(2.0 * np.pi * (12.0 * x - 0.91 * t))
    )


def default_vu_meters_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = str((os.environ.get("PIGEON_VU_METERS_SVG") or "")).strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "nowPlaying" / "widget_np_06-07_vu_meters.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "nowPlaying" / "widget_np_06-07_vu_meters.svg"


def _svg_root(path: Path | None = None) -> ET.Element:
    global _svg_template, _svg_mtime
    svg_path = Path(path) if path is not None else default_vu_meters_svg_path()
    mtime = 0
    try:
        mtime = int(svg_path.stat().st_mtime_ns)
    except OSError:
        mtime = -1
    if _svg_template is None or mtime != _svg_mtime:
        _svg_template = ET.parse(svg_path).getroot()
        _svg_mtime = mtime
    return copy.deepcopy(_svg_template)


def _layer_key(el: ET.Element) -> str:
    return str(el.get("id") or el.get("data-name") or "").strip()


def _find(root: ET.Element, name: str) -> ET.Element | None:
    want = str(name or "").strip().lower()
    aliases = {
        "oscilloscope_group": "occiliscope_group",
        "oscilloscope_line": "occiliscope_line",
        "oscilloscope_container_stroke": "occiliscope_container_stroke",
        "oscilloscope_container_stroke_and_fill": "occiliscope_container_stroke_and_fill",
        "left_semicircle_stroke_and_fill": "left_semicircle",
        "right_semicircle_stroke_and_fill": "right_semicircle",
        "right_semicircle_stroke": "right_semicircle-2",
        "right_ellipse": "right_ellipse",
    }
    wants = {want, str(aliases.get(want, "")).lower()}
    wants.discard("")
    for el in root.iter():
        if _layer_key(el).lower() in wants:
            return el
    return None


def _hide(root: ET.Element, names: tuple[str, ...]) -> None:
    for name in names:
        el = _find(root, name)
        if el is None:
            continue
        el.set("display", "none")
        el.set("opacity", "0")


def _set_paint(el: ET.Element | None, *, fill: str | None = None, stroke: str | None = None) -> None:
    if el is None:
        return
    if fill is not None:
        el.set("fill", fill)
    if stroke is not None:
        el.set("stroke", stroke)


def _apply_ui(root: ET.Element, ui_hex: str) -> None:
    ui = str(ui_hex or _UI_DEFAULT)
    _set_paint(_find(root, "left_semicircle_stroke_and_fill"), fill=ui, stroke=ui)
    _set_paint(_find(root, "right_semicircle_stroke_and_fill"), fill=ui, stroke=ui)
    fill_el = _find(root, "oscilloscope_container_stroke_and_fill")
    _set_paint(fill_el, fill="#000000", stroke=ui)


def _fit(dest_w: int, dest_h: int) -> tuple[int, int, int, int, float]:
    sx = float(max(1, dest_w)) / VIEW_W
    sy = float(max(1, dest_h)) / VIEW_H
    scale = min(sx, sy)
    rw = max(32, int(round(VIEW_W * scale)))
    rh = max(24, int(round(VIEW_H * scale)))
    ox = (int(dest_w) - rw) // 2
    oy = (int(dest_h) - rh) // 2
    return rw, rh, ox, oy, float(rw) / VIEW_W


def _rasterize(root: ET.Element, width: int, height: int) -> np.ndarray:
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    bgra = rasterize_settings_svg_bgra(
        root,
        width=int(width),
        height=int(height),
        font_mode="preferences",
    )
    try:
        from pigeon.widgets.view_circles import _decanvas_white_bgra

        return _decanvas_white_bgra(bgra)
    except Exception:
        return bgra


def _cached_layer(
    kind: str,
    *,
    width: int,
    height: int,
    ui_hex: str,
    svg_path: Path,
) -> np.ndarray:
    try:
        mtime = int(svg_path.stat().st_mtime_ns)
    except OSError:
        mtime = -1
    key = (kind, int(width), int(height), str(ui_hex).lower(), str(svg_path), mtime)
    hit = _raster_cache.get(key)
    if hit is not None:
        return hit
    root = _svg_root(svg_path)
    _apply_ui(root, ui_hex)
    if kind == "chrome":
        _hide(root, _CHROME_HIDE)
    elif kind == "hubs":
        _hide(root, _HUB_HIDE)
    elif kind == "needle_l":
        _hide(root, _NEEDLE_HIDE + ("right_needle_group",))
    elif kind == "needle_r":
        _hide(root, _NEEDLE_HIDE + ("left_needle_group",))
    layer = _rasterize(root, width, height)
    if len(_raster_cache) > 24:
        _raster_cache.clear()
        _sprite_cache.clear()
        _mask_cache.clear()
    _raster_cache[key] = layer
    return layer


def _blit_onto_bgr(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    """BGRA sprite onto a BGR well (needles / hubs)."""
    if dst is None or src is None or dst.size == 0 or src.size == 0:
        return
    sh, sw = src.shape[:2]
    x0 = int(x)
    y0 = int(y)
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(int(dst.shape[1]), x0 + sw)
    cy1 = min(int(dst.shape[0]), y0 + sh)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    patch = src[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0]
    roi = dst[cy0:cy1, cx0:cx1]
    alpha = patch[:, :, 3]
    if int(alpha.max()) <= 0:
        return
    if int(alpha.min()) >= 255:
        roi[:] = patch[:, :, :3]
        return
    a = alpha.astype(np.uint16)[:, :, None]
    ia = 255 - a
    roi[:] = (
        (patch[:, :, :3].astype(np.uint16) * a + roi.astype(np.uint16) * ia) // 255
    ).astype(np.uint8)


def _blit_bgra(dst: np.ndarray, src: np.ndarray, x: int, y: int) -> None:
    if src is None or src.size == 0 or dst is None or dst.size == 0:
        return
    sh, sw = src.shape[:2]
    x0 = int(x)
    y0 = int(y)
    x1 = x0 + sw
    y1 = y0 + sh
    cx0 = max(0, x0)
    cy0 = max(0, y0)
    cx1 = min(int(dst.shape[1]), x1)
    cy1 = min(int(dst.shape[0]), y1)
    if cx0 >= cx1 or cy0 >= cy1:
        return
    patch = src[cy0 - y0 : cy1 - y0, cx0 - x0 : cx1 - x0]
    roi = dst[cy0:cy1, cx0:cx1]
    alpha = patch[:, :, 3]
    if int(alpha.max()) <= 0:
        return
    a = alpha.astype(np.uint16)
    ia = 255 - a
    a3 = a[:, :, None]
    ia3 = ia[:, :, None]
    roi[:, :, :3] = (
        (patch[:, :, :3].astype(np.uint16) * a3 + roi[:, :, :3].astype(np.uint16) * ia3) // 255
    ).astype(np.uint8)
    roi[:, :, 3] = np.maximum(roi[:, :, 3], alpha)


def _ink_radius(layer: np.ndarray, cx: float, cy: float) -> int:
    ys, xs = np.nonzero(layer[:, :, 3] > 12)
    if xs.size == 0:
        return 8
    d2 = (xs.astype(np.float64) - float(cx)) ** 2 + (ys.astype(np.float64) - float(cy)) ** 2
    return max(8, int(math.ceil(math.sqrt(float(d2.max())) + 3.0)))


def _pivot_square(
    layer: np.ndarray, cx: float, cy: float, radius: int
) -> tuple[np.ndarray, float, float, int, int]:
    r = max(8, int(radius))
    size = 2 * r + 1
    x0 = int(round(float(cx))) - r
    y0 = int(round(float(cy))) - r
    out = np.zeros((size, size, 4), dtype=np.uint8)
    src_x0 = max(0, x0)
    src_y0 = max(0, y0)
    src_x1 = min(int(layer.shape[1]), x0 + size)
    src_y1 = min(int(layer.shape[0]), y0 + size)
    if src_x1 > src_x0 and src_y1 > src_y0:
        out[src_y0 - y0 : src_y1 - y0, src_x0 - x0 : src_x1 - x0] = layer[
            src_y0:src_y1, src_x0:src_x1
        ]
    return out, float(cx) - float(x0), float(cy) - float(y0), x0, y0


def _cached_sprite(
    kind: str,
    pivot: tuple[float, float],
    *,
    width: int,
    height: int,
    ui_hex: str,
    svg_path: Path,
    scale: float,
    radius_design: float | None = None,
) -> tuple[np.ndarray, float, float, int, int]:
    try:
        mtime = int(svg_path.stat().st_mtime_ns)
    except OSError:
        mtime = -1
    key = (
        kind,
        round(float(pivot[0]), 2),
        round(float(pivot[1]), 2),
        int(width),
        int(height),
        str(ui_hex).lower(),
        str(svg_path),
        mtime,
        None if radius_design is None else round(float(radius_design), 2),
    )
    hit = _sprite_cache.get(key)
    if hit is not None:
        return hit
    layer = _cached_layer(kind, width=width, height=height, ui_hex=ui_hex, svg_path=svg_path)
    cx = float(pivot[0]) * float(scale)
    cy = float(pivot[1]) * float(scale)
    if radius_design is not None:
        radius = max(8, int(math.ceil(float(radius_design) * float(scale) + 3.0)))
    else:
        radius = _ink_radius(layer, cx, cy)
    sprite = _pivot_square(layer, cx, cy, radius)
    if len(_sprite_cache) > 24:
        _sprite_cache.clear()
    _sprite_cache[key] = sprite
    return sprite


def _rotate_sprite(
    sprite: np.ndarray, fill: float, local_cx: float, local_cy: float
) -> np.ndarray:
    h, w = sprite.shape[:2]
    angle = opencv_needle_angle_deg(fill)
    matrix = cv2.getRotationMatrix2D((float(local_cx), float(local_cy)), float(angle), 1.0)
    return cv2.warpAffine(
        sprite,
        matrix,
        (w, h),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0, 0),
    )


def _smooth_needles(fill_l: float, fill_r: float, now: float, *, enabled: bool) -> tuple[float, float]:
    global _needle_l, _needle_r, _needle_t
    target_l = max(0.0, min(1.0, float(fill_l)))
    target_r = max(0.0, min(1.0, float(fill_r)))
    if not enabled:
        _needle_l, _needle_r, _needle_t = target_l, target_r, now
        return target_l, target_r
    dt = max(0.0, min(0.12, now - _needle_t)) if _needle_t else 0.05
    _needle_t = now
    alpha = 1.0 - math.exp(-dt / VU_SMOOTH_S)
    _needle_l += (target_l - _needle_l) * alpha
    _needle_r += (target_r - _needle_r) * alpha
    return _needle_l, _needle_r


def _scope_samples(n: int, t: float, scope: np.ndarray | None, presence: float) -> np.ndarray:
    global _scope_peak
    idle = idle_scope_wave(n, t)
    live = np.zeros(int(n), dtype=np.float64)
    src = np.asarray(scope if scope is not None else (), dtype=np.float64).reshape(-1)
    if src.size:
        xs = np.linspace(0.0, 1.0, src.size)
        live = np.interp(np.linspace(0.0, 1.0, int(n)), xs, src)
        peak = float(np.max(np.abs(live)))
        _scope_peak = max(peak, _scope_peak * 0.96)
        denom = max(_scope_peak, 0.04)
        live = np.clip(live / denom, -1.0, 1.0)
    mix = max(0.0, min(1.0, float(presence) * 1.35))
    return idle * (1.0 - mix) + live * mix


def _rounded_rect_mask(w: int, h: int, rx: float) -> np.ndarray:
    rad = max(1, int(round(rx)))
    key = (int(w), int(h), rad)
    hit = _mask_cache.get(key)
    if hit is not None:
        return hit
    mask = np.zeros((int(h), int(w)), dtype=np.uint8)
    cv2.rectangle(mask, (rad, 0), (int(w) - rad - 1, int(h) - 1), 255, -1)
    cv2.rectangle(mask, (0, rad), (int(w) - 1, int(h) - rad - 1), 255, -1)
    cv2.circle(mask, (rad, rad), rad, 255, -1)
    cv2.circle(mask, (int(w) - rad - 1, rad), rad, 255, -1)
    cv2.circle(mask, (rad, int(h) - rad - 1), rad, 255, -1)
    cv2.circle(mask, (int(w) - rad - 1, int(h) - rad - 1), rad, 255, -1)
    if len(_mask_cache) > 8:
        _mask_cache.clear()
    _mask_cache[key] = mask
    return mask


def _draw_scope_bgr(layer: np.ndarray, wave: np.ndarray, scale: float) -> None:
    """White trace directly on the BGR well — no extra BGRA buffer or LINE_AA."""
    box_x, box_y, box_w, box_h = SCOPE_BOX
    mx = int(round(box_x * scale))
    my = int(round(box_y * scale))
    mw = max(8, int(round(box_w * scale)))
    mh = max(8, int(round(box_h * scale)))
    x0 = SCOPE_X0 * scale - mx
    x1 = SCOPE_X1 * scale - mx
    cy = SCOPE_Y * scale - my
    inner_h = (box_h - 2.0 * SCOPE_INSET) * scale
    amp = max(2.0, inner_h * 0.5)
    n = max(48, min(192, int(wave.size)))
    ys = cy - np.clip(wave, -1.0, 1.0) * amp
    xs = np.linspace(x0, x1, int(wave.size))
    if int(wave.size) != n:
        xs_n = np.linspace(x0, x1, n)
        ys = np.interp(xs_n, xs, ys)
        xs = xs_n
    y_lo = SCOPE_INSET * scale
    y_hi = mh - SCOPE_INSET * scale
    ys = np.clip(ys, y_lo, y_hi)
    pts = np.stack([xs + mx, ys + my], axis=1).astype(np.int32)
    thickness = max(2, int(round(3.0 * scale)))
    cv2.polylines(layer, [pts], False, (255, 255, 255), thickness, cv2.LINE_8)


def _draw_scope(layer: np.ndarray, wave: np.ndarray, scale: float) -> None:
    box_x, box_y, box_w, box_h = SCOPE_BOX
    mx = int(round(box_x * scale))
    my = int(round(box_y * scale))
    mw = max(8, int(round(box_w * scale)))
    mh = max(8, int(round(box_h * scale)))
    x0 = SCOPE_X0 * scale - mx
    x1 = SCOPE_X1 * scale - mx
    cy = SCOPE_Y * scale - my
    inner_h = (box_h - 2.0 * SCOPE_INSET) * scale
    amp = max(2.0, inner_h * 0.5)
    n = max(48, min(192, int(wave.size)))
    ys = cy - np.clip(wave, -1.0, 1.0) * amp
    xs = np.linspace(x0, x1, int(wave.size))
    if int(wave.size) != n:
        xs_n = np.linspace(x0, x1, n)
        ys = np.interp(xs_n, xs, ys)
        xs = xs_n
    y_lo = SCOPE_INSET * scale
    y_hi = mh - SCOPE_INSET * scale
    ys = np.clip(ys, y_lo, y_hi)
    pts = np.stack([xs, ys], axis=1).astype(np.int32)
    rgb = np.zeros((mh, mw, 3), dtype=np.uint8)
    thickness = max(2, int(round(3.0 * scale)))
    cv2.polylines(rgb, [pts], False, (255, 255, 255), thickness, cv2.LINE_AA)
    stroke = np.zeros((mh, mw, 4), dtype=np.uint8)
    stroke[:, :, :3] = rgb
    stroke[:, :, 3] = np.max(rgb, axis=2)
    mask = _rounded_rect_mask(mw, mh, SCOPE_RADIUS * scale)
    stroke[:, :, 3] = np.minimum(stroke[:, :, 3], mask)
    _blit_bgra(layer, stroke, mx, my)


def _current_ui_hex() -> str:
    try:
        from pigeon.widgets.view_circles import np_theme_from_settings

        return str(np_theme_from_settings().ui_hex or _UI_DEFAULT)
    except Exception:
        return _UI_DEFAULT


def render_vu_meters_bgra(
    width: int,
    height: int,
    *,
    t: float | None = None,
    fill_l: float | None = None,
    fill_r: float | None = None,
    scope: np.ndarray | None = None,
    ui_hex: str | None = None,
    smooth: bool = True,
    assets_dir: Path | str | None = None,
    preview: bool = False,
) -> np.ndarray:
    """Letterboxed VU + oscilloscope sized to ``width``×``height``."""
    dest_w = max(32, int(width))
    dest_h = max(24, int(height))
    out = np.zeros((dest_h, dest_w, 4), dtype=np.uint8)
    if preview:
        now = 0.0 if t is None else float(t)
        use_l = 0.0 if fill_l is None else float(fill_l)
        use_r = 0.0 if fill_r is None else float(fill_r)
        presence = max(use_l, use_r)
        if scope is None:
            scope = np.zeros(64, dtype=np.float64)
    else:
        now = time.monotonic() if t is None else float(t)
        sample_fill_l = 0.0
        sample_fill_r = 0.0
        presence = 0.0
        try:
            from pigeon.widgets.audio_meter_saver import latest_meter_levels, latest_scope

            lv = latest_meter_levels()
            sample_fill_l = float(lv.fill_l)
            sample_fill_r = float(lv.fill_r)
            presence = max(sample_fill_l, sample_fill_r, float(lv.lfe_fill))
            if scope is None:
                scope = latest_scope()
        except Exception:
            pass
        use_l = sample_fill_l if fill_l is None else float(fill_l)
        use_r = sample_fill_r if fill_r is None else float(fill_r)
        presence = max(presence, use_l, use_r)
        use_l, use_r = _smooth_needles(use_l, use_r, now, enabled=bool(smooth) and t is None)
    ui = str(ui_hex or _current_ui_hex())
    svg_path = default_vu_meters_svg_path(assets_dir)
    rw, rh, ox, oy, scale = _fit(dest_w, dest_h)
    chrome = _cached_layer("chrome", width=rw, height=rh, ui_hex=ui, svg_path=svg_path)
    needle_l, lcx, lcy, lx, ly = _cached_sprite(
        "needle_l", LEFT_PIVOT, width=rw, height=rh, ui_hex=ui, svg_path=svg_path, scale=scale
    )
    needle_r, rcx, rcy, rx, ry = _cached_sprite(
        "needle_r", RIGHT_PIVOT, width=rw, height=rh, ui_hex=ui, svg_path=svg_path, scale=scale
    )
    hub_l, _, _, hx0, hy0 = _cached_sprite(
        "hubs",
        LEFT_PIVOT,
        width=rw,
        height=rh,
        ui_hex=ui,
        svg_path=svg_path,
        scale=scale,
        radius_design=22.0,
    )
    hub_r, _, _, hx1, hy1 = _cached_sprite(
        "hubs",
        RIGHT_PIVOT,
        width=rw,
        height=rh,
        ui_hex=ui,
        svg_path=svg_path,
        scale=scale,
        radius_design=22.0,
    )
    well = out[oy : oy + rh, ox : ox + rw]
    well[:] = chrome
    _blit_bgra(well, _rotate_sprite(needle_l, use_l, lcx, lcy), lx, ly)
    _blit_bgra(well, _rotate_sprite(needle_r, use_r, rcx, rcy), rx, ry)
    _blit_bgra(well, hub_l, hx0, hy0)
    _blit_bgra(well, hub_r, hx1, hy1)
    n = max(48, min(192, int(round((SCOPE_X1 - SCOPE_X0) * scale))))
    wave = _scope_samples(n, now, scope, presence)
    _draw_scope(well, wave, scale)
    return out


def render_vu_meters_into_bgr(
    dest_bgr: np.ndarray,
    *,
    face_key: object = None,
    t: float | None = None,
    fill_l: float | None = None,
    fill_r: float | None = None,
    scope: np.ndarray | None = None,
    ui_hex: str | None = None,
    smooth: bool = True,
    assets_dir: Path | str | None = None,
) -> None:
    """Paint VU needles + scope into ``dest_bgr``. Chrome is blended once per face_key."""
    global _vu_face_key, _vu_face_bgr
    if dest_bgr is None or dest_bgr.size == 0 or dest_bgr.ndim != 3:
        return
    dest_h, dest_w = int(dest_bgr.shape[0]), int(dest_bgr.shape[1])
    if dest_w < 1 or dest_h < 1:
        return
    now = time.monotonic() if t is None else float(t)
    sample_fill_l = 0.0
    sample_fill_r = 0.0
    presence = 0.0
    try:
        from pigeon.widgets.audio_meter_saver import latest_meter_levels, latest_scope

        lv = latest_meter_levels()
        sample_fill_l = float(lv.fill_l)
        sample_fill_r = float(lv.fill_r)
        presence = max(sample_fill_l, sample_fill_r, float(lv.lfe_fill))
        if scope is None:
            scope = latest_scope()
    except Exception:
        pass
    use_l = sample_fill_l if fill_l is None else float(fill_l)
    use_r = sample_fill_r if fill_r is None else float(fill_r)
    presence = max(presence, use_l, use_r)
    use_l, use_r = _smooth_needles(use_l, use_r, now, enabled=bool(smooth) and t is None)
    ui = str(ui_hex or _current_ui_hex())
    svg_path = default_vu_meters_svg_path(assets_dir)
    rw, rh, ox, oy, scale = _fit(dest_w, dest_h)
    chrome = _cached_layer("chrome", width=rw, height=rh, ui_hex=ui, svg_path=svg_path)
    well = dest_bgr[oy : oy + rh, ox : ox + rw]
    key = (int(dest_w), int(dest_h), ui, face_key)
    if (
        _vu_face_bgr is None
        or _vu_face_key != key
        or _vu_face_bgr.shape != well.shape
    ):
        from pigeon.compositing import blend_bgra_over_bgr_u8

        _vu_face_bgr = blend_bgra_over_bgr_u8(well, chrome)
        _vu_face_key = key
    well[:] = _vu_face_bgr
    needle_l, lcx, lcy, lx, ly = _cached_sprite(
        "needle_l", LEFT_PIVOT, width=rw, height=rh, ui_hex=ui, svg_path=svg_path, scale=scale
    )
    needle_r, rcx, rcy, rx, ry = _cached_sprite(
        "needle_r", RIGHT_PIVOT, width=rw, height=rh, ui_hex=ui, svg_path=svg_path, scale=scale
    )
    hub_l, _, _, hx0, hy0 = _cached_sprite(
        "hubs",
        LEFT_PIVOT,
        width=rw,
        height=rh,
        ui_hex=ui,
        svg_path=svg_path,
        scale=scale,
        radius_design=22.0,
    )
    hub_r, _, _, hx1, hy1 = _cached_sprite(
        "hubs",
        RIGHT_PIVOT,
        width=rw,
        height=rh,
        ui_hex=ui,
        svg_path=svg_path,
        scale=scale,
        radius_design=22.0,
    )
    _blit_onto_bgr(well, _rotate_sprite(needle_l, use_l, lcx, lcy), lx, ly)
    _blit_onto_bgr(well, _rotate_sprite(needle_r, use_r, rcx, rcy), rx, ry)
    _blit_onto_bgr(well, hub_l, hx0, hy0)
    _blit_onto_bgr(well, hub_r, hx1, hy1)
    n = max(48, min(192, int(round((SCOPE_X1 - SCOPE_X0) * scale))))
    wave = _scope_samples(n, now, scope, presence)
    _draw_scope_bgr(well, wave, scale)


__all__ = [
    "VIEW_H",
    "VIEW_W",
    "default_vu_meters_svg_path",
    "idle_scope_wave",
    "needle_angle_deg",
    "opencv_needle_angle_deg",
    "render_vu_meters_bgra",
    "render_vu_meters_into_bgr",
]
