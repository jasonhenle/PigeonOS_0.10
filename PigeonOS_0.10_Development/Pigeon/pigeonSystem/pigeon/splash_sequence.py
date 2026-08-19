"""Launch splash: PNG sequence (e.g. 800×480 RGBA) under ``pigeonAssets/pigeonSplash``.

Alpha is preserved end-to-end: the overlay is a full-``shell`` layer above ``content_host``.
Transparent PNG pixels reveal the UI underneath (clock saver from
``SPLASH_CLOCK_REVEAL_FRAME``). Optional ``SPLASH_FADE_OUT_FRAMES`` can still apply a
global alpha ramp at the tail; default is 0 (no fade — the PNG alpha does the reveal).
"""

from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_regular
from pigeon.version import MAJOR, MINOR

# Folder name next to other pigeonAssets media (user-provided sequence).
SPLASH_SEQUENCE_DIRNAME = "pigeonSplash"
_LEGACY_SPLASH_SEQUENCE_DIRNAME = "P_0.5_WIDGET_splash"
SPLASH_NOMINAL_W = 800
SPLASH_NOMINAL_H = 480
SPLASH_FPS = 30
# Hard cap so a huge folder cannot block startup for minutes.
SPLASH_MAX_DURATION_S = 18.0
# Last N frames: global alpha ramps 1 → 0. 0 = no software fade (PNG alpha reveals underlay).
SPLASH_FADE_OUT_FRAMES = 0
# 0-based frame index when the clock saver should paint under the splash
# (``WIDGET_splash_00090.png``). Earlier frames keep a black underlay.
SPLASH_CLOCK_REVEAL_FRAME = 90

# Built-in sequence when ``pigeonSplash`` has no PNGs (same nominal size as the window).
FALLBACK_SPLASH_FRAME_COUNT = 72
_FALLBACK_SPLASH_INTRO_FRAMES = 12


def _natural_png_sort_key(p: Path) -> tuple[object, ...]:
    parts = [int(x) for x in re.findall(r"\d+", p.stem)]
    return tuple(parts) + (p.stem.lower(), p.name.lower())


def splash_effective_frame_count(png_paths: list[Path], *, reveal_frame: int) -> int:
    """Return playback length, trimming trailing fully-transparent frames after reveal.

    After Effects / PNG sequences often pad many empty frames after the graphic is gone;
    playing those looks like the last splash frame is held.
    """
    n = len(png_paths)
    if n <= 0:
        return 0
    start = max(0, min(int(reveal_frame), n - 1))
    last_substantive = start
    for i in range(start, n):
        im = cv2.imread(str(png_paths[i]), cv2.IMREAD_UNCHANGED)
        if im is None or im.ndim != 3 or im.shape[2] < 4:
            continue
        a = im[:, :, 3]
        # Treat as empty once almost no pixels remain (allow tiny encoder dirt).
        if float(a.mean()) < 1.0 and int((a > 10).sum()) < 1000:
            break
        last_substantive = i
    # Inclusive end frame → count; keep at least the reveal frame itself.
    return max(start + 1, last_substantive + 1)


def list_splash_png_paths(assets_root: Path) -> list[Path]:
    """Sorted splash PNG sequence paths, or empty if none found.

    Discovery order (first non-empty wins):
      1. ``pigeonAssets/pigeonSplash/*.png`` (canonical folder next to ``pigeonSplash.mp4``).
      2. Legacy ``P_0.5_WIDGET_splash/*.png``.
      3. Loose numbered frames at ``pigeonAssets/`` root whose stem starts with
         ``pigeonSplash``, ``P_0.5_WIDGET_splash``, or ``splash`` (case-insensitive).
    """
    for dirname in (SPLASH_SEQUENCE_DIRNAME, _LEGACY_SPLASH_SEQUENCE_DIRNAME):
        d = assets_root / dirname
        if not d.is_dir():
            continue
        try:
            files = [p for p in d.iterdir() if p.is_file() and p.suffix.lower() == ".png"]
        except OSError:
            continue
        if files:
            return sorted(files, key=_natural_png_sort_key)

    if assets_root.is_dir():
        prefixes = ("pigeonsplash", "p_0.5_widget_splash", "splash")
        loose: list[Path] = []
        try:
            for p in assets_root.iterdir():
                if not p.is_file() or p.suffix.lower() != ".png":
                    continue
                stem = p.stem.lower()
                if any(stem.startswith(pref) for pref in prefixes):
                    loose.append(p)
        except OSError:
            pass
        if loose:
            return sorted(loose, key=_natural_png_sort_key)
    return []


def resolve_splash_media(assets_root: Path) -> tuple[list[Path], Path | None]:
    """Return ``(png_paths, video_path)`` — PNG sequence preferred, else video, else built-in."""
    pngs = list_splash_png_paths(assets_root)
    if pngs:
        return pngs, None
    return [], find_splash_video_path(assets_root)


