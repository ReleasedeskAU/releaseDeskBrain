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
    assert parse_filter_field("Channel") == "channel"
    assert parse_filter_field("Author") == "author"
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
    assert "channel" in fields
    assert "author" in fields
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
    assert field_uses_contains_match("author") is True
    assert schema["resolved_status_category"] == "done"
    assert schema["status_category_values"] == ["new", "indeterminate", "done"]
    assert "resolved_statuses" not in schema
    assert "due_before" in schema["date_range_params"]
    assert "created_asc" in schema["sort_by"]
    assert "author" in schema["list_projection"]
    assert "source" in schema["list_projection"]


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


def test_slack_queryable_fields_unset_match_today_slack_tags() -> None:
    from onyx.db.document_catalog import list_queryable_fields
    from onyx.db.document_count import queryable_fields_for_source, require_source_filter_field

    global_fields = queryable_fields()["fields"]
    assert "channel" in global_fields
    assert "author" in global_fields
    assert "assignee" in global_fields
    slack = queryable_fields_for_source(DocumentSource.SLACK, None)
    assert slack["fields"] == ["author", "channel"]
    assert slack["contains_match"] == ["author"]
    assert "Unset inherits channel and author" in str(slack["note"])
    via_catalog = list_queryable_fields(DocumentSource.SLACK, None)
    assert via_catalog["fields"] == ["author", "channel"]
    teams = queryable_fields_for_source(DocumentSource.TEAMS, None)
    assert teams["fields"] == ["author", "channel"]
    assert teams["contains_match"] == ["author"]
    jira = queryable_fields_for_source(DocumentSource.JIRA, None)
    assert "assignee" in jira["fields"]
    assert "channel" not in jira["fields"]
    assert "assignee_email" not in jira["fields"]
    assert "custom_fields" not in jira["fields"]
    assert jira["contains_match"] == [
        "assignee",
        "labels",
        "last_updater",
        "reporter",
    ]
    assert "Unset inherits today's Jira catalog" in str(jira["note"])
    assert require_source_filter_field("channel", DocumentSource.SLACK, None) == "channel"
    assert require_source_filter_field("channel", DocumentSource.TEAMS, None) == "channel"
    with pytest.raises(DocumentCountError):
        require_source_filter_field("assignee", DocumentSource.SLACK, None)
    with pytest.raises(DocumentCountError):
        require_source_filter_field("assignee", DocumentSource.TEAMS, None)
    with pytest.raises(DocumentCountError):
        require_source_filter_field("channel", DocumentSource.JIRA, None)
    assert require_source_filter_field("assignee", DocumentSource.JIRA, None) == "assignee"
    github = queryable_fields_for_source(DocumentSource.GITHUB, None)
    assert github["fields"] == [
        "labels",
        "merged",
        "num_commits",
        "num_files_changed",
        "object_type",
        "repo",
        "state",
    ]
    assert github["contains_match"] == ["labels"]
    assert "channel" not in github["fields"]
    assert "user" not in github["fields"]
    assert "assignees" not in github["fields"]
    assert "merged_by" not in github["fields"]
    assert "closed_by" not in github["fields"]
    assert "Unset inherits today's GitHub catalog" in str(github["note"])
    via_github = list_queryable_fields(DocumentSource.GITHUB, None)
    assert via_github["fields"] == github["fields"]
    assert require_source_filter_field("repo", DocumentSource.GITHUB, None) == "repo"
    with pytest.raises(DocumentCountError):
        require_source_filter_field("channel", DocumentSource.GITHUB, None)
    with pytest.raises(DocumentCountError):
        require_source_filter_field("user", DocumentSource.GITHUB, None)


def test_slack_empty_selection_hides_optional_tags() -> None:
    from onyx.db.document_count import slack_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[list[str]]:
            return [[]]

    assert slack_queryable_keys(_FakeSession()) == frozenset()


def test_slack_unset_row_keeps_channel_and_author() -> None:
    from onyx.db.document_count import slack_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[None]:
            return [None]

    assert slack_queryable_keys(_FakeSession()) == frozenset({"channel", "author"})


def test_jira_empty_selection_hides_optional_tags() -> None:
    from onyx.connectors.jira.fields import FIELD_SCHEMA
    from onyx.db.document_count import declared_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[list[str]]:
            return [[]]

    assert (
        declared_queryable_keys(DocumentSource.JIRA, FIELD_SCHEMA, _FakeSession())
        == frozenset()
    )


def test_jira_unset_row_keeps_today_catalog() -> None:
    from onyx.connectors.jira.fields import FIELD_SCHEMA
    from onyx.db.document_count import declared_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[None]:
            return [None]

    keys = declared_queryable_keys(DocumentSource.JIRA, FIELD_SCHEMA, _FakeSession())
    assert "assignee" in keys
    assert "status_category" in keys
    assert "assignee_email" not in keys
    assert "custom_fields" not in keys
    assert "channel" not in keys


def test_github_empty_selection_hides_optional_tags() -> None:
    from onyx.connectors.github.fields import FIELD_SCHEMA
    from onyx.db.document_count import declared_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[list[str]]:
            return [[]]

    assert (
        declared_queryable_keys(DocumentSource.GITHUB, FIELD_SCHEMA, _FakeSession())
        == frozenset()
    )


def test_github_unset_row_keeps_today_catalog() -> None:
    from onyx.connectors.github.fields import FIELD_SCHEMA
    from onyx.db.document_count import declared_queryable_keys

    class _FakeSession:
        def execute(self, _stmt: object) -> "_FakeSession":
            return self

        def scalars(self) -> "_FakeSession":
            return self

        def all(self) -> list[None]:
            return [None]

    keys = declared_queryable_keys(DocumentSource.GITHUB, FIELD_SCHEMA, _FakeSession())
    assert keys == frozenset(
        {
            "object_type",
            "repo",
            "state",
            "merged",
            "labels",
            "num_commits",
            "num_files_changed",
        }
    )
    assert "user" not in keys
    assert "channel" not in keys

