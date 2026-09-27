"""StudyHub local MVP baseline.

Revision ID: 0001_studyhub_baseline
Revises: None
"""
from alembic import op

from models import Base
from services.database import apply_sqlite_compatibility

revision = "0001_studyhub_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)
    apply_sqlite_compatibility(bind)


def downgrade() -> None:
    # Local user data is never dropped automatically.
    pass
