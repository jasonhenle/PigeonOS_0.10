"""Splash PNG discovery should ignore macOS AppleDouble sidecars."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.splash_sequence import (  # noqa: E402
    SPLASH_CLOCK_REVEAL_FRAME,
    SPLASH_SEQUENCE_DIRNAME,
    list_splash_png_paths,
    splash_keep_alpha_for_live_clock,
)


class SplashPngDiscoveryTests(unittest.TestCase):
    def test_skips_appledouble_dotfiles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / SPLASH_SEQUENCE_DIRNAME
            folder.mkdir()
            real = folder / "widget_pigeon_splash_00000.png"
            junk = folder / "._widget_pigeon_splash_00000.png"
            real.write_bytes(b"\x89PNG\r\n\x1a\n")
            junk.write_bytes(b"\x00" * 163)
            found = list_splash_png_paths(root)
            self.assertEqual([p.name for p in found], [real.name])

    def test_empty_folder_returns_no_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / SPLASH_SEQUENCE_DIRNAME).mkdir()
            self.assertEqual(list_splash_png_paths(root), [])


class SplashLiveClockRevealTests(unittest.TestCase):
    def test_pre_reveal_frames_flatten_over_black(self) -> None:
        self.assertFalse(splash_keep_alpha_for_live_clock(0))
        self.assertFalse(splash_keep_alpha_for_live_clock(SPLASH_CLOCK_REVEAL_FRAME - 1))

    def test_reveal_frames_keep_alpha_for_live_clock(self) -> None:
        self.assertTrue(splash_keep_alpha_for_live_clock(SPLASH_CLOCK_REVEAL_FRAME))
        self.assertTrue(splash_keep_alpha_for_live_clock(SPLASH_CLOCK_REVEAL_FRAME + 12))
        self.assertTrue(splash_keep_alpha_for_live_clock(40, reveal_frame=40))


if __name__ == "__main__":
    unittest.main()
