"""Zone-6 audio visualizer: idle motion plus frequency reaction.

Renders a mirrored frequency ribbon into a BGRA well. Capture / FFT live in
``audio_meter_saver`` (the diagnostic bars stay RMS + IIR). When the input is
silent or missing, a slow low-amplitude wave still moves so the well never
looks frozen.

The ribbon is painted at a small working size and scaled up so the Pi is not
doing a 793×488 float field every frame.
"""

from __future__ import annotations

import time

import cv2
import numpy as np

SPECTRUM_BINS = 48
_IDLE_MIN = 0.045
_IDLE_SPAN = 0.11
_MAX_HALF_FRAC = 0.44
_GREEN_BGR = np.array([74, 181, 57], dtype=np.float32)  # #39b54a
_PALE_BGR = np.array([236, 246, 232], dtype=np.float32)
_WORK_MAX_W = 360
_WORK_MAX_H = 180
_SMOOTH_KERNEL = np.array([1.0, 4.0, 8.0, 4.0, 1.0], dtype=np.float64)
_SMOOTH_KERNEL /= _SMOOTH_KERNEL.sum()
_SPINE_BGR = np.array([210, 230, 200], dtype=np.uint8)
_ribbon_out: np.ndarray | None = None
_ribbon_yy: np.ndarray | None = None
_ribbon_u: np.ndarray | None = None
_ribbon_color: np.ndarray | None = None
_ribbon_fade: np.ndarray | None = None
_work_bg: np.ndarray | None = None
_work_mixed: np.ndarray | None = None
_work_up: np.ndarray | None = None


def _resample_spectrum(spec: np.ndarray, n: int) -> np.ndarray:
    src = np.asarray(spec, dtype=np.float64).reshape(-1)
    if src.size == 0:
        return np.zeros(int(n), dtype=np.float64)
    if src.size == int(n):
        return src
    x_old = np.linspace(0.0, 1.0, src.size)
    x_new = np.linspace(0.0, 1.0, int(n))
    return np.interp(x_new, x_old, src)


def _idle_envelope(n: int, t: float) -> np.ndarray:
    i = np.arange(int(n), dtype=np.float64)
    phases = i * 0.37
    wave = (
        0.55 * np.sin(t * 0.62 + phases)
        + 0.30 * np.sin(t * 1.13 + phases * 1.7)
        + 0.15 * np.sin(t * 0.21 + i * 0.11)
    )
    return _IDLE_MIN + _IDLE_SPAN * (0.55 + 0.45 * wave)


def _smooth_curve(values: np.ndarray, width: int) -> np.ndarray:
    n = int(values.size)
    xs = np.linspace(0.0, float(max(width - 1, 1)), n)
    curve = np.interp(np.arange(int(width), dtype=np.float64), xs, values)
    padded = np.pad(curve, 2, mode="edge")
    return np.convolve(padded, _SMOOTH_KERNEL, mode="valid")


def _work_wh(width: int, height: int) -> tuple[int, int]:
    w = max(32, int(width))
    h = max(24, int(height))
    if w <= _WORK_MAX_W and h <= _WORK_MAX_H:
        return w, h
    scale = min(float(_WORK_MAX_W) / float(w), float(_WORK_MAX_H) / float(h))
    return max(32, int(round(w * scale))), max(24, int(round(h * scale)))


def _ensure_ribbon_scratch(w: int, h: int) -> np.ndarray:
    global _ribbon_out, _ribbon_yy, _ribbon_u, _ribbon_color, _ribbon_fade
    if (
        _ribbon_out is None
        or _ribbon_out.shape[0] != h
        or _ribbon_out.shape[1] != w
    ):
        _ribbon_out = np.zeros((h, w, 4), dtype=np.uint8)
        _ribbon_yy = np.arange(h, dtype=np.float32)[:, None]
        _ribbon_u = np.linspace(0.0, 1.0, w, dtype=np.float32)
        _ribbon_color = (
            _GREEN_BGR[None, :] * (1.0 - _ribbon_u[:, None])
            + _PALE_BGR[None, :] * _ribbon_u[:, None]
        )
        _ribbon_fade = np.empty((h, w), dtype=np.float32)
    return _ribbon_out


def _render_ribbon(w: int, h: int, now: float, spec: np.ndarray, presence: float) -> np.ndarray:
    out = _ensure_ribbon_scratch(w, h)
    mix = max(0.0, min(1.0, float(presence) * 1.35))
    idle = _idle_envelope(SPECTRUM_BINS, now)
    bars = np.clip(idle * (1.0 - mix) + spec * mix, 0.0, 1.0)
    curve = np.clip(_smooth_curve(bars, w), 0.0, 1.0).astype(np.float32)
    half = np.maximum(1.0, curve * (float(h) * _MAX_HALF_FRAC))
    cy = (h - 1) * 0.5
    fade = _ribbon_fade
    np.clip(1.0 - np.abs(_ribbon_yy - np.float32(cy)) / half[None, :], 0.0, 1.0, out=fade)
    fade **= np.float32(1.35)
    core = fade ** np.float32(2.4)
    rgb = _ribbon_color[None, :, :] * (0.55 + 0.45 * core[:, :, None])
    alpha = np.clip(fade * (36.0 + 210.0 * curve[None, :]), 0.0, 255.0)
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[:, :, 3] = alpha.astype(np.uint8)
    spine = max(1, int(round(h * 0.012)))
    y0 = max(0, int(round(cy)) - spine)
    y1 = min(h, y0 + spine * 2 + 1)
    spine_a = np.clip(70.0 + 140.0 * curve, 0.0, 220.0).astype(np.uint8)
    out[y0:y1, :, :3] = np.maximum(out[y0:y1, :, :3], _SPINE_BGR)
    out[y0:y1, :, 3] = np.maximum(out[y0:y1, :, 3], spine_a[None, :])
    return out


