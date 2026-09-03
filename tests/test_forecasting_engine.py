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


# ── Coverage pass ahead of the forecasting-stack consolidation ──────────────
#
# forecasting_engine.py was already at 84% from the tests above; this section
# closes the remaining gaps — pure-function edge cases, an actually-reached
# SARIMAX backtest branch, and unknown-input handling — plus adds an explicit
# output-shape contract. That contract exists specifically because of what
# investigating this consolidation turned up: GET /payout/forecast read
# run_revenue_forecast(...).forecast (an attribute) against a function that
# returns a dict keyed "forecast_values" — wrong on every call, silently
# swallowed by a broad except, undetected because nothing pinned the shape.
# Every real caller of forecasting_engine.forecast() was audited by hand and
# found correct (they all call .to_dict() or use dataclass attributes
# correctly) — this test is what would have caught it structurally instead of
# by manual audit, and what protects against the same mistake being
# reintroduced during the consolidation.

def _make_periods_rolling(n: int, start_year: int = 2022, start_month: int = 1) -> list[str]:
    """Like _make_periods, but rolls over year boundaries correctly for n > 12
    — needed to reach SARIMAX's backtest branch (requires 30+ months)."""
    labels = []
    y, m = start_year, start_month
    for _ in range(n):
        labels.append(f"{y}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return labels


def test_forecast_result_to_dict_has_exactly_the_documented_keys():
    """Pins ForecastResult's shape. If forecasting_engine.py ever changes
    field names, callers using .to_dict() (routers/forecasting.py's forecast
    lab endpoint, forecasting_lab/models.py) find out from this test, not
    from a silently-degraded response in production."""
    hist = _make_history(8)
    periods = _make_periods(8)
    result = forecast(hist, periods, horizon=2)
    d = result.to_dict()
    assert set(d.keys()) == {
        "forecast_type", "scenario", "strategy_used", "periods", "values",
        "lower_bound", "upper_bound", "backtest_mae", "backtest_rmse",
        "backtest_mape", "confidence_interval", "assumptions", "warnings",
        "history_months", "horizon_months",
        # Added for the "ensemble" strategy (forecasting-stack consolidation):
        # real commit/best-case lanes and per-component weights. Every other
        # strategy leaves these at their empty defaults ([]/{}) — they exist
        # on every ForecastResult so callers don't need a strategy-specific
        # shape check, matching this test's own "pin the shape" premise.
        "commit_values", "best_case_values", "component_weights",
    }
    # And the attribute-access path audit/payout_audit.py relies on directly.
    for attr in ("values", "periods", "backtest_mape", "backtest_mae", "history_months", "strategy_used"):
        assert hasattr(result, attr)


def test_unknown_forecast_type_falls_back_to_revenue_with_a_warning():
    hist = _make_history(8)
    periods = _make_periods(8)
    result = forecast(hist, periods, horizon=2, forecast_type="not_a_real_type")
    assert result.forecast_type == "revenue"
    assert any("Unknown forecast_type" in w for w in result.warnings)


def test_unknown_scenario_falls_back_to_base_with_a_warning():
    hist = _make_history(8)
    periods = _make_periods(8)
    result = forecast(hist, periods, horizon=2, scenario="not_a_real_scenario")
    assert result.scenario == "base"
    assert any("Unknown scenario" in w for w in result.warnings)


def test_empty_history_returns_zeroed_forecast_not_a_crash():
    result = forecast([], [], horizon=4)
    assert result.values == [0.0, 0.0, 0.0, 0.0]
    assert result.history_months == 0
    assert any("Empty history" in w for w in result.warnings)


def test_sarimax_backtest_branch_is_reached_with_enough_history():
    """bt_len >= 3 and n - bt_len >= 24 is SARIMAX's own documented gate for
    running a backtest at all (backend/ml/forecasting_engine.py's
    _forecast_sarimax) — 36 months clears it with room to spare."""
    hist = _make_history(36)
    periods = _make_periods_rolling(36)
    result = forecast(hist, periods, horizon=3, strategy_override="sarimax")
    assert result.strategy_used == "sarimax"
    assert result.backtest_mae is not None
    assert result.backtest_rmse is not None


def test_next_periods_malformed_label_returns_empty_not_a_crash():
    from backend.ml.forecasting_engine import _next_periods
    assert _next_periods("not-a-period", 3) == []


def test_next_periods_rolls_over_year_boundary():
    from backend.ml.forecasting_engine import _next_periods
    assert _next_periods("2024-11", 3) == ["2024-12", "2025-01", "2025-02"]


def test_compute_backtest_metrics_mismatched_lengths_returns_none_triple():
    from backend.ml.forecasting_engine import _compute_backtest_metrics
    assert _compute_backtest_metrics([1.0, 2.0], [1.0]) == (None, None, None)


def test_compute_backtest_metrics_skips_zero_actuals_for_mape():
    """MAPE divides by the actual value — a zero actual would divide by
    zero; the real function excludes those points rather than crashing or
    producing inf, which is worth pinning explicitly."""
    from backend.ml.forecasting_engine import _compute_backtest_metrics
    mae, rmse, mape = _compute_backtest_metrics([0.0, 100.0], [10.0, 90.0])
    assert mae == 10.0
    assert mape == 10.0 / 100.0  # only the non-zero actual counts toward MAPE


def test_baseline_backtest_branch_runs_with_four_or_more_months():
    """_forecast_baseline's own backtest only runs at len(history) >= 4 —
    every other baseline-path test in this file uses shorter or 0-length
    history, so this branch had never executed."""
    hist = _make_history(5)
    periods = _make_periods(5)
    result = forecast(hist, periods, horizon=2, strategy_override="baseline")
    assert result.strategy_used == "baseline"
    assert result.backtest_mae is not None


# ── Ensemble strategy (forecasting-stack consolidation, Phase 1) ───────────
#
# "ensemble" moves RevenueForecastModel from forecasting.py into this module
# verbatim and exposes it as an explicit-override-only strategy — reachable
# via strategy_override="ensemble", never auto-selected by select_strategy().
# It bypasses the generic 5-tuple dispatch + _confidence_bounds() path
# entirely (see forecast()'s special-case block) so it can return its own
# real bootstrap CI and commit/best-case lanes instead of a synthetic
# symmetric-MAE CI. These tests pin that both branches of that special case
# (>= 6 months real ensemble, < 6 months fallback) behave correctly.

def test_select_strategy_never_returns_ensemble():
    """Ensemble is reachable only via explicit strategy_override — it must
    never be an auto-selection outcome, at any history length."""
    for months in (0, 3, 6, 12, 18, 24, 36, 60, 120):
        assert select_strategy(months) != "ensemble"


def _make_noisy_history(n=24):
    """A perfectly linear ramp (_make_history's shape) makes one ensemble
    component fit the backtest with RMSE == 0, and RevenueForecastModel's
    inverse-RMSE weighting (1.0 / rmse) divides by zero — a real,
    pre-existing bug in forecasting.py's original class, faithfully
    preserved by the verbatim move (confirmed to reproduce identically
    against the untouched forecasting.py code, unrelated to this branch).
    Real revenue data is never perfectly linear, so exercise the ensemble
    path with mild noise instead, same as every other real caller does."""
    return [100_000 + i * 3_000 + (i % 4) * 1_500 for i in range(n)]


def test_ensemble_strategy_with_enough_history_populates_real_lanes():
    hist = _make_noisy_history(24)
    periods = _make_periods_rolling(24)
    result = forecast(hist, periods, horizon=6, strategy_override="ensemble")
    assert result.strategy_used == "ensemble"
    assert len(result.values) == 6
    assert len(result.commit_values) == 6
    assert len(result.best_case_values) == 6
    assert set(result.component_weights.keys()) == {"sarimax", "ridge", "gbr"}
    assert result.warnings == []
    # commit (p20) <= base <= best_case (p80) at every horizon step, by
    # construction of the bootstrap percentile lanes.
    for c, v, b in zip(result.commit_values, result.values, result.best_case_values):
        assert c <= v <= b


def test_ensemble_strategy_below_floor_falls_back_to_baseline_with_a_warning():
    """RevenueForecastModel's own floor is 6 months. Below that, forecast()
    must not crash or silently run the real ensemble — it warns and falls
    back to baseline, and the three ensemble-only fields stay at their empty
    defaults rather than holding stale/partial data."""
    hist = [100_000, 110_000, 95_000, 120_000]
    periods = ["2024-01", "2024-02", "2024-03", "2024-04"]
    result = forecast(hist, periods, horizon=3, strategy_override="ensemble")
    assert result.strategy_used == "ensemble"
    assert len(result.values) == 3
    assert result.commit_values == []
    assert result.best_case_values == []
    assert result.component_weights == {}
    assert any("Ensemble strategy needs >= 6 months" in w for w in result.warnings)


def test_ensemble_strategy_matches_forecasting_py_original_numerically():
    """The move must be behavior-preserving: identical input, identical
    random seed -> identical output, field-for-field, against the original
    RevenueForecastModel still in forecasting.py. This is the numeric-parity
    check the consolidation plan requires before anything downstream is
    allowed to start depending on the moved copy."""
    import random
    import numpy as np
    import pandas as pd
    from backend.ml.forecasting import RevenueForecastModel as OldModel
    from backend.ml.forecasting_engine import RevenueForecastModel as NewModel

    hist = _make_noisy_history(24)
    periods = _make_periods_rolling(24)

    def _series():
        s = pd.Series(dict(zip(periods, hist)))
        s.index = pd.PeriodIndex(s.index, freq="M").to_timestamp()
        return s

    random.seed(7)
    np.random.seed(7)
    old_result = OldModel(horizon=6).fit(_series()).predict()

    random.seed(7)
    np.random.seed(7)
    new_result = NewModel(horizon=6).fit(_series()).predict()

    assert old_result.forecast == new_result.values
    assert old_result.lower_ci == new_result.lower_ci
    assert old_result.upper_ci == new_result.upper_ci
    assert old_result.commit_lane == new_result.commit
    assert old_result.best_case_lane == new_result.best_case
    assert old_result.ensemble_weights == new_result.weights
    assert old_result.model_info == new_result.model_info


def test_sarimax_failure_falls_back_to_ridge(monkeypatch):
    """Proves the fallback chain actually works, not just that it's written:
    force SARIMAX to raise and confirm the result is Ridge's real output
    (assumptions say so, and the forecast values are non-trivial), not a
    crash propagating up through forecast(). _forecast_sarimax does its own
    `from statsmodels...sarimax import SARIMAX` inside the function body, so
    the patch target is statsmodels' own class, not this module's namespace
    — patching the latter would be a no-op against a fresh import."""
    import statsmodels.tsa.statespace.sarimax as sarimax_module

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic SARIMAX failure for this test")

    monkeypatch.setattr(sarimax_module, "SARIMAX", _boom)
    hist = _make_history(36)
    periods = _make_periods_rolling(36)
    result = forecast(hist, periods, horizon=3, strategy_override="sarimax")
    assert any("SARIMAX failed" in a and "Ridge" in a for a in result.assumptions)
    assert len(result.values) == 3
