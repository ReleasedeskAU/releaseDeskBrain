"""Declared field schema — PII fails at load; Slack only declares channel and author."""

from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    FieldSchemaError,
    default_selected_keys,
    effective_selection,
    sanitize_indexed_field_selection,
    validate_field_schema,
)
from onyx.connectors.slack.utils import FIELD_SCHEMA
from onyx.db.document_count import PII_TAG_KEYS
import pytest


def test_validate_field_schema_rejects_pii_at_load() -> None:
    with pytest.raises(FieldSchemaError, match="PII"):
        validate_field_schema(
            (
                FieldDecl(
                    "assignee_email",
                    FieldCategory.OWNERSHIP,
                    "Assignee email",
                ),
            )
        )


def test_validate_field_schema_rejects_reporter_email() -> None:
    with pytest.raises(FieldSchemaError, match="PII"):
        validate_field_schema(
            (FieldDecl("reporter_email", FieldCategory.OWNERSHIP, "Reporter email"),)
        )


def test_slack_schema_is_channel_and_author_only() -> None:
    keys = {item.key for item in FIELD_SCHEMA}
    assert keys == {"channel", "author"}
    assert keys.isdisjoint(PII_TAG_KEYS)
    assert default_selected_keys(FIELD_SCHEMA) == frozenset({"channel", "author"})


def test_unset_selection_inherits_defaults() -> None:
    assert effective_selection(None, FIELD_SCHEMA) == frozenset({"channel", "author"})


def test_empty_selection_is_not_unset() -> None:
    assert effective_selection([], FIELD_SCHEMA) == frozenset()


def test_selection_drops_pii_and_unknown_keys() -> None:
    cleaned = sanitize_indexed_field_selection(
        ["channel", "assignee_email", "reporter_email", "issuetype", "author"],
        frozenset({"channel", "author"}),
    )
    assert cleaned == ["channel", "author"]
