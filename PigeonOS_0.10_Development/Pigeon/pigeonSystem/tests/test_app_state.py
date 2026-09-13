"""App state read/write must not wipe pairings on a corrupt JSON file."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)


class AppStateRecoverTests(unittest.TestCase):
    def test_read_recovers_trailing_junk(self) -> None:
        from pigeon import app_state

        payload = {
            "saved_streaming_device": {
                "identifier": "AA",
                "address": "10.0.4.57",
                "name": "Family room",
                "label": "Family room",
            }
        }
        text = json.dumps(payload, indent=2) + "\n\n}\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(text, encoding="utf-8")
            with mock.patch.object(app_state, "state_file", return_value=path):
                app_state._STATE_TEXT_CACHE = None
                got = app_state.read_app_state()
        self.assertEqual(got["saved_streaming_device"]["address"], "10.0.4.57")

    def test_write_does_not_wipe_unreadable_sizable_file(self) -> None:
        from pigeon import app_state

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{not json at all " + ("x" * 200), encoding="utf-8")
            before = path.read_text(encoding="utf-8")
            with mock.patch.object(app_state, "state_file", return_value=path):
                app_state._STATE_TEXT_CACHE = None
                app_state.write_app_state(device_slots_v1_migrated=True)
            self.assertTrue(path.exists() or (path.with_suffix(".bad")).exists())
            leftover = path.read_text(encoding="utf-8") if path.exists() else ""
            bad = (
                path.with_suffix(".bad").read_text(encoding="utf-8")
                if path.with_suffix(".bad").exists()
                else ""
            )
            self.assertTrue(before in leftover or before in bad)
            if path.exists():
                self.assertNotIn("device_slots_v1_migrated", leftover)


class AvReceiverWriteTests(unittest.TestCase):
    def test_write_replaces_x3800_and_syncs_last_receiver(self) -> None:
        from pigeon import app_state

        loc_id = "f794d050753e4ee5acd3c79510bb16b5"
        old = {
            "identifier": "00:06:78:C9:13:61",
            "address": "10.0.7.116",
            "name": "Denon",
            "label": "Denon — 10.0.7.116",
            "looks_like_apple_tv": "false",
        }
        payload = {
            "device_slots_v1_migrated": True,
            "current_location_id": loc_id,
            "locations_v2": [
                {
                    "id": loc_id,
                    "name": "Room",
                    "streaming": [],
                    "av_receiver": [old],
                    "tv": [],
                    "projector": [],
                    "game": [],
                    "other": [],
                }
            ],
            "saved_av_receiver": old,
            "last_receiver": {"host": "10.0.7.116", "name": "Denon"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with mock.patch.object(app_state, "state_file", return_value=path):
                app_state._STATE_TEXT_CACHE = None
                app_state.write_saved_av_receiver(
                    {
                        "name": "Living room",
                        "label": "Living room",
                        "address": "10.0.4.88",
                    },
                    for_location_id=loc_id,
                )
                got = app_state.read_saved_av_receiver()
                last = app_state.read_last_receiver()
        self.assertIsNotNone(got)
        assert got is not None
        self.assertEqual(got["address"], "10.0.4.88")
        self.assertEqual(last.get("host"), "10.0.4.88")
        self.assertNotEqual(got["address"], "10.0.7.116")


if __name__ == "__main__":
    unittest.main()

