#!/usr/bin/env python3
"""Preview a widget in its shell: full design canvas or cropped to grid span (for building assets).

Examples:
  python3 pigeon_widget_preview.py --widget clock
  python3 pigeon_widget_preview.py --widget poster
  python3 pigeon_widget_preview.py --widget placeholder --span 6 3
  python3 pigeon_widget_preview.py --widget clock --full-window
  python3 pigeon_widget_preview.py --widget clock --fake-time "2020-11-30 12:55:00"

Hotkeys:
  O — toggle grid overlay
  # — toggle fake vs real clock (only while overlay is ON; fake time stays if you then turn overlay off)
"""

from __future__ import annotations

import argparse
import os
import sys

import cv2
import numpy as np
import tkinter as tk
from PIL import Image, ImageTk

import pigeon_0_9 as pigeon_app
from pigeon.widgets.clock_calendar import DEFAULT_FAKE_TIME_STRING, ClockCalendarWidget
from pigeon.widgets.placeholder import PlaceholderWidget
from pigeon.widgets.poster_art import PosterArtWidget
from pigeon.widget_shell import WidgetShell


def _frame_to_photo(canvas_bgr: np.ndarray) -> ImageTk.PhotoImage:
    rgb = cv2.cvtColor(canvas_bgr, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    return ImageTk.PhotoImage(image=img)


def main() -> int:
    p = argparse.ArgumentParser(description="Widget shell preview.")
    p.add_argument(
        "--widget",
        choices=("clock", "placeholder", "poster"),
        default="clock",
        help="clock = 5×2; poster = 4×6 posterArt; placeholder uses --span",
    )
    p.add_argument("--span", nargs=2, type=int, metavar=("W", "H"), default=(6, 3), help="Grid span for placeholder only")
    p.add_argument("--full-window", action="store_true", help="Show full 19×8 design (scaled) instead of crop")
    p.add_argument("--width", type=int, default=900, help="Window width")
    p.add_argument("--height", type=int, default=500, help="Window height")
    p.add_argument("--overlay", action="store_true", help="Start with grid overlay visible (default: off, like main app)")
    p.add_argument(
        "--fake-time",
        metavar="WHEN",
        default=None,
        help='Fixed clock/calendar for testing (e.g. "2020-11-30 12:55:00"). Also: PIGEON_FAKE_TIME env.',
    )
    args = p.parse_args()

    if args.fake_time:
        os.environ["PIGEON_FAKE_TIME"] = args.fake_time.strip()

    w, h = args.span
    if w < 1 or h < 1 or w > 19 or h > 8:
        sys.stderr.write("Span must be 1..19 wide and 1..8 tall.\n")
        return 2

    if args.widget == "clock":
        shell = WidgetShell(ClockCalendarWidget())
        title = "Pigeon · clock — O overlay | # fake (overlay on)"
    elif args.widget == "poster":
        shell = WidgetShell(
            PosterArtWidget(
                anchor_row=pigeon_app.POSTER_ANCHOR_ROW,
                anchor_col=pigeon_app.POSTER_ANCHOR_COL,
            )
        )
        title = "Pigeon · posterArt (4×6) — O overlay"
    else:
        shell = WidgetShell(PlaceholderWidget(w, h))
        title = f"Pigeon · placeholder ({w}×{h}) — O overlay"

    show_overlay = bool(args.overlay)
    fake_time_string = os.environ.get("PIGEON_FAKE_TIME") or DEFAULT_FAKE_TIME_STRING

    root = tk.Tk()
    root.title(title)
    root.geometry(f"{args.width}x{args.height}")
    lbl = tk.Label(root, bd=0, highlightthickness=0)
    lbl.pack(fill="both", expand=True)

    clock_job_id = None  # after() job id for live clock refresh

    def redraw() -> None:
        if args.full_window:
            full = shell.composite_full_with_overlay(show_overlay)
            tw, th = args.width, args.height
            scale = min(tw / full.shape[1], th / full.shape[0])
            nw = max(1, int(round(full.shape[1] * scale)))
            nh = max(1, int(round(full.shape[0] * scale)))
            shown = cv2.resize(full, (nw, nh), interpolation=cv2.INTER_AREA)
            canvas = np.zeros((th, tw, 3), dtype=np.uint8)
            ox = (tw - nw) // 2
            oy = (th - nh) // 2
            canvas[oy : oy + nh, ox : ox + nw] = shown
            photo = _frame_to_photo(canvas)
        else:
            canvas = shell.render_preview_scaled(
                args.width, args.height, include_overlay=show_overlay, local_grid=True
            )
            photo = _frame_to_photo(canvas)

        lbl.configure(image=photo)
        lbl.image = photo

    def toggle_overlay(_event=None) -> None:
        nonlocal show_overlay
        show_overlay = not show_overlay
        redraw()

    def toggle_fake_time(_event=None) -> None:
        nonlocal fake_time_string
        if not show_overlay:
            return
        if os.environ.get("PIGEON_FAKE_TIME"):
            fake_time_string = os.environ["PIGEON_FAKE_TIME"]
            os.environ.pop("PIGEON_FAKE_TIME", None)
        else:
            os.environ["PIGEON_FAKE_TIME"] = fake_time_string
        redraw()
        reschedule_clock()

    def _on_keypress_for_hash(event) -> None:
        if event.keysym == "numbersign" or getattr(event, "char", "") == "#":
            toggle_fake_time()

    def reschedule_clock() -> None:
        nonlocal clock_job_id
        if args.widget != "clock":
            return
        if clock_job_id is not None:
            try:
                root.after_cancel(clock_job_id)
            except (tk.TclError, ValueError):
                pass
            clock_job_id = None
        if os.environ.get("PIGEON_FAKE_TIME"):
            return

        def tick() -> None:
            nonlocal clock_job_id
            redraw()
            clock_job_id = root.after(500, tick)

        clock_job_id = root.after(500, tick)

    root.bind("<o>", toggle_overlay)
    root.bind("<O>", toggle_overlay)
    root.bind_all("<KeyPress>", _on_keypress_for_hash)

    redraw()
    reschedule_clock()

    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
