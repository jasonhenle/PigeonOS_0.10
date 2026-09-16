"""Diagnostic idle-face: SVG stereo meter driven by ALSA capture.

Toggle with [1] while the idle clock saver is up. Does not replace
``clock_saver.py``. Diagnostic bars stay RMS + IIR LFE (no FFT). The same
capture thread also publishes a log-spaced FFT spectrum for the zone-6
visualizer.

Capture runs off the Tk thread (``arecord`` → hw:2,0, 48 kHz S16_LE stereo).
Green bars scale vertically from the authored bottom. Brown ``LFE_group``
slabs sit at a fixed height equal to the meter bar *width* and grow *outward*
from the meters to the screen edges with mono bass energy (4-pole IIR lowpass,
not a spectrum). Dialogue-only program keeps LFE width at 0.
"""

from __future__ import annotations

import atexit
import copy
import json
import math
import os
import select
import shutil
import signal
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
# Tunable level → bar height (the one place to retune after real program)
# ---------------------------------------------------------------------------
# Continuous bars are one piece; these 10 dB marks are equal *conceptual*
# sections of height (1 = quiet, 10 = loud). Fill interpolates between them.
# Idle hiss ≈ −75…−84 dBFS stays gated off.
#
# 2026-09-15 −3 dB YouTube sines → Denon Zone 2 −10 → Donner (gain 0):
#   1 kHz captured ≈ −24 dBFS. Typical *program* on the same chain is louder
#   (−21…−12 dBFS RMS, peaks ≈ −9). Putting the test tone at mark 10 pinned
#   every real track at full scale. Marks now leave headroom so that program
#   sits mid-to-high and only the loudest peaks reach 10.
METER_LED_COUNT = 10
METER_SEGMENT_THRESHOLDS_DBFS: tuple[float, ...] = (
    -36.0,  # 1  quiet
    -24.0,  # 2
    -15.0,  # 3
    -8.0,  # 4
    -3.0,  # 5  −3 dB 1 kHz test tone (cap ≈ −24)
    1.0,  # 6
    4.0,  # 7  typical program (cap ≈ −17)
    7.0,  # 8
    9.5,  # 9  loud (cap ≈ −12)
    12.0,  # 10 peaks (cap ≈ −9)
)
CALIBRATION_GAIN_DB = 21.0
NOISE_GATE_CAL_DBFS = -54.0
METER_MAX_DBFS = 12.0  # 1.0 fill (conceptual section 10)
FILL_CACHE_STEPS = 300  # skip-cache / frame-cache quantization

ATTACK_S = 0.0  # unused; RMS envelope snaps on immediately
RELEASE_S = 0.018  # kept as a documented time-constant; LED fall uses dB/s
RELEASE_DB_PER_S = 240.0  # ~8 dB in 33 ms — visible fall between hits
SAMPLE_RATE = 48_000
CHANNELS = 2
SAMPLE_WIDTH = 2  # S16_LE
FRAMES_PER_CHUNK = 384  # 8 ms at 48 kHz; matched to arecord -F 8000
RMS_WINDOW_FRAMES = 384  # unused for display; chunk length only
FULL_SCALE = 32768.0
DIAG_PERIOD_S = 8.0
DEFAULT_ALSA_DEVICE = "hw:2,0"
ALSA_PERIOD_US = 8_000  # keep USB capture from arriving in 100 ms+ lumps
ALSA_BUFFER_US = 32_000
# LFE width: 4 cascaded one-poles ~60 Hz on a mono (L+R)/2 mix, run at
# 6 kHz (8× decimate) so the IIR stays off the Tk thread's GIL. Height
# matches the authored meter bar width; max width reaches the screen edges.
LFE_CUTOFF_HZ = 60.0
LFE_POLES = 4
LFE_DECIM = 8
LFE_PROCESS_HZ = float(SAMPLE_RATE) / float(LFE_DECIM)
LFE_LP_ALPHA = 1.0 - math.exp(-2.0 * math.pi * LFE_CUTOFF_HZ / LFE_PROCESS_HZ)

_NS_ID_KEYS = ("id", "data-name")

_SVG_TREE_TEMPLATES: dict[tuple[str, int], ET.Element] = {}
_EMPTY_PATCH = np.zeros((1, 1, 4), dtype=np.uint8)

_face_enabled = False  # idle clock is the normal face; [1] still toggles the meter
_stop = threading.Event()
_thread: threading.Thread | None = None
_proc: subprocess.Popen[bytes] | None = None
_start_lock = threading.Lock()
_capture_gen = 0
_keep_capture_until = 0.0
_logged_capture_fail = False
_logged_banner = False
_last_capture_busy_log = 0.0
CAPTURE_HOLD_AFTER_UNWANTED_S = 2.5


@dataclass(frozen=True)
class MeterLevels:
    rms_l: float
    rms_r: float
    env_l: float
    env_r: float
    dbfs_l: float
    dbfs_r: float
    cal_dbfs_l: float
    cal_dbfs_r: float
    fill_l: float
    fill_r: float
    seg_l: int
    seg_r: int
    rms_lfe: float
    env_lfe: float
    lfe_fill: float


_SILENCE = MeterLevels(
    rms_l=0.0,
    rms_r=0.0,
    env_l=0.0,
    env_r=0.0,
    dbfs_l=-120.0,
    dbfs_r=-120.0,
    cal_dbfs_l=-120.0,
    cal_dbfs_r=-120.0,
    fill_l=0.0,
    fill_r=0.0,
    seg_l=0,
    seg_r=0,
    rms_lfe=0.0,
    env_lfe=0.0,
    lfe_fill=0.0,
)
_latest: MeterLevels = _SILENCE

SPECTRUM_BINS = 48
FFT_SIZE = 2048
_FFT_EVERY_CHUNKS = 2
_SPECTRUM_FMIN = 40.0
_SPECTRUM_FMAX = 16_000.0
_spectrum_lock = threading.Lock()
_latest_spectrum = np.zeros(SPECTRUM_BINS, dtype=np.float64)
_spectrum_smooth = np.zeros(SPECTRUM_BINS, dtype=np.float64)
_pcm_ring = np.zeros(FFT_SIZE, dtype=np.float64)
_pcm_ring_pos = 0
_fft_chunk_i = 0
_fft_window = np.empty(FFT_SIZE, dtype=np.float64)
_hanning = np.hanning(FFT_SIZE)
SCOPE_SAMPLES = 512
_scope_lock = threading.Lock()
_scope_ring = np.zeros(SCOPE_SAMPLES, dtype=np.float64)
_scope_pos = 0
_latest_scope = np.zeros(SCOPE_SAMPLES, dtype=np.float64)
_last_pcm_mono = 0.0
_pcm_ever = False
_capture_dead = False
PROGRAM_AUDIO_ON_FILL = 0.05
PROGRAM_AUDIO_HOLD_S = 2.5
PROGRAM_AUDIO_SESSION_HOLD_S = 120.0
_program_audio_until = 0.0
_program_audio_session_until = 0.0
_band_bins: np.ndarray | None = None

_art_lock = threading.Lock()
_bg_bgra: np.ndarray | None = None
_bar_patches: dict[str, tuple[np.ndarray, int, int]] = {}
_lfe_patches: dict[str, tuple[np.ndarray, int, int]] = {}
_frame_cache_key: tuple[int, int, int] | None = None
_frame_cache: np.ndarray | None = None
_live_bgra: np.ndarray | None = None
_bar_union_rect: tuple[int, int, int, int] | None = None
_prev_blit_rects: list[tuple[int, int, int, int]] = []

