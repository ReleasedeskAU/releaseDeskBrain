"""Module with custom fields processing functions"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List
from urllib.parse import urlparse

from jira import JIRA
from jira.exceptions import JIRAError
from jira.resources import CustomFieldOption, Issue, User

from onyx.connectors.cross_connector_utils.attribution import (
    format_attributed_message,
)
from onyx.connectors.cross_connector_utils.miscellaneous_utils import scoped_url
from onyx.connectors.cross_connector_utils.rate_limit_wrapper import (
    RateLimitTriedTooManyTimesError,
    wrap_request_to_handle_ratelimiting,
)
from onyx.connectors.models import BasicExpertInfo
from onyx.utils.logger import setup_logger
from onyx.utils.retry_after import parse_retry_after_seconds

logger = setup_logger()


PROJECT_URL_PAT = "projects"
JIRA_SERVER_API_VERSION = os.environ.get("JIRA_SERVER_API_VERSION") or "2"
JIRA_CLOUD_API_VERSION = os.environ.get("JIRA_CLOUD_API_VERSION") or "3"

# Source-present vs tagged — logged after each sync so incomplete fields cannot hide.
JIRA_COMPLETENESS_FIELDS = (
    "assignee",
    "assignee_email",
    "reporter",
    "reporter_email",
    "status",
    "priority",
    "resolution",
    "issuetype",
    "project",
    "project_name",
    "labels",
    "key",
    "created",
    "updated",
    "duedate",
    "resolution_date",
    "parent",
    "issuelink",
    "issuelink_type",
    "last_updater",
    "status_was",
)


@dataclass
class JiraFieldStats:
    """Per-sync counts of Jira fields present on the payload vs written as tags."""

    issues_fetched: int = 0
    issues_processed: int = 0
    issues_failed: int = 0
    issues_skipped_label: int = 0
    issues_skipped_size: int = 0
    present: dict[str, int] = field(default_factory=dict)
    tagged: dict[str, int] = field(default_factory=dict)
    dropped: dict[str, int] = field(default_factory=dict)

    def observe(
        self,
        field_name: str,
        *,
        present: bool,
        tagged: bool,
        issue_key: str,
        reason: str | None = None,
    ) -> None:
        """Record one field on one issue. Logs when the source had a value we did not tag."""
        if present:
            self.present[field_name] = self.present.get(field_name, 0) + 1
        if tagged:
            self.tagged[field_name] = self.tagged.get(field_name, 0) + 1
            return
        if not present:
            return
        self.dropped[field_name] = self.dropped.get(field_name, 0) + 1
        logger.warning(
            "Jira issue %s: field %s present on the source record but not tagged (%s)",
            issue_key,
            field_name,
            reason or "unusable value",
        )

    def log_summary(self) -> None:
        """Emit completeness rates. Mismatches are errors so they cannot be ignored."""
        parts = [
            f"{name}={self.tagged.get(name, 0)}/{self.present.get(name, 0)}"
            for name in JIRA_COMPLETENESS_FIELDS
        ]
        logger.notice(
            "Jira sync summary: fetched=%s emitted=%s failed=%s "
            "skipped_label=%s skipped_size=%s field_completeness=[%s]",
            self.issues_fetched,
            self.issues_processed,
            self.issues_failed,
            self.issues_skipped_label,
            self.issues_skipped_size,
            " ".join(parts),
        )
        for name in JIRA_COMPLETENESS_FIELDS:
            n_present = self.present.get(name, 0)
            n_tagged = self.tagged.get(name, 0)
            if n_present and n_tagged != n_present:
                logger.error(
                    "Jira sync field mismatch: %s tagged %s of %s source-present values — index is incomplete",
                    name,
                    n_tagged,
                    n_present,
                )


def _stringish(value: Any) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return None


def _payload_only(obj: Any, key: str) -> Any:
    """Read a field from the JSON already on the issue. Never Resource getattr."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    raw = getattr(obj, "raw", None)
    if isinstance(raw, dict):
        return raw.get(key)
    return None


def _attr_or_raw(obj: Any, key: str) -> Any:
    """Read a Jira field from payload JSON, then an already-set instance attribute."""
    value = _payload_only(obj, key)
    if value is not None:
        return value
    if obj is None or isinstance(obj, dict):
        return None
    try:
        return object.__getattribute__(obj, key)
    except Exception:
        return None


