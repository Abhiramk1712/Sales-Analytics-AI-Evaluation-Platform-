"""phase9 baseline schema anchor

Revision ID: 20260505_0001
Revises:
Create Date: 2026-05-05
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260505_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Baseline anchor migration for the existing create_all-managed schema."""
    # Intentionally no-op: current environments bootstrap via SQLAlchemy metadata.
    pass


def downgrade() -> None:
    """No-op downgrade for baseline anchor migration."""
    pass
