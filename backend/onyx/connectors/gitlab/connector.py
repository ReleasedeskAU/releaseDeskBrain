import fnmatch
import itertools
from collections import deque
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any, TypeVar

import gitlab
import pytz

from onyx.configs.app_configs import (
    GITLAB_CONNECTOR_INCLUDE_CODE_FILES,
    INDEX_BATCH_SIZE,
)
from onyx.configs.constants import DocumentSource
from onyx.connectors.cross_connector_utils.miscellaneous_utils import time_str_to_utc
from onyx.connectors.interfaces import (
    GenerateDocumentsOutput,
    LoadConnector,
    PollConnector,
    SecondsSinceUnixEpoch,
)
from gitlab.exceptions import GitlabGetError
from gitlab.v4.objects import Project

from onyx.connectors.gitlab.docs import (
    MAX_README_CHARS,
    README_CANDIDATES,
    map_commit_to_document,
    map_overview_to_document,
    map_readme_to_document,
)
from onyx.connectors.gitlab.fields import FIELD_SCHEMA
from onyx.connectors.models import (
    BasicExpertInfo,
    ConnectorMissingCredentialError,
    Document,
    HierarchyNode,
    TextSection,
)
from onyx.utils.datetime import datetime_to_utc
from onyx.utils.logger import setup_logger

T = TypeVar("T")


logger = setup_logger()

# List of directories/Files to exclude
exclude_patterns = [
    "logs",
    ".github/",
    ".gitlab/",
    ".pre-commit-config.yaml",
]


def _batch_gitlab_objects(git_objs: Iterable[T], batch_size: int) -> Iterator[list[T]]:
    it = iter(git_objs)
    while True:
        batch = list(itertools.islice(it, batch_size))
        if not batch:
            break
        yield batch


def get_author(author: Any) -> BasicExpertInfo:
    # GitLab masks the `name` field as "****" for blocked users and as an
    # anti-scraping measure on free-tier public projects. Fall back to
    # `username` so we surface a usable identifier instead.
    return BasicExpertInfo(display_name=_person_display_name(author))


def _person_display_name(person: Any) -> str | None:
    """Visible GitLab name, or None when missing. Never uses email."""
    if person is None:
        return None
    if isinstance(person, dict):
        name = person.get("name")
        username = person.get("username")
    else:
        name = getattr(person, "name", None)
        username = getattr(person, "username", None)
    if name and str(name) != "****":
        return str(name)
    if username:
        return str(username)
    return None


def _people_names(people: Any) -> list[str]:
    """Display names from a GitLab assignees/reviewers list."""
    if not people:
        return []
    names: list[str] = []
    for person in people:
        name = _person_display_name(person)
        if name:
            names.append(name)
    return names


def _label_names(labels: Any) -> list[str]:
    """GitLab REST labels are strings; some clients return objects with name."""
    if not labels:
        return []
    names: list[str] = []
    for label in labels:
        if isinstance(label, str):
            text = label.strip()
        elif isinstance(label, dict):
            text = str(label.get("name") or "").strip()
        else:
            text = str(getattr(label, "name", "") or "").strip()
        if text:
            names.append(text)
    return names


