"""GitLab overview, README, and commit documents for Ask.

Commits are message-only on the default branch. Diffs and source files are not stored.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from onyx.configs.constants import DocumentSource
from onyx.connectors.cross_connector_utils.miscellaneous_utils import time_str_to_utc
from onyx.connectors.models import BasicExpertInfo, Document, TextSection
from onyx.utils.datetime import datetime_to_utc

README_CANDIDATES = ("README.md", "README.rst", "README.txt", "readme.md", "README")
MAX_README_CHARS = 1_000_000


def _utc(value: Any) -> datetime | None:
    """Normalize a GitLab timestamp to tz-aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return datetime_to_utc(value)
    return time_str_to_utc(value)


def overview_document_id(path: str) -> str:
    """Stable Ask id for one GitLab project overview."""
    return f"{DocumentSource.GITLAB.value}:{path}:overview"


def readme_document_id(path: str) -> str:
    """Stable Ask id for one GitLab README."""
    return f"{DocumentSource.GITLAB.value}:{path}:readme"


def commit_document_id(path: str, sha: str) -> str:
    """Stable Ask id for one GitLab commit (SHA on the default branch)."""
    return f"{DocumentSource.GITLAB.value}:{path}:commit:{sha}"


def map_overview_to_document(
    path: str,
    name: str,
    description: str,
    default_branch: str,
    web_url: str,
    visibility: str,
    last_activity_at: Any = None,
) -> Document:
    """Map GitLab project metadata to a Document. Description only — no file tree."""
    content = (
        "Project Information:\n"
        f"- Name: {name or path}\n"
        f"- Path: {path}\n"
        f"- Visibility: {visibility or 'N/A'}\n"
        f"- Default branch: {default_branch or 'N/A'}\n"
    )
    if description:
        content += f"\nDescription:\n{description}"
    return Document(
        id=overview_document_id(path),
        sections=[TextSection(link=web_url, text=content)],
        source=DocumentSource.GITLAB,
        semantic_identifier=path,
        doc_updated_at=_utc(last_activity_at),
        metadata={
            "type": "ProjectOverview",
            "object_type": "Repository",
            "project": path,
            "default_branch": default_branch or "",
            "visibility": visibility or "",
            "link": web_url,
        },
    )


def map_readme_to_document(
    path: str, file_path: str, text: str, default_branch: str, web_url: str
) -> Document:
    """Map README text to a Document. Caller already size-checked the text."""
    link = f"{web_url}/-/blob/{default_branch}/{file_path}"
    return Document(
        id=readme_document_id(path),
        sections=[TextSection(link=link, text=text)],
        source=DocumentSource.GITLAB,
        semantic_identifier=f"{path} README",
        metadata={
            "type": "Readme",
            "object_type": "Readme",
            "project": path,
            "path": file_path,
            "branch": default_branch,
            "link": link,
        },
    )


def map_commit_to_document(commit: Any, path: str, web_url: str) -> Document:
    """Map one GitLab commit to a Document. Message only — no diff."""
    sha = str(getattr(commit, "id", "") or "")
    message = getattr(commit, "message", None) or ""
    first_line = (getattr(commit, "title", None) or message.split("\n", 1)[0]).strip() or sha[:12]
    author_name = getattr(commit, "author_name", None) or "unknown"
    authored = getattr(commit, "authored_date", None) or getattr(commit, "created_at", None)
    link = getattr(commit, "web_url", None) or f"{web_url}/-/commit/{sha}"
    content = (
        "Commit Information:\n"
        f"- Hash: {sha}\n"
        f"- Author: {author_name}\n"
        f"- Date: {authored or 'N/A'}\n"
        f"\nMessage:\n{message or '(empty)'}"
    )
    return Document(
        id=commit_document_id(path, sha),
        sections=[TextSection(link=str(link), text=content)],
        source=DocumentSource.GITLAB,
        semantic_identifier=f"{path.split('/')[-1]} {first_line}",
        doc_updated_at=_utc(authored),
        doc_created_at=_utc(authored),
        primary_owners=[BasicExpertInfo(display_name=author_name)] if author_name != "unknown" else None,
        metadata={
            "type": "Commit",
            "object_type": "Commit",
            "project": path,
            "sha": sha,
            "author": author_name,
            "link": str(link),
        },
    )
