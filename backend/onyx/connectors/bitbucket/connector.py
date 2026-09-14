from __future__ import annotations

import copy
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from typing_extensions import override

from onyx.configs.app_configs import INDEX_BATCH_SIZE, REQUEST_TIMEOUT_SECONDS
from onyx.configs.constants import DocumentSource
from onyx.connectors.bitbucket.utils import (
    PR_LIST_RESPONSE_FIELDS,
    SLIM_PR_LIST_RESPONSE_FIELDS,
    build_auth_client,
    commit_document_id,
    commit_is_older_than_start,
    default_branch_name,
    fetch_commits_page,
    fetch_repository,
    fetch_src_file,
    fetch_src_listing,
    list_repositories,
    map_commit_to_document,
    map_pr_to_document,
    map_readme_to_document,
    map_repo_to_document,
    paginate,
    parse_bitbucket_datetime,
    pick_readme_path,
    readme_document_id,
    repo_document_id,
    timestamp_in_window,
)
from onyx.connectors.exceptions import (
    CredentialExpiredError,
    InsufficientPermissionsError,
    UnexpectedValidationError,
)
from onyx.connectors.interfaces import (
    CheckpointedConnector,
    CheckpointOutput,
    SecondsSinceUnixEpoch,
    SlimConnectorWithPermSync,
)
from onyx.connectors.models import (
    ConnectorCheckpoint,
    ConnectorFailure,
    ConnectorMissingCredentialError,
    DocumentFailure,
    HierarchyNode,
    SlimDocument,
)
from onyx.indexing.indexing_heartbeat import IndexingHeartbeatInterface
from onyx.utils.logger import setup_logger

if TYPE_CHECKING:
    import httpx

logger = setup_logger()

STAGE_PRS = "prs"
STAGE_REPO = "repo"
STAGE_README = "readme"
STAGE_COMMITS = "commits"


class BitbucketConnectorCheckpoint(ConnectorCheckpoint):
    """Resumable Bitbucket indexing across repos and object types.

    stage defaults to pull requests so in-flight PR checkpoints keep working.
    """

    repos_queue: list[str] = []
    current_repo_index: int = 0
    next_url: str | None = None
    stage: str = STAGE_PRS
    default_branch: str | None = None


