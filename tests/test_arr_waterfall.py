"""Tests for ARR waterfall function (backend/ml/forecasting.py)."""
from __future__ import annotations

import pytest

from backend.ml.forecasting import build_arr_waterfall


def _monthly_revenue(start_month=1, n=12, base=100_000):
    return {f"2024-{(start_month + i - 1) % 12 + 1:02d}": base + i * 2000 for i in range(n)}


def test_build_arr_waterfall_returns_dict():
    result = build_arr_waterfall(_monthly_revenue())
    assert isinstance(result, dict)


def test_waterfall_has_required_keys():
    result = build_arr_waterfall(_monthly_revenue())
    wf = result.get("waterfall", result)
    # Accept either a waterfall list or a single-period dict
    if isinstance(wf, list):
        assert len(wf) > 0
        row = wf[0]
    else:
        row = wf
    for key in ("new_logo", "expansion", "churn", "renewal"):
        assert key in row, f"Missing key: {key}"


def test_waterfall_without_type_data():
    """Should work fine when revenue_by_type is None (approximation mode)."""
    result = build_arr_waterfall(_monthly_revenue(), revenue_by_type=None)
    assert result is not None


def test_waterfall_with_type_data():
    """Should use provided type breakdown when given."""
    revenue = _monthly_revenue()
    rev_by_type = {p: {"new_logo": 15000, "renewal": 70000, "expansion": 15000} for p in revenue}
    result = build_arr_waterfall(revenue, revenue_by_type=rev_by_type)
    assert result is not None


def test_waterfall_nrr_present():
    result = build_arr_waterfall(_monthly_revenue(n=24))
    # nrr_rolling_12m or nrr_pct should appear somewhere in the result
    result_str = str(result)
    assert "nrr" in result_str.lower()


def test_empty_revenue():
    result = build_arr_waterfall({})
    assert result is not None


def test_single_period():
    result = build_arr_waterfall({"2024-01": 100_000})
    assert result is not None
