"""video_scores performance/engagement nullable (Fase G: None = ausente)

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column("video_scores", "performance", existing_type=sa.Float(), nullable=True)
    op.alter_column("video_scores", "engagement", existing_type=sa.Float(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column("video_scores", "performance", existing_type=sa.Float(), nullable=False)
    op.alter_column("video_scores", "engagement", existing_type=sa.Float(), nullable=False)
