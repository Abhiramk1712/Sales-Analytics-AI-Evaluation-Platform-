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