def jira_user_display_name(obj: Any) -> str | None:
    """Visible name already on the assignee/reporter object. Email is never used."""
    return _stringish(_attr_or_raw(obj, "displayName")) or _stringish(
        _attr_or_raw(obj, "name")
    )


def jira_user_email(obj: Any) -> str | None:
    """Email only when Jira already included it on the issue payload.

    Must not getattr emailAddress on a python-jira User: that can lazy-load
    /rest/api/3/user and fail for privacy-restricted accounts. The name tag
    must not depend on this call.
    """
    return _stringish(_payload_only(obj, "emailAddress"))


def jira_named_value(obj: Any) -> str | None:
    """Status/priority/issuetype/project name from a Resource or raw dict."""
    if obj is None:
        return None
    direct = _stringish(obj)
    if direct:
        return direct
    return _stringish(_attr_or_raw(obj, "name"))


# Jira statusCategory.key values. Display names are localized — never stored.
STATUS_CATEGORY_KEYS = frozenset({"new", "indeterminate", "done"})


def jira_status_category_key(status_obj: Any) -> str | None:
    """Portable Jira category key from a status Resource or dict.

    Args:
        status_obj: Jira status object (may include nested statusCategory).

    Returns:
        ``new``, ``indeterminate``, or ``done`` when present; otherwise None.
        Invalid or localized names are not mapped.
    """
    if status_obj is None:
        return None
    category = _attr_or_raw(status_obj, "statusCategory")
    raw = _stringish(_attr_or_raw(category, "key") if category is not None else None)
    if not raw:
        return None
    key = raw.strip().lower()
    return key if key in STATUS_CATEGORY_KEYS else None


def jira_key_value(obj: Any) -> str | None:
    """Issue or project key from a Resource or raw dict."""
    if obj is None:
        return None
    return _stringish(_attr_or_raw(obj, "key")) or _stringish(obj)


def jira_label_values(obj: Any) -> list[str]:
    """Normalize labels to a list of strings. Objects without a name are skipped."""
    if not obj:
        return []
    items = obj if isinstance(obj, list) else [obj]
    values: list[str] = []
    for item in items:
        text = _stringish(item) or jira_named_value(item)
        if text:
            values.append(text)
    return values


def best_effort_basic_expert_info(obj: Any) -> BasicExpertInfo | None:
    """Person identity for owners. Name comes from displayName; email is optional."""
    if obj is None:
        return None
    display_name = jira_user_display_name(obj)
    if not display_name:
        return None
    return BasicExpertInfo(display_name=display_name, email=jira_user_email(obj))


def best_effort_get_field_from_issue(jira_issue: Issue, field: str) -> Any:
    """Read issue fields from raw JSON first so Resource getattr cannot hide values."""
    if isinstance(jira_issue, dict):
        fields = jira_issue.get("fields")
        if isinstance(fields, dict) and field in fields:
            return fields[field]
        return jira_issue.get(field)
    raw = getattr(jira_issue, "raw", None)
    if isinstance(raw, dict):
        fields = raw.get("fields")
        if isinstance(fields, dict) and field in fields:
            return fields[field]
        if field in raw:
            return raw[field]
    try:
        fields_obj = getattr(jira_issue, "fields", None)
        if isinstance(fields_obj, dict):
            return fields_obj.get(field)
        if fields_obj is not None:
            return getattr(fields_obj, field, None)
    except Exception:
        logger.warning("Failed reading Jira field %s from issue object", field)
    return None


# Structural ADF nodes: walk children, no warning.
_ADF_CONTAINERS = frozenset(
    {
        "doc",
        "paragraph",
        "heading",
        "blockquote",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
        "panel",
        "table",
        "tableRow",
        "tableCell",
        "tableHeader",
        "mediaSingle",
        "mediaGroup",
        "expand",
        "nestedExpand",
        "decisionList",
        "decisionItem",
        "taskList",
        "taskItem",
        "layoutSection",
        "layoutColumn",
    }
)
_ADF_BLOCK_BREAKS = frozenset({"paragraph", "heading", "listItem", "blockquote"})
_JIRA_COMMENT_PAGE_SIZE = 100
_JIRA_CHANGELOG_PAGE_SIZE = 100
_JIRA_429_DEFAULT_WAIT_SEC = 30
_JIRA_429_MAX_WAIT_SEC = 300
_JIRA_429_MAX_RETRIES = 8


