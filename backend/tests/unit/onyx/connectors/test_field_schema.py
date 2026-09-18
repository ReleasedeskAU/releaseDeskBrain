"""Declared field schema — PII fails at load; Slack/Teams declare channel and author."""

from onyx.configs.constants import DocumentSource
from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    FieldSchemaError,
    PII_TAG_KEYS,
    contains_match_keys,
    default_selected_keys,
    effective_selection,
    sanitize_indexed_field_selection,
    validate_field_schema,
)
from onyx.connectors.indexed_schemas import FIELD_SCHEMAS, schema_for_source
from onyx.connectors.slack.fields import FIELD_SCHEMA as SLACK_SCHEMA
from onyx.connectors.teams.fields import FIELD_SCHEMA as TEAMS_SCHEMA
import pytest


def _keys(schema: tuple[FieldDecl, ...]) -> set[str]:
    return {item.key for item in schema}


def test_pii_blocklist_includes_sender_email() -> None:
    assert PII_TAG_KEYS == frozenset(
        {"assignee_email", "reporter_email", "sender_email"}
    )


@pytest.mark.parametrize(
    "key",
    ["assignee_email", "reporter_email", "sender_email"],
)
def test_validate_field_schema_rejects_pii_at_load(key: str) -> None:
    with pytest.raises(FieldSchemaError, match="PII"):
        validate_field_schema(
            (FieldDecl(key, FieldCategory.OWNERSHIP, "Blocked"),)
        )


def test_slack_schema_is_channel_and_author_only() -> None:
    keys = _keys(SLACK_SCHEMA)
    assert keys == {"channel", "author"}
    assert keys.isdisjoint(PII_TAG_KEYS)
    assert default_selected_keys(SLACK_SCHEMA) == frozenset({"channel", "author"})
    assert contains_match_keys(SLACK_SCHEMA) == frozenset({"author"})


def test_teams_schema_matches_slack_tags() -> None:
    assert _keys(TEAMS_SCHEMA) == {"channel", "author"}
    assert contains_match_keys(TEAMS_SCHEMA) == frozenset({"author"})
    assert effective_selection(None, TEAMS_SCHEMA) == frozenset({"channel", "author"})
    assert effective_selection([], TEAMS_SCHEMA) == frozenset()


def test_only_slack_and_teams_are_migrated() -> None:
    assert set(FIELD_SCHEMAS) == {DocumentSource.SLACK, DocumentSource.TEAMS}
    assert schema_for_source(DocumentSource.JIRA) is None
    assert schema_for_source(DocumentSource.DISCOURSE) is None
    assert schema_for_source(DocumentSource.ZULIP) is None


def test_unset_selection_inherits_defaults() -> None:
    assert effective_selection(None, SLACK_SCHEMA) == frozenset({"channel", "author"})


def test_empty_selection_is_not_unset() -> None:
    assert effective_selection([], SLACK_SCHEMA) == frozenset()


def test_selection_drops_pii_and_unknown_keys() -> None:
    cleaned = sanitize_indexed_field_selection(
        ["channel", "assignee_email", "reporter_email", "sender_email", "issuetype", "author"],
        frozenset({"channel", "author"}),
    )
    assert cleaned == ["channel", "author"]
