"""Metadata inspector result layout: flush player values, clip to the red plate."""

from __future__ import annotations

import os
import sys
import unittest
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.widgets import metadata_debug as md  # noqa: E402


class MetadataResultLayoutTests(unittest.TestCase):
    def test_player_results_sit_in_confidence_column(self) -> None:
        self.assertLess(
            md._result_x_svg(show_confidence=False),
            md._result_x_svg(show_confidence=True),
        )
        self.assertAlmostEqual(
            md._result_x_svg(show_confidence=False),
            md._RESULT_X_FLUSH_SVG,
        )

    def test_long_title_stays_inside_plate(self) -> None:
        title = "Taylor Swift | The Eras Tour (Taylor's Version)"
        x = md._result_x_svg(show_confidence=True)
        fitted = md._fit_result_text(title, x_svg=x)
        self.assertTrue(fitted.endswith("..."))
        self.assertLess(len(fitted), len(title))
        max_w_svg = md._result_max_right_svg() - x
        max_w_px = int(round(max_w_svg * md.DESIGN_W / md._METADATA_VIEWBOX[2]))
        probe = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        self.assertLessEqual(probe.textlength(fitted, font=md._result_font()), max_w_px + 0.5)

    def test_set_translate_x_keeps_baseline(self) -> None:
        el = ET.Element("text")
        el.set("transform", "translate(860.24 721.92)")
        md._set_translate_x(el, md._RESULT_X_FLUSH_SVG)
        self.assertEqual(el.get("transform"), "translate(801.09 721.92)")


if __name__ == "__main__":
    unittest.main()
