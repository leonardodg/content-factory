"""video_scores: metrics_available + formula_version + velocity nullable

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("video_scores", "velocity", existing_type=sa.Float(), nullable=True)
    op.add_column(
        "video_scores",
        sa.Column(
            "metrics_available",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "video_scores",
        sa.Column(
            "formula_version", sa.String(length=16), nullable=False, server_default="fase-h"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("video_scores", "formula_version")
    op.drop_column("video_scores", "metrics_available")
    op.alter_column("video_scores", "velocity", existing_type=sa.Float(), nullable=False)
