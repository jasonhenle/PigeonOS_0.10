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
    if s >= 1.0:
        return red
    a = float(s)
    mixed = bgr.astype(np.float32) * (1.0 - a) + red.astype(np.float32) * a
    return np.clip(mixed, 0, 255).astype(np.uint8)


# Artwork blur sits at ~24% over black, so its luma is ≲ 61. Values below this
# are the wash, not UI chrome; snapping them to ink posterizes the blur.
_BRIGHT_WASH_LUMA = 72.0


def swap_achromatic_black_white_bgr(bgr: np.ndarray) -> np.ndarray:
    """Light-mode look: black backgrounds go white; gray and white chrome go black.

    Saturated UI colors (blue, etc.) are left alone. The dim artwork-blur wash
    — including saturated stage light at 24% over black — is inverted so it
    stays a soft texture. A chroma-only wash left those tints as hard dark
    silhouettes.
    """
    if bgr is None or bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] < 3:
        return bgr
    pix = bgr[:, :, :3].astype(np.int16)
    b = pix[:, :, 0]
    g = pix[:, :, 1]
    r = pix[:, :, 2]
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    chroma = mx - mn
    y = 0.114 * b.astype(np.float32) + 0.587 * g.astype(np.float32) + 0.299 * r.astype(np.float32)
    wash = y < _BRIGHT_WASH_LUMA
    ink = (chroma < 28) & ~wash
    out = bgr.copy()
    if np.any(wash):
        inv = np.clip(255 - pix, 0, 255).astype(np.uint8)
        out[:, :, :3] = np.where(wash[..., None], inv, out[:, :, :3])
    out[ink, 0] = 0
    out[ink, 1] = 0
    out[ink, 2] = 0
    return out


_BRIGHT_ARTWORK_MASK: np.ndarray | None = None
_BRIGHT_SLANT_MASK: np.ndarray | None = None


def clear_bright_artwork_mask() -> None:
    """Drop the design-space poster/thumbnail protect mask."""
    global _BRIGHT_ARTWORK_MASK
    _BRIGHT_ARTWORK_MASK = None


def clear_bright_slant_mask() -> None:
    """Drop the settings-plate slant protect mask."""
    global _BRIGHT_SLANT_MASK
    _BRIGHT_SLANT_MASK = None


def remember_bright_slant_plate(plate: np.ndarray | None) -> None:
    """Keep gray/UI slants through the light-mode swap (black plate still inverts)."""
    global _BRIGHT_SLANT_MASK
    if plate is None or plate.size == 0 or plate.ndim < 3:
        _BRIGHT_SLANT_MASK = None
        return
    b = plate[:, :, 0].astype(np.float32)
    g = plate[:, :, 1].astype(np.float32)
    r = plate[:, :, 2].astype(np.float32)
    y = 0.114 * b + 0.587 * g + 0.299 * r
    a = plate[:, :, 3] if plate.shape[2] >= 4 else np.full(plate.shape[:2], 255, np.uint8)
    sel = (a > 8) & (y > 20.0)
    if not np.any(sel):
        _BRIGHT_SLANT_MASK = None
        return
    _BRIGHT_SLANT_MASK = sel.astype(np.uint8)


def stamp_bright_artwork_mask(alpha: np.ndarray, x: int, y: int) -> None:
    """OR-in a design-space alpha patch so bright mode leaves artwork alone."""
    global _BRIGHT_ARTWORK_MASK
    if alpha is None or alpha.size == 0:
        return
    from pigeon.design import DESIGN_H, DESIGN_W

    if alpha.ndim == 3:
        a = alpha[:, :, 3] if alpha.shape[2] >= 4 else np.full(alpha.shape[:2], 255, np.uint8)
    else:
        a = alpha
    ph, pw = int(a.shape[0]), int(a.shape[1])
    x0 = int(x)
    y0 = int(y)
    x1 = x0 + pw
    y1 = y0 + ph
    if x1 <= 0 or y1 <= 0 or x0 >= DESIGN_W or y0 >= DESIGN_H:
        return
    sx0 = max(0, -x0)
    sy0 = max(0, -y0)
    dx0 = max(0, x0)
    dy0 = max(0, y0)
    dx1 = min(DESIGN_W, x1)
    dy1 = min(DESIGN_H, y1)
    if dx1 <= dx0 or dy1 <= dy0:
        return
    if _BRIGHT_ARTWORK_MASK is None:
        _BRIGHT_ARTWORK_MASK = np.zeros((DESIGN_H, DESIGN_W), dtype=np.uint8)
    _BRIGHT_ARTWORK_MASK[dy0:dy1, dx0:dx1] |= (
        a[sy0 : sy0 + (dy1 - dy0), sx0 : sx0 + (dx1 - dx0)] > 8
    ).astype(np.uint8)


