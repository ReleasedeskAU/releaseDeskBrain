from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx

from onyx.configs.app_configs import REQUEST_TIMEOUT_SECONDS
from onyx.configs.constants import DocumentSource
from onyx.connectors.cross_connector_utils.rate_limit_wrapper import rate_limit_builder
from onyx.connectors.models import BasicExpertInfo, Document, ImageSection, TextSection
from onyx.utils.datetime import datetime_to_utc
from onyx.utils.logger import setup_logger
from onyx.utils.retry_after import parse_retry_after_seconds
from onyx.utils.retry_wrapper import retry_builder

logger = setup_logger()

# Upper bound on how long we'll honor a server-provided Retry-After before
# sleeping. Bitbucket's rate-limit window is per-minute.
_MAX_RETRY_AFTER_SLEEP_SECONDS = 60

# Fields requested from Bitbucket PR list endpoint to ensure rich PR data
PR_LIST_RESPONSE_FIELDS: str = ",".join(
    [
        "next",
        "page",
        "pagelen",
        "values.author",
        "values.close_source_branch",
        "values.closed_by",
        "values.comment_count",
        "values.created_on",
        "values.description",
        "values.destination",
        "values.draft",
        "values.id",
        "values.links",
        "values.merge_commit",
        "values.participants",
        "values.reason",
        "values.rendered",
        "values.reviewers",
        "values.source",
        "values.state",
        "values.summary",
        "values.task_count",
        "values.title",
        "values.type",
        "values.updated_on",
    ]
)

# Minimal fields for slim retrieval (IDs + creation time for doc_created_at backfill)
SLIM_PR_LIST_RESPONSE_FIELDS: str = ",".join(
    [
        "next",
        "page",
        "pagelen",
        "values.id",
        "values.created_on",
    ]
)


def parse_bitbucket_datetime(value: str | None) -> datetime | None:
    """Parse a Bitbucket ISO-8601 timestamp into a tz-aware UTC datetime."""
    if not isinstance(value, str):
        return None
    return datetime_to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


# Minimal fields for repository list calls
REPO_LIST_RESPONSE_FIELDS: str = ",".join(
    [
        "next",
        "page",
        "pagelen",
        "values.slug",
        "values.full_name",
        "values.project.key",
    ]
)

REPO_DETAIL_FIELDS: str = ",".join(
    [
        "name",
        "slug",
        "full_name",
        "description",
        "language",
        "updated_on",
        "created_on",
        "website",
        "is_private",
        "mainbranch.name",
        "project.key",
        "links.html.href",
    ]
)

COMMIT_LIST_RESPONSE_FIELDS: str = ",".join(
    [
        "next",
        "page",
        "pagelen",
        "values.hash",
        "values.date",
        "values.message",
        "values.author",
        "values.links.html.href",
    ]
)

README_BASENAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
MAX_README_BYTES = 1_000_000
COMMITS_PAGE_LEN = 50


class BitbucketRetriableError(Exception):
    """Raised for retriable Bitbucket conditions (429, 5xx)."""


class BitbucketNonRetriableError(Exception):
    """Raised for non-retriable Bitbucket client errors (4xx except 429)."""


