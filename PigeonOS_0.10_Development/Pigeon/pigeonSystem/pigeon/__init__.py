"""Pigeon modular UI: design space, grid overlay, and widget shell."""

from pigeon.design import (
    DESIGN_H,
    DESIGN_W,
    GRID_COLS,
    GRID_ROWS,
    GridGeometry,
    get_grid_geometry,
    rect_for_span_at_cell,
    rect_for_span_from_origin,
)
from pigeon.widget_protocol import Widget
from pigeon.widget_shell import WidgetShell
from pigeon.widgets.clock_calendar import ClockCalendarWidget
from pigeon.widgets.poster_art import PosterArtWidget

__all__ = [
    "DESIGN_W",
    "DESIGN_H",
    "GRID_COLS",
    "GRID_ROWS",
    "GridGeometry",
    "get_grid_geometry",
    "rect_for_span_at_cell",
    "rect_for_span_from_origin",
    "Widget",
    "WidgetShell",
    "ClockCalendarWidget",
    "PosterArtWidget",
]
