"""
Standalone preview for the mic EQ visualizer (**no microphone**, synthetic audio).

Uses the same :func:`pigeon.audio_waves.blend_mic_visualizer` path as the main app.

From ``Pigeon_python`` (venv with app requirements)::

    python testingEnvironments/mic_viz_test.py

Or double-click / run::

    testingEnvironments/run_mic_viz_test.command

Shim (same code path)::

    python -m pigeon.mic_viz_test

Options::

    python testingEnvironments/mic_viz_test.py --no-intro
    python testingEnvironments/mic_viz_test.py --width 1280 --height 640 --fps 30
"""

from __future__ import annotations

import argparse
import sys
import time
import tkinter as tk
from pathlib import Path


def _ensure_pigeon_python_on_path() -> None:
    """``Pigeon_python`` must be on ``sys.path`` (parent of this ``testingEnvironments`` folder)."""
    pigeon_python = Path(__file__).resolve().parents[1]
    s = str(pigeon_python)
    if s not in sys.path:
        sys.path.insert(0, s)


_ensure_pigeon_python_on_path()

import cv2
import numpy as np
from PIL import Image, ImageTk

from pigeon.audio_waves import MIC_VIZ_INTRO_TOTAL_S, blend_mic_visualizer

# Match main app window / internal composite cap (see pigeon_0_5.WINDOW_W/H).
_DEFAULT_TEST_W = 800
_DEFAULT_TEST_H = 480


def _synth_mono_block_512(t_wall: float) -> np.ndarray:
    """~512 samples @ 44.1 kHz; energy moves across bands over time."""
    n = 512
    sr = 44100.0
    tt = np.arange(n, dtype=np.float32) / np.float32(sr)
    out = np.zeros(n, dtype=np.float32)
    freqs_hz = (90.0, 250.0, 700.0, 2000.0, 5500.0, 12000.0)
    for j, f in enumerate(freqs_hz):
        phase = float(t_wall * (0.7 + j * 0.15))
        amp = 0.07 * (0.35 + 0.65 * (0.5 + 0.5 * np.sin(phase + j * 0.9)))
        out += amp * np.sin(np.float32(2.0 * np.pi) * np.float32(f) * tt + np.float32(phase * (j + 1)))
    out += np.float32(0.012) * np.random.randn(n).astype(np.float32)
    return np.clip(out, -1.0, 1.0).astype(np.float32)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open a Tk window that drives the mic EQ visualizer with synthetic audio."
    )
    ap.add_argument(
        "--no-intro",
        action="store_true",
        help="Skip landing-style intro (immediate full-strength bars).",
    )
    ap.add_argument("--fps", type=float, default=48.0, help="Refresh rate (default 48).")
    ap.add_argument(
        "--width",
        type=int,
        default=None,
        help=f"Frame width in pixels (default: {_DEFAULT_TEST_W}).",
    )
    ap.add_argument(
        "--height",
        type=int,
        default=None,
        help=f"Frame height in pixels (default: {_DEFAULT_TEST_H}).",
    )
    args = ap.parse_args(argv)

    w = args.width
    h = args.height
    if w is None and h is None:
        w, h = _DEFAULT_TEST_W, _DEFAULT_TEST_H
    elif w is None:
        h = int(h)  # type: ignore[arg-type]
        w = max(320, int(round(h * (_DEFAULT_TEST_W / float(_DEFAULT_TEST_H)))))
    elif h is None:
        w = int(w)  # type: ignore[arg-type]
        h = max(200, int(round(w * float(_DEFAULT_TEST_H) / float(_DEFAULT_TEST_W))))
    else:
        w, h = int(w), int(h)

    fps = max(8.0, min(120.0, float(args.fps)))
    interval_ms = max(1, int(round(1000.0 / fps)))
    t0 = time.monotonic()

    root = tk.Tk()
    root.title("Pigeon — mic EQ visualizer (test)")
    root.geometry(f"{w}x{h}")
    label = tk.Label(root, bd=0, highlightthickness=0)
    label.pack(fill=tk.BOTH, expand=True)

    bgr = np.zeros((h, w, 3), dtype=np.uint8)
    bgr[:] = (22, 20, 18)
    photo_holder: list[ImageTk.PhotoImage | None] = [None]

    def tick() -> None:
        try:
            if not root.winfo_exists():
                return
        except tk.TclError:
            return
        now = time.monotonic()
        elapsed = now - t0
        mono = _synth_mono_block_512(elapsed)
        bgr[:] = (22, 20, 18)
        landing_elapsed_s = None if args.no_intro else float(elapsed)
        blend_mic_visualizer(
            bgr,
            elapsed,
            active=True,
            landing_elapsed_s=landing_elapsed_s,
            mono_override=mono,
        )
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        pil = Image.fromarray(rgb)
        photo_holder[0] = ImageTk.PhotoImage(image=pil)
        label.configure(image=photo_holder[0])
        root.after(interval_ms, tick)

    root.bind("<Escape>", lambda e: root.destroy())
    root.bind("<q>", lambda e: root.destroy())
    hint = (
        "Synthetic audio → same EQ path as the app. Esc or q to quit."
        + ("" if args.no_intro else f" Intro ~{MIC_VIZ_INTRO_TOTAL_S:.1f}s.")
    )
    print(hint, file=sys.stderr)
    root.after(1, tick)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