def _gitlab_datetime_to_utc(value: Any) -> datetime | None:
    """Normalize a GitLab timestamp to tz-aware UTC.

    python-gitlab exposes REST attributes as raw JSON, so these arrive as
    ISO-8601 strings; handle datetime values defensively too. Uses the shared
    parser rather than a fixed format so whole-second timestamps (no fractional
    part) don't raise.
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            return datetime_to_utc(value)
        return time_str_to_utc(str(value))
    except (ValueError, TypeError, OverflowError):
        return None


def _date_tag(value: Any) -> str | None:
    """ISO timestamp for Ask date tags, or YYYY-MM-DD when that is all GitLab sent."""
    if value is None or value == "":
        return None
    parsed = _gitlab_datetime_to_utc(value)
    if parsed is not None:
        return parsed.isoformat()
    text = str(value).strip()
    return text or None


def _compact_metadata(raw: dict[str, Any]) -> dict[str, str | list[str]]:
    """Drop empty tags so Ask reports missing fields as untagged, not invented."""
    out: dict[str, str | list[str]] = {}
    for key, value in raw.items():
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, list):
            items = [str(item) for item in value if item not in (None, "")]
            if items:
                out[key] = items
        else:
            out[key] = str(value)
    return out


def _gitlab_catalog_tags(
    *,
    state: Any,
    object_type: str,
    stored_type: str,
    project_path: str,
    iid: Any,
    author: Any,
    assignees: Any,
    labels: Any,
    created_at: Any,
    updated_at: Any,
    due_date: Any = None,
    merged: Any = None,
) -> dict[str, str | list[str]]:
    """Ask list fields from GitLab REST attributes. No priority — GitLab has none."""
    state_text = str(state).strip() if state else ""
    iid_text = f"#{iid}" if iid is not None and str(iid) != "" else None
    return _compact_metadata(
        {
            "state": state_text,
            # Same GitLab state on `status` so list_documents_matching can project it.
            "status": state_text,
            "type": stored_type,
            "object_type": object_type,
            "key": iid_text,
            "project": project_path,
            "repo": project_path,
            "reporter": _person_display_name(author),
            "assignee": _people_names(assignees),
            "labels": _label_names(labels),
            "created": _date_tag(created_at),
            "updated": _date_tag(updated_at),
            "duedate": _date_tag(due_date),
            "merged": merged,
        }
    )


def _convert_merge_request_to_document(mr: Any, project_path: str) -> Document:
    """Index one merge request. Tags opened/closed/merged from GitLab `state`."""
    owners = [get_author(mr.author)] if getattr(mr, "author", None) else []
    merged = getattr(mr, "merged_at", None) is not None or str(getattr(mr, "state", "")).lower() == "merged"
    doc = Document(
        id=mr.web_url,
        sections=[TextSection(link=mr.web_url, text=mr.description or "")],
        source=DocumentSource.GITLAB,
        semantic_identifier=mr.title,
        doc_updated_at=_gitlab_datetime_to_utc(mr.updated_at),
        doc_created_at=_gitlab_datetime_to_utc(mr.created_at),
        primary_owners=owners,
        metadata=_gitlab_catalog_tags(
            state=mr.state,
            object_type="MergeRequest",
            stored_type="MergeRequest",
            project_path=project_path,
            iid=getattr(mr, "iid", None),
            author=getattr(mr, "author", None),
            assignees=getattr(mr, "assignees", None),
            labels=getattr(mr, "labels", None),
            created_at=mr.created_at,
            updated_at=mr.updated_at,
            merged=str(merged).lower() if merged else None,
        ),
    )
    return doc


def _gitlab_issue_object_type(issue: Any) -> str:
    """Ask filters GitLab issues via object_type=Issue (same field as GitHub)."""
    raw = str(getattr(issue, "type", None) or "Issue").strip()
    if raw.upper() in {"ISSUE", "ISSUE_TYPE_ISSUE", ""}:
        return "Issue"
    return raw


def _convert_issue_to_document(issue: Any, project_path: str) -> Document:
    """Index one GitLab issue. Omits assignee/due/priority when GitLab has none."""
    stored_type = issue.type if getattr(issue, "type", None) else "Issue"
    owners = [get_author(issue.author)] if getattr(issue, "author", None) else []
    doc = Document(
        id=issue.web_url,
        sections=[TextSection(link=issue.web_url, text=issue.description or "")],
        source=DocumentSource.GITLAB,
        semantic_identifier=issue.title,
        doc_updated_at=_gitlab_datetime_to_utc(issue.updated_at),
        doc_created_at=_gitlab_datetime_to_utc(issue.created_at),
        primary_owners=owners,
        metadata=_gitlab_catalog_tags(
            state=issue.state,
            object_type=_gitlab_issue_object_type(issue),
            stored_type=stored_type,
            project_path=project_path,
            iid=getattr(issue, "iid", None),
            author=getattr(issue, "author", None),
            assignees=getattr(issue, "assignees", None),
            labels=getattr(issue, "labels", None),
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            due_date=getattr(issue, "due_date", None),
        ),
    )
    return doc


def _convert_code_to_document(
    project: Project, file: Any, url: str, projectName: str, projectOwner: str
) -> Document:
    # Dynamically get the default branch from the project object
    default_branch = project.default_branch

    # Fetch the file content using the correct branch
    file_content_obj = project.files.get(
        file_path=file["path"],
        ref=default_branch,  # Use the default branch
    )
    try:
        file_content = file_content_obj.decode().decode("utf-8")
    except UnicodeDecodeError:
        file_content = file_content_obj.decode().decode("latin-1")

    # Construct the file URL dynamically using the default branch
    file_url = (
        f"{url}/{projectOwner}/{projectName}/-/blob/{default_branch}/{file['path']}"
    )

    # Create and return a Document object
    doc = Document(
        id=file["id"],
        sections=[TextSection(link=file_url, text=file_content)],
        source=DocumentSource.GITLAB,
        semantic_identifier=file["name"],
        doc_updated_at=datetime.now().replace(tzinfo=timezone.utc),
        primary_owners=[],  # Add owners if needed
        metadata={"type": "CodeFile"},
    )
    return doc


def _should_exclude(path: str) -> bool:
    """Check if a path matches any of the exclude patterns."""
    return any(fnmatch.fnmatch(path, pattern) for pattern in exclude_patterns)


class GitlabConnector(LoadConnector, PollConnector):
    field_schema = FIELD_SCHEMA

    def __init__(
        self,
        project_owner: str,
        project_name: str,
        projects: str | None = None,
        batch_size: int = INDEX_BATCH_SIZE,
        state_filter: str = "all",
        include_mrs: bool = True,
        include_issues: bool = True,
        include_code_files: bool = GITLAB_CONNECTOR_INCLUDE_CODE_FILES,
        include_overview: bool = True,
        include_commits: bool = True,
    ) -> None:
        self.project_owner = project_owner
        self.project_name = project_name
        self._projects = [s.strip() for s in (projects or "").split(",") if s.strip()]
        self.batch_size = batch_size
        self.state_filter = state_filter
        self.include_mrs = include_mrs
        self.include_issues = include_issues
        self.include_code_files = include_code_files
        self.include_overview = include_overview
        self.include_commits = include_commits
        self.gitlab_client: gitlab.Gitlab | None = None

    def _project_paths(self) -> list[str]:
        """Paths to index: explicit list, else the single constructor project."""
        if self._projects:
            return self._projects
        return [f"{self.project_owner}/{self.project_name}"]

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        self.gitlab_client = gitlab.Gitlab(
            credentials["gitlab_url"], private_token=credentials["gitlab_access_token"]
        )
        return None

    def _fetch_readme(self, project: Project, path: str, web_url: str) -> Document | None:
        """First README candidate on the default branch, or None when missing/binary."""
        default_branch = project.default_branch
        if not default_branch:
            return None
        for candidate in README_CANDIDATES:
            try:
                file_obj = project.files.get(file_path=candidate, ref=default_branch)
            except GitlabGetError:
                continue
            try:
                text = file_obj.decode().decode("utf-8")
            except UnicodeDecodeError:
                continue
            if not text.strip() or len(text) > MAX_README_CHARS:
                continue
            return map_readme_to_document(path, candidate, text, default_branch, web_url)
        return None

    def _fetch_from_gitlab(
        self, start: datetime | None = None, end: datetime | None = None
    ) -> GenerateDocumentsOutput:
        if self.gitlab_client is None:
            raise ConnectorMissingCredentialError("Gitlab")
        for path in self._project_paths():
            project: Project = self.gitlab_client.projects.get(path)
            yield from self._fetch_project(project, path, start, end)

    def _fetch_project(
        self,
        project: Project,
        path: str,
        start: datetime | None,
        end: datetime | None,
    ) -> GenerateDocumentsOutput:
        web_url = getattr(project, "web_url", None) or (
            f"{self.gitlab_client.url}/{path}" if self.gitlab_client is not None else path
        )
        default_branch = project.default_branch or ""

        if self.include_overview:
            overview_batch: list[Document | HierarchyNode] = [
                map_overview_to_document(
                    path,
                    getattr(project, "name", "") or path,
                    getattr(project, "description", None) or "",
                    default_branch,
                    web_url,
                    str(getattr(project, "visibility", "") or ""),
                    getattr(project, "last_activity_at", None),
                )
            ]
            readme = self._fetch_readme(project, path, web_url)
            if readme:
                overview_batch.append(readme)
            yield overview_batch

        if self.include_commits and default_branch:
            commit_kwargs: dict[str, Any] = {
                "ref_name": default_branch,
                "iterator": True,
            }
            if start is not None:
                commit_kwargs["since"] = start.isoformat()
            if end is not None:
                commit_kwargs["until"] = end.isoformat()
            commits = project.commits.list(**commit_kwargs)
            for commit_batch in _batch_gitlab_objects(commits, self.batch_size):
                yield [map_commit_to_document(commit, path, web_url) for commit in commit_batch]

        # Fetch code files
        if self.include_code_files:
            # Fetching using BFS as project.report_tree with recursion causing slow load
            queue = deque([""])  # Start with the root directory
            while queue:
                current_path = queue.popleft()
                files = project.repository_tree(path=current_path, all=True)
                for file_batch in _batch_gitlab_objects(files, self.batch_size):
                    code_doc_batch: list[Document | HierarchyNode] = []
                    for file in file_batch:
                        if _should_exclude(file["path"]):
                            continue

                        if file["type"] == "blob":
                            owner, _, name = path.rpartition("/")
                            code_doc_batch.append(
                                _convert_code_to_document(
                                    project,
                                    file,
                                    self.gitlab_client.url if self.gitlab_client is not None else "",
                                    name or self.project_name,
                                    owner or self.project_owner,
                                )
                            )
                        elif file["type"] == "tree":
                            queue.append(file["path"])

                    if code_doc_batch:
                        yield code_doc_batch

        if self.include_mrs:
            merge_requests = project.mergerequests.list(
                state=self.state_filter,
                order_by="updated_at",
                sort="desc",
                iterator=True,
            )

            start_utc = start.replace(tzinfo=pytz.UTC) if start is not None else None
            end_utc = end.replace(tzinfo=pytz.UTC) if end is not None else None
            stop_mrs = False
            for mr_batch in _batch_gitlab_objects(merge_requests, self.batch_size):
                mr_doc_batch: list[Document | HierarchyNode] = []
                for mr in mr_batch:
                    updated = _gitlab_datetime_to_utc(mr.updated_at)
                    if updated is None:
                        continue
                    if start_utc is not None and updated < start_utc:
                        stop_mrs = True
                        break
                    if end_utc is not None and updated > end_utc:
                        continue
                    mr_doc_batch.append(_convert_merge_request_to_document(mr, path))
                if mr_doc_batch:
                    yield mr_doc_batch
                if stop_mrs:
                    break

        if self.include_issues:
            issues = project.issues.list(
                state=self.state_filter,
                order_by="updated_at",
                sort="desc",
                iterator=True,
            )
            start_utc = start.replace(tzinfo=pytz.UTC) if start is not None else None
            end_utc = end.replace(tzinfo=pytz.UTC) if end is not None else None
            stop_issues = False
            for issue_batch in _batch_gitlab_objects(issues, self.batch_size):
                issue_doc_batch: list[Document | HierarchyNode] = []
                for issue in issue_batch:
                    updated = _gitlab_datetime_to_utc(issue.updated_at)
                    if updated is None:
                        continue
                    if start_utc is not None and updated < start_utc:
                        stop_issues = True
                        break
                    if end_utc is not None and updated > end_utc:
                        continue
                    issue_doc_batch.append(_convert_issue_to_document(issue, path))
                if issue_doc_batch:
                    yield issue_doc_batch
                if stop_issues:
                    break

    def load_from_state(self) -> GenerateDocumentsOutput:
        return self._fetch_from_gitlab()

    def poll_source(
        self, start: SecondsSinceUnixEpoch, end: SecondsSinceUnixEpoch
    ) -> GenerateDocumentsOutput:
        start_datetime = datetime.fromtimestamp(start, tz=timezone.utc)
        end_datetime = datetime.fromtimestamp(end, tz=timezone.utc)
        return self._fetch_from_gitlab(start_datetime, end_datetime)


if __name__ == "__main__":
    import os

    connector = GitlabConnector(
        # gitlab_url="https://gitlab.com/api/v4",
        project_owner=os.environ["PROJECT_OWNER"],
        project_name=os.environ["PROJECT_NAME"],
        batch_size=10,
        state_filter="all",
        include_mrs=True,
        include_issues=True,
        include_code_files=GITLAB_CONNECTOR_INCLUDE_CODE_FILES,
    )

    connector.load_credentials(
        {
            "gitlab_access_token": os.environ["GITLAB_ACCESS_TOKEN"],
            "gitlab_url": os.environ["GITLAB_URL"],
        }
    )
    document_batches = connector.load_from_state()
    print(next(document_batches))
