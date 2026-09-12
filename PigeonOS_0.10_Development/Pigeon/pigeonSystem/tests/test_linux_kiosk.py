"""Linux kiosk fullscreen helpers (PiOS panel + window cover checks)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.linux_kiosk import (  # noqa: E402
    merge_stopped_pids,
    window_covers_display,
    write_blank_xcursor,
)


class _FakeRoot:
    def __init__(self, *, sw: int, sh: int, x: int, y: int, w: int, h: int) -> None:
        self._sw, self._sh = sw, sh
        self._x, self._y, self._w, self._h = x, y, w, h

    def winfo_screenwidth(self) -> int:
        return self._sw

    def winfo_screenheight(self) -> int:
        return self._sh

    def winfo_rootx(self) -> int:
        return self._x

    def winfo_rooty(self) -> int:
        return self._y

    def winfo_width(self) -> int:
        return self._w

    def winfo_height(self) -> int:
        return self._h


class LinuxKioskTests(unittest.TestCase):
    def test_covers_when_origin_and_size_match_screen(self) -> None:
        root = _FakeRoot(sw=1280, sh=800, x=0, y=0, w=1280, h=800)
        self.assertTrue(window_covers_display(root))

    def test_rejects_panel_offset_window(self) -> None:
        # Observed on the Pi: y=62, height=738 under wf-panel-pi.
        root = _FakeRoot(sw=1280, sh=800, x=0, y=62, w=1280, h=738)
        self.assertFalse(window_covers_display(root))

    def test_apply_sets_origin_and_fullscreen(self) -> None:
        from pigeon.linux_kiosk import apply_kiosk_fullscreen

        root = MagicMock()
        root.winfo_screenwidth.return_value = 1280
        root.winfo_screenheight.return_value = 800
        apply_kiosk_fullscreen(root)
        root.overrideredirect.assert_called_with(True)
        root.geometry.assert_called_with("1280x800+0+0")
        root.attributes.assert_any_call("-fullscreen", True)

    def test_apply_can_defer_borderless_until_mapped(self) -> None:
        from pigeon.linux_kiosk import apply_kiosk_fullscreen

        root = MagicMock()
        root.winfo_screenwidth.return_value = 1280
        root.winfo_screenheight.return_value = 800
        apply_kiosk_fullscreen(root, borderless=False)
        root.overrideredirect.assert_not_called()
        root.geometry.assert_called_with("1280x800+0+0")

    def test_merge_stopped_pids_dedupes(self) -> None:
        self.assertEqual(merge_stopped_pids([1, 2], [2, 3]), [1, 2, 3])

    def test_blank_xcursor_has_magic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "left_ptr"
            write_blank_xcursor(path, size=8)
            data = path.read_bytes()
            self.assertTrue(data.startswith(b"Xcur"))
            self.assertGreater(len(data), 64)

    def test_enforce_kiosk_hides_pointer_and_merges_pids(self) -> None:
        from pigeon import linux_kiosk

        root = MagicMock()
        root.winfo_screenwidth.return_value = 1280
        root.winfo_screenheight.return_value = 800
        root.winfo_rootx.return_value = 0
        root.winfo_rooty.return_value = 0
        root.winfo_width.return_value = 1280
        root.winfo_height.return_value = 800
        with (
            patch.object(linux_kiosk, "install_user_kiosk_session"),
            patch.object(linux_kiosk, "hide_desktop_chrome", return_value=[42]),
            patch.object(linux_kiosk, "suppress_os_overlays", return_value=[]),
            patch.object(linux_kiosk, "hide_pointer") as hide_ptr,
        ):
            stopped = linux_kiosk.enforce_kiosk(root, [7], borderless=True)
        self.assertEqual(stopped, [7, 42])
        hide_ptr.assert_called()

    def test_kiosk_disabled_off_linux(self) -> None:
        from pigeon.linux_kiosk import linux_kiosk_enabled

        with patch.object(sys, "platform", "darwin"):
            self.assertFalse(linux_kiosk_enabled())


if __name__ == "__main__":
    unittest.main()
