"""Reusable searching spinner (same glyph animation as settings WiFi/box scan)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

_SEARCH_SPINNER_STEPS = 36
_SEARCH_ROTATION_DPS = 720.0


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


def build_search_spinner_frames(
    assets_dir: Path | str | None = None,
) -> tuple[np.ndarray, ...] | None:
    """Build 36-frame spinner from the settings WiFi search glyph."""
    try:
        from pigeon.widgets.main_settings import (
            _WIFI_SEARCH_CENTER_SVG,
            _WIFI_SEARCH_GLYPH_BGR,
            _WIFI_SEARCH_PATCH_RADIUS_PX,
            _discover_onboarding_search_arc_specs,
            _discover_onboarding_search_triangle_specs,
            _draw_onboarding_search_arc_overlays_on_patch,
            _draw_triangle_specs_on_patch,
            _mask_wifi_search_glyph_patch,
            _svg_to_px,
            _svg_tree_from_path,
            default_main_settings_svg_path,
        )
    except Exception:
        return None
    path = default_main_settings_svg_path(assets_dir)
    if not path.is_file():
        return None
    try:
        root = _svg_tree_from_path(path)
        arc_specs = _discover_onboarding_search_arc_specs(root)
        triangle_specs = _discover_onboarding_search_triangle_specs(root)
        if not arc_specs and not triangle_specs:
            return None
        cx_px, cy_px = _svg_to_px(_WIFI_SEARCH_CENTER_SVG[0], _WIFI_SEARCH_CENTER_SVG[1])
        r = int(_WIFI_SEARCH_PATCH_RADIUS_PX)
        x0 = max(0, cx_px - r)
        y0 = max(0, cy_px - r)
        patch = np.zeros((2 * r, 2 * r, 4), dtype=np.uint8)
        color = tuple(_WIFI_SEARCH_GLYPH_BGR)
        _draw_onboarding_search_arc_overlays_on_patch(
            patch, arc_specs, origin_x0=x0, origin_y0=y0, color_bgr=color
        )
        _draw_triangle_specs_on_patch(
            patch,
            triangle_specs,
            origin_x0=x0,
            origin_y0=y0,
            color_bgr=color,
        )
        glyph = _mask_wifi_search_glyph_patch(patch)
    except Exception:
        return None
    return precompute_rotated_patches(glyph)


def advance_angle_deg(angle_deg: float, dt: float, *, dps: float = _SEARCH_ROTATION_DPS) -> float:
    return (float(angle_deg) + float(dps) * max(0.0, float(dt))) % 360.0
