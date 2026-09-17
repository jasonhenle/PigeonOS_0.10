"""Widget contract: each widget draws into a shared DESIGN_W×DESIGN_H BGR canvas."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class Widget(Protocol):
    """A widget occupies a rectangle of grid squares on the global 19×8 grid."""

    @property
    def grid_span(self) -> tuple[int, int]:
        """(squares_wide, squares_tall), e.g. (6, 3) for six columns × three rows."""

    def render(self, canvas_bgr: np.ndarray) -> None:
        """Draw into the design canvas within ``grid_span`` from the widget's top-left anchor cell."""
