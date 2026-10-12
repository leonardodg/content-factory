"""trend_observations

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "trend_observations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_granularity", sa.String(length=8), nullable=False),
        sa.Column("interest", sa.Float(), nullable=False),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_trend_obs_segment_keyword_period",
        "trend_observations",
        ["segment_id", "keyword", "period_start"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_trend_obs_segment_keyword_period", table_name="trend_observations")
    op.drop_table("trend_observations")
