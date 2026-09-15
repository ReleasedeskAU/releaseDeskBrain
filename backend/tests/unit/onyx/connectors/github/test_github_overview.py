from unittest.mock import MagicMock, PropertyMock

from github.GithubException import GithubException, UnknownObjectException

from onyx.connectors.github.overview import (
    fetch_repo_overview_facts,
    map_overview_to_document,
    map_readme_to_document,
    readme_document_id,
    repo_document_id,
)


def _paginated(items: list, total: int | None = None) -> MagicMock:
    mock = MagicMock()
    mock.totalCount = len(items) if total is None else total
    mock.__iter__.return_value = iter(items)
    mock.get_page.return_value = items
    return mock


def _repo() -> MagicMock:
    repo = MagicMock()
    repo.full_name = "ReleasedeskAU/website-test"
    repo.html_url = "https://github.com/ReleasedeskAU/website-test"
    repo.description = "Demo site for connector tests"
    repo.homepage = "https://website-test-eta-blue.vercel.app"
    repo.default_branch = "main"
    repo.pushed_at = None
    return repo


def test_overview_maps_commit_branch_pr_and_contributors() -> None:
    repo = _repo()
    branches = [MagicMock(name="main"), MagicMock(name="dev")]
    branches[0].name = "main"
    branches[1].name = "dev"
    repo.get_branches.return_value = _paginated(branches, total=4)

    last = MagicMock()
    last.sha = "abc123def456"
    last.html_url = "https://github.com/ReleasedeskAU/website-test/commit/abc123def456"
    last.commit = MagicMock(message="Ship landing page\n\nDetails")
    last.stats = MagicMock(additions=80, deletions=12)
    last.files = MagicMock(side_effect=AssertionError("do not load the diff"))
    commits = _paginated([last], total=14)
    repo.get_commits.return_value = commits

    person = MagicMock()
    person.raw_data = {"login": "ada", "contributions": 9}
    repo.get_contributors.return_value = _paginated([person])

    repo._requester.requestJsonAndCheck.return_value = (
        {},
        {
            "data": {
                "repository": {
                    "open": {"totalCount": 2},
                    "merged": {"totalCount": 4},
                    "closedUnmerged": {"totalCount": 1},
                }
            }
        },
    )

    readme = MagicMock()
    readme.decoded_content = b"# website-test\nWhy this repo exists.\n"
    readme.path = "README.md"
    readme.html_url = "https://github.com/ReleasedeskAU/website-test/blob/main/README.md"
    repo.get_readme.return_value = readme

    facts = fetch_repo_overview_facts(repo, "main")
    assert facts.commit_count == 14
    assert facts.branch_count == 4
    assert facts.branch_names == ["main", "dev"]
    assert facts.open_pr_count == 2
    assert facts.merged_pr_count == 4
    assert facts.closed_unmerged_pr_count == 1
    assert facts.last_commit is not None
    assert facts.last_commit.additions == 80
    assert facts.last_commit.deletions == 12
    assert facts.contributors[0].login == "ada"
    assert facts.contributors[0].contributions == 9
    assert facts.readme_text is not None
    assert "Why this repo exists" in facts.readme_text

    doc = map_overview_to_document(facts, None)
    assert doc.metadata["object_type"] == "Repository"
    assert doc.metadata["commit_count"] == "14"
    assert doc.metadata["merged_pr_count"] == "4"
    assert doc.metadata["closed_unmerged_pr_count"] == "1"
    assert "closed_pr_count" not in doc.metadata
    assert "14" in doc.sections[0].text
    assert "Merged pull requests: 4" in doc.sections[0].text
    assert "Closed without merging: 1" in doc.sections[0].text
    assert "includes merged" not in doc.sections[0].text.lower()
    assert "diff" not in doc.sections[0].text.lower()
    assert doc.id == repo_document_id("ReleasedeskAU/website-test")

    readme_doc = map_readme_to_document(facts, None)
    assert readme_doc is not None
    assert readme_doc.metadata["object_type"] == "Readme"
    assert "Why this repo exists" in readme_doc.sections[0].text
    assert readme_doc.id == readme_document_id("ReleasedeskAU/website-test")


def test_missing_readme_does_not_emit_readme_document() -> None:
    repo = _repo()
    repo.description = None
    repo.get_branches.return_value = _paginated([])
    repo.get_commits.return_value = _paginated([], total=0)
    repo.get_contributors.return_value = _paginated([])
    repo.get_pulls.return_value = _paginated([], total=0)
    repo.get_readme.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})

    facts = fetch_repo_overview_facts(repo, "main")
    assert facts.readme_text is None
    assert map_readme_to_document(facts, None) is None
    overview = map_overview_to_document(facts, None)
    assert "none on GitHub" in overview.sections[0].text


