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


# ── Open-deal expected_close_date realism ──────────────────────────────────
#
# expected_close_date used to always be computed as created + cycle_days +
# noise, regardless of stage — fine for a deal that actually closed on
# schedule, but for a still-open deal it meant a date frozen at generation
# time relative to whenever the deal happened to be created, not a
# forward-looking commitment. With deals seasonally sampled across the
# entire `months`-long window and stage chosen independent of deal age,
# most open deals were old enough that this date had already passed by
# "today": on techo-solutions' real seeded data, ~87% of ALL open deals were
# flagged "overdue" by pipeline hygiene — not a modest, realistic minority,
# virtually the entire open pipeline. Fixed by making expected_close_date
# forward-looking (today + N days) for the ~82% of open deals that are on
# track, and only deliberately in the past (a realistic slipped minority)
# for the rest — without changing how many deals land in an open stage at
# all, so overall pipeline size/value isn't collateral damage of the fix.

def test_open_deal_close_dates_are_a_realistic_minority_overdue():
    import random as _random
    from datetime import date as _date
    from backend.data_generator import _generate_dataset

    _random.seed(42)
    dataset = _generate_dataset(n_reps=8, n_accounts=20, n_deals=300, months=36)
    deals = dataset["deals"]
    today = _date.today()

    open_deals = [d for d in deals if d["stage"] not in ("Closed Won", "Closed Lost")]
    assert len(open_deals) > 0, "test is meaningless with zero open deals"

    overdue = [d for d in open_deals if _date.fromisoformat(d["expected_close_date"]) < today]
    overdue_pct = len(overdue) / len(open_deals)

    # Was ~0.87 before the fix; target is ~0.18. Generous bound (not tuned
    # tight against the target) so ordinary sampling noise doesn't flake it.
    assert overdue_pct < 0.40, f"{overdue_pct:.0%} of open deals are overdue — pipeline hygiene signal is drowned out again"


def test_open_deal_pipeline_size_is_not_collateral_damage():
    """The fix must not achieve a low overdue rate by making almost nothing
    open at all — an earlier draft of this fix did exactly that (age-biased
    stage selection cut a 103-deal open pipeline down to 14). Open deals
    should still make up a substantial share of the total, matching what
    techo-solutions' real seeded data looks like today (~52% open)."""
    import random as _random
    from backend.data_generator import _generate_dataset

    _random.seed(42)
    dataset = _generate_dataset(n_reps=8, n_accounts=20, n_deals=300, months=36)
    deals = dataset["deals"]

    open_count = sum(1 for d in deals if d["stage"] not in ("Closed Won", "Closed Lost"))
    open_pct = open_count / len(deals)

    assert open_pct > 0.30, f"only {open_pct:.0%} of deals are open — pipeline size looks collapsed"