def render_audio_visualizer_bgra(
    width: int,
    height: int,
    *,
    t: float | None = None,
    spectrum: np.ndarray | None = None,
    preview: bool = False,
) -> np.ndarray:
    """Mirrored frequency ribbon sized to ``width``×``height``."""
    w = max(32, int(width))
    h = max(24, int(height))
    if preview:
        now = 0.0 if t is None else float(t)
        spec = _resample_spectrum(
            np.zeros(SPECTRUM_BINS, dtype=np.float64)
            if spectrum is None
            else np.asarray(spectrum, dtype=np.float64),
            SPECTRUM_BINS,
        )
        presence = 0.0
    else:
        now = time.monotonic() if t is None else float(t)
        if spectrum is None:
            try:
                from pigeon.widgets.audio_meter_saver import latest_spectrum

                spectrum = latest_spectrum()
            except Exception:
                spectrum = np.zeros(SPECTRUM_BINS, dtype=np.float64)
        spec = _resample_spectrum(np.asarray(spectrum, dtype=np.float64), SPECTRUM_BINS)
        presence = float(np.max(spec)) if spec.size else 0.0
        try:
            from pigeon.widgets.audio_meter_saver import latest_meter_levels

            lv = latest_meter_levels()
            presence = max(presence, float(lv.fill_l), float(lv.fill_r))
        except Exception:
            pass
    ww, hh = _work_wh(w, h)
    small = _render_ribbon(ww, hh, now, spec, presence)
    if ww == w and hh == h:
        return small.copy()
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def render_audio_visualizer_into_bgr(
    dest_bgr: np.ndarray,
    *,
    t: float | None = None,
    spectrum: np.ndarray | None = None,
) -> None:
    """Composite the ribbon into ``dest_bgr`` at the small work size, then upsample.

    Full-size BGRA lerp of the 793×488 well was ~16 ms on the Pi 5. Blending
    292×180 and scaling the already-composited BGR is cheap enough for 30 fps.
    """
    if dest_bgr is None or dest_bgr.size == 0 or dest_bgr.ndim != 3:
        return
    h, w = int(dest_bgr.shape[0]), int(dest_bgr.shape[1])
    if w < 1 or h < 1:
        return
    now = time.monotonic() if t is None else float(t)
    if spectrum is None:
        try:
            from pigeon.widgets.audio_meter_saver import latest_spectrum

            spectrum = latest_spectrum()
        except Exception:
            spectrum = np.zeros(SPECTRUM_BINS, dtype=np.float64)
    spec = _resample_spectrum(np.asarray(spectrum, dtype=np.float64), SPECTRUM_BINS)
    presence = float(np.max(spec)) if spec.size else 0.0
    try:
        from pigeon.widgets.audio_meter_saver import latest_meter_levels

        lv = latest_meter_levels()
        presence = max(presence, float(lv.fill_l), float(lv.fill_r))
    except Exception:
        pass
    ww, hh = _work_wh(w, h)
    small = _render_ribbon(ww, hh, now, spec, presence)
    from pigeon.compositing import blend_bgra_over_bgr_u8

    global _work_bg, _work_mixed, _work_up
    src = dest_bgr if dest_bgr.flags["C_CONTIGUOUS"] else np.ascontiguousarray(dest_bgr)
    if ww == w and hh == h:
        dest_bgr[:] = blend_bgra_over_bgr_u8(src, small)
        return
    if _work_bg is None or _work_bg.shape[:2] != (hh, ww):
        _work_bg = np.empty((hh, ww, 3), dtype=np.uint8)
        _work_mixed = np.empty((hh, ww, 3), dtype=np.uint8)
    cv2.resize(src, (ww, hh), dst=_work_bg, interpolation=cv2.INTER_AREA)
    blend_bgra_over_bgr_u8(_work_bg, small, out=_work_mixed)
    if dest_bgr.flags["C_CONTIGUOUS"]:
        cv2.resize(_work_mixed, (w, h), dst=dest_bgr, interpolation=cv2.INTER_LINEAR)
        return
    if _work_up is None or _work_up.shape[:2] != (h, w):
        _work_up = np.empty((h, w, 3), dtype=np.uint8)
    cv2.resize(_work_mixed, (w, h), dst=_work_up, interpolation=cv2.INTER_LINEAR)
    dest_bgr[:] = _work_up


__all__ = [
    "SPECTRUM_BINS",
    "render_audio_visualizer_bgra",
    "render_audio_visualizer_into_bgr",
]
