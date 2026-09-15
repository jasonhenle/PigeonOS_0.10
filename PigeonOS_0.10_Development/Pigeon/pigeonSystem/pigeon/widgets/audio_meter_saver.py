"""Diagnostic idle-face prototype: SVG stereo LED meter driven by ALSA capture.

Temporarily substitutes for the clock saver (toggle with [1] while the saver is
up). Does not replace ``clock_saver.py``. No FFT, bands, or beat detection.

Capture runs off the Tk thread (``arecord`` → hw:2,0, 48 kHz S16_LE stereo).
The named SVG shapes are shown/hidden like LED segments; fills stay as authored.
"""

from __future__ import annotations

import atexit
import copy
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from pigeon.design import DESIGN_H, DESIGN_W

# ---------------------------------------------------------------------------
# Tunable level → LED mapping (the one place to retune after real program)
# ---------------------------------------------------------------------------
# Segments 1–10 light when the smoothed channel level reaches each dBFS step.
# This is intentionally *not* a linear 0–1 amplitude map: digital full-scale
# is rarely reached, and equal linear steps look empty on speech/music.
# 1–7 green, 8–9 yellow, 10 red (colors live in the SVG, not here).
METER_SEGMENT_THRESHOLDS_DBFS: tuple[float, ...] = (
    -48.0,  # 1
    -42.0,  # 2
    -36.0,  # 3
    -30.0,  # 4
    -24.0,  # 5
    -18.0,  # 6
    -12.0,  # 7  last green
    -8.0,  # 8  first yellow
    -4.0,  # 9  second yellow
    -1.0,  # 10 red
)

ATTACK_S = 0.018
RELEASE_S = 0.220
SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2  # S16_LE
FRAMES_PER_CHUNK = 1024  # ~21 ms at 48 kHz
FULL_SCALE = 32768.0
DIAG_PERIOD_S = 0.50
DEFAULT_ALSA_DEVICE = "hw:2,0"

_NS_ID_KEYS = ("id", "data-name")

_SVG_TREE_TEMPLATES: dict[tuple[str, int], ET.Element] = {}
_EMPTY_PATCH = np.zeros((1, 1, 4), dtype=np.uint8)

_face_enabled = True  # prototype default: meter replaces idle clock
_stop = threading.Event()
_thread: threading.Thread | None = None
_proc: subprocess.Popen[bytes] | None = None
_start_lock = threading.Lock()
_logged_capture_fail = False
_logged_banner = False


@dataclass(frozen=True)
class MeterLevels:
    rms_l: float
    rms_r: float
    env_l: float
    env_r: float
    dbfs_l: float
    dbfs_r: float
    seg_l: int
    seg_r: int


_SILENCE = MeterLevels(
    rms_l=0.0,
    rms_r=0.0,
    env_l=0.0,
    env_r=0.0,
    dbfs_l=-120.0,
    dbfs_r=-120.0,
    seg_l=0,
    seg_r=0,
)
_latest: MeterLevels = _SILENCE

_art_lock = threading.Lock()
_bg_bgra: np.ndarray | None = None
_led_patches: dict[tuple[str, int], tuple[np.ndarray, int, int]] = {}
_frame_cache_key: tuple[int, int] | None = None
_frame_cache: np.ndarray | None = None


def meter_segments_from_dbfs(dbfs: float) -> int:
    """Map smoothed channel dBFS to discrete LED count 0–10.

    This is the single place to retune after seeing real program material.
    Segment *n* is lit when ``dbfs`` is at least ``METER_SEGMENT_THRESHOLDS_DBFS[n-1]``.
    """
    n = 0
    for thr in METER_SEGMENT_THRESHOLDS_DBFS:
        if float(dbfs) >= float(thr):
            n += 1
        else:
            break
    return max(0, min(10, n))


def dbfs_from_normalized(norm: float) -> float:
    return 20.0 * math.log10(max(float(norm), 1e-12))


def audio_meter_face_enabled() -> bool:
    return bool(_face_enabled)


def set_audio_meter_face_enabled(on: bool) -> None:
    global _face_enabled
    _face_enabled = bool(on)


def toggle_audio_meter_face() -> bool:
    set_audio_meter_face_enabled(not _face_enabled)
    return bool(_face_enabled)


def latest_meter_levels() -> MeterLevels:
    return _latest


def latest_meter_cache_key() -> int:
    sample = _latest
    return 1 + int(sample.seg_l) * 11 + int(sample.seg_r)


