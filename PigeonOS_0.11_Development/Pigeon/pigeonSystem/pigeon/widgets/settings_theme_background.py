"""Shared settings-menu theme background (black + UI-tinted diagonal slants).

Geometry comes from ``pigeonAssets/settings_0.8/settings_background.svg``.
Chrome SVGs (``settings_main``, ``settings_pigeon``, …) must not paint their own
baked stripe/background layers — those are stripped/hidden; this module draws
the replacement plate under the rasterized UI.
"""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W

# Artboard origin of the Illustrator export (canvas rect top-left).
_CANVAS_ORIGIN_SVG = (363.37, 441.08)

# Default brightness ladder (tweakable later for custom themes).
_SLANT_BRIGHTNESS: tuple[tuple[str, float], ...] = (
    ("slant1_90_theme", 0.90),
    ("slant2_80_theme", 0.80),
    ("slant3_70_theme", 0.70),
    ("slant4_60_theme", 0.60),  # also accepts typo slant4_60_thene
    ("slant5_50_theme", 0.50),
    ("slant6_40_theme", 0.40),  # also accepts slant6_40theme
)

_TRANSLATE_ROTATE_RE = re.compile(
    r"translate\(\s*([-\d.]+)(?:[,\s]+([-\d.]+))?\s*\)\s*"
    r"rotate\(\s*([-\d.]+)\s*\)",
    re.IGNORECASE,
)
_ID_BRIGHTNESS_RE = re.compile(
    r"slant\d+_(\d{2})(?:_theme)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ThemeSlantSpec:
    """One diagonal slant in design-space SVG units (800×480)."""

    x_svg: float
    y_svg: float
    width_svg: float
    height_svg: float
    matrix: tuple[float, float, float, float, float, float]
    brightness: float  # 0..1 multiplier of theme UI color


def default_settings_background_svg_path(assets_dir: Path | str | None = None) -> Path:
    from pigeon.settings_layout import settings_widget_path

    native = settings_widget_path("background", assets_dir=assets_dir)
    if native.is_file():
        return native
    if assets_dir is not None:
        return Path(assets_dir) / "settings_0.8" / "settings_background.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "settings_0.8" / "settings_background.svg"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#").lower()
    if len(h) == 3:
        h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
    if len(h) != 6:
        return (255, 0, 19)
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def settings_background_ui_hex(ui_hex: str) -> str:
    """Light mode uses the gray swatch slants instead of the live UI hue."""
    try:
        from pigeon.widgets.options_settings import ui_is_bright

        if ui_is_bright():
            from pigeon.widgets.ui_color_settings import hex_for_color_key

            return hex_for_color_key("ui", "gray")
    except Exception:
        pass
    return str(ui_hex or "")


def _look_is_bright() -> bool:
    try:
        from pigeon.widgets.options_settings import ui_is_bright

        return bool(ui_is_bright())
    except Exception:
        return False


def _paper_bgr() -> tuple[int, int, int]:
    try:
        from pigeon.widgets.options_settings import ui_paper_bgr

        return ui_paper_bgr()
    except Exception:
        return (0, 0, 0)


def _paper_hex() -> str:
    return "#FFFFFF" if _look_is_bright() else "#000000"


def scale_ui_hex(ui_hex: str, brightness: float) -> str:
    """Return ``#rrggbb`` for ``ui_hex`` scaled by ``brightness`` (0..1)."""
    r, g, b = _hex_to_rgb(ui_hex)
    t = max(0.0, min(1.0, float(brightness)))
    return f"#{int(round(r * t)):02x}{int(round(g * t)):02x}{int(round(b * t)):02x}"


def _translate_rotate_to_matrix(
    tx: float, ty: float, degrees: float
) -> tuple[float, float, float, float, float, float]:
    """SVG ``translate(tx,ty) rotate(deg)`` → matrix (a,b,c,d,e,f)."""
    rad = math.radians(degrees)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    # CTM = T × R
    return (cos_a, sin_a, -sin_a, cos_a, tx, ty)


