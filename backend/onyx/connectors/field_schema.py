"""Declared indexed-tag schema for connectors.

Identity (id, title, link) and Content (body text) are always-on Document
fields — not customer checkboxes. Optional tags are declared per connector.

PII_TAG_KEYS is platform-owned. A connector schema that lists a blocked key
fails at load, not only at query time. Selection cannot add a blocked key.

Unchecking a tag hides it from Ask going forward. It does not purge stored
document__tag rows and does not force a re-index. content_hash() hashes
doc_metadata, not Document.metadata, so a normal sync will not rewrite tags.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

# Platform deny-list. Connectors cannot declare or select these.
PII_TAG_KEYS = frozenset({"assignee_email", "reporter_email", "sender_email"})


class FieldCategory(str, Enum):
    """UI grouping only — not a status ontology and not vendor value mapping."""

    IDENTITY = "identity"
    STATUS = "status"
    OWNERSHIP = "ownership"
    TIMING = "timing"
    RELATIONSHIPS = "relationships"
    RISK = "risk"
    CONTENT = "content"


class FieldSchemaError(ValueError):
    """A connector declared a blocked or invalid tag key."""


@dataclass(frozen=True)
class FieldDecl:
    """One optional indexed tag. Not id/title/link/body."""

    key: str
    category: FieldCategory
    label: str
    match: str = "exact"
    default_selected: bool = True


def declared_keys(schema: tuple[FieldDecl, ...]) -> frozenset[str]:
    """Lowercase keys from a connector schema."""
    return frozenset(item.key.strip().lower() for item in schema)


def validate_field_schema(schema: tuple[FieldDecl, ...]) -> tuple[FieldDecl, ...]:
    """Fail loudly if a schema lists PII or an empty/duplicate key.

    Raises:
        FieldSchemaError: Blocked, empty, or duplicate keys.
    """
    seen: set[str] = set()
    for item in schema:
        key = item.key.strip().lower()
        if not key or key != item.key:
            raise FieldSchemaError("Field keys must be lowercase and non-empty")
        if key in PII_TAG_KEYS:
            raise FieldSchemaError("PII tag keys must not be declared")
        if key in seen:
            raise FieldSchemaError("Duplicate field key")
        if item.match not in {"exact", "contains", "date", "display"}:
            raise FieldSchemaError("Unknown match mode")
        seen.add(key)
    return schema


def default_selected_keys(schema: tuple[FieldDecl, ...]) -> frozenset[str]:
    """Keys that are selected when the instance selection is unset."""
    return frozenset(item.key for item in schema if item.default_selected)


def contains_match_keys(schema: tuple[FieldDecl, ...]) -> frozenset[str]:
    """Keys this schema publishes as contains-match, not exact."""
    return frozenset(item.key for item in schema if item.match == "contains")


def sanitize_indexed_field_selection(
    raw: object,
    declared: frozenset[str],
) -> list[str] | None:
    """Normalize a stored selection. None stays unset. [] stays empty.

    Blocked and unknown keys are dropped. Selection cannot override the
    platform PII blocklist.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        key = item.strip().lower()
        if not key or key in PII_TAG_KEYS or key not in declared or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def effective_selection(
    stored: list[str] | None,
    schema: tuple[FieldDecl, ...],
) -> frozenset[str]:
    """Unset inherits defaults. Empty list means no optional tags."""
    declared = declared_keys(schema)
    if stored is None:
        return default_selected_keys(schema) & declared
    cleaned = sanitize_indexed_field_selection(stored, declared)
    return frozenset(cleaned or [])