def _adf_link_href(node: dict[str, Any]) -> str | None:
    for mark in node.get("marks") or []:
        if isinstance(mark, dict) and mark.get("type") == "link":
            href = (mark.get("attrs") or {}).get("href")
            if isinstance(href, str) and href.strip():
                return href.strip()
    return None


def _adf_leaf_text(node: dict[str, Any]) -> str:
    """Readable text for one ADF leaf. Mentions, status, emoji, and links stay visible."""
    ntype = node.get("type")
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    if ntype == "text":
        text = _stringish(node.get("text")) or ""
        href = _adf_link_href(node)
        return f"[{text}]({href})" if href and text else text
    if ntype == "mention":
        name = _stringish(attrs.get("text")) or _stringish(attrs.get("id")) or "mention"
        return f"@{name.lstrip('@')}"
    if ntype == "emoji":
        return _stringish(attrs.get("text")) or _stringish(attrs.get("shortName")) or ""
    if ntype == "status":
        return f"[{_stringish(attrs.get('text')) or 'status'}]"
    if ntype == "date":
        return _stringish(str(attrs.get("timestamp") or "")) or ""
    if ntype in {"inlineCard", "blockCard", "embedCard"}:
        url = attrs.get("url")
        if isinstance(url, dict):
            url = url.get("url")
        return _stringish(url) or ""
    if ntype == "hardBreak":
        return "\n"
    if ntype == "rule":
        return "\n---\n"
    if ntype == "media":
        label = _stringish(attrs.get("alt")) or _stringish(attrs.get("id"))
        return f"[media: {label}]" if label else "[media]"
    return ""


def _adf_attr_fallback(node: dict[str, Any]) -> str:
    attrs = node.get("attrs") if isinstance(node.get("attrs"), dict) else {}
    for key in ("text", "shortName", "url", "alt", "id"):
        value = attrs.get(key)
        if isinstance(value, dict):
            value = value.get("url") or value.get("text")
        text = _stringish(value)
        if text:
            return text
    return _stringish(node.get("text")) or ""


def extract_text_from_adf(adf: dict | str | None) -> str:
    """Turn ADF into readable text. Unknown node types warn instead of vanishing.

    https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/
    """
    if adf is None:
        return ""
    if isinstance(adf, str):
        return adf
    if not isinstance(adf, dict):
        return str(adf)
    parts: list[str] = []
    unknown: set[str] = set()
    _walk_adf(adf, parts, unknown)
    return _join_adf_parts(parts)


def _walk_adf(node: Any, parts: list[str], unknown: set[str]) -> None:
    if not isinstance(node, dict):
        return
    ntype = node.get("type")
    leaf = _adf_leaf_text(node)
    children = [
        child for child in (node.get("content") or []) if isinstance(child, dict)
    ]
    if ntype in {
        "text",
        "mention",
        "emoji",
        "status",
        "date",
        "hardBreak",
        "rule",
        "media",
    } or ntype in {
        "inlineCard",
        "blockCard",
        "embedCard",
    }:
        if leaf:
            parts.append(leaf)
        return
    if ntype not in _ADF_CONTAINERS:
        if ntype not in unknown:
            unknown.add(str(ntype))
            logger.warning("Unhandled ADF node type %s; using text fallback", ntype)
        fallback = _adf_attr_fallback(node)
        if fallback:
            parts.append(fallback)
    for child in children:
        _walk_adf(child, parts, unknown)
    if ntype in _ADF_BLOCK_BREAKS:
        parts.append("\n")


def _join_adf_parts(parts: list[str]) -> str:
    chunks: list[str] = []
    for part in parts:
        if not part:
            continue
        if chunks and not part.startswith("\n") and not chunks[-1].endswith("\n"):
            chunks.append(" ")
        chunks.append(part)
    return "".join(chunks).strip()