# Container formats searched in order when looking for a hardware-decodable splash video.
# H.264 in .mp4 is the best default on macOS (VideoToolbox via AVFoundation / FFmpeg).
# Prefer ``pigeonSplash.mp4`` first so a renamed canonical asset wins over legacy filenames.
_SPLASH_VIDEO_FILENAMES: tuple[str, ...] = (
    "pigeonSplash.mp4",
    "pigeonSplash.mov",
    "P_0.5_WIDGET_splash.mp4",
    "P_0.5_WIDGET_splash.mov",
    "splash.mp4",
    "splash.mov",
)

# Video extensions we'll accept if a filename scan doesn't match exactly.
_SPLASH_VIDEO_EXTS: tuple[str, ...] = (".mp4", ".mov", ".m4v")


def find_splash_video_path(assets_root: Path) -> Path | None:
    """Return the first playable splash video under ``pigeonAssets``, or ``None``.

    Search order (first hit wins):
      1. ``pigeonSplash/`` subfolder for any known splash video filename.
      2. ``pigeonAssets/`` root (legacy layout) for the same known filenames —
         typical current name: ``pigeonSplash.mp4``; older trees used
         ``P_0.5_WIDGET_splash.mp4`` at the assets root.
      3. Either directory, any ``*.mp4`` / ``*.mov`` whose stem starts with
         ``pigeonsplash``, ``P_0.5_WIDGET_splash``, or ``splash`` (case-insensitive).

    H.264/HEVC assets decode via OpenCV's ``VideoCapture`` (hardware-accelerated on
    macOS). Callers should use ``resolve_splash_media`` so a ``pigeonSplash/`` PNG
    sequence wins when present; this helper is the video fallback only.
    """
    search_dirs: list[Path] = []
    for name in (SPLASH_SEQUENCE_DIRNAME, _LEGACY_SPLASH_SEQUENCE_DIRNAME):
        d = assets_root / name
        if d.is_dir():
            search_dirs.append(d)
    # Assets root itself — where ``P_0.5_WIDGET_splash.mp4`` actually ships.
    if assets_root.is_dir():
        search_dirs.append(assets_root)

    # Pass 1: exact known filenames.
    for d in search_dirs:
        for fn in _SPLASH_VIDEO_FILENAMES:
            p = d / fn
            if p.is_file():
                return p

    # Pass 2: prefix match on stem ("P_0.5_WIDGET_splash*", "splash*", "pigeonSplash*").
    prefixes = ("p_0.5_widget_splash", "pigeonsplash", "splash")
    for d in search_dirs:
        try:
            candidates = [p for p in d.iterdir() if p.is_file()]
        except OSError:
            continue
        # Prefer larger files (avoids grabbing a 0-byte sentinel if one ever exists).
        candidates.sort(key=lambda p: p.name.lower())
        for p in candidates:
            if p.suffix.lower() not in _SPLASH_VIDEO_EXTS:
                continue
            stem = p.stem.lower()
            if any(stem.startswith(pref) for pref in prefixes):
                return p
    return None


def flatten_bgra_over_bg_to_rgb(bgra: np.ndarray, bg_bgr: tuple[int, int, int]) -> np.ndarray:
    """Pre-compose BGRA over a solid BGR background and return a contiguous **RGB** uint8 array.

    Feeding ``Image.fromarray(..., "RGB")`` into ``ImageTk.PhotoImage`` uses Tk's fast
    opaque blit path; an equivalent RGBA array forces per-pixel alpha compositing in
    software. For the splash we know the background colour (window ``bg``), so we bake
    alpha off ahead of time for every frame outside the fade tail.
    """
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        raise ValueError("expected BGRA")
    bgr = composite_splash_over_bg(bgra, bg_bgr)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(rgb)


