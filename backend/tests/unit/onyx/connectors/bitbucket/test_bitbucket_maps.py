from onyx.connectors.bitbucket.connector import BitbucketConnector
from onyx.connectors.bitbucket.utils import (
    commit_is_older_than_start,
    default_branch_name,
    map_commit_to_document,
    map_readme_to_document,
    map_repo_to_document,
    pick_readme_path,
    timestamp_in_window,
)


def test_pick_readme_prefers_markdown() -> None:
    path = pick_readme_path(
        [
            {"type": "commit_directory", "path": "src"},
            {"type": "commit_file", "path": "README"},
            {"type": "commit_file", "path": "README.md"},
        ]
    )
    assert path == "README.md"


def test_pick_readme_empty_when_missing() -> None:
    assert pick_readme_path([{"type": "commit_file", "path": "setup.py"}]) is None


def test_map_repo_and_commit_and_readme() -> None:
    repo_doc = map_repo_to_document(
        {
            "name": "Desk",
            "description": "Release tools",
            "language": "python",
            "mainbranch": {"name": "main"},
            "project": {"key": "RD"},
            "links": {"html": {"href": "https://bitbucket.org/acme/desk"}},
            "updated_on": "2026-01-02T00:00:00+00:00",
        },
        "acme",
        "desk",
    )
    assert repo_doc.metadata["object_type"] == "Repository"
    assert "Release tools" in repo_doc.sections[0].text
    assert repo_doc.id.endswith(":repo")

    readme_doc = map_readme_to_document(
        "acme", "desk", "README.md", "# Hello", "main"
    )
    assert readme_doc.metadata["object_type"] == "Readme"
    assert readme_doc.sections[0].text == "# Hello"

    commit_doc = map_commit_to_document(
        {
            "hash": "abc123def",
            "message": "Fix login\n\nDetails here",
            "author": {"user": {"display_name": "Ada"}, "raw": "Ada <a@b.co>"},
            "date": "2026-03-01T12:00:00+00:00",
            "links": {"html": {"href": "https://bitbucket.org/acme/desk/commits/abc123def"}},
        },
        "acme",
        "desk",
    )
    assert commit_doc.metadata["object_type"] == "Commit"
    assert "Fix login" in commit_doc.sections[0].text
    assert "diff" not in commit_doc.sections[0].text.lower()
    assert commit_doc.id.endswith(":commit:abc123def")


def test_commit_window_stops_before_start() -> None:
    start = 1_700_000_000.0
    assert commit_is_older_than_start("2020-01-01T00:00:00+00:00", start) is True
    assert timestamp_in_window("2024-01-01T00:00:00+00:00", start, None) is True


def test_default_branch_missing_is_none() -> None:
    assert default_branch_name({}) is None
    assert default_branch_name({"mainbranch": {"name": "develop"}}) == "develop"


def test_enabled_stages_follow_include_flags() -> None:
    connector = BitbucketConnector(
        workspace="acme",
        include_prs=False,
        include_repo=True,
        include_readme=True,
        include_commits=False,
    )
    assert connector._enabled_stages() == ["repo", "readme"]
    prs_only = BitbucketConnector(
        workspace="acme",
        include_prs=True,
        include_repo=False,
        include_readme=False,
        include_commits=False,
    )
    assert prs_only._enabled_stages() == ["prs"]
