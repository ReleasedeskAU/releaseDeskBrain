"""Per-source declared tag schemas.

Unlisted sources still publish ALLOWED_TAG_KEYS. Categories are UI grouping
only — not a shared meaning across connectors.
"""

from __future__ import annotations

from onyx.configs.constants import DocumentSource
from onyx.connectors.field_schema import FieldDecl
from onyx.connectors.slack.fields import FIELD_SCHEMA as SLACK_FIELD_SCHEMA
from onyx.connectors.teams.fields import FIELD_SCHEMA as TEAMS_FIELD_SCHEMA

FIELD_SCHEMAS: dict[DocumentSource, tuple[FieldDecl, ...]] = {
    DocumentSource.SLACK: SLACK_FIELD_SCHEMA,
    DocumentSource.TEAMS: TEAMS_FIELD_SCHEMA,
}


def schema_for_source(source: DocumentSource | None) -> tuple[FieldDecl, ...] | None:
    """Declared schema for a migrated source, or None for the ALLOWED remainder.

    Args:
        source: Connector source, or None for the unscoped catalog.

    Returns:
        FieldDecl tuple, or None when this source still uses ALLOWED_TAG_KEYS.
    """
    if source is None:
        return None
    return FIELD_SCHEMAS.get(source)