# Per-title automatic meter range. Peak calibrated dB is learned while the
# title plays and persisted so a replay starts already scaled.
TITLE_CAL_TARGET_DB = 9.5  # mark 9 — leaves the top section as headroom
TITLE_CAL_MIN_PEAK_DB = -24.0
TITLE_CAL_BOOST_AFTER_S = 12.0
TITLE_CAL_MAX_BOOST_DB = 12.0
TITLE_CAL_MAX_CUT_DB = 18.0
TITLE_CAL_PEAK_MAX = 250
_title_lock = threading.Lock()
_title_key = ""
_title_peak_db = -120.0
_title_peak_from_disk = False
_title_since_mono = 0.0
_title_peaks: dict[str, dict[str, float]] = {}
_title_dirty = False
_title_peaks_loaded = False
_title_persist_enabled = True
_title_persist_mono = 0.0
_title_last_persisted_peak = -120.0


def meter_segments_from_dbfs(dbfs: float) -> int:
    """Map a dB value to discrete LED count 0–``METER_LED_COUNT``.

    Segment *n* is lit when ``dbfs`` is at least ``METER_SEGMENT_THRESHOLDS_DBFS[n-1]``.
    Pass *calibrated* dB (capture dBFS + ``CALIBRATION_GAIN_DB``), not raw ADC level.
    """
    n = 0
    for thr in METER_SEGMENT_THRESHOLDS_DBFS:
        if float(dbfs) >= float(thr):
            n += 1
        else:
            break
    return max(0, min(int(METER_LED_COUNT), n))


def meter_segments_from_calibrated_dbfs(cal_dbfs: float) -> int:
    """Apply the idle noise gate, then the LED thresholds."""
    if float(cal_dbfs) < float(NOISE_GATE_CAL_DBFS):
        return 0
    return meter_segments_from_dbfs(cal_dbfs)


def calibrated_dbfs(capture_dbfs: float) -> float:
    return float(capture_dbfs) + float(CALIBRATION_GAIN_DB)


def meter_segments_from_capture_dbfs(capture_dbfs: float) -> int:
    """Map captured ADC dBFS through gain + noise gate onto 0–10 (logs only)."""
    return meter_segments_from_calibrated_dbfs(calibrated_dbfs(capture_dbfs))


def meter_fill_from_dbfs(dbfs: float) -> float:
    """Map a calibrated dB value to 0–1 bar height.

    The ten marks in ``METER_SEGMENT_THRESHOLDS_DBFS`` are equal conceptual
    sections of the bar (not drawn). Mark *n* is height ``n / 10``. Between
    marks, height interpolates in dB. Below mark 1, height ramps from the
    noise gate; above mark 10 it stays at full.
    """
    level = float(dbfs)
    if level < float(NOISE_GATE_CAL_DBFS):
        return 0.0
    marks = METER_SEGMENT_THRESHOLDS_DBFS
    n_marks = len(marks)
    first = float(marks[0])
    if level < first:
        span = first - float(NOISE_GATE_CAL_DBFS)
        if span <= 1e-6:
            return 0.0
        return max(0.0, min(1.0 / n_marks, (level - float(NOISE_GATE_CAL_DBFS)) / span / n_marks))
    for i in range(n_marks - 1):
        t1 = float(marks[i + 1])
        if level < t1:
            t0 = float(marks[i])
            f0 = (i + 1) / n_marks
            f1 = (i + 2) / n_marks
            frac = 0.0 if t1 <= t0 else (level - t0) / (t1 - t0)
            return max(0.0, min(1.0, f0 + frac * (f1 - f0)))
    return 1.0


def meter_fill_from_calibrated_dbfs(cal_dbfs: float) -> float:
    return meter_fill_from_dbfs(cal_dbfs)


def meter_fill_from_capture_dbfs(capture_dbfs: float) -> float:
    return meter_fill_from_calibrated_dbfs(calibrated_dbfs(capture_dbfs))


def lfe_fill_from_calibrated_dbfs(cal_dbfs: float) -> float:
    """Map calibrated lowpassed dB onto 0–1 LFE *width* (same marks as the bars)."""
    return meter_fill_from_dbfs(cal_dbfs)


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


def meter_content_key(*, title: str, artist: str = "", mode: str = "") -> str:
    t = " ".join(str(title or "").split()).casefold()
    if not t:
        return ""
    a = " ".join(str(artist or "").split()).casefold()
    m = str(mode or "").strip().casefold()
    return f"{m}|{t}|{a}"


def reset_meter_title_calibration(*, persist: bool = False) -> None:
    """Clear in-memory title peaks. Tests pass ``persist=False`` to skip disk."""
    global _title_key, _title_peak_db, _title_peak_from_disk, _title_since_mono
    global _title_dirty, _title_peaks_loaded, _title_persist_enabled
    global _title_persist_mono, _title_last_persisted_peak
    with _title_lock:
        _title_key = ""
        _title_peak_db = -120.0
        _title_peak_from_disk = False
        _title_since_mono = 0.0
        _title_peaks.clear()
        _title_dirty = False
        _title_peaks_loaded = True
        _title_persist_enabled = bool(persist)
        _title_persist_mono = 0.0
        _title_last_persisted_peak = -120.0


def _title_peaks_path() -> Path:
    from pigeon.runtime_paths import pigeon_state_dir

    return pigeon_state_dir() / "meter_title_peaks.json"


def _ensure_title_peaks_loaded() -> None:
    global _title_peaks_loaded
    if _title_peaks_loaded:
        return
    _title_peaks_loaded = True
    if not _title_persist_enabled:
        return
    try:
        path = _title_peaks_path()
        if not path.is_file():
            return
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return
    if not isinstance(raw, dict):
        return
    with _title_lock:
        for key, row in raw.items():
            k = str(key or "").strip()
            if not k:
                continue
            if isinstance(row, dict):
                try:
                    peak = float(row.get("peak_db"))
                    ts = float(row.get("ts") or 0.0)
                except (TypeError, ValueError):
                    continue
            else:
                try:
                    peak = float(row)
                    ts = 0.0
                except (TypeError, ValueError):
                    continue
            if peak < TITLE_CAL_MIN_PEAK_DB:
                continue
            _title_peaks[k] = {"peak_db": peak, "ts": ts}


def _persist_title_peaks() -> None:
    global _title_dirty, _title_persist_mono, _title_last_persisted_peak
    if not _title_persist_enabled:
        return
    with _title_lock:
        rows = dict(_title_peaks)
        peak = float(_title_peak_db)
    if len(rows) > TITLE_CAL_PEAK_MAX:
        ranked = sorted(
            rows.items(),
            key=lambda item: float(item[1].get("ts") or 0.0),
        )
        rows = dict(ranked[-TITLE_CAL_PEAK_MAX:])
    try:
        path = _title_peaks_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return
    _title_dirty = False
    _title_persist_mono = time.monotonic()
    _title_last_persisted_peak = peak


def _maybe_persist_title_peaks() -> None:
    if not _title_dirty or not _title_persist_enabled:
        return
    now = time.monotonic()
    with _title_lock:
        peak = float(_title_peak_db)
        key = _title_key
    if not key:
        return
    if (
        now - _title_persist_mono < 20.0
        and peak - _title_last_persisted_peak < 1.5
    ):
        return
    _persist_title_peaks()


