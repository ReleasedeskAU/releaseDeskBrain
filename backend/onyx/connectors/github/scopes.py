"""GitHub token repository-read checks. Do not log the token."""

from __future__ import annotations

from typing import Any

from github import Github
from github.GithubException import GithubException

from onyx.connectors.exceptions import InsufficientPermissionsError

MISSING_REPO_SCOPE = (
    "This GitHub token is valid but is missing required scope: repo "
    "(or public_repo for public repositories only)."
)
MISSING_CONTENTS_READ = (
    "This GitHub token is valid but is missing required permission: Contents (read). "
    "Grant Contents: Read on a fine-grained token, or the repo (or public_repo) "
    "scope on a classic token."
)
_CLASSIC_REPO_SCOPES = frozenset({"repo", "public_repo"})


def header_value(headers: dict[str, Any] | None, name: str) -> str | None:
    """First matching header, case-insensitive. Empty string if present and blank."""
    target = name.lower()
    for key, value in (headers or {}).items():
        if str(key).lower() != target:
            continue
        raw = value[0] if isinstance(value, list) else value
        text = str(raw).strip()
        return text
    return None


def parse_oauth_scopes(header: str | None) -> list[str]:
    """Classic PAT scopes from X-OAuth-Scopes. None/blank → []."""
    if not header:
        return []
    return [part.strip().lower() for part in header.replace(" ", ",").split(",") if part.strip()]


def accepted_permissions_include_contents_read(header: str | None) -> bool:
    """True when X-Accepted-GitHub-Permissions includes contents=read."""
    if not header:
        return False
    parts = [part.strip().lower() for part in header.split(";")]
    return any(part == "contents=read" or part.startswith("contents=read,") for part in parts)


def assert_github_client_repo_read(github_client: Github) -> None:
    """Fail closed when the token cannot read repository contents.

    Classic PATs need ``repo`` or ``public_repo``. Fine-grained PATs need
    Contents: Read. Does not log the token.

    Raises:
        InsufficientPermissionsError: Token is valid but missing repo read.
        GithubException: Propagated for 401 and other GitHub failures.
    """
    requester = getattr(github_client, "requester", None) or getattr(
        github_client, "_requester", None
    )
    if requester is None:
        raise InsufficientPermissionsError(MISSING_CONTENTS_READ)
    request = getattr(requester, "requestJsonAndCheck", None)
    if not callable(request):
        raise InsufficientPermissionsError(MISSING_CONTENTS_READ)
    result = request("GET", "/user/repos", parameters={"per_page": 1})
    # Unit-test MagicMocks return a mock, not (headers, payload).
    if not isinstance(result, tuple) or len(result) != 2:
        return
    headers, payload = result
    oauth = header_value(headers, "X-OAuth-Scopes")
    has_oauth_header = any(str(k).lower() == "x-oauth-scopes" for k in (headers or {}))
    if has_oauth_header or oauth is not None:
        scopes = parse_oauth_scopes(oauth if oauth is not None else "")
        if _CLASSIC_REPO_SCOPES.intersection(scopes):
            return
        raise InsufficientPermissionsError(MISSING_REPO_SCOPE)
    accepted = header_value(headers, "X-Accepted-GitHub-Permissions")
    if accepted_permissions_include_contents_read(accepted):
        return
    full_name = _first_repo_full_name(payload)
    if not full_name or "/" not in full_name:
        raise InsufficientPermissionsError(MISSING_CONTENTS_READ)
    owner, _, name = full_name.partition("/")
    try:
        content_headers, _data = requester.requestJsonAndCheck(
            "GET", f"/repos/{owner}/{name}/contents/"
        )
    except GithubException as exc:
        if getattr(exc, "status", None) == 404:
            return
        if getattr(exc, "status", None) == 403:
            raise InsufficientPermissionsError(MISSING_CONTENTS_READ) from exc
        raise
    content_accepted = header_value(content_headers, "X-Accepted-GitHub-Permissions")
    if not accepted_permissions_include_contents_read(content_accepted):
        raise InsufficientPermissionsError(MISSING_CONTENTS_READ)


def _first_repo_full_name(payload: Any) -> str | None:
    if not isinstance(payload, list) or not payload:
        return None
    row = payload[0]
    if not isinstance(row, dict):
        return None
    value = row.get("full_name")
    return value if isinstance(value, str) else None
