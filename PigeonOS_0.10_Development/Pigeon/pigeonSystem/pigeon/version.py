"""Pigeon semantic version for UI/build labeling."""

from __future__ import annotations

MAJOR = 0
MINOR = 10
PATCH = 0


def version_tuple() -> tuple[int, int, int]:
    return (MAJOR, MINOR, PATCH)


def version_string() -> str:
    return f"{MAJOR}.{MINOR}.{PATCH}"
