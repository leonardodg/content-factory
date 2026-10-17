"""term_trend_days — trend_score por termo e dia, normalizado pela âncora (R3)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-17 00:00:00.000000

Aditiva: cria só a tabela nova (histórico do Trends). O valor corrente segue em
`segment_terms.trend_score`; `downgrade` remove apenas `term_trend_days`.
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, Sequence[str], None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "term_trend_days",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("segment_id", sa.Integer(), sa.ForeignKey("segments.id"), nullable=False),
        sa.Column("term", sa.String(length=200), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("trend_score", sa.Float(), nullable=True),
        sa.Column("interest_mean", sa.Float(), nullable=True),
        sa.Column("slope", sa.Float(), nullable=True),
        sa.Column("anchor", sa.String(length=200), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.UniqueConstraint("segment_id", "term", "day", name="uq_term_trend_day"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("term_trend_days")
