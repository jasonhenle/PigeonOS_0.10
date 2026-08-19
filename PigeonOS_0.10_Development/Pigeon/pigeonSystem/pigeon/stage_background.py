"""
Global stage background BGR for video area, letterboxing, and empty compositor layers.

Derived from the active poster where possible; constrained to luma ≤ ~50% gray (no white / light grays).
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

# Rec. 601 luma on 0–255 RGB (R,G,B order); reject anything lighter than mid-gray.
_MAX_LUMA = 127.49

_STAGE_BGR: tuple[int, int, int] = (0, 0, 0)


def get_stage_bgr() -> tuple[int, int, int]:
    return _STAGE_BGR


def set_stage_bgr(b: int, g: int, r: int) -> None:
    global _STAGE_BGR
    _STAGE_BGR = (int(b) & 255, int(g) & 255, int(r) & 255)


def bgr_to_tk_hex(b: int, g: int, r: int) -> str:
    return f"#{r & 255:02x}{g & 255:02x}{b & 255:02x}"


def _luma_from_bgr_pixel(b: float, g: float, r: float) -> float:
    return 0.299 * r + 0.587 * g + 0.114 * b


def _is_near_white_gray(b: float, g: float, r: float) -> bool:
    mx, mn = max(r, g, b), min(r, g, b)
    return mn > 235 and (mx - mn) < 12


def dominant_dark_bgr_from_poster_file(path: Path) -> tuple[int, int, int]:
    """
    k-means clusters on a downsampled poster; pick the largest cluster that is dark enough.
    If none qualify, darken the global mean until luma ≤ threshold.
    """
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None or img.size == 0:
        return (0, 0, 0)

    h, w = img.shape[:2]
    if h < 1 or w < 1:
        return (0, 0, 0)

    side = 128
    scale = side / float(max(h, w))
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    small = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_AREA)
    pixels = small.reshape(-1, 3).astype(np.float32)
    n = pixels.shape[0]
    if n > 10000:
        rng = np.random.default_rng(0)
        pick = rng.choice(n, size=10000, replace=False)
        pixels = pixels[pick]
        n = pixels.shape[0]

    if n < 8:
        mean = pixels.mean(axis=0)
        b, g, r = float(mean[0]), float(mean[1]), float(mean[2])
        y = _luma_from_bgr_pixel(b, g, r)
        if y < 1.0:
            return (0, 0, 0)
        factor = min(1.0, (_MAX_LUMA / y) * 0.92)
        out = np.clip(np.array([b, g, r], dtype=np.float32) * factor, 0, 255).astype(np.uint8)
        for _ in range(24):
            if _luma_from_bgr_pixel(float(out[0]), float(out[1]), float(out[2])) <= _MAX_LUMA:
                break
            out = np.clip(out.astype(np.float32) * 0.88, 0, 255).astype(np.uint8)
        if _luma_from_bgr_pixel(float(out[0]), float(out[1]), float(out[2])) > _MAX_LUMA:
            return (28, 28, 32)
        return (int(out[0]), int(out[1]), int(out[2]))

    k = max(2, min(8, n // 400))
    k = min(k, n)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.5)
    _compactness, labels, centers = cv2.kmeans(
        pixels,
        k,
        None,
        criteria,
        3,
        cv2.KMEANS_PP_CENTERS,
    )
    labels = labels.reshape(-1)
    counts = np.bincount(labels, minlength=k)
    order = np.argsort(-counts)

    for j in order:
        c = centers[j]
        b, g, r = float(c[0]), float(c[1]), float(c[2])
        if _luma_from_bgr_pixel(b, g, r) > _MAX_LUMA:
            continue
        if _is_near_white_gray(b, g, r):
            continue
        out = np.clip(c, 0, 255).astype(np.uint8)
        return (int(out[0]), int(out[1]), int(out[2]))

    mean = pixels.mean(axis=0)
    b, g, r = float(mean[0]), float(mean[1]), float(mean[2])
    y = _luma_from_bgr_pixel(b, g, r)
    if y < 1.0:
        return (0, 0, 0)
    factor = min(1.0, (_MAX_LUMA / y) * 0.92)
    out = np.clip(np.array([b, g, r], dtype=np.float32) * factor, 0, 255).astype(np.uint8)
    for _ in range(24):
        if _luma_from_bgr_pixel(float(out[0]), float(out[1]), float(out[2])) <= _MAX_LUMA:
            break
        out = np.clip(out.astype(np.float32) * 0.88, 0, 255).astype(np.uint8)
    if _luma_from_bgr_pixel(float(out[0]), float(out[1]), float(out[2])) > _MAX_LUMA:
        return (28, 28, 32)
    return (int(out[0]), int(out[1]), int(out[2]))
