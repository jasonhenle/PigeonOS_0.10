"""TMDb logo widget (cached in pigeonReFormattedMedia).

Renders the cached ``{Title}_Logo.*`` asset into a fixed grid rectangle with aspect-preserving scale.
"""

from __future__ import annotations

from typing import Literal

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from pigeon.compositing import alpha_blend_bgra_over_bgr, cv_resize_interp
from pigeon.design import rect_for_span_at_cell, rect_for_span_top_right_at_cell
from pigeon.image_ui_protocol import load_image_bgra
from pigeon.font_paths import resolve_ui_font_bold, resolve_ui_font_medium
from pigeon.media_cache import ASSET_LOGO_EN, find_cached_reformatted_asset


class TmdbLogoWidget:
    """Aspect-preserving title logo in a configurable grid span (default 9×3)."""

    def __init__(
        self,
        *,
        anchor_row: int | float = 3,
        anchor_col: int = 6,
        span_wide: int = 9,
        span_tall: int = 3,
        fit_scale: float = 1.0,
        top_right_col_1based: float | None = None,
        vertical_align: Literal["center", "top"] = "center",
    ) -> None:
        self._anchor_row = anchor_row
        self._anchor_col = anchor_col
        self._top_right_col = top_right_col_1based
        self._span = (max(1, int(span_wide)), max(1, int(span_tall)))
        self._fit_scale = max(0.2, min(1.0, float(fit_scale)))
        self._vertical_align: Literal["center", "top"] = vertical_align
        self._cached_title_key: str | None = None
        self._cached_display_title: str | None = None
        self._cached_patch_wh: tuple[int, int] | None = None
        self._cached_patch_bgra: np.ndarray | None = None

    @property
    def grid_span(self) -> tuple[int, int]:
        return self._span

    @property
    def grid_anchor(self) -> tuple[int | float, int]:
        return (self._anchor_row, self._anchor_col)

    def design_rect(self) -> tuple[int, int, int, int]:
        """Design-pixel (x, y, w, h) for the logo span (top-left or top-right anchored)."""
        sw, sh = self._span
        if self._top_right_col is not None:
            return rect_for_span_top_right_at_cell(
                sw,
                sh,
                row_1based=self._anchor_row,
                col_right_1based=float(self._top_right_col),
            )
        return rect_for_span_at_cell(
            sw,
            sh,
            row_1based=self._anchor_row,
            col_1based=self._anchor_col,
        )

    def clear_cache(self) -> None:
        self._cached_title_key = None
        self._cached_display_title = None
        self._cached_patch_wh = None
        self._cached_patch_bgra = None

    def _pick_font_path(self) -> str | None:
        return resolve_ui_font_medium() or resolve_ui_font_bold()

    def _fit_font_to_box(self, text: str, max_w: int, max_h: int) -> ImageFont.ImageFont:
        path = self._pick_font_path()
        lo, hi = 6, max(max_h * 4, max_w * 2, 400)
        best = ImageFont.load_default()
        if path:
            try:
                best = ImageFont.truetype(path, 6)
            except OSError:
                pass
        while lo <= hi:
            mid = (lo + hi) // 2
            if path:
                try:
                    f = ImageFont.truetype(path, mid)
                except OSError:
                    f = ImageFont.load_default()
            else:
                f = ImageFont.load_default()
            tw, th = f.getbbox(text)[2:]
            if tw <= max_w and th <= max_h:
                best = f
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    def _render_text_fallback_patch(self, text: str, w: int, h: int) -> np.ndarray:
        if not text:
            return np.zeros((h, w, 4), dtype=np.uint8)
        pad = max(6, int(round(min(w, h) * 0.07)))
        mw = max(4, w - 2 * pad)
        mh = max(4, h - 2 * pad)
        font = self._fit_font_to_box(text, mw, mh)
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        tw, th = r - l, b - t
        x = (w // 2) - (tw // 2) - l
        if self._vertical_align == "top":
            y = -t
        else:
            y = (h // 2) - (th // 2) - t
        draw.text((x, y), text, font=font, fill=(255, 255, 255, 255))
        rgba = np.asarray(img)
        return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)

    def _render_logo_patch(self, title_key_str: str, w: int, h: int, *, display_title: str | None) -> np.ndarray:
        src_path = find_cached_reformatted_asset(title_key_str, ASSET_LOGO_EN)
        if src_path is None:
            return self._render_text_fallback_patch(display_title or "", w, h)
        src = load_image_bgra(src_path)
        if src is None or src.size == 0:
            return self._render_text_fallback_patch(display_title or "", w, h)
        sh, sw = src.shape[:2]
        if sh < 1 or sw < 1:
            return self._render_text_fallback_patch(display_title or "", w, h)
        # Center using the *visible* logo bounds, not raw file bounds, so transparent side padding
        # in cached assets does not bias placement left/right.
        if src.ndim == 3 and src.shape[2] >= 4:
            a = src[:, :, 3]
            nz = a > 8
            if np.any(nz):
                ys, xs = np.nonzero(nz)
                y0 = int(ys.min())
                y1 = int(ys.max()) + 1
                x0 = int(xs.min())
                x1 = int(xs.max()) + 1
                src = src[y0:y1, x0:x1]
                sh, sw = src.shape[:2]
                if sh < 1 or sw < 1:
                    return self._render_text_fallback_patch(display_title or "", w, h)

        # Uniform "contain" scale: fit fully inside the rectangle; no cropping.
        scale = min(w / float(sw), h / float(sh)) * self._fit_scale
        tw = max(1, int(round(sw * scale)))
        th = max(1, int(round(sh * scale)))
        resized = cv2.resize(src, (tw, th), interpolation=cv_resize_interp(sw, sh, tw, th))

        out = np.zeros((h, w, 4), dtype=np.uint8)
        x0 = max(0, (w - tw) // 2)
        if self._vertical_align == "top":
            y0 = 0
        else:
            y0 = max(0, (h - th) // 2)
        out[y0 : y0 + th, x0 : x0 + tw] = resized
        return out

    def bgra_patch_from_source_bgra(
        self,
        src_bgra: np.ndarray | None,
        *,
        patch_wh: tuple[int, int] | None = None,
    ) -> np.ndarray:
        """Aspect-fit an arbitrary BGRA source into the widget's rect (transparent pad).

        Used by the content-logo slot to surface the streaming-app logo when no TMDb
        match is available — same placement rules as :meth:`bgra_patch_for_title` but
        without consulting the cached-asset store. Returns a fully-transparent patch
        when ``src_bgra`` is unusable.
        """
        if patch_wh is not None:
            w, h = max(1, int(patch_wh[0])), max(1, int(patch_wh[1]))
        else:
            _wx, _wy, w, h = self.design_rect()
        if src_bgra is None or src_bgra.size == 0 or src_bgra.ndim != 3:
            return np.zeros((h, w, 4), dtype=np.uint8)
        src = src_bgra
        if src.shape[2] == 3:
            src = cv2.cvtColor(src, cv2.COLOR_BGR2BGRA)
        elif src.shape[2] != 4:
            return np.zeros((h, w, 4), dtype=np.uint8)
        sh, sw = src.shape[:2]
        if sh < 1 or sw < 1:
            return np.zeros((h, w, 4), dtype=np.uint8)
        a = src[:, :, 3]
        nz = a > 8
        if np.any(nz):
            ys, xs = np.nonzero(nz)
            y0 = int(ys.min())
            y1 = int(ys.max()) + 1
            x0 = int(xs.min())
            x1 = int(xs.max()) + 1
            src = src[y0:y1, x0:x1]
            sh, sw = src.shape[:2]
            if sh < 1 or sw < 1:
                return np.zeros((h, w, 4), dtype=np.uint8)
        scale = min(w / float(sw), h / float(sh)) * self._fit_scale
        tw = max(1, int(round(sw * scale)))
        th = max(1, int(round(sh * scale)))
        resized = cv2.resize(src, (tw, th), interpolation=cv_resize_interp(sw, sh, tw, th))
        out = np.zeros((h, w, 4), dtype=np.uint8)
        x0 = max(0, (w - tw) // 2)
        if self._vertical_align == "top":
            y0 = 0
        else:
            y0 = max(0, (h - th) // 2)
        out[y0 : y0 + th, x0 : x0 + tw] = resized
        return out

    def bgra_patch_for_title(
        self,
        title_key_str: str,
        *,
        display_title: str | None = None,
        patch_wh: tuple[int, int] | None = None,
    ) -> np.ndarray:
        """Rasterize at ``patch_wh`` when set, else at ``design_rect()`` (larger ``patch_wh`` = sharper downscale)."""
        if patch_wh is not None:
            w, h = max(1, int(patch_wh[0])), max(1, int(patch_wh[1]))
        else:
            _wx, _wy, w, h = self.design_rect()
        disp = display_title or ""
        if (
            self._cached_patch_bgra is not None
            and self._cached_title_key == title_key_str
            and self._cached_display_title == disp
            and self._cached_patch_wh == (w, h)
        ):
            return self._cached_patch_bgra
        self._cached_patch_bgra = self._render_logo_patch(title_key_str, w, h, display_title=display_title)
        self._cached_title_key = title_key_str
        self._cached_display_title = disp
        self._cached_patch_wh = (w, h)
        return self._cached_patch_bgra

    def render(self, canvas_bgr: np.ndarray, *, title_key_str: str | None, display_title: str | None = None) -> None:
        if not title_key_str:
            return
        wx, wy, w, h = self.design_rect()
        roi = canvas_bgr[wy : wy + h, wx : wx + w]
        patch = self.bgra_patch_for_title(title_key_str, display_title=display_title)
        roi[:] = alpha_blend_bgra_over_bgr(roi, patch)

