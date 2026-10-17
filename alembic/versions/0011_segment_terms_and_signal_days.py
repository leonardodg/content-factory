"""segment_terms + keyword_signal_days (pool e persistência do Suggest: R4/R2)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-16 00:00:00.000000

Aditiva: `downgrade` remove só as duas tabelas novas — não toca `videos`,
`video_scores`, `keyword_signals` nem o experimento web (F0–F5).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "segment_terms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("kw_rank", sa.Float(), nullable=True),
        sa.Column("trend_score", sa.Float(), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("exclude_reason", sa.String(length=32), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "term", "origin", name="uq_segment_term"),
    )
    op.create_index("ix_segment_terms_segment_status", "segment_terms", ["segment_id", "status"])
    op.create_index("ix_segment_terms_segment_origin", "segment_terms", ["segment_id", "origin"])
    op.create_table(
        "keyword_signal_days",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("keyword", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("seen_on", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "keyword", "source", "seen_on"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("keyword_signal_days")
    op.drop_index("ix_segment_terms_segment_origin", table_name="segment_terms")
    op.drop_index("ix_segment_terms_segment_status", table_name="segment_terms")
    op.drop_table("segment_terms")
