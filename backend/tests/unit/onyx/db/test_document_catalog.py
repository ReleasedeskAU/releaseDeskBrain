"""Catalog helpers for listing matching documents by tag."""

from unittest.mock import MagicMock, patch

import pytest

from onyx.configs.constants import DocumentSource
from onyx.db.document_catalog import (
    MAX_CATALOG_ROWS,
    catalog_document_row,
    list_documents_matching_filter,
    ticket_key_from_semantic_id,
)
from onyx.db.document_count import DocumentCountError


def test_ticket_key_from_semantic_id() -> None:
    assert ticket_key_from_semantic_id("RD-63: Approvals missing") == "RD-63"
    assert ticket_key_from_semantic_id("RD-63 Approvals missing") == "RD-63"
    assert ticket_key_from_semantic_id("") is None
    assert ticket_key_from_semantic_id(None) is None


def test_catalog_document_row_prefers_key_tag() -> None:
    row = catalog_document_row(
        key="RD-10",
        semantic_id="OTHER-1: ignored title prefix",
        link="https://example.test/browse/RD-10",
    )
    assert row["key"] == "RD-10"
    assert row["title"] == "OTHER-1: ignored title prefix"
    assert row["link"] == "https://example.test/browse/RD-10"


def test_catalog_document_row_keeps_unassigned_explicit() -> None:
    row = catalog_document_row(
        key="RD-142",
        semantic_id="RD-142: Highest",
        link=None,
        extras={"assignee": None, "status": "To Do", "created": "2026-08-01"},
    )
    assert row["assignee"] is None
    assert row["status"] == "To Do"


def test_catalog_document_row_includes_connector_source() -> None:
    row = catalog_document_row(
        key="C1__1.0",
        semantic_id="Unknown in #social: hi",
        link=None,
        extras={"source": "slack", "author": None, "document_id": "slack-thread-1"},
    )
    assert row["source"] == "slack"
    assert row["author"] is None
    assert row["document_id"] == "slack-thread-1"


def test_catalog_document_row_falls_back_to_semantic_prefix() -> None:
    row = catalog_document_row(
        key=None,
        semantic_id="RD-67: Conflicts not visible",
        link=None,
    )
    assert row["key"] == "RD-67"
    assert "Conflicts not visible" in (row["title"] or "")


def test_list_matching_rejects_all_sources_with_no_filter() -> None:
    with pytest.raises(DocumentCountError, match="At least one filter is required"):
        list_documents_matching_filter(
            MagicMock(), source=None, filters=[], date_ranges=None
        )


def test_list_matching_named_source_alone_is_capped() -> None:
    ids = [f"doc-{i}" for i in range(MAX_CATALOG_ROWS + 1)]
    with patch(
        "onyx.db.document_catalog._indexed_document_ids_for_source",
        return_value=set(ids),
    ), patch(
        "onyx.db.document_catalog._sort_document_ids", return_value=ids
    ), patch(
        "onyx.db.document_catalog._documents_with_keys",
        side_effect=lambda _db, rows: [{"key": row} for row in rows],
    ), patch(
        "onyx.db.document_catalog.intersect_date_range_ids", return_value=None
    ):
        result = list_documents_matching_filter(
            MagicMock(),
            source=DocumentSource.TEAMS,
            filters=[],
            date_ranges=None,
        )
    assert result["count"] == MAX_CATALOG_ROWS + 1
    assert result["truncated"] is True
    assert result["returned"] == MAX_CATALOG_ROWS
    assert result["source"] == "teams"
    assert len(result["documents"]) == MAX_CATALOG_ROWS


def test_list_matching_named_source_alone_under_cap_is_not_truncated() -> None:
    ids = ["doc-a", "doc-b"]
    with patch(
        "onyx.db.document_catalog._indexed_document_ids_for_source",
        return_value=set(ids),
    ), patch(
        "onyx.db.document_catalog._sort_document_ids", return_value=ids
    ), patch(
        "onyx.db.document_catalog._documents_with_keys",
        side_effect=lambda _db, rows: [{"key": row} for row in rows],
    ), patch(
        "onyx.db.document_catalog.intersect_date_range_ids", return_value=None
    ):
        result = list_documents_matching_filter(
            MagicMock(),
            source=DocumentSource.TEAMS,
            filters=[],
            date_ranges=None,
        )
    assert result["count"] == 2
    assert result["truncated"] is False
    assert result["returned"] == 2
