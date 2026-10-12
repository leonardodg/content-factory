"""research_campaigns

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "research_campaigns",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_campaigns_segment_id_active",
        "research_campaigns",
        ["segment_id", "active"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_research_campaigns_segment_id_active", table_name="research_campaigns")
    op.drop_table("research_campaigns")
