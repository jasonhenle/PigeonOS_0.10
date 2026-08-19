"""Unit tests for rotary cross-transport dedupe, ADB forwarding, and backoff."""

from __future__ import annotations

import os
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

# pigeonSystem is the import root used on device.
_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon import rotary_serial as rs  # noqa: E402


class ActionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = rs._ActionGate(window_s=0.04)

    def test_same_source_rapid_both_accepted(self) -> None:
        self.assertTrue(self.gate.accept("forward", "usb", when=1.000))
        self.assertTrue(self.gate.accept("forward", "usb", when=1.020))

    def test_cross_transport_within_window_rejects_second(self) -> None:
        self.assertTrue(self.gate.accept("forward", "usb", when=1.000))
        self.assertFalse(self.gate.accept("forward", "tcp", when=1.020))

    def test_cross_transport_outside_window_both_accepted(self) -> None:
        self.assertTrue(self.gate.accept("forward", "usb", when=1.000))
        self.assertTrue(self.gate.accept("forward", "tcp", when=1.050))

    def test_different_actions_close_together_both_accepted(self) -> None:
        self.assertTrue(self.gate.accept("forward", "usb", when=1.000))
        self.assertTrue(self.gate.accept("backward", "tcp", when=1.010))

    def test_triple_same_source_all_accepted(self) -> None:
        self.assertTrue(self.gate.accept("forward", "usb", when=1.000))
        self.assertTrue(self.gate.accept("forward", "usb", when=1.015))
        self.assertTrue(self.gate.accept("forward", "usb", when=1.030))


class AdbParseSelectTests(unittest.TestCase):
    def test_parse_ignores_unauthorized_and_offline(self) -> None:
        out = (
            "List of devices attached\n"
            "ABC123\tdevice\n"
            "BAD\tunauthorized\n"
            "OFF\toffline\n"
            "NOP\tno permissions\n"
        )
        self.assertEqual(rs._parse_adb_devices(out), ["ABC123"])

    def test_select_single_device(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIGEON_ADB_SERIAL", None)
            self.assertEqual(rs._select_adb_serial(["UNOQ1"]), "UNOQ1")

    def test_select_multiple_without_override_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIGEON_ADB_SERIAL", None)
            with mock.patch.object(rs, "_stderr") as err:
                self.assertIsNone(rs._select_adb_serial(["A", "B"]))
            err.assert_called()
            self.assertIn("PIGEON_ADB_SERIAL", err.call_args[0][0])

    def test_select_multiple_with_valid_override(self) -> None:
        with mock.patch.dict(os.environ, {"PIGEON_ADB_SERIAL": "B"}):
            self.assertEqual(rs._select_adb_serial(["A", "B"]), "B")

    def test_select_override_missing_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {"PIGEON_ADB_SERIAL": "Z"}):
            with mock.patch.object(rs, "_stderr"):
                self.assertIsNone(rs._select_adb_serial(["A", "B"]))


