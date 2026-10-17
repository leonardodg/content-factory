"""video_scores_v2 + term_reports — viral-v2 e relatório de temas no corte (R9/R10)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-17 00:00:00.000000

Aditiva: duas tabelas novas. `video_scores` (fase-h) não é tocada;
`downgrade` remove só as tabelas desta revisão.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: Union[str, Sequence[str], None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "video_scores_v2",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id"), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("viral_score_v2", sa.Float(), nullable=True),
        sa.Column("velocity", sa.Float(), nullable=True),
        sa.Column("engagement", sa.Float(), nullable=True),
        sa.Column("channel_relative", sa.Float(), nullable=True),
        sa.Column("reach", sa.Float(), nullable=True),
        sa.Column("population_n", sa.Integer(), nullable=False),
        sa.Column("explanation", postgresql.JSONB(), nullable=False),
        sa.Column("formula_version", sa.String(length=16), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
    )
    op.create_index(
        "ix_video_scores_v2_segment_computed", "video_scores_v2", ["segment_id", "computed_at"]
    )
    op.create_index(
        "ix_video_scores_v2_video_computed", "video_scores_v2", ["video_id", "computed_at"]
    )
    op.create_table(
        "term_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("report_id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id"), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("report_key", sa.String(length=200), nullable=False),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("baseline_n", sa.Integer(), nullable=True),
        sa.Column("median_viral_v2", sa.Float(), nullable=True),
        sa.Column("median_velocity", sa.Float(), nullable=True),
        sa.Column("median_engagement", sa.Float(), nullable=True),
        sa.Column("median_channel_relative", sa.Float(), nullable=True),
        sa.Column("top10_fraction", sa.Float(), nullable=True),
        sa.Column("engagement_score", sa.Float(), nullable=True),
        sa.Column("diff", sa.Float(), nullable=True),
        sa.Column("ci_low", sa.Float(), nullable=True),
        sa.Column("ci_high", sa.Float(), nullable=True),
        sa.Column("overlap", sa.Integer(), nullable=True),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("rules_version", sa.String(length=64), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_term_reports_segment_as_of", "term_reports", ["segment_id", "as_of"])
    op.create_index("ix_term_reports_report_id", "term_reports", ["report_id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("term_reports")
    op.drop_table("video_scores_v2")
