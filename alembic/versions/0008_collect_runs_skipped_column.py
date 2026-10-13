"""collect_runs: coluna skipped (separa de failed)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-12 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0008'
down_revision: Union[str, Sequence[str], None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "collect_runs", sa.Column("skipped", sa.Integer(), nullable=False, server_default="0")
    )
    # backfill: runs antigas gravaram o "ignorado" em failed quando status=ok —
    # migrar o valor pra skipped e zerar failed nesses casos específicos
    op.execute(
        "UPDATE collect_runs SET skipped = failed, failed = 0 WHERE status = 'ok' AND failed > 0"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("UPDATE collect_runs SET failed = skipped WHERE status = 'ok' AND skipped > 0")
    op.drop_column("collect_runs", "skipped")
