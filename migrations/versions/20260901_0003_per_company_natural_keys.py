"""make natural-key uniqueness per-company instead of global

Two companies legitimately use the same human-readable identifiers — every
generated dataset numbers its positions POS-001 upward, and two tenants can each
employ a rep at the same email. Those columns carried global UNIQUE constraints,
which was invisible while the database held one company at a time and became a
hard failure the moment a second could be resident: loading the second company
collided on the first company's rows.

Each affected constraint is replaced with a composite over (column, company_id).
UUID-keyed uniques are deliberately untouched: a UUID cannot collide across
companies, so widening them would add an index for no protection.

Revision ID: 20260901_0003
Revises: 20260901_0002
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0003"
down_revision: Union[str, None] = "20260901_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: table -> natural-key columns that must be unique per company, not globally.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "reps": ("email",),
    "positions": ("external_id",),
    "users": ("external_id", "email"),
    "plans": ("external_id",),
    "territories": ("external_id", "territory_code"),
    "brands": ("external_id", "name"),
    "products": ("external_id", "product_sku"),
    "sales_units": ("external_id",),
    "leads": ("external_id",),
    "opportunities": ("external_id",),
}


def _drop_existing_unique(bind, inspector, table: str, column: str) -> None:
    """
    Drop whatever single-column unique constraint or index guards `column`.

    The names were assigned by PostgreSQL when the tables were first created, so
    they are discovered rather than assumed — a hard-coded name would be right
    on one database and wrong on another.
    """
    for con in inspector.get_unique_constraints(table):
        if list(con["column_names"]) == [column]:
            op.drop_constraint(con["name"], table, type_="unique")
            return
    for idx in inspector.get_indexes(table):
        if idx.get("unique") and list(idx["column_names"]) == [column]:
            op.drop_index(idx["name"], table_name=table)
            return


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    for table, columns in NATURAL_KEYS.items():
        if table not in existing:
            continue
        for column in columns:
            _drop_existing_unique(bind, inspector, table, column)
            op.create_unique_constraint(
                f"uq_{table}_{column}_company", table, [column, "company_id"]
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    for table, columns in NATURAL_KEYS.items():
        if table not in existing:
            continue
        for column in columns:
            op.drop_constraint(f"uq_{table}_{column}_company", table, type_="unique")
            # Restoring the global constraint can fail if more than one company
            # is resident — which is precisely the state this migration enables.
            op.create_unique_constraint(f"uq_{table}_{column}", table, [column])
