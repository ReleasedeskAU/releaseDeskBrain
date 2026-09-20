"""Declared optional GitLab tags. Display names only — never email."""

from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    validate_field_schema,
)

# Optional GitLab tags only. Identity (id/title/link) and body text are
# always-on Document fields, not this list. Import fails if a PII key is
# added. All default_selected so unset selection matches today's catalog
# (ALLOWED_TAG_KEYS GitLab subset). repo and project stay separate keys
# even though they store the same path; state and status stay separate
# even though they store the same GitLab state. type / sha / link /
# visibility / default_branch / path / branch stay out — written but not
# in today's queryable set.
FIELD_SCHEMA = validate_field_schema(
    (
        FieldDecl("key", FieldCategory.IDENTITY, "Key", match="exact"),
        FieldDecl("project", FieldCategory.IDENTITY, "Project", match="exact"),
        FieldDecl("repo", FieldCategory.IDENTITY, "Repository", match="exact"),
        FieldDecl("object_type", FieldCategory.IDENTITY, "Object type", match="exact"),
        FieldDecl("state", FieldCategory.STATUS, "State", match="exact"),
        FieldDecl("status", FieldCategory.STATUS, "Status", match="exact"),
        FieldDecl("merged", FieldCategory.STATUS, "Merged", match="exact"),
        FieldDecl("assignee", FieldCategory.OWNERSHIP, "Assignee", match="contains"),
        FieldDecl("reporter", FieldCategory.OWNERSHIP, "Reporter", match="contains"),
        FieldDecl("author", FieldCategory.OWNERSHIP, "Author", match="contains"),
        FieldDecl("created", FieldCategory.TIMING, "Created", match="date"),
        FieldDecl("updated", FieldCategory.TIMING, "Updated", match="date"),
        FieldDecl("duedate", FieldCategory.TIMING, "Due date", match="date"),
        FieldDecl("labels", FieldCategory.CONTENT, "Labels", match="contains"),
    )
)
