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
