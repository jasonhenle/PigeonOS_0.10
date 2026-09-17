"""Paused-screen label plate sits near the bottom with rounded corners."""

from __future__ import annotations

import os
import sys
import unittest

import numpy as np
from PIL import Image, ImageFont

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.paused_screen import (  # noqa: E402
    PAUSED_SCREEN_BAR_BOTTOM_PX,
    PAUSED_SCREEN_TEXT,
    paint_paused_screen_label,
    paused_screen_bar_rect,
)


class PausedScreenLabelTests(unittest.TestCase):
    def test_bar_sits_fifty_pixels_above_the_frame_bottom(self) -> None:
        x, y, w, h, radius = paused_screen_bar_rect(1280, 800, 240, 80)
        self.assertEqual(y + h, 800 - PAUSED_SCREEN_BAR_BOTTOM_PX)
        self.assertGreater(x, 0)
        self.assertLess(x + w, 1280)
        self.assertGreater(radius, 12)
        self.assertGreater(y, 800 // 2)

    def test_label_paints_rounded_black_plate_near_bottom(self) -> None:
        img = Image.new("RGB", (1280, 800), (90, 90, 90))
        font = ImageFont.load_default()
        x, y, w, h = paint_paused_screen_label(
            img, text=PAUSED_SCREEN_TEXT, font=font
        )
        arr = np.asarray(img)
        self.assertEqual(y + h, 800 - PAUSED_SCREEN_BAR_BOTTOM_PX)
        cx = x + w // 2
        cy = y + h // 2
        # Plate interior is black; AABB corners stay the gray backdrop.
        self.assertLess(int(arr[cy, cx].max()), 12)
        self.assertGreater(int(arr[y, x].max()), 40)
        self.assertGreater(int(arr[y, x + w - 1].max()), 40)
        self.assertGreater(int(arr[y + h - 1, x].max()), 40)
        self.assertGreater(int(arr[y + h - 1, x + w - 1].max()), 40)
        # Fifty-pixel gap under the plate stays backdrop.
        self.assertGreater(int(arr[799, cx].max()), 40)
        self.assertGreater(int(arr[800 - PAUSED_SCREEN_BAR_BOTTOM_PX + 8, cx].max()), 40)
        # White label ink sits inside the plate.
        white = arr.max(axis=2) > 200
        self.assertGreater(int(white.sum()), 10)
        ys, xs = np.where(white)
        self.assertGreaterEqual(int(ys.min()), y)
        self.assertLess(int(ys.max()), y + h)
        self.assertGreaterEqual(int(xs.min()), x)
        self.assertLess(int(xs.max()), x + w)


class PausedScreenCoverTests(unittest.TestCase):
    def test_square_album_art_fills_wide_frame(self) -> None:
        from pigeon.paused_screen import cover_scale_and_crop

        art = np.zeros((100, 100, 3), dtype=np.uint8)
        art[:, :] = (18, 40, 220)
        out = cover_scale_and_crop(art, 1280, 800)
        self.assertEqual(out.shape, (800, 1280, 3))
        # Cover-crop, not pillarbox: left, right, top, and bottom are album pixels.
        self.assertGreater(int(out[:, 2, 2].min()), 180)
        self.assertGreater(int(out[:, 1277, 2].min()), 180)
        self.assertGreater(int(out[2, :, 2].min()), 180)
        self.assertGreater(int(out[797, :, 2].min()), 180)
