"""collect_runs: colunas de baseline discover_ms/persist_ms

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-13 00:00:00.000000

"""
from collections.abc import Sequence
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = '0009'
down_revision: Union[str, Sequence[str], None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # nullable sem default: runs antigas (baseline pré-instrumentação)
    # ficam NULL — esperado e documentado (médias ignoram NULLs)
    op.add_column("collect_runs", sa.Column("discover_ms", sa.Integer(), nullable=True))
    op.add_column("collect_runs", sa.Column("persist_ms", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("collect_runs", "persist_ms")
    op.drop_column("collect_runs", "discover_ms")
