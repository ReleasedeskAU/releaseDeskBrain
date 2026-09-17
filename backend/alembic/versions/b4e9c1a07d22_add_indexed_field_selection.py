"""add indexed_field_selection to connector

Revision ID: b4e9c1a07d22
Revises: 34fe28843029
Create Date: 2026-09-17

NULL means unset (inherit connector schema defaults). JSON [] means no optional
tags. Not stored in connector_specific_config because that dict is passed into
the connector constructor.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b4e9c1a07d22"
down_revision = "34fe28843029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "connector",
        sa.Column("indexed_field_selection", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("connector", "indexed_field_selection")
