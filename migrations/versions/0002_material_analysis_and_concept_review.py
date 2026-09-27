"""Material analysis, concept review, and multi-concept sessions.

Revision ID: 0002_material_analysis
Revises: 0001_studyhub_baseline
"""
from alembic import op

from models import Base
from services.database import apply_sqlite_compatibility

revision = "0002_material_analysis"
down_revision = "0001_studyhub_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    apply_sqlite_compatibility(bind)


def downgrade() -> None:
    # This local-first application never removes user learning data automatically.
    pass
