"""video_discoveries + search_queries (atribuição por termo e log de buscas: R6/R13)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-16 00:00:00.000000

Aditiva: `downgrade` remove só as duas tabelas novas — não toca `videos`,
`segment_terms`, `video_scores` nem o experimento web (F0-F5).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, Sequence[str], None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "video_discoveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("video_id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("term_origin", sa.String(length=32), nullable=False),
        sa.Column("query_id", sa.String(length=96), nullable=True),
        sa.Column("result_position", sa.Integer(), nullable=True),
        sa.Column("run_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["video_id"], ["videos.id"]),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_video_discoveries_segment_discovered",
        "video_discoveries",
        ["segment_id", "discovered_at"],
    )
    op.create_index("ix_video_discoveries_video_id", "video_discoveries", ["video_id"])
    op.create_table(
        "search_queries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("segment_id", sa.Integer(), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("query_id", sa.String(length=96), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("results", sa.Integer(), nullable=False),
        sa.Column("kept", sa.Integer(), nullable=False),
        sa.Column("quota_used", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_search_queries_segment_started", "search_queries", ["segment_id", "started_at"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_search_queries_segment_started", table_name="search_queries")
    op.drop_table("search_queries")
    op.drop_index("ix_video_discoveries_video_id", table_name="video_discoveries")
    op.drop_index("ix_video_discoveries_segment_discovered", table_name="video_discoveries")
    op.drop_table("video_discoveries")