def jira_body_text(payload: Any) -> str:
    """Plain text from a Jira description/comment body (string or ADF)."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        return extract_text_from_adf(payload)
    raw = getattr(payload, "raw", None)
    if isinstance(raw, dict):
        return extract_text_from_adf(raw)
    for attr in ("body", "text"):
        value = getattr(payload, attr, None)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return extract_text_from_adf(value)
    return ""


def jira_issue_link_pairs(issue: Any) -> list[tuple[str, str]]:
    """(link type phrase, linked key) from the issue payload. No live follow-up fetch."""
    raw = best_effort_get_field_from_issue(issue, "issuelinks")
    if not raw:
        return []
    items = raw if isinstance(raw, list) else [raw]
    pairs: list[tuple[str, str]] = []
    for item in items:
        pair = _one_issue_link(item)
        if pair:
            pairs.append(pair)
    return pairs


def jira_changelog_payload(issue: Any) -> dict[str, Any] | None:
    """Changelog already on the issue (expand=changelog). None if it was not fetched."""
    raw = getattr(issue, "raw", None)
    if isinstance(raw, dict) and isinstance(raw.get("changelog"), dict):
        return raw["changelog"]
    changelog = getattr(issue, "changelog", None)
    if isinstance(changelog, dict):
        return changelog
    if changelog is not None:
        nested = getattr(changelog, "raw", None)
        if isinstance(nested, dict):
            return nested
        histories = getattr(changelog, "histories", None)
        if histories is not None:
            return {"histories": list(histories)}
    return None


def changelog_is_truncated(changelog: dict[str, Any]) -> bool:
    """True when the expand payload is a prefix of the full history."""
    histories = _changelog_histories(changelog)
    total = changelog.get("total")
    return isinstance(total, int) and total > len(histories)


def fetch_all_jira_changelog(jira_client: JIRA, issue_key: str) -> dict[str, Any]:
    """Page the issue changelog API. Used at index time when expand is truncated."""
    histories: list[Any] = []
    start_at = 0
    total: int | None = None
    changelog_url = jira_client._get_url(f"issue/{issue_key}/changelog")
    while True:
        params = {"maxResults": _JIRA_CHANGELOG_PAGE_SIZE, "startAt": start_at}
        response = jira_session_request(
            jira_client._session, "get", changelog_url, params=params
        )
        payload = response.json()
        page = payload.get("values") or payload.get("histories") or []
        histories.extend(page)
        if isinstance(payload.get("total"), int):
            total = payload["total"]
        start_at += len(page)
        if not page:
            break
        if total is not None and start_at >= total:
            break
    return {"histories": histories, "total": total if total is not None else len(histories)}


def jira_last_updater(changelog: dict[str, Any]) -> str | None:
    """Display name of the most recent changelog author. Never an email."""
    latest: tuple[str, str] | None = None
    for history in _changelog_histories(changelog):
        if not isinstance(history, dict):
            continue
        created = _stringish(history.get("created")) or ""
        name = jira_user_display_name(history.get("author"))
        if not name:
            continue
        if latest is None or created >= latest[0]:
            latest = (created, name)
    return latest[1] if latest else None


def jira_status_was_values(changelog: dict[str, Any]) -> list[str]:
    """Distinct status names that appear in changelog from/to strings."""
    seen: list[str] = []
    for history in _changelog_histories(changelog):
        if not isinstance(history, dict):
            continue
        for item in history.get("items") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("field") or "").lower() != "status":
                continue
            for key in ("fromString", "toString"):
                value = _stringish(item.get(key))
                if value and value not in seen:
                    seen.append(value)
    return seen


def _changelog_histories(changelog: dict[str, Any]) -> list[Any]:
    histories = changelog.get("histories") or changelog.get("values") or []
    return list(histories) if isinstance(histories, list) else []


def _one_issue_link(item: Any) -> tuple[str, str] | None:
    raw = item if isinstance(item, dict) else getattr(item, "raw", None)
    if not isinstance(raw, dict):
        raw = {
            "type": getattr(item, "type", None),
            "inwardIssue": getattr(item, "inwardIssue", None),
            "outwardIssue": getattr(item, "outwardIssue", None),
        }
    link_type = raw.get("type") if isinstance(raw.get("type"), dict) else {}
    inward = raw.get("inwardIssue")
    outward = raw.get("outwardIssue")
    if inward is not None:
        phrase = _stringish(link_type.get("inward")) or "is related to"
        key = jira_key_value(inward)
        return (phrase, key) if key else None
    if outward is not None:
        phrase = _stringish(link_type.get("outward")) or "relates to"
        key = jira_key_value(outward)
        return (phrase, key) if key else None
    return None


def jira_issue_key(issue: Any) -> str:
    """Issue key from the payload or object, without lazy field loads."""
    return (
        _stringish(getattr(issue, "key", None))
        or jira_key_value(issue)
        or _stringish(best_effort_get_field_from_issue(issue, "key"))
        or "unknown"
    )


def jira_issue_plain_field(issue: Issue, field: str) -> str:
    """Read one issue field as text from raw JSON first."""
    return jira_body_text(best_effort_get_field_from_issue(issue, field))


def jira_session_request(session: Any, method: str, *args: Any, **kwargs: Any) -> Any:
    """HTTP call that retries Jira 429s using Retry-After, then raise_for_status."""
    request_fn = wrap_request_to_handle_ratelimiting(
        getattr(session, method),
        default_wait_time_sec=_JIRA_429_DEFAULT_WAIT_SEC,
        max_waits=_JIRA_429_MAX_RETRIES,
        max_wait_time_sec=_JIRA_429_MAX_WAIT_SEC,
    )
    response = request_fn(*args, **kwargs)
    response.raise_for_status()
    return response


def jira_sdk_call(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Retry python-jira SDK calls when Jira returns 429."""
    last_error: Exception | None = None
    for _ in range(_JIRA_429_MAX_RETRIES):
        try:
            return func(*args, **kwargs)
        except JIRAError as exc:
            last_error = exc
            if exc.status_code != 429:
                raise
            response = getattr(exc, "response", None)
            headers = (
                getattr(response, "headers", None)
                or getattr(exc, "headers", None)
                or {}
            )
            parsed = parse_retry_after_seconds(
                headers.get("Retry-After") if hasattr(headers, "get") else None
            )
            wait = min(parsed or _JIRA_429_DEFAULT_WAIT_SEC, _JIRA_429_MAX_WAIT_SEC)
            logger.warning(
                "Jira SDK rate-limited (429). Retrying after %s seconds", wait
            )
            time.sleep(wait)
    raise RateLimitTriedTooManyTimesError(
        f"Exceeded '{_JIRA_429_MAX_RETRIES}' Jira 429 retries"
    ) from last_error


