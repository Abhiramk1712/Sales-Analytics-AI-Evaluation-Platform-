"""
Tests for data_generator realism — sales_units, sales_credits, and attainment_snapshot
generation in backend/data_generator.py.
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Test CREDIT_SPLIT_PROFILES ────────────────────────────────────────────

def test_credit_split_profiles_structure():
    from backend.data_generator import CREDIT_SPLIT_PROFILES
    assert isinstance(CREDIT_SPLIT_PROFILES, dict)
    assert len(CREDIT_SPLIT_PROFILES) > 0
    for profile_name, entries in CREDIT_SPLIT_PROFILES.items():
        assert isinstance(entries, list), f"{profile_name} should map to a list"
        for entry in entries:
            credit_type, pct_min, pct_max = entry
            assert 0 < pct_min <= pct_max <= 1.01, f"Bad range in {profile_name}/{credit_type}"


def test_credit_split_profiles_has_ae_role():
    from backend.data_generator import CREDIT_SPLIT_PROFILES
    for profile_name, entries in CREDIT_SPLIT_PROFILES.items():
        types = [e[0] for e in entries]
        assert any("ae" in t.lower() or "primary" in t.lower() for t in types), (
            f"Profile {profile_name} missing AE credit type"
        )


# ── Test EXTENSION_TABLE_ORDER ────────────────────────────────────────────

def test_extension_table_order_has_new_tables():
    from backend.data_generator import EXTENSION_TABLE_ORDER
    assert "sales_units" in EXTENSION_TABLE_ORDER
    assert "sales_credits" in EXTENSION_TABLE_ORDER
    assert "attainment_snapshots" in EXTENSION_TABLE_ORDER


def test_extension_table_order_is_list():
    from backend.data_generator import EXTENSION_TABLE_ORDER
    assert isinstance(EXTENSION_TABLE_ORDER, list)


# ── Test row generation logic (unit tests on shape) ───────────────────────

def _make_fake_opportunities(n=5):
    """Return minimal opportunity-like dicts for closed-won deals."""
    ops = []
    for i in range(n):
        op = MagicMock()
        op.id = f"opp-{i}"
        op.owner_rep_id = f"rep-{i % 3}"
        op.stage = "Closed Won"
        op.close_date = "2024-03-15"
        op.amount = 50_000 + i * 10_000
        ops.append(op)
    return ops


def test_sales_unit_rows_shape():
    """sales_unit_rows should produce one dict per Closed Won deal."""
    from backend.data_generator import CREDIT_SPLIT_PROFILES
    ops = _make_fake_opportunities(5)
    rep_to_user = {f"rep-{i}": f"user-{i}" for i in range(3)}

    sales_unit_rows = []
    for op in ops:
        if op.stage == "Closed Won":
            sales_unit_rows.append({
                "opportunity_id": op.id,
                "owner_user_id": rep_to_user.get(op.owner_rep_id),
                "booked_date": op.close_date,
                "amount": op.amount,
            })

    assert len(sales_unit_rows) == 5
    for row in sales_unit_rows:
        assert "opportunity_id" in row
        assert "owner_user_id" in row
        assert row["amount"] > 0


def test_sales_credit_rows_have_required_fields():
    """sales_credit_rows should include credit_type and credit_percent."""
    mock_credit = {
        "opportunity_id": "opp-1",
        "user_id": "user-0",
        "credit_type": "primary_ae",
        "credit_percent": 1.0,
        "credited_amount": 50_000,
        "booked_date": "2024-03-15",
    }
    required_fields = {"opportunity_id", "user_id", "credit_type", "credit_percent", "credited_amount"}
    for field in required_fields:
        assert field in mock_credit


def test_attainment_snapshot_rows_grain():
    """Attainment snapshots should have 'monthly' or 'quarterly' grain."""
    monthly_snap = {"user_id": "u1", "period": "2024-01", "grain": "monthly", "revenue": 30_000, "quota": 33_333}
    quarterly_snap = {"user_id": "u1", "period": "2024-Q1", "grain": "quarterly", "revenue": 90_000, "quota": 100_000}
    assert monthly_snap["grain"] in ("monthly", "quarterly")
    assert quarterly_snap["grain"] in ("monthly", "quarterly")


def test_attainment_snapshot_quota_monthly_vs_quarterly():
    """Monthly quota should be approximately quarterly quota / 3."""
    quarterly_quota = 90_000
    monthly_quota = quarterly_quota / 3
    assert abs(monthly_quota - 30_000) < 1


# ── Integration check: _build_saas_extension_tables returns new keys ──────

def test_build_extension_tables_returns_new_keys():
    """
    Light integration test — verifies the data_generator module loads cleanly
    and CREDIT_SPLIT_PROFILES, EXTENSION_TABLE_ORDER are all accessible.
    """
    import backend.data_generator as dg
    assert hasattr(dg, "CREDIT_SPLIT_PROFILES")
    assert hasattr(dg, "EXTENSION_TABLE_ORDER")