def _present_artwork_mask(mask: np.ndarray, frame_w: int, frame_h: int) -> np.ndarray:
    """Map the design-space protect mask onto a presented (letterboxed / PAR) frame."""
    mh, mw = int(mask.shape[0]), int(mask.shape[1])
    if mh == frame_h and mw == frame_w:
        return mask
    rgb = np.repeat(mask[:, :, None], 3, axis=2)
    try:
        from pigeon.display_par import apply_par_compensation

        mapped = apply_par_compensation(rgb, display_w=frame_w, display_h=frame_h)
    except Exception:
        mapped = scale_uniform_letterbox(rgb, frame_w, frame_h)
    return (mapped[:, :, 0] > 0).astype(np.uint8)


def _combined_bright_protect_mask() -> np.ndarray | None:
    art = _BRIGHT_ARTWORK_MASK
    slant = _BRIGHT_SLANT_MASK
    if art is None:
        return slant
    if slant is None:
        return art
    if art.shape != slant.shape:
        return art
    return np.maximum(art, slant)


def restore_bright_artwork_pixels(original: np.ndarray, swapped: np.ndarray) -> np.ndarray:
    """Copy poster/thumbnail and settings-slant pixels back after a bright swap."""
    mask = _combined_bright_protect_mask()
    if (
        mask is None
        or original is None
        or swapped is None
        or original.shape[:2] != swapped.shape[:2]
        or not np.any(mask)
    ):
        return swapped
    fh, fw = int(swapped.shape[0]), int(swapped.shape[1])
    mapped = _present_artwork_mask(mask, fw, fh)
    if mapped.shape[0] != fh or mapped.shape[1] != fw:
        return swapped
    sel = mapped > 0
    if not np.any(sel):
        return swapped
    out = swapped if swapped is not original else swapped.copy()
    out[sel, :3] = original[sel, :3]
    return out


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
        return base_bgr
    # Now-playing is composited onto a black canvas — skip the boolean-index blend.
    if not np.any(base_bgr):
        a = alpha_u8.astype(np.uint16)
        rgb = overlay_bgra[:, :, :3].astype(np.uint16)
        return ((rgb * a[:, :, None] + 127) // 255).astype(np.uint8)

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


def premultiply_bgra_on_black(overlay_bgra: np.ndarray) -> np.ndarray:
    """BGR of ``overlay`` as if composited onto black. Cached chrome uses this once."""
    alpha_u8 = overlay_bgra[:, :, 3]
    if int(alpha_u8.min()) == 255:
        return overlay_bgra[:, :, :3].copy()
    a = alpha_u8.astype(np.uint16)
    rgb = overlay_bgra[:, :, :3].astype(np.uint16)
    return ((rgb * a[:, :, None] + 127) // 255).astype(np.uint8)


def blend_bgra_over_bgr_u8(
    base_bgr: np.ndarray,
    overlay_bgra: np.ndarray,
    *,
    out: np.ndarray | None = None,
) -> np.ndarray:
    """Uint8 lerp without boolean indexing — cheap enough for per-frame widget rects."""
    if base_bgr.shape[:2] != overlay_bgra.shape[:2]:
        raise ValueError("Overlay and base frame sizes must match")
    alpha_u8 = overlay_bgra[:, :, 3]
    if int(alpha_u8.max()) <= 0:
        if out is None:
            return base_bgr
        if out is not base_bgr:
            out[:] = base_bgr
        return out
    if int(alpha_u8.min()) >= 255:
        rgb = overlay_bgra[:, :, :3]
        if out is None:
            return rgb
        out[:] = rgb
        return out
    a = alpha_u8.astype(np.uint16)
    ia = 255 - a
    blended = (
        overlay_bgra[:, :, :3].astype(np.uint16) * a[:, :, None]
        + base_bgr.astype(np.uint16) * ia[:, :, None]
        + 127
    ) // 255
    if out is None:
        return blended.astype(np.uint8)
    out[:] = blended
    return out


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

    Wider display than the source aspect → scale by height (pillarbox). Narrower display →
    scale by width (letterbox). Never crops. Used to present the 1280×800 design on any panel.
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
        # Transparent bars so BGRA overlays composite cleanly over underlays.
        pad_value = (0, 0, 0, 0)
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