def test_contributors_202_does_not_fail_overview() -> None:
    repo = _repo()
    repo.get_branches.return_value = _paginated([])
    repo.get_commits.return_value = _paginated([], total=0)
    repo.get_contributors.side_effect = GithubException(
        202, {"message": "Accepted"}, {}
    )
    repo.get_pulls.return_value = _paginated([], total=0)
    repo.get_readme.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})

    facts = fetch_repo_overview_facts(repo, "main")
    assert facts.contributors_pending is True
    assert facts.contributors == []
    text = map_overview_to_document(facts, None).sections[0].text
    assert "still computing contributor stats" in text


def test_closed_list_splits_merged_using_merged_at_not_lazy_merged() -> None:
    """REST fallback: merged_at on the list payload, never pull.merged (extra GET)."""
    repo = _repo()
    repo.get_branches.return_value = _paginated([])
    repo.get_commits.return_value = _paginated([], total=0)
    repo.get_contributors.return_value = _paginated([])
    repo.get_readme.side_effect = UnknownObjectException(404, {"message": "Not Found"}, {})
    repo._requester.requestJsonAndCheck.side_effect = GithubException(404, {}, {})

    merged_pr = MagicMock()
    merged_pr.raw_data = {"merged_at": "2026-01-02T00:00:00Z"}
    type(merged_pr).merged = PropertyMock(
        side_effect=AssertionError("do not lazy-load merged")
    )
    closed_pr = MagicMock()
    closed_pr.raw_data = {"merged_at": None}
    type(closed_pr).merged = PropertyMock(
        side_effect=AssertionError("do not lazy-load merged")
    )

    def _pulls(state: str = "all", **_kwargs: object) -> MagicMock:
        if state == "open":
            return _paginated([], total=3)
        if state == "closed":
            return _paginated([merged_pr, closed_pr, merged_pr])
        return _paginated([])

    repo.get_pulls.side_effect = _pulls

    facts = fetch_repo_overview_facts(repo, "main")
    assert facts.open_pr_count == 3
    assert facts.merged_pr_count == 2
    assert facts.closed_unmerged_pr_count == 1
    text = map_overview_to_document(facts, None).sections[0].text
    assert "Merged pull requests: 2" in text
    assert "Closed without merging: 1" in text


def test_connector_overview_stage_emits_repo_and_readme() -> None:
    """Overview runs before PRs and does not require the files stage."""
    import time
    from unittest.mock import patch

    from onyx.connectors.github.connector import GithubConnector
    from onyx.connectors.github.models import SerializedRepository
    from onyx.connectors.github.overview import RepoOverviewFacts
    from onyx.connectors.models import Document
    from tests.unit.onyx.connectors.utils import load_everything_from_checkpoint_connector

    facts = RepoOverviewFacts(
        full_name="test-org/test-repo",
        html_url="https://github.com/test-org/test-repo",
        default_branch="main",
        description="Purpose line",
        commit_count=14,
        branch_count=4,
        open_pr_count=1,
        merged_pr_count=2,
        closed_unmerged_pr_count=1,
        readme_text="# Why this repo exists",
        readme_path="README.md",
    )
    connector = GithubConnector(
        repo_owner="test-org",
        repositories="test-repo",
        include_prs=False,
        include_issues=False,
        include_files=False,
        include_overview=True,
        include_commits=False,
    )
    mock_client = MagicMock()
    mock_repo = MagicMock()
    mock_repo.name = "test-repo"
    mock_repo.id = 1
    mock_repo.full_name = "test-org/test-repo"
    mock_repo.default_branch = "main"
    mock_repo.raw_headers = {"status": "200 OK"}
    mock_repo.raw_data = {"id": 1, "name": "test-repo", "full_name": "test-org/test-repo"}
    mock_client.get_repo.return_value = mock_repo
    connector.github_client = mock_client

    with (
        patch.object(SerializedRepository, "to_Repository", return_value=mock_repo),
        patch(
            "onyx.connectors.github.connector.fetch_repo_overview_facts",
            return_value=facts,
        ),
    ):
        outputs = load_everything_from_checkpoint_connector(
            connector, 0, time.time()
        )

    docs = [
        item
        for batch in outputs
        for item in batch.items
        if isinstance(item, Document)
    ]
    types = [doc.metadata.get("object_type") for doc in docs]
    assert types == ["Repository", "Readme"]
    assert "Purpose line" in docs[0].sections[0].text
    assert "Why this repo exists" in docs[1].sections[0].text
