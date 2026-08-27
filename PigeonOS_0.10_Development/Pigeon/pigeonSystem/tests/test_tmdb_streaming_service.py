"""Streaming-service TMDb identification (PBS Kids protocol applied to every app)."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon import tmdb_poster as tp  # noqa: E402


class ServiceFirstQueryTests(unittest.TestCase):
    def test_netflix_leads_with_title_plus_service(self) -> None:
        hint = tp.streaming_service_display_name("Netflix", "com.netflix.Netflix")
        self.assertEqual(hint, "Netflix")
        q = tp._service_augmented_search_queries(
            "Bluey",
            service_hint=hint,
            forgiving=False,
            service_first=tp._service_first_enabled(hint),
        )
        self.assertEqual(q[0], "Bluey Netflix")
        self.assertIn("Bluey", q)

    def test_pbs_kids_still_leads_with_service(self) -> None:
        q = tp._service_augmented_search_queries(
            "Secret Tunnels",
            service_hint="PBS Kids",
            forgiving=False,
            service_first=True,
        )
        self.assertEqual(q[0], "Secret Tunnels PBS Kids")
        self.assertIn("Secret Tunnels", q)

    def test_unknown_app_does_not_invent_a_service_query(self) -> None:
        q = tp._service_augmented_search_queries(
            "Bluey", service_hint=None, forgiving=False, service_first=False
        )
        self.assertEqual(q, ["Bluey"])

    def test_service_first_enabled_for_any_named_app(self) -> None:
        self.assertTrue(tp._service_first_enabled("Netflix"))
        self.assertTrue(tp._service_first_enabled("PBS Kids"))
        self.assertFalse(tp._service_first_enabled(None))
        self.assertFalse(tp._service_first_enabled(""))


class WatchProviderMappingTests(unittest.TestCase):
    def test_netflix_maps_to_provider_8(self) -> None:
        ids = tp.watch_provider_ids_for_streaming_service(app_name="Netflix")
        self.assertIn(8, ids)

    def test_disney_plus_maps_despite_plus_sign(self) -> None:
        ids = tp.watch_provider_ids_for_streaming_service(service_hint="Disney+")
        self.assertIn(337, ids)

    def test_pbs_kids_maps_to_pbs_providers(self) -> None:
        ids = tp.watch_provider_ids_for_streaming_service(app_name="PBS Kids")
        self.assertTrue(ids & frozenset({209, 293}))

    def test_unknown_app_has_no_providers(self) -> None:
        self.assertEqual(
            tp.watch_provider_ids_for_streaming_service(app_name="Settings"),
            frozenset(),
        )


class WatchProviderRankingTests(unittest.TestCase):
    def setUp(self) -> None:
        tp._WATCH_PROVIDERS_CACHE.clear()

    def tearDown(self) -> None:
        tp._WATCH_PROVIDERS_CACHE.clear()

    def test_same_title_prefers_the_service_listing(self) -> None:
        uk = {
            "id": 1421,
            "name": "The Office",
            "original_language": "en",
            "popularity": 90.0,
        }
        us = {
            "id": 2316,
            "name": "The Office",
            "original_language": "en",
            "popularity": 40.0,
        }
        tp._WATCH_PROVIDERS_CACHE[("tv", 1421)] = frozenset()
        tp._WATCH_PROVIDERS_CACHE[("tv", 2316)] = frozenset({8})
        picked = tp._best_from_results(
            "The Office",
            [uk, us],
            forgiving=True,
            media_kind="tv",
            watch_provider_ids=frozenset({8}),
        )
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], 2316)

    def test_without_service_popularity_still_wins(self) -> None:
        uk = {
            "id": 1421,
            "name": "The Office",
            "original_language": "en",
            "popularity": 90.0,
        }
        us = {
            "id": 2316,
            "name": "The Office",
            "original_language": "en",
            "popularity": 40.0,
        }
        picked = tp._best_from_results("The Office", [uk, us], forgiving=True)
        self.assertEqual(picked["id"], 1421)

    def test_netflix_does_not_force_tv_prefer(self) -> None:
        self.assertEqual(
            tp.prefer_media_for_streaming_service("auto", app_name="Netflix"),
            "auto",
        )
        self.assertEqual(
            tp.prefer_media_for_streaming_service("auto", app_name="PBS Kids"),
            "tv",
        )


if __name__ == "__main__":
    unittest.main()
