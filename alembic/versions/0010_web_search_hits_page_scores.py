"""web_search_hits + web_page_scores (experimento web próprio: F1 e F3)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-16 00:00:00.000000

Aditiva: o `downgrade` remove só as duas tabelas do experimento web — não
toca `videos`, `video_segments`, `video_metrics` nem `video_scores`.
"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, Sequence[str], None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "web_search_hits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=True),
        sa.Column(
            "collected_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("query_id", sa.String(length=64), nullable=False),
        sa.Column("query_intent", sa.String(length=24), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_result_position", sa.Integer(), nullable=False),
        sa.Column("url_original", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("discard_reason", sa.String(length=64), nullable=True),
        sa.Column("collector_version", sa.String(length=32), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("snippet", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_web_search_hits_segment_collected_at",
        "web_search_hits",
        ["segment_id", "collected_at"],
    )
    op.create_table(
        "web_page_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("score_version", sa.String(length=16), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explanation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_web_page_scores_video_id", "web_page_scores", ["video_id"])
    op.create_index("ix_web_page_scores_segment_id", "web_page_scores", ["segment_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_web_page_scores_segment_id", table_name="web_page_scores")
    op.drop_index("ix_web_page_scores_video_id", table_name="web_page_scores")
    op.drop_table("web_page_scores")
    op.drop_index("ix_web_search_hits_segment_collected_at", table_name="web_search_hits")
    op.drop_table("web_search_hits")
