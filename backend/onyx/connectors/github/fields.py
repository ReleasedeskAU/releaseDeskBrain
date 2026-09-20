"""Declared optional GitHub tags. Never people dicts that can hold email."""

from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    validate_field_schema,
)

# Optional GitHub tags only. Identity (id/title/link) and body text are
# always-on Document fields, not this list. Import fails if a PII key is
# added. All default_selected so unset selection matches today's catalog
# (ALLOWED_TAG_KEYS GitHub subset). user / assignees / merged_by / closed_by
# stay out — those values can contain email.
FIELD_SCHEMA = validate_field_schema(
    (
        FieldDecl("object_type", FieldCategory.IDENTITY, "Object type", match="exact"),
        FieldDecl("repo", FieldCategory.IDENTITY, "Repository", match="exact"),
        FieldDecl("state", FieldCategory.STATUS, "State", match="exact"),
        FieldDecl("merged", FieldCategory.STATUS, "Merged", match="exact"),
        FieldDecl("labels", FieldCategory.CONTENT, "Labels", match="contains"),
        FieldDecl("num_commits", FieldCategory.CONTENT, "Commit count", match="exact"),
        FieldDecl(
            "num_files_changed",
            FieldCategory.CONTENT,
            "Files changed",
            match="exact",
        ),
    )
)
