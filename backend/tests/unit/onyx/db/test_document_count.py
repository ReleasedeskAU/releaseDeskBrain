"""Unit tests for document-count argument parsing — no DB."""

from onyx.configs.constants import DocumentSource
from onyx.db.document_count import (
    ALLOWED_TAG_KEYS,
    BY_KEY_TAG_KEYS,
    DISPLAY_TAG_KEYS,
    DocumentCountError,
    PII_TAG_KEYS,
    escape_ilike_pattern,
    field_uses_contains_match,
    parse_catalog_filters,
    parse_count_source,
    parse_document_key,
    parse_filter_field,
    parse_filter_value,
    queryable_fields,
    require_filter_field,
    stored_value_matches_filter,
)
import pytest


def test_escape_ilike_pattern_neutralizes_wildcards() -> None:
    assert escape_ilike_pattern("Kabir") == "Kabir"
    assert escape_ilike_pattern("100%") == "100\\%"
    assert escape_ilike_pattern("a_b") == "a\\_b"
    assert escape_ilike_pattern("a\\b") == "a\\\\b"


def test_parse_count_source() -> None:
    assert parse_count_source(None) is None
    assert parse_count_source("all") is None
    assert parse_count_source("JIRA") is DocumentSource.JIRA
    with pytest.raises(DocumentCountError):
        parse_count_source("not-a-source")


def test_parse_filter_field_allowlist() -> None:
    assert parse_filter_field(None) is None
    assert parse_filter_field("Assignee") == "assignee"
    with pytest.raises(DocumentCountError):
        parse_filter_field("sql_injection")


def test_parse_filter_value_bounds() -> None:
    assert parse_filter_value(None) is None
    assert parse_filter_value("  Kabir  ") == "Kabir"
    with pytest.raises(DocumentCountError):
        parse_filter_value("   ")
    with pytest.raises(DocumentCountError):
        parse_filter_value("x" * 81)


def test_require_filter_field() -> None:
    assert require_filter_field("Status") == "status"
    with pytest.raises(DocumentCountError):
        require_filter_field(None)
    with pytest.raises(DocumentCountError):
        require_filter_field("  ")


def test_parse_document_key_exact_not_contains() -> None:
    assert parse_document_key("  RD-82  ") == "RD-82"
    with pytest.raises(DocumentCountError):
        parse_document_key(None)
    with pytest.raises(DocumentCountError):
        parse_document_key("   ")
    with pytest.raises(DocumentCountError):
        parse_document_key("RD-82\n")
    with pytest.raises(DocumentCountError):
        parse_document_key("x" * 41)


def test_pii_fields_are_never_queryable() -> None:
    assert ALLOWED_TAG_KEYS.isdisjoint(PII_TAG_KEYS)
    schema = queryable_fields()
    fields = schema["fields"]
    assert isinstance(fields, list)
    for blocked in PII_TAG_KEYS:
        assert blocked not in fields
        with pytest.raises(DocumentCountError):
            parse_filter_field(blocked)
    assert "parent" in fields
    assert "duedate" in fields
    assert "issuelink" in fields
    assert "last_updater" in fields
    assert "status_was" in fields
    assert "status_category" in fields
    assert "repo" in fields
    assert "object_type" in fields
    assert "num_files_changed" in fields
    assert "num_commits" in fields
    assert "state" in fields
    assert "merged" in fields
    assert "custom_fields" not in fields
    with pytest.raises(DocumentCountError):
        parse_filter_field("custom_fields")
    assert DISPLAY_TAG_KEYS == frozenset({"custom_fields"})
    assert "custom_fields" in BY_KEY_TAG_KEYS
    assert "custom_fields" not in ALLOWED_TAG_KEYS
    assert field_uses_contains_match("state") is False
    assert field_uses_contains_match("merged") is False
    assert field_uses_contains_match("repo") is False
    assert field_uses_contains_match("object_type") is False
    assert schema["resolved_status_category"] == "done"
    assert schema["status_category_values"] == ["new", "indeterminate", "done"]
    assert "resolved_statuses" not in schema
    assert "due_before" in schema["date_range_params"]
    assert "created_asc" in schema["sort_by"]


def test_key_and_parent_are_exact_not_contains() -> None:
    assert field_uses_contains_match("parent") is False
    assert field_uses_contains_match("key") is False
    assert stored_value_matches_filter("parent", "RD-90", "RD-90") is True
    assert stored_value_matches_filter("parent", "RD-90", "rd-90") is True
    assert stored_value_matches_filter("parent", "RD-90", "RD-9") is False
    assert stored_value_matches_filter("key", "RD-90", "RD-9") is False


def test_names_and_labels_keep_contains_match() -> None:
    assert stored_value_matches_filter("last_updater", "Release Desk", "Release") is True
    assert stored_value_matches_filter("assignee", "Mohd Kabir", "Kabir") is True
    assert stored_value_matches_filter("labels", "release123", "release") is True
    assert stored_value_matches_filter("status", "To Do", "todo") is False
    assert stored_value_matches_filter("status", "To Do", "To Do") is True
    assert field_uses_contains_match("status_category") is False
    assert stored_value_matches_filter("status_category", "done", "done") is True
    assert stored_value_matches_filter("status_category", "done", "Closed") is False


def test_and_filters_parse_and_reject_mix() -> None:
    assert parse_catalog_filters("status", "To Do", None) == [("status", "To Do")]
    assert parse_catalog_filters(
        None, None, [("issuetype", "Bug"), ("assignee", "Kabir")]
    ) == [("issuetype", "Bug"), ("assignee", "Kabir")]
    with pytest.raises(DocumentCountError):
        parse_catalog_filters("status", "To Do", [("assignee", "Kabir")])
    with pytest.raises(DocumentCountError):
        parse_catalog_filters("assignee_email", "a@b.c", None)
    with pytest.raises(DocumentCountError):
        parse_catalog_filters(None, None, [("status", "To Do")] * 6)

