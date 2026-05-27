"""
backend/ml/forecasting_engine.py
==================================
Enterprise forecasting engine with automatic strategy selection.

Strategy selector
-----------------
  < 6 months history  → baseline (moving-average / last-known)
  6–17 months         → ETS (Holt-Winters exponential smoothing)
  18–35 months        → Ridge regression with calendar features
  ≥ 36 months         → SARIMAX (seasonal ARIMA with exogenous vars)

Forecast types
--------------
  revenue | pipeline | booking | payout | quota_attainment | ARR | commit | best_case

Scenario modifiers
------------------
  base | optimistic | conservative | pipeline_slippage | churn_spike

Output (ForecastResult)
-----------------------
  strategy_used, backtest_mae, backtest_rmse, backtest_mape,
  assumptions, confidence_interval, periods, values,
  scenario, forecast_type, warnings
"""
from __future__ import annotations

import math
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

# ── Constants ────────────────────────────────────────────────────────────

STRATEGY_BASELINE = "baseline"
STRATEGY_ETS      = "ets"
STRATEGY_RIDGE    = "ridge"
STRATEGY_SARIMAX  = "sarimax"

SCENARIO_BASE              = "base"
SCENARIO_OPTIMISTIC        = "optimistic"
SCENARIO_CONSERVATIVE      = "conservative"
SCENARIO_PIPELINE_SLIPPAGE = "pipeline_slippage"
SCENARIO_CHURN_SPIKE       = "churn_spike"

SCENARIO_MULTIPLIERS: dict[str, float] = {
    SCENARIO_BASE:              1.00,
    SCENARIO_OPTIMISTIC:        1.12,
    SCENARIO_CONSERVATIVE:      0.88,
    SCENARIO_PIPELINE_SLIPPAGE: 0.78,
    SCENARIO_CHURN_SPIKE:       0.72,
}

FORECAST_TYPES = frozenset([
    "revenue", "pipeline", "booking", "payout",
    "quota_attainment", "ARR", "commit", "best_case",
])


# ── Output dataclass ────────────────────────────────────────────────────

@dataclass
class ForecastResult:
    forecast_type:      str
    scenario:           str
    strategy_used:      str
    periods:            list[str]           = field(default_factory=list)   # "YYYY-MM" labels
    values:             list[float]         = field(default_factory=list)
    lower_bound:        list[float]         = field(default_factory=list)
    upper_bound:        list[float]         = field(default_factory=list)
    backtest_mae:       Optional[float]     = None
    backtest_rmse:      Optional[float]     = None
    backtest_mape:      Optional[float]     = None
    confidence_interval: float             = 0.80
    assumptions:        list[str]           = field(default_factory=list)
    warnings:           list[str]           = field(default_factory=list)
    history_months:     int                 = 0
    horizon_months:     int                 = 6

    def to_dict(self) -> dict[str, Any]:
        return {
            "forecast_type":       self.forecast_type,
            "scenario":            self.scenario,
            "strategy_used":       self.strategy_used,
            "periods":             self.periods,
            "values":              [round(v, 2) for v in self.values],
            "lower_bound":         [round(v, 2) for v in self.lower_bound],
            "upper_bound":         [round(v, 2) for v in self.upper_bound],
            "backtest_mae":        round(self.backtest_mae, 2) if self.backtest_mae is not None else None,
            "backtest_rmse":       round(self.backtest_rmse, 2) if self.backtest_rmse is not None else None,
            "backtest_mape":       round(self.backtest_mape, 4) if self.backtest_mape is not None else None,
            "confidence_interval": self.confidence_interval,
            "assumptions":         self.assumptions,
            "warnings":            self.warnings,
            "history_months":      self.history_months,
            "horizon_months":      self.horizon_months,
        }


# ── Period helpers ───────────────────────────────────────────────────────

def _next_periods(last_period: str, n: int) -> list[str]:
    """Return the next N YYYY-MM labels after last_period."""
    try:
        yr, mo = int(last_period[:4]), int(last_period[5:7])
    except (ValueError, IndexError):
        return []
    result: list[str] = []
    for _ in range(n):
        mo += 1
        if mo > 12:
            mo, yr = 1, yr + 1
        result.append(f"{yr}-{mo:02d}")
    return result


