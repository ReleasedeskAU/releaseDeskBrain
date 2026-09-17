"""Exact catalog queries on indexed Postgres tags — not OpenSearch top-N search.

Distinct values, group-by counts, key lookup, and filter lists share the same
unique-document census as document_count. Tags are the source of truth;
doc_metadata is unused.
"""

from __future__ import annotations

from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from onyx.configs.constants import DocumentSource
from onyx.db.document_count import (
    BY_KEY_TAG_KEYS,
    DocumentCountError,
    MAX_CATALOG_ROWS,
    MAX_DOCUMENT_KEY_CHARS,
    PII_TAG_KEYS,
    _count_indexed_by_source,
    _resolve_filters,
    escape_ilike_pattern,
    matching_document_ids,
    tag_key_is,
)
from onyx.db.document_date_filter import (
    DATE_TAG_KEYS,
    DateRangeSpec,
    intersect_date_range_ids,
    parse_date_bucket,
    parse_sort_by,
)
from onyx.db.models import Connector, Document, DocumentByConnectorCredentialPair, Document__Tag, Tag

KEY_TAG = "key"
LIST_PROJECTION_KEYS = (
    "assignee",
    "author",
    "status",
    "status_category",
    "priority",
    "created",
    "updated",
    "duedate",
)


def ticket_key_from_semantic_id(semantic_id: str | None) -> str | None:
    """Best-effort ticket key from `RD-82: title` or `RD-82 title` semantic ids."""
    if not semantic_id:
        return None
    trimmed = semantic_id.strip()
    for sep in (": ", " "):
        if sep not in trimmed:
            continue
        prefix = trimmed.split(sep, 1)[0].strip()
        if prefix and len(prefix) <= MAX_DOCUMENT_KEY_CHARS:
            return prefix
    return None


def list_distinct_tag_values(
    db_session: Session,
    *,
    source: DocumentSource | None,
    filter_field: str,
) -> dict[str, object]:
    """Distinct stored values for one allow-listed tag key on indexed documents.

    Args:
        db_session: Tenant DB session.
        source: Restrict to this DocumentSource, or all sources.
        filter_field: Allow-listed tag key.

    Returns:
        values, untagged_count, total_indexed, truncated flag.
    """
    groups, truncated = _group_counts(db_session, source, filter_field)
    total = _count_indexed_by_source(db_session, source)
    tagged = _count_docs_with_tag_key(db_session, source, filter_field)
    values = [row[0] for row in groups]
    return {
        "field": filter_field,
        "source": source.value if source else "all",
        "values": values,
        "untagged_count": max(0, total - tagged),
        "total_indexed": total,
        "truncated": truncated,
        "note": "Exact distinct indexed tag values, not a search sample.",
    }


def breakdown_by_tag(
    db_session: Session,
    *,
    source: DocumentSource | None,
    filter_field: str,
    date_bucket: str | None = None,
) -> dict[str, object]:
    """Exact unique-document counts grouped by one allow-listed tag key.

    A document with several list-tag values (labels) can appear in more than
    one group. untagged_count is indexed documents with no tag for this field.

    Args:
        db_session: Tenant DB session.
        source: Restrict to this DocumentSource, or all sources.
        filter_field: Allow-listed tag key.
        date_bucket: When "month", group date tags by YYYY-MM.

    Returns:
        groups [{value, count}], untagged_count, total_indexed, truncated.
    """
    bucket = parse_date_bucket(date_bucket)
    if bucket and filter_field not in DATE_TAG_KEYS:
        raise DocumentCountError("date_bucket requires a date field")
    rows, truncated = _group_counts(db_session, source, filter_field, date_bucket=bucket)
    total = _count_indexed_by_source(db_session, source)
    tagged_docs = _count_docs_with_tag_key(db_session, source, filter_field)
    groups = [{"value": value, "count": count} for value, count in rows]
    return {
        "field": filter_field,
        "source": source.value if source else "all",
        "groups": groups,
        "untagged_count": max(0, total - tagged_docs),
        "total_indexed": total,
        "truncated": truncated,
        "note": "Exact unique indexed document counts grouped by field, not a search sample.",
    }


