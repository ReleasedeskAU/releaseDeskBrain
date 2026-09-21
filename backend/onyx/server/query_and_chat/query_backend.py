from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from onyx.auth.permissions import require_permission
from onyx.configs.chat_configs import NUM_RETURNED_HITS
from onyx.configs.constants import DocumentSource
from onyx.context.search.enums import QueryType
from onyx.context.search.models import IndexFilters, InferenceChunk, SearchDoc
from onyx.context.search.preprocessing.access_filters import (
    build_access_filters_for_user,
)
from onyx.context.search.utils import get_query_embedding
from onyx.db.document_catalog import (
    breakdown_by_tag,
    list_distinct_tag_values,
    list_documents_matching_filter,
    list_queryable_fields,
    lookup_document_by_key,
)
from onyx.db.document_count import (
    DocumentCountError,
    count_indexed_documents,
    parse_catalog_filters,
    parse_count_source,
    parse_document_key,
    require_source_filter_field,
)
from onyx.db.document_date_filter import parse_date_range_args
from onyx.db.engine.sql_engine import get_session
from onyx.db.enums import Permission
from onyx.db.models import User
from onyx.db.search_settings import get_current_search_settings
from onyx.db.tag import find_tags
from onyx.document_index.factory import get_default_document_index
from onyx.document_index.interfaces_new import DocumentIndex
from onyx.server.query_and_chat.document_content import (
    DocumentContentError,
    DocumentContentRequest,
    build_document_content_response,
    parse_content_document_id,
    retrieve_document_content_chunks,
)
from onyx.server.query_and_chat.models import (
    AdminSearchRequest,
    AdminSearchResponse,
    SourceTag,
    TagResponse,
)
from onyx.server.query_and_chat.recency_bias import (
    maybe_apply_recency_bias,
    should_apply_recency_bias,
)
from onyx.server.query_and_chat.tei_rerank import (
    maybe_rerank_hybrid_documents,
    should_rerank_admin_search,
)
from onyx.server.utils_vector_db import require_vector_db
from onyx.utils.logger import setup_logger
from shared_configs.contextvars import get_current_tenant_id

logger = setup_logger()

admin_router = APIRouter(prefix="/admin")
basic_router = APIRouter(prefix="/query")


class CatalogFilter(BaseModel):
    """One AND-filter clause. Extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")
    filter_field: str = Field(max_length=40)
    filter_value: str = Field(max_length=80)


class DateRangeMixin(BaseModel):
    """YYYY-MM-DD range params. Extra fields stay forbidden on the child model."""

    created_from: str | None = Field(default=None, max_length=10)
    created_to: str | None = Field(default=None, max_length=10)
    resolved_from: str | None = Field(default=None, max_length=10)
    resolved_to: str | None = Field(default=None, max_length=10)
    updated_from: str | None = Field(default=None, max_length=10)
    updated_to: str | None = Field(default=None, max_length=10)
    due_from: str | None = Field(default=None, max_length=10)
    due_to: str | None = Field(default=None, max_length=10)
    due_before: str | None = Field(default=None, max_length=10)


class DocumentCountRequest(DateRangeMixin):
    """Exact unique-document count. Extra fields are rejected."""

    model_config = ConfigDict(extra="forbid")
    source: str | None = Field(default=None, max_length=40)
    filter_field: str | None = Field(default=None, max_length=40)
    filter_value: str | None = Field(default=None, max_length=80)
    filters: list[CatalogFilter] | None = Field(default=None, max_length=5)


def _date_ranges(body: DateRangeMixin):
    """Parse optional YYYY-MM-DD range fields from a count or list request."""
    return parse_date_range_args(
        created_from=body.created_from,
        created_to=body.created_to,
        resolved_from=body.resolved_from,
        resolved_to=body.resolved_to,
        updated_from=body.updated_from,
        updated_to=body.updated_to,
        due_from=body.due_from,
        due_to=body.due_to,
        due_before=body.due_before,
    )


def _parsed_filters(
    filter_field: str | None,
    filter_value: str | None,
    filters: list[CatalogFilter] | None,
) -> list[tuple[str, str]]:
    raw = [(item.filter_field, item.filter_value) for item in filters] if filters else None
    return parse_catalog_filters(filter_field, filter_value, raw)


@admin_router.post("/document-count")
def document_count(
    body: DocumentCountRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exact unique indexed document count. Not a search-hit sample.

    Uses Postgres connector membership and document tags (assignee, status, …).
    Names/labels use contains; key/parent/status/dates are exact.
    """
    try:
        return count_indexed_documents(
            db_session,
            source=parse_count_source(body.source),
            filters=_parsed_filters(body.filter_field, body.filter_value, body.filters),
            date_ranges=_date_ranges(body),
        )
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid count request") from None