def set_meter_content_key(key: str) -> None:
    """Bind meter auto-range to the now-playing title. Empty key disables it."""
    global _title_key, _title_peak_db, _title_peak_from_disk, _title_since_mono
    global _title_dirty
    _ensure_title_peaks_loaded()
    k = str(key or "").strip()
    persist_old = False
    with _title_lock:
        if k == _title_key:
            return
        if _title_key and _title_peak_db >= TITLE_CAL_MIN_PEAK_DB:
            _title_peaks[_title_key] = {
                "peak_db": float(_title_peak_db),
                "ts": time.time(),
            }
            _title_dirty = True
            persist_old = True
        _title_key = k
        _title_since_mono = time.monotonic()
        stored = _title_peaks.get(k) if k else None
        if stored is not None:
            _title_peak_db = float(stored.get("peak_db", -120.0))
            _title_peak_from_disk = True
        else:
            _title_peak_db = -120.0
            _title_peak_from_disk = False
    if persist_old:
        _persist_title_peaks()


def note_title_peak_db(cal_l: float, cal_r: float, cal_lfe: float) -> None:
    """Raise the current title's observed peak. Capture thread safe."""
    global _title_peak_db, _title_dirty
    peak = max(float(cal_l), float(cal_r), float(cal_lfe))
    if peak < TITLE_CAL_MIN_PEAK_DB:
        return
    with _title_lock:
        if not _title_key:
            return
        if peak > _title_peak_db + 0.05:
            _title_peak_db = peak
            _title_peaks[_title_key] = {
                "peak_db": peak,
                "ts": time.time(),
            }
            _title_dirty = True


def title_meter_gain_db() -> float:
    """dB added to calibrated levels before they become bar height."""
    with _title_lock:
        peak = float(_title_peak_db)
        age = time.monotonic() - float(_title_since_mono or 0.0)
        key = _title_key
        from_disk = bool(_title_peak_from_disk)
    if not key or peak < TITLE_CAL_MIN_PEAK_DB:
        return 0.0
    raw = float(TITLE_CAL_TARGET_DB) - peak
    if raw > 0.0 and (not from_disk) and age < float(TITLE_CAL_BOOST_AFTER_S):
        return 0.0
    return max(-float(TITLE_CAL_MAX_CUT_DB), min(float(TITLE_CAL_MAX_BOOST_DB), raw))


def title_calibrated_fill(cal_db: float) -> float:
    return meter_fill_from_calibrated_dbfs(float(cal_db) + title_meter_gain_db())


def latest_meter_cache_key() -> int:
    sample = _latest
    steps = int(FILL_CACHE_STEPS)
    span = steps + 1

    def _q(fill: float) -> int:
        return int(round(max(0.0, min(1.0, float(fill))) * steps))

    return 1 + _q(sample.fill_l) + span * (_q(sample.fill_r) + span * _q(sample.lfe_fill))


def latest_spectrum() -> np.ndarray:
    with _spectrum_lock:
        return _latest_spectrum.copy()


def latest_scope() -> np.ndarray:
    """Chronological bass-filtered waveform for the zone-6 oscilloscope."""
    with _scope_lock:
        return _latest_scope.copy()