def resize_bgra_if_needed(bgra: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    """Resize BGRA to ``target_w``×``target_h`` once (no-op when already sized)."""
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        return bgra
    h, w = bgra.shape[:2]
    if w == target_w and h == target_h:
        return bgra
    # Shared interp picker lives in pigeon.compositing; import here to avoid a hot-path cycle.
    try:
        from pigeon.compositing import cv_resize_interp

        interp = cv_resize_interp(w, h, target_w, target_h)
    except Exception:
        interp = cv2.INTER_AREA if (target_w * target_h) < (w * h) else cv2.INTER_LINEAR
    return cv2.resize(bgra, (target_w, target_h), interpolation=interp)


def load_splash_bgra(path: Path) -> np.ndarray | None:
    """BGRA uint8, or None. Adds opaque alpha if the file has no alpha channel."""
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None or im.size == 0:
        return None
    if im.ndim != 3:
        return None
    if im.shape[2] == 3:
        bgra = cv2.cvtColor(im, cv2.COLOR_BGR2BGRA)
        bgra[:, :, 3] = 255
        return bgra
    if im.shape[2] == 4:
        return im
    return None


def composite_splash_over_bg(bgra: np.ndarray, bg_bgr: tuple[int, int, int]) -> np.ndarray:
    """Alpha-composite BGRA over a solid BGR background (same H×W)."""
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        raise ValueError("expected BGRA")
    h, w = bgra.shape[:2]
    base = np.empty((h, w, 3), dtype=np.uint8)
    base[:, :] = bg_bgr
    a = bgra[:, :, 3:4].astype(np.float32) / 255.0
    fg = bgra[:, :, :3].astype(np.float32)
    bg = base.astype(np.float32)
    out = fg * a + bg * (1.0 - a)
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def apply_splash_global_alpha(bgra: np.ndarray, factor: float) -> np.ndarray:
    """Multiply the alpha channel by ``factor`` in ``[0, 1]`` (copy)."""
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        return bgra
    f = max(0.0, min(1.0, float(factor)))
    if f >= 0.999:
        return bgra
    out = bgra.copy()
    out[:, :, 3] = np.clip(out[:, :, 3].astype(np.float32) * f, 0.0, 255.0).astype(np.uint8)
    return out


def builtin_splash_bgra_frame(
    frame_index: int,
    total_frames: int,
    *,
    width: int | None = None,
    height: int | None = None,
) -> np.ndarray:
    """
    Single RGBA frame as BGRA uint8 for the Tk overlay (no disk assets).

    Dark plate + wordmark; intro ramp and tail fade match the PNG path behavior.
    """
    w = int(width or SPLASH_NOMINAL_W)
    h = int(height or SPLASH_NOMINAL_H)
    w = max(64, w)
    h = max(32, h)
    # Intro ramp only; ``pigeon_0_5`` applies ``splash_end_fade_factor`` + ``apply_splash_global_alpha`` like PNGs.
    intro = min(
        1.0,
        (float(frame_index) + 1.0) / float(max(1, _FALLBACK_SPLASH_INTRO_FRAMES)),
    )
    alpha_i = int(round(255.0 * max(intro, 0.12)))
    _ = total_frames  # frame count matches PNG path timing / fade window

    img = Image.new("RGBA", (w, h), (8, 8, 10, alpha_i))
    draw = ImageDraw.Draw(img)
    title = "Pigeon"
    sub = f"{MAJOR}.{MINOR}"
    title_px = max(18, h // 5)
    sub_px = max(11, h // 12)
    font_title = ImageFont.load_default()
    font_sub = ImageFont.load_default()
    title_path = resolve_ui_font_bold()
    if title_path:
        try:
            font_title = ImageFont.truetype(title_path, title_px)
        except OSError:
            pass
    sub_path = resolve_ui_font_regular()
    if sub_path:
        try:
            font_sub = ImageFont.truetype(sub_path, sub_px)
        except OSError:
            pass

    tb = draw.textbbox((0, 0), title, font=font_title)
    sb = draw.textbbox((0, 0), sub, font=font_sub)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    sw, sh = sb[2] - sb[0], sb[3] - sb[1]
    gap = max(4, h // 40)
    block_h = th + gap + sh
    y0 = (h - block_h) // 2
    tx = (w - tw) // 2
    sx = (w - sw) // 2
    fill = (245, 247, 250, alpha_i)
    draw.text((tx, y0), title, font=font_title, fill=fill)
    draw.text((sx, y0 + th + gap), sub, font=font_sub, fill=fill)

    rgba = np.asarray(img)
    return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)


def splash_end_fade_factor(frame_index: int, total_frames: int, fade_frames: int) -> float:
    """
    Return a multiplier for ``apply_splash_global_alpha``: 1.0 until the fade zone, then linear 1 → 0.
    ``frame_index`` is 0-based for the frame currently being shown.
    """
    if total_frames <= 0 or fade_frames <= 0:
        return 1.0
    ff = min(fade_frames, total_frames)
    start = total_frames - ff
    if frame_index < start:
        return 1.0
    if ff <= 1:
        return 0.0 if frame_index >= start else 1.0
    u = (frame_index - start) / float(ff - 1)
    return max(0.0, 1.0 - u)


def bgra_to_pil_rgba(bgra: np.ndarray) -> Image.Image:
    """BGRA uint8 → PIL RGBA (for Tk PhotoImage with per-pixel alpha)."""
    if bgra.ndim != 3 or bgra.shape[2] != 4:
        raise ValueError("expected BGRA")
    rgba = cv2.cvtColor(bgra, cv2.COLOR_BGRA2RGBA)
    return Image.fromarray(rgba, "RGBA")
