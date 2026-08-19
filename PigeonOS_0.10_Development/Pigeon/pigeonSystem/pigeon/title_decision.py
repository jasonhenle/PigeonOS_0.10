"""Remember why Pigeon chose a title — explainable, durable decisions."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


@dataclass
class TitleDecision:
    """One identity / TMDb choice Pigeon made, with a human-readable why."""

    title: str
    source: str
    reason: str
    at: float = field(default_factory=time.time)
    extras: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["at"] = float(self.at)
        return out

    def explain(self) -> str:
        src = (self.source or "unknown").strip() or "unknown"
        why = (self.reason or "").strip() or "no reason recorded"
        title = (self.title or "").strip() or "(empty)"
        return f"{title} ← {src}: {why}"


_HISTORY_MAX = 24
_history: list[TitleDecision] = []
_latest: TitleDecision | None = None


def reset_title_decisions() -> None:
    """Clear remembered decisions (tests / factory reset)."""
    global _history, _latest
    _history = []
    _latest = None


def record_title_decision(
    title: str,
    *,
    source: str,
    reason: str,
    extras: Mapping[str, Any] | None = None,
) -> TitleDecision:
    """Store a title decision and return it."""
    global _latest
    decision = TitleDecision(
        title=str(title or "").strip(),
        source=str(source or "").strip().lower() or "unknown",
        reason=str(reason or "").strip() or "unspecified",
        at=time.time(),
        extras=dict(extras or {}),
    )
    _latest = decision
    _history.append(decision)
    if len(_history) > _HISTORY_MAX:
        del _history[: len(_history) - _HISTORY_MAX]
    return decision


def latest_title_decision() -> TitleDecision | None:
    return _latest


def title_decision_history() -> list[TitleDecision]:
    return list(_history)


def apply_decision_to_metadata(
    metadata: dict[str, Any], decision: TitleDecision
) -> None:
    """Copy the latest decision onto live metadata for View 4 / inspectors."""
    metadata["title_decision"] = decision.explain()
    metadata["title_decision_source"] = decision.source
    metadata["title_decision_reason"] = decision.reason
    metadata["title_decision_title"] = decision.title
    metadata["title_decision_at"] = float(decision.at)


def decision_from_metadata(metadata: Mapping[str, Any] | None) -> str:
    """Best available explanation string from metadata or memory."""
    md = metadata if isinstance(metadata, dict) else {}
    cached = str(md.get("title_decision") or "").strip()
    if cached:
        return cached
    if _latest is not None:
        return _latest.explain()
    return ""
