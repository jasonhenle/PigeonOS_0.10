"""Full-screen paused treatment: dimmed backdrop + bottom label plate."""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import cv_resize_interp
from pigeon.design import DESIGN_H

PAUSED_SCREEN_TEXT = "paused"
# Design-space (1280×800) inset from the frame bottom to the plate.
PAUSED_SCREEN_BAR_BOTTOM_PX = 50
PAUSED_SCREEN_BAR_PAD_X_PX = 48
PAUSED_SCREEN_BAR_PAD_Y_PX = 22
PAUSED_SCREEN_BAR_RADIUS_PX = 28


def cover_scale_and_crop(
    frame_bgr: np.ndarray, target_w: int, target_h: int
) -> np.ndarray:
    """Scale so *frame_bgr* fills ``target_w``×``target_h``, then center-crop.

    Square album art on a wide display is cropped top/bottom instead of
    pillarboxed. Landscape TMDb backdrops crop the sides the same way.
    """
    tw = max(1, int(target_w))
    th = max(1, int(target_h))
    if frame_bgr is None or getattr(frame_bgr, "size", 0) == 0:
        return np.zeros((th, tw, 3), dtype=np.uint8)
    src = np.ascontiguousarray(frame_bgr)
    if src.ndim == 2:
        src = cv2.cvtColor(src, cv2.COLOR_GRAY2BGR)
    elif src.ndim == 3 and src.shape[2] > 3:
        src = src[:, :, :3]
    src_h, src_w = int(src.shape[0]), int(src.shape[1])
    if src_h < 1 or src_w < 1:
        return np.zeros((th, tw, 3), dtype=np.uint8)
    scale = max(tw / float(src_w), th / float(src_h))
    nw = max(1, int(round(src_w * scale)))
    nh = max(1, int(round(src_h * scale)))
    resized = cv2.resize(
        src, (nw, nh), interpolation=cv_resize_interp(src_w, src_h, nw, nh)
    )
    x0 = max(0, (nw - tw) // 2)
    y0 = max(0, (nh - th) // 2)
    crop = resized[y0 : y0 + th, x0 : x0 + tw]
    ch, cw = int(crop.shape[0]), int(crop.shape[1])
    if ch == th and cw == tw:
        return crop
    out = np.zeros((th, tw, 3), dtype=np.uint8)
    out[: min(ch, th), : min(cw, tw)] = crop[: min(ch, th), : min(cw, tw)]
    return out


def paused_screen_scale(cap_h: int, *, design_h: int = DESIGN_H) -> float:
    return float(max(1, int(cap_h))) / float(max(1, int(design_h)))


def paused_screen_bar_rect(
    cap_w: int,
    cap_h: int,
    text_w: int,
    text_h: int,
    *,
    design_h: int = DESIGN_H,
) -> tuple[int, int, int, int, int]:
    """Return ``(x, y, w, h, radius)`` for the label plate in output pixels."""
    s = paused_screen_scale(cap_h, design_h=design_h)
    pad_x = max(8, int(round(PAUSED_SCREEN_BAR_PAD_X_PX * s)))
    pad_y = max(6, int(round(PAUSED_SCREEN_BAR_PAD_Y_PX * s)))
    bottom = max(8, int(round(PAUSED_SCREEN_BAR_BOTTOM_PX * s)))
    radius = max(8, int(round(PAUSED_SCREEN_BAR_RADIUS_PX * s)))
    bw = int(text_w) + 2 * pad_x
    bh = int(text_h) + 2 * pad_y
    max_w = max(1, int(cap_w) - 2 * bottom)
    if bw > max_w:
        bw = max_w
    bw = max(1, bw)
    bh = max(1, bh)
    x = (int(cap_w) - bw) // 2
    y = int(cap_h) - bottom - bh
    if y < 0:
        y = 0
        bh = min(bh, int(cap_h) - bottom)
    radius = min(radius, bw // 2, bh // 2)
    return (x, y, bw, bh, radius)


def paint_paused_screen_label(
    image: Image.Image,
    *,
    text: str = PAUSED_SCREEN_TEXT,
    font: ImageFont.ImageFont,
    design_h: int = DESIGN_H,
) -> tuple[int, int, int, int]:
    """Draw a rounded black plate with *text*, 50 design-px above the frame bottom.

    Returns the plate ``(x, y, w, h)``.
    """
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = int(bbox[2] - bbox[0])
    th = int(bbox[3] - bbox[1])
    x, y, bw, bh, radius = paused_screen_bar_rect(
        image.width, image.height, tw, th, design_h=design_h
    )
    box = [x, y, x + bw, y + bh]
    if hasattr(draw, "rounded_rectangle"):
        draw.rounded_rectangle(box, radius=radius, fill=(0, 0, 0))
    else:
        draw.rectangle(box, fill=(0, 0, 0))
    tx = x + (bw - tw) // 2 - int(bbox[0])
    ty = y + (bh - th) // 2 - int(bbox[1])
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255))
    return (x, y, bw, bh)
