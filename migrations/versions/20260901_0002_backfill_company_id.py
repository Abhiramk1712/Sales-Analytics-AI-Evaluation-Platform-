"""backfill company_id on pre-tenancy rows

The previous migration added `company_id` as nullable, so every row that existed
before it has NULL there. Those rows are invisible to tenant-scoped reads and,
worse, invisible to the scoped delete the loader now uses — so reloading the
company they actually belong to collides on the primary key against rows nothing
can see.

Backfilling is well defined precisely because of the design being replaced: under
the swap model the database held exactly one company's data at a time, so every
pre-existing row belongs to whichever company was last loaded. That is
`DEMO_DEFAULT_COMPANY`, which is also what the loader and the middleware default
to.

Set `TENANCY_BACKFILL_COMPANY` to override when upgrading a deployment whose last
loaded company was something else. Rows that already carry a company_id are left
alone, so this is safe to re-run.

Revision ID: 20260901_0002
Revises: 20260901_0001
Create Date: 2026-09-01
"""
from __future__ import annotations

import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0002"
down_revision: Union[str, None] = "20260901_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _target_company() -> str:
    explicit = (os.getenv("TENANCY_BACKFILL_COMPANY") or "").strip()
    if explicit:
        return explicit
    return (os.getenv("DEMO_DEFAULT_COMPANY") or "techo-solutions").strip()


def upgrade() -> None:
    company = _target_company()
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table_name in inspector.get_table_names():
        columns = {c["name"] for c in inspector.get_columns(table_name)}
        if "company_id" not in columns:
            continue
        bind.execute(
            sa.text(
                f'UPDATE "{table_name}" SET company_id = :company '
                "WHERE company_id IS NULL"
            ),
            {"company": company},
        )


def downgrade() -> None:
    # Which rows were backfilled and which were written with a company_id is not
    # recoverable, so this deliberately does nothing rather than guess and clear
    # ownership from rows that legitimately have it.
    pass
