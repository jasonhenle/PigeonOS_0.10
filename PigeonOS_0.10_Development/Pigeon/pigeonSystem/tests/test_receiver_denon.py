"""Denon power / volume parse helpers for the now-playing volume widget."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon.receiver_denon import (  # noqa: E402
    _denon_power_is_standby,
    _denon_volume_line,
)


class DenonPowerStandbyTests(unittest.TestCase):
    def test_http_standby_loses_to_telnet_on(self) -> None:
        self.assertFalse(
            _denon_power_is_standby({"Power": "STANDBY", "PW": "ON"})
        )

    def test_both_off_is_standby(self) -> None:
        self.assertTrue(
            _denon_power_is_standby({"Power": "STANDBY", "PW": "STANDBY"})
        )

    def test_zm_off_does_not_force_standby_when_pw_on(self) -> None:
        self.assertFalse(_denon_power_is_standby({"PW": "ON", "ZM": "OFF"}))

    def test_empty_is_not_standby(self) -> None:
        self.assertFalse(_denon_power_is_standby({}))


class DenonVolumeLineTests(unittest.TestCase):
    def test_mv_db_in_standby(self) -> None:
        self.assertEqual(
            _denon_volume_line({"PW": "STANDBY", "MV_DB": "-58.5 dB", "MU": "OFF"}),
            "-58.5 dB",
        )

    def test_mv_step_converts_to_db(self) -> None:
        # 215 → -58.5 dB (Denon half-step encoding).
        self.assertEqual(_denon_volume_line({"MV": "215", "MU": "OFF"}), "-58.5 dB")

    def test_mute_wins(self) -> None:
        self.assertEqual(
            _denon_volume_line({"MV_DB": "-22.5 dB", "MU": "ON"}),
            "mute",
        )
