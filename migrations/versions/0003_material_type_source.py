"""Track automatic versus user-selected material classification.

Revision ID: 0003_material_type_source
Revises: 0002_material_analysis
"""
from alembic import op

from models import Base
from services.database import apply_sqlite_compatibility

revision = "0003_material_type_source"
down_revision = "0002_material_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    apply_sqlite_compatibility(bind)


def downgrade() -> None:
    # Preserve local material metadata and user choices.
    pass
