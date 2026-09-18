"""merge slack field schema and has_been_indexed heads

Revision ID: d8a41c2e9f70
Revises: c7bf5721733e, b4e9c1a07d22
Create Date: 2026-09-18

Empty merge. Both branches must apply: has_been_indexed on
document_by_connector_credential_pair, and indexed_field_selection on connector.
"""

from typing import Sequence, Union

revision: str = "d8a41c2e9f70"
down_revision: Union[str, Sequence[str], None] = ("c7bf5721733e", "b4e9c1a07d22")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
