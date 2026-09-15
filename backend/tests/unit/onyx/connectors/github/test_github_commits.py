from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from onyx.connectors.github.commits import (
    commit_document_id,
    map_commit_to_document,
)
from onyx.connectors.github.connector import GithubConnector
from onyx.connectors.github.models import SerializedRepository
from onyx.connectors.models import Document
from tests.unit.onyx.connectors.utils import load_everything_from_checkpoint_connector


def _file(name: str, patch: str) -> MagicMock:
    entry = MagicMock()
    entry.filename = name
    entry.additions = 2
    entry.deletions = 1
    entry.patch = patch
    return entry


def test_map_commit_includes_files_and_omits_patch() -> None:
    commit = MagicMock()
    commit.sha = "abc123def456"
    commit.html_url = "https://github.com/acme/app/commit/abc123def456"
    commit.commit = MagicMock()
    commit.commit.message = "Harden login\n\nDetails"
    commit.commit.author = MagicMock(date=datetime(2026, 9, 1, tzinfo=timezone.utc))
    commit.author = MagicMock(login="ada")
    commit.stats = MagicMock(additions=10, deletions=3)
    commit.files = [
        _file("src/login.ts", "@@ unused patch body @@"),
        _file("src/auth.ts", "diff --git a/src/auth.ts"),
    ]

    doc = map_commit_to_document(
        commit, "acme/app", "https://github.com/acme/app", "main", None
    )
    text = doc.sections[0].text
    assert doc.metadata["object_type"] == "Commit"
    assert doc.metadata["files_touched"] == "2"
    assert doc.metadata["additions"] == "10"
    assert doc.metadata["deletions"] == "3"
    assert "src/login.ts" in text
    assert "Harden login" in text
    assert "unused patch body" not in text
    assert "diff --git" not in text
    assert doc.id == commit_document_id("acme/app", "abc123def456")


def test_same_sha_on_two_branches_is_one_document() -> None:
    import time

    sha = "abc123def456"
    listed = MagicMock()
    listed.sha = sha
    listed.commit = MagicMock()
    listed.commit.author = MagicMock(date=datetime(2026, 9, 1, tzinfo=timezone.utc))
    listed.html_url = f"https://github.com/test-org/test-repo/commit/{sha}"

    detail = MagicMock()
    detail.sha = sha
    detail.html_url = listed.html_url
    detail.commit = MagicMock(message="Shared commit")
    detail.commit.author = MagicMock(date=datetime(2026, 9, 1, tzinfo=timezone.utc))
    detail.author = MagicMock(login="ada")
    detail.stats = MagicMock(additions=4, deletions=1)
    detail.files = [_file("README.md", "@@ patch must not index @@")]

    main = MagicMock()
    main.name = "main"
    feat = MagicMock()
    feat.name = "feat"

    mock_repo = MagicMock()
    mock_repo.name = "test-repo"
    mock_repo.id = 1
    mock_repo.full_name = "test-org/test-repo"
    mock_repo.html_url = "https://github.com/test-org/test-repo"
    mock_repo.default_branch = "main"
    mock_repo.raw_headers = {"status": "200 OK"}
    mock_repo.raw_data = {"id": 1, "name": "test-repo", "full_name": "test-org/test-repo"}
    mock_repo.get_branches.return_value = [main, feat]
    mock_paginated = MagicMock()
    mock_paginated.get_page.side_effect = lambda page: [listed] if page == 0 else []
    mock_repo.get_commits.return_value = mock_paginated
    mock_repo.get_commit.return_value = detail

    connector = GithubConnector(
        repo_owner="test-org",
        repositories="test-repo",
        include_prs=False,
        include_issues=False,
        include_files=False,
        include_overview=False,
        include_commits=True,
    )
    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    connector.github_client = mock_client

    with patch.object(SerializedRepository, "to_Repository", return_value=mock_repo):
        outputs = load_everything_from_checkpoint_connector(
            connector, 0, time.time()
        )

    docs = [
        item
        for batch in outputs
        for item in batch.items
        if isinstance(item, Document)
    ]
    assert [doc.metadata.get("object_type") for doc in docs] == ["Commit"]
    assert mock_repo.get_commit.call_count == 1
    assert "@@ patch must not index @@" not in docs[0].sections[0].text
    assert docs[0].metadata["files_touched"] == "1"


def test_commit_list_uses_sync_window() -> None:
    main = MagicMock()
    main.name = "main"
    mock_repo = MagicMock()
    mock_repo.name = "test-repo"
    mock_repo.id = 1
    mock_repo.full_name = "test-org/test-repo"
    mock_repo.html_url = "https://github.com/test-org/test-repo"
    mock_repo.default_branch = "main"
    mock_repo.raw_headers = {"status": "200 OK"}
    mock_repo.raw_data = {"id": 1, "name": "test-repo", "full_name": "test-org/test-repo"}
    mock_repo.get_branches.return_value = [main]
    mock_paginated = MagicMock()
    mock_paginated.get_page.return_value = []
    mock_repo.get_commits.return_value = mock_paginated

    connector = GithubConnector(
        repo_owner="test-org",
        repositories="test-repo",
        include_prs=False,
        include_issues=False,
        include_files=False,
        include_overview=False,
        include_commits=True,
    )
    mock_client = MagicMock()
    mock_client.get_repo.return_value = mock_repo
    connector.github_client = mock_client

    start = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    end = datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp()
    with patch.object(SerializedRepository, "to_Repository", return_value=mock_repo):
        load_everything_from_checkpoint_connector(connector, start, end)

    assert mock_repo.get_commits.call_count >= 1
    kwargs = mock_repo.get_commits.call_args.kwargs
    assert kwargs["sha"] == "main"
    assert kwargs["since"] is not None
    assert kwargs["until"] is not None
    assert mock_repo.get_commit.call_count == 0