class BitbucketConnector(
    CheckpointedConnector[BitbucketConnectorCheckpoint],
    SlimConnectorWithPermSync,
):
    """Bitbucket Cloud: PRs, repository overview, README, and commit messages."""

    def __init__(
        self,
        workspace: str,
        repositories: str | None = None,
        projects: str | None = None,
        batch_size: int = INDEX_BATCH_SIZE,
        include_prs: bool = True,
        include_repo: bool = True,
        include_readme: bool = True,
        include_commits: bool = True,
    ) -> None:
        self.workspace = workspace
        self._repositories = (
            [s.strip() for s in repositories.split(",") if s.strip()]
            if repositories
            else None
        )
        self._projects: list[str] | None = (
            [s.strip() for s in projects.split(",") if s.strip()] if projects else None
        )
        self.batch_size = batch_size
        self.include_prs = include_prs
        self.include_repo = include_repo
        self.include_readme = include_readme
        self.include_commits = include_commits
        self.email: str | None = None
        self.api_token: str | None = None

    def _enabled_stages(self) -> list[str]:
        """Object types this connector will index, in checkpoint order."""
        stages: list[str] = []
        if self.include_prs:
            stages.append(STAGE_PRS)
        if self.include_repo:
            stages.append(STAGE_REPO)
        if self.include_readme:
            stages.append(STAGE_README)
        if self.include_commits:
            stages.append(STAGE_COMMITS)
        return stages

    def _first_stage(self) -> str:
        stages = self._enabled_stages()
        return stages[0] if stages else STAGE_PRS

    def _advance_stage(self, checkpoint: BitbucketConnectorCheckpoint) -> None:
        """Move to the next type, or the next repository when the current repo is done."""
        stages = self._enabled_stages()
        try:
            idx = stages.index(checkpoint.stage)
        except ValueError:
            idx = -1
        if idx + 1 < len(stages):
            checkpoint.stage = stages[idx + 1]
            checkpoint.next_url = None
            return
        checkpoint.current_repo_index += 1
        checkpoint.next_url = None
        checkpoint.default_branch = None
        checkpoint.stage = self._first_stage()
        checkpoint.has_more = checkpoint.current_repo_index < len(checkpoint.repos_queue)

    def load_credentials(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """Load API token-based credentials.

        Expects a dict with keys: `bitbucket_email`, `bitbucket_api_token`.
        """
        self.email = credentials.get("bitbucket_email")
        self.api_token = credentials.get("bitbucket_api_token")
        if not self.email or not self.api_token:
            raise ConnectorMissingCredentialError("Bitbucket")
        return None

    def _client(self) -> httpx.Client:
        """Build an authenticated HTTP client or raise if credentials missing."""
        if not self.email or not self.api_token:
            raise ConnectorMissingCredentialError("Bitbucket")
        return build_auth_client(self.email, self.api_token)

    def _iter_pull_requests_for_repo(
        self,
        client: httpx.Client,
        repo_slug: str,
        params: dict[str, Any] | None = None,
        start_url: str | None = None,
        on_page: Callable[[str | None], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        base = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}/{repo_slug}/pullrequests"
        yield from paginate(
            client,
            base,
            params,
            start_url=start_url,
            on_page=on_page,
        )

    def _build_params(
        self,
        fields: str = PR_LIST_RESPONSE_FIELDS,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
    ) -> dict[str, Any]:
        """Build Bitbucket fetch params.

        Always include OPEN, MERGED, and DECLINED PRs. If both ``start`` and
        ``end`` are provided, apply a single updated_on time window.
        """

        def _iso(ts: SecondsSinceUnixEpoch) -> str:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

        def _tc_epoch(
            lower_epoch: SecondsSinceUnixEpoch | None,
            upper_epoch: SecondsSinceUnixEpoch | None,
        ) -> str | None:
            if lower_epoch is not None and upper_epoch is not None:
                lower_iso = _iso(lower_epoch)
                upper_iso = _iso(upper_epoch)
                return f'(updated_on >= "{lower_iso}" AND updated_on <= "{upper_iso}")'
            return None

        params: dict[str, Any] = {"fields": fields, "pagelen": 50}
        time_clause = _tc_epoch(start, end)
        q = '(state = "OPEN" OR state = "MERGED" OR state = "DECLINED")'
        if time_clause:
            q = f"{q} AND {time_clause}"
        params["q"] = q
        return params

    def _iter_target_repositories(self, client: httpx.Client) -> Iterator[str]:
        """Yield repository slugs based on configuration.

        Priority:
        - repositories list
        - projects list (list repos by project key)
        - workspace (all repos)
        """
        if self._repositories:
            for slug in self._repositories:
                yield slug
            return
        if self._projects:
            for project_key in self._projects:
                for repo in list_repositories(client, self.workspace, project_key):
                    slug_val = repo.get("slug")
                    if isinstance(slug_val, str) and slug_val:
                        yield slug_val
            return
        for repo in list_repositories(client, self.workspace, None):
            slug_val = repo.get("slug")
            if isinstance(slug_val, str) and slug_val:
                yield slug_val

    def _yield_prs(
        self,
        client: httpx.Client,
        repo_slug: str,
        start: SecondsSinceUnixEpoch,
        end: SecondsSinceUnixEpoch,
        checkpoint: BitbucketConnectorCheckpoint,
    ) -> Iterator[Any]:
        """Index every PR page for this repo, then advance stage."""
        params = self._build_params(fields=PR_LIST_RESPONSE_FIELDS, start=start, end=end)

        def _on_page(next_url: str | None) -> None:
            checkpoint.next_url = next_url

        for pr in self._iter_pull_requests_for_repo(
            client,
            repo_slug,
            params=params,
            start_url=checkpoint.next_url,
            on_page=_on_page,
        ):
            try:
                yield map_pr_to_document(pr, self.workspace, repo_slug)
            except Exception as e:
                pr_id = pr.get("id")
                yield ConnectorFailure(
                    failed_document=DocumentFailure(
                        document_id=(
                            f"{DocumentSource.BITBUCKET.value}:{self.workspace}:{repo_slug}:pr:{pr_id}"
                            if pr_id is not None
                            else f"{DocumentSource.BITBUCKET.value}:{self.workspace}:{repo_slug}:pr:unknown"
                        ),
                        document_link=(
                            f"https://bitbucket.org/{self.workspace}/{repo_slug}/pull-requests/{pr_id}"
                            if pr_id is not None
                            else None
                        ),
                    ),
                    failure_message=f"Failed to process Bitbucket PR: {e}",
                    exception=e,
                )
        self._advance_stage(checkpoint)

    def _ensure_default_branch(
        self, client: httpx.Client, repo_slug: str, checkpoint: BitbucketConnectorCheckpoint
    ) -> dict[str, Any] | None:
        """Load repo JSON and cache the default branch. None if the repo call fails."""
        try:
            repo = fetch_repository(client, self.workspace, repo_slug)
        except Exception:
            logger.warning("Bitbucket repo lookup failed for %s/%s", self.workspace, repo_slug)
            return None
        checkpoint.default_branch = default_branch_name(repo)
        return repo

    def _yield_repo(
        self, client: httpx.Client, repo_slug: str, checkpoint: BitbucketConnectorCheckpoint
    ) -> Iterator[Any]:
        """Index repository overview, then advance stage."""
        repo = self._ensure_default_branch(client, repo_slug, checkpoint)
        if repo is not None:
            try:
                yield map_repo_to_document(repo, self.workspace, repo_slug)
            except Exception as e:
                yield ConnectorFailure(
                    failed_document=DocumentFailure(
                        document_id=repo_document_id(self.workspace, repo_slug),
                        document_link=f"https://bitbucket.org/{self.workspace}/{repo_slug}",
                    ),
                    failure_message=f"Failed to process Bitbucket repository: {e}",
                    exception=e,
                )
        self._advance_stage(checkpoint)

    def _yield_readme(
        self, client: httpx.Client, repo_slug: str, checkpoint: BitbucketConnectorCheckpoint
    ) -> Iterator[Any]:
        """Index default-branch README when present, then advance stage."""
        if not checkpoint.default_branch:
            self._ensure_default_branch(client, repo_slug, checkpoint)
        branch = checkpoint.default_branch
        if branch:
            entries = fetch_src_listing(client, self.workspace, repo_slug, branch)
            path = pick_readme_path(entries)
            text = (
                fetch_src_file(client, self.workspace, repo_slug, branch, path)
                if path
                else None
            )
            if path and text:
                try:
                    yield map_readme_to_document(
                        self.workspace, repo_slug, path, text, branch
                    )
                except Exception as e:
                    yield ConnectorFailure(
                        failed_document=DocumentFailure(
                            document_id=readme_document_id(self.workspace, repo_slug),
                            document_link=f"https://bitbucket.org/{self.workspace}/{repo_slug}",
                        ),
                        failure_message=f"Failed to process Bitbucket README: {e}",
                        exception=e,
                    )
        self._advance_stage(checkpoint)

    def _yield_commits(
        self,
        client: httpx.Client,
        repo_slug: str,
        start: SecondsSinceUnixEpoch,
        end: SecondsSinceUnixEpoch,
        checkpoint: BitbucketConnectorCheckpoint,
    ) -> Iterator[Any]:
        """Index one commit page on the default branch, then pause or advance."""
        if not checkpoint.default_branch:
            self._ensure_default_branch(client, repo_slug, checkpoint)
        branch = checkpoint.default_branch
        if not branch:
            self._advance_stage(checkpoint)
            return
        try:
            items, next_url = fetch_commits_page(
                client, self.workspace, repo_slug, branch, checkpoint.next_url
            )
        except Exception as e:
            yield ConnectorFailure(
                failed_document=DocumentFailure(
                    document_id=f"{DocumentSource.BITBUCKET.value}:{self.workspace}:{repo_slug}:commits",
                    document_link=f"https://bitbucket.org/{self.workspace}/{repo_slug}/commits",
                ),
                failure_message=f"Failed to list Bitbucket commits: {e}",
                exception=e,
            )
            self._advance_stage(checkpoint)
            return
        reached_floor = False
        for commit in items:
            if commit_is_older_than_start(commit.get("date"), start):
                reached_floor = True
                break
            if not timestamp_in_window(commit.get("date"), start, end):
                continue
            try:
                yield map_commit_to_document(commit, self.workspace, repo_slug)
            except Exception as e:
                commit_hash = commit.get("hash")
                yield ConnectorFailure(
                    failed_document=DocumentFailure(
                        document_id=(
                            commit_document_id(self.workspace, repo_slug, str(commit_hash))
                            if commit_hash
                            else f"{DocumentSource.BITBUCKET.value}:{self.workspace}:{repo_slug}:commit:unknown"
                        ),
                        document_link=None,
                    ),
                    failure_message=f"Failed to process Bitbucket commit: {e}",
                    exception=e,
                )
        if reached_floor or not next_url:
            self._advance_stage(checkpoint)
            return
        checkpoint.next_url = next_url

    @override
    def load_from_checkpoint(
        self,
        start: SecondsSinceUnixEpoch,
        end: SecondsSinceUnixEpoch,
        checkpoint: BitbucketConnectorCheckpoint,
    ) -> CheckpointOutput[BitbucketConnectorCheckpoint]:
        """One stage (or one commit page) for the current repository, then checkpoint."""
        new_checkpoint = copy.deepcopy(checkpoint)

        with self._client() as client:
            if not new_checkpoint.repos_queue:
                repos_list = list(self._iter_target_repositories(client))
                new_checkpoint.repos_queue = sorted(set(repos_list))
                new_checkpoint.current_repo_index = 0
                new_checkpoint.next_url = None
                new_checkpoint.stage = self._first_stage()
                new_checkpoint.default_branch = None

            repos = new_checkpoint.repos_queue
            if not repos or new_checkpoint.current_repo_index >= len(repos):
                new_checkpoint.has_more = False
                return new_checkpoint

            if not self._enabled_stages():
                new_checkpoint.has_more = False
                return new_checkpoint

            repo_slug = repos[new_checkpoint.current_repo_index]
            stage = new_checkpoint.stage or self._first_stage()
            if stage == STAGE_PRS:
                yield from self._yield_prs(client, repo_slug, start, end, new_checkpoint)
            elif stage == STAGE_REPO:
                yield from self._yield_repo(client, repo_slug, new_checkpoint)
            elif stage == STAGE_README:
                yield from self._yield_readme(client, repo_slug, new_checkpoint)
            elif stage == STAGE_COMMITS:
                yield from self._yield_commits(client, repo_slug, start, end, new_checkpoint)
            else:
                self._advance_stage(new_checkpoint)

        return new_checkpoint

    @override
    def build_dummy_checkpoint(self) -> BitbucketConnectorCheckpoint:
        """Create an initial checkpoint with work remaining."""
        return BitbucketConnectorCheckpoint(has_more=True)

    @override
    def validate_checkpoint_json(
        self, checkpoint_json: str
    ) -> BitbucketConnectorCheckpoint:
        """Validate and deserialize a checkpoint instance from JSON."""
        return BitbucketConnectorCheckpoint.model_validate_json(checkpoint_json)

    def _append_slim(
        self,
        batch: list[SlimDocument | HierarchyNode],
        doc_id: str,
        created_at: Any,
        callback: IndexingHeartbeatInterface | None,
    ) -> Iterator[list[SlimDocument | HierarchyNode]]:
        """Add one slim id and yield the batch when it is full."""
        batch.append(
            SlimDocument(id=doc_id, doc_created_at=parse_bitbucket_datetime(created_at))
        )
        if len(batch) < self.batch_size:
            return
        yield list(batch)
        batch.clear()
        if callback:
            if callback.should_stop():
                raise RuntimeError("bitbucket_pr_sync: Stop signal detected")
            callback.progress("bitbucket_pr_sync", self.batch_size)

    def retrieve_all_slim_docs_perm_sync(
        self,
        start: SecondsSinceUnixEpoch | None = None,
        end: SecondsSinceUnixEpoch | None = None,
        callback: IndexingHeartbeatInterface | None = None,
    ) -> Iterator[list[SlimDocument | HierarchyNode]]:
        """Document IDs for PRs, repo overview, README, and windowed commits."""
        batch: list[SlimDocument | HierarchyNode] = []
        params = self._build_params(
            fields=SLIM_PR_LIST_RESPONSE_FIELDS,
            start=start,
            end=end,
        )
        with self._client() as client:
            for slug in self._iter_target_repositories(client):
                repo = None
                if self.include_repo or self.include_readme or self.include_commits:
                    try:
                        repo = fetch_repository(client, self.workspace, slug)
                    except Exception:
                        repo = None
                branch = default_branch_name(repo) if repo else None
                if self.include_repo and repo is not None:
                    yield from self._append_slim(
                        batch, repo_document_id(self.workspace, slug), repo.get("created_on"), callback
                    )
                if self.include_readme and branch:
                    entries = fetch_src_listing(client, self.workspace, slug, branch)
                    if pick_readme_path(entries):
                        yield from self._append_slim(
                            batch, readme_document_id(self.workspace, slug), None, callback
                        )
                if self.include_commits and branch:
                    next_url: str | None = None
                    while True:
                        try:
                            items, next_url = fetch_commits_page(
                                client, self.workspace, slug, branch, next_url
                            )
                        except Exception:
                            break
                        reached_floor = False
                        for commit in items:
                            if commit_is_older_than_start(commit.get("date"), start):
                                reached_floor = True
                                break
                            if not timestamp_in_window(commit.get("date"), start, end):
                                continue
                            commit_hash = commit.get("hash")
                            if not commit_hash:
                                continue
                            yield from self._append_slim(
                                batch,
                                commit_document_id(self.workspace, slug, str(commit_hash)),
                                commit.get("date"),
                                callback,
                            )
                        if reached_floor or not next_url:
                            break
                if self.include_prs:
                    for pr in self._iter_pull_requests_for_repo(client, slug, params=params):
                        pr_id = pr["id"]
                        yield from self._append_slim(
                            batch,
                            f"{DocumentSource.BITBUCKET.value}:{self.workspace}:{slug}:pr:{pr_id}",
                            pr.get("created_on"),
                            callback,
                        )
        if batch:
            yield batch

    def _validation_url(self) -> str:
        """Repo URL when slugs are set; otherwise the workspace resource.

        Do not probe GET /repositories/{workspace}?fields=pagelen. Scoped API
        tokens often 404 on that list even when the account owns a repo.
        """
        workspace = quote(self.workspace.strip(), safe="")
        if self._repositories:
            repo = quote(self._repositories[0], safe="")
            return f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}"
        return f"https://api.bitbucket.org/2.0/workspaces/{workspace}"

    def validate_connector_settings(self) -> None:
        """Validate Bitbucket credentials against the configured repo or workspace.

        Raises:
            CredentialExpiredError: on HTTP 401
            InsufficientPermissionsError: on HTTP 403
            UnexpectedValidationError: on any other failure
        """
        try:
            with self._client() as client:
                resp = client.get(self._validation_url(), timeout=REQUEST_TIMEOUT_SECONDS)
                if resp.status_code == 401:
                    raise CredentialExpiredError(
                        "Invalid or expired Bitbucket credentials (HTTP 401)."
                    )
                if resp.status_code == 403:
                    raise InsufficientPermissionsError(
                        "Insufficient permissions to access Bitbucket workspace (HTTP 403)."
                    )
                if resp.status_code < 200 or resp.status_code >= 300:
                    raise UnexpectedValidationError(
                        f"Unexpected Bitbucket error (status={resp.status_code})."
                    )
        except Exception as e:
            if isinstance(
                e,
                (
                    CredentialExpiredError,
                    InsufficientPermissionsError,
                    UnexpectedValidationError,
                    ConnectorMissingCredentialError,
                ),
            ):
                raise
            raise UnexpectedValidationError(
                f"Unexpected error while validating Bitbucket settings: {e}"
            )