def _comment_records(comment_field: Any) -> tuple[list[Any], int | None]:
    if comment_field is None:
        return [], None
    if isinstance(comment_field, list):
        return list(comment_field), None
    if isinstance(comment_field, dict):
        comments = comment_field.get("comments") or []
        total = comment_field.get("total")
        return list(comments), int(total) if isinstance(total, int) else None
    comments = getattr(comment_field, "comments", None) or []
    total = getattr(comment_field, "total", None)
    return list(comments), int(total) if isinstance(total, int) else None


def _comment_body_text(comment: Any) -> str:
    if isinstance(comment, dict):
        return jira_body_text(comment.get("body"))
    raw = getattr(comment, "raw", None)
    if isinstance(raw, dict) and "body" in raw:
        return jira_body_text(raw.get("body"))
    return jira_body_text(getattr(comment, "body", None))


def _comment_author(comment: Any) -> Any:
    if isinstance(comment, dict):
        return comment.get("author")
    raw = getattr(comment, "raw", None)
    if isinstance(raw, dict) and "author" in raw:
        return raw.get("author")
    return getattr(comment, "author", None)


def fetch_all_jira_comments(jira_client: JIRA, issue_key: str) -> list[Any]:
    """Page every comment for one issue. Raises if the source total is not reached."""
    comments: list[Any] = []
    start_at = 0
    total: int | None = None
    next_page_token: str | None = None
    comment_url = jira_client._get_url(f"issue/{issue_key}/comment")
    while True:
        params: dict[str, Any] = {"maxResults": _JIRA_COMMENT_PAGE_SIZE}
        if next_page_token:
            params["nextPageToken"] = next_page_token
        else:
            params["startAt"] = start_at
        response = jira_session_request(
            jira_client._session, "get", comment_url, params=params
        )
        payload = response.json()
        page = payload.get("comments") or []
        comments.extend(page)
        if isinstance(payload.get("total"), int):
            total = payload["total"]
        next_page_token = payload.get("nextPageToken")
        start_at = int(payload.get("startAt") or start_at) + len(page)
        if total is not None and len(comments) >= total:
            break
        if not page:
            break
        if not next_page_token and (total is None or start_at >= total):
            break
    if total is not None and len(comments) != total:
        raise RuntimeError(
            f"Jira issue {issue_key}: fetched {len(comments)} of {total} comments"
        )
    return comments