def _add_months(yr: int, mo: int, delta: int) -> tuple[int, int]:
    mo0 = mo - 1 + delta
    return yr + mo0 // 12, mo0 % 12 + 1


# ── Strategy selector ───────────────────────────────────────────────────

def select_strategy(history_months: int) -> str:
    if history_months < 6:
        return STRATEGY_BASELINE
    if history_months < 18:
        return STRATEGY_ETS
    if history_months < 36:
        return STRATEGY_RIDGE
    return STRATEGY_SARIMAX


# ── Backtest helpers ────────────────────────────────────────────────────

def _compute_backtest_metrics(
    actuals: list[float],
    predictions: list[float],
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (MAE, RMSE, MAPE) for paired actual/prediction lists."""
    if not actuals or len(actuals) != len(predictions):
        return None, None, None
    n = len(actuals)
    errors = [abs(a - p) for a, p in zip(actuals, predictions)]
    mae = sum(errors) / n
    rmse = math.sqrt(sum((a - p) ** 2 for a, p in zip(actuals, predictions)) / n)
    non_zero = [(a, p) for a, p in zip(actuals, predictions) if a != 0]
    mape = sum(abs(a - p) / abs(a) for a, p in non_zero) / len(non_zero) if non_zero else None
    return mae, rmse, mape


def _confidence_bounds(
    values: list[float],
    mae: Optional[float],
    ci: float = 0.80,
) -> tuple[list[float], list[float]]:
    """Simple symmetric bounds using MAE-based interval."""
    z = {0.80: 1.282, 0.90: 1.645, 0.95: 1.960}.get(ci, 1.282)
    spread = (mae or 0.0) * z
    lower = [max(0.0, v - spread) for v in values]
    upper = [v + spread for v in values]
    return lower, upper


# ── Strategy implementations ─────────────────────────────────────────────

def _forecast_baseline(
    history: list[float],
    horizon: int,
    periods: list[str],
) -> tuple[list[float], Optional[float], Optional[float], Optional[float], list[str]]:
    """Moving average of last 3 periods (or fewer) applied flat."""
    window = history[-3:] if len(history) >= 3 else history
    forecast_val = sum(window) / len(window) if window else 0.0
    values = [round(forecast_val, 2)] * horizon
    # Backtest: last 2 actuals vs. prior-window average
    mae, rmse, mape = None, None, None
    if len(history) >= 4:
        bt_actuals = history[-2:]
        bt_preds = [sum(history[-4:-2]) / 2] * 2
        mae, rmse, mape = _compute_backtest_metrics(bt_actuals, bt_preds)
    assumptions = [
        f"Baseline: moving average of last {len(window)} months applied flat for {horizon} months.",
        f"Average value used: ${forecast_val:,.2f}.",
    ]
    return values, mae, rmse, mape, assumptions


def _forecast_ets(
    history: list[float],
    horizon: int,
    periods: list[str],
) -> tuple[list[float], Optional[float], Optional[float], Optional[float], list[str]]:
    """Holt-Winters ETS via statsmodels (no seasonal if < 24m)."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        import pandas as pd
        n = len(history)
        seasonal = "add" if n >= 24 else None
        seasonal_periods = 12 if seasonal else None
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                history,
                trend="add",
                seasonal=seasonal,
                seasonal_periods=seasonal_periods,
                initialization_method="estimated",
            ).fit(optimized=True)
        forecast = list(model.forecast(horizon))
        # Backtest: last 3-period hold-out
        bt_len = min(3, n // 4)
        mae, rmse, mape = None, None, None
        if bt_len >= 2 and n - bt_len >= 6:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                bt_model = ExponentialSmoothing(
                    history[: n - bt_len],
                    trend="add",
                    seasonal=seasonal,
                    seasonal_periods=seasonal_periods,
                    initialization_method="estimated",
                ).fit(optimized=True)
            bt_preds = list(bt_model.forecast(bt_len))
            mae, rmse, mape = _compute_backtest_metrics(history[-bt_len:], bt_preds)
        assumptions = [
            f"ETS (Holt-Winters): trend=additive, seasonal={'additive' if seasonal else 'none'}.",
            f"History: {n} months. Horizon: {horizon} months.",
        ]
        return [max(0.0, round(v, 2)) for v in forecast], mae, rmse, mape, assumptions
    except Exception as exc:  # fallback to baseline if ETS fails
        values, mae, rmse, mape, assumptions = _forecast_baseline(history, horizon, periods)
        assumptions.append(f"ETS failed ({exc}); fell back to baseline.")
        return values, mae, rmse, mape, assumptions


def _forecast_ridge(
    history: list[float],
    horizon: int,
    periods: list[str],
) -> tuple[list[float], Optional[float], Optional[float], Optional[float], list[str]]:
    """Ridge regression with month-index + month-of-year features."""
    try:
        from sklearn.linear_model import Ridge
        n = len(history)
        X = np.array([[i, (i % 12) + 1, ((i % 12) + 1) ** 2] for i in range(n)])
        y = np.array(history)
        model = Ridge(alpha=1.0)
        model.fit(X, y)
        forecast_X = np.array([
            [n + j, ((n + j) % 12) + 1, (((n + j) % 12) + 1) ** 2]
            for j in range(horizon)
        ])
        forecast = [max(0.0, round(v, 2)) for v in model.predict(forecast_X)]
        # Backtest: 3-period hold-out
        bt_len = min(3, n // 5)
        mae, rmse, mape = None, None, None
        if bt_len >= 2:
            X_tr, y_tr = X[: n - bt_len], y[: n - bt_len]
            Ridge(alpha=1.0).fit(X_tr, y_tr)
            bt_preds = list(Ridge(alpha=1.0).fit(X_tr, y_tr).predict(X[n - bt_len:]))
            mae, rmse, mape = _compute_backtest_metrics(list(y[n - bt_len:]), bt_preds)
        assumptions = [
            "Ridge regression with month-index, month-of-year, and quadratic seasonal features.",
            f"History: {n} months. α=1.0.",
        ]
        return forecast, mae, rmse, mape, assumptions
    except Exception as exc:
        values, mae, rmse, mape, assumptions = _forecast_ets(history, horizon, periods)
        assumptions.append(f"Ridge failed ({exc}); fell back to ETS.")
        return values, mae, rmse, mape, assumptions


def _forecast_sarimax(
    history: list[float],
    horizon: int,
    periods: list[str],
) -> tuple[list[float], Optional[float], Optional[float], Optional[float], list[str]]:
    """SARIMAX(1,1,1)(1,0,1)[12] for long histories."""
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX
        n = len(history)
        with _warnings.catch_warnings():
            _warnings.simplefilter("ignore")
            model = SARIMAX(
                history,
                order=(1, 1, 1),
                seasonal_order=(1, 0, 1, 12),
                enforce_stationarity=False,
                enforce_invertibility=False,
            ).fit(disp=False, maxiter=200)
        forecast_obj = model.get_forecast(steps=horizon)
        raw_forecast = list(forecast_obj.predicted_mean)

        # Numerical sanity guard: SARIMAX can diverge on ill-conditioned series.
        # Cap any single predicted value at 20× the maximum observed monthly value.
        max_observed = max(abs(v) for v in history) if history else 1.0
        sarimax_cap = max_observed * 20.0
        raw_forecast = [min(max(0.0, v), sarimax_cap) for v in raw_forecast]

        forecast = [round(v, 2) for v in raw_forecast]
        # Backtest hold-out
        bt_len = min(6, n // 6)
        mae, rmse, mape = None, None, None
        if bt_len >= 3 and n - bt_len >= 24:
            with _warnings.catch_warnings():
                _warnings.simplefilter("ignore")
                bt_model = SARIMAX(
                    history[: n - bt_len],
                    order=(1, 1, 1),
                    seasonal_order=(1, 0, 1, 12),
                    enforce_stationarity=False,
                    enforce_invertibility=False,
                ).fit(disp=False, maxiter=200)
            bt_preds = list(bt_model.get_forecast(bt_len).predicted_mean)
            mae, rmse, mape = _compute_backtest_metrics(history[-bt_len:], bt_preds)
        assumptions = [
            "SARIMAX(1,1,1)(1,0,1)[12]: differenced, ARMA(1,1) + seasonal ARMA(1,1) at 12-month lag.",
            f"History: {n} months. Horizon: {horizon} months.",
        ]
        return forecast, mae, rmse, mape, assumptions
    except Exception as exc:
        values, mae, rmse, mape, assumptions = _forecast_ridge(history, horizon, periods)
        assumptions.append(f"SARIMAX failed ({exc}); fell back to Ridge.")
        return values, mae, rmse, mape, assumptions


# ── Public API ───────────────────────────────────────────────────────────

def forecast(
    history: list[float],
    history_periods: list[str],
    horizon: int = 6,
    forecast_type: str = "revenue",
    scenario: str = SCENARIO_BASE,
    confidence_interval: float = 0.80,
    strategy_override: Optional[str] = None,
) -> ForecastResult:
    """
    Generate a forecast from historical monthly values.

    Parameters
    ----------
    history          : list of monthly floats, oldest first
    history_periods  : matching YYYY-MM period labels
    horizon          : number of future months to forecast (default 6)
    forecast_type    : one of FORECAST_TYPES
    scenario         : one of SCENARIO_* constants
    confidence_interval : 0.80 | 0.90 | 0.95
    strategy_override : force a specific strategy (testing/debugging)
    """
    warn: list[str] = []

    if forecast_type not in FORECAST_TYPES:
        warn.append(f"Unknown forecast_type '{forecast_type}'; treated as 'revenue'.")
        forecast_type = "revenue"

    scenario_mult = SCENARIO_MULTIPLIERS.get(scenario, 1.0)
    if scenario not in SCENARIO_MULTIPLIERS:
        warn.append(f"Unknown scenario '{scenario}'; using 'base' (×1.0).")
        scenario = SCENARIO_BASE

    n = len(history)
    strategy = strategy_override or select_strategy(n)

    # Derive future period labels
    last_period = history_periods[-1] if history_periods else "2024-01"
    future_periods = _next_periods(last_period, horizon)

    # Dispatch to strategy
    dispatch = {
        STRATEGY_BASELINE: _forecast_baseline,
        STRATEGY_ETS:      _forecast_ets,
        STRATEGY_RIDGE:    _forecast_ridge,
        STRATEGY_SARIMAX:  _forecast_sarimax,
    }
    fn = dispatch.get(strategy, _forecast_baseline)

    if n == 0:
        values_raw = [0.0] * horizon
        mae = rmse = mape = None
        assumptions = ["No history available; forecast is zero."]
        warn.append("Empty history provided.")
    else:
        values_raw, mae, rmse, mape, assumptions = fn(history, horizon, future_periods)

    # Apply scenario multiplier
    values = [round(v * scenario_mult, 2) for v in values_raw]
    if scenario != SCENARIO_BASE:
        assumptions.append(
            f"Scenario '{scenario}' applied: ×{scenario_mult:.2f} multiplier on base forecast."
        )

    # Confidence bounds
    lower, upper = _confidence_bounds(values, mae, confidence_interval)

    return ForecastResult(
        forecast_type=forecast_type,
        scenario=scenario,
        strategy_used=strategy,
        periods=future_periods,
        values=values,
        lower_bound=lower,
        upper_bound=upper,
        backtest_mae=mae,
        backtest_rmse=rmse,
        backtest_mape=mape,
        confidence_interval=confidence_interval,
        assumptions=assumptions,
        warnings=warn,
        history_months=n,
        horizon_months=horizon,
    )


def forecast_multi_scenario(
    history: list[float],
    history_periods: list[str],
    horizon: int = 6,
    forecast_type: str = "revenue",
    scenarios: Optional[list[str]] = None,
    confidence_interval: float = 0.80,
) -> dict[str, ForecastResult]:
    """Run forecast for multiple scenarios at once."""
    scenarios = scenarios or [SCENARIO_BASE, SCENARIO_OPTIMISTIC, SCENARIO_CONSERVATIVE]
    return {
        s: forecast(
            history, history_periods, horizon, forecast_type, s, confidence_interval
        )
        for s in scenarios
    }
