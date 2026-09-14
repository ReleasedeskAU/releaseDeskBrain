"""GitHub repository overview and README documents for Ask.

Repo JSON has no commit_count field. The Code tab number is the commit history
length on the selected branch (default branch unless the connector sets one).
REST ``state=closed`` includes merged PRs. Overview stores merged vs
closed-without-merge as separate snapshot counts (GraphQL states MERGED and
CLOSED, or ``merged_at`` on the closed list — never the lazy ``merged`` flag).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timezone

from github import RateLimitExceededException
from github.GithubException import GithubException, UnknownObjectException
from github.Repository import Repository

from onyx.access.models import ExternalAccess
from onyx.configs.constants import DocumentSource
from onyx.connectors.models import Document, TextSection
from onyx.utils.logger import setup_logger

logger = setup_logger()

MAX_BRANCH_NAMES = 200
MAX_CONTRIBUTORS = 200
MAX_README_BYTES = 1_000_000
_GITHUB_EMPTY_REPO_STATUS = 409
_CONTRIBUTORS_COMPUTING_STATUS = 202

# GraphQL CLOSED is closed without merge; REST state=closed includes merged.
_PR_STATE_COUNTS_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    open: pullRequests(states: OPEN) { totalCount }
    merged: pullRequests(states: MERGED) { totalCount }
    closedUnmerged: pullRequests(states: CLOSED) { totalCount }
  }
}
"""


@dataclass
class ContributorFact:
    login: str
    contributions: int


@dataclass
class LastCommitStats:
    sha: str
    message: str
    additions: int | None
    deletions: int | None
    html_url: str | None


@dataclass
class RepoOverviewFacts:
    full_name: str
    html_url: str
    default_branch: str
    description: str | None = None
    homepage: str | None = None
    commit_count: int | None = None
    branch_count: int | None = None
    branch_names: list[str] = field(default_factory=list)
    contributors: list[ContributorFact] = field(default_factory=list)
    contributors_pending: bool = False
    open_pr_count: int | None = None
    merged_pr_count: int | None = None
    closed_unmerged_pr_count: int | None = None
    last_commit: LastCommitStats | None = None
    readme_text: str | None = None
    readme_path: str | None = None
    readme_html_url: str | None = None
    pushed_at: object | None = None


def repo_document_id(full_name: str) -> str:
    """Stable Ask id for a GitHub repository overview document."""
    return f"{DocumentSource.GITHUB.value}:{full_name}:repo"


def readme_document_id(full_name: str) -> str:
    """Stable Ask id for a GitHub README document."""
    return f"{DocumentSource.GITHUB.value}:{full_name}:readme"


