"""Exact unique-document counts from indexed Postgres tags — not OpenSearch top-N search.

StaffLess POST /admin/search always returns a sample (empty query: 10 random
chunks; keyword: NUM_RETURNED_HITS). OpenSearch can count, but the HTTP search
API does not expose _count. Unique document_id via document__tag is the census
used for how-many answers.
"""

from __future__ import annotations

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from onyx.configs.constants import DocumentSource
from onyx.connectors.field_schema import (
    FieldDecl,
    PII_TAG_KEYS,
    contains_match_keys,
    effective_selection,
)
from onyx.connectors.indexed_schemas import schema_for_source
from onyx.db.document_date_filter import (
    DATE_TAG_KEYS,
    RESOLVED_STATUS_CATEGORY,
    SORT_BY_VALUES,
    STATUS_CATEGORY_VALUES,
    DateRangeSpec,
    intersect_date_range_ids,
)
from onyx.db.models import Connector, DocumentByConnectorCredentialPair, Document__Tag, Tag

# Never queryable or returned. Enforced at parse and at field projection.
# PII_TAG_KEYS is the platform blocklist (onyx.connectors.field_schema).

ALLOWED_TAG_KEYS = frozenset(
    {
        "assignee",
        "status",
        "status_category",
        "priority",
        "project",
        "project_name",
        "labels",
        "issuetype",
        "reporter",
        "key",
        "parent",
        "duedate",
        "created",
        "updated",
        "resolution",
        "resolution_date",
        "issuelink",
        "issuelink_type",
        "last_updater",
        "status_was",
        "repo",
        "object_type",
        "num_files_changed",
        "num_commits",
        "state",
        "merged",
        "channel",
        "author",
    }
)
CONTAINS_TAG_KEYS = frozenset(
    {"assignee", "reporter", "labels", "last_updater", "author"}
)
MAX_FILTER_VALUE_CHARS = 80
MAX_MATCHED_VALUES = 20
MAX_DOCUMENT_KEY_CHARS = 40
MAX_CATALOG_ROWS = 50
MAX_AND_FILTERS = 5

if not ALLOWED_TAG_KEYS.isdisjoint(PII_TAG_KEYS):
    raise RuntimeError("PII tag keys must not be queryable")

# Returned on document-by-key only — not count/filter fields.
DISPLAY_TAG_KEYS = frozenset({"custom_fields"})
BY_KEY_TAG_KEYS = ALLOWED_TAG_KEYS | DISPLAY_TAG_KEYS

if not DISPLAY_TAG_KEYS.isdisjoint(PII_TAG_KEYS):
    raise RuntimeError("PII tag keys must not be returned")
if not DISPLAY_TAG_KEYS.isdisjoint(ALLOWED_TAG_KEYS):
    raise RuntimeError("Display-only tags must not be count/filter fields")


def tag_key_is(filter_field: str):
    """Allow-list keys are lowercase. Slack stored Channel with a capital C."""
    return func.lower(Tag.tag_key) == filter_field


class DocumentCountError(ValueError):
    """Rejected count arguments (unknown field, empty value, etc.)."""


