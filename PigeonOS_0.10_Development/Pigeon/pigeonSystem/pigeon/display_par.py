"""Pixel aspect ratio (PAR) detection and square-pixel UI compensation.

Pigeon authors UI assuming square pixels at 800×480. Some panels (notably the
official Raspberry Pi 7″ Touch Display) have wider-than-tall pixels, so circles
and grid cells look horizontally stretched. This module:

1. Detects a likely panel PAR (DRM size mm, known Pi 7″ profile, env override)
2. Optionally persists a user mode: ``auto`` (detect) or ``off`` (force 1.0)
3. Applies a last-step horizontal squeeze (1/PAR) before letterboxing to the
   framebuffer so physical geometry matches the design intent.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import cv2
import numpy as np

from pigeon.compositing import cv_resize_interp, scale_uniform_letterbox

# Official Raspberry Pi Touch Display (7″): active area 154.08 × 85.92 mm @ 800×480.
# PAR = (mm_per_px_x) / (mm_per_px_y) = (W_mm/W_px) / (H_mm/H_px).
_PI_7_ACTIVE_W_MM = 154.08
_PI_7_ACTIVE_H_MM = 85.92
_PI_7_W_PX = 800
_PI_7_H_PX = 480
OFFICIAL_PI_7_PAR = (_PI_7_ACTIVE_W_MM / _PI_7_W_PX) / (_PI_7_ACTIVE_H_MM / _PI_7_H_PX)

_PAR_NEAR_SQUARE = 0.02  # |par-1| below this → treat as square (no compensate)
_MODE_AUTO = "auto"
_MODE_OFF = "off"
_VALID_MODES = (_MODE_AUTO, _MODE_OFF)

# Cached detection (process lifetime); cleared when mode / geometry changes.
_cached_auto_par: float | None = None
_cached_auto_reason: str = ""


def clear_auto_par_cache() -> None:
    """Invalidate cached auto-detect (call when display size or mode changes)."""
    global _cached_auto_par, _cached_auto_reason
    _cached_auto_par = None
    _cached_auto_reason = ""


def _clamp_par(par: float) -> float:
    return float(max(0.5, min(2.0, par)))


def parse_par_value(raw: object) -> float | None:
    """Parse a numeric PAR; ``None`` if missing/invalid."""
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not (0.5 <= v <= 2.0):
        return None
    return float(v)


def read_par_mode() -> str:
    """Persisted override mode: ``auto`` (default) or ``off``."""
    env = str(os.environ.get("PIGEON_PAR_MODE", "") or "").strip().lower()
    if env in _VALID_MODES:
        return env
    try:
        from pigeon.app_state import read_app_state

        m = str(read_app_state().get("display_par_mode") or "").strip().lower()
        if m in _VALID_MODES:
            return m
    except Exception:
        pass
    return _MODE_AUTO


def write_par_mode(mode: str) -> str:
    """Persist mode and invalidate auto cache. Returns the stored mode."""
    m = str(mode or "").strip().lower()
    if m not in _VALID_MODES:
        m = _MODE_AUTO
    clear_auto_par_cache()
    try:
        from pigeon.app_state import write_app_state

        write_app_state(display_par_mode=m)
    except Exception:
        pass
    return m


def cycle_par_mode() -> tuple[str, float, str]:
    """
    Toggle ``auto`` ↔ ``off`` (P+A+R shortcut).

    Returns ``(mode, effective_par, reason)``.
    """
    cur = read_par_mode()
    nxt = _MODE_OFF if cur == _MODE_AUTO else _MODE_AUTO
    write_par_mode(nxt)
    par, reason = resolve_display_par()
    return nxt, par, reason


def _raspberry_pi_model() -> str:
    for path in (
        Path("/proc/device-tree/model"),
        Path("/sys/firmware/devicetree/base/model"),
    ):
        try:
            raw = path.read_bytes()
            text = raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace").strip()
            if text:
                return text
        except OSError:
            continue
    return ""


def _looks_like_raspberry_pi() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    model = _raspberry_pi_model().lower()
    if "raspberry pi" in model:
        return True
    return Path("/boot/firmware/config.txt").is_file() or Path("/boot/config.txt").is_file()


def _drm_size_mm_and_mode() -> tuple[float, float, int, int] | None:
    """
    Best-effort: first connected DRM connector with width_mm/height_mm + a mode.

    Returns ``(width_mm, height_mm, mode_w, mode_h)`` or ``None``.
    """
    drm_root = Path("/sys/class/drm")
    if not drm_root.is_dir():
        return None
    try:
        entries = sorted(drm_root.iterdir())
    except OSError:
        return None
    for entry in entries:
        name = entry.name
        if name == "version" or "-" not in name:
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if status != "connected":
            continue
        try:
            w_mm = float((entry / "width_mm").read_text(encoding="utf-8").strip())
            h_mm = float((entry / "height_mm").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if w_mm < 10.0 or h_mm < 10.0:
            continue
        mode_w = mode_h = 0
        modes_path = entry / "modes"
        try:
            first = modes_path.read_text(encoding="utf-8").splitlines()[0].strip()
            m = re.match(r"^(\d+)x(\d+)", first)
            if m:
                mode_w, mode_h = int(m.group(1)), int(m.group(2))
        except (OSError, IndexError, ValueError):
            pass
        return w_mm, h_mm, mode_w, mode_h
    return None


def _par_from_physical_mm(
    width_mm: float, height_mm: float, px_w: int, px_h: int
) -> float | None:
    if width_mm <= 0 or height_mm <= 0 or px_w < 1 or px_h < 1:
        return None
    return _clamp_par((width_mm / float(px_w)) / (height_mm / float(px_h)))


def detect_auto_par(
    *, display_w: int = 800, display_h: int = 480
) -> tuple[float, str]:
    """
    Detect panel PAR for square-pixel compensation.

    Prefer DRM physical size; else known official Pi 7″ when on a Pi at 800×480.
    """
    global _cached_auto_par, _cached_auto_reason
    if _cached_auto_par is not None:
        return _cached_auto_par, _cached_auto_reason

    dw = max(1, int(display_w))
    dh = max(1, int(display_h))

    drm = _drm_size_mm_and_mode()
    if drm is not None:
        w_mm, h_mm, mode_w, mode_h = drm
        px_w = mode_w if mode_w >= 1 else dw
        px_h = mode_h if mode_h >= 1 else dh
        par = _par_from_physical_mm(w_mm, h_mm, px_w, px_h)
        if par is not None:
            _cached_auto_par = par
            _cached_auto_reason = (
                f"drm {w_mm:.1f}×{h_mm:.1f}mm @ {px_w}×{px_h} → PAR {par:.4f}"
            )
            return _cached_auto_par, _cached_auto_reason

    if (
        _looks_like_raspberry_pi()
        and dw == _PI_7_W_PX
        and dh == _PI_7_H_PX
    ):
        _cached_auto_par = float(OFFICIAL_PI_7_PAR)
        _cached_auto_reason = (
            f"official Pi 7″ profile @ {dw}×{dh} → PAR {_cached_auto_par:.4f}"
        )
        return _cached_auto_par, _cached_auto_reason

    _cached_auto_par = 1.0
    _cached_auto_reason = f"assume square pixels @ {dw}×{dh}"
    return _cached_auto_par, _cached_auto_reason


def resolve_display_par(
    *, display_w: int = 800, display_h: int = 480
) -> tuple[float, str]:
    """
    Effective PAR for presenting frames.

    Priority: ``PIGEON_PAR`` env → mode ``off`` → auto-detect.
    """
    env_par = parse_par_value(os.environ.get("PIGEON_PAR"))
    if env_par is not None:
        return env_par, f"env PIGEON_PAR={env_par:.4f}"

    mode = read_par_mode()
    if mode == _MODE_OFF:
        return 1.0, "mode=off (square pixels)"

    return detect_auto_par(display_w=display_w, display_h=display_h)


def apply_par_compensation(
    image: np.ndarray,
    *,
    display_w: int,
    display_h: int,
    par: float | None = None,
) -> np.ndarray:
    """
    Fit ``image`` into ``display_w``×``display_h`` with PAR correction.

    When PAR &gt; 1 (wider pixels), content is squeezed horizontally by ``1/par``
    so designed circles stay round on glass, then letterboxed into the framebuffer.
    """
    dw = max(1, int(display_w))
    dh = max(1, int(display_h))
    if image is None or image.size == 0:
        ch = 3
        return np.zeros((dh, dw, ch), dtype=np.uint8)

    if par is None:
        par, _reason = resolve_display_par(display_w=dw, display_h=dh)
    par_f = _clamp_par(float(par))

    src = image
    if abs(par_f - 1.0) >= _PAR_NEAR_SQUARE:
        src_h, src_w = int(src.shape[0]), int(src.shape[1])
        # Wider pixels → shrink width in pixel space so physical X matches design.
        corrected_w = max(1, int(round(src_w / par_f)))
        corrected_h = src_h
        if corrected_w != src_w:
            src = cv2.resize(
                src,
                (corrected_w, corrected_h),
                interpolation=cv_resize_interp(src_w, src_h, corrected_w, corrected_h),
            )

    if int(src.shape[1]) == dw and int(src.shape[0]) == dh:
        return src
    return scale_uniform_letterbox(src, dw, dh)
