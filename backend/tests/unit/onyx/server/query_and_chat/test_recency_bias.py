"""Recency multiply: missing dates unchanged, 45-day half-life, 0.70 floor."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from onyx.configs.constants import DocumentSource
from onyx.context.search.models import SearchDoc
from onyx.server.query_and_chat.recency_bias import (
    RECENCY_FLOOR,
    RECENCY_HALF_LIFE_DAYS,
    maybe_apply_recency_bias,
    recency_multiplier,
    should_apply_recency_bias,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def _doc(doc_id: str, score: float | None, updated_at: datetime | None) -> SearchDoc:
    return SearchDoc(
        document_id=doc_id,
        chunk_ind=0,
        semantic_identifier=doc_id,
        blurb=doc_id,
        source_type=DocumentSource.JIRA,
        boost=0,
        hidden=False,
        metadata={},
        match_highlights=[],
        score=score,
        updated_at=updated_at,
    )


def test_should_apply_requires_flag_hybrid_and_query() -> None:
    with patch("onyx.server.query_and_chat.recency_bias.ENABLE_RECENCY_BIAS", False):
        assert should_apply_recency_bias("hybrid", "blocker") is False
    with patch("onyx.server.query_and_chat.recency_bias.ENABLE_RECENCY_BIAS", True):
        assert should_apply_recency_bias("hybrid", "blocker") is True
        assert should_apply_recency_bias("keyword", "blocker") is False
        assert should_apply_recency_bias("hybrid", "  ") is False


def test_missing_date_is_identity_multiplier() -> None:
    assert recency_multiplier(None, now=NOW) == 1.0


def test_half_life_and_floor() -> None:
    half = recency_multiplier(NOW - timedelta(days=RECENCY_HALF_LIFE_DAYS), now=NOW)
    assert abs(half - 0.5) < 1e-9
    year = recency_multiplier(NOW - timedelta(days=365), now=NOW)
    assert year == RECENCY_FLOOR
    week = recency_multiplier(NOW - timedelta(days=7), now=NOW)
    assert 0.89 < week < 0.91


def test_flag_off_keeps_hybrid_order() -> None:
    old = _doc("old", 0.6, NOW - timedelta(days=200))
    new = _doc("new", 0.6, NOW - timedelta(days=3))
    docs = [old, new]
    with patch("onyx.server.query_and_chat.recency_bias.ENABLE_RECENCY_BIAS", False):
        assert maybe_apply_recency_bias(docs) is docs


def test_close_scores_prefer_recent() -> None:
    old = _doc("old", 0.62, NOW - timedelta(days=200))
    new = _doc("new", 0.60, NOW - timedelta(days=3))
    with patch("onyx.server.query_and_chat.recency_bias.ENABLE_RECENCY_BIAS", True):
        out = maybe_apply_recency_bias([old, new])
    assert [doc.document_id for doc in out] == ["new", "old"]


def test_large_hybrid_gap_keeps_identity_first() -> None:
    """Exact-key winner with a much higher hybrid score must stay #1."""
    exact = _doc("BN-378", 1.0, NOW - timedelta(days=200))
    neighbor = _doc("BN-377", 0.55, NOW - timedelta(days=3))
    with patch("onyx.server.query_and_chat.recency_bias.ENABLE_RECENCY_BIAS", True):
        out = maybe_apply_recency_bias([exact, neighbor])
    assert out[0].document_id == "BN-378"
