"""Governed date-range matching on stored tag values — not model-authored SQL.

Date tags are stored as Jira strings (ISO date or datetime). Comparisons use the
YYYY-MM-DD prefix. Unknown or unparseable values are excluded, never guessed.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import NoReturn

from sqlalchemy import select
from sqlalchemy.orm import Session

from onyx.configs.constants import DocumentSource
from onyx.db.models import DocumentByConnectorCredentialPair, Document__Tag, Tag

DATE_TAG_KEYS = frozenset({"created", "updated", "duedate", "resolution_date"})
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
DATE_PREFIX_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# Jira statusCategory.key values. Display names are localized — never used as the rule.
STATUS_CATEGORY_VALUES = ("new", "indeterminate", "done")
RESOLVED_STATUS_CATEGORY = "done"

SORT_BY_VALUES = (
    "key_asc",
    "created_asc",
    "created_desc",
    "updated_asc",
    "updated_desc",
)
DATE_BUCKET_VALUES = ("month",)


@dataclass(frozen=True)
class DateRangeSpec:
    """Inclusive start, optional inclusive end, optional exclusive end (due_before)."""

    field: str
    start: str | None
    end_inclusive: str | None
    end_exclusive: str | None


def parse_iso_date(value: str | None, *, name: str) -> str | None:
    """Require YYYY-MM-DD. Empty becomes None.

    Raises:
        DocumentCountError: Malformed or impossible calendar date.
    """
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    match = ISO_DATE_RE.fullmatch(trimmed)
    if not match:
        _reject(f"Invalid {name} date")
    year, month, day = (int(part) for part in match.groups())
    try:
        dt.date(year, month, day)
    except ValueError:
        _reject(f"Invalid {name} date")
    return trimmed


def date_prefix(stored: str) -> str | None:
    """YYYY-MM-DD prefix of a stored tag, or None when the value is not a date."""
    match = DATE_PREFIX_RE.match(stored.strip())
    return match.group(0) if match else None


def stored_date_in_range(stored: str, spec: DateRangeSpec) -> bool:
    """True when the stored tag's date prefix satisfies the range bounds."""
    prefix = date_prefix(stored)
    if prefix is None:
        return False
    if spec.start and prefix < spec.start:
        return False
    if spec.end_inclusive and prefix > spec.end_inclusive:
        return False
    if spec.end_exclusive and prefix >= spec.end_exclusive:
        return False
    return True


def parse_date_range_args(
    *,
    created_from: str | None = None,
    created_to: str | None = None,
    resolved_from: str | None = None,
    resolved_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    due_from: str | None = None,
    due_to: str | None = None,
    due_before: str | None = None,
) -> list[DateRangeSpec]:
    """Build range specs from request fields. Extra/unknown keys are not accepted here."""
    specs = [
        _one_range("created", created_from, created_to, None, "created"),
        _one_range("resolution_date", resolved_from, resolved_to, None, "resolved"),
        _one_range("updated", updated_from, updated_to, None, "updated"),
        _one_range("duedate", due_from, due_to, due_before, "due"),
    ]
    return [spec for spec in specs if spec is not None]


def parse_sort_by(sort_by: str | None) -> str:
    """Allow-listed list sort. Default is key_asc.

    Raises:
        DocumentCountError: Unknown sort token.
    """
    if sort_by is None or sort_by.strip() == "":
        return "key_asc"
    value = sort_by.strip().lower()
    if value not in SORT_BY_VALUES:
        _reject("Unknown sort_by")
    return value


def parse_date_bucket(date_bucket: str | None) -> str | None:
    """Optional month bucket for date-field breakdowns.

    Raises:
        DocumentCountError: Unknown bucket token.
    """
    if date_bucket is None or date_bucket.strip() == "":
        return None
    value = date_bucket.strip().lower()
    if value not in DATE_BUCKET_VALUES:
        _reject("Unknown date_bucket")
    return value


def matching_date_tag_values(
    db_session: Session,
    source: DocumentSource | None,
    spec: DateRangeSpec,
) -> list[str]:
    """All stored tag values for spec.field whose date prefix is in range."""
    stmt = select(Tag.tag_value).where(Tag.tag_key == spec.field).distinct()
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    values = [str(value) for value in db_session.execute(stmt).scalars().all()]
    return [value for value in values if stored_date_in_range(value, spec)]


def indexed_doc_ids_for_date_range(
    db_session: Session,
    source: DocumentSource | None,
    spec: DateRangeSpec,
) -> set[str]:
    """Indexed document ids whose date tag falls in spec. Empty values → empty set."""
    values = matching_date_tag_values(db_session, source, spec)
    if not values:
        return set()
    stmt = (
        select(Document__Tag.document_id)
        .select_from(Document__Tag)
        .join(Tag, Tag.id == Document__Tag.tag_id)
        .join(
            DocumentByConnectorCredentialPair,
            DocumentByConnectorCredentialPair.id == Document__Tag.document_id,
        )
        .where(DocumentByConnectorCredentialPair.has_been_indexed.is_(True))
        .where(Tag.tag_key == spec.field)
        .where(Tag.tag_value.in_(values))
        .distinct()
    )
    if source is not None:
        stmt = stmt.where(Tag.source == source)
    return {str(doc_id) for doc_id in db_session.execute(stmt).scalars().all()}


def intersect_date_range_ids(
    db_session: Session,
    source: DocumentSource | None,
    specs: list[DateRangeSpec],
) -> set[str] | None:
    """AND-intersect date ranges. None means no date filter was supplied."""
    if not specs:
        return None
    ids: set[str] | None = None
    for spec in specs:
        part = indexed_doc_ids_for_date_range(db_session, source, spec)
        ids = part if ids is None else ids & part
        if not ids:
            return set()
    return ids or set()


def _reject(message: str) -> NoReturn:
    """Lazy import avoids a load-time cycle with document_count."""
    from onyx.db.document_count import DocumentCountError

    raise DocumentCountError(message)


def _one_range(
    field: str,
    start_raw: str | None,
    end_raw: str | None,
    before_raw: str | None,
    name: str,
) -> DateRangeSpec | None:
    start = parse_iso_date(start_raw, name=f"{name}_from")
    end_inclusive = parse_iso_date(end_raw, name=f"{name}_to")
    end_exclusive = parse_iso_date(before_raw, name=f"{name}_before")
    if start is None and end_inclusive is None and end_exclusive is None:
        return None
    if start and end_inclusive and start > end_inclusive:
        _reject(f"Invalid {name} range")
    if start and end_exclusive and start >= end_exclusive:
        _reject(f"Invalid {name} range")
    return DateRangeSpec(
        field=field,
        start=start,
        end_inclusive=end_inclusive,
        end_exclusive=end_exclusive,
    )
