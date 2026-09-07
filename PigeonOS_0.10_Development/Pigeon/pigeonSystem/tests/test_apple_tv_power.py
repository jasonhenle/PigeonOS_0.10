"""Apple TV power-off should drop now-playing and show the idle analog clock."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.apple_tv_now_playing import (  # noqa: E402
    ATV_OFF_POLL_FAILS,
    _attach_power_state,
    apple_tv_power_is_off,
    apple_tv_should_show_idle_clock,
)


class AppleTvPowerOffTests(unittest.TestCase):
    def test_power_state_off_variants(self) -> None:
        self.assertTrue(apple_tv_power_is_off({"power_state": "PowerState.Off"}))
        self.assertTrue(apple_tv_power_is_off({"power_state": "Off"}))
        self.assertFalse(apple_tv_power_is_off({"power_state": "PowerState.On"}))
        self.assertFalse(apple_tv_power_is_off({"power_state": "PowerState.Unknown"}))
        self.assertFalse(apple_tv_power_is_off({"power_state": ""}))
        self.assertFalse(apple_tv_power_is_off(None))

    def test_unreachable_polls_count_as_off(self) -> None:
        self.assertFalse(apple_tv_should_show_idle_clock({}, consecutive_fail=2))
        self.assertTrue(
            apple_tv_should_show_idle_clock({}, consecutive_fail=ATV_OFF_POLL_FAILS)
        )
        self.assertTrue(
            apple_tv_should_show_idle_clock(
                {"power_state": "PowerState.Off", "title": "The Crown"},
                consecutive_fail=0,
            )
        )

    def test_attach_reads_pyatv_power_property(self) -> None:
        md: dict[str, object] = {}
        atv = SimpleNamespace(power=SimpleNamespace(power_state="PowerState.Off"))
        _attach_power_state(atv, md)
        self.assertEqual(md.get("power_state"), "PowerState.Off")
        self.assertTrue(apple_tv_power_is_off(md))
