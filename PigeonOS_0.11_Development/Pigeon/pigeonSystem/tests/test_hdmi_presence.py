"""HDMI status LED follows a live video signal, not a stale OpenCV handle."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

import numpy as np  # noqa: E402

from pigeon import hdmi_ocr as ho  # noqa: E402
from pigeon.ocr_clues import OcrClues  # noqa: E402


class _FakeCap:
    def __init__(self, frame=None, *, opened: bool = True, ok: bool = True):
        self.frame = frame
        self._opened = opened
        self.ok = ok
        self.released = False

    def isOpened(self) -> bool:
        return bool(self._opened)

    def read(self):
        return self.ok, self.frame

    def release(self) -> None:
        self.released = True
        self._opened = False


class HdmiPresenceLedTests(unittest.TestCase):
    def setUp(self) -> None:
        ho._cap = None
        ho._cap_index = None
        ho._hdmi_present = None
        ho._hdmi_no_signal_hits = 0
        ho._hdmi_probe_in_flight = False
        ho._hdmi_probe_mono = 0.0
        ho._av_devices_cache = None
        ho.reset_ocr_schedule()

    def tearDown(self) -> None:
        ho._cap = None
        ho._hdmi_present = None
        ho._hdmi_no_signal_hits = 0
        ho._av_devices_cache = None

    def test_open_handle_does_not_keep_led_green(self) -> None:
        ho._hdmi_present = False
        ho._cap = _FakeCap(opened=True)
        self.assertFalse(ho.hdmi_capture_available())

    def test_note_present_drives_led(self) -> None:
        ho.note_hdmi_present(True)
        self.assertTrue(ho.hdmi_capture_available())
        ho.note_hdmi_present(False)
        self.assertFalse(ho.hdmi_capture_available())

    def test_black_frame_is_not_signal(self) -> None:
        black = np.zeros((1080, 1920, 3), dtype=np.uint8)
        self.assertFalse(ho._frame_has_video_signal(black))
        noisy = np.random.randint(0, 255, (180, 320, 3), dtype=np.uint8)
        self.assertTrue(ho._frame_has_video_signal(noisy))

    def test_failed_read_drops_presence(self) -> None:
        ho.note_hdmi_present(True)
        cap = _FakeCap(frame=None, opened=True, ok=False)
        ho._cap = cap
        original = ho._hdmi_device_enumerated
        ho._hdmi_device_enumerated = lambda: False  # type: ignore[method-assign]
        try:
            present = ho._probe_hdmi_now()
        finally:
            ho._hdmi_device_enumerated = original  # type: ignore[method-assign]
        self.assertFalse(present)
        self.assertFalse(ho.hdmi_capture_available())
        self.assertTrue(cap.released)

    def test_black_frames_drop_led_after_hysteresis(self) -> None:
        ho.note_hdmi_present(True)
        black = np.zeros((240, 320, 3), dtype=np.uint8)
        ho._cap = _FakeCap(frame=black, opened=True, ok=True)

        def _fail_enumerate() -> bool:
            raise AssertionError("black frames must use the open handle")

        original = ho._hdmi_device_enumerated
        ho._hdmi_device_enumerated = _fail_enumerate  # type: ignore[method-assign]
        try:
            first = ho._probe_hdmi_now()
            second = ho._probe_hdmi_now()
        finally:
            ho._hdmi_device_enumerated = original  # type: ignore[method-assign]
        self.assertTrue(first)
        self.assertFalse(second)
        self.assertFalse(ho.hdmi_capture_available())

    def test_live_frame_keeps_led_green(self) -> None:
        ho.note_hdmi_present(False)
        noisy = np.random.randint(0, 255, (240, 320, 3), dtype=np.uint8)
        ho._cap = _FakeCap(frame=noisy, opened=True, ok=True)
        self.assertTrue(ho._probe_hdmi_now())
        self.assertTrue(ho.hdmi_capture_available())

    def test_capture_unavailable_drops_stale_ocr_title(self) -> None:
        md = {"ocr_title": "Ted Lasso", "query": "Ted Lasso"}
        clues = OcrClues(reason="watch", extras=["capture_unavailable"])
        out = ho.apply_clues_to_metadata(md, clues)
        self.assertNotIn("ocr_title", out)
        self.assertEqual(out.get("ocr_status"), "capture_unavailable")


if __name__ == "__main__":
    unittest.main()