def escape_ilike_pattern(value: str) -> str:
    """Escape ILIKE wildcards so user input cannot broaden the match.

    Args:
        value: Raw filter substring.

    Returns:
        Pattern fragment safe to wrap in %...% with ESCAPE '\\'.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def field_uses_contains_match(field: str) -> bool:
    """Names and labels use contains; keys, status, dates, and parent are exact."""
    return field in CONTAINS_TAG_KEYS


def stored_value_matches_filter(field: str, stored: str, needle: str) -> bool:
    """Same match rule as SQL: contains for names/labels, else case-insensitive equality.

    Args:
        field: Allow-listed tag key.
        stored: Indexed tag_value.
        needle: Caller filter.

    Returns:
        True when this stored value should be included for the filter.
    """
    hay = stored.strip()
    want = needle.strip()
    if not hay or not want:
        return False
    if field_uses_contains_match(field):
        return want.lower() in hay.lower()
    return hay.lower() == want.lower()


def queryable_fields() -> dict[str, object]:
    """Published schema for Ask — the allow-list, not raw tag discovery.

    Returns:
        Sorted field names plus which keys use contains vs exact match.
    """
    return _queryable_payload(ALLOWED_TAG_KEYS)


def queryable_fields_for_source(
    source: DocumentSource | None,
    db_session: Session | None = None,
) -> dict[str, object]:
    """Per-source published fields. Migrated sources use declared schema + selection.

    Unmigrated sources still use ALLOWED_TAG_KEYS. Unset selection is schema
    defaults (today's tags). Empty selection is no optional tags.
    """
    schema = schema_for_source(source)
    if source is None or schema is None:
        return queryable_fields()
    keys = declared_queryable_keys(source, schema, db_session)
    payload = _queryable_payload(keys, contains_keys=contains_match_keys(schema))
    # Slack's published note is unchanged from the verified schema POC.
    if source == DocumentSource.SLACK:
        payload["note"] = (
            "Slack optional tags from this tenant's field selection. "
            "Unset inherits channel and author. Empty list is none. "
            "Unchecking does not purge stored tags or force a re-index."
        )
    elif source == DocumentSource.JIRA:
        payload["note"] = (
            "Jira optional tags from this tenant's field selection. "
            "Unset inherits today's Jira catalog (no emails, no custom_fields). "
            "Empty list is none. Unchecking does not purge stored tags or "
            "force a re-index."
        )
    elif source == DocumentSource.GITHUB:
        payload["note"] = (
            "GitHub optional tags from this tenant's field selection. "
            "Unset inherits today's GitHub catalog (object_type, repo, "
            "state, merged, labels, num_commits, num_files_changed — "
            "no people fields). Empty list is none. Unchecking does not "
            "purge stored tags or force a re-index."
        )
    elif source == DocumentSource.GITLAB:
        payload["note"] = (
            "GitLab optional tags from this tenant's field selection. "
            "Unset inherits today's GitLab catalog (key, project, repo, "
            "object_type, state, status, merged, assignee, reporter, "
            "author, created, updated, duedate, labels — display names, "
            "never email). Empty list is none. Unchecking does not "
            "purge stored tags or force a re-index."
        )
    else:
        payload["note"] = (
            "Optional tags from this tenant's field selection. "
            "Unset inherits schema defaults. Empty list is none. "
            "Unchecking does not purge stored tags or force a re-index."
        )
    return payload


def declared_queryable_keys(
    source: DocumentSource,
    schema: tuple[FieldDecl, ...],
    db_session: Session | None,
) -> frozenset[str]:
    """Union of effective selections for one declared-schema source.

    No rows / unset → schema defaults.
    """
    if db_session is None:
        return effective_selection(None, schema)
    stmt = select(Connector.indexed_field_selection).where(Connector.source == source)
    rows = list(db_session.execute(stmt).scalars().all())
    if not rows:
        return effective_selection(None, schema)
    selected: set[str] = set()
    for stored in rows:
        selected |= set(effective_selection(stored, schema))
    return frozenset(selected)


def slack_queryable_keys(db_session: Session | None) -> frozenset[str]:
    """Union of effective Slack selections. No rows / unset → schema defaults."""
    schema = schema_for_source(DocumentSource.SLACK)
    if schema is None:
        return frozenset()
    return declared_queryable_keys(DocumentSource.SLACK, schema, db_session)


def require_source_filter_field(
    filter_field: str | None,
    source: DocumentSource | None,
    db_session: Session | None,
) -> str:
    """Allow-listed tag that is also selected on this source when it has a schema."""
    key = require_filter_field(filter_field)
    _assert_field_allowed_on_source(key, source, db_session)
    return key


def _assert_field_allowed_on_source(
    key: str,
    source: DocumentSource | None,
    db_session: Session | None,
) -> None:
    schema = schema_for_source(source)
    if source is None or schema is None:
        return
    if key not in declared_queryable_keys(source, schema, db_session):
        raise DocumentCountError("Unknown filter field")


def _queryable_payload(
    keys: frozenset[str],
    *,
    contains_keys: frozenset[str] | None = None,
) -> dict[str, object]:
    match_contains = contains_keys if contains_keys is not None else CONTAINS_TAG_KEYS
    fields = sorted(keys)
    contains = sorted(keys & match_contains)
    exact = sorted(keys - match_contains)
    return {
        "fields": fields,
        "contains_match": contains,
        "exact_match": exact,
        "resolved_status_category": RESOLVED_STATUS_CATEGORY,
        "status_category_values": list(STATUS_CATEGORY_VALUES),
        "date_range_fields": sorted(keys & DATE_TAG_KEYS),
        "date_range_params": [
            "created_from",
            "created_to",
            "resolved_from",
            "resolved_to",
            "updated_from",
            "updated_to",
            "due_from",
            "due_to",
            "due_before",
        ],
        "sort_by": list(SORT_BY_VALUES),
        "list_projection": [
            "key",
            "title",
            "link",
            "assignee",
            "author",
            "status",
            "created",
            "updated",
            "duedate",
            "priority",
            "status_category",
            "source",
        ],
        "cap": MAX_CATALOG_ROWS,
        "note": (
            "Queryable indexed tags only. Date ranges use created_from/created_to, "
            "resolved_from/resolved_to, updated_from/updated_to, due_from/due_to/due_before "
            "(YYYY-MM-DD). Resolved = status_category=done (Jira statusCategory.key). "
            "Open = new + indeterminate. Tickets missing status_category are not classified. "
            "Emails and other PII fields are not listed and cannot be queried. "
            "GitHub: unfiltered source=github counts every indexed document "
            "(PullRequest, Issue, Repository, Readme, Commit, File). That is not a repository census. "
            "Repository names are the distinct repo tag. PR vs issue vs overview vs commit use object_type. "
            "Open PRs use object_type=PullRequest AND state=open. Merged PRs use merged=true. "
            "Closed without merging uses state=closed AND merged=false (GitHub state=closed includes merged). "
            "Commits use object_type=Commit (message, file names, line stats; no diffs). "
            "num_files_changed and num_commits are PR tags, not repository counts."
        ),
    }


def parse_count_source(source: str | None) -> DocumentSource | None:
    """Map a wire source string to DocumentSource, or None for all sources.

    Raises:
        DocumentCountError: Unknown source name.
    """
    if source is None or source.strip() == "" or source.strip().lower() == "all":
        return None
    try:
        return DocumentSource(source.strip().lower())
    except ValueError as exc:
        raise DocumentCountError("Unknown source") from exc


def parse_filter_field(filter_field: str | None) -> str | None:
    """Allow-listed metadata tag key, or None for an unfiltered source total.

    Raises:
        DocumentCountError: Field is PII or not in ALLOWED_TAG_KEYS.
    """
    if filter_field is None or filter_field.strip() == "":
        return None
    key = filter_field.strip().lower()
    if key in PII_TAG_KEYS or key not in ALLOWED_TAG_KEYS:
        raise DocumentCountError("Unknown filter field")
    return key


def require_filter_field(filter_field: str | None) -> str:
    """Allow-listed tag key; required for distinct/breakdown queries.

    Raises:
        DocumentCountError: Missing, PII, or unknown field.
    """
    key = parse_filter_field(filter_field)
    if key is None:
        raise DocumentCountError("Filter field is required")
    return key


def parse_document_key(key: str | None) -> str:
    """Exact ticket/document key (e.g. RD-82). Not a contains match.

    Raises:
        DocumentCountError: Empty or too long.
    """
    if key is None:
        raise DocumentCountError("Document key is empty")
    value = key.strip()
    if not value or "\n" in value or "\r" in value:
        raise DocumentCountError("Document key is empty")
    if len(value) > MAX_DOCUMENT_KEY_CHARS:
        raise DocumentCountError("Document key is too long")
    return value


def parse_filter_value(filter_value: str | None) -> str | None:
    """Non-empty filter substring, length-capped.

    Raises:
        DocumentCountError: Value is empty or too long.
    """
    if filter_value is None:
        return None
    value = filter_value.strip()
    if not value:
        raise DocumentCountError("Filter value is empty")
    if len(value) > MAX_FILTER_VALUE_CHARS:
        raise DocumentCountError("Filter value is too long")
    return value


def parse_catalog_filters(
    filter_field: str | None,
    filter_value: str | None,
    filters: list[tuple[str, str]] | None,
) -> list[tuple[str, str]]:
    """Normalize a single pair or an AND list. Empty means unfiltered total.

    Raises:
        DocumentCountError: Mixed pair+list, incomplete pair, too many, or bad field.
    """
    has_pair = filter_field is not None or filter_value is not None
    has_list = bool(filters)
    if has_pair and has_list:
        raise DocumentCountError("Send either a single filter or filters, not both")
    if has_list:
        if len(filters) > MAX_AND_FILTERS:
            raise DocumentCountError("Too many filters")
        parsed: list[tuple[str, str]] = []
        for raw_field, raw_value in filters:
            field = require_filter_field(raw_field)
            value = parse_filter_value(raw_value)
            if value is None:
                raise DocumentCountError("Filter value is empty")
            parsed.append((field, value))
        return parsed
    if (filter_field is None) != (filter_value is None):
        raise DocumentCountError("filter_field and filter_value must be sent together")
    if filter_field is None:
        return []
    value = parse_filter_value(filter_value)
    if value is None:
        raise DocumentCountError("Filter value is empty")
    return [(require_filter_field(filter_field), value)]


def count_indexed_documents(
    db_session: Session,
    *,
    source: DocumentSource | None,
    filters: list[tuple[str, str]],
    date_ranges: list[DateRangeSpec] | None = None,
) -> dict[str, object]:
    """Exact unique indexed document count, optionally AND-filtered by tags.

    Unfiltered counts use connector membership (has_been_indexed). Names/labels
    use contains; key/parent/status/dates use case-insensitive equality.
    Date ranges compare the YYYY-MM-DD prefix of stored date tags.

    Args:
        db_session: Tenant DB session.
        source: Restrict to this DocumentSource, or all sources.
        filters: Allow-listed field/value pairs combined with AND.
        date_ranges: Optional governed date comparisons (due_before, created_from, …).

    Returns:
        count, source, filters with matched values, truncated-style caps.

    Raises:
        DocumentCountError: Invalid filter arguments.
    """
    ranges = date_ranges or []
    range_ids = intersect_date_range_ids(db_session, source, ranges)
    if not filters:
        if range_ids is None:
            count = _count_indexed_by_source(db_session, source)
        else:
            count = len(range_ids)
        return _count_payload(count, source, [])

    resolved = _resolve_filters(db_session, source, filters)
    if any(not item["matched_values"] for item in resolved):
        return _count_payload(0, source, resolved)
    specs = [(item["filter_field"], item["matched_values"]) for item in resolved]
    tag_ids = _intersect_doc_ids(db_session, source, specs)
    if range_ids is not None:
        tag_ids = tag_ids & range_ids
    return _count_payload(len(tag_ids), source, resolved)


def _count_payload(
    count: int,
    source: DocumentSource | None,
    resolved: list[dict[str, object]],
) -> dict[str, object]:
    first = resolved[0] if resolved else None
    return {
        "count": count,
        "source": source.value if source else "all",
        "filters": resolved,
        "filter_field": first["filter_field"] if first else None,
        "filter_value": first["filter_value"] if first else None,
        "matched_values": first["matched_values"] if first else [],
        "note": "Exact unique indexed document count, not a search sample.",
    }


def _resolve_filters(
    db_session: Session,
    source: DocumentSource | None,
    filters: list[tuple[str, str]],
) -> list[dict[str, object]]:
    resolved: list[dict[str, object]] = []
    for field, value in filters:
        _assert_field_allowed_on_source(field, source, db_session)
        matched = _matching_tag_values(db_session, source, field, value)
        resolved.append(
            {
                "filter_field": field,
                "filter_value": value,
                "matched_values": matched[:MAX_MATCHED_VALUES],
            }
        )
    return resolved


def _count_indexed_by_source(db_session: Session, source: DocumentSource | None) -> int:
    stmt = (
        select(func.count(distinct(DocumentByConnectorCredentialPair.id)))
        .select_from(DocumentByConnectorCredentialPair)
        .join(Connector, Connector.id == DocumentByConnectorCredentialPair.connector_id)
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
    )
    if source is not None:
        stmt = stmt.where(Connector.source == source)
    return int(db_session.execute(stmt).scalar_one())


def _tag_value_clause(filter_field: str, filter_value: str):
    if field_uses_contains_match(filter_field):
        pattern = f"%{escape_ilike_pattern(filter_value)}%"
        return Tag.tag_value.ilike(pattern, escape="\\")
    return func.lower(Tag.tag_value) == filter_value.lower()


def _matching_tag_values(
    db_session: Session,
    source: DocumentSource | None,
    filter_field: str,
    filter_value: str,
) -> list[str]:
    stmt = (
        select(Tag.tag_value)
        .where(tag_key_is(filter_field))
        .where(_tag_value_clause(filter_field, filter_value))
        .distinct()
        .limit(MAX_MATCHED_VALUES + 1)
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    return list(db_session.execute(stmt).scalars().all())


def _indexed_doc_ids_for_values(
    db_session: Session,
    source: DocumentSource | None,
    filter_field: str,
    tag_values: list[str],
) -> set[str]:
    stmt = (
        select(Document__Tag.document_id)
        .select_from(Document__Tag)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .join(
            DocumentByConnectorCredentialPair,
            DocumentByConnectorCredentialPair.id == Document__Tag.document_id,
        )
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
        .where(tag_key_is(filter_field))
        .where(Tag.tag_value.in_(tag_values))
        .distinct()
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    return {str(doc_id) for doc_id in db_session.execute(stmt).scalars().all()}


def _intersect_doc_ids(
    db_session: Session,
    source: DocumentSource | None,
    specs: list[tuple[str, list[str]]],
) -> set[str]:
    ids: set[str] | None = None
    for field, values in specs:
        part = _indexed_doc_ids_for_values(db_session, source, field, values)
        ids = part if ids is None else ids & part
        if not ids:
            return set()
    return ids or set()


def matching_document_ids(
    db_session: Session,
    source: DocumentSource | None,
    specs: list[tuple[str, list[str]]],
) -> list[str]:
    """AND-intersection of indexed document ids, ordered."""
    return sorted(_intersect_doc_ids(db_session, source, specs))
