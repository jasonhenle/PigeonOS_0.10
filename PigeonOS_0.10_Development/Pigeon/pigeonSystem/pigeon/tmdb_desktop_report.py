"""Desktop spreadsheet report for TMDb events (CSV beside the Pigeon project).

Canonical files under ``Desktop/Pigeon/pigeonReport/``:

- ``tmdb_error_report.numbers`` — **TMDb errors** (Apple Numbers): Date, Time, App,
  ``raw_title``, ``layer_series_title``, supplemental context, and **My notes** (your column;
  Pigeon leaves it blank). Requires ``numbers-parser``. Without it, ``tmdb_error_report.csv``
  uses the same columns.
- ``pigeonTMDBReport.csv`` — wide operational log (quality SUCCESS/FAILURE rows, manual retries).

Changelog stays in ``CHANGELOG.md`` only; it is not copied into either CSV.

Override directory with env ``PIGEON_TMDB_REPORT_ROOT`` (absolute path to ``pigeonReport``).

Rows use ``record_type`` = ``tmdb_quality`` (scored events) or ``tmdb_retry`` (JSON in ``notes``).
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

try:
    from numbers_parser import Document as _NumbersDocument
except ImportError:
    _NumbersDocument = None  # type: ignore[misc, assignment]

def report_root() -> Path:
    raw = (os.environ.get("PIGEON_TMDB_REPORT_ROOT") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "Desktop" / "Pigeon" / "pigeonReport"


def csv_path() -> Path:
    return report_root() / "pigeonTMDBReport.csv"


def numbers_path() -> Path:
    return report_root() / "pigeonTMDBReport.numbers"


def error_report_numbers_path() -> Path:
    """Apple Numbers workbook for TMDb error rows."""
    return report_root() / "tmdb_error_report.numbers"


def error_report_csv_path() -> Path:
    """Fallback CSV when ``numbers-parser`` is not installed."""
    return report_root() / "tmdb_error_report.csv"


_ERROR_REPORT_COLUMNS = (
    "Date",
    "Time",
    "App",
    "raw_title",
    "layer_series_title",
    "supplemental",
    "My notes",
)


def _prepare_numbers_error_table(t: Any) -> None:
    """Ensure column count and header labels; expand legacy tables when new columns are added."""
    ncols = len(_ERROR_REPORT_COLUMNS)
    if t.num_cols < ncols:
        t.add_column(ncols - t.num_cols, start_col=None)
    for c, name in enumerate(_ERROR_REPORT_COLUMNS):
        if c >= t.num_cols:
            break
        cur = str(getattr(t.cell(0, c), "value", "") or "").strip()
        if not cur:
            t.write(0, c, name)


def _migrate_error_report_csv_if_needed() -> None:
    """Pad older CSVs with ``My notes`` column so row lengths stay consistent."""
    p = error_report_csv_path()
    if not p.is_file() or p.stat().st_size == 0:
        return
    try:
        with p.open("r", encoding="utf-8", newline="") as rf:
            rows = list(csv.reader(rf))
        if not rows or str(rows[0][0] or "").strip() != "Date":
            return
        n = len(_ERROR_REPORT_COLUMNS)
        hdr = list(rows[0])
        if len(hdr) >= n:
            return
        old_len = len(hdr)
        hdr.extend([""] * (n - old_len))
        for i in range(old_len, n):
            hdr[i] = _ERROR_REPORT_COLUMNS[i]
        rows[0] = hdr
        for ri in range(1, len(rows)):
            r = list(rows[ri])
            if len(r) < n:
                r.extend([""] * (n - len(r)))
            rows[ri] = r[:n]
        with p.open("w", encoding="utf-8", newline="") as wf:
            csv.writer(wf).writerows(rows)
    except Exception:
        pass


def _escape_report_field(s: str | None) -> str:
    return (s or "").replace("\\", "\\\\").replace("\t", " ").replace("\r", " ").replace("\n", " ")


def _extract_raw_title_columns(md: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return ``(raw_title, layer_series_title)`` from metadata via :class:`~pigeon.raw_title.RawTitle`."""
    if not isinstance(md, dict):
        return "", ""
    try:
        from pigeon.raw_title import raw_title_from_metadata_dict

        rt = raw_title_from_metadata_dict(md)
        return (
            (rt.raw_title or "").strip(),
            (rt.layer_series_title or "").strip(),
        )
    except Exception:
        return (str(md.get("title") or "").strip(), "")


def _app_label(md: Mapping[str, Any] | None, badge: Mapping[str, Any] | None) -> str:
    if isinstance(md, dict):
        app_name = str(md.get("app_name") or "").strip()
        if app_name:
            return app_name
        app_id = str(md.get("app_id") or "").strip()
        if app_id:
            return app_id
    if isinstance(badge, dict):
        lab = str(badge.get("label") or "").strip()
        if lab:
            return lab
        fn = str(badge.get("filename") or "").strip()
        if fn:
            return fn
    return "(unknown)"


def _append_error_report_row_numbers(values: list[str]) -> None:
    p = error_report_numbers_path()
    d = _NumbersDocument(str(p))
    t = d.sheets[0].tables[0]
    _prepare_numbers_error_table(t)
    ncols = len(_ERROR_REPORT_COLUMNS)
    t.add_row(1)
    r = t.num_rows - 1
    for c, val in enumerate(values[:ncols]):
        t.write(r, c, val)
    d.save(str(p))