@retry_builder(
    tries=6,
    delay=1,
    backoff=2,
    max_delay=30,
    exceptions=(BitbucketRetriableError, httpx.RequestError),
)
@rate_limit_builder(max_calls=60, period=60)
def bitbucket_get(
    client: httpx.Client, url: str, params: dict[str, Any] | None = None
) -> httpx.Response:
    """Perform a GET against Bitbucket with retry and rate limiting.

    Retries on 429 and 5xx responses, and on transport errors. Honors
    `Retry-After` header for 429 when present by sleeping before retrying.
    """
    try:
        response = client.get(url, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    except httpx.RequestError:
        # Allow retry_builder to handle retries of transport errors
        raise

    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 429:
            retry_after = (
                parse_retry_after_seconds(e.response.headers.get("Retry-After"))
                if e.response
                else None
            )
            if retry_after is not None:
                time.sleep(min(retry_after, _MAX_RETRY_AFTER_SLEEP_SECONDS))
            raise BitbucketRetriableError("Bitbucket rate limit exceeded (429)") from e
        if status is not None and 500 <= status < 600:
            raise BitbucketRetriableError(f"Bitbucket server error: {status}") from e
        if status is not None and 400 <= status < 500:
            raise BitbucketNonRetriableError(f"Bitbucket client error: {status}") from e
        # Unknown status, propagate
        raise

    return response


def build_auth_client(email: str, api_token: str) -> httpx.Client:
    """Create an authenticated HTTP/1.1 client for Bitbucket Cloud API.

    HTTP/2 is off on purpose: some Bitbucket edges drop Basic auth on h2,
    which looks like a 404 on a private workspace instead of 401.
    """
    return httpx.Client(auth=(email, api_token), http2=False)


def paginate(
    client: httpx.Client,
    url: str,
    params: dict[str, Any] | None = None,
    start_url: str | None = None,
    on_page: Callable[[str | None], None] | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate over paginated Bitbucket API responses yielding individual values.

    Args:
        client: Authenticated HTTP client.
        url: Base collection URL (first page when start_url is None).
        params: Query params for the first page.
        start_url: If provided, start from this absolute URL (ignores params).
        on_page: Optional callback invoked after each page with the next page URL.
    """
    next_url = start_url or url
    # If resuming from a next URL, do not pass params again
    query = params.copy() if params else None
    query = None if start_url else query
    while next_url:
        resp = bitbucket_get(client, next_url, params=query)
        data = resp.json()
        values = data.get("values", [])
        for item in values:
            yield item
        next_url = data.get("next")
        if on_page is not None:
            on_page(next_url)
        # only include params on first call, next_url will contain all necessary params
        query = None


def list_repositories(
    client: httpx.Client, workspace: str, project_key: str | None = None
) -> Iterator[dict[str, Any]]:
    """List repositories in a workspace, optionally filtered by project key."""
    base_url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"
    params: dict[str, Any] = {
        "fields": REPO_LIST_RESPONSE_FIELDS,
        "pagelen": 100,
        # Ensure deterministic ordering
        "sort": "full_name",
    }
    if project_key:
        params["q"] = f'project.key="{project_key}"'
    yield from paginate(client, base_url, params)


def map_pr_to_document(pr: dict[str, Any], workspace: str, repo_slug: str) -> Document:
    """Map a Bitbucket pull request JSON to Onyx Document."""
    pr_id = pr["id"]
    title = pr.get("title") or f"PR {pr_id}"
    description = pr.get("description") or ""
    state = pr.get("state")
    draft = pr.get("draft", False)
    author = pr.get("author", {})
    reviewers = pr.get("reviewers", [])
    participants = pr.get("participants", [])

    link = pr.get("links", {}).get("html", {}).get("href") or (
        f"https://bitbucket.org/{workspace}/{repo_slug}/pull-requests/{pr_id}"
    )

    created_on = pr.get("created_on")
    updated_on = pr.get("updated_on")
    updated_dt = (
        datetime_to_utc(datetime.fromisoformat(updated_on.replace("Z", "+00:00")))
        if isinstance(updated_on, str)
        else None
    )
    created_dt = (
        datetime_to_utc(datetime.fromisoformat(created_on.replace("Z", "+00:00")))
        if isinstance(created_on, str)
        else None
    )

    source_branch = pr.get("source", {}).get("branch", {}).get("name", "")
    destination_branch = pr.get("destination", {}).get("branch", {}).get("name", "")

    approved_by = [
        _get_user_name(p.get("user", {})) for p in participants if p.get("approved")
    ]

    primary_owner = None
    if author:
        primary_owner = BasicExpertInfo(
            display_name=_get_user_name(author),
        )

    secondary_owners = [
        BasicExpertInfo(display_name=_get_user_name(r)) for r in reviewers
    ] or None

    reviewer_names = [_get_user_name(r) for r in reviewers]

    # Create a concise summary of key PR info
    created_date = created_on.split("T")[0] if created_on else "N/A"
    updated_date = updated_on.split("T")[0] if updated_on else "N/A"
    content_text = (
        "Pull Request Information:\n"
        f"- Pull Request ID: {pr_id}\n"
        f"- Title: {title}\n"
        f"- State: {state or 'N/A'} {'(Draft)' if draft else ''}\n"
    )
    if state == "DECLINED":
        content_text += f"- Reason: {pr.get('reason', 'N/A')}\n"
    content_text += (
        f"- Author: {_get_user_name(author) if author else 'N/A'}\n"
        f"- Reviewers: {', '.join(reviewer_names) if reviewer_names else 'N/A'}\n"
        f"- Branch: {source_branch} -> {destination_branch}\n"
        f"- Created: {created_date}\n"
        f"- Updated: {updated_date}"
    )
    if description:
        content_text += f"\n\nDescription:\n{description}"
    sections: list[TextSection | ImageSection] = [
        TextSection(link=link, text=content_text)
    ]

    metadata: dict[str, str | list[str]] = {
        "object_type": "PullRequest",
        "workspace": workspace,
        "repository": repo_slug,
        "pr_key": f"{workspace}/{repo_slug}#{pr_id}",
        "id": str(pr_id),
        "title": title,
        "state": state or "",
        "draft": str(bool(draft)),
        "link": link,
        "author": _get_user_name(author) if author else "",
        "reviewers": reviewer_names,
        "approved_by": approved_by,
        "comment_count": str(pr.get("comment_count", "")),
        "task_count": str(pr.get("task_count", "")),
        "created_on": created_on or "",
        "updated_on": updated_on or "",
        "source_branch": source_branch,
        "destination_branch": destination_branch,
        "closed_by": (
            _get_user_name(pr.get("closed_by", {})) if pr.get("closed_by") else ""
        ),
        "close_source_branch": str(bool(pr.get("close_source_branch", False))),
    }

    return Document(
        id=f"{DocumentSource.BITBUCKET.value}:{workspace}:{repo_slug}:pr:{pr_id}",
        sections=sections,
        source=DocumentSource.BITBUCKET,
        semantic_identifier=f"#{pr_id}: {title}",
        title=title,
        doc_updated_at=updated_dt,
        # NOTE: doc_created_at population not yet verified against live data
        doc_created_at=created_dt,
        primary_owners=[primary_owner] if primary_owner else None,
        secondary_owners=secondary_owners,
        metadata=metadata,
    )


def _get_user_name(user: dict[str, Any]) -> str:
    return user.get("display_name") or user.get("nickname") or "unknown"


def repo_document_id(workspace: str, repo_slug: str) -> str:
    """Stable Ask id for a repository overview document."""
    return f"{DocumentSource.BITBUCKET.value}:{workspace}:{repo_slug}:repo"


def readme_document_id(workspace: str, repo_slug: str) -> str:
    """Stable Ask id for a repository README."""
    return f"{DocumentSource.BITBUCKET.value}:{workspace}:{repo_slug}:readme"


def commit_document_id(workspace: str, repo_slug: str, commit_hash: str) -> str:
    """Stable Ask id for one commit message."""
    return f"{DocumentSource.BITBUCKET.value}:{workspace}:{repo_slug}:commit:{commit_hash}"


def timestamp_in_window(
    value: str | None,
    start: float | None,
    end: float | None,
) -> bool:
    """True when the Bitbucket timestamp is inside [start, end]. Missing dates stay in."""
    dt = parse_bitbucket_datetime(value)
    if dt is None:
        return True
    ts = dt.timestamp()
    if start is not None and ts < start:
        return False
    if end is not None and ts > end:
        return False
    return True


def commit_is_older_than_start(value: str | None, start: float | None) -> bool:
    """True when this commit is strictly before the sync window (newest-first stop)."""
    if start is None:
        return False
    dt = parse_bitbucket_datetime(value)
    if dt is None:
        return False
    return dt.timestamp() < start


def default_branch_name(repo: dict[str, Any]) -> str | None:
    """Default branch from a repository payload. None if Bitbucket omitted it."""
    main = repo.get("mainbranch")
    if not isinstance(main, dict):
        return None
    name = main.get("name")
    return name.strip() if isinstance(name, str) and name.strip() else None


def readme_sort_key(path: str) -> int:
    """Lower is better. Unknown names sort last."""
    base = path.rsplit("/", 1)[-1].lower()
    try:
        return README_BASENAMES.index(base)
    except ValueError:
        return len(README_BASENAMES)


def pick_readme_path(entries: list[dict[str, Any]]) -> str | None:
    """First README-like file in a src directory listing. Directories are ignored."""
    matches: list[str] = []
    for entry in entries:
        if entry.get("type") not in (None, "commit_file", "file"):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        if readme_sort_key(path) < len(README_BASENAMES):
            matches.append(path)
    if not matches:
        return None
    matches.sort(key=readme_sort_key)
    return matches[0]


def map_repo_to_document(repo: dict[str, Any], workspace: str, repo_slug: str) -> Document:
    """Map a Bitbucket repository payload to a Document (overview only)."""
    name = repo.get("name") or repo_slug
    description = repo.get("description") or ""
    language = repo.get("language") or ""
    project_key = (repo.get("project") or {}).get("key") or ""
    branch = default_branch_name(repo) or ""
    link = repo.get("links", {}).get("html", {}).get("href") or (
        f"https://bitbucket.org/{workspace}/{repo_slug}"
    )
    updated_on = repo.get("updated_on")
    created_on = repo.get("created_on")
    content = (
        "Repository Information:\n"
        f"- Name: {name}\n"
        f"- Workspace: {workspace}\n"
        f"- Slug: {repo_slug}\n"
        f"- Project: {project_key or 'N/A'}\n"
        f"- Language: {language or 'N/A'}\n"
        f"- Default branch: {branch or 'N/A'}\n"
        f"- Updated: {updated_on or 'N/A'}\n"
    )
    if description:
        content += f"\nDescription:\n{description}"
    return Document(
        id=repo_document_id(workspace, repo_slug),
        sections=[TextSection(link=link, text=content)],
        source=DocumentSource.BITBUCKET,
        semantic_identifier=f"{workspace}/{repo_slug}",
        title=name,
        doc_updated_at=parse_bitbucket_datetime(updated_on),
        doc_created_at=parse_bitbucket_datetime(created_on),
        metadata={
            "object_type": "Repository",
            "workspace": workspace,
            "repository": repo_slug,
            "language": language,
            "project": project_key,
            "default_branch": branch,
            "link": link,
            "updated_on": updated_on or "",
        },
    )


def map_readme_to_document(
    workspace: str,
    repo_slug: str,
    path: str,
    text: str,
    branch: str,
    updated_on: str | None = None,
) -> Document:
    """Map README file bytes to a Document. Caller already size-checked the text."""
    link = f"https://bitbucket.org/{workspace}/{repo_slug}/src/{branch}/{path}"
    return Document(
        id=readme_document_id(workspace, repo_slug),
        sections=[TextSection(link=link, text=text)],
        source=DocumentSource.BITBUCKET,
        semantic_identifier=f"{workspace}/{repo_slug} README",
        title=f"{repo_slug} {path}",
        doc_updated_at=parse_bitbucket_datetime(updated_on),
        metadata={
            "object_type": "Readme",
            "workspace": workspace,
            "repository": repo_slug,
            "path": path,
            "branch": branch,
            "link": link,
        },
    )


def map_commit_to_document(
    commit: dict[str, Any], workspace: str, repo_slug: str
) -> Document:
    """Map a Bitbucket commit to a Document. Message only — no diff."""
    commit_hash = commit["hash"]
    message = commit.get("message") or ""
    first_line = message.split("\n", 1)[0].strip() or commit_hash[:12]
    author = commit.get("author") or {}
    author_user = author.get("user") if isinstance(author, dict) else {}
    author_name = (
        _get_user_name(author_user)
        if isinstance(author_user, dict) and author_user
        else (author.get("raw") if isinstance(author, dict) else None) or "unknown"
    )
    date = commit.get("date")
    link = (
        commit.get("links", {}).get("html", {}).get("href")
        or f"https://bitbucket.org/{workspace}/{repo_slug}/commits/{commit_hash}"
    )
    content = (
        "Commit Information:\n"
        f"- Hash: {commit_hash}\n"
        f"- Author: {author_name}\n"
        f"- Date: {date or 'N/A'}\n"
        f"\nMessage:\n{message}"
    )
    return Document(
        id=commit_document_id(workspace, repo_slug, commit_hash),
        sections=[TextSection(link=link, text=content)],
        source=DocumentSource.BITBUCKET,
        semantic_identifier=f"{repo_slug} {first_line}",
        title=first_line,
        doc_updated_at=parse_bitbucket_datetime(date),
        doc_created_at=parse_bitbucket_datetime(date),
        primary_owners=[BasicExpertInfo(display_name=author_name)],
        metadata={
            "object_type": "Commit",
            "workspace": workspace,
            "repository": repo_slug,
            "hash": commit_hash,
            "author": author_name,
            "date": date or "",
            "link": link,
        },
    )


def fetch_repository(
    client: httpx.Client, workspace: str, repo_slug: str
) -> dict[str, Any]:
    """GET one repository. Raises Bitbucket*Error on HTTP failure."""
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}"
    return bitbucket_get(client, url, params={"fields": REPO_DETAIL_FIELDS}).json()


def fetch_src_listing(
    client: httpx.Client, workspace: str, repo_slug: str, revision: str
) -> list[dict[str, Any]]:
    """List the repo root at revision. Empty list when the tree is missing."""
    url = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/src/{revision}/"
    try:
        data = bitbucket_get(client, url, params={"pagelen": 100}).json()
    except BitbucketNonRetriableError:
        return []
    values = data.get("values")
    return values if isinstance(values, list) else []


def fetch_src_file(
    client: httpx.Client, workspace: str, repo_slug: str, revision: str, path: str
) -> str | None:
    """Raw file text, or None when missing, binary, or over the size cap."""
    url = (
        f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/src/{revision}/{path}"
    )
    try:
        resp = bitbucket_get(client, url)
    except BitbucketNonRetriableError:
        return None
    content = resp.content
    if len(content) > MAX_README_BYTES or b"\x00" in content[:1024]:
        return None
    return content.decode("utf-8", errors="replace")


def fetch_commits_page(
    client: httpx.Client,
    workspace: str,
    repo_slug: str,
    revision: str,
    start_url: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of commits on revision (newest first). Returns (values, next_url)."""
    url = (
        start_url
        or f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo_slug}/commits/{revision}"
    )
    params = None if start_url else {"fields": COMMIT_LIST_RESPONSE_FIELDS, "pagelen": COMMITS_PAGE_LEN}
    data = bitbucket_get(client, url, params=params).json()
    values = data.get("values")
    items = values if isinstance(values, list) else []
    next_url = data.get("next")
    return items, next_url if isinstance(next_url, str) else None
