"""Horizontal capsule (pill) mask — same geometry as the now-playing progress bar."""

from __future__ import annotations

import cv2
import numpy as np


def pill_alpha_mask(bw: int, bh: int) -> np.ndarray:
    """Return uint8 mask, 255 inside the pill, 0 outside."""
    mask = np.zeros((max(1, bh), max(1, bw)), dtype=np.uint8)
    if bw < 1 or bh < 1:
        return mask
    radius = max(1, min(bh // 2, bw // 2))
    cy = bh // 2
    lx = radius
    rx = bw - 1 - radius
    if lx <= rx:
        cv2.rectangle(mask, (lx, 0), (rx, bh - 1), 255, thickness=-1)
        cv2.circle(mask, (lx, cy), radius, 255, thickness=-1)
        cv2.circle(mask, (rx, cy), radius, 255, thickness=-1)
    else:
        cv2.circle(mask, (bw // 2, cy), max(1, bw // 2), 255, thickness=-1)
    return mask


def pill_bgra_black(bw: int, bh: int, *, alpha: int = 255) -> np.ndarray:
    """BGRA patch: black with capsule alpha (``alpha`` scales the mask)."""
    bw = max(1, int(bw))
    bh = max(1, int(bh))
    patch = np.zeros((bh, bw, 4), dtype=np.uint8)
    m = pill_alpha_mask(bw, bh).astype(np.float32) * (max(0, min(255, alpha)) / 255.0)
    patch[:, :, 3] = m.astype(np.uint8)
    return patch
