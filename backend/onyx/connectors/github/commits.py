"""GitHub commit documents for Ask: message, file names, and line stats — no patches.

The same SHA can sit on many branches. Callers walk every branch and skip SHAs
already emitted so each commit is one document.
"""

from __future__ import annotations

from datetime import datetime, timezone

from github.Commit import Commit

from onyx.access.models import ExternalAccess
from onyx.configs.constants import DocumentSource
from onyx.connectors.models import BasicExpertInfo, Document, TextSection

MAX_FILE_NAMES = 200


def commit_document_id(full_name: str, sha: str) -> str:
    """Stable Ask id for one GitHub commit (unique by SHA, not by branch)."""
    return f"{DocumentSource.GITHUB.value}:{full_name}:commit:{sha}"


def commit_datetime(commit: Commit) -> datetime | None:
    """Author date from the git object. Naive GitHub timestamps are treated as UTC."""
    git_commit = getattr(commit, "commit", None)
    author = getattr(git_commit, "author", None) if git_commit is not None else None
    value = getattr(author, "date", None) if author is not None else None
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def commit_file_names(commit: Commit) -> tuple[list[str], int]:
    """File names and touched count. Never reads ``patch`` (the diff body)."""
    files = getattr(commit, "files", None) or []
    names: list[str] = []
    total = 0
    for entry in files:
        total += 1
        name = getattr(entry, "filename", None)
        if isinstance(name, str) and name.strip() and len(names) < MAX_FILE_NAMES:
            names.append(name.strip())
    return names, total


def map_commit_to_document(
    commit: Commit,
    full_name: str,
    html_url: str,
    seen_on_branch: str,
    repo_external_access: ExternalAccess | None,
) -> Document:
    """Map one GitHub commit to a Document. Message + file names + line stats, no diff.

    Args:
        commit: Detail payload from GET /commits/{sha} (includes files and stats).
        full_name: owner/repo.
        html_url: Repo web URL, used if the commit has no html_url.
        seen_on_branch: First branch that listed this SHA (not every branch).
        repo_external_access: EE permission payload, or None.

    Returns:
        Document with object_type=Commit. ``patch`` is never copied into text.
    """
    sha = str(getattr(commit, "sha", "") or "")
    git_commit = getattr(commit, "commit", None)
    message = ""
    if git_commit is not None:
        raw_message = getattr(git_commit, "message", None)
        if isinstance(raw_message, str):
            message = raw_message
    first_line = message.split("\n", 1)[0].strip() or sha[:12]
    names, files_touched = commit_file_names(commit)
    stats = getattr(commit, "stats", None)
    additions = getattr(stats, "additions", None) if stats is not None else None
    deletions = getattr(stats, "deletions", None) if stats is not None else None
    link = getattr(commit, "html_url", None) or f"{html_url}/commit/{sha}"
    author_name = _commit_author_name(commit)
    truncated = files_touched > len(names)
    lines = [
        "Commit Information:",
        f"- Hash: {sha}",
        f"- Seen on branch: {seen_on_branch}",
        f"- Author: {author_name}",
        f"- Files touched: {files_touched}",
        f"- Lines added: {additions if additions is not None else 'unknown'}",
        f"- Lines removed: {deletions if deletions is not None else 'unknown'}",
        f"- File names: {', '.join(names) if names else 'none'}",
    ]
    if truncated:
        lines.append(f"- File names truncated to {MAX_FILE_NAMES}")
    lines.extend(["", "Message:", message or "(empty)"])
    text = "\n".join(lines)
    metadata: dict[str, str | list[str]] = {
        "object_type": "Commit",
        "repo": full_name,
        "sha": sha,
        "branch": seen_on_branch,
        "files_touched": str(files_touched),
        "link": str(link),
    }
    if additions is not None:
        metadata["additions"] = str(additions)
    if deletions is not None:
        metadata["deletions"] = str(deletions)
    if names:
        metadata["files"] = names
    owner_name, _, repo_name = full_name.partition("/")
    updated = commit_datetime(commit)
    owners = [BasicExpertInfo(display_name=author_name)] if author_name != "unknown" else None
    return Document(
        id=commit_document_id(full_name, sha),
        sections=[TextSection(link=str(link), text=text)],
        source=DocumentSource.GITHUB,
        external_access=repo_external_access,
        semantic_identifier=f"{repo_name} {first_line}" if repo_name else first_line,
        title=first_line,
        doc_updated_at=updated,
        doc_created_at=updated,
        primary_owners=owners,
        doc_metadata={
            "repo": full_name,
            "hierarchy": {
                "source_path": [owner_name, repo_name, "commits", sha],
                "owner": owner_name,
                "repo": repo_name,
                "object_type": "commit",
            },
        },
        metadata=metadata,
    )


def slim_commit_document(
    full_name: str, sha: str, repo_external_access: ExternalAccess | None
) -> Document:
    """Stable id for prune/perm-sync without fetching file lists."""
    return Document(
        id=commit_document_id(full_name, sha),
        sections=[],
        external_access=repo_external_access,
        source=DocumentSource.GITHUB,
        semantic_identifier="",
        metadata={},
    )


def _commit_author_name(commit: Commit) -> str:
    user = getattr(commit, "author", None)
    login = getattr(user, "login", None) if user is not None else None
    if isinstance(login, str) and login.strip():
        return login.strip()
    git_commit = getattr(commit, "commit", None)
    author = getattr(git_commit, "author", None) if git_commit is not None else None
    name = getattr(author, "name", None) if author is not None else None
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "unknown"