def get_comment_strs(
    issue: Issue,
    comment_email_blacklist: tuple[str, ...] = (),
    jira_client: JIRA | None = None,
) -> list[str]:
    """Attributed ``Name: text`` lines for every comment on an issue.

    Pages the comment API when ``jira_client`` is set and the first page is
    truncated. Author emails are used only for the blacklist and never written
    into the returned text.

    Raises RuntimeError if a known comment total cannot be fully fetched.
    """
    issue_key = jira_issue_key(issue)
    comment_field = best_effort_get_field_from_issue(issue, "comment")
    comments, total = _comment_records(comment_field)
    incomplete = total is not None and len(comments) < total
    # Missing total on a non-empty first page is the classic silent 20-comment cut-off.
    if jira_client is not None and (
        comment_field is None or incomplete or (comments and total is None)
    ):
        comments = fetch_all_jira_comments(jira_client, issue_key)
        total = len(comments)
    if total is not None and len(comments) < total:
        raise RuntimeError(
            f"Jira issue {issue_key}: only {len(comments)} of {total} comments were available"
        )
    bodies: list[str] = []
    for comment in comments:
        try:
            author = _comment_author(comment)
            author_email = jira_user_email(author)
            if author_email and author_email in comment_email_blacklist:
                continue
            # Display name only — email is for the blacklist, never the body.
            bodies.append(
                format_attributed_message(
                    jira_user_display_name(author),
                    _comment_body_text(comment),
                )
            )
        except Exception as exc:
            logger.error("Failed to process comment on %s: %s", issue_key, exc)
            bodies.append(format_attributed_message(None, "[unreadable comment]"))
    return bodies


def build_jira_url(jira_base_url: str, issue_key: str) -> str:
    """
    Get the url used to access an issue in the UI.
    """
    return f"{jira_base_url}/browse/{issue_key}"


def build_jira_client(
    credentials: dict[str, Any], jira_base: str, scoped_token: bool = False
) -> JIRA:
    jira_base = scoped_url(jira_base, "jira") if scoped_token else jira_base
    api_token = credentials["jira_api_token"]
    # if user provide an email we assume it's cloud
    if "jira_user_email" in credentials:
        email = credentials["jira_user_email"]
        return JIRA(
            basic_auth=(email, api_token),
            server=jira_base,
            options={"rest_api_version": JIRA_CLOUD_API_VERSION},
        )
    else:
        return JIRA(
            token_auth=api_token,
            server=jira_base,
            options={"rest_api_version": JIRA_SERVER_API_VERSION},
        )


def extract_jira_project(url: str) -> tuple[str, str]:
    parsed_url = urlparse(url)
    jira_base = parsed_url.scheme + "://" + parsed_url.netloc

    # Split the path by '/' and find the position of 'projects' to get the project name
    split_path = parsed_url.path.split("/")
    if PROJECT_URL_PAT in split_path:
        project_pos = split_path.index(PROJECT_URL_PAT)
        if len(split_path) > project_pos + 1:
            jira_project = split_path[project_pos + 1]
        else:
            raise ValueError("No project name found in the URL")
    else:
        raise ValueError("'projects' not found in the URL")

    return jira_base, jira_project


def get_jira_project_key_from_issue(issue: Issue) -> str | None:
    if not hasattr(issue, "fields"):
        return None
    if not hasattr(issue.fields, "project"):
        return None
    if not hasattr(issue.fields.project, "key"):
        return None

    return issue.fields.project.key