def default_audio_meter_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_AUDIO_METER_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "clocksaver_audio_visualization.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "clocksaver_audio_visualization.svg"


def meter_shape_id(side: str, index: int) -> str:
    return f"{side}_meter_{int(index):02d}_shape"


def _normalize_logical(raw_id: str) -> str:
    return str(raw_id or "").strip().lower()


def find_meter_element(root: ET.Element, *logical_ids: str) -> ET.Element | None:
    want = {_normalize_logical(x) for x in logical_ids if x}
    for el in root.iter():
        for key in _NS_ID_KEYS:
            lid = _normalize_logical(el.get(key) or "")
            if lid in want:
                return el
    return None


def svg_tree_from_path(path: Path | None = None) -> ET.Element:
    path = Path(path) if path is not None else default_audio_meter_svg_path()
    key = (str(path.resolve()), path.stat().st_mtime_ns)
    template = _SVG_TREE_TEMPLATES.get(key)
    if template is None:
        tree = ET.parse(path)
        root = tree.getroot()
        _SVG_TREE_TEMPLATES.clear()
        _SVG_TREE_TEMPLATES[key] = root
        template = root
    return copy.deepcopy(template)


def set_meter_shape_lit(el: ET.Element | None, lit: bool) -> None:
    """Show or hide one authored LED. Never rewrite fill / stroke / geometry."""
    if el is None:
        return
    if lit:
        el.attrib.pop("opacity", None)
        el.attrib.pop("display", None)
        el.set("visibility", "visible")
    else:
        el.set("opacity", "0")
        el.set("display", "none")
        el.set("visibility", "hidden")


def apply_meter_leds(root: ET.Element, left_level: int, right_level: int) -> None:
    """LED-bar visibility: level n shows shapes 01..n; 0 hides all ten."""
    left_n = max(0, min(10, int(left_level)))
    right_n = max(0, min(10, int(right_level)))
    for side, level in (("left", left_n), ("right", right_n)):
        for i in range(1, 11):
            el = find_meter_element(root, meter_shape_id(side, i))
            set_meter_shape_lit(el, i <= level)


def _shape_fill(el: ET.Element | None) -> str:
    if el is None:
        return ""
    return str(el.get("fill") or "").strip().lower()


def _remove_from_tree(root: ET.Element, el: ET.Element | None) -> None:
    if el is None:
        return
    for parent in root.iter():
        children = list(parent)
        if el in children:
            parent.remove(el)
            return


def _prune_hidden_meter_shapes(root: ET.Element) -> None:
    """Drop hidden LEDs from a clone so rasterizers that ignore display:none still hide them."""
    hidden: list[tuple[ET.Element, ET.Element]] = []
    for parent in root.iter():
        for child in list(parent):
            if (child.get("opacity") or "") == "0" or (child.get("display") or "") == "none":
                lid = _normalize_logical(child.get("id") or "")
                if "_meter_" in lid and lid.endswith("_shape"):
                    hidden.append((parent, child))
    for parent, child in hidden:
        parent.remove(child)


def _apply_layer_opacity(bgra: np.ndarray, op: float) -> np.ndarray:
    o = max(0.0, min(1.0, float(op)))
    if o >= 0.999:
        return bgra
    out = bgra.astype(np.float32)
    out[:, :, 3] *= o
    return np.clip(out, 0, 255).astype(np.uint8)


def _rasterize(root: ET.Element) -> np.ndarray:
    from pigeon.widgets.settings_svg_text import _pymupdf_rasterize

    return _pymupdf_rasterize(root, width=int(DESIGN_W), height=int(DESIGN_H))