def _parse_slant_transform(
    transform: str | None,
) -> tuple[float, float, float, float, float, float] | None:
    if not transform:
        return None
    m = _TRANSLATE_ROTATE_RE.search(transform)
    if m:
        tx = float(m.group(1))
        ty = float(m.group(2) or 0.0)
        return _translate_rotate_to_matrix(tx, ty, float(m.group(3)))
    mm = re.search(
        r"matrix\(\s*([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)[,\s]+"
        r"([-\d.]+)[,\s]+([-\d.]+)[,\s]+([-\d.]+)\s*\)",
        transform,
        re.IGNORECASE,
    )
    if mm:
        return tuple(float(mm.group(i)) for i in range(1, 7))  # type: ignore[return-value]
    return None


def _brightness_for_slant_id(raw_id: str) -> float | None:
    lid = (raw_id or "").strip().lower().replace("-", "_")
    # Explicit map (handles Illustrator typos in export ids).
    aliases = {
        "slant1_90_theme": 0.90,
        "slant2_80_theme": 0.80,
        "slant3_70_theme": 0.70,
        "slant4_60_theme": 0.60,
        "slant4_60_thene": 0.60,
        "slant5_50_theme": 0.50,
        "slant6_40_theme": 0.40,
        "slant6_40theme": 0.40,
    }
    if lid in aliases:
        return aliases[lid]
    m = _ID_BRIGHTNESS_RE.search(lid)
    if m:
        return int(m.group(1)) / 100.0
    return None