def _int_or_none(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _paginated_total_count(paginated: object) -> int | None:
    """GitHub list endpoints do not always send a total; PyGithub derives one."""
    try:
        return _int_or_none(getattr(paginated, "totalCount"))
    except RateLimitExceededException:
        raise
    except GithubException:
        return None


def _safe_str(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None
    return None


def fetch_repo_overview_facts(repo: Repository, branch: str) -> RepoOverviewFacts:
    """Load overview fields from GitHub. Missing optional APIs stay None; they do not fail the sync.

    Args:
        repo: Authenticated PyGithub repository.
        branch: Default branch, or the connector's configured branch.

    Returns:
        Snapshot facts for one repository. Contributors may be pending (HTTP 202).

    Raises:
        GithubException: Unexpected non-optional GitHub errors (auth, 500).
    """
    facts = RepoOverviewFacts(
        full_name=repo.full_name,
        html_url=repo.html_url,
        default_branch=branch,
        description=_safe_str(getattr(repo, "description", None)),
        homepage=_safe_str(getattr(repo, "homepage", None)),
        pushed_at=getattr(repo, "pushed_at", None),
    )
    _fill_branches(repo, facts)
    _fill_commits(repo, branch, facts)
    _fill_contributors(repo, facts)
    _fill_pr_counts(repo, facts)
    _fill_readme(repo, branch, facts)
    return facts


def _fill_branches(repo: Repository, facts: RepoOverviewFacts) -> None:
    try:
        branches = repo.get_branches()
        facts.branch_count = _paginated_total_count(branches)
        names: list[str] = []
        for i, branch in enumerate(branches):
            if i >= MAX_BRANCH_NAMES:
                break
            name = _safe_str(getattr(branch, "name", None))
            if name:
                names.append(name)
        facts.branch_names = names
        if facts.branch_count is None:
            facts.branch_count = len(names)
    except RateLimitExceededException:
        raise
    except GithubException as e:
        logger.warning("Could not list branches for %s: %s", facts.full_name, e)


def _fill_commits(repo: Repository, branch: str, facts: RepoOverviewFacts) -> None:
    try:
        commits = repo.get_commits(sha=branch)
        facts.commit_count = _paginated_total_count(commits)
        page = commits.get_page(0)
        if not page:
            if facts.commit_count is None:
                facts.commit_count = 0
            return
        commit = page[0]
        stats = getattr(commit, "stats", None)
        git_commit = getattr(commit, "commit", None)
        message = _safe_str(getattr(git_commit, "message", None) if git_commit is not None else None)
        if message is None:
            message = _safe_str(getattr(commit, "message", None)) or ""
        facts.last_commit = LastCommitStats(
            sha=str(getattr(commit, "sha", "") or ""),
            message=message.split("\n", 1)[0],
            additions=_int_or_none(getattr(stats, "additions", None) if stats is not None else None),
            deletions=_int_or_none(getattr(stats, "deletions", None) if stats is not None else None),
            html_url=_safe_str(getattr(commit, "html_url", None)),
        )
    except RateLimitExceededException:
        raise
    except GithubException as e:
        if e.status == _GITHUB_EMPTY_REPO_STATUS:
            facts.commit_count = 0
            return
        logger.warning("Could not list commits for %s: %s", facts.full_name, e)


def _fill_contributors(repo: Repository, facts: RepoOverviewFacts) -> None:
    try:
        contributors = repo.get_contributors()
        collected: list[ContributorFact] = []
        for i, person in enumerate(contributors):
            if i >= MAX_CONTRIBUTORS:
                break
            raw = getattr(person, "raw_data", None) or {}
            login = _safe_str(raw.get("login") if isinstance(raw, dict) else None) or _safe_str(
                getattr(person, "login", None)
            )
            if not login:
                continue
            contributions = _int_or_none(
                raw.get("contributions") if isinstance(raw, dict) else getattr(person, "contributions", None)
            )
            collected.append(
                ContributorFact(login=login, contributions=contributions if contributions is not None else 0)
            )
        facts.contributors = collected
    except RateLimitExceededException:
        raise
    except GithubException as e:
        if e.status == _CONTRIBUTORS_COMPUTING_STATUS:
            facts.contributors_pending = True
            return
        logger.warning("Could not list contributors for %s: %s", facts.full_name, e)


def _fill_pr_counts(repo: Repository, facts: RepoOverviewFacts) -> None:
    """Fill open / merged / closed-unmerged snapshot counts. GraphQL first, then search, then closed-list merged_at."""
    graphql_counts = _graphql_pr_state_counts(repo)
    if graphql_counts is not None:
        facts.open_pr_count, facts.merged_pr_count, facts.closed_unmerged_pr_count = (
            graphql_counts
        )
        return
    try:
        facts.open_pr_count = _paginated_total_count(repo.get_pulls(state="open"))
    except RateLimitExceededException:
        raise
    except GithubException as e:
        logger.warning("Could not count open pull requests for %s: %s", facts.full_name, e)
    search_counts = _search_pr_state_counts(repo)
    if search_counts is not None:
        if facts.open_pr_count is None:
            facts.open_pr_count = search_counts[0]
        facts.merged_pr_count = search_counts[1]
        facts.closed_unmerged_pr_count = search_counts[2]
        return
    try:
        merged, closed_unmerged = _split_closed_prs_from_list(repo)
        facts.merged_pr_count = merged
        facts.closed_unmerged_pr_count = closed_unmerged
    except RateLimitExceededException:
        raise
    except GithubException as e:
        logger.warning(
            "Could not split closed pull requests for %s: %s", facts.full_name, e
        )


def _graphql_pr_state_counts(repo: Repository) -> tuple[int, int, int] | None:
    """Open / merged / closed-unmerged via GraphQL totalCount. None if GraphQL is unavailable."""
    requester = getattr(repo, "_requester", None)
    if requester is None:
        return None
    owner, _, name = repo.full_name.partition("/")
    if not owner or not name:
        return None
    try:
        _headers, payload = requester.requestJsonAndCheck(
            "POST",
            "/graphql",
            input={
                "query": _PR_STATE_COUNTS_QUERY,
                "variables": {"owner": owner, "name": name},
            },
        )
    except RateLimitExceededException:
        raise
    except GithubException:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("errors"):
        return None
    repository = (payload.get("data") or {}).get("repository")
    if not isinstance(repository, dict):
        return None
    open_count = _nested_total_count(repository.get("open"))
    merged_count = _nested_total_count(repository.get("merged"))
    closed_unmerged = _nested_total_count(repository.get("closedUnmerged"))
    if open_count is None or merged_count is None or closed_unmerged is None:
        return None
    return open_count, merged_count, closed_unmerged


def _nested_total_count(node: object) -> int | None:
    if not isinstance(node, dict):
        return None
    return _int_or_none(node.get("totalCount"))


def _search_pr_state_counts(repo: Repository) -> tuple[int, int, int] | None:
    """Search API total_count fallback. None when search is disabled or incomplete."""
    requester = getattr(repo, "_requester", None)
    if requester is None:
        return None
    open_count = _search_issue_total(requester, repo.full_name, "is:open")
    merged_count = _search_issue_total(requester, repo.full_name, "is:merged")
    closed_unmerged = _search_issue_total(
        requester, repo.full_name, "is:closed is:unmerged"
    )
    if open_count is None or merged_count is None or closed_unmerged is None:
        return None
    return open_count, merged_count, closed_unmerged


def _search_issue_total(requester: object, full_name: str, extra: str) -> int | None:
    try:
        _headers, payload = requester.requestJsonAndCheck(
            "GET",
            "/search/issues",
            parameters={"q": f"repo:{full_name} is:pr {extra}", "per_page": 1},
        )
    except RateLimitExceededException:
        raise
    except GithubException:
        return None
    if not isinstance(payload, dict):
        return None
    return _int_or_none(payload.get("total_count"))


def _split_closed_prs_from_list(repo: Repository) -> tuple[int, int]:
    """Count merged vs closed-unmerged from the closed list using merged_at only."""
    merged = 0
    closed_unmerged = 0
    for pull in repo.get_pulls(state="closed"):
        if _closed_pr_was_merged(pull):
            merged += 1
        else:
            closed_unmerged += 1
    return merged, closed_unmerged


def _closed_pr_was_merged(pull: object) -> bool:
    """True when the list payload has merged_at. Does not touch pull.merged (extra GET)."""
    raw = getattr(pull, "raw_data", None)
    if not isinstance(raw, dict):
        return False
    return raw.get("merged_at") not in (None, "")


def _fill_readme(repo: Repository, branch: str, facts: RepoOverviewFacts) -> None:
    try:
        content = repo.get_readme(ref=branch)
    except TypeError:
        try:
            content = repo.get_readme()
        except RateLimitExceededException:
            raise
        except (UnknownObjectException, GithubException) as e:
            if isinstance(e, UnknownObjectException) or getattr(e, "status", None) == 404:
                return
            logger.warning("Could not fetch README for %s: %s", facts.full_name, e)
            return
    except UnknownObjectException:
        return
    except RateLimitExceededException:
        raise
    except GithubException as e:
        if e.status == 404:
            return
        logger.warning("Could not fetch README for %s: %s", facts.full_name, e)
        return

    try:
        raw = content.decoded_content
    except RateLimitExceededException:
        raise
    except GithubException as e:
        logger.warning("Could not decode README for %s: %s", facts.full_name, e)
        return
    if raw is None or not isinstance(raw, (bytes, bytearray)):
        return
    if len(raw) > MAX_README_BYTES or b"\x00" in raw[:1024]:
        return
    try:
        text = bytes(raw).decode("utf-8")
    except UnicodeDecodeError:
        text = bytes(raw).decode("utf-8", errors="replace")
    facts.readme_text = text
    facts.readme_path = _safe_str(getattr(content, "path", None)) or "README"
    facts.readme_html_url = _safe_str(getattr(content, "html_url", None))


def map_overview_to_document(
    facts: RepoOverviewFacts,
    repo_external_access: ExternalAccess | None,
) -> Document:
    """Turn overview facts into one searchable Repository document.

    Args:
        facts: Snapshot from fetch_repo_overview_facts.
        repo_external_access: EE permission payload, or None.

    Returns:
        Document with object_type=Repository. Does not include the README body.
    """
    owner_name, _, repo_name = facts.full_name.partition("/")
    lines = [
        "Repository Information:",
        f"- Name: {facts.full_name}",
        f"- Default branch: {facts.default_branch}",
        f"- Homepage: {facts.homepage or 'N/A'}",
        f"- Commit count ({facts.default_branch}): {_fmt_count(facts.commit_count)}",
        f"- Branch count: {_fmt_count(facts.branch_count)}",
        f"- Open pull requests: {_fmt_count(facts.open_pr_count)}",
        f"- Merged pull requests: {_fmt_count(facts.merged_pr_count)}",
        f"- Closed without merging: {_fmt_count(facts.closed_unmerged_pr_count)}",
    ]
    if facts.branch_names:
        lines.append(f"- Branches: {', '.join(facts.branch_names)}")
    if facts.last_commit is not None:
        last = facts.last_commit
        added = _fmt_count(last.additions)
        deleted = _fmt_count(last.deletions)
        lines.append(f"- Last commit: {last.sha[:12]} {last.message}".rstrip())
        lines.append(f"- Last commit lines changed: +{added} / -{deleted}")
    if facts.contributors_pending:
        lines.append("- Contributors: GitHub is still computing contributor stats; re-sync later.")
    elif facts.contributors:
        lines.append("- Contributors (login + commit count on the default branch):")
        for person in facts.contributors:
            lines.append(f"  - {person.login}: {person.contributions}")
    if facts.description:
        lines.extend(["", "Description:", facts.description])
    else:
        lines.extend(
            [
                "",
                "Description: (none on GitHub — see the README document for why this repository exists)",
            ]
        )

    updated_at = None
    if facts.pushed_at is not None and getattr(facts.pushed_at, "tzinfo", None) is None:
        updated_at = facts.pushed_at.replace(tzinfo=timezone.utc)
    elif facts.pushed_at is not None:
        updated_at = facts.pushed_at

    metadata: dict[str, str | list[str]] = {
        "object_type": "Repository",
        "repo": facts.full_name,
        "default_branch": facts.default_branch,
        "link": facts.html_url,
    }
    if facts.homepage:
        metadata["homepage"] = facts.homepage
    if facts.commit_count is not None:
        metadata["commit_count"] = str(facts.commit_count)
    if facts.branch_count is not None:
        metadata["branch_count"] = str(facts.branch_count)
    if facts.open_pr_count is not None:
        metadata["open_pr_count"] = str(facts.open_pr_count)
    if facts.merged_pr_count is not None:
        metadata["merged_pr_count"] = str(facts.merged_pr_count)
    if facts.closed_unmerged_pr_count is not None:
        metadata["closed_unmerged_pr_count"] = str(facts.closed_unmerged_pr_count)
    if facts.last_commit is not None:
        if facts.last_commit.additions is not None:
            metadata["last_commit_additions"] = str(facts.last_commit.additions)
        if facts.last_commit.deletions is not None:
            metadata["last_commit_deletions"] = str(facts.last_commit.deletions)

    return Document(
        id=repo_document_id(facts.full_name),
        sections=[TextSection(link=facts.html_url, text="\n".join(lines))],
        source=DocumentSource.GITHUB,
        external_access=repo_external_access,
        semantic_identifier=facts.full_name,
        title=facts.full_name,
        doc_updated_at=updated_at,
        doc_metadata={
            "repo": facts.full_name,
            "hierarchy": {
                "source_path": [owner_name, repo_name, "repository"],
                "owner": owner_name,
                "repo": repo_name,
                "object_type": "repository",
            },
        },
        metadata=metadata,
    )


def map_readme_to_document(
    facts: RepoOverviewFacts,
    repo_external_access: ExternalAccess | None,
) -> Document | None:
    """README body as its own document. None when GitHub has no README.

    Args:
        facts: Snapshot that may include readme_text.
        repo_external_access: EE permission payload, or None.

    Returns:
        Document with object_type=Readme, or None if README was missing.
    """
    if facts.readme_text is None:
        return None
    owner_name, _, repo_name = facts.full_name.partition("/")
    path = facts.readme_path or "README"
    link = facts.readme_html_url or f"{facts.html_url}/blob/{facts.default_branch}/{path}"
    return Document(
        id=readme_document_id(facts.full_name),
        sections=[TextSection(link=link, text=facts.readme_text)],
        source=DocumentSource.GITHUB,
        external_access=repo_external_access,
        semantic_identifier=f"{facts.full_name} README",
        title=f"{facts.full_name} {path}",
        doc_metadata={
            "repo": facts.full_name,
            "hierarchy": {
                "source_path": [owner_name, repo_name, "readme", path],
                "owner": owner_name,
                "repo": repo_name,
                "object_type": "readme",
            },
        },
        metadata={
            "object_type": "Readme",
            "repo": facts.full_name,
            "path": path,
            "branch": facts.default_branch,
            "link": link,
        },
    )


def slim_overview_documents(
    facts: RepoOverviewFacts,
    repo_external_access: ExternalAccess | None,
) -> list[Document]:
    """Stable ids for prune/perm-sync without embedding overview text."""
    docs = [
        Document(
            id=repo_document_id(facts.full_name),
            sections=[],
            external_access=repo_external_access,
            source=DocumentSource.GITHUB,
            semantic_identifier="",
            metadata={},
        )
    ]
    if facts.readme_text is not None:
        docs.append(
            Document(
                id=readme_document_id(facts.full_name),
                sections=[],
                external_access=repo_external_access,
                source=DocumentSource.GITHUB,
                semantic_identifier="",
                metadata={},
            )
        )
    return docs


def _fmt_count(value: int | None) -> str:
    return str(value) if value is not None else "unknown"
