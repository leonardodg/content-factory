"""trend_observations: unique (segment_id, keyword, period_start, period_granularity)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = '0007'
down_revision: Union[str, Sequence[str], None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # dedupe antes da constraint: mantém UMA linha por
    # (segment_id, keyword, period_start, period_granularity) — preferindo a
    # de maior collected_at; empate de collected_at (mesma transação) mantém
    # a de maior id (inserida depois).
    op.execute(
        """
        DELETE FROM trend_observations t1
        USING trend_observations t2
        WHERE t1.segment_id = t2.segment_id
          AND t1.keyword = t2.keyword
          AND t1.period_start = t2.period_start
          AND t1.period_granularity = t2.period_granularity
          AND t1.id <> t2.id
          AND (t1.collected_at < t2.collected_at
               OR (t1.collected_at = t2.collected_at AND t1.id < t2.id))
        """
    )
    op.drop_index("ix_trend_obs_segment_keyword_period", table_name="trend_observations")
    op.create_unique_constraint(
        "uq_trend_observation_period",
        "trend_observations",
        ["segment_id", "keyword", "period_start", "period_granularity"],
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint("uq_trend_observation_period", "trend_observations", type_="unique")
    op.create_index(
        "ix_trend_obs_segment_keyword_period",
        "trend_observations",
        ["segment_id", "keyword", "period_start"],
    )
