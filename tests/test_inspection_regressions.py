"""
tests/test_inspection_regressions.py
====================================
Regressions for the defects found by running the application.

Each of these returned 500 on every request, and none was caught by the existing
suite — they were found by driving the running app, not by testing it. The tests
here are written against the specific failure mode rather than the happy path,
because in two of the three cases the happy path already worked and the bug lived
in a branch the tests never took.
"""
from __future__ import annotations

import statistics

import numpy as np
import pytest

from backend.audit.payout_audit import to_native


# ── #6: NameError in the churn forecaster's account_id fallback ──────────────


def test_survival_frame_handles_records_without_account_id():
    """
    `[r.get("account_id", str(i)) ...]` never bound `i`, so any record missing
    account_id raised NameError and the endpoint 500'd.

    The records here deliberately omit account_id: a fixture built from complete
    records exercises the `.get()` hit and passes against the broken code.
    """
    from backend.ml.churn_forecaster import fit_survival_model

    records = [
        {"duration_months": 12, "churned": True, "cohort": "a"},
        {"duration_months": 8, "churned": False, "cohort": "a"},
        {"duration_months": 20, "churned": True, "cohort": "b"},
        {"duration_months": 4, "churned": False, "cohort": "b"},
    ]
    result = fit_survival_model(records)

    assert isinstance(result, dict)
    assert "error" not in result, result


def test_survival_frame_still_uses_a_supplied_account_id():
    """The fallback must not override ids that are present."""
    from backend.ml.churn_forecaster import fit_survival_model

    records = [
        {"duration_months": 12, "churned": True, "account_id": "acct-1"},
        {"duration_months": 6, "churned": False, "account_id": "acct-2"},
    ]
    assert "error" not in fit_survival_model(records)


# ── #7: numpy scalars are not JSON-serializable ──────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (np.False_, False),
        (np.True_, True),
        (np.float64(1.5), 1.5),
        (np.int64(7), 7),
    ],
)
def test_numpy_scalars_become_python_natives(value, expected):
    converted = to_native(value)
    assert converted == expected
    assert type(converted).__module__ == "builtins"


def test_numpy_values_are_converted_inside_containers():
    """
    The audit report nests numpy values several levels deep — per-rep rows inside
    lists inside dicts — so a shallow conversion would still leave 500s.
    """
    payload = {
        "flag": np.False_,
        "reps": [{"mape_flagged": np.True_, "mape": np.float64(0.42)}],
        "nested": {"deep": [np.int64(3)]},
    }
    out = to_native(payload)

    import json

    json.dumps(out)  # would raise before the fix
    assert out == {"flag": False, "reps": [{"mape_flagged": True, "mape": 0.42}],
                   "nested": {"deep": [3]}}


def test_to_native_leaves_ordinary_values_alone():
    payload = {"a": 1, "b": "x", "c": None, "d": [1.5, True]}
    assert to_native(payload) == payload


# ── #8: GROUP BY expression must match the SELECT expression ─────────────────


def test_cohort_query_reuses_one_expression_object():
    """
    Building `func.to_char(...)` separately for SELECT, GROUP BY and ORDER BY
    makes SQLAlchemy emit a distinct bind parameter each time. PostgreSQL matches
    GROUP BY expressions syntactically, so `to_char(col, $1)` and
    `to_char(col, $4)` are different expressions and the column reads as
    ungrouped — every request 500'd with "must appear in the GROUP BY clause".

    Compiling with literal binds shows whether the rendered expressions agree.
    """
    from sqlalchemy import func, select

    from backend.models import Deal

    cohort_expr = func.to_char(Deal.actual_close_date, "YYYY-MM")
    good = select(cohort_expr.label("cohort"), func.count(Deal.id)).group_by(cohort_expr)

    sql = str(good.compile(compile_kwargs={"literal_binds": True}))
    select_part, _, group_part = sql.partition("GROUP BY")
    assert "to_char" in group_part
    # The same rendered expression must appear on both sides.
    assert "to_char(deals.actual_close_date, 'YYYY-MM')" in select_part
    assert "to_char(deals.actual_close_date, 'YYYY-MM')" in group_part


def test_analytics_module_does_not_rebuild_the_cohort_expression():
    """Guard the actual endpoint, not just the pattern in isolation."""
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent
              / "backend" / "routers" / "analytics.py").read_text(encoding="utf-8")
    start = source.index("cohort_expr = func.to_char")
    end = source.index("@router.", start)
    body = source[start:end]

    # Exactly one construction: the assignment itself.
    assert body.count('func.to_char(Deal.actual_close_date, "YYYY-MM")') == 1, (
        "the cohort expression is rebuilt instead of reused; GROUP BY will not match"
    )


# ── #11: quotas must be calibrated against generated revenue ─────────────────


