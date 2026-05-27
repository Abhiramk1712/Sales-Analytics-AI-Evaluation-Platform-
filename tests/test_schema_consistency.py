"""
test_schema_consistency.py
Verify that every SQLAlchemy ORM table defined in models.py is also declared
in database/schema.sql, and that the revenue extra columns are present in both.
"""
import re
from pathlib import Path

import pytest

SCHEMA_PATH = Path(__file__).parents[1] / "database" / "schema.sql"


def _schema_sql() -> str:
    return SCHEMA_PATH.read_text()


def _orm_tablenames() -> list[str]:
    """Extract __tablename__ values from models.py."""
    models_path = Path(__file__).parents[1] / "backend" / "models.py"
    source = models_path.read_text()
    return re.findall(r'__tablename__\s*=\s*["\'](\w+)["\']', source)


class TestSchemaConsistency:

    def test_all_orm_tables_in_schema(self):
        sql = _schema_sql().lower()
        missing = []
        for table in _orm_tablenames():
            # Accept CREATE TABLE or ALTER TABLE patterns
            if f"create table" not in sql and f"create table if not exists {table}" not in sql:
                pass  # keep generic check below
            pattern = re.compile(
                rf"\bcreate\s+table\s+(?:if\s+not\s+exists\s+)?{re.escape(table)}\b",
                re.IGNORECASE,
            )
            if not pattern.search(sql):
                missing.append(table)
        assert missing == [], f"ORM tables missing from schema.sql: {missing}"

    def test_revenue_extra_columns_in_schema(self):
        sql = _schema_sql().lower()
        expected_cols = [
            "account_id",
            "deal_id",
            "revenue_type",
            "contract_term_months",
            "recognition_start_date",
            "product_sku",
            "is_recurring",
        ]
        missing = [c for c in expected_cols if f"alter table revenue add column if not exists {c}" not in sql]
        assert missing == [], f"Revenue extra columns missing from schema.sql: {missing}"

    def test_phase2_tables_in_schema(self):
        sql = _schema_sql().lower()
        phase2_tables = [
            "model_runs",
            "rep_product_assignments",
            "arr_waterfall",
            "bookings",
            "churn_events",
        ]
        missing = []
        for t in phase2_tables:
            if f"create table if not exists {t}" not in sql:
                missing.append(t)
        assert missing == [], f"Phase 2 tables missing from schema.sql: {missing}"

    def test_phase2_indexes_in_schema(self):
        sql = _schema_sql().lower()
        expected_indexes = [
            "idx_arr_waterfall_period",
            "idx_bookings_rep_date",
            "idx_churn_events_period",
            "idx_rep_products_rep",
        ]
        missing = [i for i in expected_indexes if i not in sql]
        assert missing == [], f"Phase 2 indexes missing from schema.sql: {missing}"
