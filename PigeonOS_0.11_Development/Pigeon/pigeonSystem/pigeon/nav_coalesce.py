"""Collapse burst Left/Right / rotary ticks into one UI paint.

Encoder boards can emit tens of detents before Tk finishes a single full-frame
compose. Applying every tick as ``render_once()`` serialized those rasters and
capped navigation at about one control per second. This coalescer:

- lets ``navigate()`` run immediately (index only)
- paints once after the current Tk event batch (``after_idle``)
- rate-limits paints to ~60 Hz so PhotoImage uploads cannot stampede
- stays "hot" briefly so overlays that cost tens of ms on the Pi can wait
  until the knob stops
"""

from __future__ import annotations

import time
from collections.abc import Callable


class NavPaintCoalescer:
    def __init__(
        self,
        *,
        after_idle: Callable[[Callable[[], None]], object],
        after: Callable[[int, Callable[[], None]], object],
        cancel: Callable[[object], None],
        paint: Callable[[], None],
        min_interval_s: float = 1.0 / 60.0,
        settle_s: float = 0.09,
        hot_s: float = 0.14,
    ) -> None:
        self._after_idle = after_idle
        self._after = after
        self._cancel = cancel
        self._paint = paint
        self.min_interval_s = float(min_interval_s)
        self.settle_s = float(settle_s)
        self.hot_s = float(hot_s)
        self.pending = False
        self.hot_until = 0.0
        self._settle_id: object | None = None
        self._rate_id: object | None = None
        self._last_paint = 0.0

    def is_hot(self) -> bool:
        return time.monotonic() < self.hot_until

    def request(self) -> None:
        now = time.monotonic()
        self.hot_until = now + max(self.hot_s, self.settle_s)
        self._arm_settle()
        if self.pending:
            return
        self.pending = True
        self._after_idle(self._flush)

    def _cancel_id(self, handle: object | None) -> None:
        if handle is None:
            return
        try:
            self._cancel(handle)
        except Exception:
            pass

    def _arm_settle(self) -> None:
        self._cancel_id(self._settle_id)
        self._settle_id = self._after(max(1, int(self.settle_s * 1000.0)), self._settle)

    def _flush(self) -> None:
        self.pending = False
        self._rate_id = None
        now = time.monotonic()
        wait = self.min_interval_s - (now - self._last_paint)
        if wait > 0.001:
            self.pending = True
            self._rate_id = self._after(max(1, int(wait * 1000.0)), self._flush)
            return
        self._last_paint = now
        self._paint()

    def _settle(self) -> None:
        self._settle_id = None
        self.hot_until = 0.0
        self._paint()