def _transform_rect_corners(
    x: float,
    y: float,
    w: float,
    h: float,
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[tuple[float, float], ...]:
    a, b, c, d, e, f = matrix
    corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    out: list[tuple[float, float]] = []
    for px, py in corners:
        out.append((a * px + c * py + e, b * px + d * py + f))
    return tuple(out)


@lru_cache(maxsize=2)
def load_theme_slant_specs(svg_path: str) -> tuple[ThemeSlantSpec, ...]:
    """Parse slant rects from ``settings_background.svg`` into design-space specs."""
    path = Path(svg_path)
    root = ET.parse(path).getroot()
    ox, oy = _CANVAS_ORIGIN_SVG
    found: list[ThemeSlantSpec] = []
    for el in root.iter():
        if not el.tag.endswith("rect"):
            continue
        rid = el.get("id") or ""
        brightness = _brightness_for_slant_id(rid)
        if brightness is None:
            continue
        matrix = _parse_slant_transform(el.get("transform"))
        if matrix is None:
            continue
        try:
            x = float(el.get("x") or 0.0)
            y = float(el.get("y") or 0.0)
            w = float(el.get("width") or 0.0)
            h = float(el.get("height") or 0.0)
        except ValueError:
            continue
        # Shift absolute artboard coords into 800×480 design space via corner remap.
        corners = _transform_rect_corners(x, y, w, h, matrix)
        # Rebuild an identity-local rect at the AABB of design-space corners is wrong
        # for rotation — keep original local rect but bake origin into matrix translation.
        a, b, c, d, e, f = matrix
        matrix_design = (a, b, c, d, e - ox, f - oy)
        found.append(
            ThemeSlantSpec(
                x_svg=x,
                y_svg=y,
                width_svg=w,
                height_svg=h,
                matrix=matrix_design,
                brightness=brightness,
            )
        )
    # Paint dark → light so brighter slants sit on top (matches AI layer order).
    found.sort(key=lambda s: s.brightness)
    return tuple(found)


def _svg_to_px(x_svg: float, y_svg: float) -> tuple[int, int]:
    x = int(round(x_svg * DESIGN_W / 800.0))
    y = int(round(y_svg * DESIGN_H / 480.0))
    return x, y


_PLATE_CACHE: dict[tuple[str, str, int], np.ndarray] = {}


def _menu_plate_mask(h: int, w: int) -> np.ndarray:
    """Opaque rounded rect matching the background SVG clipPath, in canvas space."""
    from PIL import Image, ImageDraw

    from pigeon.settings_layout import MENU_PLATE_RADIUS, MENU_PLATE_XYWH

    img = Image.new("L", (w, h), 0)
    x, y, pw, ph = MENU_PLATE_XYWH
    r = max(1, int(round(MENU_PLATE_RADIUS)))
    x0, y0 = int(round(x)), int(round(y))
    x1, y1 = int(round(x + pw)), int(round(y + ph))
    ImageDraw.Draw(img).rounded_rectangle((x0, y0, x1, y1), radius=r, fill=255)
    return np.asarray(img)


def _recolor_native_background(root: ET.Element, ui_hex: str) -> None:
    from pigeon.settings_layout import BACKGROUND_BRIGHTNESS, SETTINGS_CANVAS_H, SETTINGS_CANVAS_ORIGIN, SETTINGS_CANVAS_W

    ox, oy = SETTINGS_CANVAS_ORIGIN
    root.set("viewBox", f"{ox} {oy} {SETTINGS_CANVAS_W} {SETTINGS_CANVAS_H}")
    root.set("width", str(int(SETTINGS_CANVAS_W)))
    root.set("height", str(int(SETTINGS_CANVAS_H)))
    for el in root.iter():
        eid = (el.get("id") or el.get("data-name") or "").strip()
        brightness = BACKGROUND_BRIGHTNESS.get(eid)
        if brightness is None:
            continue
        el.set("fill", scale_ui_hex(ui_hex, brightness))
        style = el.get("style") or ""
        parts = [p for p in style.split(";") if p.strip() and not p.strip().startswith("fill")]
        parts.append(f"fill:{scale_ui_hex(ui_hex, brightness)}")
        el.set("style", ";".join(parts))
    # Darkest slant has no id in the export — treat unnamed clipped rects as 35%.
    for el in root.iter():
        if not el.tag.endswith("rect"):
            continue
        eid = (el.get("id") or "").strip()
        if eid:
            continue
        fill = (el.get("fill") or "").strip()
        if fill in ("", "none", "#000", "#000000"):
            continue
        if (el.get("width") or "") == "1280":
            continue
        el.set("fill", scale_ui_hex(ui_hex, 0.35))


def _paint_background_paper(root: ET.Element) -> None:
    """Assign the page plate (id=bg) instead of relying on a later invert."""
    paper = _paper_hex()
    for el in root.iter():
        eid = (el.get("id") or "").strip()
        if eid != "bg" and not (
            el.tag.endswith("rect") and (el.get("width") or "") == "1280"
        ):
            continue
        if el.tag.endswith("g"):
            for child in el:
                if child.tag.endswith("rect"):
                    child.set("fill", paper)
            continue
        el.set("fill", paper)


def draw_settings_theme_background_bgra(
    bgra: np.ndarray,
    *,
    ui_hex: str,
    clip_mask: np.ndarray,
    assets_dir: Path | str | None = None,
    svg_path: Path | str | None = None,
) -> None:
    """Fill ``bgra`` black, then paint UI-tinted slants.

    Native 1280×800 ``widget_general_settings_background.svg`` is preferred.
    Legacy 800×480 slants still honor ``clip_mask``.
    """
    tint = settings_background_ui_hex(ui_hex)
    path = (
        Path(svg_path)
        if svg_path is not None
        else default_settings_background_svg_path(assets_dir)
    )
    if path.is_file() and "widget_general_settings_background" in path.name:
        cache_key = (
            str(path.resolve()),
            tint.lower(),
            "bright" if _look_is_bright() else "std",
            -1283,
        )
        cached = _PLATE_CACHE.get(cache_key)
        if cached is not None:
            h = min(bgra.shape[0], cached.shape[0])
            w = min(bgra.shape[1], cached.shape[1])
            bgra[:h, :w] = cached[:h, :w]
            _remember_slants(cached)
            return
        import copy
        import xml.etree.ElementTree as ET

        from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

        root = copy.deepcopy(ET.parse(path).getroot())
        _recolor_native_background(root, tint)
        _paint_background_paper(root)
        plate = rasterize_settings_svg_bgra(
            root, width=int(bgra.shape[1]), height=int(bgra.shape[0])
        )
        from pigeon.settings_layout import SETTINGS_BACKGROUND_SHIFT_Y

        shift = -int(SETTINGS_BACKGROUND_SHIFT_Y)
        if shift > 0 and plate.shape[0] > shift:
            moved = np.zeros_like(plate)
            moved[:-shift] = plate[shift:]
            plate = moved
        # PyMuPDF ignores SVG clipPath; force slants into the rounded menu plate.
        mask = _menu_plate_mask(int(plate.shape[0]), int(plate.shape[1]))
        outside = mask == 0
        paper = _paper_bgr()
        plate[outside, 0] = paper[0]
        plate[outside, 1] = paper[1]
        plate[outside, 2] = paper[2]
        while len(_PLATE_CACHE) >= 12:
            _PLATE_CACHE.pop(next(iter(_PLATE_CACHE)))
        _PLATE_CACHE[cache_key] = plate.copy()
        h = min(bgra.shape[0], plate.shape[0])
        w = min(bgra.shape[1], plate.shape[1])
        bgra[:h, :w] = plate[:h, :w]
        _remember_slants(plate)
        return

    if not path.is_file():
        # Fallback: solid UI plate if asset missing (should not happen in installs).
        bgra[:, :, :3] = 0
        bgra[:, :, 3] = 255
        sel = clip_mask > 0
        rgb = _hex_to_rgb(tint)
        bgra[sel, 0] = rgb[2]
        bgra[sel, 1] = rgb[1]
        bgra[sel, 2] = rgb[0]
        _remember_slants(bgra)
        return

    cache_key = (str(path.resolve()), tint.lower(), int(clip_mask.sum()))
    cached = _PLATE_CACHE.get(cache_key)
    if cached is not None:
        bgra[:] = cached
        _remember_slants(cached)
        return

    plate = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
    plate[:, :, 3] = 255  # opaque black full frame
    specs = load_theme_slant_specs(str(path.resolve()))
    for spec in specs:
        fill = scale_ui_hex(tint, spec.brightness)
        r, g, b = _hex_to_rgb(fill)
        corners = _transform_rect_corners(
            spec.x_svg, spec.y_svg, spec.width_svg, spec.height_svg, spec.matrix
        )
        pts = np.array([_svg_to_px(x, y) for x, y in corners], dtype=np.int32)
        poly = np.zeros((DESIGN_H, DESIGN_W), dtype=np.uint8)
        cv2.fillConvexPoly(poly, pts, 255)
        poly = cv2.bitwise_and(poly, clip_mask)
        sel = poly > 0
        plate[sel, 0] = b
        plate[sel, 1] = g
        plate[sel, 2] = r
        plate[sel, 3] = 255

    while len(_PLATE_CACHE) >= 12:
        _PLATE_CACHE.pop(next(iter(_PLATE_CACHE)))
    _PLATE_CACHE[cache_key] = plate.copy()
    bgra[:] = plate
    _remember_slants(plate)


def _remember_slants(plate: np.ndarray) -> None:
    try:
        from pigeon.widgets.options_settings import ui_is_bright

        if not ui_is_bright():
            from pigeon.compositing import clear_bright_slant_mask

            clear_bright_slant_mask()
            return
        from pigeon.compositing import remember_bright_slant_plate

        remember_bright_slant_plate(plate)
    except Exception:
        pass


__all__ = [
    "ThemeSlantSpec",
    "default_settings_background_svg_path",
    "draw_settings_theme_background_bgra",
    "load_theme_slant_specs",
    "scale_ui_hex",
    "settings_background_ui_hex",
]
