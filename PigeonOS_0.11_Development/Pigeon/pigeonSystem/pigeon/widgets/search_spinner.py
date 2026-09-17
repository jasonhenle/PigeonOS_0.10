"""Reusable searching spinner from ``widget_general_searching_icon.svg``.

PyMuPDF ignores ``clip-path``, so the two ring stems are redrawn here with
the Illustrator masks applied before the glyph is rotated.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

_SEARCH_SPINNER_STEPS = 36
_SEARCH_ROTATION_DPS = 720.0

# widget_general_searching_icon.svg — circle + two rotated-rect clips + arrows.
_SEARCH_CX = 226.12
_SEARCH_CY = 200.66
_SEARCH_R = 51.6
_SEARCH_STROKE = 8.0
# clippath / clippath-1 after ``translate(...) rotate(-45)``.
_SEARCH_CLIP_BOTTOM = (
    (156.74, 291.36),
    (339.08, 109.02),
    (449.03, 218.98),
    (266.70, 401.31),
)
_SEARCH_CLIP_TOP = (
    (0.0, 182.33),
    (182.33, 0.0),
    (292.29, 109.95),
    (109.95, 292.29),
)
_SEARCH_ARROW_DOWN = (
    (198.64, 207.58),
    (193.77, 230.36),
    (188.40, 253.03),
    (171.11, 237.42),
    (154.16, 221.44),
    (176.32, 214.27),
)
_SEARCH_ARROW_UP = (
    (251.99, 193.99),
    (256.85, 171.21),
    (262.22, 148.55),
    (279.52, 164.15),
    (296.46, 180.13),
    (274.30, 187.31),
)
# Crop around the circle so rotation stays centered on the glyph, not the artboard.
_SEARCH_PATCH_R = 96.0


def precompute_rotated_patches(
    patch: np.ndarray,
    *,
    steps: int = _SEARCH_SPINNER_STEPS,
) -> tuple[np.ndarray, ...]:
    """Pre-render spinner rotations so animation avoids per-frame warpAffine."""
    ph, pw = patch.shape[:2]
    center = (pw * 0.5, ph * 0.5)
    frames: list[np.ndarray] = []
    step_deg = 360.0 / max(1, steps)
    for i in range(steps):
        angle = i * step_deg
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        frames.append(
            cv2.warpAffine(
                patch,
                matrix,
                (pw, ph),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0, 0),
            )
        )
    return tuple(frames)


def rotated_patch_for_angle(
    frames: tuple[np.ndarray, ...],
    angle_deg: float,
) -> np.ndarray:
    if not frames:
        raise ValueError("spinner frame cache is empty")
    step_deg = 360.0 / len(frames)
    idx = int(round(float(angle_deg) / step_deg)) % len(frames)
    return frames[idx]


def blit_spinner_patch(frame: np.ndarray, patch: np.ndarray, *, cx: int, cy: int) -> None:
    ph, pw = patch.shape[:2]
    x0 = cx - pw // 2
    y0 = cy - ph // 2
    x1 = x0 + pw
    y1 = y0 + ph
    fx0 = max(0, x0)
    fy0 = max(0, y0)
    fx1 = min(frame.shape[1], x1)
    fy1 = min(frame.shape[0], y1)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    sx0 = fx0 - x0
    sy0 = fy0 - y0
    sx1 = sx0 + (fx1 - fx0)
    sy1 = sy0 + (fy1 - fy0)
    region = frame[fy0:fy1, fx0:fx1]
    strip = patch[sy0:sy1, sx0:sx1]
    alpha = strip[:, :, 3:4].astype(np.float32) / 255.0
    fg = strip[:, :, :3].astype(np.float32)
    bg = region[:, :, :3].astype(np.float32)
    blended = np.clip(fg * alpha + bg * (1.0 - alpha), 0, 255).astype(np.uint8)
    out_a = np.clip(
        alpha * 255.0 + (1.0 - alpha) * region[:, :, 3:4].astype(np.float32),
        0,
        255,
    ).astype(np.uint8)
    region[:, :, :3] = blended
    region[:, :, 3:4] = out_a


def _poly_mask(
    w: int,
    h: int,
    pts_svg: tuple[tuple[float, float], ...],
    *,
    origin_x: float,
    origin_y: float,
    scale: float,
) -> np.ndarray:
    pts = np.array(
        [
            (
                int(round((px - origin_x) * scale)),
                int(round((py - origin_y) * scale)),
            )
            for px, py in pts_svg
        ],
        dtype=np.int32,
    )
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    return mask


def render_search_glyph_bgra(
    *,
    size: int = 180,
    color_bgr: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    """Paint the clipped search glyph into a square BGRA patch centered on the ring."""
    size = max(24, int(size))
    ss = 4
    dim = size * ss
    scale = dim / (2.0 * _SEARCH_PATCH_R)
    origin_x = _SEARCH_CX - _SEARCH_PATCH_R
    origin_y = _SEARCH_CY - _SEARCH_PATCH_R
    rings = np.zeros((dim, dim, 4), dtype=np.uint8)
    cx = int(round((_SEARCH_CX - origin_x) * scale))
    cy = int(round((_SEARCH_CY - origin_y) * scale))
    radius = max(1, int(round(_SEARCH_R * scale)))
    stroke = max(ss, int(round(_SEARCH_STROKE * scale)))
    color = (*tuple(int(c) for c in color_bgr), 255)
    cv2.circle(rings, (cx, cy), radius, color, stroke, lineType=cv2.LINE_AA)

    out = np.zeros((dim, dim, 4), dtype=np.uint8)
    for clip_pts in (_SEARCH_CLIP_BOTTOM, _SEARCH_CLIP_TOP):
        mask = _poly_mask(
            dim, dim, clip_pts, origin_x=origin_x, origin_y=origin_y, scale=scale
        )
        layer = rings.copy()
        layer[mask == 0] = 0
        visible = layer[:, :, 3] > out[:, :, 3]
        out[visible] = layer[visible]

    for pts_svg in (_SEARCH_ARROW_DOWN, _SEARCH_ARROW_UP):
        pts = np.array(
            [
                (
                    int(round((px - origin_x) * scale)),
                    int(round((py - origin_y) * scale)),
                )
                for px, py in pts_svg
            ],
            dtype=np.int32,
        )
        cv2.fillPoly(out, [pts], color)

    return cv2.resize(out, (size, size), interpolation=cv2.INTER_AREA)


@lru_cache(maxsize=16)
def _cached_search_frames(
    size: int, color_bgr: tuple[int, int, int]
) -> tuple[np.ndarray, ...]:
    glyph = render_search_glyph_bgra(size=size, color_bgr=color_bgr)
    return precompute_rotated_patches(glyph)


def search_patch_for_angle(
    angle_deg: float,
    *,
    size: int = 180,
    color_bgr: tuple[int, int, int] = (255, 255, 255),
) -> np.ndarray:
    return rotated_patch_for_angle(
        _cached_search_frames(int(size), tuple(int(c) for c in color_bgr)),
        angle_deg,
    )


def build_search_spinner_frames(
    assets_dir: Path | str | None = None,
    *,
    size: int = 180,
    color_bgr: tuple[int, int, int] = (255, 255, 255),
) -> tuple[np.ndarray, ...] | None:
    """Build 36-frame spinner from the settings searching-icon clips."""
    del assets_dir
    try:
        return _cached_search_frames(int(size), tuple(int(c) for c in color_bgr))
    except Exception:
        return None


def advance_angle_deg(angle_deg: float, dt: float, *, dps: float = _SEARCH_ROTATION_DPS) -> float:
    return (float(angle_deg) + float(dps) * max(0.0, float(dt))) % 360.0
