"""Append-only JSONL log for manual TMDb artwork retries (tuning search rules)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pigeon.runtime_paths import pigeon_state_dir

LOG_FILENAME = "tmdb_retry_log.jsonl"


def log_path() -> Path:
    return pigeon_state_dir() / LOG_FILENAME


def append_entry(entry: dict) -> None:
    p = log_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = dict(entry)
    if "t" not in rec:
        rec["t"] = datetime.now(timezone.utc).isoformat()
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    try:
        from pigeon.tmdb_desktop_report import append_tmdb_retry_row

        append_tmdb_retry_row(rec)
    except Exception:
        pass


def read_tail_lines(max_lines: int = 120) -> list[str]:
    p = log_path()
    if not p.is_file():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()
    return lines[-max_lines:]