class CustomFieldExtractor:
    @staticmethod
    def _process_custom_field_value(value: Any) -> str:
        """
        Process a custom field value to a string
        """
        try:
            if value is None:
                return ""
            if isinstance(value, str):
                return value
            elif isinstance(value, bool):
                return "true" if value else "false"
            elif isinstance(value, (int, float)):
                return str(value)
            elif isinstance(value, CustomFieldOption):
                return value.value
            elif isinstance(value, User):
                return value.displayName
            elif isinstance(value, dict):
                for key in ("value", "name", "displayName"):
                    inner = value.get(key)
                    if inner not in (None, ""):
                        return CustomFieldExtractor._process_custom_field_value(inner)
                return ""
            elif isinstance(value, List):
                return " ".join(
                    [
                        part
                        for v in value
                        if (part := CustomFieldExtractor._process_custom_field_value(v))
                    ]
                )
            else:
                return str(value)
        except Exception as e:
            logger.error("Error processing custom field value %s: %s", value, e)
            return ""

    @staticmethod
    def get_issue_custom_fields(
        jira: Issue, custom_fields: dict, max_value_length: int = 250
    ) -> dict:
        """
        Process all custom fields of an issue to a dictionary of strings
        :param jira: jira_issue, bug or similar
        :param custom_fields: custom fields dictionary
        :param max_value_length: maximum length of the value to be processed, if exceeded, it will be truncated
        """

        issue_custom_fields = {
            custom_fields[key]: value
            for key, value in jira.fields.__dict__.items()
            if value and key in custom_fields.keys()
        }

        processed_fields = {}

        if issue_custom_fields:
            for key, value in issue_custom_fields.items():
                processed = CustomFieldExtractor._process_custom_field_value(value)
                # We need max length  parameter, because there are some plugins that often has very long description
                # and there is just a technical information so we just avoid long values
                if len(processed) < max_value_length:
                    processed_fields[key] = processed

        return processed_fields

    @staticmethod
    def get_all_custom_fields(jira_client: JIRA) -> dict:
        """Get all custom fields from Jira"""
        fields = jira_client.fields()
        fields_dct = {
            field["id"]: field["name"] for field in fields if field["custom"] is True
        }
        return fields_dct


# Rank is Jira Software's board order, not a tenant-named estimate field.
_SKIP_CUSTOM_FIELD_NAMES = frozenset({"rank"})
_LEXORANK_VALUE = re.compile(r"^\d+\|")
_CUSTOM_FIELD_MAX_CHARS = 250


def extract_populated_custom_field_lines(
    issue: Issue,
    custom_field_names: dict[str, str],
    max_value_length: int = _CUSTOM_FIELD_MAX_CHARS,
) -> list[str]:
    """Name: value lines for populated custom fields.

    Uses issue.raw["fields"] when present so MagicMock / bulk-fetch payloads work.
    Skips Rank and lexorank board-order values. Does not guess tenant field names.

    Returns:
        Display lines such as ``Story point estimate: 13``. Empty when none apply.
    """
    raw_fields: dict[str, Any] = {}
    raw = getattr(issue, "raw", None)
    if isinstance(raw, dict):
        maybe = raw.get("fields")
        if isinstance(maybe, dict):
            raw_fields = maybe

    lines: list[str] = []
    for field_id, name in custom_field_names.items():
        label = (name or "").strip()
        if not field_id or not label or label.lower() in _SKIP_CUSTOM_FIELD_NAMES:
            continue
        value = raw_fields.get(field_id) if field_id in raw_fields else None
        if value is None and hasattr(issue, "fields"):
            value = getattr(issue.fields, field_id, None)
        processed = CustomFieldExtractor._process_custom_field_value(value).strip()
        if not processed or _LEXORANK_VALUE.match(processed):
            continue
        if len(processed) > max_value_length:
            processed = processed[:max_value_length]
        lines.append(f"{label}: {processed}")
    return lines


class CommonFieldExtractor:
    @staticmethod
    def get_issue_common_fields(jira: Issue) -> dict:
        return {
            "Priority": jira.fields.priority.name if jira.fields.priority else None,
            "Reporter": (
                jira.fields.reporter.displayName if jira.fields.reporter else None
            ),
            "Assignee": (
                jira.fields.assignee.displayName if jira.fields.assignee else None
            ),
            "Status": jira.fields.status.name if jira.fields.status else None,
            "Resolution": (
                jira.fields.resolution.name if jira.fields.resolution else None
            ),
        }
