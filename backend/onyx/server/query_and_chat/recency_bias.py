"""Hybrid-hit recency multiply. Hypothesis curve — not a fixed product constant."""

from __future__ import annotations

from datetime import datetime, timezone

from onyx.configs.app_configs import ENABLE_RECENCY_BIAS
from onyx.context.search.models import SearchDoc

# Hypothesis to test, not a platform constant. 45-day half-life, 0.70 floor:
# this week vs last quarter can reorder close hybrid scores; a year-old runbook
# cannot fall below 70% of its hybrid score.
RECENCY_HALF_LIFE_DAYS = 45.0
RECENCY_FLOOR = 0.70


def should_apply_recency_bias(retrieval: str, query: str) -> bool:
    """True only for a non-empty hybrid Ask search with ENABLE_RECENCY_BIAS on.

    Keyword and empty-query (random sample) never apply recency.
    """
    return ENABLE_RECENCY_BIAS and retrieval == "hybrid" and bool(query.strip())


def recency_multiplier(
    updated_at: datetime | None,
    *,
    now: datetime | None = None,
) -> float:
    """Return the age multiplier for one hit.

    Missing ``updated_at`` returns 1.0 (hybrid order unchanged — do not invent
    a date). Future timestamps are treated as age 0.
    """
    if updated_at is None:
        return 1.0
    current = now or datetime.now(timezone.utc)
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (current - updated_at).total_seconds() / 86400.0)
    return max(RECENCY_FLOOR, 0.5 ** (age_days / RECENCY_HALF_LIFE_DAYS))


def maybe_apply_recency_bias(documents: list[SearchDoc]) -> list[SearchDoc]:
    """Re-sort unique hybrid hits by ``score * recency_multiplier(updated_at)``.

    Skips when the flag is off or the list is empty. Stable for equal keys so
    hybrid order is kept when dates are missing or multipliers match. Does not
    raise.
    """
    if not ENABLE_RECENCY_BIAS or not documents:
        return documents
    now = datetime.now(timezone.utc)
    return sorted(
        documents,
        key=lambda doc: (doc.score if doc.score is not None else 0.0)
        * recency_multiplier(doc.updated_at, now=now),
        reverse=True,
    )
