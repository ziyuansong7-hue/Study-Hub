"""Content quality, question provenance, and session source policy.

Revision ID: 0005_content_quality
Revises: 0004_practice_scopes
"""
from alembic import op

from models import Base
from services.database import apply_sqlite_compatibility

revision = "0005_content_quality"
down_revision = "0004_practice_scopes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    apply_sqlite_compatibility(bind)


def downgrade() -> None:
    pass
