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

    def test_s670h_appcommand_off_loses_to_telnet_on(self) -> None:
        self.assertFalse(
            _denon_power_is_standby(
                {"Power": "OFF", "PW": "ON", "MasterVolume": "-18.5"}
            )
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

    def test_appcommand_relative_db_is_master_volume(self) -> None:
        self.assertEqual(
            _denon_volume_line({"MasterVolume": "-18.5", "Mute": "off"}),
            "-18.5 dB",
        )
        self.assertEqual(
            _denon_volume_line({"MasterVolume": "-24.0", "Mute": "off"}),
            "-24.0 dB",
        )

    def test_parse_get_volume_level_xml(self) -> None:
        from pigeon.receiver_denon import _parse_appcommand_rx

        xml = """<?xml version="1.0" encoding="utf-8" ?>
<rx>
<cmd><zone1>ON</zone1><zone2>ON</zone2></cmd>
<cmd>
<volume>-18.5</volume>
<state>variable</state>
<disptype>RELATIVE</disptype>
<dispvalue>-18.5dB</dispvalue>
</cmd>
<cmd><mute>off</mute></cmd>
</rx>
"""
        parsed = _parse_appcommand_rx(xml)
        self.assertEqual(parsed.get("MasterVolume"), "-18.5")
        self.assertEqual(_denon_volume_line(parsed), "-18.5 dB")

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
        ) as heos:
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        heos.assert_called_once()
        self.assertEqual(vol, "-12.5 dB")
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
        ) as heos:
            ok, msg, vol = apply_denon_master_volume("10.0.7.116", steps=1)
        self.assertTrue(ok)
        heos.assert_called_once()
        # HEOS player level is not AVR master — keep the telnet / AppCommand line.
        self.assertEqual(vol, "-27.5 dB")
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


class DenonTelnetHubTests(unittest.TestCase):
    def tearDown(self) -> None:
        from pigeon.receiver_denon_telnet import stop_denon_telnet_hub

        stop_denon_telnet_hub()

    def test_query_reads_hub_snapshot_without_connecting(self) -> None:
        from pigeon.receiver_denon_telnet import (
            prime_denon_telnet_hub_snapshot_for_tests,
            query_denon_volume_telnet,
        )

        prime_denon_telnet_hub_snapshot_for_tests(
            "10.0.4.64",
            {"PW": "ON", "MV": "575", "MV_DB": "-22.5 dB", "MU": "OFF"},
        )
        row = query_denon_volume_telnet("10.0.4.64", blocking=False)
        self.assertEqual(row.get("MV_DB"), "-22.5 dB")
        self.assertEqual(row.get("PW"), "ON")

    def test_ingest_unsolicited_mv_updates_last_volume(self) -> None:
        from pigeon.receiver_denon_telnet import telnet_hub_ingest_for_tests

        self.assertIn("dB", telnet_hub_ingest_for_tests(b"MV575\rMUOFF\r"))