def latest_visualizer_cache_key() -> int:
    """Skip-cache token: idle phase plus a coarse spectrum hash."""
    t = int(time.monotonic() * 30.0)
    spec = latest_spectrum()
    q = 0
    if spec.size:
        step = max(1, spec.size // 12)
        for v in spec[::step]:
            q = (q * 21 + int(max(0.0, min(1.0, float(v))) * 40.0)) & 0xFFFFF
    sample = _latest
    q = (q + int(sample.fill_l * 40) + int(sample.fill_r * 40)) & 0xFFFFF
    return t * 1_000_000 + q


def audio_connection_ok() -> bool:
    """True when ALSA capture is alive, starting, or has not yet failed.

    Missing ``arecord`` / a dead USB device sets ``_capture_dead``. Until that
    happens we stay optimistic so NP fallbacks do not flash volume/clock on
    the first frames before PCM arrives.
    """
    if _capture_dead:
        return False
    if _pcm_ever and (time.monotonic() - _last_pcm_mono) >= 2.5:
        return False
    return True


def program_audio_present() -> bool:
    """True while captured program is above the noise gate, with a short hold.

    Used to wake now-playing from the clock saver. USB being plugged in is not
    enough — ``fill`` already sits at 0 for idle hiss.
    """
    global _program_audio_until, _program_audio_session_until
    now = time.monotonic()
    if _capture_dead:
        return now < _program_audio_until
    sample = _latest
    level = max(float(sample.fill_l), float(sample.fill_r), float(sample.lfe_fill))
    fresh = _last_pcm_mono > 0.0 and (now - _last_pcm_mono) < 0.75
    if fresh and level >= float(PROGRAM_AUDIO_ON_FILL):
        _program_audio_until = now + float(PROGRAM_AUDIO_HOLD_S)
        _program_audio_session_until = now + float(PROGRAM_AUDIO_SESSION_HOLD_S)
        return True
    return now < _program_audio_until


def program_audio_session_present() -> bool:
    """True while recent program audio is still holding now-playing up.

    Movie quiet scenes drop below the gate for much longer than
    ``PROGRAM_AUDIO_HOLD_S``. The session hold keeps now-playing (and its
    artwork caches) up through those gaps; the clock saver only returns after
    a full quiet stretch.
    """
    now = time.monotonic()
    if program_audio_present():
        return True
    return now < _program_audio_session_until


def _spectrum_bin_slices() -> np.ndarray:
    global _band_bins
    if _band_bins is not None:
        return _band_bins
    freqs = np.fft.rfftfreq(int(FFT_SIZE), 1.0 / float(SAMPLE_RATE))
    edges = np.geomspace(_SPECTRUM_FMIN, _SPECTRUM_FMAX, int(SPECTRUM_BINS) + 1)
    bins = np.zeros((int(SPECTRUM_BINS), 2), dtype=np.int32)
    for i in range(int(SPECTRUM_BINS)):
        lo = int(np.searchsorted(freqs, edges[i], side="left"))
        hi = int(np.searchsorted(freqs, edges[i + 1], side="left"))
        if hi <= lo:
            hi = min(lo + 1, freqs.size)
        bins[i, 0] = lo
        bins[i, 1] = hi
    _band_bins = bins
    return bins


def _push_spectrum_mono(mono: np.ndarray) -> None:
    global _pcm_ring_pos, _fft_chunk_i, _latest_spectrum, _spectrum_smooth, _fft_window
    if mono.size < 1:
        return
    n = int(mono.size)
    pos = int(_pcm_ring_pos)
    ring = _pcm_ring
    size = int(ring.size)
    if n >= size:
        ring[:] = mono[-size:]
        pos = 0
    else:
        end = pos + n
        if end <= size:
            ring[pos:end] = mono
        else:
            first = size - pos
            ring[pos:] = mono[:first]
            ring[: n - first] = mono[first:]
        pos = (pos + n) % size
    _pcm_ring_pos = pos
    _fft_chunk_i += 1
    if _fft_chunk_i < int(_FFT_EVERY_CHUNKS):
        return
    _fft_chunk_i = 0
    if pos == 0:
        np.multiply(ring, _hanning, out=_fft_window)
    else:
        n_tail = size - pos
        _fft_window[:n_tail] = ring[pos:]
        _fft_window[n_tail:] = ring[:pos]
        np.multiply(_fft_window, _hanning, out=_fft_window)
    mag = np.abs(np.fft.rfft(_fft_window))
    bins = _spectrum_bin_slices()
    raw = np.zeros(int(SPECTRUM_BINS), dtype=np.float64)
    for i, (lo, hi) in enumerate(bins):
        sl = mag[int(lo) : int(hi)]
        if sl.size:
            raw[i] = float(np.sqrt(np.mean(sl * sl)))
    peak = float(np.max(raw)) if raw.size else 0.0
    if peak > 1e-9:
        raw = raw / peak
    raw = np.clip(raw, 0.0, 1.0)
    sample = _latest
    presence = max(float(sample.fill_l), float(sample.fill_r))
    if presence <= 0.02:
        raw *= 0.0
    prev = _spectrum_smooth
    attack = 0.45
    release = 0.18
    alpha = np.where(raw >= prev, attack, release)
    smooth = prev + (raw - prev) * alpha
    _spectrum_smooth = smooth
    with _spectrum_lock:
        _latest_spectrum = smooth.copy()


def _push_scope_bass(y_lfe: np.ndarray) -> None:
    global _scope_pos, _latest_scope
    src = np.asarray(y_lfe, dtype=np.float64).reshape(-1)
    if src.size < 1:
        return
    n = int(src.size)
    pos = int(_scope_pos)
    ring = _scope_ring
    size = int(ring.size)
    if n >= size:
        ring[:] = src[-size:]
        pos = 0
    else:
        end = pos + n
        if end <= size:
            ring[pos:end] = src
        else:
            first = size - pos
            ring[pos:] = src[:first]
            ring[: n - first] = src[first:]
        pos = (pos + n) % size
    _scope_pos = pos
    if pos == 0:
        ordered = ring.copy()
    else:
        ordered = np.concatenate((ring[pos:], ring[:pos]))
    with _scope_lock:
        _latest_scope = ordered


def _reset_spectrum() -> None:
    global _latest_spectrum, _spectrum_smooth, _pcm_ring_pos, _fft_chunk_i
    global _scope_pos, _latest_scope, _program_audio_until, _program_audio_session_until
    _spectrum_smooth[:] = 0.0
    _pcm_ring[:] = 0.0
    _pcm_ring_pos = 0
    _fft_chunk_i = 0
    _scope_ring[:] = 0.0
    _scope_pos = 0
    _program_audio_until = 0.0
    _program_audio_session_until = 0.0
    _spectrum_smooth[:] = 0.0
    _pcm_ring[:] = 0.0
    _pcm_ring_pos = 0
    _fft_chunk_i = 0
    _scope_ring[:] = 0.0
    _scope_pos = 0
    with _spectrum_lock:
        _latest_spectrum = np.zeros(SPECTRUM_BINS, dtype=np.float64)
    with _scope_lock:
        _latest_scope = np.zeros(SCOPE_SAMPLES, dtype=np.float64)


def default_audio_meter_svg_path(assets_dir: Path | str | None = None) -> Path:
    env = os.environ.get("PIGEON_AUDIO_METER_SVG", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    if assets_dir is not None:
        return Path(assets_dir) / "clocksaver_audio_visualization_withLFE.svg"
    pigeon_root = Path(__file__).resolve().parents[3]
    return pigeon_root / "pigeonAssets" / "clocksaver_audio_visualization_withLFE.svg"


def meter_bar_id(side: str) -> str:
    return f"{side}_meter_shape"


def lfe_shape_id(side: str) -> str:
    return f"{side}_LFE"


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


def apply_meter_bars(root: ET.Element, *, left_on: bool, right_on: bool) -> None:
    """Show or hide the authored bars. Fills / geometry stay as authored."""
    set_meter_shape_lit(find_meter_element(root, meter_bar_id("left")), left_on)
    set_meter_shape_lit(find_meter_element(root, meter_bar_id("right")), right_on)


def apply_lfe_shapes(root: ET.Element, *, left_on: bool, right_on: bool) -> None:
    """Show or hide the authored LFE slabs. Fills / geometry stay as authored."""
    set_meter_shape_lit(find_meter_element(root, lfe_shape_id("left")), left_on)
    set_meter_shape_lit(find_meter_element(root, lfe_shape_id("right")), right_on)


def apply_meter_leds(root: ET.Element, left_level: int, right_level: int) -> None:
    """LED-bar visibility (legacy 20-segment SVG). Unused by the continuous face."""
    n_leds = int(METER_LED_COUNT)
    left_n = max(0, min(n_leds, int(left_level)))
    right_n = max(0, min(n_leds, int(right_level)))
    for side, level in (("left", left_n), ("right", right_n)):
        for i in range(1, n_leds + 1):
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
    """Drop hidden continuous bars and LFE slabs from a clone."""
    drop_ids = {
        meter_bar_id("left"),
        meter_bar_id("right"),
        lfe_shape_id("left"),
        lfe_shape_id("right"),
    }
    hidden: list[tuple[ET.Element, ET.Element]] = []
    for parent in root.iter():
        for child in list(parent):
            if (child.get("opacity") or "") == "0" or (child.get("display") or "") == "none":
                lid = _normalize_logical(child.get("id") or "")
                if lid in {_normalize_logical(x) for x in drop_ids}:
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
    from pigeon.widgets.settings_svg_text import rasterize_settings_svg_bgra

    # Meter face type is Sharp Sans in the SVG; "preferences" follows the
    # authored family instead of substituting Digital-7.
    return rasterize_settings_svg_bgra(
        root,
        width=int(DESIGN_W),
        height=int(DESIGN_H),
        font_mode="preferences",
    )


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
    alpha = src[:, :, 3]
    if int(alpha.min()) > 8:
        roi[:] = src
        return
    mask = alpha > 8
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


def _scale_patch_from_bottom(
    patch: np.ndarray,
    x: int,
    y: int,
    fill: float,
) -> tuple[np.ndarray, int, int] | None:
    """Crop a pre-rasterized bar from its authored bottom (fill 0–1).

    Solid fills look the same as a vertical scale and avoid a gather/resize.
    """
    fill = max(0.0, min(1.0, float(fill)))
    if fill <= 1e-4:
        return None
    ph, pw = patch.shape[:2]
    if ph < 1 or pw < 1:
        return None
    new_h = max(1, int(round(ph * fill)))
    if new_h >= ph:
        return patch, int(x), int(y)
    y_off = ph - new_h
    return patch[y_off:ph], int(x), int(y) + y_off


def _crop_patch_from_bottom_px(
    patch: np.ndarray,
    x: int,
    y: int,
    height_px: int,
) -> tuple[np.ndarray, int, int] | None:
    """Keep ``height_px`` rows from the authored bottom of a solid patch."""
    ph, pw = patch.shape[:2]
    if ph < 1 or pw < 1:
        return None
    new_h = max(1, min(ph, int(height_px)))
    if new_h >= ph:
        return patch, int(x), int(y)
    y_off = ph - new_h
    return patch[y_off:ph], int(x), int(y) + y_off


def _opaque_pixel(patch: np.ndarray) -> np.ndarray | None:
    alpha = patch[:, :, 3]
    ys, xs = np.where(alpha > 8)
    if xs.size == 0:
        return None
    return patch[int(ys[0]), int(xs[0])].copy()


def _lfe_patch_to_screen_edge(
    patch: np.ndarray,
    x: int,
    y: int,
    *,
    side: str,
    bar: tuple[np.ndarray, int, int] | None,
) -> tuple[np.ndarray, int, int]:
    """Solid LFE from the meter-facing edge out to the screen edge. Authored fill is kept."""
    color = _opaque_pixel(patch)
    if color is None:
        return patch, int(x), int(y)
    ph = int(patch.shape[0])
    screen_w = int(DESIGN_W)
    if side == "left":
        inner = int(bar[1]) if bar is not None else int(x) + int(patch.shape[1])
        inner = max(1, min(screen_w, inner))
        out = np.empty((ph, inner, 4), dtype=np.uint8)
        out[...] = color
        return out, 0, int(y)
    inner = int(bar[1] + bar[0].shape[1]) if bar is not None else int(x)
    inner = max(0, min(screen_w - 1, inner))
    max_w = screen_w - inner
    if max_w < 1:
        return patch, int(x), int(y)
    out = np.empty((ph, max_w, 4), dtype=np.uint8)
    out[...] = color
    return out, inner, int(y)


def _scale_lfe_patch(
    patch: np.ndarray,
    x: int,
    y: int,
    *,
    width_fill: float,
    anchor: str,
) -> tuple[np.ndarray, int, int] | None:
    """Crop LFE width from the inner (meter-facing) edge. Height is already meter-width."""
    width_fill = max(0.0, min(1.0, float(width_fill)))
    if width_fill <= 1e-4:
        return None
    ph, pw = patch.shape[:2]
    if ph < 1 or pw < 1:
        return None
    new_w = max(1, int(round(pw * width_fill)))
    if anchor == "right":
        x_off = pw - new_w
        strip = patch[:, x_off:pw]
        nx = int(x) + x_off
    else:
        strip = patch[:, :new_w]
        nx = int(x)
    return strip, nx, int(y)


def _ensure_art() -> tuple[np.ndarray, dict[str, tuple[np.ndarray, int, int]]]:
    global _bg_bgra
    with _art_lock:
        if _bg_bgra is not None and _bar_patches:
            return _bg_bgra, _bar_patches
        root = svg_tree_from_path()
        apply_meter_bars(root, left_on=False, right_on=False)
        apply_lfe_shapes(root, left_on=False, right_on=False)
        _prune_hidden_meter_shapes(root)
        bg = _rasterize(root)
        patches: dict[str, tuple[np.ndarray, int, int]] = {}
        lfe: dict[str, tuple[np.ndarray, int, int]] = {}
        for side in ("left", "right"):
            one = svg_tree_from_path()
            apply_meter_bars(one, left_on=(side == "left"), right_on=(side == "right"))
            apply_lfe_shapes(one, left_on=False, right_on=False)
            _remove_from_tree(one, find_meter_element(one, "background"))
            _remove_from_tree(one, find_meter_element(one, "calibration_group"))
            _remove_from_tree(one, find_meter_element(one, "LFE_group"))
            _prune_hidden_meter_shapes(one)
            cropped = _crop_ink(_rasterize(one))
            if cropped is not None:
                patches[side] = cropped
            slab = svg_tree_from_path()
            apply_meter_bars(slab, left_on=False, right_on=False)
            apply_lfe_shapes(slab, left_on=(side == "left"), right_on=(side == "right"))
            _remove_from_tree(slab, find_meter_element(slab, "background"))
            _remove_from_tree(slab, find_meter_element(slab, "calibration_group"))
            _remove_from_tree(slab, find_meter_element(slab, "continuous_meter_group"))
            _prune_hidden_meter_shapes(slab)
            lfe_cropped = _crop_ink(_rasterize(slab))
            if lfe_cropped is not None:
                bar = patches.get(side)
                height_px = int(bar[0].shape[1]) if bar is not None else lfe_cropped[0].shape[1]
                cropped_h = _crop_patch_from_bottom_px(
                    lfe_cropped[0], lfe_cropped[1], lfe_cropped[2], height_px
                )
                if cropped_h is not None:
                    lfe[side] = _lfe_patch_to_screen_edge(
                        cropped_h[0],
                        cropped_h[1],
                        cropped_h[2],
                        side=side,
                        bar=bar,
                    )
        _bg_bgra = bg
        _bar_patches.clear()
        _bar_patches.update(patches)
        _lfe_patches.clear()
        _lfe_patches.update(lfe)
        return _bg_bgra, _bar_patches


def clear_audio_meter_render_caches() -> None:
    global _bg_bgra, _frame_cache, _frame_cache_key, _live_bgra, _bar_union_rect
    global _widget_meter_key, _widget_meter_frame, _widget_native_buf
    with _art_lock:
        _bg_bgra = None
        _bar_patches.clear()
        _lfe_patches.clear()
        _frame_cache = None
        _frame_cache_key = None
        _live_bgra = None
        _bar_union_rect = None
        _prev_blit_rects.clear()
    _widget_meter_key = None
    _widget_meter_frame = None
    _widget_native_buf = None
    _SVG_TREE_TEMPLATES.clear()


def meter_bar_union_rect() -> tuple[int, int, int, int] | None:
    """Design-pixel union of the two authored bar columns, or None before art is ready."""
    global _bar_union_rect
    if _bar_union_rect is not None:
        return _bar_union_rect
    _, patches = _ensure_art()
    blobs = list(patches.values()) + list(_lfe_patches.values())
    if not blobs:
        return None
    x0 = y0 = 10**9
    x1 = y1 = 0
    for patch, x, y in blobs:
        ph, pw = patch.shape[:2]
        x0 = min(x0, int(x))
        y0 = min(y0, int(y))
        x1 = max(x1, int(x) + int(pw))
        y1 = max(y1, int(y) + int(ph))
    if x1 <= x0 or y1 <= y0:
        return None
    pad = 1
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(int(DESIGN_W), x1 + pad)
    y1 = min(int(DESIGN_H), y1 + pad)
    _bar_union_rect = (x0, y0, x1 - x0, y1 - y0)
    return _bar_union_rect


def _fill_cache_quant(fill: float) -> int:
    steps = int(FILL_CACHE_STEPS)
    return int(round(max(0.0, min(1.0, float(fill))) * steps))


def _restore_rect(
    live: np.ndarray,
    bg: np.ndarray,
    x: int,
    y: int,
    w: int,
    h: int,
) -> None:
    x0 = max(0, int(x))
    y0 = max(0, int(y))
    x1 = min(int(live.shape[1]), x0 + int(w))
    y1 = min(int(live.shape[0]), y0 + int(h))
    if x1 <= x0 or y1 <= y0:
        return
    live[y0:y1, x0:x1] = bg[y0:y1, x0:x1]


def _restore_shape_rects(
    live: np.ndarray,
    bg: np.ndarray,
    patches: dict[str, tuple[np.ndarray, int, int]],
) -> None:
    for patch, x, y in patches.values():
        ph, pw = patch.shape[:2]
        _restore_rect(live, bg, x, y, pw, ph)


def render_audio_meter_bgra(
    *,
    left_fill: float,
    right_fill: float,
    lfe_fill: float = 0.0,
    lfe_left: float | None = None,
    lfe_right: float | None = None,
) -> np.ndarray:
    """Return the live meter frame. Mutates on the next unique fill — copy if you keep it."""
    width_l = float(lfe_fill if lfe_left is None else lfe_left)
    width_r = float(lfe_fill if lfe_right is None else lfe_right)
    left_q = _fill_cache_quant(left_fill)
    right_q = _fill_cache_quant(right_fill)
    lfe_q = _fill_cache_quant(width_l) + (_fill_cache_quant(width_r) << 10)
    global _frame_cache, _frame_cache_key, _live_bgra
    key = (left_q, right_q, lfe_q)
    if _frame_cache is not None and _frame_cache_key == key:
        return _frame_cache
    bg, patches = _ensure_art()
    live = _live_bgra
    if live is None or live.shape != bg.shape:
        live = bg.copy()
        _live_bgra = live
        _prev_blit_rects.clear()
    else:
        for rx, ry, rw, rh in _prev_blit_rects:
            _restore_rect(live, bg, rx, ry, rw, rh)
    blit_rects: list[tuple[int, int, int, int]] = []
    for side, width, anchor in (
        ("left", width_l, "right"),
        ("right", width_r, "left"),
    ):
        slab = _lfe_patches.get(side)
        if slab is not None:
            scaled_lfe = _scale_lfe_patch(
                slab[0],
                slab[1],
                slab[2],
                width_fill=width,
                anchor=anchor,
            )
            if scaled_lfe is not None:
                _blit_opaque(live, scaled_lfe[0], scaled_lfe[1], scaled_lfe[2])
                ph, pw = scaled_lfe[0].shape[:2]
                blit_rects.append((scaled_lfe[1], scaled_lfe[2], pw, ph))
    for side, fill in (("left", left_fill), ("right", right_fill)):
        patch = patches.get(side)
        if patch is None:
            continue
        scaled = _scale_patch_from_bottom(patch[0], patch[1], patch[2], fill)
        if scaled is not None:
            _blit_opaque(live, scaled[0], scaled[1], scaled[2])
            ph, pw = scaled[0].shape[:2]
            blit_rects.append((scaled[1], scaled[2], pw, ph))
    _prev_blit_rects.clear()
    _prev_blit_rects.extend(blit_rects)
    _frame_cache = live
    _frame_cache_key = key
    return live


def render_audio_meter_composite_bgra(
    *,
    layer_opacity: float = 1.0,
) -> tuple[
    tuple[np.ndarray, tuple[int, int, int, int]],
    tuple[np.ndarray, tuple[int, int, int, int]],
]:
    sample = latest_meter_levels()
    frame = render_audio_meter_bgra(
        left_fill=sample.fill_l,
        right_fill=sample.fill_r,
        lfe_fill=sample.lfe_fill,
    )
    frame = _apply_layer_opacity(frame, layer_opacity)
    full_rect = (0, 0, int(DESIGN_W), int(DESIGN_H))
    return (frame, full_rect), (_EMPTY_PATCH, (0, 0, 1, 1))


_widget_meter_key: tuple[int, ...] | None = None
_widget_meter_frame: np.ndarray | None = None
_widget_native_buf: np.ndarray | None = None

# Leave the lower fifth of the well for a volume readout; sit the bars
# a little above the remaining center so they do not rest on the number.
STEREO_METER_BOTTOM_RESERVE_FRAC = 0.22
STEREO_METER_LIFT_FRAC = 0.04
STEREO_METER_READOUT_CY_IN_BAND = 0.52


def stereo_meter_volume_band_h(dest_h: int) -> int:
    """Height reserved under the NP levels bars for the volume number."""
    return max(0, int(round(max(1, int(dest_h)) * float(STEREO_METER_BOTTOM_RESERVE_FRAC))))


def levels_readout_center_y(zone_y: float, zone_h: float) -> float:
    """Design-Y center of the volume / TRT row under a portrait well."""
    h = max(1.0, float(zone_h))
    reserve = stereo_meter_volume_band_h(int(round(h)))
    return float(zone_y) + h - float(reserve) * float(STEREO_METER_READOUT_CY_IN_BAND)


def render_stereo_meter_widget_bgra(
    dest_w: int,
    dest_h: int,
    *,
    left_fill: float | None = None,
    right_fill: float | None = None,
) -> np.ndarray:
    """Two diagnostic green bars, letterboxed into the upper part of the well."""
    import cv2

    from pigeon.compositing import cv_resize_interp

    global _widget_meter_key, _widget_meter_frame, _widget_native_buf
    sample = latest_meter_levels()
    live = left_fill is None and right_fill is None
    if live:
        gain = title_meter_gain_db()
        fill_l = meter_fill_from_calibrated_dbfs(sample.cal_dbfs_l + gain)
        fill_r = meter_fill_from_calibrated_dbfs(sample.cal_dbfs_r + gain)
        _maybe_persist_title_peaks()
    else:
        fill_l = float(sample.fill_l if left_fill is None else left_fill)
        fill_r = float(sample.fill_r if right_fill is None else right_fill)
    dw = max(1, int(dest_w))
    dh = max(1, int(dest_h))
    key = (dw, dh, _fill_cache_quant(fill_l), _fill_cache_quant(fill_r))
    if _widget_meter_frame is not None and _widget_meter_key == key:
        return _widget_meter_frame
    _bg, patches = _ensure_art()
    left = patches.get("left")
    right = patches.get("right")
    if left is None or right is None:
        return np.zeros((max(1, int(dest_h)), max(1, int(dest_w)), 4), dtype=np.uint8)
    blobs: list[tuple[np.ndarray, int, int]] = []
    for fill, blob in ((fill_l, left), (fill_r, right)):
        scaled = _scale_patch_from_bottom(blob[0], blob[1], blob[2], fill)
        if scaled is not None:
            blobs.append(scaled)
        else:
            # Keep the authored column width so layout does not jump at silence.
            empty = np.zeros((1, int(blob[0].shape[1]), 4), dtype=np.uint8)
            blobs.append((empty, int(blob[1]), int(blob[2]) + int(blob[0].shape[0]) - 1))
    x0 = min(b[1] for b in blobs + [left, right])
    y0 = min(int(left[2]), int(right[2]))
    x1 = max(b[1] + b[0].shape[1] for b in blobs + [left, right])
    y1 = max(int(left[2]) + int(left[0].shape[0]), int(right[2]) + int(right[0].shape[0]))
    pad = 2
    need_h = max(1, y1 - y0 + pad * 2)
    need_w = max(1, x1 - x0 + pad * 2)
    native_buf = _widget_native_buf
    if (
        native_buf is None
        or native_buf.shape[0] < need_h
        or native_buf.shape[1] < need_w
    ):
        native_buf = np.zeros((need_h, need_w, 4), dtype=np.uint8)
        _widget_native_buf = native_buf
    else:
        native_buf[:need_h, :need_w] = 0
    native = native_buf[:need_h, :need_w]
    for patch, x, y in blobs:
        px = int(x) - x0 + pad
        py = int(y) - y0 + pad
        ph, pw = patch.shape[:2]
        if ph < 1 or pw < 1:
            continue
        y_a = max(0, py)
        x_a = max(0, px)
        y_b = min(native.shape[0], py + ph)
        x_b = min(native.shape[1], px + pw)
        if y_b <= y_a or x_b <= x_a:
            continue
        native[y_a:y_b, x_a:x_b] = patch[
            y_a - py : y_a - py + (y_b - y_a),
            x_a - px : x_a - px + (x_b - x_a),
        ]
    out = np.zeros((dh, dw, 4), dtype=np.uint8)
    nh, nw = int(native.shape[0]), int(native.shape[1])
    if nw < 1 or nh < 1:
        return out
    reserve = stereo_meter_volume_band_h(dh)
    usable_h = max(1, dh - reserve)
    inset = 0.88
    scale = min((dw * inset) / float(nw), (usable_h * inset) / float(nh))
    tw = max(1, int(round(nw * scale)))
    th = max(1, int(round(nh * scale)))
    resized = cv2.resize(native, (tw, th), interpolation=cv_resize_interp(nw, nh, tw, th))
    ox = (dw - tw) // 2
    lift = int(round(dh * float(STEREO_METER_LIFT_FRAC)))
    oy = max(0, (usable_h - th) // 2 - lift)
    y_a = max(0, oy)
    x_a = max(0, ox)
    y_b = min(dh, oy + th)
    x_b = min(dw, ox + tw)
    out[y_a:y_b, x_a:x_b] = resized[y_a - oy : y_a - oy + (y_b - y_a), x_a - ox : x_a - ox + (x_b - x_a)]
    _widget_meter_key = key
    _widget_meter_frame = out
    return out


def _rms_envelope(prev: float, rms: float, dt: float) -> float:
    """Instant attack on RMS; fall linearly in dB so the bar can drop between hits."""
    if rms >= prev:
        return float(rms)
    prev_db = dbfs_from_normalized(prev)
    next_db = prev_db - float(RELEASE_DB_PER_S) * max(0.0, float(dt))
    if next_db <= -120.0:
        return 0.0
    return float(10.0 ** (next_db / 20.0))


def _channel_peak(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    return float(np.max(np.abs(pcm.astype(np.float64))) / FULL_SCALE)


def _channel_rms(pcm: np.ndarray) -> float:
    if pcm.size == 0:
        return 0.0
    x = pcm.astype(np.float64, copy=False)
    return float(np.sqrt(np.mean(x * x)) / FULL_SCALE)


def _lowpass_cascade(
    samples: np.ndarray,
    state: np.ndarray,
    alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Cascade one-pole lowpasses. ``samples`` are already normalized to ±1."""
    x = np.asarray(samples, dtype=np.float64)
    z = np.array(state, dtype=np.float64, copy=True)
    a = float(alpha)
    n_poles = int(z.size)
    out = np.empty(x.size, dtype=np.float64)
    for i in range(x.size):
        v = float(x[i])
        for p in range(n_poles):
            z[p] += a * (v - z[p])
            v = float(z[p])
        out[i] = v
    return out, z


def _lfe_filter_chunk(
    mono: np.ndarray,
    state: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Boxcar-decimate to ``LFE_PROCESS_HZ``, then the 4-pole lowpass."""
    x = np.asarray(mono, dtype=np.float64)
    d = int(LFE_DECIM)
    n = (int(x.size) // d) * d
    if n < d:
        return np.zeros(0, dtype=np.float64), np.array(state, dtype=np.float64, copy=True)
    dec = x[:n].reshape(-1, d).mean(axis=1)
    return _lowpass_cascade(dec, state, LFE_LP_ALPHA)


def _normalized_rms(x: np.ndarray) -> float:
    if x.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(x * x)))


def _alsa_device() -> str:
    return os.environ.get("PIGEON_ALSA_CAPTURE_DEVICE", DEFAULT_ALSA_DEVICE).strip() or DEFAULT_ALSA_DEVICE


def _arecord_argv(arecord: str, *, low_latency: bool) -> list[str]:
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
    if low_latency:
        cmd.extend(["-F", str(int(ALSA_PERIOD_US)), "-B", str(int(ALSA_BUFFER_US))])
    return cmd


def _log(msg: str, *, flush: bool = False) -> None:
    try:
        sys.stderr.write(msg)
        if not msg.endswith("\n"):
            sys.stderr.write("\n")
        if flush:
            sys.stderr.flush()
    except Exception:
        pass


def _print_banner_once() -> None:
    global _logged_banner
    if _logged_banner:
        return
    _logged_banner = True
    _log(
        "pigeon: diagnostic stereo meter — ALSA "
        f"{_alsa_device()} 48 kHz S16_LE 2ch; [1] toggles clock saver while idle"
    )
    _log(
        f"pigeon: meter calibration {CALIBRATION_GAIN_DB:g} dB; "
        f"noise gate {NOISE_GATE_CAL_DBFS:g} dB cal; "
        f"full scale {METER_MAX_DBFS:g} dB cal; LFE {LFE_CUTOFF_HZ:g} Hz × {LFE_POLES} poles mono, "
        f"height = meter width to screen edge, 10 conceptual sections, continuous scale from bottom"
    )


def _publish(
    rms_l: float,
    rms_r: float,
    env_l: float,
    env_r: float,
    *,
    rms_lfe: float = 0.0,
    env_lfe: float = 0.0,
) -> MeterLevels:
    global _latest
    cap_l = dbfs_from_normalized(env_l)
    cap_r = dbfs_from_normalized(env_r)
    cal_l = calibrated_dbfs(cap_l)
    cal_r = calibrated_dbfs(cap_r)
    cap_lfe = dbfs_from_normalized(env_lfe)
    cal_lfe = calibrated_dbfs(cap_lfe)
    note_title_peak_db(cal_l, cal_r, cal_lfe)
    sample = MeterLevels(
        rms_l=float(rms_l),
        rms_r=float(rms_r),
        env_l=float(env_l),
        env_r=float(env_r),
        dbfs_l=cap_l,
        dbfs_r=cap_r,
        cal_dbfs_l=cal_l,
        cal_dbfs_r=cal_r,
        fill_l=meter_fill_from_calibrated_dbfs(cal_l),
        fill_r=meter_fill_from_calibrated_dbfs(cal_r),
        seg_l=meter_segments_from_calibrated_dbfs(cal_l),
        seg_r=meter_segments_from_calibrated_dbfs(cal_r),
        rms_lfe=float(rms_lfe),
        env_lfe=float(env_lfe),
        lfe_fill=lfe_fill_from_calibrated_dbfs(cal_lfe),
    )
    _latest = sample
    return sample


def _proc_ppid_from_stat(stat_text: str) -> int:
    """Parse ``/proc/<pid>/stat`` ppid. ``comm`` is in parentheses and may contain spaces."""
    rparen = stat_text.rfind(")")
    if rparen < 0:
        return 0
    fields = stat_text[rparen + 2 :].split()
    if len(fields) < 2:
        return 0
    try:
        return int(fields[1])
    except ValueError:
        return 0


def _cmdline_looks_like_arecord(cmdline: str, device: str) -> bool:
    return "arecord" in cmdline and device in cmdline


def _reap_arecord_children() -> int:
    """Kill leaked ``arecord`` on our ALSA device so capture can reopen.

    A second capture thread used to overwrite ``_proc`` and leave the first
    ``arecord`` holding ``hw:2,0``. Incoming audio then never reached Python.
    """
    my_pid = os.getpid()
    keep: int | None = None
    proc = _proc
    if proc is not None:
        keep = int(proc.pid)
    device = _alsa_device()
    killed = 0
    try:
        names = os.listdir("/proc")
    except Exception:
        return 0
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if keep is not None and pid == keep:
            continue
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text()
        except Exception:
            continue
        ppid = _proc_ppid_from_stat(stat_text)
        if ppid not in (my_pid, 1):
            continue
        try:
            raw = Path(f"/proc/{pid}/cmdline").read_bytes()
            cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if not _cmdline_looks_like_arecord(cmd, device):
            continue
        try:
            os.kill(pid, signal.SIGKILL)
            killed += 1
        except Exception:
            pass
    return killed


def _still_this_capture(gen: int) -> bool:
    return (not _stop.is_set()) and gen == _capture_gen


def _capture_loop(gen: int) -> None:
    global _proc, _logged_capture_fail, _last_pcm_mono, _pcm_ever, _capture_dead
    global _last_capture_busy_log
    if not _still_this_capture(gen):
        return
    _print_banner_once()
    nbytes = FRAMES_PER_CHUNK * CHANNELS * SAMPLE_WIDTH
    dt_chunk = float(FRAMES_PER_CHUNK) / float(SAMPLE_RATE)
    use_low_latency = True
    arecord = shutil.which("arecord")
    if not arecord:
        _capture_dead = True
        if not _logged_capture_fail:
            _logged_capture_fail = True
            _log("pigeon: meter capture: arecord not found (expected on Pi / ALSA)")
        _publish(0.0, 0.0, 0.0, 0.0)
        _reset_spectrum()
        return
    _capture_dead = False
    try:
        _capture_loop_body(gen, arecord, nbytes, dt_chunk, use_low_latency)
    except Exception as exc:
        _log(f"pigeon: meter capture: crashed: {exc}")
    finally:
        _kill_proc()
        _publish(0.0, 0.0, 0.0, 0.0)
        _reset_spectrum()


def _capture_loop_body(
    gen: int,
    arecord: str,
    nbytes: int,
    dt_chunk: float,
    use_low_latency: bool,
) -> None:
    global _proc, _logged_capture_fail, _last_pcm_mono, _pcm_ever, _capture_dead
    global _last_capture_busy_log
    last_diag = 0.0
    while _still_this_capture(gen):
        _kill_proc()
        leaked = _reap_arecord_children()
        if leaked:
            _log(f"pigeon: meter capture: reaped {leaked} leftover arecord")
            _stop.wait(0.4)
            if not _still_this_capture(gen):
                break
        cmd = _arecord_argv(arecord, low_latency=use_low_latency)
        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                start_new_session=True,
            )
        except Exception as exc:
            _capture_dead = True
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
        period_note = (
            f" period={ALSA_PERIOD_US}us buf={ALSA_BUFFER_US}us"
            if use_low_latency
            else " (default ALSA period)"
        )
        env_l = 0.0
        env_r = 0.0
        env_lfe = 0.0
        lp_state = np.zeros(int(LFE_POLES), dtype=np.float64)
        got_pcm = False
        while _still_this_capture(gen):
            try:
                ready, _, _ = select.select([stdout], [], [], 0.25)
            except Exception:
                break
            if not ready:
                continue
            raw = stdout.read(nbytes)
            if not raw:
                break
            if len(raw) < nbytes:
                continue
            got_pcm = True
            _pcm_ever = True
            _last_pcm_mono = time.monotonic()
            _capture_dead = False
            if last_diag <= 0.0:
                last_diag = time.monotonic()
                _log(f"pigeon: meter capture started ({_alsa_device()}{period_note})")
            pcm = np.frombuffer(raw, dtype="<i2")
            if pcm.size < CHANNELS:
                continue
            stereo = pcm.reshape(-1, CHANNELS)
            stereo_f = stereo.astype(np.float64) * (1.0 / FULL_SCALE)
            rms_l = _normalized_rms(stereo_f[:, 0])
            rms_r = _normalized_rms(stereo_f[:, 1])
            mono = (stereo_f[:, 0] + stereo_f[:, 1]) * 0.5
            y_lfe, lp_state = _lfe_filter_chunk(mono, lp_state)
            rms_lfe = _normalized_rms(y_lfe)
            env_l = _rms_envelope(env_l, rms_l, dt_chunk)
            env_r = _rms_envelope(env_r, rms_r, dt_chunk)
            env_lfe = _rms_envelope(env_lfe, rms_lfe, dt_chunk)
            sample = _publish(
                rms_l,
                rms_r,
                env_l,
                env_r,
                rms_lfe=rms_lfe,
                env_lfe=env_lfe,
            )
            _push_spectrum_mono(mono)
            _push_scope_bass(y_lfe)
            now = time.monotonic()
            if now - last_diag >= DIAG_PERIOD_S:
                last_diag = now
                peak_l = float(np.max(np.abs(stereo_f[:, 0])))
                peak_r = float(np.max(np.abs(stereo_f[:, 1])))
                _log(
                    "pigeon: meter "
                    f"L rms={sample.rms_l:.5f} env={sample.env_l:.5f} peak={peak_l:.5f} "
                    f"cap={sample.dbfs_l:.1f} cal={sample.cal_dbfs_l:.1f} "
                    f"fill={sample.fill_l:.3f} seg={sample.seg_l} | "
                    f"R rms={sample.rms_r:.5f} env={sample.env_r:.5f} peak={peak_r:.5f} "
                    f"cap={sample.dbfs_r:.1f} cal={sample.cal_dbfs_r:.1f} "
                    f"fill={sample.fill_r:.3f} seg={sample.seg_r} | "
                    f"lfe={sample.lfe_fill:.3f}"
                )
        err = b""
        proc = _proc
        try:
            if proc is not None and proc.stderr is not None:
                ready, _, _ = select.select([proc.stderr], [], [], 0.05)
                if ready:
                    err = proc.stderr.read() or b""
        except Exception:
            err = b""
        _kill_proc()
        if not _still_this_capture(gen):
            break
        busy = b"busy" in err.lower() if err else False
        if err:
            now_log = time.monotonic()
            if (not busy) or (now_log - _last_capture_busy_log >= 30.0):
                _last_capture_busy_log = now_log
                _log("pigeon: meter capture: " + err.decode("utf-8", "replace").strip())
        if use_low_latency and not got_pcm:
            use_low_latency = False
            _log("pigeon: meter capture: retrying without forced ALSA period")
            continue
        if err and not _logged_capture_fail:
            _logged_capture_fail = True
        if busy:
            leaked = _reap_arecord_children()
            if leaked:
                _log(f"pigeon: meter capture: reaped {leaked} leftover arecord")
            _stop.wait(0.5)
            continue
        if not got_pcm:
            _capture_dead = True
        _stop.wait(2.0)
    _publish(0.0, 0.0, 0.0, 0.0)
    _reset_spectrum()


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
        os.kill(int(proc.pid), signal.SIGKILL)
    except Exception:
        pass
    try:
        proc.wait(timeout=0.4)
    except Exception:
        pass


def ensure_audio_meter_capture() -> None:
    global _thread, _capture_gen, _capture_dead
    with _start_lock:
        if _thread is not None and _thread.is_alive():
            return
        _capture_gen += 1
        gen = _capture_gen
        _stop.clear()
        _capture_dead = False
        _kill_proc()
        _reap_arecord_children()
        _thread = threading.Thread(
            target=_capture_loop,
            name="pigeon-audio-meter",
            args=(gen,),
            daemon=True,
        )
        _thread.start()


def stop_audio_meter_capture() -> None:
    global _capture_gen
    with _start_lock:
        _capture_gen += 1
        _stop.set()
        _kill_proc()
        _reap_arecord_children()


def sync_audio_meter_capture(wanted: bool) -> None:
    """Keep capture across NP ↔ clock-saver flips so a stop cannot leak arecord."""
    global _keep_capture_until
    now = time.monotonic()
    if wanted:
        _keep_capture_until = now + float(CAPTURE_HOLD_AFTER_UNWANTED_S)
        ensure_audio_meter_capture()
        return
    if now < _keep_capture_until:
        return
    stop_audio_meter_capture()


atexit.register(stop_audio_meter_capture)
