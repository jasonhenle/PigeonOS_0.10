"""Turn OCR lines into identifying clues. No camera, no Tesseract.

OCR is one more metadata source next to pyatv / Roku. This module only
*reads text* and picks fields Pigeon can compare or search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

# UI chrome we never treat as a title.
_CHROME = {
    "options",
    "home",
    "shows",
    "movies",
    "my netflix",
    "my list",
    "play",
    "extras",
    "related",
    "info",
    "chapters",
    "continue watching",
    "trailers",
    "more",
    "other",
    "new on netflix",
    "gems for you",
    "leaving soon",
    "documentaries",
    "comedies",
    "suggested",
    "extras",
    "details",
    "remaining",
    "permission",
    "bts",
}

_SEASON_EP = re.compile(
    r"(?i)(?:\$|s|season)\s*(\d{1,2})\s*[,.]?\s*(?:e|ep|episode)\s*(\d{1,3})"
)
_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_RUNTIME = re.compile(r"(?i)\b(\d{1,2})\s*h(?:r)?\s*(\d{1,2})\s*m|\b(\d{2,3})\s*min\b")


@dataclass
class OcrClues:
    """Identifying bits pulled from one OCR pass."""

    lines: list[str] = field(default_factory=list)
    title_guess: str | None = None
    season: int | None = None
    episode: int | None = None
    year: int | None = None
    runtime_min: int | None = None
    extras: list[str] = field(default_factory=list)
    reason: str = ""

    def as_metadata_fields(self) -> dict[str, Any]:
        return {
            "ocr_title": self.title_guess or "",
            "ocr_lines": list(self.lines),
            "ocr_season": self.season,
            "ocr_episode": self.episode,
            "ocr_year": self.year,
            "ocr_runtime_min": self.runtime_min,
            "ocr_extras": list(self.extras),
            "ocr_reason": self.reason,
        }


def clues_from_lines(lines: list[str], reason: str = "") -> OcrClues:
    """Pick title / S-E / year / runtime from grouped OCR lines."""
    cleaned = [_clean_line(x) for x in lines]
    cleaned = [x for x in cleaned if x]
    clues = OcrClues(lines=cleaned, reason=reason)
    for line in cleaned:
        se = _SEASON_EP.search(line)
        if se and clues.season is None:
            clues.season = int(se.group(1))
            clues.episode = int(se.group(2))
        year = _YEAR.search(line)
        if year and clues.year is None:
            clues.year = int(year.group(1))
        run = _RUNTIME.search(line)
        if run and clues.runtime_min is None:
            if run.group(1) and run.group(2):
                clues.runtime_min = int(run.group(1)) * 60 + int(run.group(2))
            elif run.group(3):
                clues.runtime_min = int(run.group(3))
    clues.title_guess = _guess_title(cleaned)
    clues.extras = [
        line
        for line in cleaned
        if (
            line != clues.title_guess
            and not _is_chrome(line)
            and not _looks_like_synopsis(line)
            and not looks_like_ocr_junk(line)
        )
    ][:8]
    parts: list[str] = []
    if clues.title_guess:
        parts.append(clues.title_guess)
    for extra in clues.extras:
        if not _is_title_row(extra):
            continue
        blob = " ".join(parts).casefold()
        if extra.casefold() in blob:
            continue
        parts.append(extra)
    if parts:
        clues.title_guess = " ".join(parts)
    return clues


def clues_agree_with_metadata(clues: OcrClues, metadata: Mapping[str, Any] | None) -> bool:
    """True when OCR and Apple TV / Roku metadata look like the same title."""
    if not metadata or not clues.title_guess:
        return False
    guess_n = _norm(clues.title_guess)
    guess_tok = _tokens(clues.title_guess)
    if len(guess_n) < 3:
        return False
    for key in ("query", "title", "series_name", "artist", "ocr_title"):
        field = str(metadata.get(key) or "").strip()
        if not field:
            continue
        other = _norm(field)
        if not other:
            continue
        if guess_n in other or other in guess_n:
            return True
        if len(guess_tok & _tokens(field)) >= 2:
            return True
    return False


def _collapse_repeated_words(text: str) -> str:
    """``TAYLOR TAYLOR SWIFT SWIFT`` → ``TAYLOR SWIFT`` (multi-pass OCR)."""
    words = text.split()
    out: list[str] = []
    for word in words:
        if out and word.casefold() == out[-1].casefold():
            continue
        out.append(word)
    return " ".join(out)


def looks_like_ocr_junk(line: str) -> bool:
    """True for glyph noise that must not be sent to TMDb (``S S Sh``)."""
    words = [re.sub(r"[^A-Za-z0-9]", "", w) for w in (line or "").split()]
    words = [w for w in words if w]
    if not words:
        return True
    tiny = sum(1 for w in words if len(w) <= 2)
    if tiny >= 2 and tiny >= len(words) * 0.5:
        return True
    if len(words) <= 2 and all(len(w) <= 3 for w in words):
        return True
    letters = re.sub(r"[^A-Za-z]", "", line or "")
    return len(letters) < 3


def _clean_line(text: str) -> str:
    return _collapse_repeated_words(re.sub(r"\s+", " ", (text or "").strip()))


def _is_chrome(line: str) -> bool:
    low = line.lower().strip(" .:-")
    if low in _CHROME:
        return True
    toks = [re.sub(r"[^a-z0-9]+", "", t) for t in low.split()]
    toks = [t for t in toks if t]
    if toks and all(t in _CHROME for t in toks):
        return True
    if "english" in low and "original" in low:
        return True
    if low.startswith("home shows") or "my netflix" in low:
        return True
    if any(w in low for w in ("remaining", "ultrahd", "hdr10", "dolbyatmos")):
        return True
    return False


def _is_title_row(line: str) -> bool:
    """True for a short stacked title row, not year / runtime / genre leftovers."""
    if _YEAR.search(line) or _RUNTIME.search(line) or "," in line:
        return False
    words = line.split()
    return 6 <= len(line) <= 48 and 1 <= len(words) <= 6


def _looks_like_synopsis(line: str) -> bool:
    if len(line) > 72:
        return True
    return line.endswith(".") and len(line.split()) >= 8


def _guess_title(lines: list[str]) -> str | None:
    candidates: list[str] = []
    for line in lines:
        if _is_chrome(line) or _looks_like_synopsis(line) or looks_like_ocr_junk(line):
            continue
        if _SEASON_EP.search(line) and len(line) > 28:
            # "S1, E7 The Flamekeepers: …" — keep the episode-title side.
            rest = _SEASON_EP.sub("", line).strip(" :,-")
            if rest and not _looks_like_synopsis(rest):
                candidates.append(rest)
            continue
        if _YEAR.fullmatch(line):
            continue
        candidates.append(line)
    if not candidates:
        return None
    candidates.sort(key=lambda s: (-_title_score(s), len(s)))
    return candidates[0]


def _title_score(line: str) -> int:
    n = len(line)
    words = [w for w in line.split() if len(re.sub(r"[^A-Za-z0-9]", "", w)) >= 3]
    score = 0
    if 6 <= n <= 48:
        score += 20
    elif n <= 64:
        score += 8
    if 2 <= len(words) <= 8:
        score += 16
    elif len(words) == 1 and 4 <= len(words[0]) <= 24:
        score += 10
    if ":" in line and n <= 50:
        score += 8
    if line[:1].isupper():
        score += 3
    if _SEASON_EP.search(line):
        score -= 15
    if looks_like_ocr_junk(line):
        score -= 80
    return score


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", text.lower()))