def _blit_opaque(dst: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    ph, pw = patch.shape[:2]
    dh, dw = dst.shape[:2]
    x = int(x)
    y = int(y)
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(dw, x + pw)
    y1 = min(dh, y + ph)
    if x1 <= x0 or y1 <= y0:
        return
    src = patch[y0 - y : y1 - y, x0 - x : x1 - x]
    roi = dst[y0:y1, x0:x1]
    mask = src[:, :, 3] > 8
    roi[mask] = src[mask]


def _crop_ink(frame: np.ndarray) -> tuple[np.ndarray, int, int] | None:
    alpha = frame[:, :, 3]
    ys, xs = np.where(alpha > 8)
    if xs.size == 0 or ys.size == 0:
        return None
    x0 = int(xs.min())
    x1 = int(xs.max()) + 1
    y0 = int(ys.min())
    y1 = int(ys.max()) + 1
    return frame[y0:y1, x0:x1].copy(), x0, y0


def _ensure_art() -> tuple[np.ndarray, dict[tuple[str, int], tuple[np.ndarray, int, int]]]:
    global _bg_bgra
    with _art_lock:
        if _bg_bgra is not None and _led_patches:
            return _bg_bgra, _led_patches
        root = svg_tree_from_path()
        apply_meter_leds(root, 0, 0)
        _prune_hidden_meter_shapes(root)
        bg = _rasterize(root)
        patches: dict[tuple[str, int], tuple[np.ndarray, int, int]] = {}
        for side in ("left", "right"):
            for i in range(1, 11):
                one = svg_tree_from_path()
                apply_meter_leds(one, 0, 0)
                el = find_meter_element(one, meter_shape_id(side, i))
                set_meter_shape_lit(el, True)
                _remove_from_tree(one, find_meter_element(one, "background"))
                _prune_hidden_meter_shapes(one)
                cropped = _crop_ink(_rasterize(one))
                if cropped is not None:
                    patches[(side, i)] = cropped
        _bg_bgra = bg
        _led_patches.clear()
        _led_patches.update(patches)
        return _bg_bgra, _led_patches


def clear_audio_meter_render_caches() -> None:
    global _bg_bgra, _frame_cache, _frame_cache_key
    with _art_lock:
        _bg_bgra = None
        _led_patches.clear()
        _frame_cache = None
        _frame_cache_key = None
    _SVG_TREE_TEMPLATES.clear()


def render_audio_meter_bgra(*, left_level: int, right_level: int) -> np.ndarray:
    left_n = max(0, min(10, int(left_level)))
    right_n = max(0, min(10, int(right_level)))
    global _frame_cache, _frame_cache_key
    key = (left_n, right_n)
    if _frame_cache is not None and _frame_cache_key == key:
        return _frame_cache
    bg, patches = _ensure_art()
    frame = bg.copy()
    for i in range(1, left_n + 1):
        patch = patches.get(("left", i))
        if patch is not None:
            _blit_opaque(frame, patch[0], patch[1], patch[2])
    for i in range(1, right_n + 1):
        patch = patches.get(("right", i))
        if patch is not None:
            _blit_opaque(frame, patch[0], patch[1], patch[2])
    _frame_cache = frame
    _frame_cache_key = key
    return frame


def render_audio_meter_composite_bgra(
    *,
    layer_opacity: float = 1.0,
) -> tuple[
    tuple[np.ndarray, tuple[int, int, int, int]],
    tuple[np.ndarray, tuple[int, int, int, int]],
]:
    sample = latest_meter_levels()
    frame = render_audio_meter_bgra(left_level=sample.seg_l, right_level=sample.seg_r)
    frame = _apply_layer_opacity(frame, layer_opacity)
    full_rect = (0, 0, int(DESIGN_W), int(DESIGN_H))
    return (frame, full_rect), (_EMPTY_PATCH.copy(), (0, 0, 1, 1))


def _smooth(prev: float, target: float, dt: float) -> float:
    tau = ATTACK_S if target > prev else RELEASE_S
    alpha = 1.0 - math.exp(-max(0.0, float(dt)) / max(float(tau), 1e-6))
    return float(prev) + (float(target) - float(prev)) * alpha


def _channel_rms(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    x = pcm.astype(np.float64)
    return float(np.sqrt(np.mean(x * x)) / FULL_SCALE)


def _alsa_device() -> str:
    return os.environ.get("PIGEON_ALSA_CAPTURE_DEVICE", DEFAULT_ALSA_DEVICE).strip() or DEFAULT_ALSA_DEVICE


def _log(msg: str) -> None:
    try:
        sys.stderr.write(msg)
        if not msg.endswith("\n"):
            sys.stderr.write("\n")
        sys.stderr.flush()
    except Exception:
        pass


def _print_banner_once() -> None:
    global _logged_banner
    if _logged_banner:
        return
    _logged_banner = True
    thr = ", ".join(f"{v:g}" for v in METER_SEGMENT_THRESHOLDS_DBFS)
    _log(
        "pigeon: diagnostic stereo meter — ALSA "
        f"{_alsa_device()} 48 kHz S16_LE 2ch; [1] toggles clock saver while idle"
    )
    _log(f"pigeon: meter segment thresholds dBFS (tune in audio_meter_saver.py): {thr}")


def _publish(rms_l: float, rms_r: float, env_l: float, env_r: float) -> MeterLevels:
    global _latest
    sample = MeterLevels(
        rms_l=float(rms_l),
        rms_r=float(rms_r),
        env_l=float(env_l),
        env_r=float(env_r),
        dbfs_l=dbfs_from_normalized(env_l),
        dbfs_r=dbfs_from_normalized(env_r),
        seg_l=meter_segments_from_dbfs(dbfs_from_normalized(env_l)),
        seg_r=meter_segments_from_dbfs(dbfs_from_normalized(env_r)),
    )
    _latest = sample
    return sample


def _capture_loop() -> None:
    global _proc, _logged_capture_fail
    _print_banner_once()
    env_l = 0.0
    env_r = 0.0
    last_diag = 0.0
    nbytes = FRAMES_PER_CHUNK * CHANNELS * SAMPLE_WIDTH
    dt_chunk = float(FRAMES_PER_CHUNK) / float(SAMPLE_RATE)
    arecord = shutil.which("arecord")
    if not arecord:
        if not _logged_capture_fail:
            _logged_capture_fail = True
            _log("pigeon: meter capture: arecord not found (expected on Pi / ALSA)")
        _publish(0.0, 0.0, 0.0, 0.0)
        return
    while not _stop.is_set():
        cmd = [
            arecord,
            "-q",
            "-D",
            _alsa_device(),
            "-f",
            "S16_LE",
            "-c",
            str(CHANNELS),
            "-r",
            str(SAMPLE_RATE),
            "-t",
            "raw",
        ]
        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
        except Exception as exc:
            if not _logged_capture_fail:
                _logged_capture_fail = True
                _log(f"pigeon: meter capture: failed to start arecord: {exc}")
            _stop.wait(2.0)
            continue
        stdout = _proc.stdout
        if stdout is None:
            _kill_proc()
            _stop.wait(2.0)
            continue
        _log(f"pigeon: meter capture started ({_alsa_device()})")
        while not _stop.is_set():
            raw = stdout.read(nbytes)
            if not raw:
                break
            if len(raw) < nbytes:
                continue
            pcm = np.frombuffer(raw, dtype="<i2")
            if pcm.size < CHANNELS:
                continue
            stereo = pcm.reshape(-1, CHANNELS)
            rms_l = _channel_rms(stereo[:, 0])
            rms_r = _channel_rms(stereo[:, 1])
            env_l = _smooth(env_l, rms_l, dt_chunk)
            env_r = _smooth(env_r, rms_r, dt_chunk)
            sample = _publish(rms_l, rms_r, env_l, env_r)
            now = time.monotonic()
            if now - last_diag >= DIAG_PERIOD_S:
                last_diag = now
                _log(
                    "pigeon: meter "
                    f"L rms={sample.rms_l:.5f} env={sample.env_l:.5f} "
                    f"dbfs={sample.dbfs_l:.1f} seg={sample.seg_l} | "
                    f"R rms={sample.rms_r:.5f} env={sample.env_r:.5f} "
                    f"dbfs={sample.dbfs_r:.1f} seg={sample.seg_r}"
                )
        err = b""
        try:
            if _proc.stderr is not None:
                err = _proc.stderr.read() or b""
        except Exception:
            err = b""
        _kill_proc()
        if _stop.is_set():
            break
        if err and not _logged_capture_fail:
            _logged_capture_fail = True
            _log("pigeon: meter capture: " + err.decode("utf-8", "replace").strip())
        _stop.wait(2.0)
    _publish(0.0, 0.0, 0.0, 0.0)


def _kill_proc() -> None:
    global _proc
    proc = _proc
    _proc = None
    if proc is None:
        return
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=0.5)
    except Exception:
        pass


def ensure_audio_meter_capture() -> None:
    global _thread
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(
            target=_capture_loop,
            name="pigeon-audio-meter",
            daemon=True,
        )
        _thread.start()


def stop_audio_meter_capture() -> None:
    global _thread
    with _start_lock:
        thread = _thread
        if thread is None and _proc is None:
            _stop.set()
            return
        _stop.set()
        _kill_proc()
        _thread = None
    if thread is not None and thread.is_alive() and thread is not threading.current_thread():
        thread.join(timeout=0.4)


def sync_audio_meter_capture(wanted: bool) -> None:
    if wanted:
        ensure_audio_meter_capture()
    else:
        stop_audio_meter_capture()


atexit.register(stop_audio_meter_capture)
