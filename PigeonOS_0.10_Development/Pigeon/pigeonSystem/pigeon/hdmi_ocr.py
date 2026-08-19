"""HDMI frame → OCR lines → OcrClues. Soft-fails if capture or Tesseract is missing.

Runs off the UI thread. At most one OCR pass at a time.
Pause checks: rising edge only, and not more than once per minute.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from pigeon.ocr_clues import OcrClues, clues_agree_with_metadata, clues_from_lines

CAPTURE_INDEX = 0
PREFERRED_WIDTH = 1920
PREFERRED_HEIGHT = 1080
PAUSE_OCR_MIN_INTERVAL_S = 60.0
NO_METADATA_RETRY_S = 5.0
# After TMDb has a title (or art is up), re-check HDMI every this many seconds.
WATCH_OCR_INTERVAL_S = 5.0
POSITION_STALL_S = 5.0
POSITION_ADVANCE_EPS = 0.2
OCR_TITLE_CONFIRM_HITS = 2
# Unchanged HDMI frames in a row before HDMI-driven clock saver arms.
CLOCK_SAVER_OCR_STREAK = 24
# Mean abs diff (0–255) above this → frame considered changed.
_FRAME_DIFF_MEAN_MIN = 6.0
_FRAME_FP_W = 48
_FRAME_FP_H = 27
TITLE_MIN_HEIGHT = 40
TOP_RIGHT_X, TOP_RIGHT_Y, TOP_RIGHT_W, TOP_RIGHT_H = 0.52, 0.0, 0.48, 0.30

_HDMI_NAME_HINTS = (
    "blueavs",
    "hdmi",
    "capture",
    "usb video",
    "av to usb",
    "magewell",
    "elgato",
    "cam link",
    "video capture",
)
_SKIP_NAME_HINTS = (
    "facetime",
    "isight",
    "iphone",
    "ipad",
    "continuity",
    "studio display",
    "desk view",
    "macbook",
    "built-in",
)

OnClues = Callable[[OcrClues], None]


_OCR_FIELD_KEYS = (
    "ocr_title",
    "ocr_lines",
    "ocr_season",
    "ocr_episode",
    "ocr_year",
    "ocr_runtime_min",
    "ocr_extras",
    "ocr_reason",
    "ocr_agrees",
    "ocr_at",
    "ocr_status",
    "ocr_capture",
    "ocr_pending_title",
    "ocr_pending_hits",
    "ocr_frame_changed",
    "ocr_unchanged_streak",
)


@dataclass
class OcrSchedule:
    was_paused: bool = False
    last_pause_ocr_mono: float = 0.0
    last_no_meta_ocr_mono: float = 0.0
    last_content_key: str | None = None
    last_confirm_key: str | None = None
    last_position: float | None = None
    last_position_change_mono: float = 0.0
    in_flight: bool = False
    last_frame_fp: Any = None
    consecutive_unchanged: int = 0
    last_frame_changed: bool = False


_schedule = OcrSchedule()


def reset_ocr_schedule() -> None:
    """Clear cadence / position / frame memory (tests, HDMI off)."""
    global _schedule
    _schedule = OcrSchedule()


def _position_is_advancing(md: Mapping[str, Any], now: float) -> bool:
    """Track reported position; False when missing or unchanged past the stall window."""
    from pigeon.display_confidence import parse_position

    pos = parse_position(md)
    if pos is None:
        return False
    prev = _schedule.last_position
    if prev is None or abs(pos - prev) >= POSITION_ADVANCE_EPS:
        _schedule.last_position = pos
        _schedule.last_position_change_mono = now
        return True
    return (now - _schedule.last_position_change_mono) < POSITION_STALL_S


def hdmi_unchanged_streak() -> int:
    """How many consecutive OCR reads saw an unchanged HDMI frame."""
    return int(_schedule.consecutive_unchanged)


def hdmi_clock_saver_due() -> bool:
    """True after ``CLOCK_SAVER_OCR_STREAK`` unchanged HDMI frames in a row."""
    return int(_schedule.consecutive_unchanged) >= CLOCK_SAVER_OCR_STREAK


def hdmi_last_frame_changed() -> bool:
    """True when the most recent OCR frame differed from the prior fingerprint."""
    return bool(_schedule.last_frame_changed)


def _frame_fingerprint(frame) -> Any:
    """Small grayscale downsample used to detect HDMI content changes."""
    try:
        import cv2
        import numpy as np

        if frame is None or not getattr(frame, "size", 0):
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        small = cv2.resize(
            gray, (_FRAME_FP_W, _FRAME_FP_H), interpolation=cv2.INTER_AREA
        )
        return small.astype(np.float32)
    except Exception:
        return None


def _note_frame_fingerprint(frame) -> bool:
    """Compare ``frame`` to the last OCR fingerprint. Returns True if changed."""
    fp = _frame_fingerprint(frame)
    prev = _schedule.last_frame_fp
    changed = True
    if fp is not None and prev is not None:
        try:
            import numpy as np

            diff = float(np.mean(np.abs(fp - prev)))
            changed = diff >= _FRAME_DIFF_MEAN_MIN
        except Exception:
            changed = True
    if fp is not None:
        _schedule.last_frame_fp = fp
    if changed:
        _schedule.consecutive_unchanged = 0
        _schedule.last_frame_changed = True
    else:
        _schedule.consecutive_unchanged = int(_schedule.consecutive_unchanged) + 1
        _schedule.last_frame_changed = False
    return bool(_schedule.last_frame_changed)


_lock = threading.Lock()
_cap = None
_cap_index: int | None = None
_last_capture_label = ""
_tesseract_ready = False
_av_devices_cache: list[tuple[int, str, str]] | None = None
_hdmi_present: bool | None = None
_hdmi_probe_mono: float = 0.0
_hdmi_probe_in_flight: bool = False
_hdmi_no_signal_hits: int = 0
_HDMI_PROBE_TTL_S = 1.5
# Near-black, near-flat frames (typical HDMI-unplug output from a USB dongle).
_NO_SIGNAL_MEAN_MAX = 8.0
_NO_SIGNAL_STD_MAX = 4.0
_NO_SIGNAL_HITS_TO_DROP = 2


def _can_pass_to_tmdb(md: Mapping[str, Any]) -> bool:
    """True when we already have a title TMDb will accept."""
    try:
        from pigeon.tmdb_poster import is_degenerate_tmdb_query
    except ImportError:

        def is_degenerate_tmdb_query(q: str) -> bool:  # type: ignore[misc]
            return len(str(q or "").strip()) < 2

    from pigeon.ocr_clues import looks_like_ocr_junk

    for key in ("ocr_title", "query"):
        q = str(md.get(key) or "").strip()
        if q and not is_degenerate_tmdb_query(q) and not looks_like_ocr_junk(q):
            return True
    return False


def decide_ocr_reason(
    metadata: Mapping[str, Any] | None,
    now: float | None = None,
    *,
    tmdb_up: bool = False,
) -> str | None:
    """When should we look at HDMI?

    ``no_metadata`` — keep OCR'ing until there is a title TMDb will accept.
    ``watch`` — TMDb already has (or can get) content; re-check every 5s for a
    new / different screen (image fingerprint + OCR text).
    ``pause`` — rising pause edge (at most once a minute).
    """
    now = time.monotonic() if now is None else now
    md = metadata if isinstance(metadata, dict) else {}
    ds = str(md.get("device_state") or "").lower()
    paused = "paused" in ds or ds.endswith("pause")
    content_key = str(md.get("content_key") or "") or None
    tmdb_ready = _can_pass_to_tmdb(md) or bool(tmdb_up)

    # Keep position cadence warm for other callers even though schedule no longer
    # gates OCR on stall alone.
    _position_is_advancing(md, now)

    key_changed = content_key != _schedule.last_content_key
    if key_changed:
        _schedule.last_content_key = content_key

    rising_pause = paused and not _schedule.was_paused
    _schedule.was_paused = paused
    if rising_pause and (now - _schedule.last_pause_ocr_mono) >= PAUSE_OCR_MIN_INTERVAL_S:
        _schedule.last_pause_ocr_mono = now
        return "pause"

    # New content identity without a usable title → OCR immediately.
    if key_changed and not tmdb_ready:
        _schedule.last_no_meta_ocr_mono = now
        return "no_metadata"

    interval = NO_METADATA_RETRY_S if not tmdb_ready else WATCH_OCR_INTERVAL_S
    due = (now - _schedule.last_no_meta_ocr_mono) >= interval
    if not due:
        return None
    _schedule.last_no_meta_ocr_mono = now
    if not tmdb_ready:
        return "no_metadata"
    return "watch"


def request_ocr(reason: str, on_done: OnClues) -> bool:
    """Start one background OCR pass. Returns False if already busy or OCR is unavailable."""
    try:
        from pigeon.source_toggles import source_enabled

        if not source_enabled("hdmi"):
            return False
    except Exception:
        pass
    if not hdmi_capture_available():
        # Keep probing so a re-plug (or signal return) can turn the LED green.
        probe_hdmi_presence()
        return False
    with _lock:
        if _schedule.in_flight:
            return False
        _schedule.in_flight = True
    thread = threading.Thread(
        target=_ocr_worker,
        args=(reason, on_done),
        name="hdmi-ocr",
        daemon=True,
    )
    thread.start()
    return True


def _ocr_worker(reason: str, on_done: OnClues) -> None:
    global _last_capture_label
    clues = OcrClues(reason=reason)
    try:
        frame = _grab_frame()
        if frame is None:
            _last_capture_label = "none"
            clues = OcrClues(reason=reason, extras=["capture_unavailable"])
            _schedule.last_frame_changed = True
            _schedule.consecutive_unchanged = 0
        elif not _configure_tesseract():
            h, w = frame.shape[:2]
            name = _device_name(int(_cap_index)) if _cap_index is not None else ""
            _last_capture_label = f"{_cap_index} {name} {w}x{h}".strip()
            changed = _note_frame_fingerprint(frame)
            clues = OcrClues(
                reason=reason,
                extras=[
                    _tesseract_error or "tesseract_unavailable",
                    "frame_changed" if changed else "frame_same",
                ],
            )
        else:
            h, w = frame.shape[:2]
            name = _device_name(int(_cap_index)) if _cap_index is not None else ""
            _last_capture_label = f"{_cap_index} {name} {w}x{h}".strip()
            changed = _note_frame_fingerprint(frame)
            lines = _ocr_lines(frame)
            clues = clues_from_lines(lines, reason=reason)
            clues.extras = list(clues.extras or [])
            clues.extras.append("frame_changed" if changed else "frame_same")
    except Exception:
        clues = OcrClues(reason=reason, extras=["ocr_error"])
    finally:
        with _lock:
            _schedule.in_flight = False
    try:
        on_done(clues)
    except Exception:
        pass


def apply_clues_to_metadata(
    metadata: dict[str, Any],
    clues: OcrClues,
) -> dict[str, Any]:
    """Copy OCR clues onto the live metadata dict. Does not drop pyatv fields."""
    out = dict(metadata)
    prev_title = str(out.get("ocr_title") or "").strip()
    out.update(clues.as_metadata_fields())
    if not str(out.get("ocr_title") or "").strip() and prev_title:
        out["ocr_title"] = prev_title
    out["ocr_agrees"] = clues_agree_with_metadata(clues, metadata)
    out["ocr_at"] = time.time()
    extra0 = ""
    if isinstance(out.get("ocr_extras"), list) and out["ocr_extras"]:
        extra0 = str(out["ocr_extras"][0])
    if extra0 in ("capture_unavailable", "tesseract_unavailable", "ocr_error"):
        out["ocr_status"] = extra0
        if extra0 == "capture_unavailable":
            # HDMI cannot feed Pigeon — drop stale OCR so it cannot look live.
            for key in _OCR_FIELD_KEYS:
                if key in ("ocr_status", "ocr_reason", "ocr_extras", "ocr_at"):
                    continue
                out.pop(key, None)
    elif str(out.get("ocr_title") or "").strip() or (
        isinstance(out.get("ocr_lines"), list) and out["ocr_lines"]
    ):
        out["ocr_status"] = "ok"
    else:
        out["ocr_status"] = "no_text"
    if _last_capture_label:
        out["ocr_capture"] = _last_capture_label
    out["ocr_frame_changed"] = bool(_schedule.last_frame_changed)
    out["ocr_unchanged_streak"] = int(_schedule.consecutive_unchanged)
    return out


def _identity_is_placeholder(value: str) -> bool:
    from pigeon.display_confidence import is_placeholder_identity

    return is_placeholder_identity(value)


def ocr_session_anchor(metadata: Mapping[str, Any] | None) -> str:
    """pyatv-only identity used to decide whether last HDMI clues still apply."""
    md = metadata if isinstance(metadata, dict) else {}
    app = str(md.get("app_id") or md.get("app_name") or "").strip().casefold()
    query = str(md.get("query") or "").strip()
    if _identity_is_placeholder(query):
        query = ""
    return f"{app}|{query.casefold()}"


def apply_ocr_title_as_identity(metadata: dict[str, Any]) -> bool:
    """Fill or replace identity from OCR when the player did not supply a title.

    First good read fills an empty identity. A different title (next card, or a
    service that just lost pyatv metadata) must match twice before it takes over.
    Player-owned titles are left alone.
    """
    from pigeon.display_confidence import (
        OCR_IDENTITY,
        mark_identity,
        player_metadata_adequate,
    )

    guess = str(metadata.get("ocr_title") or "").strip()
    if not guess or _identity_is_placeholder(guess):
        return False
    if player_metadata_adequate(metadata):
        return False
    current = str(metadata.get("query") or "").strip()
    if _identity_is_placeholder(current):
        metadata["query"] = guess
        if _identity_is_placeholder(str(metadata.get("title") or "")):
            metadata["title"] = guess
        mark_identity(metadata, source="ocr", confidence=OCR_IDENTITY)
        metadata["ocr_pending_title"] = ""
        metadata["ocr_pending_hits"] = 0
        try:
            from pigeon.title_decision import apply_decision_to_metadata, record_title_decision

            decision = record_title_decision(
                guess,
                source="ocr",
                reason="HDMI OCR filled an empty player title",
            )
            apply_decision_to_metadata(metadata, decision)
        except Exception:
            pass
        return True
    if current.casefold() == guess.casefold():
        mark_identity(metadata, source="ocr", confidence=OCR_IDENTITY)
        metadata["ocr_pending_title"] = ""
        metadata["ocr_pending_hits"] = 0
        return False
    pending = str(metadata.get("ocr_pending_title") or "").strip()
    hits = int(metadata.get("ocr_pending_hits") or 0)
    if pending.casefold() == guess.casefold():
        hits += 1
    else:
        pending = guess
        hits = 1
    metadata["ocr_pending_title"] = pending
    metadata["ocr_pending_hits"] = hits
    if hits < OCR_TITLE_CONFIRM_HITS:
        return False
    metadata["query"] = guess
    metadata["title"] = guess
    mark_identity(metadata, source="ocr", confidence=OCR_IDENTITY)
    metadata["ocr_pending_title"] = ""
    metadata["ocr_pending_hits"] = 0
    try:
        from pigeon.title_decision import apply_decision_to_metadata, record_title_decision

        decision = record_title_decision(
            guess,
            source="ocr",
            reason=f"HDMI OCR confirmed a new title after {OCR_TITLE_CONFIRM_HITS} matching reads",
            extras={"replaced": current},
        )
        apply_decision_to_metadata(metadata, decision)
    except Exception:
        pass
    return True


def copy_ocr_fields(src: Mapping[str, Any] | None, dest: dict[str, Any]) -> None:
    """Keep last OCR clues across a pyatv poll that does not re-run OCR."""
    if not isinstance(src, dict):
        return
    for key in _OCR_FIELD_KEYS:
        if key in src:
            dest[key] = src[key]


def clear_ocr_fields(dest: dict[str, Any]) -> None:
    """Drop HDMI OCR clues when that source is toggled off."""
    for key in _OCR_FIELD_KEYS:
        dest.pop(key, None)


def _drop_open_capture() -> None:
    """Close the OpenCV handle without resetting OCR cadence."""
    global _cap, _cap_index, _av_devices_cache
    cap = _cap
    _cap = None
    _cap_index = None
    _av_devices_cache = None
    if cap is None:
        return
    try:
        cap.release()
    except Exception:
        pass


def release_capture() -> None:
    """Free the HDMI capture device so another process can use it."""
    reset_ocr_schedule()
    with _lock:
        _drop_open_capture()


_tesseract_error = ""


def _configure_tesseract() -> bool:
    global _tesseract_ready, _tesseract_error
    if _tesseract_ready:
        return True
    try:
        import pytesseract
    except ImportError:
        _tesseract_error = "pytesseract_missing"
        return False
    brew_bin = "/opt/homebrew/bin"
    path_now = os.environ.get("PATH") or ""
    if brew_bin not in path_now.split(":"):
        os.environ["PATH"] = f"{brew_bin}:{path_now}" if path_now else brew_bin
    if not os.environ.get("TESSDATA_PREFIX"):
        for tessdata in (
            "/opt/homebrew/share/tessdata",
            "/usr/local/share/tessdata",
            "/usr/share/tesseract-ocr/5/tessdata",
            "/usr/share/tesseract-ocr/4.00/tessdata",
        ):
            if os.path.isfile(os.path.join(tessdata, "eng.traineddata")):
                os.environ["TESSDATA_PREFIX"] = tessdata
                break
    for path in (
        shutil.which("tesseract"),
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ):
        if path and os.path.isfile(path):
            pytesseract.pytesseract.tesseract_cmd = path
            try:
                pytesseract.get_tesseract_version()
            except Exception:
                continue
            _tesseract_ready = True
            _tesseract_error = ""
            return True
    _tesseract_error = "tesseract_bin_missing"
    return False


def _is_skip_camera(name: str, dtype: str = "") -> bool:
    blob = f"{name} {dtype}".lower()
    return any(skip in blob for skip in _SKIP_NAME_HINTS)


def _is_hdmi_camera(name: str, dtype: str = "") -> bool:
    if _is_skip_camera(name, dtype):
        return False
    low = name.lower()
    return any(hint in low for hint in _HDMI_NAME_HINTS)


def note_hdmi_present(present: bool) -> None:
    """Remember whether HDMI can currently deliver a video frame (settings LED)."""
    global _hdmi_present, _hdmi_no_signal_hits
    _hdmi_present = bool(present)
    if present:
        _hdmi_no_signal_hits = 0


def _linux_usb_video_indices() -> list[int]:
    """V4L2 indices of USB video devices — SoC codec/ISP nodes don't count.

    Raspberry Pi exposes a dozen /dev/video* nodes (bcm2835 codec/ISP, HEVC
    decoder) with nothing plugged in; a capture dongle is the only device
    whose sysfs path routes through the USB bus.
    """
    import glob
    import re as _re

    out: list[int] = []
    for sys_dir in sorted(glob.glob("/sys/class/video4linux/video*")):
        m = _re.search(r"video(\d+)$", sys_dir)
        if m is None:
            continue
        try:
            real = os.path.realpath(os.path.join(sys_dir, "device"))
        except OSError:
            continue
        if "/usb" in real:
            out.append(int(m.group(1)))
    return out


def hdmi_capture_available() -> bool:
    """True when HDMI can currently deliver a video frame to Pigeon.

    An open OpenCV handle is not enough: after the cable is pulled the capture
    often stays ``isOpened()`` (and the USB dongle may still enumerate), which
    used to leave the settings LED green while OCR was impossible.
    """
    return bool(_hdmi_present)


def probe_hdmi_presence(*, force: bool = False) -> bool:
    """Return last known presence; refresh capture/signal in the background."""
    global _hdmi_probe_in_flight, _hdmi_probe_mono
    now = time.monotonic()
    stale = (
        force
        or _hdmi_probe_mono <= 0.0
        or (now - _hdmi_probe_mono) >= _HDMI_PROBE_TTL_S
    )
    if stale:
        with _lock:
            start = not _hdmi_probe_in_flight
            if start:
                _hdmi_probe_in_flight = True
        if start:
            threading.Thread(
                target=_hdmi_probe_worker, name="hdmi-probe", daemon=True
            ).start()
    return hdmi_capture_available()


def _frame_has_video_signal(frame) -> bool:
    """False for empty / near-black frames that cannot yield HDMI metadata."""
    if frame is None or not getattr(frame, "size", 0):
        return False
    try:
        import numpy as np

        arr = np.asarray(frame)
        if arr.size == 0:
            return False
        if arr.ndim == 3:
            gray = (
                arr[..., 0].astype(np.float32) * 0.114
                + arr[..., 1].astype(np.float32) * 0.587
                + arr[..., 2].astype(np.float32) * 0.299
            )
        else:
            gray = arr.astype(np.float32)
        step_y = max(1, int(gray.shape[0]) // 36)
        step_x = max(1, int(gray.shape[1]) // 64)
        sample = gray[::step_y, ::step_x]
        mean = float(sample.mean())
        std = float(sample.std())
        return mean > _NO_SIGNAL_MEAN_MAX or std > _NO_SIGNAL_STD_MAX
    except Exception:
        return True


def _hdmi_device_enumerated() -> bool:
    """True when a capture dongle is listed (USB present; signal not required)."""
    global _av_devices_cache
    if sys.platform == "darwin":
        _av_devices_cache = None
        named = _avfoundation_devices()
        return any(_is_hdmi_camera(name, dtype) for _i, name, dtype in named)
    if sys.platform.startswith("linux"):
        return bool(_linux_usb_video_indices())
    cached = _av_devices_cache
    if cached is not None:
        return any(_is_hdmi_camera(name, dtype) for _i, name, dtype in cached)
    return False


def _note_frame_signal(frame) -> bool:
    """Update presence from one grabbed frame. True when the frame has signal."""
    global _hdmi_no_signal_hits
    if frame is None or not getattr(frame, "size", 0):
        _hdmi_no_signal_hits = 0
        note_hdmi_present(False)
        return False
    if _frame_has_video_signal(frame):
        note_hdmi_present(True)
        return True
    _hdmi_no_signal_hits += 1
    if _hdmi_no_signal_hits >= _NO_SIGNAL_HITS_TO_DROP:
        note_hdmi_present(False)
        return False
    return bool(_hdmi_present)


def _read_open_capture():
    """One ``cap.read()`` from the current handle. ``None`` if it failed."""
    cap = _cap
    if cap is None:
        return None
    try:
        if not cap.isOpened():
            return None
        ok, frame = cap.read()
    except Exception:
        return None
    if not ok or frame is None or not getattr(frame, "size", 0):
        return None
    return frame


def _probe_hdmi_now() -> bool:
    """Check live HDMI signal; enumeration is a fallback when capture is dead.

    Fast path: read the already-open device (catches HDMI cable unplug without
    waiting on Swift / sysfs). An open handle alone never counts as present.
    """
    if _schedule.in_flight:
        return bool(_hdmi_present)

    frame = _read_open_capture()
    if frame is not None:
        return _note_frame_signal(frame)

    if _cap is not None:
        _drop_open_capture()
        note_hdmi_present(False)

    if not _hdmi_device_enumerated():
        note_hdmi_present(False)
        return False

    peeked = _grab_frame()
    if peeked is None:
        note_hdmi_present(False)
        return False
    return bool(_hdmi_present)


def _hdmi_probe_worker() -> None:
    global _hdmi_probe_in_flight, _hdmi_probe_mono
    try:
        _probe_hdmi_now()
        _hdmi_probe_mono = time.monotonic()
    finally:
        with _lock:
            _hdmi_probe_in_flight = False


def _avfoundation_devices() -> list[tuple[int, str, str]]:
    """OpenCV AVFoundation indexes with names. Cached; Swift is slow to start."""
    global _av_devices_cache
    if _av_devices_cache is not None:
        return _av_devices_cache
    if sys.platform != "darwin":
        _av_devices_cache = []
        return _av_devices_cache
    # Same enumeration OpenCV uses: video + muxed, sorted by uniqueID.
    script = (
        "import AVFoundation\n"
        "import Foundation\n"
        "let video = AVCaptureDevice.devices(for: .video)\n"
        "let muxed = AVCaptureDevice.devices(for: .muxed)\n"
        "let all = (video + muxed).sorted { $0.uniqueID < $1.uniqueID }\n"
        "for (i, d) in all.enumerated() {\n"
        '    print("\\(i)\\t\\(d.localizedName)\\t\\(d.deviceType.rawValue)")\n'
        "}\n"
    )
    path = ""
    try:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".swift", delete=False) as handle:
            handle.write(script)
            path = handle.name
        result = subprocess.run(
            ["swift", path],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _av_devices_cache = []
        return _av_devices_cache
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
    out: list[tuple[int, str, str]] = []
    for line in (result.stdout or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        idx = int(parts[0].strip())
        name = parts[1].strip()
        dtype = parts[2].strip() if len(parts) > 2 else ""
        if name:
            out.append((idx, name, dtype))
    _av_devices_cache = out
    return _av_devices_cache


def _device_name(index: int) -> str:
    for idx, name, _dtype in _avfoundation_devices():
        if idx == index:
            return name
    return ""


def _candidate_capture_indices() -> list[int]:
    env = str(os.environ.get("PIGEON_HDMI_CAPTURE_INDEX") or "").strip()
    if env.isdigit():
        return [int(env)]
    named = _avfoundation_devices()
    hdmi = [i for i, name, dtype in named if _is_hdmi_camera(name, dtype)]
    if hdmi:
        return hdmi
    other = [i for i, name, dtype in named if not _is_skip_camera(name, dtype)]
    if other:
        return other
    if named:
        return []
    # Do not scan 0..3 on macOS — Continuity Camera often sits at index 1.
    if sys.platform == "darwin":
        return []
    if sys.platform.startswith("linux"):
        # Only USB devices — opening the Pi's SoC codec/ISP nodes fails with
        # V4L2 "can't capture by index" noise and false presence.
        return _linux_usb_video_indices()
    out = [CAPTURE_INDEX]
    for extra in (1, 2, 3):
        if extra not in out:
            out.append(extra)
    return out


def _opencv_backend():
    import cv2

    if sys.platform == "darwin" and hasattr(cv2, "CAP_AVFOUNDATION"):
        return cv2.CAP_AVFOUNDATION
    if sys.platform.startswith("linux") and hasattr(cv2, "CAP_V4L2"):
        return cv2.CAP_V4L2
    return cv2.CAP_ANY


def _open_capture_at(index: int):
    import cv2

    cap = cv2.VideoCapture(index, _opencv_backend())
    if cap is None or not cap.isOpened():
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
        return None
    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    cap.set(cv2.CAP_PROP_FOURCC, fourcc)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, float(PREFERRED_WIDTH))
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, float(PREFERRED_HEIGHT))
    frame = None
    for _ in range(8):
        ok, frame = cap.read()
        if ok and frame is not None and getattr(frame, "size", 0):
            return cap
    try:
        cap.release()
    except Exception:
        pass
    return None


def _grab_frame():
    global _cap, _cap_index, _av_devices_cache
    try:
        import cv2  # noqa: F401
    except ImportError:
        return None
    if _cap is None or not _cap.isOpened():
        _cap = None
        _cap_index = None
        env = str(os.environ.get("PIGEON_HDMI_CAPTURE_INDEX") or "").strip()
        if env.isdigit():
            opened = _open_capture_at(int(env))
            if opened is not None:
                _cap = opened
                _cap_index = int(env)
        else:
            # Prefer the named HDMI dongle (blueAVS). Never open iPhone / FaceTime
            # when a capture card is present — iPhone is also "external" and 1080p.
            for index in _candidate_capture_indices():
                opened = _open_capture_at(index)
                if opened is None:
                    continue
                _cap = opened
                _cap_index = index
                break
        if _cap is None:
            note_hdmi_present(False)
            return None
    ok, frame = _cap.read()
    if not ok or frame is None or not getattr(frame, "size", 0):
        note_hdmi_present(False)
        _drop_open_capture()
        return None
    if not _note_frame_signal(frame):
        # Keep the handle open so a later probe can see the cable return.
        return None
    return frame


def _ocr_lines(bgr_frame) -> list[str]:
    if not _configure_tesseract():
        return []
    import cv2

    hits = _hits_from_image(_gray_variants(bgr_frame))
    hits.extend(_hits_top_right(bgr_frame))
    hits.extend(_hits_left_title(bgr_frame))
    hits = _dedupe_hits(hits)
    grouped = _group_lines(hits)
    return [h[0] for h in grouped if h[0]]


def _gray_variants(bgr_frame) -> list:
    import cv2

    gray = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    boosted = clahe.apply(gray)
    return [boosted, cv2.bitwise_not(boosted), gray]


def _dedupe_hits(hits: list[tuple]) -> list[tuple]:
    """Keep the highest-confidence copy when several passes see the same word."""
    best: dict[tuple, tuple] = {}
    for hit in hits:
        text, conf, left, top, width, height = hit
        key = (text.lower(), int(left) // 24, int(top) // 16)
        prior = best.get(key)
        if prior is None or float(conf) > float(prior[1]):
            best[key] = hit
    return list(best.values())


def _hits_from_image(images, min_conf: float = 30.0, config: str = "--psm 11") -> list[tuple]:
    import pytesseract

    out: list[tuple] = []
    for image in images:
        data = pytesseract.image_to_data(
            image, lang="eng", config=config, output_type=pytesseract.Output.DICT
        )
        n = len(data.get("text", []))
        for i in range(n):
            text = (data["text"][i] or "").strip()
            if not text or not any(ch.isalnum() for ch in text):
                continue
            try:
                conf = float(data["conf"][i])
            except (TypeError, ValueError):
                continue
            if conf < min_conf:
                continue
            left, top = int(data["left"][i]), int(data["top"][i])
            width, height = int(data["width"][i]), int(data["height"][i])
            if width < 6 or height < 8:
                continue
            out.append((text, conf, left, top, width, height))
    return out


def _hits_top_right(bgr_frame) -> list[tuple]:
    import cv2

    h, w = bgr_frame.shape[:2]
    x0 = int(w * TOP_RIGHT_X)
    y0 = int(h * TOP_RIGHT_Y)
    x1 = min(w, int(w * (TOP_RIGHT_X + TOP_RIGHT_W)))
    y1 = min(h, int(h * (TOP_RIGHT_Y + TOP_RIGHT_H)))
    crop = bgr_frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    _, bright = cv2.threshold(gray, 170, 255, cv2.THRESH_BINARY)
    paper = cv2.bitwise_not(bright)
    scale = 3.0
    big = cv2.resize(paper, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    raw = _hits_from_image([big], min_conf=25.0, config="--psm 7")
    shifted: list[tuple] = []
    for text, conf, left, top, width, height in raw:
        shifted.append(
            (
                text,
                conf,
                x0 + int(left / scale),
                y0 + int(top / scale),
                max(1, int(width / scale)),
                max(1, int(height / scale)),
            )
        )
    return shifted


def _hits_left_title(bgr_frame) -> list[tuple]:
    """Disney+ / Apple TV title cards put the name on the left, not top-right."""
    import cv2

    h, w = bgr_frame.shape[:2]
    x0, y0 = 0, int(h * 0.12)
    x1, y1 = int(w * 0.58), int(h * 0.62)
    crop = bgr_frame[y0:y1, x0:x1]
    if crop.size == 0:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    boosted = clahe.apply(gray)
    _, bright = cv2.threshold(boosted, 160, 255, cv2.THRESH_BINARY)
    paper = cv2.bitwise_not(bright)
    scale = 2.0
    big = cv2.resize(paper, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    raw = _hits_from_image([big], min_conf=25.0, config="--psm 11")
    shifted: list[tuple] = []
    for text, conf, left, top, width, height in raw:
        shifted.append(
            (
                text,
                conf,
                x0 + int(left / scale),
                y0 + int(top / scale),
                max(1, int(width / scale)),
                max(1, int(height / scale)),
            )
        )
    return shifted


def _group_lines(hits: list[tuple]) -> list[tuple]:
    if not hits:
        return []
    rows: list[list[tuple]] = []
    for hit in sorted(hits, key=lambda h: (h[3], h[2])):
        placed = False
        for row in rows:
            if _same_row(row, hit):
                row.append(hit)
                placed = True
                break
        if not placed:
            rows.append([hit])
    grouped: list[tuple] = []
    for row in rows:
        row.sort(key=lambda h: h[2])
        clusters: list[list[tuple]] = [[row[0]]]
        for hit in row[1:]:
            last = clusters[-1][-1]
            gap = hit[2] - (last[2] + last[4])
            typical_h = max(last[5], hit[5])
            limit = max(36, typical_h * 1.2) if typical_h >= TITLE_MIN_HEIGHT else max(180, typical_h * 8)
            if gap <= limit:
                clusters[-1].append(hit)
            else:
                clusters.append([hit])
        for cluster in clusters:
            text = " ".join(h[0] for h in cluster)
            left = min(h[2] for h in cluster)
            top = min(h[3] for h in cluster)
            right = max(h[2] + h[4] for h in cluster)
            bottom = max(h[3] + h[5] for h in cluster)
            grouped.append((text, left, top, right - left, bottom - top))
    grouped.sort(key=lambda h: (h[2], h[1]))
    return grouped


def _same_row(row: list[tuple], hit: tuple) -> bool:
    line_c = sum(h[3] + h[5] / 2.0 for h in row) / len(row)
    hit_c = hit[3] + hit[5] / 2.0
    taller = max(max(h[5] for h in row), hit[5])
    if abs(hit_c - line_c) > taller * 0.55:
        return False
    line_h = max(h[5] for h in row)
    smaller, big = sorted((line_h, hit[5]))
    if smaller < 1:
        return False
    if big < TITLE_MIN_HEIGHT:
        return True
    return big / smaller <= 2.2
