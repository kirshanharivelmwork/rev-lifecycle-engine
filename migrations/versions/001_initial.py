"""Create the current multi-tenant schema.

Revision ID: 001_initial
Revises:
Create Date: 2026-09-19
"""

from alembic import op

from src.models_db import Base

revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
