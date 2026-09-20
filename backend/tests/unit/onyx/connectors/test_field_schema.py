"""Declared field schema — PII fails at load; Slack/Teams/Jira/GitHub optional tags."""

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
from onyx.connectors.github.fields import FIELD_SCHEMA as GITHUB_SCHEMA
from onyx.connectors.indexed_schemas import FIELD_SCHEMAS, schema_for_source
from onyx.connectors.jira.fields import FIELD_SCHEMA as JIRA_SCHEMA
from onyx.connectors.slack.fields import FIELD_SCHEMA as SLACK_SCHEMA
from onyx.connectors.teams.fields import FIELD_SCHEMA as TEAMS_SCHEMA
import pytest

PII_BLOCKLIST = frozenset(
    {
        "assignee_email",
        "reporter_email",
        "sender_email",
        "user",
        "assignees",
        "merged_by",
        "closed_by",
    }
)


def _keys(schema: tuple[FieldDecl, ...]) -> set[str]:
    return {item.key for item in schema}


def test_pii_blocklist_includes_github_people_keys() -> None:
    assert PII_TAG_KEYS == PII_BLOCKLIST


@pytest.mark.parametrize(
    "key",
    sorted(PII_BLOCKLIST),
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


JIRA_OPTIONAL_KEYS = {
    "key",
    "project",
    "project_name",
    "issuetype",
    "status",
    "status_category",
    "resolution",
    "status_was",
    "priority",
    "assignee",
    "reporter",
    "last_updater",
    "created",
    "updated",
    "duedate",
    "resolution_date",
    "parent",
    "issuelink",
    "issuelink_type",
    "labels",
}


def test_jira_schema_matches_today_catalog_without_pii() -> None:
    keys = _keys(JIRA_SCHEMA)
    assert keys == JIRA_OPTIONAL_KEYS
    assert keys.isdisjoint(PII_TAG_KEYS)
    assert "assignee_email" not in keys
    assert "reporter_email" not in keys
    assert "custom_fields" not in keys
    assert default_selected_keys(JIRA_SCHEMA) == frozenset(JIRA_OPTIONAL_KEYS)
    assert contains_match_keys(JIRA_SCHEMA) == frozenset(
        {"assignee", "reporter", "last_updater", "labels"}
    )
    assert {item.key for item in JIRA_SCHEMA if item.match == "date"} == {
        "created",
        "updated",
        "duedate",
        "resolution_date",
    }
    assert effective_selection(None, JIRA_SCHEMA) == frozenset(JIRA_OPTIONAL_KEYS)
    assert effective_selection([], JIRA_SCHEMA) == frozenset()


GITHUB_OPTIONAL_KEYS = {
    "object_type",
    "repo",
    "state",
    "merged",
    "labels",
    "num_commits",
    "num_files_changed",
}


def test_github_schema_matches_today_catalog_without_pii() -> None:
    keys = _keys(GITHUB_SCHEMA)
    assert keys == GITHUB_OPTIONAL_KEYS
    assert keys.isdisjoint(PII_TAG_KEYS)
    assert "user" not in keys
    assert "assignees" not in keys
    assert "merged_by" not in keys
    assert "closed_by" not in keys
    assert "created_at" not in keys
    assert default_selected_keys(GITHUB_SCHEMA) == frozenset(GITHUB_OPTIONAL_KEYS)
    assert contains_match_keys(GITHUB_SCHEMA) == frozenset({"labels"})
    assert effective_selection(None, GITHUB_SCHEMA) == frozenset(GITHUB_OPTIONAL_KEYS)
    assert effective_selection([], GITHUB_SCHEMA) == frozenset()


def test_slack_teams_jira_and_github_are_migrated() -> None:
    assert set(FIELD_SCHEMAS) == {
        DocumentSource.SLACK,
        DocumentSource.TEAMS,
        DocumentSource.JIRA,
        DocumentSource.GITHUB,
    }
    assert schema_for_source(DocumentSource.JIRA) is JIRA_SCHEMA
    assert schema_for_source(DocumentSource.GITHUB) is GITHUB_SCHEMA
    assert schema_for_source(DocumentSource.DISCOURSE) is None
    assert schema_for_source(DocumentSource.ZULIP) is None


def test_unset_selection_inherits_defaults() -> None:
    assert effective_selection(None, SLACK_SCHEMA) == frozenset({"channel", "author"})


def test_empty_selection_is_not_unset() -> None:
    assert effective_selection([], SLACK_SCHEMA) == frozenset()


def test_selection_drops_pii_and_unknown_keys() -> None:
    cleaned = sanitize_indexed_field_selection(
        [
            "channel",
            "assignee_email",
            "reporter_email",
            "sender_email",
            "user",
            "assignees",
            "merged_by",
            "closed_by",
            "issuetype",
            "author",
        ],
        frozenset({"channel", "author"}),
    )
    assert cleaned == ["channel", "author"]
