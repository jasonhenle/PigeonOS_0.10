"""Official colon titles must not collapse to a different work (It vs IT: Welcome to Derry)."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)

from pigeon import tmdb_poster as tp  # noqa: E402


class OfficialColonTitleTests(unittest.TestCase):
    def test_welcome_to_derry_is_not_it(self) -> None:
        raw = "IT: Welcome to Derry"
        self.assertIsNone(tp.colon_prefix_show_query(raw))
        self.assertEqual(tp.refine_tmdb_search_query(raw), raw)
        self.assertFalse(tp.equivalent_tmdb_search_queries(raw, "It"))
        self.assertFalse(tp.equivalent_tmdb_search_queries(raw, "IT"))

    def test_welcome_to_derry_dash_variant_stays_intact(self) -> None:
        raw = "IT - Welcome to Derry"
        self.assertIsNone(tp.colon_prefix_show_query(raw))
        self.assertEqual(tp.refine_tmdb_search_query(raw), raw)

    def test_variants_do_not_fall_back_to_bare_it(self) -> None:
        variants = [v.casefold() for v in tp._tmdb_query_variants("IT: Welcome to Derry")]
        self.assertIn("it: welcome to derry", variants)
        self.assertNotIn("it", variants)

    def test_true_show_episode_colon_still_splits(self) -> None:
        self.assertEqual(tp.colon_prefix_show_query("The Office: The Dundies"), "The Office")
        self.assertEqual(tp.refine_tmdb_search_query("The Office: The Dundies"), "The Office")

    def test_snl_colon_segment_still_uses_show_side(self) -> None:
        self.assertEqual(tp.colon_prefix_show_query("SNL: Cold Open"), "SNL")

    def test_series_plus_episode_fields_keep_welcome_to_derry(self) -> None:
        q = tp.resolve_tmdb_query_from_now_playing_fields(
            base_query="IT: Welcome to Derry",
            title="29 Neibolt Street",
            series_name="IT: Welcome to Derry",
            forgiving=False,
        )
        self.assertEqual(q, "IT: Welcome to Derry")

    def test_rank_does_not_treat_it_as_welcome_to_derry(self) -> None:
        it_row = {"id": 19614, "name": "It"}
        derry_row = {"id": 249042, "name": "IT: Welcome to Derry"}
        self.assertLess(tp._match_rank("IT: Welcome to Derry", it_row)[0], 4)
        self.assertEqual(tp._match_rank("IT: Welcome to Derry", derry_row)[0], 5)
        picked = tp._best_from_results(
            "IT: Welcome to Derry",
            [it_row, derry_row],
            forgiving=False,
        )
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], 249042)


if __name__ == "__main__":
    unittest.main()
