"""Saved multi-concept practice scopes.

Revision ID: 0004_practice_scopes
Revises: 0003_material_type_source
"""
from alembic import op

from models import Base

revision = "0004_practice_scopes"
down_revision = "0003_material_type_source"
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    # Saved local scopes are not deleted automatically.
    pass
