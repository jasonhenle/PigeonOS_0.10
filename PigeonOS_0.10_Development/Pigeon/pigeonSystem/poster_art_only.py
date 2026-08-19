#!/usr/bin/env python3
"""
Open only the poster art widget (4×6 crop in a window).

Run from Pigeon_python (so `pigeon` and `pigeon_0_5` import correctly):

  python3 poster_art_only.py

Same extra flags as pigeon_widget_preview.py, e.g.:

  python3 poster_art_only.py --width 600 --height 900 --overlay
  python3 poster_art_only.py --full-window
"""

from __future__ import annotations

import sys


def main() -> int:
    args = sys.argv[1:]
    if not any(a == "--widget" for a in args):
        args = ["--widget", "poster", *args]
    sys.argv = [sys.argv[0], *args]
    from pigeon_widget_preview import main as preview_main

    return preview_main()


if __name__ == "__main__":
    raise SystemExit(main())
