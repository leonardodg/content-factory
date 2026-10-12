"""keyword_signals

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "keyword_signals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="0"),
        sa.Column("depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "segment_id", "keyword", "source", name="uq_keyword_signal"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("keyword_signals")
