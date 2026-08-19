"""Composite status bar: one continuous black pill, bar-shaped hole, then labels, then the bar (on top).

The black strip is a **single** unbroken capsule (e.g. cols 3–17). A hole matching the progress
bar’s pill mask (same geometry as :func:`pill_alpha_mask`) is punched through it so the backdrop
shows there; the semi-transparent bar draws on top in the same footprint.

The bar track is ~60% gray. Progress fill uses the settings UI color (color page);
backdrop sampling remains available as a fallback accent for other chrome.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr
from pigeon.design import DESIGN_H, DESIGN_W, GRID_COLS, GRID_ROWS
from pigeon.grid_overlay import overlay_grid_metrics
from pigeon.font_paths import resolve_digital7_font, resolve_ui_font_bold
from pigeon.image_ui_protocol import load_image_bgra
from pigeon.ui_pill import pill_alpha_mask

# ~60% luminance neutral (BGR).
_TRACK_GRAY_BGR = (153, 153, 153)

# Fallback accent when no backdrop (vivid orange, BGR).
_ACCENT_FALLBACK_BGR = (40, 140, 255)

# Progress track + fill alpha inside the pill mask (backdrop shows through; black strip does not).
_PROGRESS_BAR_OPACITY = 0.8

# 1-based overlay-grid column for the visible center of ``pigeonNowPlaying_Bar`` (debug grid).
_NOW_PLAYING_BAR_OVERLAY_CENTER_COL = 10.0

# Elapsed / remaining chrome (``pigeonNowPlayingTC_*``) — container + numerals share this alpha.
_NOW_PLAYING_TC_OPACITY = 0.7
# Digital-7 cap height passed to ``_font_for_pixel_height`` (legacy 24px, 10% smaller).
_NOW_PLAYING_TC_FONT_TARGET_PX = int(round(24 * 0.9))

# Elapsed pill fades before its opaque bounds would overlap the remaining pill (design px).
_ELAPSED_NEAR_REMAINING_GAP_PX = 18
_ELAPSED_CLEAR_REMAINING_GAP_PX = 30
_ELAPSED_FADE_DURATION_S = 1.25


def _visible_alpha_x_lr(bgra: np.ndarray) -> tuple[int, int] | None:
    """Return ``(left, right)`` inclusive column indices of opaque pixels, or ``None``."""
    if bgra.ndim != 3 or bgra.shape[2] < 4:
        return None
    xs = np.where(bgra[:, :, 3] > 0)[1]
    if xs.size == 0:
        return None
    return (int(xs.min()), int(xs.max()))


def _visible_alpha_ltrb(bgra: np.ndarray) -> tuple[int, int, int, int] | None:
    """Return ``(left, top, right, bottom)`` inclusive bounds of opaque pixels, or ``None``."""
    if bgra.ndim != 3 or bgra.shape[2] < 4:
        return None
    ys, xs = np.where(bgra[:, :, 3] > 0)
    if xs.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def _layout_now_playing_bar_patch_xywh(patch: np.ndarray) -> tuple[int, int, int, int]:
    """Bottom-anchored ``pigeonNowPlaying_Bar`` placement on the design canvas (overlay grid)."""
    cell_f, gx0, _gy0, _gw, _gh, _cr = overlay_grid_metrics(
        DESIGN_W, DESIGN_H, GRID_ROWS, GRID_COLS
    )
    h, w = int(patch.shape[0]), int(patch.shape[1])
    cx = float(gx0) + (_NOW_PLAYING_BAR_OVERLAY_CENTER_COL - 0.5) * cell_f
    span = _visible_alpha_x_lr(patch)
    if span is None:
        x = int(round(cx - 0.5 * float(w)))
    else:
        vl, vr = span
        vis_c = 0.5 * (float(vl) + float(vr))
        x = int(round(cx - vis_c))
    y = int(DESIGN_H) - h
    return (x, y, w, h)


def design_now_playing_bar_rect(assets_dir: Path) -> tuple[int, int, int, int] | None:
    """``(x, y, w, h)`` for ``pigeonNowPlaying_Bar.png`` on the design canvas, or ``None`` if missing."""
    p = Path(assets_dir) / "pigeonNowPlaying_Bar.png"
    if not p.is_file():
        return None
    try:
        patch = load_image_bgra(p)
    except Exception:
        return None
    if patch is None or patch.size == 0:
        return None
    x, y, w, h = _layout_now_playing_bar_patch_xywh(patch)
    return (int(x), int(y), int(w), int(h))


def design_now_playing_bar_opaque_rect(assets_dir: Path) -> tuple[int, int, int, int] | None:
    """Tight ``(x, y, w, h)`` around **non-transparent** bar pixels (canvas space).

    Use this to center in-bar labels (e.g. ``paused``) so they align with the visible pill rather
    than the asset’s transparent margins.
    """
    p = Path(assets_dir) / "pigeonNowPlaying_Bar.png"
    if not p.is_file():
        return None
    try:
        patch = load_image_bgra(p)
    except Exception:
        return None
    if patch is None or patch.size == 0:
        return None
    x, y, w, h = _layout_now_playing_bar_patch_xywh(patch)
    bb = _visible_alpha_ltrb(patch)
    if bb is None:
        return (int(x), int(y), int(w), int(h))
    l, t, r, b = bb
    iw = max(1, int(r) - int(l) + 1)
    ih = max(1, int(b) - int(t) + 1)
    return (int(x) + int(l), int(y) + int(t), iw, ih)


@dataclass(frozen=True)
class DesignPatch:
    x: int
    y: int
    w: int
    h: int
    bgra: np.ndarray
    # "default" | "wordmark" | "streaming_badge" | "receiver_audio" (playback overlay).
    layer: str = "default"


def sample_accent_bgr_from_backdrop(bgr: np.ndarray | None) -> tuple[int, int, int]:
    """
    Pick a bold BGR accent from backdrop art: favor high saturation × value pixels,
    then boost saturation/value for on-screen pop.
    """
    if bgr is None or bgr.size == 0 or bgr.ndim != 3 or bgr.shape[2] < 3:
        return _ACCENT_FALLBACK_BGR
    h0, w0 = bgr.shape[:2]
    if h0 < 2 or w0 < 2:
        return _ACCENT_FALLBACK_BGR
    # Center crop avoids letterbox extremes on some masters.
    ym, xm = int(h0 * 0.12), int(w0 * 0.08)
    y1, x1 = h0 - ym, w0 - xm
    crop = bgr[ym:y1, xm:x1]
    if crop.size == 0:
        crop = bgr
    small = cv2.resize(crop, (72, 40), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    _, s, v = cv2.split(hsv)
    s_f = s.astype(np.float32)
    v_f = v.astype(np.float32)
    scores = s_f * v_f
    flat = scores.ravel()
    if flat.size == 0:
        return _ACCENT_FALLBACK_BGR
    # Top 5% by score → robust mean (reduces single-pixel noise).
    k = max(1, int(round(0.05 * flat.size)))
    idx = np.argpartition(flat, -k)[-k:]
    ys, xs = np.unravel_index(idx, scores.shape)
    b_acc = int(np.median(small[ys, xs, 0]))
    g_acc = int(np.median(small[ys, xs, 1]))
    r_acc = int(np.median(small[ys, xs, 2]))
    return _boost_accent_bgr(b_acc, g_acc, r_acc)


def _punch_bar_shaped_hole_in_strip_bgra(
    strip_bgra: np.ndarray,
    *,
    bar_local_x: int,
    bar_local_y: int,
    bar_bw: int,
    bar_bh: int,
) -> None:
    """
    In-place: reduce strip alpha by the progress-bar pill mask so the hole matches the bar shape.

    Uses the same ``pill_alpha_mask(bar_bw, bar_bh)`` as :meth:`StatusBarWidget._build_bar_patch_bgra`.
    """
    uh, uw = strip_bgra.shape[:2]
    if bar_bw < 1 or bar_bh < 1:
        return
    bar_m = pill_alpha_mask(bar_bw, bar_bh).astype(np.float32) / 255.0
    hx0, hy0 = bar_local_x, bar_local_y
    sy0 = max(0, hy0)
    sx0 = max(0, hx0)
    sy1 = min(uh, hy0 + bar_bh)
    sx1 = min(uw, hx0 + bar_bw)
    if sy0 >= sy1 or sx0 >= sx1:
        return
    my0, mx0 = sy0 - hy0, sx0 - hx0
    mh, mw = sy1 - sy0, sx1 - sx0
    m = bar_m[my0 : my0 + mh, mx0 : mx0 + mw]
    sub_a = strip_bgra[sy0:sy1, sx0:sx1, 3].astype(np.float32)
    strip_bgra[sy0:sy1, sx0:sx1, 3] = np.clip(sub_a * (1.0 - m), 0, 255).astype(np.uint8)


def _boost_accent_bgr(b: int, g: int, r: int) -> tuple[int, int, int]:
    px = np.uint8([[[b, g, r]]])
    hsv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)
    h = int(hsv[0, 0, 0])
    s = min(255, int(hsv[0, 0, 1]) + 55)
    v = min(255, int(hsv[0, 0, 2]) + 45)
    hsv[0, 0, 0] = h
    hsv[0, 0, 1] = s
    hsv[0, 0, 2] = v
    out = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)[0, 0]
    return (int(out[0]), int(out[1]), int(out[2]))


class StatusBarWidget:
    """Asset-driven nowPlaying bar: elapsed tracks the bar fill boundary; remaining is flush-right."""

    def __init__(
        self,
        *,
        assets_dir: Path,
        trt_row: int | float = 8,
        trt_played_col: int = 3,
        trt_remaining_col: int = 16,  # 2-wide ending col 17 leaves margin before grid edge
        trt_played_text: str = "0",
        trt_remaining_text: str = "1:00:00",
        trt_label_span_wide: int = 2,
        trt_label_span_tall: int = 1,
    ) -> None:
        self._assets_dir = Path(assets_dir)
        _ = trt_row
        _ = trt_played_col
        _ = trt_remaining_col
        _ = trt_label_span_wide
        _ = trt_label_span_tall
        self._tc_played_text = str(trt_played_text)
        self._tc_remaining_text = str(trt_remaining_text)
        self._accent_bgr: tuple[int, int, int] = _ACCENT_FALLBACK_BGR
        self._progress: float = 0.0
        self._cached_blits: list[DesignPatch] | None = None
        self._cached_sig: tuple[object, ...] | None = None
        # When False, omit TRT pills + progress bar (nothing queued / idle Apple TV).
        self._now_playing_chrome_visible: bool = False
        # When False, omit bar + pills even if chrome is "visible" (live stream, no duration, etc.).
        self._trt_substantive: bool = False
        # When True, omit bar + pills during theater idle-dim (red mono) even if chrome would show.
        self._theater_dim_suppressed: bool = False
        # Fade elapsed pill/text when it nears the remaining pill (see ``design_blits``).
        self._elapsed_fade_start_mono: float | None = None
        self._bar_asset = self._load_asset("pigeonNowPlaying_Bar.png")
        self._tc_container_asset = self._load_asset("pigeonNowPlaying_TC_container.png")
        self._tc_elapsed_asset = self._load_asset("pigeonTimeCode_Elapsed.png")
        if self._tc_elapsed_asset is None:
            self._tc_elapsed_asset = self._load_asset("pigeonNowPlayingTC_elapsed.png")
        if self._tc_elapsed_asset is None:
            self._tc_elapsed_asset = self._load_asset("pigeonNowPlaying_TC_elapsed.png")
        if self._tc_elapsed_asset is None:
            self._tc_elapsed_asset = self._load_asset("pigeonNowPlaying_TC_elaspsed.png")
        if self._tc_elapsed_asset is None:
            self._tc_elapsed_asset = self._tc_container_asset
        self._tc_remaining_asset = self._load_asset("pigeonTimeCode_Remaining.png")
        if self._tc_remaining_asset is None:
            self._tc_remaining_asset = self._load_asset("pigeonNowPlayingTC_remaining.png")
        if self._tc_remaining_asset is None:
            self._tc_remaining_asset = self._tc_container_asset
        self._digital7_path = resolve_digital7_font() or resolve_ui_font_bold()

    @property
    def accent_bgr(self) -> tuple[int, int, int]:
        """BGR tint used for ambient chrome (sampled from backdrop, or fallback)."""
        return self._accent_bgr

    def _progress_fill_bgr(self) -> tuple[int, int, int]:
        """Settings UI color for the elapsed bar; backdrop accent as fallback."""
        try:
            from pigeon.widgets.ui_color_settings import (
                hex_for_color_key,
                read_ui_color_keys,
            )

            keys = read_ui_color_keys()
            hex_color = hex_for_color_key("ui", keys.get("ui", "red"))
            h = (hex_color or "").strip().lstrip("#")
            if len(h) == 3:
                h = f"{h[0]}{h[0]}{h[1]}{h[1]}{h[2]}{h[2]}"
            if len(h) == 6:
                r = int(h[0:2], 16)
                g = int(h[2:4], 16)
                b = int(h[4:6], 16)
                return (b, g, r)
        except Exception:
            pass
        return self._accent_bgr

    def clear_cache(self) -> None:
        self._cached_blits = None
        self._cached_sig = None

    def set_now_playing_chrome_visible(self, visible: bool) -> bool:
        """Show or hide the progress bar and timecode pills (compositor uses empty blits when hidden)."""
        v = bool(visible)
        if v == self._now_playing_chrome_visible:
            return False
        self._now_playing_chrome_visible = v
        self.clear_cache()
        return True

    def set_trt_substantive(self, substantive: bool) -> bool:
        """Allow TRT bar + pills only when we have duration-based timecodes (not live / unknown TRT)."""
        v = bool(substantive)
        if v == self._trt_substantive:
            return False
        self._trt_substantive = v
        self.clear_cache()
        return True

    @property
    def now_playing_chrome_visible(self) -> bool:
        return self._now_playing_chrome_visible

    @property
    def theater_dim_suppressed(self) -> bool:
        return self._theater_dim_suppressed

    def set_theater_dim_suppressed(self, suppressed: bool) -> bool:
        """Hide TRT + progress chrome while the UI is in (or easing into) theater idle-dim."""
        v = bool(suppressed)
        if v == self._theater_dim_suppressed:
            return False
        self._theater_dim_suppressed = v
        self.clear_cache()
        return True

    def set_accent_from_backdrop_bgr(self, backdrop_bgr: np.ndarray | None) -> bool:
        """Re-sample accent from TMDb backdrop (call when backdrop image updates)."""
        new_acc = sample_accent_bgr_from_backdrop(backdrop_bgr)
        if new_acc != self._accent_bgr:
            self._accent_bgr = new_acc
            self.clear_cache()
            return True
        return False

    def set_timecodes(self, *, played_text: str | None = None, remaining_text: str | None = None) -> bool:
        return self.set_now_playing_display(
            played_text=played_text, remaining_text=remaining_text, progress=None
        )

    def set_now_playing_display(
        self,
        *,
        played_text: str | None = None,
        remaining_text: str | None = None,
        progress: float | None = None,
    ) -> bool:
        """Update TRT strings and/or **elapsed** fraction ``progress`` in ``[0, 1]`` (played / total).

        Pass ``progress=None`` to leave the fraction unchanged.
        """
        changed = False
        if played_text is not None:
            t = str(played_text)
            if t != self._tc_played_text:
                self._tc_played_text = t
                changed = True
        if remaining_text is not None:
            t = str(remaining_text)
            if t != self._tc_remaining_text:
                self._tc_remaining_text = t
                changed = True
        if progress is not None:
            pf = max(0.0, min(1.0, float(progress)))
            if abs(pf - self._progress) > 1e-9:
                self._progress = pf
                changed = True
        if changed:
            self.clear_cache()
        return changed

    def _load_asset(self, name: str) -> np.ndarray | None:
        p = self._assets_dir / name
        if not p.is_file():
            return None
        try:
            return load_image_bgra(p)
        except Exception:
            return None

    def _anchor_bar_bottom_overlay_col_center(self, patch: np.ndarray) -> tuple[int, int, int, int]:
        """Bottom-anchored bar; **visible** center on the overlay debug grid column (1-based)."""
        return _layout_now_playing_bar_patch_xywh(patch)

    def _anchor_tc_patch_bottom_right_flush(self, patch: np.ndarray) -> tuple[int, int, int, int]:
        """Bottom-anchored; flush the patch to the **right** of the design canvas (not clipped)."""
        h, w = int(patch.shape[0]), int(patch.shape[1])
        x = int(DESIGN_W) - w
        if x < 0:
            x = 0
        y = int(DESIGN_H) - h
        if y < 0:
            y = 0
        return x, y, w, h

    def _font_for_pixel_height(self, target_px: int) -> ImageFont.ImageFont:
        target_px = max(6, int(target_px))
        probe = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
        draw = ImageDraw.Draw(probe)
        lo, hi = 6, 120
        try:
            best = ImageFont.truetype(self._digital7_path, 24)
        except OSError:
            return ImageFont.load_default()
        while lo <= hi:
            mid = (lo + hi) // 2
            try:
                f = ImageFont.truetype(self._digital7_path, mid)
            except OSError:
                break
            l, t, r, b = draw.textbbox((0, 0), "00:00:00", font=f)
            th = b - t
            if th <= target_px:
                best = f
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _text_patch(
        self,
        *,
        text: str,
        center_x: int,
        center_y: int,
        alpha_mul: float = 1.0,
    ) -> np.ndarray:
        out = np.zeros((DESIGN_H, DESIGN_W, 4), dtype=np.uint8)
        if not text:
            return out
        rgba = Image.new("RGBA", (DESIGN_W, DESIGN_H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(rgba)
        font = self._font_for_pixel_height(_NOW_PLAYING_TC_FONT_TARGET_PX)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        tw = r - l
        th = b - t
        x = int(round(center_x - tw / 2 - l))
        y = int(round(center_y - th / 2 - t))
        a = int(round(255 * max(0.0, min(1.0, float(alpha_mul)))))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, a))
        arr = np.asarray(rgba)
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA)

    def _elapsed_alpha(self) -> float:
        if self._elapsed_fade_start_mono is None:
            return 1.0
        t = max(0.0, time.monotonic() - self._elapsed_fade_start_mono)
        return max(0.0, 1.0 - (t / float(_ELAPSED_FADE_DURATION_S)))

    def _update_elapsed_fade_for_remaining_gap(self, gap_px: float) -> None:
        """Start / clear the elapsed fade timer from horizontal clearance to the remaining pill."""
        if gap_px < float(_ELAPSED_NEAR_REMAINING_GAP_PX):
            if self._elapsed_fade_start_mono is None:
                self._elapsed_fade_start_mono = time.monotonic()
        elif gap_px > float(_ELAPSED_CLEAR_REMAINING_GAP_PX):
            self._elapsed_fade_start_mono = None

    def design_blits(self) -> list[DesignPatch]:
        if (
            not self._now_playing_chrome_visible
            or self._theater_dim_suppressed
            or not self._trt_substantive
        ):
            return []

        bx = by = bw = bh = 0
        transition_x = float(DESIGN_W) * 0.5
        if self._bar_asset is not None:
            bx, by, bw, bh = self._anchor_bar_bottom_overlay_col_center(self._bar_asset)
            # Must match the accent fill edge below: ``elapsed_w = progress * bar_w``.
            elapsed_frac_tx = max(0.0, min(1.0, float(self._progress)))
            elapsed_w_tx = int(round(elapsed_frac_tx * float(bw)))
            elapsed_w_tx = max(0, min(bw, elapsed_w_tx))
            transition_x = float(bx + int(elapsed_w_tx))

        rx = ry = rw = rh = 0
        r_opaque_l: int | None = None
        if self._tc_remaining_asset is not None:
            _rp = self._tc_remaining_asset
            rx, ry, rw, rh = self._anchor_tc_patch_bottom_right_flush(_rp)
            r_box0 = _visible_alpha_ltrb(_rp)
            if r_box0 is not None:
                r_opaque_l = int(r_box0[0])

        ex = ey = ew = eh = 0
        if self._tc_elapsed_asset is not None and self._bar_asset is not None:
            ep0 = self._tc_elapsed_asset
            eh0, ew0 = int(ep0.shape[0]), int(ep0.shape[1])
            e_box0 = _visible_alpha_ltrb(ep0)
            if e_box0 is None:
                el0, et0, er0, eb0 = 0, 0, ew0 - 1, eh0 - 1
            else:
                el0, et0, er0, eb0 = e_box0
            ecx = 0.5 * (float(el0) + float(er0))
            x_ideal = float(transition_x) - ecx
            ex = int(round(x_ideal))
            ex = max(0, min(ex, int(DESIGN_W) - ew0))
            ey = int(DESIGN_H) - eh0
            ey = max(0, ey)
            ew, eh = ew0, eh0
            elapsed_right = float(ex + float(er0))
            if r_opaque_l is not None:
                gap = float(rx + float(r_opaque_l)) - elapsed_right
                self._update_elapsed_fade_for_remaining_gap(gap)
            else:
                self._elapsed_fade_start_mono = None
        else:
            self._elapsed_fade_start_mono = None

        fade_a = self._elapsed_alpha()
        fill_bgr = self._progress_fill_bgr()
        sig = (
            tuple(fill_bgr),
            round(float(self._progress), 6),
            self._tc_played_text,
            self._tc_remaining_text,
            int(round(fade_a * 1000.0)),
        )
        if self._cached_blits is not None and self._cached_sig == sig:
            return self._cached_blits

        blits: list[DesignPatch] = []
        if self._bar_asset is not None:
            bx, by, bw, bh = self._anchor_bar_bottom_overlay_col_center(self._bar_asset)
            blits.append(DesignPatch(x=bx, y=by, w=bw, h=bh, bgra=self._bar_asset))
            accent = self._bar_asset.copy()
            amask = accent[:, :, 3] > 0
            accent[amask, 0] = int(fill_bgr[0])
            accent[amask, 1] = int(fill_bgr[1])
            accent[amask, 2] = int(fill_bgr[2])
            elapsed_frac = max(0.0, min(1.0, float(self._progress)))
            elapsed_w = int(round(elapsed_frac * float(bw)))
            elapsed_w = max(0, min(bw, elapsed_w))
            if elapsed_w < bw:
                accent[:, elapsed_w:, 3] = 0
            blits.append(DesignPatch(x=bx, y=by, w=bw, h=bh, bgra=accent))

        if self._tc_elapsed_asset is not None and self._bar_asset is not None:
            epatch = self._tc_elapsed_asset
            tc_a = float(_NOW_PLAYING_TC_OPACITY) * float(fade_a)
            if tc_a < 0.999:
                epatch = epatch.copy()
                epatch[:, :, 3] = np.clip(
                    epatch[:, :, 3].astype(np.float32) * tc_a, 0, 255
                ).astype(np.uint8)
            blits.append(DesignPatch(x=ex, y=ey, w=ew, h=eh, bgra=epatch))
            e_box = _visible_alpha_ltrb(self._tc_elapsed_asset)
            if e_box is not None:
                el, et, er, eb = e_box
                e_cx = float(ex) + 0.5 * (float(el) + float(er))
                e_cy = float(ey) + 0.5 * (float(et) + float(eb))
            else:
                e_cx = float(ex) + 0.5 * float(ew)
                e_cy = float(ey) + 0.5 * float(eh)
            etxt = self._text_patch(
                text=self._tc_played_text,
                center_x=int(round(e_cx)),
                center_y=int(round(e_cy)),
                alpha_mul=float(fade_a) * float(_NOW_PLAYING_TC_OPACITY),
            )
            blits.append(DesignPatch(x=0, y=0, w=DESIGN_W, h=DESIGN_H, bgra=etxt))

        if self._tc_remaining_asset is not None:
            rpatch = self._tc_remaining_asset
            rx, ry, rw, rh = self._anchor_tc_patch_bottom_right_flush(rpatch)
            if float(_NOW_PLAYING_TC_OPACITY) < 0.999:
                rpatch = rpatch.copy()
                rpatch[:, :, 3] = np.clip(
                    rpatch[:, :, 3].astype(np.float32) * float(_NOW_PLAYING_TC_OPACITY),
                    0,
                    255,
                ).astype(np.uint8)
            blits.append(DesignPatch(x=rx, y=ry, w=rw, h=rh, bgra=rpatch))
            r_box = _visible_alpha_ltrb(rpatch)
            if r_box is not None:
                rl, rt, rr, rb = r_box
                r_cx = float(rx) + 0.5 * (float(rl) + float(rr))
                r_cy = float(ry) + 0.5 * (float(rt) + float(rb))
            else:
                r_cx = float(rx) + 0.5 * float(rw)
                r_cy = float(ry) + 0.5 * float(rh)
            rtxt = self._text_patch(
                text=self._tc_remaining_text,
                center_x=int(round(r_cx)),
                center_y=int(round(r_cy)),
                alpha_mul=float(_NOW_PLAYING_TC_OPACITY),
            )
            blits.append(DesignPatch(x=0, y=0, w=DESIGN_W, h=DESIGN_H, bgra=rtxt))

        self._cached_sig = sig
        self._cached_blits = blits
        return blits

    def render(self, canvas_bgr: np.ndarray) -> None:
        if not self._now_playing_chrome_visible or self._theater_dim_suppressed:
            return
        ch, cw = canvas_bgr.shape[:2]
        for p in self.design_blits():
            x, y, w, h = p.x, p.y, p.w, p.h
            if w < 1 or h < 1:
                continue
            x0 = max(0, x)
            y0 = max(0, y)
            x1 = min(cw, x + w)
            y1 = min(ch, y + h)
            if x0 >= x1 or y0 >= y1:
                continue
            sx0 = x0 - x
            sy0 = y0 - y
            roi = canvas_bgr[y0:y1, x0:x1]
            patch = p.bgra[sy0 : sy0 + (y1 - y0), sx0 : sx0 + (x1 - x0)]
            roi[:] = alpha_blend_bgra_over_bgr(roi, patch)
