"""Metadata tagging must work for every assignee, not only the authenticated user."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from jira.resources import Issue

from onyx.connectors.jira.connector import process_jira_issue
from onyx.connectors.jira.utils import (
    JiraFieldStats,
    best_effort_basic_expert_info,
    best_effort_get_field_from_issue,
    jira_issue_link_pairs,
    jira_last_updater,
    jira_status_category_key,
    jira_status_was_values,
    jira_user_display_name,
    jira_user_email,
)


class _EmailRaisesUser:
    """Mimics a Jira User Resource whose emailAddress access lazy-loads and fails."""

    email_reads = 0

    def __init__(self, display_name: str, account_id: str) -> None:
        self.raw = {"displayName": display_name, "accountId": account_id}
        self.displayName = display_name
        self.accountId = account_id

    @property
    def emailAddress(self) -> str:
        type(self).email_reads += 1
        raise RuntimeError("profile fetch forbidden")


def _issue(
    key: str,
    *,
    assignee: object | None,
    reporter: object | None = None,
    status: str = "To Do",
    priority: str = "Medium",
    issuetype: str = "Bug",
) -> MagicMock:
    issue = MagicMock(spec=Issue)
    issue.key = key
    issue.raw = {
        "key": key,
        "fields": {
            "assignee": assignee,
            "reporter": reporter,
            "status": {"name": status},
            "priority": {"name": priority},
            "issuetype": {"name": issuetype},
            "project": {"key": "RD", "name": "ReleaseDesk"},
            "labels": ["alpha"],
            "summary": "summary",
            "description": "body",
            "updated": "2023-01-01T00:00:00+0000",
            "created": "2023-01-01T00:00:00+0000",
            "comment": {"comments": []},
        },
    }
    issue.fields = SimpleNamespace(
        description="body",
        summary="summary",
        updated="2023-01-01T00:00:00+0000",
        created="2023-01-01T00:00:00+0000",
        labels=["alpha"],
        assignee=assignee,
        comment=SimpleNamespace(comments=[]),
    )
    return issue


def test_display_name_does_not_read_email() -> None:
    _EmailRaisesUser.email_reads = 0
    user = _EmailRaisesUser("Jalla venkata shivananda", "acct-jalla")
    assert jira_user_display_name(user) == "Jalla venkata shivananda"
    assert jira_user_email(user) is None
    expert = best_effort_basic_expert_info(user)
    assert expert is not None
    assert expert.get_semantic_name() == "Jalla venkata shivananda"
    assert expert.get_email() is None
    assert _EmailRaisesUser.email_reads == 0


def test_email_and_account_id_are_not_used_as_the_name() -> None:
    payload = {"emailAddress": "hidden@example.com", "accountId": "acct-only"}
    assert jira_user_display_name(payload) is None
    assert jira_user_email(payload) == "hidden@example.com"
    assert best_effort_basic_expert_info(payload) is None


def test_all_assignees_are_tagged_not_just_one() -> None:
    _EmailRaisesUser.email_reads = 0
    stats = JiraFieldStats()
    people = [
        {"displayName": "Mohd Kabir", "emailAddress": "mohd.kabir@example.com"},
        _EmailRaisesUser("Release Desk", "acct-rd"),
        {"displayName": "Jalla venkata shivananda", "accountId": "acct-j"},
        {"displayName": "kiran.reddy"},
        {"displayName": "Suresh Chudoji"},
        None,
    ]
    docs = []
    for i, assignee in enumerate(people, start=1):
        doc = process_jira_issue(
            "https://example.atlassian.net",
            _issue(f"RD-{i}", assignee=assignee),
            field_stats=stats,
        )
        assert doc is not None
        docs.append(doc)

    tagged = [doc.metadata.get("assignee") for doc in docs]
    assert tagged == [
        "Mohd Kabir",
        "Release Desk",
        "Jalla venkata shivananda",
        "kiran.reddy",
        "Suresh Chudoji",
        None,
    ]
    assert stats.present["assignee"] == 5
    assert stats.tagged["assignee"] == 5
    assert stats.dropped.get("assignee", 0) == 0
    assert docs[0].metadata["status"] == "To Do"
    assert docs[0].metadata["priority"] == "Medium"
    assert docs[0].metadata["issuetype"] == "Bug"
    assert docs[0].metadata["project"] == "RD"
    assert docs[0].metadata["labels"] == ["alpha"]
    assert docs[0].metadata.get("assignee_email") == "mohd.kabir@example.com"
    assert "assignee_email" not in docs[1].metadata
    assert "assignee_email" not in docs[2].metadata
    assert _EmailRaisesUser.email_reads == 0


def test_named_fields_work_as_dicts_not_only_resources() -> None:
    issue = _issue("RD-9", assignee=None)
    issue.raw["fields"]["status"] = {"name": "In Review"}
    issue.raw["fields"]["priority"] = {"name": "High"}
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert doc.metadata["status"] == "In Review"
    assert doc.metadata["priority"] == "High"


def test_raw_fields_win_over_lazy_resource_getattr() -> None:
    boom = _EmailRaisesUser("ignored", "acct")
    issue = _issue("RD-10", assignee={"displayName": "kiran.reddy"})
    issue.fields.assignee = boom
    assert (
        best_effort_get_field_from_issue(issue, "assignee")["displayName"]
        == "kiran.reddy"
    )
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert doc.metadata["assignee"] == "kiran.reddy"


def test_issuelinks_are_tagged_as_type_and_key() -> None:
    issue = _issue("RD-72", assignee=None)
    issue.raw["fields"]["issuelinks"] = [
        {
            "type": {"name": "Blocks", "inward": "is blocked by", "outward": "blocks"},
            "inwardIssue": {"key": "RD-73"},
        },
        {
            "type": {"name": "Relates", "inward": "relates to", "outward": "relates to"},
            "outwardIssue": {"key": "RD-74"},
        },
    ]
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert doc.metadata["issuelink_type"] == ["is blocked by", "relates to"]
    assert doc.metadata["issuelink"] == ["is blocked by:RD-73", "relates to:RD-74"]
    assert jira_issue_link_pairs(issue)[0] == ("is blocked by", "RD-73")


def test_changelog_tags_last_updater_and_status_was() -> None:
    issue = _issue("RD-3", assignee=None)
    issue.raw["changelog"] = {
        "total": 2,
        "histories": [
            {
                "created": "2023-01-01T00:00:00.000+0000",
                "author": {"displayName": "Jalla"},
                "items": [{"field": "status", "fromString": "To Do", "toString": "Done"}],
            },
            {
                "created": "2023-08-23T12:00:00.000+0000",
                "author": {"displayName": "Release Desk"},
                "items": [
                    {"field": "status", "fromString": "Done", "toString": "To Do"}
                ],
            },
        ],
    }
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert doc.metadata["last_updater"] == "Release Desk"
    assert doc.metadata["status_was"] == ["To Do", "Done"]
    assert jira_last_updater(issue.raw["changelog"]) == "Release Desk"
    assert "Reopened" not in jira_status_was_values(issue.raw["changelog"])


def test_completeness_mismatch_is_detectable() -> None:
    stats = JiraFieldStats()
    stats.observe(
        "assignee", present=True, tagged=False, issue_key="RD-1", reason="test"
    )
    assert stats.dropped["assignee"] == 1
    assert stats.present["assignee"] != stats.tagged.get("assignee", 0)


def test_status_category_key_accepts_only_jira_keys() -> None:
    assert (
        jira_status_category_key({"name": "Closed", "statusCategory": {"key": "done"}})
        == "done"
    )
    assert (
        jira_status_category_key(
            {"name": "To Do", "statusCategory": {"key": "new", "name": "To Do"}}
        )
        == "new"
    )
    assert jira_status_category_key({"name": "Done"}) is None
    assert (
        jira_status_category_key({"name": "Closed", "statusCategory": {"key": "closed"}})
        is None
    )
    assert jira_status_category_key(None) is None


def test_closed_ticket_with_done_category_is_tagged() -> None:
    issue = _issue("RD-200", assignee={"displayName": "Ada"}, status="Closed")
    issue.raw["fields"]["status"] = {
        "name": "Closed",
        "statusCategory": {"id": 3, "key": "done", "name": "Done"},
    }
    doc = process_jira_issue("https://example.atlassian.net", issue)
    assert doc is not None
    assert doc.metadata["status"] == "Closed"
    assert doc.metadata["status_category"] == "done"


def test_done_status_name_without_category_is_not_classified() -> None:
    doc = process_jira_issue(
        "https://example.atlassian.net",
        _issue("RD-201", assignee={"displayName": "Ada"}, status="Done"),
    )
    assert doc is not None
    assert doc.metadata["status"] == "Done"
    assert "status_category" not in doc.metadata
