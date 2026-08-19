"""
Image preparation for Pigeon UI widgets.

Design resolution and documented UI caps follow ``pigeon.design`` (800×480 landscape baseline).

Poster master: 1800×2700 BGRA — uniform width 1800, vertical letterbox (transparent) or center-crop
if content height exceeds 2700. Widget display scales the master by fixed factors per poster size.

English title logos (``LogoEn`` in pigeonReFormattedMedia) are fetched from TMDb at ``w1280`` and saved
as copied/reformatted assets; widgets downscale from that file into each on-screen logo box.
"""

from __future__ import annotations

import io
from enum import Enum
from pathlib import Path
import cv2
import numpy as np

from pigeon.app_state import auto_delete_pulled_media
from pigeon.compositing import cv_resize_interp, scale_cover_center_crop
from pigeon.design import DESIGN_H, DESIGN_W, rect_for_span_at_cell, rect_for_span_top_right_at_cell
from pigeon.media_folders import ensure_reformatted_media_dir, pigeon_pulled_media_dir
from pigeon.stage_background import get_stage_bgr

MAX_UI_LANDSCAPE_W, MAX_UI_LANDSCAPE_H = DESIGN_W, DESIGN_H
MAX_UI_PORTRAIT_W, MAX_UI_PORTRAIT_H = DESIGN_H, DESIGN_W

POSTER_MASTER_W = 1800
POSTER_MASTER_H = 2700

# TMDb backdrop: pull → uniform height BACKDROP_MASTER_HEIGHT, then cover-fit onto the full design canvas.
BACKDROP_MASTER_HEIGHT = DESIGN_H
# Design grid (1-based): patch top-left = top-left of this cell, bottom-right = bottom-right of this cell.
BACKDROP_PATCH_TL_ROW_1BASED = 1
# Full grid width (cols 1–19): fewer stage-colored side margins around the still.
BACKDROP_PATCH_TL_COL_1BASED = 1
BACKDROP_PATCH_BR_ROW_1BASED = 8
BACKDROP_PATCH_BR_COL_1BASED = 19

# Streaming app logo during clock saver: same 5×2 top-right anchor as the main title logo (row 1.5, col 13).
APP_LOGO_CLOCK_SAVER_ROW_1BASED = 1.5
APP_LOGO_CLOCK_SAVER_COL_RIGHT_1BASED = 13.0
APP_LOGO_CLOCK_SAVER_SPAN_W = 5
APP_LOGO_CLOCK_SAVER_SPAN_H = 2
APP_LOGO_CLOCK_SAVER_OPACITY = 0.2


class PosterWidgetSize(Enum):
    """Grid span label → scale factor applied to 1800px-wide master (width × factor, uniform)."""

    SMALL_3X4 = "small_3x4"  # 39%
    MEDIUM_4X6 = "medium_4x6"  # 59%
    LARGE_6X8 = "large_6x8"  # 71%
    SUPER_8X10 = "super_8x10"  # 100%

    @property
    def scale_factor(self) -> float:
        return _POSTER_SIZE_FACTORS[self]


_POSTER_SIZE_FACTORS: dict[PosterWidgetSize, float] = {
    PosterWidgetSize.SMALL_3X4: 0.39,
    PosterWidgetSize.MEDIUM_4X6: 0.59,
    PosterWidgetSize.LARGE_6X8: 0.71,
    PosterWidgetSize.SUPER_8X10: 1.0,
}

_SVG_DECODE_WARNED = False