def list_documents_matching_filter(
    db_session: Session,
    *,
    source: DocumentSource | None,
    filters: list[tuple[str, str]],
    date_ranges: list[DateRangeSpec] | None = None,
    sort_by: str | None = None,
) -> dict[str, object]:
    """Exact indexed documents matching AND tag filters, with ticket keys.

    Same match rules as document-count. Results are capped; count is the full
    unique-document total so callers can say first 50 of N. List rows include
    assignee/status/created/updated/duedate so follow-ups do not need one-by-one
    lookups for those fields.

    Args:
        db_session: Tenant DB session.
        source: Restrict to this DocumentSource, or all sources.
        filters: Allow-listed field/value pairs combined with AND.
        date_ranges: Optional governed date comparisons.
        sort_by: key_asc (default) or created/updated asc/desc.

    Returns:
        count, documents[{key, title, link, …projection}], filters, truncated, cap.

    Raises:
        DocumentCountError: No named source, tag filter, or date range.
    """
    ranges = date_ranges or []
    # source=all (None) with no tag/date would dump every connector; a named source is enough.
    if not filters and not ranges and source is None:
        raise DocumentCountError("At least one filter is required")
    resolved = _resolve_filters(db_session, source, filters) if filters else []
    if filters and any(not item["matched_values"] for item in resolved):
        return _matching_list_payload([], 0, source, resolved, truncated=False)
    if filters:
        tag_ids = set(
            matching_document_ids(
                db_session,
                source,
                [(str(item["filter_field"]), list(item["matched_values"])) for item in resolved],
            )
        )
    elif ranges:
        tag_ids = None
    else:
        if source is None:
            raise DocumentCountError("At least one filter is required")
        tag_ids = _indexed_document_ids_for_source(db_session, source)
    range_ids = intersect_date_range_ids(db_session, source, ranges)
    doc_ids = _combine_id_sets(tag_ids, range_ids)
    ordered = _sort_document_ids(db_session, list(doc_ids), parse_sort_by(sort_by))
    true_count = len(ordered)
    truncated = true_count > MAX_CATALOG_ROWS
    documents = _documents_with_keys(db_session, ordered[:MAX_CATALOG_ROWS])
    return _matching_list_payload(
        documents, true_count, source, resolved, truncated=truncated
    )


def lookup_document_by_key(
    db_session: Session,
    *,
    source: DocumentSource | None,
    key: str,
) -> dict[str, object]:
    """Exact indexed-document lookup by ticket/document key (e.g. RD-82).

    Matches the `key` tag case-insensitively, then semantic_id prefix `KEY:` / `KEY `.

    Args:
        db_session: Tenant DB session.
        source: Restrict to this DocumentSource, or all sources.
        key: Exact key string already validated by parse_document_key.

    Returns:
        found=False when missing; otherwise title, link, source, allow-listed fields.
    """
    doc = _find_by_key_tag(db_session, source, key) or _find_by_semantic_prefix(
        db_session, source, key
    )
    if doc is None:
        return {
            "found": False,
            "key": key,
            "source": source.value if source else "all",
            "note": "No indexed document with this exact key.",
        }
    return {
        "found": True,
        "key": key,
        "title": doc.semantic_id,
        "link": doc.link,
        "source": source.value if source else "all",
        "fields": _allowlisted_fields(db_session, doc.id),
        "note": "Exact indexed document lookup by key, not a search ranking.",
    }


def list_queryable_fields(
    source: DocumentSource | None = None,
    db_session: Session | None = None,
) -> dict[str, object]:
    """Published allow-list for Ask. Slack uses declared schema + selection."""
    from onyx.db.document_count import queryable_fields_for_source

    return queryable_fields_for_source(source, db_session)


def catalog_document_row(
    *,
    key: str | None,
    semantic_id: str | None,
    link: str | None,
    extras: dict[str, str | None] | None = None,
) -> dict[str, str | None]:
    """One list-result document. Key tag wins; semantic_id prefix is the fallback."""
    resolved = (key or "").strip() or ticket_key_from_semantic_id(semantic_id)
    row: dict[str, str | None] = {
        "key": resolved,
        "title": (semantic_id or "").strip() or resolved,
        "link": link,
    }
    if extras:
        row.update(extras)
    return row


