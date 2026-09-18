"""Document content fetch: stitch, truncation, ACL filters, not-found."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from onyx.server.query_and_chat.document_content import (
    DOCUMENT_CONTENT_CHAR_CAP,
    DOCUMENT_CONTENT_MAX_CHUNKS,
    ContentSectionRequest,
    DocumentContentError,
    DocumentContentRequest,
    build_document_content_response,
    parse_content_document_id,
    retrieve_document_content_chunks,
    stitch_document_content,
)
from pydantic import ValidationError
import pytest


def _chunk(
    chunk_id: int,
    content: str,
    *,
    title: str = "hello",
    link: str | None = "https://releasedesk.slack.com/archives/C123/p1",
    source: str = "slack",
) -> SimpleNamespace:
    links = {0: link} if link else {}
    return SimpleNamespace(
        chunk_id=chunk_id,
        content=content,
        semantic_identifier=title,
        source_links=links,
        source_type=SimpleNamespace(value=source),
    )


def test_parse_content_document_id_rejects_empty_and_oversized() -> None:
    assert parse_content_document_id(" slack-thread-1 ") == "slack-thread-1"
    with pytest.raises(DocumentContentError):
        parse_content_document_id("")
    with pytest.raises(DocumentContentError):
        parse_content_document_id("a\nb")
    with pytest.raises(DocumentContentError):
        parse_content_document_id("x" * 1025)


def test_stitch_joins_chunks_in_index_order() -> None:
    chunks = [
        _chunk(1, "second reply"),
        _chunk(0, "parent message"),
    ]
    content, truncated, count = stitch_document_content(chunks)
    assert content == "parent message\nsecond reply"
    assert truncated is False
    assert count == 2


def test_stitch_truncates_at_char_cap() -> None:
    body = "a" * (DOCUMENT_CONTENT_CHAR_CAP + 50)
    content, truncated, count = stitch_document_content([_chunk(0, body)])
    assert truncated is True
    assert len(content) == DOCUMENT_CONTENT_CHAR_CAP
    assert count == 1
    assert content == "a" * DOCUMENT_CONTENT_CHAR_CAP


def test_stitch_marks_truncated_when_chunk_cap_is_hit() -> None:
    chunks = [_chunk(i, f"c{i}") for i in range(DOCUMENT_CONTENT_MAX_CHUNKS)]
    content, truncated, count = stitch_document_content(chunks)
    assert truncated is True
    assert count == DOCUMENT_CONTENT_MAX_CHUNKS
    assert content.startswith("c0\nc1")


def test_retrieve_passes_acl_tenant_and_hidden_flag() -> None:
    document_index = MagicMock()
    document_index.id_based_retrieval.return_value = []
    filters = SimpleNamespace(
        access_control_list=["user:alice"],
        tenant_id="tenant-a",
        source_type=["slack"],
    )
    retrieve_document_content_chunks(
        document_id="slack-1",
        document_index=document_index,
        filters=filters,
    )
    document_index.id_based_retrieval.assert_called_once()
    kwargs = document_index.id_based_retrieval.call_args.kwargs
    assert kwargs["filters"] is filters
    assert kwargs["filters"].access_control_list == ["user:alice"]
    assert kwargs["filters"].tenant_id == "tenant-a"
    assert kwargs["include_hidden"] is True
    requests = kwargs["chunk_requests"]
    assert len(requests) == 1
    assert isinstance(requests[0], ContentSectionRequest)
    assert requests[0].document_id == "slack-1"
    assert requests[0].min_chunk_ind == 0
    assert requests[0].max_chunk_ind == DOCUMENT_CONTENT_MAX_CHUNKS - 1


def test_empty_retrieval_is_not_found() -> None:
    payload = build_document_content_response(
        document_id="missing",
        source="slack",
        chunks=[],
    )
    assert payload["found"] is False
    assert payload["document_id"] == "missing"
    assert "content" not in payload
    assert "readable in this tenant" in str(payload["hint"])


def test_acl_miss_looks_like_not_found() -> None:
    """OpenSearch returns no chunks when ACL/tenant filters exclude the doc."""
    document_index = MagicMock()
    document_index.id_based_retrieval.return_value = []
    other_tenant = SimpleNamespace(
        access_control_list=["user:bob"],
        tenant_id="tenant-b",
    )
    chunks = retrieve_document_content_chunks(
        document_id="slack-1",
        document_index=document_index,
        filters=other_tenant,
    )
    payload = build_document_content_response(
        document_id="slack-1",
        source="slack",
        chunks=chunks,
    )
    assert other_tenant.tenant_id == "tenant-b"
    assert payload["found"] is False
    assert payload["hint"] == build_document_content_response(
        document_id="other", source="slack", chunks=[]
    )["hint"]


def test_happy_path_payload_stitches_slack_thread() -> None:
    chunks = [
        _chunk(0, "parent: shipping Friday"),
        _chunk(1, "reply: delayed to Monday"),
    ]
    payload = build_document_content_response(
        document_id="slack-1",
        source="slack",
        chunks=chunks,
    )
    assert payload["found"] is True
    assert payload["document_id"] == "slack-1"
    assert payload["source"] == "slack"
    assert payload["content"] == "parent: shipping Friday\nreply: delayed to Monday"
    assert payload["truncated"] is False
    assert payload["content_chars"] == len(str(payload["content"]))
    assert payload["cap_chars"] == DOCUMENT_CONTENT_CHAR_CAP
    assert payload["chunk_count"] == 2
    assert payload["content_trust"] == "untrusted"
    assert payload["link"] == "https://releasedesk.slack.com/archives/C123/p1"
    assert "hint" not in payload


def test_truncated_payload_sets_hint() -> None:
    body = "b" * (DOCUMENT_CONTENT_CHAR_CAP + 1)
    payload = build_document_content_response(
        document_id="wiki-1",
        source="confluence",
        chunks=[_chunk(0, body, title="Page", source="confluence")],
    )
    assert payload["found"] is True
    assert payload["truncated"] is True
    assert payload["content_chars"] == DOCUMENT_CONTENT_CHAR_CAP
    assert "truncated at cap" in str(payload["hint"])


def test_document_content_request_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentContentRequest.model_validate({"document_id": "slack-1", "extra": True})
    parsed = DocumentContentRequest.model_validate({"document_id": "slack-1"})
    assert parsed.document_id == "slack-1"
    assert parsed.source is None
