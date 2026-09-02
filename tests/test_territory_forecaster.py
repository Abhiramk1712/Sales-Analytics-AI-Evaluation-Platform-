"""
tests/test_territory_forecaster.py
===================================
backend/ml/territory_forecaster.py had 0% coverage. It's reachable from
POST /ml/forecast/territories via backend/routers/forecasting.py, and it has a
genuinely load-bearing invariant its own docstring claims but nothing checked:
reconciled region/company totals must equal the sum of their children — that's
the entire point of "reconciliation." Pure function, no DB required.
"""
from __future__ import annotations

from backend.ml.territory_forecaster import forecast_territories


def test_empty_history_returns_an_error_not_a_crash():
    result = forecast_territories({})
    assert "error" in result


def test_short_history_falls_back_to_flat_average_forecast():
    # Fewer than 6 points: _forecast_series can't fit ETS, so every forecast value
    # should equal the historical average, repeated for the whole horizon.
    result = forecast_territories({"west": [100.0, 200.0, 300.0]}, horizon=4)
    assert result["forecasts"]["west"] == [200.0] * 4
    assert not result["reconciled"]


def test_unreconciled_forecast_has_one_series_per_territory_with_no_hierarchy():
    history = {
        "west": [100.0] * 8,
        "east": [200.0] * 8,
    }
    result = forecast_territories(history, horizon=3)
    assert set(result["forecasts"].keys()) == {"west", "east"}
    assert result["reconciled"] is False
    assert result["territory_count"] == 2


def test_reconciled_region_total_equals_sum_of_its_territories():
    history = {
        "west-1": [100.0] * 8,
        "west-2": [150.0] * 8,
        "east-1": [200.0] * 8,
    }
    hierarchy = {"west": ["west-1", "west-2"], "east": ["east-1"]}
    result = forecast_territories(history, hierarchy=hierarchy, horizon=3)

    assert result["reconciled"] is True
    fc = result["forecasts"]
    for period_idx in range(3):
        west_sum = fc["west-1"][period_idx] + fc["west-2"][period_idx]
        assert round(fc["west"][period_idx], 1) == round(west_sum, 1)


def test_reconciled_company_total_equals_sum_of_all_regions():
    history = {
        "west-1": [100.0] * 8,
        "east-1": [200.0] * 8,
    }
    hierarchy = {"west": ["west-1"], "east": ["east-1"]}
    result = forecast_territories(history, hierarchy=hierarchy, horizon=3)

    fc = result["forecasts"]
    assert "__company__" in fc
    for period_idx in range(3):
        region_sum = fc["west"][period_idx] + fc["east"][period_idx]
        assert round(fc["__company__"][period_idx], 1) == round(region_sum, 1)


def test_unknown_territory_in_hierarchy_falls_back_unreconciled_with_a_warning():
    history = {"west-1": [100.0] * 8}
    hierarchy = {"west": ["west-1", "west-does-not-exist"]}
    result = forecast_territories(history, hierarchy=hierarchy, horizon=3)

    assert result["reconciled"] is False
    assert any("west-does-not-exist" in w for w in result["warnings"])
    # Falls back to the unreconciled base forecast — still one series per known territory.
    assert "west-1" in result["forecasts"]


def test_forecast_periods_roll_over_year_boundary_from_history_periods():
    history = {"west-1": [100.0] * 8}
    result = forecast_territories(
        history, horizon=3, history_periods=["2025-10", "2025-11", "2025-12"],
    )
    assert result["forecast_periods"] == ["2026-01", "2026-02", "2026-03"]


def test_no_history_periods_falls_back_to_relative_labels():
    history = {"west-1": [100.0] * 8}
    result = forecast_territories(history, horizon=2)
    assert result["forecast_periods"] == ["T+1", "T+2"]


def test_territory_forecast_values_are_never_negative():
    # A declining series shouldn't extrapolate into negative revenue.
    declining = [1000.0, 800.0, 600.0, 400.0, 200.0, 0.0, 0.0]
    result = forecast_territories({"west-1": declining}, horizon=3)
    assert all(v >= 0.0 for v in result["forecasts"]["west-1"])
