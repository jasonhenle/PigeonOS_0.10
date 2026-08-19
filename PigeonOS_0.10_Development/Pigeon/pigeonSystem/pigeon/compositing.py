"""Image blending and scaling helpers."""

from __future__ import annotations

import cv2
import numpy as np


def cv_resize_interp(src_w: int, src_h: int, dst_w: int, dst_h: int) -> int:
    """INTER_AREA when shrinking, LANCZOS4 when enlarging, CUBIC near 1:1 — less aliasing than LINEAR."""
    if src_w < 1 or src_h < 1 or dst_w < 1 or dst_h < 1:
        return cv2.INTER_LINEAR
    sa = float(src_w * src_h)
    da = float(dst_w * dst_h)
    if da > sa * 1.01:
        return cv2.INTER_LANCZOS4
    if da < sa * 0.99:
        return cv2.INTER_AREA
    return cv2.INTER_CUBIC


def bgr_to_red_monochrome_luma(bgr: np.ndarray) -> np.ndarray:
    """Rec. 601 luma mapped to the red channel only (BGR): dark → black, bright → red."""
    if bgr.ndim != 3 or bgr.shape[2] < 3:
        return bgr
    b = bgr[:, :, 0].astype(np.float32)
    g = bgr[:, :, 1].astype(np.float32)
    r = bgr[:, :, 2].astype(np.float32)
    y = 0.114 * b + 0.587 * g + 0.299 * r
    y_u8 = np.clip(y, 0, 255).astype(np.uint8)
    out = np.zeros_like(bgr)
    out[:, :, 2] = y_u8
    return out


def lerp_bgr_red_monochrome(bgr: np.ndarray, strength: float) -> np.ndarray:
    """Blend ``bgr`` toward luma→red monochrome; ``strength`` 0 = original, 1 = full mono."""
    s = float(max(0.0, min(1.0, strength)))
    if s <= 0.0:
        return bgr
    red = bgr_to_red_monochrome_luma(bgr)
    if s >= 1.0 - 1e-6:
        return red
    return cv2.addWeighted(bgr, 1.0 - s, red, s, 0)


def scale_bgra_rgb(bgra: np.ndarray, rgb_factor: float) -> np.ndarray:
    """Scale BGR channels only; alpha unchanged. Used for idle-dim per-widget tuning."""
    f = float(rgb_factor)
    if f >= 0.999:
        return bgra
    if f <= 0.0:
        out = np.zeros_like(bgra)
        out[:, :, 3] = bgra[:, :, 3]
        return out
    out = bgra.copy()
    out[:, :, :3] = np.clip(bgra[:, :, :3].astype(np.float32) * f, 0, 255).astype(np.uint8)
    return out


def alpha_blend_bgra_over_bgr(base_bgr: np.ndarray, overlay_bgra: np.ndarray) -> np.ndarray:
    if base_bgr.shape[:2] != overlay_bgra.shape[:2]:
        raise ValueError("Overlay and base frame sizes must match")

    alpha_u8 = overlay_bgra[:, :, 3]
    # Fully opaque settings UI (common case): skip float convert of the whole frame.
    if int(alpha_u8.min()) == 255:
        return overlay_bgra[:, :, :3].copy()
    if not np.any(alpha_u8):
        return base_bgr.copy()

    opaque = alpha_u8 == 255
    partial = (alpha_u8 > 0) & (alpha_u8 < 255)
    out = base_bgr.copy()
    if np.any(opaque):
        out[opaque] = overlay_bgra[opaque, :3]
    if np.any(partial):
        alpha = overlay_bgra[partial, 3:4].astype(np.float32) * (1.0 / 255.0)
        fg = overlay_bgra[partial, :3].astype(np.float32)
        bg = out[partial].astype(np.float32)
        out[partial] = fg * alpha + bg * (1.0 - alpha)
    return out.astype(np.uint8)


def scale_height_and_center_crop(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Scale by height to target_h, then center-crop horizontally to target_w."""
    src_h, src_w = image.shape[:2]
    scale = target_h / float(src_h)
    scaled_w = int(round(src_w * scale))
    resized = cv2.resize(
        image,
        (scaled_w, target_h),
        interpolation=cv_resize_interp(src_w, src_h, scaled_w, target_h),
    )

    if scaled_w < target_w:
        pad = target_w - scaled_w
        left = pad // 2
        right = pad - left
        if resized.ndim == 3 and resized.shape[2] == 4:
            pad_value = (0, 0, 0, 0)
        else:
            pad_value = (0, 0, 0)
        return cv2.copyMakeBorder(
            resized,
            top=0,
            bottom=0,
            left=left,
            right=right,
            borderType=cv2.BORDER_CONSTANT,
            value=pad_value,
        )

    x0 = (scaled_w - target_w) // 2
    x1 = x0 + target_w
    return resized[:, x0:x1]


def scale_cover_center_crop(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """
    Uniform scale so the image **covers** ``target_w``×``target_h``, then center-crop excess.
    Fills the target (no letterboxing); may discard content at edges. Supports BGR or BGRA.
    """
    src_h, src_w = image.shape[:2]
    if src_w < 1 or src_h < 1 or target_w < 1 or target_h < 1:
        ch = 3 if image.ndim < 3 else int(image.shape[2])
        return np.zeros((max(1, target_h), max(1, target_w), ch), dtype=image.dtype)

    scale = max(target_w / float(src_w), target_h / float(src_h))
    scaled_w = max(1, int(round(src_w * scale)))
    scaled_h = max(1, int(round(src_h * scale)))
    resized = cv2.resize(
        image,
        (scaled_w, scaled_h),
        interpolation=cv_resize_interp(src_w, src_h, scaled_w, scaled_h),
    )
    x0 = max(0, (scaled_w - target_w) // 2)
    y0 = max(0, (scaled_h - target_h) // 2)
    return resized[y0 : y0 + target_h, x0 : x0 + target_w].copy()


def scale_uniform_letterbox(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """
    Uniform scale so the **entire** image fits inside ``target_w``×``target_h``, centered on black bars.

    Unlike ``scale_height_and_center_crop`` (scale-to-height then crop width), this never discards
    horizontal content. Used for developer grid mode so narrow windows (e.g. 800×480) still show
    design columns 1–2 instead of cropping them off.
    """
    src_h, src_w = image.shape[:2]
    if src_w < 1 or src_h < 1 or target_w < 1 or target_h < 1:
        ch = 3 if image.ndim < 3 else int(image.shape[2])
        return np.zeros((max(1, target_h), max(1, target_w), ch), dtype=image.dtype)

    scale = min(target_w / float(src_w), target_h / float(src_h))
    nw = max(1, min(target_w, int(round(src_w * scale))))
    nh = max(1, min(target_h, int(round(src_h * scale))))
    resized = cv2.resize(
        image, (nw, nh), interpolation=cv_resize_interp(src_w, src_h, nw, nh)
    )

    pad_w = target_w - nw
    pad_h = target_h - nh
    left = max(0, pad_w // 2)
    right = max(0, pad_w - left)
    top = max(0, pad_h // 2)
    bottom = max(0, pad_h - top)

    if image.ndim == 3 and image.shape[2] == 4:
        pad_value = (0, 0, 0, 255)
    else:
        pad_value = (0, 0, 0)

    return cv2.copyMakeBorder(
        resized,
        top=top,
        bottom=bottom,
        left=left,
        right=right,
        borderType=cv2.BORDER_CONSTANT,
        value=pad_value,
    )