class AdbForwardTests(unittest.TestCase):
    def _devices_ok(self, stdout: str) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    def test_forward_success_uses_serial_and_logs(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if "devices" in cmd:
                return self._devices_ok(
                    "List of devices attached\nSERIAL1\tdevice\n"
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(rs, "_adb_bin", return_value="/usr/bin/adb"),
            mock.patch.object(rs.subprocess, "run", side_effect=fake_run),
            mock.patch.object(rs, "_stderr") as err,
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("PIGEON_ADB_SERIAL", None)
            self.assertTrue(rs._ensure_adb_forward(7500))
        self.assertEqual(
            calls[1],
            ["/usr/bin/adb", "-s", "SERIAL1", "forward", "tcp:7500", "tcp:7500"],
        )
        self.assertTrue(
            any("adb forward tcp:7500" in c[0][0] for c in err.call_args_list)
        )

    def test_forward_failure_does_not_claim_success(self) -> None:
        def fake_run(cmd, **_kwargs):
            if "devices" in cmd:
                return self._devices_ok(
                    "List of devices attached\nSERIAL1\tdevice\n"
                )
            return SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="error: device offline",
            )

        with (
            mock.patch.object(rs, "_adb_bin", return_value="/usr/bin/adb"),
            mock.patch.object(rs.subprocess, "run", side_effect=fake_run),
            mock.patch.object(rs, "_stderr") as err,
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("PIGEON_ADB_SERIAL", None)
            self.assertFalse(rs._ensure_adb_forward(7500))
        joined = " ".join(c[0][0] for c in err.call_args_list)
        self.assertIn("adb forward failed", joined)
        self.assertIn("rc=1", joined)
        self.assertNotIn("adb forward tcp:7500 → device", joined)

    def test_forward_multiple_devices_without_override(self) -> None:
        def fake_run(cmd, **_kwargs):
            return self._devices_ok(
                "List of devices attached\nA\tdevice\nB\tdevice\n"
            )

        with (
            mock.patch.object(rs, "_adb_bin", return_value="/usr/bin/adb"),
            mock.patch.object(rs.subprocess, "run", side_effect=fake_run),
            mock.patch.object(rs, "_stderr") as err,
            mock.patch.dict(os.environ, {}, clear=False),
        ):
            os.environ.pop("PIGEON_ADB_SERIAL", None)
            self.assertFalse(rs._ensure_adb_forward(7500))
        self.assertIn("Multiple ADB devices", err.call_args[0][0])

    def test_forward_multiple_devices_with_override(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if "devices" in cmd:
                return self._devices_ok(
                    "List of devices attached\nA\tdevice\nB\tdevice\n"
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            mock.patch.object(rs, "_adb_bin", return_value="/usr/bin/adb"),
            mock.patch.object(rs.subprocess, "run", side_effect=fake_run),
            mock.patch.object(rs, "_stderr"),
            mock.patch.dict(os.environ, {"PIGEON_ADB_SERIAL": "B"}),
        ):
            self.assertTrue(rs._ensure_adb_forward(7500))
        self.assertEqual(calls[1][2], "B")


class BackoffAndStateLogTests(unittest.TestCase):
    def test_backoff_progression_and_reset(self) -> None:
        bo = rs._RetryBackoff()
        self.assertEqual(bo.bump(), 2.0)
        self.assertEqual(bo.bump(), 5.0)
        self.assertEqual(bo.bump(), 10.0)
        self.assertEqual(bo.bump(), 30.0)
        self.assertEqual(bo.bump(), 30.0)
        bo.reset()
        self.assertEqual(bo.bump(), 2.0)

    def test_state_log_throttles_identical_keys(self) -> None:
        slog = rs._StateLog()
        with mock.patch.object(rs, "_stderr") as err:
            slog.emit("waiting:2", "msg a")
            slog.emit("waiting:2", "msg a again")
            slog.emit("waiting:5", "msg b")
        self.assertEqual(err.call_count, 2)
        self.assertEqual(err.call_args_list[0][0][0], "msg a")
        self.assertEqual(err.call_args_list[1][0][0], "msg b")


class MegaProtocolLineTests(unittest.TestCase):
    def test_mega_encoder_and_button_map(self) -> None:
        self.assertEqual(
            rs._action_for_line("MEGA,ENCODER,NAV,RIGHT"), "forward"
        )
        self.assertEqual(
            rs._action_for_line("MEGA,ENCODER,NAV,LEFT"), "backward"
        )
        self.assertEqual(
            rs._action_for_line("MEGA,BUTTON,NAV,PRESSED"), "activate"
        )

    def test_mega_ready_is_not_an_action(self) -> None:
        self.assertTrue(rs._is_ready_line("MEGA,SYS,READY,1"))
        self.assertIsNone(rs._action_for_line("MEGA,SYS,READY,1"))

    def test_legacy_tokens_still_work(self) -> None:
        self.assertEqual(rs._action_for_line("RIGHT"), "forward")
        self.assertEqual(rs._action_for_line("PUSH"), "activate")


class StartListenerEnvTests(unittest.TestCase):
    def test_disabled_by_env_returns_none(self) -> None:
        with mock.patch.dict(os.environ, {"PIGEON_ROTARY_SERIAL": "0"}):
            self.assertIsNone(rs.start_rotary_serial_listener(object()))

    def test_uno_q_tcp_is_off_unless_enabled(self) -> None:
        with mock.patch.dict(os.environ, {"PIGEON_ROTARY_TCP": ""}, clear=False):
            os.environ.pop("PIGEON_ROTARY_TCP", None)
            self.assertIsNone(rs._env_tcp_endpoint())
        with mock.patch.dict(os.environ, {"PIGEON_ROTARY_TCP": "1"}):
            self.assertEqual(rs._env_tcp_endpoint(), ("127.0.0.1", 7500))


class DispatchReceiveStampTests(unittest.TestCase):
    def test_handle_line_uses_receive_time_not_callback_time(self) -> None:
        gate = rs._ActionGate(window_s=0.04)
        actions: list[str] = []
        scheduled: list = []

        class FakeRoot:
            def after(self, _ms, cb):
                scheduled.append(cb)

        root = FakeRoot()
        logged_ok = [0]
        ignored = [0]
        with mock.patch.object(rs.time, "monotonic", return_value=10.0):
            rs._handle_line(
                root,
                "RIGHT",
                on_action=actions.append,
                invert=False,
                gate=gate,
                logged_ok=logged_ok,
                ignored=ignored,
                source="usb",
            )
            rs._handle_line(
                root,
                "RIGHT",
                on_action=actions.append,
                invert=False,
                gate=gate,
                logged_ok=logged_ok,
                ignored=ignored,
                source="tcp",
            )
        # Simulate late Tk drain well outside the window; gate still uses stamp.
        with mock.patch.object(rs.time, "monotonic", return_value=99.0):
            for cb in scheduled:
                cb()
        self.assertEqual(actions, ["forward"])


if __name__ == "__main__":
    unittest.main()
