"""add company_id to tenant-scoped tables

Introduces the column that query-scoped tenancy needs. Before this, tenant
separation was achieved by dropping every table and reloading one company's
data — which meant the server could hold exactly one tenant at a time, and a
request naming a different company rebuilt the database to satisfy it.

The column is nullable here on purpose. Making it NOT NULL is a second step,
taken once every write path populates it; doing both at once would break the
CSV loader without making anything safer in the meantime. Each table is indexed
because every scoped query will filter on it.

Revision ID: 20260901_0001
Revises: 20260505_0001
Create Date: 2026-09-01
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260901_0001"
down_revision: Union[str, None] = "20260505_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


#: payout_configs is deliberately absent — it already carries a non-nullable
#: company_id and a (company_id, version) unique constraint. job_status is
#: absent because background job bookkeeping is not tenant data.
TENANT_TABLES: tuple[str, ...] = (
    "account_brand_maps",
    "account_ownership",
    "accounts",
    "activities",
    "arr_waterfall",
    "attainment_snapshots",
    "bookings",
    "brand_products",
    "brand_territories",
    "brand_users",
    "brands",
    "churn_events",
    "deals",
    "leads",
    "managers",
    "ml_predictions",
    "model_runs",
    "monthly_finance",
    "opportunities",
    "payouts",
    "plan_assignments",
    "plan_cascade_rules",
    "plans",
    "positions",
    "products",
    "quotas",
    "rep_product_assignments",
    "rep_ramp",
    "reps",
    "revenue",
    "rules",
    "sales_credits",
    "sales_unit_line_items",
    "sales_units",
    "teams",
    "territories",
    "territory_history",
    "user_territory_assignments",
    "users",
)


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.add_column(table, sa.Column("company_id", sa.String(length=100), nullable=True))
        op.create_index(f"ix_{table}_company_id", table, ["company_id"])


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
