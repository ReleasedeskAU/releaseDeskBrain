from types import SimpleNamespace

from onyx.connectors.gitlab.connector import (
    _convert_issue_to_document,
    _convert_merge_request_to_document,
)
from onyx.connectors.gitlab.docs import (
    map_commit_to_document,
    map_overview_to_document,
    map_readme_to_document,
)


def test_map_overview_readme_and_commit() -> None:
    overview = map_overview_to_document(
        "acme/app",
        "App",
        "Release tools",
        "main",
        "https://gitlab.com/acme/app",
        "private",
    )
    assert overview.metadata["object_type"] == "Repository"
    assert overview.metadata["repo"] == "acme/app"
    assert "Release tools" in overview.sections[0].text
    assert overview.id.endswith(":overview")

    readme = map_readme_to_document(
        "acme/app", "README.md", "# Hello", "main", "https://gitlab.com/acme/app"
    )
    assert readme.metadata["object_type"] == "Readme"
    assert readme.metadata["repo"] == "acme/app"
    assert readme.sections[0].text == "# Hello"

    commit = map_commit_to_document(
        SimpleNamespace(
            id="abc123def",
            message="Fix login\n\nDetails",
            title="Fix login",
            author_name="Ada",
            authored_date="2026-03-01T12:00:00.000Z",
            web_url="https://gitlab.com/acme/app/-/commit/abc123def",
        ),
        "acme/app",
        "https://gitlab.com/acme/app",
    )
    assert commit.metadata["object_type"] == "Commit"
    assert commit.metadata["repo"] == "acme/app"
    assert "Fix login" in commit.sections[0].text
    assert "diff" not in commit.sections[0].text.lower()
    assert commit.id.endswith(":commit:abc123def")


def test_issue_tags_status_and_dates_omits_empty_assignee_priority() -> None:
    issue = SimpleNamespace(
        web_url="https://gitlab.com/acme/app/-/work_items/3",
        title="test issue 3",
        description=None,
        state="opened",
        type="ISSUE",
        iid=3,
        author={"name": "Release Desk", "username": "ReleasedeskAU"},
        assignees=[],
        labels=[],
        created_at="2026-09-15T12:40:50.661Z",
        updated_at="2026-09-15T12:40:50.661Z",
        due_date=None,
    )
    doc = _convert_issue_to_document(issue, "acme/app")
    assert doc.metadata["object_type"] == "Issue"
    assert doc.metadata["status"] == "opened"
    assert doc.metadata["state"] == "opened"
    assert doc.metadata["key"] == "#3"
    assert doc.metadata["repo"] == "acme/app"
    assert doc.metadata["created"].startswith("2026-09-15")
    assert doc.metadata["updated"].startswith("2026-09-15")
    assert "assignee" not in doc.metadata
    assert "priority" not in doc.metadata
    assert "duedate" not in doc.metadata


def test_issue_tags_assignee_and_due_date() -> None:
    issue = SimpleNamespace(
        web_url="https://gitlab.com/acme/app/-/work_items/1",
        title="assigned",
        description="",
        state="opened",
        type="ISSUE",
        iid=1,
        author={"username": "owner"},
        assignees=[{"name": "****", "username": "ada"}],
        labels=["board"],
        created_at="2026-09-15T12:00:00Z",
        updated_at="2026-09-15T12:00:00Z",
        due_date="2026-09-20",
    )
    doc = _convert_issue_to_document(issue, "acme/app")
    assert doc.metadata["assignee"] == ["ada"]
    assert doc.metadata["duedate"].startswith("2026-09-20")
    assert doc.metadata["labels"] == ["board"]


def test_mr_tags_merged_state() -> None:
    mr = SimpleNamespace(
        web_url="https://gitlab.com/acme/app/-/merge_requests/2",
        title="Polish landing",
        description="",
        state="merged",
        iid=2,
        author={"name": "Release Desk"},
        assignees=[],
        labels=[],
        created_at="2026-09-07T00:00:00Z",
        updated_at="2026-09-08T00:00:00Z",
        merged_at="2026-09-08T00:00:00Z",
    )
    doc = _convert_merge_request_to_document(mr, "acme/app")
    assert doc.metadata["object_type"] == "MergeRequest"
    assert doc.metadata["status"] == "merged"
    assert doc.metadata["merged"] == "true"
    assert doc.metadata["repo"] == "acme/app"
    assert "assignee" not in doc.metadata
