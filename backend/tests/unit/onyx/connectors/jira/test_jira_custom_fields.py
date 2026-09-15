"""Populated Jira custom fields are indexed; Rank / lexorank is not."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from jira.resources import Issue

from onyx.connectors.jira.connector import process_jira_issue
from onyx.connectors.jira.utils import extract_populated_custom_field_lines


def _issue_from_raw(raw: dict[str, Any]) -> MagicMock:
    issue = MagicMock(spec=Issue)
    issue.key = raw["key"]
    issue.raw = raw
    issue.fields = SimpleNamespace()
    return issue


def test_process_jira_issue_indexes_populated_custom_fields() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-319",
            "fields": {
                "summary": "Estimate ticket",
                "description": "body",
                "comment": {"comments": []},
                "updated": "2023-01-01T00:00:00+0000",
                "created": "2023-01-01T00:00:00+0000",
                "status": {"name": "To Do"},
                "customfield_10016": 13,
                "customfield_10019": "0|i00007:",
            },
        }
    )
    catalog = {
        "customfield_10016": "Story point estimate",
        "customfield_10019": "Rank",
    }
    doc = process_jira_issue(
        "https://example.atlassian.net",
        issue,
        custom_field_names=catalog,
    )
    assert doc is not None
    assert "Story point estimate: 13" in doc.sections[0].text
    assert doc.metadata["custom_fields"] == ["Story point estimate: 13"]
    assert "Rank:" not in doc.sections[0].text
    assert "0|i00007:" not in doc.sections[0].text


def test_extract_skips_empty_and_lexorank_without_guessing_names() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-1",
            "fields": {
                "customfield_1": None,
                "customfield_2": "",
                "customfield_3": "0|zzz",
                "customfield_4": "Platform",
            },
        }
    )
    lines = extract_populated_custom_field_lines(
        issue,
        {
            "customfield_1": "Empty",
            "customfield_2": "Blank",
            "customfield_3": "Board order",
            "customfield_4": "Team",
        },
    )
    assert lines == ["Team: Platform"]


def test_process_jira_issue_omits_custom_fields_without_catalog() -> None:
    issue = _issue_from_raw(
        {
            "key": "RD-2",
            "fields": {
                "summary": "No catalog",
                "description": "body",
                "comment": {"comments": []},
                "updated": "2023-01-01T00:00:00+0000",
                "created": "2023-01-01T00:00:00+0000",
                "customfield_10016": 13,
            },
        }
    )
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert "custom_fields" not in doc.metadata
    assert "13" not in doc.sections[0].text
