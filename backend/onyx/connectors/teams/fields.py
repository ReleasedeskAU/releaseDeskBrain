"""Declared optional Teams tags. Channel and author only — never email."""

from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    validate_field_schema,
)

# Optional Teams tags only. Identity (id/title/link) and message text are
# always-on Document fields, not this list. Import fails if a PII key is added.
FIELD_SCHEMA = validate_field_schema(
    (
        FieldDecl(
            "channel",
            FieldCategory.RELATIONSHIPS,
            "Channel",
            match="exact",
        ),
        FieldDecl(
            "author",
            FieldCategory.OWNERSHIP,
            "Author",
            match="contains",
        ),
    )
)
