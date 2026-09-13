"""YouTube 16×9 thumbnail identity helpers (no network)."""

from __future__ import annotations

import os
import sys
import unittest

_SYS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SYS_ROOT not in sys.path:
    sys.path.insert(0, _SYS_ROOT)


class YoutubeVideoIdTests(unittest.TestCase):
    def test_content_identifier_and_watch_url(self) -> None:
        from pigeon.apple_tv_now_playing import youtube_video_id_from_metadata

        self.assertEqual(
            youtube_video_id_from_metadata(
                {"content_identifier": "dQw4w9WgXcQ"}
            ),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_video_id_from_metadata(
                {"query": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
            ),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_video_id_from_metadata({"title": "Never Gonna Give You Up"}),
            None,
        )
        self.assertIsNone(youtube_video_id_from_metadata(None))

    def test_search_payload_extracts_first_video_id(self) -> None:
        from pigeon.apple_tv_now_playing import (
            youtube_title_from_metadata,
            youtube_video_id_from_search_payload,
        )

        html = r'{"videoId":"dQw4w9WgXcQ","thumbnail":{"url":"x"}}'
        self.assertEqual(youtube_video_id_from_search_payload(html), "dQw4w9WgXcQ")
        nested = {
            "contents": {
                "twoColumnSearchResultsRenderer": {
                    "primaryContents": {
                        "sectionListRenderer": {
                            "contents": [
                                {
                                    "itemSectionRenderer": {
                                        "contents": [
                                            {
                                                "videoRenderer": {
                                                    "videoId": "abcdefghijk",
                                                    "title": {"runs": [{"text": "x"}]},
                                                }
                                            }
                                        ]
                                    }
                                }
                            ]
                        }
                    }
                }
            }
        }
        self.assertEqual(youtube_video_id_from_search_payload(nested), "abcdefghijk")
        self.assertIsNone(youtube_video_id_from_search_payload(""))
        self.assertEqual(
            youtube_title_from_metadata({"title": "Never Gonna Give You Up"}),
            "Never Gonna Give You Up",
        )

    def test_thumb_identity_prefers_id(self) -> None:
        from pigeon.apple_tv_now_playing import youtube_thumb_identity

        self.assertEqual(
            youtube_thumb_identity({"content_identifier": "dQw4w9WgXcQ", "title": "Nope"}),
            "id:dQw4w9WgXcQ",
        )
        self.assertEqual(
            youtube_thumb_identity({"title": "Never Gonna Give You Up"}),
            "title:never gonna give you up",
        )


if __name__ == "__main__":
    unittest.main()