def test_quota_calibration_targets_a_realistic_attainment_spread():
    """
    Quotas were built top-down from a constant and never referred to the revenue
    the same generator produced, so attainment averaged 218% with individual
    rep-quarters at 452% and 768%, and some quarters carried a $1.00 quota.

    Beyond realism that defeated the compensation demo: the plans define four
    attainment tiers, and with nearly every rep in the top one the tiering was
    never exercised.
    """
    from backend.data_generator import _calibrate_quotas_to_revenue

    quota_rows = [{"rep_id": f"r{i}", "period": "2026-Q1", "amount": "1.00"} for i in range(200)]
    revenue_rows = [
        {"rep_id": f"r{i}", "period": "2026-02", "amount": "100000"} for i in range(200)
    ]

    calibrated = _calibrate_quotas_to_revenue(quota_rows, revenue_rows)
    attainments = [100_000 / float(r["amount"]) * 100 for r in calibrated]

    assert len(calibrated) == 200
    # Centred near plan rather than several times over it.
    assert 90 <= statistics.median(attainments) <= 115, statistics.median(attainments)
    # And spread across the tiers rather than piled into one.
    assert min(attainments) < 90
    assert max(attainments) > 115


def test_quota_calibration_floors_tiny_quotas():
    """A ramp factor on a small base produced $1.00 quotas, which made attainment
    a meaningless ratio."""
    from backend.data_generator import MIN_QUARTERLY_QUOTA, _calibrate_quotas_to_revenue

    rows = _calibrate_quotas_to_revenue(
        [{"rep_id": "r1", "period": "2026-Q1", "amount": "1.00"}],
        [],  # no revenue to calibrate against
    )
    assert float(rows[0]["amount"]) >= MIN_QUARTERLY_QUOTA


def test_quota_calibration_leaves_row_identity_intact():
    """Only the amount changes; rep and period must survive."""
    from backend.data_generator import _calibrate_quotas_to_revenue

    rows = _calibrate_quotas_to_revenue(
        [{"rep_id": "r1", "period": "2026-Q1", "amount": "5.00"}],
        [{"rep_id": "r1", "period": "2026-01", "amount": "50000"}],
    )
    assert rows[0]["rep_id"] == "r1"
    assert rows[0]["period"] == "2026-Q1"
    assert float(rows[0]["amount"]) > 5.0


# ── #12: SPIFs and clawbacks must exist and fire ─────────────────────────────


def test_default_config_ships_spiff_and_clawback_rules():
    """Both endpoints returned empty lists, so two advertised features showed
    nothing and neither engine branch ran against data."""
    from backend.payout.engine import DEFAULT_PAYOUT_CONFIG

    assert DEFAULT_PAYOUT_CONFIG.spiff_rules, "no SPIF rules configured"
    assert DEFAULT_PAYOUT_CONFIG.clawback_rules, "no clawback rules configured"


def test_spiff_fires_for_an_overachiever_and_not_on_plan():
    from backend.payout.engine import DEFAULT_PAYOUT_CONFIG, PayoutEngine

    engine = PayoutEngine(config=DEFAULT_PAYOUT_CONFIG)
    over = engine.compute(150_000, 100_000, 9, 1)   # 150% attainment, 90% win rate
    on_plan = engine.compute(105_000, 100_000, 6, 4)  # 105%, 60%

    assert over["spiff_total"] > 0
    assert on_plan["spiff_total"] == 0


def test_clawback_fires_only_when_win_rate_is_far_below_plan():
    from backend.payout.engine import DEFAULT_PAYOUT_CONFIG, PayoutEngine

    engine = PayoutEngine(config=DEFAULT_PAYOUT_CONFIG)
    poor = engine.compute(50_000, 100_000, 2, 8)     # 20% win rate
    healthy = engine.compute(105_000, 100_000, 6, 4)  # 60% win rate

    assert poor["clawback_total"] > 0
    assert healthy["clawback_total"] == 0


# ── Non-finite floats are not JSON either ────────────────────────────────────


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_floats_become_none(value):
    """
    /ml/forecast/churn-risk 500'd on `inf` after the numpy fix: a survival curve
    that never drops below 0.5 has an infinite median tenure, and JSON cannot
    express it.

    None rather than 0 — "there is no median tenure" is genuinely absent, and 0
    would read as "churns immediately", the opposite of what infinity means.
    """
    from backend.utils.json_safe import json_safe

    assert json_safe(value) is None


def test_json_safe_handles_mixed_nested_payloads():
    """Both failure modes reached JSON from the same shape of code."""
    import json

    from backend.utils.json_safe import json_safe

    payload = {
        "median_tenure_months": float("inf"),
        "flag": np.True_,
        "curve": {"0": 1.0, "1": float("nan")},
        "weights": [np.float64(0.5), float("-inf")],
    }
    out = json_safe(payload)
    json.dumps(out)  # would raise before the fix

    assert out["median_tenure_months"] is None
    assert out["flag"] is True
    assert out["curve"]["1"] is None
    assert out["weights"] == [0.5, None]


def test_json_safe_preserves_ordinary_finite_values():
    from backend.utils.json_safe import json_safe

    payload = {"a": 1, "b": 2.5, "c": "x", "d": None, "e": [True, 0.0]}
    assert json_safe(payload) == payload