def _bgr_or_gray_array_to_bgra(raw: np.ndarray) -> np.ndarray:
    if raw.ndim == 2:
        bgr = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
        a = np.full((bgr.shape[0], bgr.shape[1], 1), 255, dtype=np.uint8)
        return np.concatenate([bgr, a], axis=2)
    if raw.shape[2] == 3:
        bgra = cv2.cvtColor(raw, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = 255
        return bgra
    return raw


def _load_svg_as_bgra(path: Path) -> np.ndarray | None:
    """Rasterize SVG to BGRA via optional ``cairosvg`` (for App logos under pigeonAssets)."""
    global _SVG_DECODE_WARNED
    try:
        import cairosvg
    except ImportError:
        if not _SVG_DECODE_WARNED:
            _SVG_DECODE_WARNED = True
            import sys

            sys.stderr.write(
                "pigeon: SVG images (e.g. App logos) need cairosvg — "
                "pip install cairosvg — or use PNG/JPEG assets\n"
            )
        return None
    try:
        out = io.BytesIO()
        cairosvg.svg2png(bytestring=path.read_bytes(), write_to=out)
        out.seek(0)
        data = np.frombuffer(out.getvalue(), dtype=np.uint8)
        raw = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    except Exception:
        return None
    if raw is None or raw.size == 0:
        return None
    return _bgr_or_gray_array_to_bgra(raw)


def load_image_bgra(path: Path | str) -> np.ndarray | None:
    """Load image as BGRA uint8, or None on failure."""
    p = Path(path)
    if not p.is_file():
        return None
    if p.suffix.lower() == ".svg":
        return _load_svg_as_bgra(p)
    raw = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if raw is None or raw.size == 0:
        bgr = _load_bgr_via_pil(p)
        if bgr is None:
            return None
        raw = bgr
    return _bgr_or_gray_array_to_bgra(raw)


def _bgr_to_bgra(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        bgr = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        a = np.full((bgr.shape[0], bgr.shape[1], 1), 255, dtype=np.uint8)
        return np.concatenate([bgr, a], axis=2)
    if img.shape[2] == 3:
        bgra = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = 255
        return bgra
    return img


def poster_master_bgra_from_source(img_bgr_or_bgra: np.ndarray) -> np.ndarray:
    """
    Build POSTER_MASTER_W×POSTER_MASTER_H BGRA: scale uniform to width 1800, then
    center-crop vertically if h>2700, else vertically center on transparent canvas.
    """
    src = _bgr_to_bgra(img_bgr_or_bgra)
    h0, w0 = src.shape[:2]
    if w0 < 1 or h0 < 1:
        return np.zeros((POSTER_MASTER_H, POSTER_MASTER_W, 4), dtype=np.uint8)

    scale = POSTER_MASTER_W / float(w0)
    nh = max(1, int(round(h0 * scale)))
    nw = POSTER_MASTER_W
    resized = cv2.resize(src, (nw, nh), interpolation=cv_resize_interp(w0, h0, nw, nh))

    out = np.zeros((POSTER_MASTER_H, POSTER_MASTER_W, 4), dtype=np.uint8)
    if nh >= POSTER_MASTER_H:
        y0 = (nh - POSTER_MASTER_H) // 2
        crop = resized[y0 : y0 + POSTER_MASTER_H, :, :]
        out[:, :, :] = crop
    else:
        y0 = (POSTER_MASTER_H - nh) // 2
        out[y0 : y0 + nh, :, :] = resized
    return out


def scale_master_for_widget_size(master_bgra: np.ndarray, size: PosterWidgetSize | str) -> np.ndarray:
    """Uniformly scale master so width = 1800 × size.scale_factor (height follows aspect)."""
    if isinstance(size, str):
        size = PosterWidgetSize(size)
    f = size.scale_factor
    mh, mw = master_bgra.shape[:2]
    if mw < 1 or mh < 1:
        return master_bgra
    tw = max(1, int(round(POSTER_MASTER_W * f)))
    th = max(1, int(round(mh * f)))
    return cv2.resize(master_bgra, (tw, th), interpolation=cv_resize_interp(mw, mh, tw, th))


def bgra_to_bgr_on_black(bgra: np.ndarray) -> np.ndarray:
    """Flatten transparency onto black for …_poster.png (3-channel BGR)."""
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        return bgra
    bgr = bgra[:, :, :3].astype(np.float32)
    a = bgra[:, :, 3:4].astype(np.float32) / 255.0
    comp = bgr * a
    return np.clip(comp, 0, 255).astype(np.uint8)


def save_png_bgra(path: Path | str, arr: np.ndarray) -> bool:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return bool(cv2.imwrite(str(p), arr))


def bgr_scale_uniform_height(img_bgr: np.ndarray, target_h: int) -> np.ndarray:
    """Scale BGR image uniformly so height == ``target_h`` (width follows)."""
    if img_bgr is None or img_bgr.size == 0 or target_h < 1:
        return img_bgr
    h0, w0 = img_bgr.shape[:2]
    if h0 < 1 or w0 < 1:
        return img_bgr
    scale = target_h / float(h0)
    tw = max(1, int(round(w0 * scale)))
    th = target_h
    return cv2.resize(img_bgr, (tw, th), interpolation=cv_resize_interp(w0, h0, tw, th))


def _load_bgr_via_pil(path: Path) -> np.ndarray | None:
    """Fallback when OpenCV cannot decode (common on some Pi JPEG builds)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
        if rgb.size == 0:
            return None
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except OSError:
        return None


def load_bgr(path: Path | str) -> np.ndarray | None:
    """Load image as BGR uint8, or None."""
    p = Path(path)
    if not p.is_file():
        return None
    raw = cv2.imread(str(p), cv2.IMREAD_COLOR)
    if raw is None or raw.size == 0:
        raw = _load_bgr_via_pil(p)
        if raw is None or raw.size == 0:
            return None
    if raw.ndim == 2:
        return cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
    return raw


def backdrop_master_bgr_from_file(path: Path | str) -> np.ndarray | None:
    """Load file and scale uniformly to ``BACKDROP_MASTER_HEIGHT`` tall."""
    bgr = load_bgr(path)
    if bgr is None:
        return None
    return bgr_scale_uniform_height(bgr, BACKDROP_MASTER_HEIGHT)


def _bgr_uniform_fit_letterbox_center(
    bgr: np.ndarray,
    target_w: int,
    target_h: int,
    *,
    fill_bgr: tuple[int, int, int] = (0, 0, 0),
) -> np.ndarray:
    """Scale uniformly so the image fits entirely inside ``target_w``×``target_h``; center on ``fill_bgr``."""
    out = np.zeros((max(1, target_h), max(1, target_w), 3), dtype=np.uint8)
    out[:, :] = fill_bgr
    if bgr is None or bgr.size == 0 or target_w < 1 or target_h < 1:
        return out
    sh, sw = bgr.shape[:2]
    if sh < 1 or sw < 1:
        return out
    scale = min(target_w / float(sw), target_h / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv_resize_interp(sw, sh, nw, nh))
    x0 = (target_w - nw) // 2
    y0 = (target_h - nh) // 2
    out[y0 : y0 + nh, x0 : x0 + nw] = resized
    return out


def app_logo_fallback_master_bgr(
    bgr: np.ndarray,
    *,
    display_w: int,
    display_h: int,
    fraction: float = 0.9,
) -> np.ndarray:
    """
    Letterbox the streaming-app logo onto black at ``fraction``×(display_w×display_h).

    The bitmap is exactly ``cap_w``×``cap_h`` with the logo uniformly scaled **inside** (never cropped).
    A second letterbox pass in ``build_backdrop_design_layer_bgr`` then fits that into the grid patch.
    """
    if bgr is None or bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] != 3:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    sh, sw = bgr.shape[:2]
    if sh < 1 or sw < 1:
        return np.zeros((1, 1, 3), dtype=np.uint8)

    rw = max(1, int(display_w))
    rh = max(1, int(display_h))
    frac = max(0.05, min(1.0, float(fraction)))
    cap_w = max(1, int(round(rw * frac)))
    cap_h = max(1, int(round(rh * frac)))
    return _bgr_uniform_fit_letterbox_center(bgr, cap_w, cap_h, fill_bgr=(0, 0, 0))


def _bgr_uniform_cover_center_crop(bgr: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Scale uniformly until the image covers ``target_w``×``target_h``, then center-crop to that size."""
    if bgr is None or bgr.size == 0 or target_w < 1 or target_h < 1:
        return np.zeros((max(1, target_h), max(1, target_w), 3), dtype=np.uint8)
    sh, sw = bgr.shape[:2]
    if sh < 1 or sw < 1:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)
    scale = max(target_w / float(sw), target_h / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv_resize_interp(sw, sh, nw, nh))
    x0 = max(0, (nw - target_w) // 2)
    y0 = max(0, (nh - target_h) // 2)
    return resized[y0 : y0 + target_h, x0 : x0 + target_w].copy()


def _paste_bgr_patch(
    canvas: np.ndarray,
    patch: np.ndarray,
    tl_x: int,
    tl_y: int,
) -> None:
    """Paste ``patch`` onto ``canvas`` with top-left ``(tl_x, tl_y)``, clipping to canvas bounds."""
    ch, cw = canvas.shape[:2]
    ph, pw = patch.shape[:2]
    dst_x0 = max(0, tl_x)
    dst_y0 = max(0, tl_y)
    dst_x1 = min(cw, tl_x + pw)
    dst_y1 = min(ch, tl_y + ph)
    if dst_x0 >= dst_x1 or dst_y0 >= dst_y1:
        return
    src_x0 = dst_x0 - tl_x
    src_y0 = dst_y0 - tl_y
    src_x1 = src_x0 + (dst_x1 - dst_x0)
    src_y1 = src_y0 + (dst_y1 - dst_y0)
    canvas[dst_y0:dst_y1, dst_x0:dst_x1] = patch[src_y0:src_y1, src_x0:src_x1]


def _app_logo_clock_saver_design_layer_bgr(master_bgr: np.ndarray) -> np.ndarray:
    """Black design canvas; letterboxed logo in the 5×2 cell top-right at (1.5, 13), dimmed by opacity."""
    canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
    if master_bgr is None or master_bgr.size == 0 or master_bgr.ndim != 3 or master_bgr.shape[2] != 3:
        return canvas
    sh, sw = int(master_bgr.shape[0]), int(master_bgr.shape[1])
    if sh < 1 or sw < 1:
        return canvas
    wx, wy, box_w, box_h = rect_for_span_top_right_at_cell(
        APP_LOGO_CLOCK_SAVER_SPAN_W,
        APP_LOGO_CLOCK_SAVER_SPAN_H,
        row_1based=APP_LOGO_CLOCK_SAVER_ROW_1BASED,
        col_right_1based=APP_LOGO_CLOCK_SAVER_COL_RIGHT_1BASED,
    )
    if box_w < 1 or box_h < 1:
        return canvas
    scale = min(box_w / float(sw), box_h / float(sh))
    nw = max(1, int(round(sw * scale)))
    nh = max(1, int(round(sh * scale)))
    resized = cv2.resize(
        master_bgr, (nw, nh), interpolation=cv_resize_interp(sw, sh, nw, nh)
    )
    x0 = wx + max(0, (box_w - nw) // 2)
    y0 = wy + max(0, (box_h - nh) // 2)
    y1 = min(DESIGN_H, y0 + nh)
    x1 = min(DESIGN_W, x0 + nw)
    if y1 <= y0 or x1 <= x0:
        return canvas
    patch = resized[: y1 - y0, : x1 - x0, :]
    dim = (patch.astype(np.float32) * float(APP_LOGO_CLOCK_SAVER_OPACITY)).astype(np.uint8)
    canvas[y0:y1, x0:x1] = dim
    return canvas


def build_backdrop_design_layer_bgr(
    master_h2160_bgr: np.ndarray,
    *,
    app_logo_letterbox_fit: bool = False,
    app_logo_clock_saver_style: bool = False,
) -> np.ndarray:
    """
    ``DESIGN_W``×``DESIGN_H`` canvas with backdrop.

    Default: the master **covers** the full design canvas (center-crop), edge-to-edge.

    ``app_logo_letterbox_fit``: **black** canvas; the image **fits** inside the grid patch from
    ``BACKDROP_PATCH_*`` (letterbox) so wide/tall app logos are never cropped.

    ``app_logo_clock_saver_style`` (with ``app_logo_letterbox_fit``): ignore the TMDb patch; letterbox the
    logo into the grid span at ``APP_LOGO_CLOCK_SAVER_*`` and multiply RGB by ``APP_LOGO_CLOCK_SAVER_OPACITY``.
    """
    if app_logo_letterbox_fit and app_logo_clock_saver_style:
        return _app_logo_clock_saver_design_layer_bgr(master_h2160_bgr)
    if app_logo_letterbox_fit:
        canvas = np.zeros((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
    else:
        b, g, r = get_stage_bgr()
        canvas = np.empty((DESIGN_H, DESIGN_W, 3), dtype=np.uint8)
        canvas[:] = (b, g, r)
    if master_h2160_bgr is None or master_h2160_bgr.size == 0:
        return canvas

    if app_logo_letterbox_fit:
        r_tl = rect_for_span_at_cell(
            1,
            1,
            row_1based=BACKDROP_PATCH_TL_ROW_1BASED,
            col_1based=BACKDROP_PATCH_TL_COL_1BASED,
        )
        r_br = rect_for_span_at_cell(
            1,
            1,
            row_1based=BACKDROP_PATCH_BR_ROW_1BASED,
            col_1based=BACKDROP_PATCH_BR_COL_1BASED,
        )
        tl_x, tl_y = int(r_tl[0]), int(r_tl[1])
        br_x = int(r_br[0] + r_br[2] - 1)
        br_y = int(r_br[1] + r_br[3] - 1)
        box_w = br_x - tl_x + 1
        box_h = br_y - tl_y + 1
        if box_w < 1 or box_h < 1:
            return canvas
        patch = _bgr_uniform_fit_letterbox_center(master_h2160_bgr, box_w, box_h, fill_bgr=(0, 0, 0))
        _paste_bgr_patch(canvas, patch, tl_x, tl_y)
        return canvas

    patch = _bgr_uniform_cover_center_crop(master_h2160_bgr, DESIGN_W, DESIGN_H)
    canvas[:, :] = patch
    return canvas


def _fit_bgr_scale_height_center_crop_or_pad(frame_bgr: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Scale/crop design-sized backdrop to ``target_w``×``target_h`` (cover: fills window, center-crop)."""
    if frame_bgr is None or frame_bgr.size == 0:
        return np.zeros((max(1, target_h), max(1, target_w), 3), dtype=np.uint8)
    src_h, src_w = frame_bgr.shape[:2]
    if src_h <= 0 or src_w <= 0:
        return np.zeros((target_h, target_w, 3), dtype=np.uint8)
    return scale_cover_center_crop(frame_bgr, target_w, target_h)


def backdrop_scene_bgr_for_display(
    master_h2160_bgr: np.ndarray,
    target_w: int,
    target_h: int,
    *,
    app_logo_letterbox_fit: bool = False,
    app_logo_clock_saver_style: bool = False,
) -> np.ndarray:
    """
    Build the design-resolution backdrop layer (stage or black + placed patch), then scale/crop to ``target_w``×``target_h``
    using the same height-first fit as scene video frames.
    """
    if target_w < 1 or target_h < 1:
        return np.zeros((max(1, target_h), max(1, target_w), 3), dtype=np.uint8)
    design = build_backdrop_design_layer_bgr(
        master_h2160_bgr,
        app_logo_letterbox_fit=app_logo_letterbox_fit,
        app_logo_clock_saver_style=app_logo_clock_saver_style,
    )
    return _fit_bgr_scale_height_center_crop_or_pad(design, target_w, target_h)


def medium_poster_bgr_for_active_widget(img_bgr_or_bgra: np.ndarray) -> np.ndarray:
    """Pipeline for current 4×6 medium poster: master → 59% scale → BGR on black."""
    master = poster_master_bgra_from_source(img_bgr_or_bgra)
    scaled = scale_master_for_widget_size(master, PosterWidgetSize.MEDIUM_4X6)
    return bgra_to_bgr_on_black(scaled)


def pulled_path_is_under_pulled_dir(src: Path) -> bool:
    try:
        src_r = src.resolve()
        pulled = pigeon_pulled_media_dir().resolve()
        if src_r == pulled:
            return True
        return pulled in src_r.parents
    except (OSError, ValueError):
        return False


def reformat_pulled_to_reformatted(
    src_path: Path | str,
    *,
    dest_basename: str,
    auto_delete_setting: bool | None = None,
) -> tuple[bool, str]:
    """
    Read ``src_path``, build master BGRA, save ``dest_basename``.png under pigeonReFormattedMedia.
    If ``auto_delete_setting`` is True (or None → read from app state) and the source lies under
    pigeonPulledMedia, delete the source file after a successful write.
    """
    src = Path(src_path)
    raw = load_image_bgra(src)
    if raw is None:
        return False, f"Could not read image: {src}"

    master = poster_master_bgra_from_source(raw)
    out_dir = ensure_reformatted_media_dir()
    safe = dest_basename.strip().replace("/", "_").replace("\\", "_")
    if not safe:
        return False, "Invalid destination basename."
    out_path = out_dir / f"{safe}.png"
    if not save_png_bgra(out_path, master):
        return False, f"Could not write {out_path.name}"

    do_delete = auto_delete_setting if auto_delete_setting is not None else auto_delete_pulled_media()
    if do_delete and pulled_path_is_under_pulled_dir(src):
        try:
            src.unlink()
        except OSError as e:
            return True, f"Saved {out_path.name} (could not delete pulled original: {e})"
        return True, f"Saved {out_path.name}; removed pulled original."
    return True, f"Saved {out_path.name}."
