"""Background LAN prefetch and faster cached device searches."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)


class BoxScanCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        from pigeon.widgets.main_settings import MainSettingsWidget

        assets = Path(__file__).resolve().parents[2] / "pigeonAssets"
        self.widget = MainSettingsWidget(assets_dir=assets)
        self.widget._periodic_prefetch_started = True
        self.widget._state.selected_wifi_ssid = "TESTNET"

    def _seed(self, box_num: int, *, age_s: float = 0.0) -> None:
        name = "APPLETV" if box_num == 2 else "DENON"
        ip = "10.0.0.8" if box_num == 2 else "10.0.0.9"
        self.widget._box_scan_cache[box_num] = (
            ((name, ip),),
            ({"name": name, "address": ip},),
            time.monotonic() - age_s,
        )

    def test_cache_hit_finishes_after_short_hold(self) -> None:
        self._seed(2)
        self.widget._start_box_device_scan(2)
        panel = self.widget.state.box2_devices
        self.assertTrue(panel.scanning)
        self.assertLess(panel.scan_duration_s, 3.0)
        panel.scan_started_mono = time.monotonic() - 1.3
        self.widget.tick()
        self.assertFalse(panel.scanning)
        self.assertEqual(panel.phase, "results")
        self.assertEqual(panel.devices[0][0], "APPLETV")

    def test_empty_live_scan_holds_until_timeout(self) -> None:
        self.widget._start_box_device_scan(2)
        panel = self.widget.state.box2_devices
        self.widget._box_scan_result[2] = ((), ())
        panel.scan_started_mono = time.monotonic() - 2.0
        self.widget.tick()
        self.assertTrue(panel.scanning)
        self.assertEqual(panel.phase, "scanning")

    def test_prefetch_skipped_when_cache_is_fresh(self) -> None:
        self._seed(2)
        self._seed(3)
        called = []
        self.widget._prefetch_boxes_shared = lambda: called.append(1)  # type: ignore[method-assign]
        self.widget._maybe_refresh_box_scan_cache()
        self.assertEqual(called, [])

    def test_prefetch_runs_when_cache_is_stale(self) -> None:
        self._seed(2, age_s=91.0)
        self._seed(3, age_s=91.0)
        called = []
        self.widget._prefetch_boxes_shared = lambda: called.append(1)  # type: ignore[method-assign]
        self.widget._maybe_refresh_box_scan_cache()
        self.assertEqual(called, [1])

    def test_store_result_feeds_pending_user_scan(self) -> None:
        self.widget._box_scan_pending.add(2)
        self.widget._store_box_scan_result(
            2,
            ((("SKYNET", "10.0.0.1"),), ({"name": "SKYNET", "address": "10.0.0.1"},)),
        )
        cached = self.widget._cached_box_scan(2)
        self.assertIsNotNone(cached)
        self.assertEqual(cached[0][0][0], "SKYNET")
        self.assertEqual(self.widget._box_scan_result[2][0][0][0], "SKYNET")


if __name__ == "__main__":
    unittest.main()
