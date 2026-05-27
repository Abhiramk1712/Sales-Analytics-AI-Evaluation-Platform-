"""
Tests for backend/ml/forecasting_engine.py — strategy selection, ForecastResult shape,
multi-scenario, and cascade fallbacks.
"""
import pytest
from backend.ml.forecasting_engine import (
    select_strategy,
    forecast,
    forecast_multi_scenario,
    ForecastResult,
)


# ── Strategy selector ─────────────────────────────────────────────────────

@pytest.mark.parametrize("months,expected", [
    (0,  "baseline"),
    (3,  "baseline"),
    (5,  "baseline"),
    (6,  "ets"),
    (12, "ets"),
    (17, "ets"),
    (18, "ridge"),
    (24, "ridge"),
    (35, "ridge"),
    (36, "sarimax"),
    (60, "sarimax"),
])
def test_select_strategy(months, expected):
    assert select_strategy(months) == expected


# ── ForecastResult shape ──────────────────────────────────────────────────

def _make_history(n=8):
    """Simple ascending integer history."""
    return list(range(100_000, 100_000 + n * 10_000, 10_000))


def _make_periods(n=8):
    return [f"2024-{m:02d}" for m in range(1, n + 1)]


def test_forecast_result_fields():
    hist = _make_history(8)
    periods = _make_periods(8)
    result = forecast(hist, periods, horizon=3, forecast_type="revenue", scenario="base")
    assert isinstance(result, ForecastResult)
    assert result.forecast_type == "revenue"
    assert result.scenario == "base"
    assert len(result.values) == 3
    assert len(result.periods) == 3
    assert len(result.lower_bound) == 3
    assert len(result.upper_bound) == 3
    assert result.strategy_used in ("baseline", "ets", "ridge", "sarimax")
    assert result.horizon_months == 3
    assert result.history_months == 8


def test_forecast_to_dict():
    hist = _make_history(8)
    periods = _make_periods(8)
    result = forecast(hist, periods, horizon=3)
    d = result.to_dict()
    assert "values" in d
    assert "strategy_used" in d
    assert "periods" in d
    assert "lower_bound" in d
    assert "upper_bound" in d
    assert "assumptions" in d
    assert "warnings" in d


def test_forecast_values_are_positive():
    """Forecasted revenue should stay positive for a positive history."""
    hist = _make_history(12)
    periods = _make_periods(12)
    result = forecast(hist, periods, horizon=6)
    assert all(v >= 0 for v in result.values)


def test_forecast_lower_le_upper():
    hist = _make_history(12)
    periods = _make_periods(12)
    result = forecast(hist, periods, horizon=6)
    for lo, hi in zip(result.lower_bound, result.upper_bound):
        assert lo <= hi


def test_forecast_strategy_override():
    hist = _make_history(24)
    periods = _make_periods(24)
    result = forecast(hist, periods, horizon=3, strategy_override="ridge")
    assert result.strategy_used == "ridge"


def test_forecast_short_history_falls_back_to_baseline():
    hist = [100_000, 120_000, 110_000]
    periods = ["2024-01", "2024-02", "2024-03"]
    result = forecast(hist, periods, horizon=3)
    assert result.strategy_used == "baseline"


# ── Multi-scenario ────────────────────────────────────────────────────────

def test_multi_scenario_keys():
    hist = _make_history(8)
    periods = _make_periods(8)
    results = forecast_multi_scenario(hist, periods, horizon=3)
    assert "base" in results
    assert "optimistic" in results
    assert "conservative" in results


def test_multi_scenario_values_ordered():
    """optimistic >= base >= conservative by construction (scenario multipliers)."""
    hist = _make_history(8)
    periods = _make_periods(8)
    results = forecast_multi_scenario(hist, periods, horizon=3)
    base_avg = sum(results["base"].values) / len(results["base"].values)
    opt_avg  = sum(results["optimistic"].values) / len(results["optimistic"].values)
    cons_avg = sum(results["conservative"].values) / len(results["conservative"].values)
    assert opt_avg >= base_avg
    assert base_avg >= cons_avg


def test_multi_scenario_custom_scenarios():
    hist = _make_history(8)
    periods = _make_periods(8)
    results = forecast_multi_scenario(hist, periods, horizon=2, scenarios=["base", "pipeline_slippage"])
    assert set(results.keys()) == {"base", "pipeline_slippage"}


def test_forecast_types_accepted():
    hist = _make_history(8)
    periods = _make_periods(8)
    for ft in ("revenue", "pipeline", "booking", "payout", "quota_attainment"):
        result = forecast(hist, periods, horizon=2, forecast_type=ft)
        assert result.forecast_type == ft


def test_backtest_metrics_numeric():
    hist = _make_history(18)  # enough for ets/ridge
    periods = _make_periods(18)
    result = forecast(hist, periods, horizon=3)
    assert isinstance(result.backtest_mae, (int, float))
    assert isinstance(result.backtest_rmse, (int, float))
    assert isinstance(result.backtest_mape, (int, float))