def _matching_list_payload(
    documents: list[dict[str, str | None]],
    count: int,
    source: DocumentSource | None,
    resolved: list[dict[str, object]],
    *,
    truncated: bool,
) -> dict[str, object]:
    first = resolved[0] if resolved else None
    return {
        "count": count,
        "returned": len(documents),
        "cap": MAX_CATALOG_ROWS,
        "source": source.value if source else "all",
        "filters": resolved,
        "filter_field": first["filter_field"] if first else None,
        "filter_value": first["filter_value"] if first else None,
        "matched_values": first["matched_values"] if first else [],
        "documents": documents,
        "truncated": truncated,
        "note": (
            f"Showing first {len(documents)} of {count} matching indexed documents."
            if truncated
            else "Exact indexed documents matching this filter, not a search sample."
        ),
    }


def _indexed_document_ids_for_source(
    db_session: Session, source: DocumentSource
) -> set[str]:
    """Indexed document ids for one connector source (same membership as counts)."""
    stmt = (
        select(DocumentByConnectorCredentialPair.id)
        .select_from(DocumentByConnectorCredentialPair)
        .join(Connector, Connector.id == DocumentByConnectorCredentialPair.connector_id)
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
        .where(Connector.source == source)
    )
    return {str(doc_id) for doc_id in db_session.execute(stmt).scalars().all()}


def _combine_id_sets(
    tag_ids: set[str] | None, range_ids: set[str] | None
) -> set[str]:
    if tag_ids is None:
        return range_ids or set()
    if range_ids is None:
        return tag_ids
    return tag_ids & range_ids


def _sort_document_ids(
    db_session: Session, doc_ids: list[str], sort_by: str
) -> list[str]:
    if not doc_ids:
        return []
    if sort_by == "key_asc":
        return sorted(doc_ids)
    docs = {
        str(row.id): row
        for row in db_session.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()
    }
    attr = "doc_created_at" if sort_by.startswith("created") else "doc_updated_at"
    reverse = sort_by.endswith("_desc")
    present: list[tuple[str, object]] = []
    missing: list[str] = []
    for doc_id in doc_ids:
        value = getattr(docs.get(doc_id), attr, None)
        if value is None:
            missing.append(doc_id)
        else:
            present.append((doc_id, value))
    present.sort(key=lambda item: item[1], reverse=reverse)
    return [doc_id for doc_id, _ in present] + missing


def _documents_with_keys(
    db_session: Session, doc_ids: list[str]
) -> list[dict[str, str | None]]:
    if not doc_ids:
        return []
    docs = {
        str(row.id): row
        for row in db_session.execute(select(Document).where(Document.id.in_(doc_ids))).scalars()
    }
    projection = _list_projection(db_session, doc_ids)
    sources = _document_sources(db_session, doc_ids)
    return [
        catalog_document_row(
            key=projection.get(doc_id, {}).get("key"),
            semantic_id=docs[doc_id].semantic_id,
            link=docs[doc_id].link,
            extras={
                **_projection_extras(projection.get(doc_id, {})),
                "source": sources.get(doc_id),
            },
        )
        for doc_id in doc_ids
        if doc_id in docs
    ]


def _list_projection(
    db_session: Session, doc_ids: list[str]
) -> dict[str, dict[str, str]]:
    keys = (KEY_TAG, *LIST_PROJECTION_KEYS)
    rows = db_session.execute(
        select(Document__Tag.document_id, Tag.tag_key, Tag.tag_value)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .where(Document__Tag.document_id.in_(doc_ids))
        .where(Tag.tag_key.in_(keys))
    ).all()
    out: dict[str, dict[str, str]] = {}
    for doc_id, tag_key, tag_value in rows:
        out.setdefault(str(doc_id), {})[str(tag_key)] = str(tag_value)
    return out


def _projection_extras(fields: dict[str, str]) -> dict[str, str | None]:
    """Always emit projection keys so an unassigned ticket is explicit null."""
    return {key: fields.get(key) for key in LIST_PROJECTION_KEYS}


def _document_sources(db_session: Session, doc_ids: list[str]) -> dict[str, str]:
    """Connector source id per document. Omits a doc when paired sources disagree."""
    if not doc_ids:
        return {}
    rows = db_session.execute(
        select(DocumentByConnectorCredentialPair.id, Connector.source)
        .join(Connector, Connector.id == DocumentByConnectorCredentialPair.connector_id)
        .where(DocumentByConnectorCredentialPair.id.in_(doc_ids))
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
    ).all()
    out: dict[str, str] = {}
    conflicted: set[str] = set()
    for doc_id, source in rows:
        key = str(doc_id)
        if source is None:
            continue
        value = source.value if isinstance(source, DocumentSource) else str(source).strip().lower()
        if not value:
            continue
        prior = out.get(key)
        if prior is None:
            out[key] = value
        elif prior != value:
            conflicted.add(key)
    for key in conflicted:
        out.pop(key, None)
    return out