class DocumentFieldRequest(BaseModel):
    """Distinct values or group-by for one metadata field. Extra fields rejected."""

    model_config = ConfigDict(extra="forbid")
    source: str | None = Field(default=None, max_length=40)
    field: str = Field(max_length=40)
    date_bucket: str | None = Field(default=None, max_length=16)


class DocumentByKeyRequest(BaseModel):
    """Exact document lookup by ticket/document key. Extra fields rejected."""

    model_config = ConfigDict(extra="forbid")
    source: str | None = Field(default=None, max_length=40)
    key: str = Field(max_length=40)


class DocumentMatchRequest(DateRangeMixin):
    """List indexed documents matching AND metadata filters. Extra fields rejected."""

    model_config = ConfigDict(extra="forbid")
    source: str | None = Field(default=None, max_length=40)
    filter_field: str | None = Field(default=None, max_length=40)
    filter_value: str | None = Field(default=None, max_length=80)
    filters: list[CatalogFilter] | None = Field(default=None, max_length=5)
    sort_by: str | None = Field(default=None, max_length=20)


class DocumentFieldsRequest(BaseModel):
    """Published queryable fields. Extra fields rejected."""

    model_config = ConfigDict(extra="forbid")
    source: str | None = Field(default=None, max_length=40)


@admin_router.post("/document-fields")
def document_fields(
    body: DocumentFieldsRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Published allow-list of queryable tag fields. Not raw tag discovery."""
    try:
        return list_queryable_fields(parse_count_source(body.source), db_session)
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None


@admin_router.post("/document-distinct")
def document_distinct(
    body: DocumentFieldRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exact distinct tag values for one field on indexed documents."""
    try:
        return list_distinct_tag_values(
            db_session,
            source=parse_count_source(body.source),
            filter_field=require_source_filter_field(
                body.field, parse_count_source(body.source), db_session
            ),
        )
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None


@admin_router.post("/document-breakdown")
def document_breakdown(
    body: DocumentFieldRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exact unique-document counts grouped by one metadata field."""
    try:
        return breakdown_by_tag(
            db_session,
            source=parse_count_source(body.source),
            filter_field=require_source_filter_field(
                body.field, parse_count_source(body.source), db_session
            ),
            date_bucket=body.date_bucket,
        )
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None


@admin_router.post("/document-by-key")
def document_by_key(
    body: DocumentByKeyRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exact indexed document lookup by ticket key (not ranked search)."""
    try:
        return lookup_document_by_key(
            db_session,
            source=parse_count_source(body.source),
            key=parse_document_key(body.key),
        )
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None


@admin_router.post("/document-list")
def document_list(
    body: DocumentMatchRequest,
    _: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Exact indexed documents matching AND tag filters, including ticket keys.

    Same match rules as document-count. Not a ranked search sample.
    """
    try:
        filters = _parsed_filters(body.filter_field, body.filter_value, body.filters)
        return list_documents_matching_filter(
            db_session,
            source=parse_count_source(body.source),
            filters=filters,
            date_ranges=_date_ranges(body),
            sort_by=body.sort_by,
        )
    except DocumentCountError:
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None


@admin_router.post("/document-content", dependencies=[Depends(require_vector_db)])
def document_content(
    body: DocumentContentRequest,
    user: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> dict[str, object]:
    """Indexed document body for one document_id. ACL miss is found=false.

    Uses OpenSearch id_based_retrieval with the same tenant and ACL filters as
    admin search. Does not log body text.
    """
    try:
        document_id = parse_content_document_id(body.document_id)
        source = parse_count_source(body.source)
    except (DocumentCountError, DocumentContentError):
        raise HTTPException(status_code=400, detail="Invalid catalog request") from None

    tenant_id = get_current_tenant_id()
    user_acl_filters = build_access_filters_for_user(user, db_session)
    final_filters = IndexFilters(
        source_type=[source] if source is not None else None,
        document_set=None,
        created_at_range=None,
        updated_at_range=None,
        tags=None,
        access_control_list=user_acl_filters,
        tenant_id=tenant_id,
    )
    search_settings = get_current_search_settings(db_session)
    document_index = get_default_document_index(search_settings, None, db_session)
    chunks = retrieve_document_content_chunks(
        document_id=document_id,
        document_index=document_index,
        filters=final_filters,
    )
    return build_document_content_response(
        document_id=document_id,
        source=source.value if source else "all",
        chunks=chunks,
    )


def retrieve_admin_search_chunks(
    *,
    query: str,
    retrieval: str,
    document_index: DocumentIndex,
    filters: IndexFilters,
    db_session: Session,
) -> list[InferenceChunk]:
    """Pick random / keyword / hybrid retrieval for admin search.

    Empty query always uses random_retrieval so Connectors' blank list stays
    unchanged. Hybrid embeds with the current SearchSettings model.
    """
    if not query or query.strip() == "":
        return document_index.random_retrieval(filters=filters)
    if retrieval == "hybrid":
        query_embedding = get_query_embedding(query, db_session=db_session)
        return document_index.hybrid_retrieval(
            query=query,
            query_embedding=query_embedding,
            final_keywords=None,
            # OpenSearch hybrid ignores query_type; SEMANTIC matches chat default.
            query_type=QueryType.SEMANTIC,
            filters=filters,
            num_to_retrieve=NUM_RETURNED_HITS,
            include_hidden=True,
        )
    return document_index.keyword_retrieval(
        query=query,
        filters=filters,
        num_to_retrieve=NUM_RETURNED_HITS,
        include_hidden=True,
    )


@admin_router.post("/search", dependencies=[Depends(require_vector_db)])
def admin_search(
    question: AdminSearchRequest,
    user: User = Depends(require_permission(Permission.FULL_ADMIN_PANEL_ACCESS)),
    db_session: Session = Depends(get_session),
) -> AdminSearchResponse:
    tenant_id = get_current_tenant_id()

    query = question.query
    logger.notice("Received admin search query: %s", query)
    user_acl_filters = build_access_filters_for_user(user, db_session)

    final_filters = IndexFilters(
        source_type=question.filters.source_type,
        document_set=question.filters.document_set,
        created_at_range=question.filters.created_at_range,
        updated_at_range=question.filters.updated_at_range,
        tags=question.filters.tags,
        access_control_list=user_acl_filters,
        tenant_id=tenant_id,
    )
    search_settings = get_current_search_settings(db_session)
    # This flow is for search so we do not get all indices.
    document_index = get_default_document_index(search_settings, None, db_session)
    matching_chunks = retrieve_admin_search_chunks(
        query=query,
        retrieval=question.retrieval,
        document_index=document_index,
        filters=final_filters,
        db_session=db_session,
    )

    documents = SearchDoc.from_chunks_or_sections(matching_chunks)

    # Deduplicate documents by id
    deduplicated_documents: list[SearchDoc] = []
    seen_documents: set[str] = set()
    for document in documents:
        if document.document_id not in seen_documents:
            deduplicated_documents.append(document)
            seen_documents.add(document.document_id)
    # Unique docs, not raw chunks — one Slack thread would otherwise fill the TEI batch.
    # Recency re-sorts fused hybrid hits; TEI (when on) still runs after and can override.
    if should_apply_recency_bias(question.retrieval, query):
        deduplicated_documents = maybe_apply_recency_bias(deduplicated_documents)
    if should_rerank_admin_search(question.retrieval, query):
        deduplicated_documents = maybe_rerank_hybrid_documents(
            query, deduplicated_documents
        )
    return AdminSearchResponse(documents=deduplicated_documents)


@basic_router.get("/valid-tags")
def get_tags(
    match_pattern: str | None = None,
    # If this is empty or None, then tags for all sources are considered
    sources: list[DocumentSource] | None = None,
    allow_prefix: bool = True,  # This is currently the only option
    limit: int = 50,
    _: User = Depends(require_permission(Permission.READ_SEARCH)),
    db_session: Session = Depends(get_session),
) -> TagResponse:
    if not allow_prefix:
        raise NotImplementedError("Cannot disable prefix match for now")

    key_prefix = match_pattern
    value_prefix = match_pattern
    require_both_to_match = False

    # split on = to allow the user to type in "author=bob"
    EQUAL_PAT = "="
    if match_pattern and EQUAL_PAT in match_pattern:
        split_pattern = match_pattern.split(EQUAL_PAT)
        key_prefix = split_pattern[0]
        value_prefix = EQUAL_PAT.join(split_pattern[1:])
        require_both_to_match = True

    db_tags = find_tags(
        tag_key_prefix=key_prefix,
        tag_value_prefix=value_prefix,
        sources=sources,
        limit=limit,
        db_session=db_session,
        require_both_to_match=require_both_to_match,
    )
    server_tags = [
        SourceTag(
            tag_key=db_tag.tag_key, tag_value=db_tag.tag_value, source=db_tag.source
        )
        for db_tag in db_tags
    ]
    return TagResponse(tags=server_tags)
