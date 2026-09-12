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
    forget_heos_player,
    heos_player_id,
    pick_heos_player_for_host,
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

    def test_telnet_standby_wins_over_http_on(self) -> None:
        self.assertTrue(
            _denon_power_is_standby({"Power": "ON", "PW": "STANDBY"})
        )


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

    def test_parser_still_reads_last_mv_in_standby(self) -> None:
        # HTTP/telnet keep reporting last-MV while the AVR is off. The volume
        # disc still shows that string; source/format stay hidden.
        self.assertEqual(
            _denon_volume_line({"PW": "STANDBY", "MV_DB": "-30.5 dB", "MU": "OFF"}),
            "-30.5 dB",
        )


class DenonHttpCommandTests(unittest.TestCase):
    def test_appdirect_treats_empty_200_as_success(self) -> None:
        from unittest.mock import MagicMock, patch

        from pigeon.receiver_denon import send_denon_http_command

        resp = MagicMock()
        resp.status = 200
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        with patch(
            "pigeon.receiver_denon.urllib.request.urlopen", return_value=resp
        ) as opener:
            ok, msg = send_denon_http_command("10.0.7.116", "MVUP")
        self.assertTrue(ok)
        self.assertIn("MVUP", msg)
        url = opener.call_args[0][0].full_url
        self.assertIn(":8080/goform/formiPhoneAppDirect.xml?MVUP", url)

    def test_volume_control_maps_actions(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import send_denon_volume_control

        with patch(
            "pigeon.receiver_denon.apply_denon_master_volume",
            return_value=(True, "Denon: MVDOWN → MV665", "-13.5 dB"),
        ) as apply:
            ok, msg = send_denon_volume_control("10.0.7.116", "volume_down")
        self.assertTrue(ok)
        self.assertEqual(apply.call_args.kwargs["steps"], -1)
        self.assertIn("MVDOWN", msg)

    def test_control_requires_telnet_ack(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import send_denon_control_command

        with patch(
            "pigeon.receiver_denon.send_denon_http_command",
            return_value=(True, "Denon HTTP: PWON"),
        ) as http, patch(
            "pigeon.receiver_denon_telnet.send_denon_telnet_command",
            return_value=(True, "Denon: PWON → PWON"),
        ) as telnet:
            ok, msg = send_denon_control_command("10.0.7.116", "PWON")
        self.assertTrue(ok)
        telnet.assert_called_once()
        http.assert_called_once()
        self.assertIn("PWON", msg)

    def test_coalesce_knob_burst(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_actions

        self.assertEqual(
            coalesce_receiver_volume_actions(
                ["volume_up", "volume_up", "volume_down", "mute_toggle"]
            ),
            (1, 1),
        )

    def test_apply_volume_sends_mvup_when_already_on(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import apply_denon_master_volume

        with patch(
            "pigeon.receiver_denon.read_denon_appcommand_status",
            side_effect=[
                {"Power": "ON", "MasterVolume": "-13.5", "Mute": "off"},
                {"Power": "ON", "MasterVolume": "-13.0", "Mute": "off"},
            ],
        ), patch(
            "pigeon.receiver_denon.read_heos_volume",
            return_value={},
        ), patch(
            "pigeon.receiver_denon_telnet.query_denon_volume_telnet",
            return_value={},
        ), patch(
            "pigeon.receiver_denon_telnet.send_denon_telnet_commands",
            return_value=(True, "Denon: MVUP → MV67"),
        ) as send:
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        self.assertEqual(send.call_args[0][1], ["MVUP"])
        self.assertEqual(vol, "-13.0 dB")
        self.assertIn("MVUP", msg)

    def test_apply_volume_sends_mv_when_http_says_off(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import apply_denon_master_volume

        with patch(
            "pigeon.receiver_denon.read_denon_appcommand_status",
            side_effect=[
                {"Power": "OFF", "MasterVolume": "-28.0", "Mute": "off"},
                {"Power": "ON", "MasterVolume": "-27.5", "Mute": "off"},
            ],
        ), patch(
            "pigeon.receiver_denon.read_heos_volume",
            return_value={},
        ), patch(
            "pigeon.receiver_denon_telnet.query_denon_volume_telnet",
            return_value={},
        ), patch(
            "pigeon.receiver_denon.send_denon_http_command",
            return_value=(True, "Denon HTTP: PWON"),
        ), patch(
            "pigeon.receiver_denon_telnet.send_denon_telnet_commands",
            return_value=(True, "Denon: MVUP → MV525"),
        ) as send:
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        self.assertEqual(send.call_args[0][1], ["MVUP"])
        self.assertEqual(vol, "-27.5 dB")
        self.assertIn("MVUP", msg)

    def test_apply_falls_back_to_heos(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import apply_denon_master_volume

        with patch(
            "pigeon.receiver_denon.read_denon_appcommand_status",
            side_effect=[
                {"Power": "ON", "MasterVolume": "-13.0", "Mute": "off"},
                {"Power": "ON", "MasterVolume": "-12.5", "Mute": "off"},
            ],
        ), patch(
            "pigeon.receiver_denon_telnet.query_denon_volume_telnet",
            return_value={},
        ), patch(
            "pigeon.receiver_denon_telnet.send_denon_telnet_commands",
            return_value=(False, "Denon: MVUP sent, no reply"),
        ), patch(
            "pigeon.receiver_denon.send_heos_volume_control",
            return_value=(True, "HEOS: volume_up"),
        ) as heos, patch(
            "pigeon.receiver_denon.read_heos_volume",
            return_value={"pid": 222, "level": 67, "muted": False},
        ):
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        heos.assert_called_once()
        self.assertEqual(vol, "-13.0 dB")
        self.assertIn("HEOS", msg)

    def test_apply_heos_when_telnet_echoes_same_mv(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import apply_denon_master_volume

        stuck = {"Power": "ON", "MasterVolume": "-27.5", "Mute": "off"}
        with patch(
            "pigeon.receiver_denon.read_denon_appcommand_status",
            return_value=stuck,
        ), patch(
            "pigeon.receiver_denon_telnet.query_denon_volume_telnet",
            return_value={"MV": "525", "MV_DB": "-27.5 dB", "MU": "OFF"},
        ), patch(
            "pigeon.receiver_denon_telnet.send_denon_telnet_commands",
            return_value=(True, "Denon: MVUP → MV525"),
        ), patch(
            "pigeon.receiver_denon.send_heos_volume_control",
            return_value=(True, "HEOS: volume_up"),
        ) as heos, patch(
            "pigeon.receiver_denon.read_heos_volume",
            return_value={"level": 26, "muted": False},
        ):
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        heos.assert_called_once()
        self.assertEqual(vol, "-54.0 dB")
        self.assertIn("HEOS", msg)

    def test_same_db_two_and_three_digit_mv_is_not_a_move(self) -> None:
        from pigeon.receiver_denon import _volume_lines_moved

        self.assertFalse(_volume_lines_moved("-27.5 dB", "-27.5 dB", steps=1))
        self.assertFalse(_volume_lines_moved("-27.5 dB", "-27.0 dB", steps=-1))
        self.assertTrue(_volume_lines_moved("-13.5 dB", "-13.0 dB", steps=1))
        self.assertTrue(_volume_lines_moved("-27.5 dB", "-28.0 dB", steps=-1))
        self.assertTrue(_volume_lines_moved("-27.5 dB", "mute"))

    def test_heos_level_maps_to_denon_db(self) -> None:
        from pigeon.receiver_denon import heos_level_to_db

        self.assertEqual(heos_level_to_db(0), "-80.0 dB")
        self.assertEqual(heos_level_to_db(52), "-28.0 dB")
        self.assertEqual(heos_level_to_db(80), "0.0 dB")

    def test_heos_uses_set_volume_not_five_percent_steps(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import send_heos_volume_control

        calls: list[str] = []

        def _ex(_host: str, command: str, timeout: float = 1.5) -> dict:
            calls.append(command)
            return {"heos": {"result": "success", "message": ""}}

        with patch(
            "pigeon.receiver_denon.heos_player_id", return_value=222
        ), patch(
            "pigeon.receiver_denon.read_heos_volume",
            return_value={"pid": 222, "level": 25, "muted": False},
        ), patch(
            "pigeon.receiver_denon._heos_exchange", side_effect=_ex
        ):
            ok, msg = send_heos_volume_control("10.0.7.116", steps=1)
        self.assertTrue(ok)
        self.assertEqual(calls, ["heos://player/set_volume?pid=222&level=26"])
        self.assertIn("set_volume", msg)


class HeosHostBindTests(unittest.TestCase):
    def tearDown(self) -> None:
        forget_heos_player("10.0.7.116")
        forget_heos_player("10.0.8.10")

    def test_picks_player_at_this_ip_not_first_or_model(self) -> None:
        row = pick_heos_player_for_host(
            "10.0.7.116",
            [
                {
                    "name": "Denon AVR-X3800H",
                    "pid": 111,
                    "ip": "10.0.8.10",
                    "model": "Denon AVR-X3800H",
                },
                {
                    "name": "Family Room",
                    "pid": 222,
                    "ip": "10.0.7.116",
                    "model": "Denon AVR-S670H",
                    "lineout": 0,
                },
            ],
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row["pid"]), 222)

    def test_refuses_when_no_player_has_this_ip(self) -> None:
        self.assertIsNone(
            pick_heos_player_for_host(
                "10.0.7.116",
                [
                    {
                        "name": "Denon AVR-X3800H",
                        "pid": 111,
                        "ip": "10.0.8.10",
                    }
                ],
            )
        )

    def test_heos_player_id_ignores_first_row_model(self) -> None:
        from unittest.mock import patch

        payload = {
            "heos": {"result": "success"},
            "payload": [
                {"name": "Denon AVR-X3800H", "pid": 111, "ip": "10.0.8.10"},
                {"name": "Denon AVR-S670H", "pid": 222, "ip": "10.0.7.116"},
            ],
        }
        with patch(
            "pigeon.receiver_denon._heos_exchange", return_value=payload
        ):
            self.assertEqual(heos_player_id("10.0.7.116"), 222)
            self.assertIsNone(heos_player_id("10.0.9.1"))


class DenonTelnetAckTests(unittest.TestCase):
    def test_ack_matcher(self) -> None:
        from pigeon.receiver_denon_telnet import _telnet_ack_matches

        self.assertTrue(_telnet_ack_matches("MVUP", "MV67"))
        self.assertTrue(_telnet_ack_matches("MVUP", "MV665"))
        self.assertFalse(_telnet_ack_matches("MVUP", "MVMAX 915"))
        self.assertTrue(_telnet_ack_matches("PWON", "PWON"))
        self.assertFalse(_telnet_ack_matches("MVUP", "PWON"))

    def test_send_waits_for_mv_reply(self) -> None:
        import socket
        import threading
        import time

        from pigeon.receiver_denon_telnet import send_denon_telnet_commands

        srv = socket.socket()
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        seen: list[bytes] = []

        def serve() -> None:
            conn, _addr = srv.accept()
            try:
                conn.sendall(b"PWON\r")
                end = time.monotonic() + 2.0
                buf = b""
                while time.monotonic() < end and b"MVUP\r" not in buf:
                    conn.settimeout(0.2)
                    try:
                        buf += conn.recv(64)
                    except socket.timeout:
                        continue
                seen.append(buf)
                conn.sendall(b"MV67\r")
            finally:
                conn.close()
                srv.close()

        threading.Thread(target=serve, daemon=True).start()
        ok, msg = send_denon_telnet_commands(
            "127.0.0.1", ["MVUP"], port=port, timeout=2.0
        )
        self.assertTrue(ok, msg)
        self.assertIn("MV67", msg)
        self.assertTrue(any(b"MVUP" in blob for blob in seen))