def _group_counts(
    db_session: Session,
    source: DocumentSource | None,
    filter_field: str,
    date_bucket: str | None = None,
) -> tuple[list[tuple[str, int]], bool]:
    value_expr = (
        func.substr(Tag.tag_value, 1, 7) if date_bucket == "month" else Tag.tag_value
    )
    stmt = (
        select(value_expr, func.count(distinct(Document__Tag.document_id)))
        .select_from(Document__Tag)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .join(
            DocumentByConnectorCredentialPair,
            DocumentByConnectorCredentialPair.id == Document__Tag.document_id,
        )
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
        .where(tag_key_is(filter_field))
        .group_by(value_expr)
        .order_by(func.count(distinct(Document__Tag.document_id)).desc(), value_expr)
        .limit(MAX_CATALOG_ROWS + 1)
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    rows = [(str(value), int(count)) for value, count in db_session.execute(stmt).all()]
    truncated = len(rows) > MAX_CATALOG_ROWS
    return rows[:MAX_CATALOG_ROWS], truncated


def _count_docs_with_tag_key(
    db_session: Session,
    source: DocumentSource | None,
    filter_field: str,
) -> int:
    stmt = (
        select(func.count(distinct(Document__Tag.document_id)))
        .select_from(Document__Tag)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .join(
            DocumentByConnectorCredentialPair,
            DocumentByConnectorCredentialPair.id == Document__Tag.document_id,
        )
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
        .where(tag_key_is(filter_field))
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    return int(db_session.execute(stmt).scalar_one())


def _indexed_base(source: DocumentSource | None):
    stmt = (
        select(Document)
        .join(
            DocumentByConnectorCredentialPair,
            DocumentByConnectorCredentialPair.id == Document.id,
        )
        .join(Connector, Connector.id == DocumentByConnectorCredentialPair.connector_id)
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
    )
    if source is not None:
        stmt = stmt.where(Connector.source == source)
    return stmt


def _find_by_key_tag(
    db_session: Session, source: DocumentSource | None, key: str
) -> Document | None:
    stmt = (
        _indexed_base(source)
        .join(Document__Tag, Document__Tag.document_id == Document.id)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .where(Tag.tag_key == KEY_TAG)
        .where(func.lower(Tag.tag_value) == key.lower())
        .limit(1)
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    return db_session.execute(stmt).scalars().first()


def _find_by_semantic_prefix(
    db_session: Session, source: DocumentSource | None, key: str
) -> Document | None:
    escaped = escape_ilike_pattern(key)
    stmt = _indexed_base(source).where(
        or_(
            Document.semantic_id.ilike(f"{escaped}:%", escape="\\"),
            Document.semantic_id.ilike(f"{escaped} %", escape="\\"),
        )
    ).limit(1)
    return db_session.execute(stmt).scalars().first()


def _allowlisted_fields(db_session: Session, document_id: str) -> dict[str, str | list[str]]:
    stmt = (
        select(Tag.tag_key, Tag.tag_value, Tag.is_list)
        .select_from(Document__Tag)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .where(Document__Tag.document_id == document_id)
        .where(Tag.tag_key.in_(BY_KEY_TAG_KEYS))
        .order_by(Tag.tag_key, Tag.tag_value)
    )
    fields: dict[str, str | list[str]] = {}
    for tag_key, tag_value, is_list in db_session.execute(stmt):
        if tag_key in PII_TAG_KEYS or tag_key not in BY_KEY_TAG_KEYS:
            continue
        _append_field(fields, tag_key, tag_value, bool(is_list))
    return fields


def _append_field(
    fields: dict[str, str | list[str]], tag_key: str, tag_value: str, is_list: bool
) -> None:
    if not is_list and tag_key != "labels" and tag_key != "custom_fields":
        fields[tag_key] = tag_value
        return
    current = fields.get(tag_key)
    if isinstance(current, list):
        current.append(tag_value)
    elif current is None:
        fields[tag_key] = [tag_value]
    else:
        fields[tag_key] = [current, tag_value]
