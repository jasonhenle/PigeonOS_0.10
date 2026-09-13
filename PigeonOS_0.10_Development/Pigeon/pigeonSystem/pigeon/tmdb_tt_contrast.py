"""Pick a high-contrast bottom-gradient tint based on the TMDb title treatment (TT) brightness.

The bottom gradient (tint ramp, then full opacity to the canvas bottom) visually anchors the composition. When the
``PigeonTMDB_TT`` art is dominantly **bright** (e.g. white logotype), we want the gradient to
stay **black** so the bottom chrome recedes. When the TT art is dominantly **dark**, we flip the
gradient to **white** so it contrasts against the dark logo instead of blending into it.

``relative_luminance`` uses BT.601 coefficients on the visible (non-transparent) pixels only;
transparent padding in cached TMDb logo PNGs is ignored so it doesn't skew the score toward 0.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

# Alpha below this is considered "not part of the logo" (antialiasing edges / transparent pad).
_VISIBLE_ALPHA_MIN = 16

# BT.601 luminance coefficients (sRGB-weighted, standard for perceived brightness).
_COEF_B = 0.114
_COEF_G = 0.587
_COEF_R = 0.299

# Default decision threshold in [0, 1]. 0.5 = middle gray pivot.
DEFAULT_LUMINANCE_THRESHOLD = 0.5

# Gradient tint options.
GRADIENT_BGR_DARK: Tuple[int, int, int] = (0, 0, 0)        # black — contrasts with bright TT art
GRADIENT_BGR_LIGHT: Tuple[int, int, int] = (255, 255, 255)  # white — contrasts with dark TT art


def relative_luminance(bgra: np.ndarray | None) -> float | None:
    """Return BT.601 luminance of the visible pixels in ``bgra`` (0..1), or ``None`` if N/A.

    Expects an HxWx4 uint8 array in **BGRA** order (the Pigeon convention). Pixels with
    ``alpha < _VISIBLE_ALPHA_MIN`` are excluded so transparent padding in TMDb logo assets
    does not bias the score toward 0.
    """
    if bgra is None or not isinstance(bgra, np.ndarray):
        return None
    if bgra.ndim != 3 or bgra.shape[2] != 4 or bgra.size == 0:
        return None
    alpha = bgra[:, :, 3]
    mask = alpha >= _VISIBLE_ALPHA_MIN
    if not bool(np.any(mask)):
        return None
    # Weighted per-pixel: alpha acts as a confidence; fully-opaque pixels dominate anti-aliased edges.
    sel = bgra[mask]
    b = sel[:, 0].astype(np.float32)
    g = sel[:, 1].astype(np.float32)
    r = sel[:, 2].astype(np.float32)
    a = sel[:, 3].astype(np.float32)
    y = (_COEF_B * b + _COEF_G * g + _COEF_R * r) / 255.0  # per-pixel luminance in [0, 1]
    wsum = float(a.sum())
    if wsum <= 0.0:
        return None
    lum = float(np.dot(y, a) / wsum)
    return max(0.0, min(1.0, lum))


def pick_gradient_bgr(
    bgra: np.ndarray | None,
    *,
    threshold: float = DEFAULT_LUMINANCE_THRESHOLD,
    dark_bgr: Tuple[int, int, int] = GRADIENT_BGR_DARK,
    light_bgr: Tuple[int, int, int] = GRADIENT_BGR_LIGHT,
) -> Tuple[Tuple[int, int, int], float | None]:
    """Return ``(gradient_bgr, measured_luminance)``.

    Bright TT (luminance >= threshold) → ``dark_bgr`` (black by default).
    Dark TT   (luminance <  threshold) → ``light_bgr`` (white by default).
    When the TT is unavailable / fully transparent, returns ``(dark_bgr, None)`` so the
    caller keeps the legacy black gradient.
    """
    lum = relative_luminance(bgra)
    if lum is None:
        return (dark_bgr, None)
    return (dark_bgr if lum >= float(threshold) else light_bgr, lum)


# Below this luminance the TT is treated as "black or close to black" and is
# recolored pure white for display on dark widgets (e.g. the countdown card).
DARK_TT_LUMINANCE_MAX = 0.25


def whiten_dark_tt_bgra(
    bgra: np.ndarray | None,
    *,
    threshold: float = DARK_TT_LUMINANCE_MAX,
) -> np.ndarray | None:
    """Return the TT unchanged unless it is black / near-black — then pure white.

    Dark logos (visible-pixel luminance below ``threshold``) get their RGB
    replaced with pure white while the alpha channel (shape + anti-aliased
    edges) is preserved. Anything brighter passes through untouched.
    """
    if bgra is None or not isinstance(bgra, np.ndarray):
        return bgra
    if bgra.ndim != 3 or bgra.shape[2] != 4 or bgra.size == 0:
        return bgra
    lum = relative_luminance(bgra)
    if lum is None or lum >= float(threshold):
        return bgra
    out = bgra.copy()
    out[:, :, 0] = 255
    out[:, :, 1] = 255
    out[:, :, 2] = 255
    return out


# Visible pixels below this mean HSV saturation are treated as ink / paper, not a hue.
_THEME_MIN_SAT = 28
# Need at least this many chromatic pixels so anti-aliased edges don't invent a tint.
_THEME_MIN_CHROMA_PX = 12


def theme_hex_from_tt_bgra(bgra: np.ndarray | None) -> str | None:
    """Saturated ``#RRGGBB`` from visible TT pixels, or ``None`` if the logo is gray/empty.

    White / black title treatments have no hue — callers should keep the settings
    UI color. Colored logotypes (red wordmarks, etc.) yield a boosted UI swatch
    suitable for clock ticks, volume pie, and the status-bar fill.
    """
    bgr = theme_bgr_from_tt_bgra(bgra)
    if bgr is None:
        return None
    b, g, r = bgr
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def theme_bgr_from_tt_bgra(bgra: np.ndarray | None) -> tuple[int, int, int] | None:
    """BGR accent from visible TT pixels, or ``None`` when there is no usable hue."""
    if bgra is None or not isinstance(bgra, np.ndarray):
        return None
    if bgra.ndim != 3 or bgra.shape[2] < 3 or bgra.size == 0:
        return None
    h0, w0 = bgra.shape[:2]
    if h0 < 2 or w0 < 2:
        return None
    try:
        import cv2
    except Exception:
        return None
    if bgra.shape[2] == 4:
        alpha = bgra[:, :, 3]
        mask = alpha >= _VISIBLE_ALPHA_MIN
        if int(np.count_nonzero(mask)) < _THEME_MIN_CHROMA_PX:
            return None
        bgr = np.ascontiguousarray(bgra[:, :, :3])
    else:
        mask = np.ones((h0, w0), dtype=bool)
        bgr = np.ascontiguousarray(bgra[:, :, :3])
    # Downscale so a large logo is cheap; keep the alpha mask aligned.
    tw, th = 96, 64
    small = cv2.resize(bgr, (tw, th), interpolation=cv2.INTER_AREA)
    mask_u8 = mask.astype(np.uint8) * 255
    mask_s = cv2.resize(mask_u8, (tw, th), interpolation=cv2.INTER_AREA) >= 128
    if int(np.count_nonzero(mask_s)) < _THEME_MIN_CHROMA_PX:
        return None
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]
    chroma = mask_s & (sat >= _THEME_MIN_SAT) & (val >= 24)
    if int(np.count_nonzero(chroma)) < _THEME_MIN_CHROMA_PX:
        return None
    scores = sat.astype(np.float32) * val.astype(np.float32)
    scores = np.where(chroma, scores, 0.0)
    flat = scores.ravel()
    k = max(_THEME_MIN_CHROMA_PX, int(round(0.12 * float(np.count_nonzero(chroma)))))
    k = min(k, int(np.count_nonzero(chroma)))
    idx = np.argpartition(flat, -k)[-k:]
    ys, xs = np.unravel_index(idx, scores.shape)
    pick = small[ys, xs]
    b_acc = int(np.median(pick[:, 0]))
    g_acc = int(np.median(pick[:, 1]))
    r_acc = int(np.median(pick[:, 2]))
    px = np.uint8([[[b_acc, g_acc, r_acc]]])
    hsv_p = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)
    hsv_p[0, 0, 1] = min(255, max(140, int(hsv_p[0, 0, 1]) + 40))
    hsv_p[0, 0, 2] = min(255, max(160, int(hsv_p[0, 0, 2]) + 30))
    out = cv2.cvtColor(hsv_p, cv2.COLOR_HSV2BGR)[0, 0]
    return (int(out[0]), int(out[1]), int(out[2]))
