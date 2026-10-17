"""videos.removed_at — parar de remedir removidos/privados (R7)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-16 00:00:00.000000

Aditiva: `downgrade` remove só a coluna nova — não toca `videos`,
`video_discoveries` nem `search_queries` (Bloco 2).
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, Sequence[str], None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("videos", sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("videos", "removed_at")
