"""Declared optional Jira tags. Never email. Never custom_fields."""

from onyx.connectors.field_schema import (
    FieldCategory,
    FieldDecl,
    validate_field_schema,
)

# Optional Jira tags only. Identity (id/title/link) and description/comments
# are always-on Document fields, not this list. Import fails if a PII key is
# added. All default_selected so unset selection matches today's catalog.
FIELD_SCHEMA = validate_field_schema(
    (
        FieldDecl("key", FieldCategory.IDENTITY, "Key", match="exact"),
        FieldDecl("project", FieldCategory.IDENTITY, "Project", match="exact"),
        FieldDecl(
            "project_name", FieldCategory.IDENTITY, "Project name", match="exact"
        ),
        FieldDecl("issuetype", FieldCategory.IDENTITY, "Issue type", match="exact"),
        FieldDecl("status", FieldCategory.STATUS, "Status", match="exact"),
        FieldDecl(
            "status_category",
            FieldCategory.STATUS,
            "Status category",
            match="exact",
        ),
        FieldDecl("resolution", FieldCategory.STATUS, "Resolution", match="exact"),
        FieldDecl("status_was", FieldCategory.STATUS, "Prior status", match="exact"),
        FieldDecl("priority", FieldCategory.RISK, "Priority", match="exact"),
        FieldDecl("assignee", FieldCategory.OWNERSHIP, "Assignee", match="contains"),
        FieldDecl("reporter", FieldCategory.OWNERSHIP, "Reporter", match="contains"),
        FieldDecl(
            "last_updater",
            FieldCategory.OWNERSHIP,
            "Last updater",
            match="contains",
        ),
        FieldDecl("created", FieldCategory.TIMING, "Created", match="date"),
        FieldDecl("updated", FieldCategory.TIMING, "Updated", match="date"),
        FieldDecl("duedate", FieldCategory.TIMING, "Due date", match="date"),
        FieldDecl(
            "resolution_date",
            FieldCategory.TIMING,
            "Resolution date",
            match="date",
        ),
        FieldDecl("parent", FieldCategory.RELATIONSHIPS, "Parent", match="exact"),
        FieldDecl("issuelink", FieldCategory.RELATIONSHIPS, "Issue link", match="exact"),
        FieldDecl(
            "issuelink_type",
            FieldCategory.RELATIONSHIPS,
            "Issue link type",
            match="exact",
        ),
        FieldDecl("labels", FieldCategory.CONTENT, "Labels", match="contains"),
    )
)
