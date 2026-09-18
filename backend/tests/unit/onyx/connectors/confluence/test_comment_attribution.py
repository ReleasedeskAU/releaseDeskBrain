"""Confluence folded comments prefix each speaker."""

from unittest.mock import MagicMock, patch

from onyx.connectors.confluence.connector import (
    ConfluenceConnector,
    _COMMENT_EXPANSION_FIELDS,
    _confluence_comment_speaker_name,
)


def test_comment_speaker_uses_version_display_name() -> None:
    comment = {"version": {"by": {"displayName": "Ada", "email": "ada@example.com"}}}
    assert _confluence_comment_speaker_name(comment) == "Ada"


def test_comment_speaker_none_when_unresolved() -> None:
    assert _confluence_comment_speaker_name({}) is None
    assert _confluence_comment_speaker_name({"version": {"by": {"email": "x"}}}) is None
    assert _confluence_comment_speaker_name({"version": {"by": {"displayName": "  "}}}) is None


def test_comment_expansion_includes_version() -> None:
    assert "version" in _COMMENT_EXPANSION_FIELDS


def test_comment_string_prefixes_each_speaker() -> None:
    connector = MagicMock()
    connector.cql_label_filter = ""
    connector.confluence_client = MagicMock()
    connector.confluence_client.paginated_cql_retrieval.return_value = [
        {"version": {"by": {"displayName": "Ada"}}},
        {"version": {"by": {"displayName": "Bob"}}},
        {},
    ]
    with patch(
        "onyx.connectors.confluence.connector.extract_text_from_confluence_html",
        side_effect=["first note", "second note", "no name"],
    ):
        text = ConfluenceConnector._get_comment_string_for_page_id(connector, "42")
    assert text == "Ada: first note\nBob: second note\nUnknown: no name"
    expand = connector.confluence_client.paginated_cql_retrieval.call_args.kwargs[
        "expand"
    ]
    assert "version" in expand.split(",")