class ReceiverVolumeCoalesceTests(unittest.TestCase):
    def test_telnet_wins_over_frozen_http(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="-16.0 dB",
            http_line="-18.5 dB",
            last_http="-18.5 dB",
            held="-18.5 dB",
        )
        self.assertEqual(line, "-16.0 dB")
        self.assertEqual(src, "telnet")

    def test_unchanged_http_keeps_held_telnet(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="",
            http_line="-18.5 dB",
            last_http="-18.5 dB",
            held="-16.0 dB",
        )
        self.assertEqual(line, "-16.0 dB")
        self.assertEqual(src, "hold")

    def test_http_wins_when_it_moves(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="",
            http_line="-22.0 dB",
            last_http="-18.5 dB",
            held="-16.0 dB",
        )
        self.assertEqual(line, "-22.0 dB")
        self.assertEqual(src, "appcommand")

    def test_stale_hub_does_not_block_live_http(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="-18.5 dB",
            http_line="-20.0 dB",
            last_http="-19.5 dB",
            last_telnet="-18.5 dB",
            held="-18.5 dB",
        )
        self.assertEqual(line, "-20.0 dB")
        self.assertEqual(src, "appcommand")

    def test_disagreeing_unchanged_sources_prefer_http(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="-18.5 dB",
            http_line="-20.0 dB",
            last_http="-20.0 dB",
            last_telnet="-18.5 dB",
            held="-18.5 dB",
        )
        self.assertEqual(line, "-20.0 dB")
        self.assertEqual(src, "appcommand")

    def test_recent_telnet_change_beats_older_http(self) -> None:
        from pigeon.receiver_denon import coalesce_receiver_volume_read

        line, src = coalesce_receiver_volume_read(
            telnet_line="-16.0 dB",
            http_line="-18.5 dB",
            last_http="-18.5 dB",
            last_telnet="-16.0 dB",
            held="-16.0 dB",
            last_http_mono=10.0,
            last_telnet_mono=20.0,
        )
        self.assertEqual(line, "-16.0 dB")
        self.assertEqual(src, "telnet")

    def test_http_off_plus_telnet_on_is_not_standby(self) -> None:
        from pigeon.receiver_denon import _denon_power_is_standby

        self.assertFalse(
            _denon_power_is_standby({"Power": "OFF", "PW": "ON", "MV_DB": "-18.5 dB"})
        )

    def test_http_only_poll_merges_short_telnet_volume(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import poll_denon_like_receiver

        http = {
            "Power": "OFF",
            "MasterVolume": "-18.5",
            "Mute": "off",
            "FriendlyName": "Denon AVR-S670H",
        }
        tn = {"PW": "ON", "MV": "575", "MV_DB": "-22.5 dB", "MU": "OFF"}
        with patch(
            "pigeon.receiver_denon._merge_zone_status_with_fallback",
            return_value=http,
        ), patch(
            "pigeon.receiver_denon.query_denon_volume_telnet",
            return_value=tn,
        ):
            r = poll_denon_like_receiver("10.0.4.64", timeout=2.0, include_telnet=False)
        self.assertTrue(r.ok)
        self.assertFalse(r.standby)
        self.assertEqual(r.volume, "-22.5 dB")


class ReceiverIdentityTests(unittest.TestCase):
    def test_model_key_ignores_brand_only_names(self) -> None:
        from pigeon.receiver_denon import receiver_model_key

        self.assertEqual(receiver_model_key("Denon AVR-S670H"), "s670h")
        self.assertEqual(receiver_model_key("AVR-X3800H"), "x3800h")
        self.assertEqual(receiver_model_key("Denon"), "")

    def test_identities_match_model_not_brand(self) -> None:
        from pigeon.receiver_denon import receiver_identities_match

        paired = {
            "name": "Denon AVR-S670H",
            "identifier": "00:06:78:E5:3D:66",
            "address": "10.0.4.64",
        }
        self.assertTrue(
            receiver_identities_match(
                paired, name="Family Room", model="Denon AVR-S670H"
            )
        )
        self.assertFalse(
            receiver_identities_match(
                paired, name="Denon AVR-X3800H", model="Denon AVR-X3800H"
            )
        )
        self.assertTrue(
            receiver_identities_match(
                paired, name="Denon", device_id="00:06:78:e5:3d:66"
            )
        )

    def test_slash22_scan_includes_other_octets(self) -> None:
        import ipaddress

        from pigeon.receiver_denon import ipv4_hosts_for_networks

        hosts = ipv4_hosts_for_networks(
            [ipaddress.IPv4Network("10.0.4.0/22")], max_hosts=2048
        )
        self.assertIn("10.0.4.64", hosts)
        self.assertIn("10.0.7.116", hosts)
        self.assertNotIn("10.0.4.0", hosts)

    def test_resolve_uses_heos_directory_ip_for_paired_model(self) -> None:
        from unittest.mock import patch

        from pigeon.receiver_denon import resolve_paired_receiver_host

        paired = {
            "name": "Denon AVR-S670H",
            "identifier": "00:06:78:E5:3D:66",
            "address": "10.0.4.64",
        }
        heos_rows = [
            {
                "name": "Denon AVR-X3800H",
                "model": "Denon AVR-X3800H",
                "ip": "10.0.7.116",
                "pid": 1,
            },
            {
                "name": "Denon AVR-S670H",
                "model": "Denon AVR-S670H",
                "ip": "10.0.6.20",
                "pid": 2,
            },
        ]
        with patch(
            "pigeon.receiver_denon._probe_host_for_receiver",
            side_effect=lambda host, timeout: (
                {"host": host, "name": "Denon AVR-S670H"}
                if str(host).startswith("10.0.6.20")
                else None
            ),
        ), patch(
            "pigeon.receiver_denon._ssdp_collect_probe_hints",
            return_value=[("10.0.7.116", None)],
        ), patch(
            "pigeon.receiver_denon.heos_players_from_hosts",
            return_value=heos_rows,
        ):
            self.assertEqual(
                resolve_paired_receiver_host(paired, extra_hosts=["10.0.4.64"]),
                "10.0.6.20",
            )


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