def _append_error_report_row_csv(values: list[str]) -> None:
    p = error_report_csv_path()
    _migrate_error_report_csv_if_needed()
    new_file = not p.is_file() or p.stat().st_size == 0
    with p.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(list(_ERROR_REPORT_COLUMNS))
        w.writerow(values[: len(_ERROR_REPORT_COLUMNS)])


def append_tmdb_error_event(
    *,
    last_metadata: Mapping[str, Any] | None,
    streaming_badge_state: Mapping[str, Any] | None,
    supplemental_metadata: str | None = None,
) -> None:
    """Append one error row (Numbers workbook, or CSV fallback).

    Called on TMDb no-match, worker exceptions surfaced as no-match, and quality FAILURE scoring.
    """
    try:
        ensure_error_report_initialized()
        raw_title_s, layer_series_s = _extract_raw_title_columns(last_metadata)
        sup = ""
        if supplemental_metadata:
            sup = _escape_report_field(supplemental_metadata).strip()
        date_s = time.strftime("%Y-%m-%d", time.localtime())
        time_s = time.strftime("%H:%M:%S", time.localtime())
        row = [
            date_s,
            time_s,
            _app_label(last_metadata, streaming_badge_state),
            raw_title_s,
            layer_series_s,
            sup,
            "",  # My notes — for you in Numbers only; Pigeon does not overwrite this once written.
        ]
        if _NumbersDocument is not None:
            _append_error_report_row_numbers(row)
        else:
            _append_error_report_row_csv(row)
    except Exception:
        pass


_CSV_FIELDS = (
    "record_type",
    "timestamp_local",
    "outcome",
    "tmdb_title_key",
    "display_title",
    "fetch_summary_head",
    "app_streaming_context",
    "raw_title_context",
    "notes",
)


def _ensure_parent() -> None:
    report_root().mkdir(parents=True, exist_ok=True)


def ensure_error_report_initialized() -> None:
    """Create an empty error workbook (``.numbers``) or CSV fallback with headers."""
    try:
        _ensure_parent()
        if _NumbersDocument is not None:
            p = error_report_numbers_path()
            if p.is_file() and p.stat().st_size > 0:
                try:
                    d = _NumbersDocument(str(p))
                    t = d.sheets[0].tables[0]
                    if t.num_rows >= 1:
                        c0 = t.cell(0, 0)
                        if str(getattr(c0, "value", "") or "").strip() == "Date":
                            _prepare_numbers_error_table(t)
                            d.save(str(p))
                            return
                except Exception:
                    pass
            n = len(_ERROR_REPORT_COLUMNS)
            d = _NumbersDocument(num_rows=1, num_cols=n)
            t = d.sheets[0].tables[0]
            for c, h in enumerate(_ERROR_REPORT_COLUMNS):
                t.write(0, c, h)
            d.save(str(p))
            return
        p = error_report_csv_path()
        if p.is_file() and p.stat().st_size > 0:
            try:
                _migrate_error_report_csv_if_needed()
                with p.open("r", encoding="utf-8", newline="") as rf:
                    r = csv.reader(rf)
                    row1 = next(r, None)
                    if row1 and len(row1) >= 1 and row1[0] == "Date":
                        return
            except Exception:
                pass
        with p.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f).writerow(list(_ERROR_REPORT_COLUMNS))
    except Exception:
        pass


def ensure_error_report_csv_initialized() -> None:
    """Backward-compatible alias — initializes Numbers or CSV error report."""
    ensure_error_report_initialized()


def ensure_csv_initialized() -> None:
    """Create ``pigeonTMDBReport.csv`` with header only (no changelog lines)."""
    _ensure_parent()
    p = csv_path()
    if p.is_file() and p.stat().st_size > 0:
        try:
            with p.open("r", encoding="utf-8", newline="") as rf:
                r = csv.reader(rf)
                row1 = next(r, None)
                if row1 and row1[0] == "record_type":
                    ensure_error_report_initialized()
                    return
        except Exception:
            pass

    with p.open("w", encoding="utf-8", newline="") as f:
        csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore").writeheader()
    ensure_error_report_initialized()


def append_tmdb_quality_row(
    *,
    outcome: str,
    title_key: str | None,
    display_title: str | None,
    fetch_summary_head: str,
    app_streaming_context: str,
    raw_title_context: str,
) -> None:
    """Append one scored quality row (same conceptual fields as the ~/.pigeon log line)."""
    try:
        ensure_csv_initialized()
        _ensure_parent()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        out_u = str(outcome or "").strip().upper()
        if out_u not in ("SUCCESS", "FAILURE"):
            out_u = "UNKNOWN"
        row = {
            "record_type": "tmdb_quality",
            "timestamp_local": ts,
            "outcome": out_u,
            "tmdb_title_key": title_key or "",
            "display_title": display_title or "",
            "fetch_summary_head": fetch_summary_head or "",
            "app_streaming_context": app_streaming_context or "",
            "raw_title_context": raw_title_context or "",
            "notes": "",
        }
        with csv_path().open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore").writerow(row)
    except Exception:
        pass


def append_tmdb_retry_row(entry: dict) -> None:
    """Append one manual retry log entry (JSON in ``notes``)."""
    try:
        ensure_csv_initialized()
        _ensure_parent()
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        row = {
            "record_type": "tmdb_retry",
            "timestamp_local": ts,
            "outcome": "",
            "tmdb_title_key": "",
            "display_title": "",
            "fetch_summary_head": "",
            "app_streaming_context": "",
            "raw_title_context": "",
            "notes": json.dumps(entry, ensure_ascii=False),
        }
        with csv_path().open("a", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=_CSV_FIELDS, extrasaction="ignore").writerow(row)
    except Exception:
        pass
