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

"ensemble" is a fifth strategy, reachable only via an explicit
strategy_override — select_strategy()'s automatic history-length selection
above never picks it. It's the SARIMAX+Ridge+GBR ensemble that used to live
solely in backend/ml/forecasting.py (RevenueForecastModel, moved here
verbatim — same feature engineering, same fit/predict math, same bootstrap
residual CIs). run_revenue_forecast() in that module now delegates to
forecast(strategy_override="ensemble") for its full-history (>=24mo) branch
instead of keeping a second copy of this logic. See CLAUDE.md's payout-forecast
history for why two independently-maintained forecasters was worth collapsing:
GET /payout/forecast read the wrong shape off one of them for an unknown
period before anyone noticed.

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
  scenario, forecast_type, warnings, and — populated only by the "ensemble"
  strategy, empty for the other four — commit_values, best_case_values,
  component_weights.
"""
from __future__ import annotations

import math
import warnings as _warnings
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.preprocessing import StandardScaler

# ── Constants ────────────────────────────────────────────────────────────

STRATEGY_BASELINE = "baseline"
STRATEGY_ETS      = "ets"
STRATEGY_RIDGE    = "ridge"
STRATEGY_SARIMAX  = "sarimax"
STRATEGY_ENSEMBLE = "ensemble"  # override-only — never auto-selected, see select_strategy()

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
    # Populated only by the "ensemble" strategy (see module docstring); every
    # other strategy leaves these empty. Kept on the shared dataclass rather
    # than a separate result type so every strategy still returns the one
    # ForecastResult shape — the whole point of this module existing.
    commit_values:      list[float]         = field(default_factory=list)
    best_case_values:   list[float]         = field(default_factory=list)
    component_weights:  dict[str, float]    = field(default_factory=dict)

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
            "commit_values":       [round(v, 2) for v in self.commit_values],
            "best_case_values":    [round(v, 2) for v in self.best_case_values],
            "component_weights":   self.component_weights,
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


# ── Ensemble strategy (moved from backend/ml/forecasting.py) ─────────────
#
# Everything in this section — feature engineering, fit, predict, the
# bootstrap-CI math — is an unmodified relocation of what was
# RevenueForecastModel in forecasting.py. Behavior is pinned by a captured
# before/after output diff (see the branch this landed on), not just visual
# comparison: moving ~250 lines of stochastic model code by hand is exactly
# the kind of change that's easy to get subtly wrong.

class _EnsembleFit:
    """Internal fit/predict result for the ensemble strategy — deliberately
    not named ForecastResult to avoid colliding with this module's own
    dataclass of that name; _forecast_ensemble_full() below is what actually
    builds a ForecastResult from this."""
    def __init__(self, values, lower_ci, upper_ci, commit, best_case, weights, metrics, model_info):
        self.values = values
        self.lower_ci = lower_ci
        self.upper_ci = upper_ci
        self.commit = commit
        self.best_case = best_case
        self.weights = weights
        self.metrics = metrics
        self.model_info = model_info


class RevenueForecastModel:
    """
    Ensemble revenue forecasting model.

    Steps
    -----
    1. Feature engineering (lags, rolling stats, calendar features)
    2. SARIMAX(1,1,1)(1,0,1,12) for trend + seasonality
    3. Ridge regression on engineered features
    4. Weighted ensemble with inverse-RMSE weights
    5. Bootstrap confidence intervals
    """

    min_periods_sarimax: int = 12
    min_periods_ridge: int = 6

    def __init__(self, horizon: int = 6, alpha: float = 0.3):
        self.horizon = horizon
        self.alpha   = alpha          # Ridge regularisation
        self.scaler  = StandardScaler()
        self._fitted = False

    # ── Feature Engineering ────────────────────────────────────────────────
    def _build_features(self, series: pd.Series, short_mode: bool = False) -> pd.DataFrame:
        df = pd.DataFrame({"y": series})
        df["lag_1"]  = df["y"].shift(1)
        df["lag_2"]  = df["y"].shift(2)
        df["lag_3"]  = df["y"].shift(3)
        if not short_mode and len(series) >= 13:
            df["lag_12"] = df["y"].shift(12)
        roll_window = min(3, len(series) - 1)
        df["roll_3"] = df["y"].shift(1).rolling(max(2, roll_window)).mean()
        roll6_window = min(6, len(series) - 1)
        df["roll_6"] = df["y"].shift(1).rolling(max(2, roll6_window)).mean()
        df["month"]  = series.index.month if hasattr(series.index, "month") else range(len(series))
        df["trend"]  = np.arange(len(df))
        df["trend_sq"] = df["trend"] ** 2
        return df.dropna()

    # ── Fit ────────────────────────────────────────────────────────────────
    def fit(self, revenue_series: pd.Series):
        """
        Parameters
        ----------
        revenue_series : pd.Series
            Monthly revenue indexed by period string 'YYYY-MM', sorted ascending.
        """
        self.series_ = revenue_series.astype(float)
        n = len(self.series_)

        if n < self.min_periods_ridge:
            raise ValueError(
                f"Insufficient data: need at least {self.min_periods_ridge} months, got {n}."
            )

        test_size = min(3, max(1, n // 5))
        train, test = self.series_.iloc[:-test_size], self.series_.iloc[-test_size:]

        # ── SARIMAX (only when enough history) ───────────────────────────
        if n >= self.min_periods_sarimax:
            try:
                from statsmodels.tsa.statespace.sarimax import SARIMAX
                self.sarimax_ = SARIMAX(
                    train, order=(1, 1, 1), seasonal_order=(1, 0, 1, 12),
                    enforce_stationarity=False, enforce_invertibility=False
                ).fit(disp=False)
                sarimax_pred = self.sarimax_.forecast(test_size)
                sarimax_rmse = float(np.sqrt(mean_squared_error(test, sarimax_pred)))
            except Exception:
                self.sarimax_ = None
                sarimax_rmse  = float("inf")
        else:
            # Not enough data for SARIMAX — use Ridge only
            self.sarimax_ = None
            sarimax_rmse  = float("inf")

        # ── Ridge regression ─────────────────────────────────────────────
        short_mode = n < self.min_periods_sarimax
        feat_df = self._build_features(self.series_, short_mode=short_mode)
        X = feat_df.drop(columns=["y"]).values
        y = feat_df["y"].values
        # Guard: if test_size > available rows, shrink
        actual_test = min(test_size, len(X) - 1)
        if actual_test < 1:
            actual_test = 1
        X_train, X_test = X[:-actual_test], X[-actual_test:]
        y_train, y_test = y[:-actual_test], y[-actual_test:]
        X_train_s = self.scaler.fit_transform(X_train)
        X_test_s  = self.scaler.transform(X_test)
        self.ridge_ = Ridge(alpha=self.alpha).fit(X_train_s, y_train)
        ridge_pred  = self.ridge_.predict(X_test_s)
        ridge_rmse  = float(np.sqrt(mean_squared_error(y_test, ridge_pred)))

        # ── GBR (C1d) ─────────────────────────────────────────────────────
        gbr_rmse = float("inf")
        self.gbr_ = None
        if len(X_train) >= 8:
            try:
                from sklearn.ensemble import GradientBoostingRegressor
                self.gbr_ = GradientBoostingRegressor(
                    n_estimators=100, max_depth=3, learning_rate=0.05,
                    subsample=0.8, random_state=42,
                ).fit(X_train_s, y_train)
                gbr_pred = self.gbr_.predict(X_test_s)
                gbr_rmse = float(np.sqrt(mean_squared_error(y_test, gbr_pred)))
            except Exception:
                self.gbr_ = None

        # ── Ensemble weights (inverse RMSE) ──────────────────────────────
        rmse_map: dict[str, float] = {"ridge": ridge_rmse}
        if sarimax_rmse < float("inf"):
            rmse_map["sarimax"] = sarimax_rmse
        if gbr_rmse < float("inf"):
            rmse_map["gbr"] = gbr_rmse
        inv_sum = sum(1.0 / r for r in rmse_map.values())
        self.w_sarimax = (1.0 / rmse_map["sarimax"] / inv_sum) if "sarimax" in rmse_map else 0.0
        self.w_ridge   = (1.0 / rmse_map["ridge"]   / inv_sum)
        self.w_gbr     = (1.0 / rmse_map["gbr"]     / inv_sum) if "gbr"     in rmse_map else 0.0

        # ── Held-out metrics ─────────────────────────────────────────────
        ensemble_pred = self.w_ridge * ridge_pred
        if self.sarimax_ is not None:
            ensemble_pred = ensemble_pred + self.w_sarimax * np.array(sarimax_pred)
        if self.gbr_ is not None:
            ensemble_pred = ensemble_pred + self.w_gbr * self.gbr_.predict(X_test_s)
        self.metrics_ = {
            "MAE":  round(float(mean_absolute_error(test.values, ensemble_pred)), 2),
            "RMSE": round(float(np.sqrt(mean_squared_error(test.values, ensemble_pred))), 2),
            "MAPE": round(float(np.mean(np.abs((test.values - ensemble_pred) / (test.values + 1e-9))) * 100), 2),
        }
        components = ["Ridge"]
        if self.sarimax_ is not None:
            components.insert(0, "SARIMAX(1,1,1)(1,0,1,12)")
        if self.gbr_ is not None:
            components.append("GBR")
        self._model_info = " + ".join(components) + " ensemble"
        self._fitted = True
        return self

    # ── Predict ────────────────────────────────────────────────────────────
    def predict(self) -> _EnsembleFit:
        assert self._fitted, "Call .fit() first."

        n = len(self.series_)
        warning = f"WARNING: Only {n} months of data. Forecasts may be unreliable." if n < 24 else ""
        short_mode = n < self.min_periods_sarimax

        # SARIMAX forecast
        if self.sarimax_ is not None:
            sx_fc   = self.sarimax_.get_forecast(self.horizon)
            sx_mean = sx_fc.predicted_mean.values
        else:
            last    = float(self.series_.iloc[-1])
            sx_mean = np.array([last] * self.horizon)

        # Ridge iterative forecast
        history = list(self.series_.values)
        ridge_preds: list[float] = []
        for _ in range(self.horizon):
            tmp = pd.Series(history)
            feat_row = self._build_features(tmp, short_mode=short_mode).iloc[[-1]]
            X_row = self.scaler.transform(feat_row.drop(columns=["y"]).values)
            pred  = float(self.ridge_.predict(X_row)[0])
            ridge_preds.append(pred)
            history.append(pred)
        ridge_arr = np.array(ridge_preds)

        # GBR iterative forecast (C1d)
        gbr_arr = np.zeros(self.horizon)
        if self.gbr_ is not None:
            gbr_history = list(self.series_.values)
            for j in range(self.horizon):
                tmp = pd.Series(gbr_history)
                feat_row = self._build_features(tmp, short_mode=short_mode).iloc[[-1]]
                X_row = self.scaler.transform(feat_row.drop(columns=["y"]).values)
                pred = float(self.gbr_.predict(X_row)[0])
                gbr_arr[j] = pred
                gbr_history.append(pred)

        # Ensemble base forecast
        ensemble = self.w_sarimax * sx_mean + self.w_ridge * ridge_arr + self.w_gbr * gbr_arr
        ensemble = np.maximum(ensemble, 0.0)

        # ── Bootstrap residual CIs (500 resamples, asymmetric p10/p90) ────
        history_vals = self.series_.values
        # Use training residuals as the noise distribution
        feat_df_full = self._build_features(self.series_, short_mode=short_mode)
        X_full_s = self.scaler.transform(feat_df_full.drop(columns=["y"]).values)
        fitted_vals = (
            self.w_ridge * self.ridge_.predict(X_full_s)
            + (self.w_gbr * self.gbr_.predict(X_full_s) if self.gbr_ is not None else 0.0)
        )
        if self.sarimax_ is not None:
            sx_in_sample = self.sarimax_.fittedvalues.values[-len(fitted_vals):]
            fitted_vals = fitted_vals + self.w_sarimax * sx_in_sample
        residuals = history_vals[-len(fitted_vals):] - fitted_vals

        rng = np.random.default_rng(42)
        n_boot = 500
        boot_forecasts = np.zeros((n_boot, self.horizon))
        for b in range(n_boot):
            noise = rng.choice(residuals, size=self.horizon, replace=True)
            boot_forecasts[b] = np.maximum(ensemble + noise, 0.0)
        lower_ci = np.percentile(boot_forecasts, 10, axis=0)
        upper_ci = np.percentile(boot_forecasts, 90, axis=0)

        # ── Three-lane forecast (Commit / Base / Best Case) ───────────────
        # Commit = p20, Base = p50 (median), Best Case = p80
        commit     = np.percentile(boot_forecasts, 20, axis=0)
        best_case  = np.percentile(boot_forecasts, 80, axis=0)
        # Base lane is the ensemble point estimate (already computed as `ensemble`)

        result = _EnsembleFit(
            values    = [round(float(v), 2) for v in ensemble],
            lower_ci  = [round(float(v), 2) for v in lower_ci],
            upper_ci  = [round(float(v), 2) for v in upper_ci],
            commit    = [round(float(v), 2) for v in commit],
            best_case = [round(float(v), 2) for v in best_case],
            weights   = {
                "sarimax": float(self.w_sarimax),
                "ridge":   float(self.w_ridge),
                "gbr":     float(self.w_gbr),
            },
            metrics    = self.metrics_,
            model_info = self._model_info,
        )
        if warning:
            result.model_info = f"{result.model_info} | {warning}"
        return result


def _forecast_ensemble_full(
    history: list[float],
    history_periods: list[str],
    horizon: int,
) -> ForecastResult:
    """
    Fit and predict the SARIMAX+Ridge+GBR ensemble, returning a fully
    populated ForecastResult directly (not the 5-tuple contract the other
    _forecast_* strategy functions use) — the ensemble's own bootstrap CI and
    commit/best-case lanes are real information the generic post-dispatch
    _confidence_bounds() in forecast() would otherwise silently discard.
    Requires >= 6 months of history (RevenueForecastModel's own floor);
    forecast() is responsible for not calling this on shorter series.
    """
    series = pd.Series(dict(zip(history_periods, history))) if history_periods else pd.Series(history)
    try:
        series.index = pd.PeriodIndex(series.index, freq="M").to_timestamp()
    except Exception:
        series.index = pd.Index(series.index)

    model = RevenueForecastModel(horizon=horizon).fit(series)
    fit_result = model.predict()

    last_period = history_periods[-1] if history_periods else "2024-01"
    periods = _next_periods(last_period, horizon)

    return ForecastResult(
        forecast_type="revenue",
        scenario=SCENARIO_BASE,
        strategy_used=STRATEGY_ENSEMBLE,
        periods=periods,
        values=fit_result.values,
        lower_bound=fit_result.lower_ci,
        upper_bound=fit_result.upper_ci,
        backtest_mae=fit_result.metrics.get("MAE"),
        backtest_rmse=fit_result.metrics.get("RMSE"),
        backtest_mape=fit_result.metrics.get("MAPE"),
        assumptions=[fit_result.model_info],
        warnings=[],
        history_months=len(history),
        horizon_months=horizon,
        commit_values=fit_result.commit,
        best_case_values=fit_result.best_case,
        component_weights=fit_result.weights,
    )


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

    # The ensemble strategy returns a fully-populated ForecastResult directly
    # (real bootstrap CI, commit/best-case lanes) rather than going through
    # the generic 5-tuple dispatch + _confidence_bounds() below, which would
    # discard that in favor of a symmetric MAE-based CI. RevenueForecastModel
    # needs >= 6 months (its own floor) — below that, fall back to baseline
    # with a warning rather than let the ValueError propagate; forecast()'s
    # contract is "always return a ForecastResult", including for degenerate
    # input, same as every other strategy here.
    if strategy == STRATEGY_ENSEMBLE:
        if n < 6:
            # Don't compute a fallback result here — just warn and fall through
            # to the generic dispatch block below. `dispatch` (built further down)
            # has no "ensemble" key, so `dispatch.get(strategy, _forecast_baseline)`
            # already resolves to _forecast_baseline on its own; computing it here
            # too would just run the same deterministic function twice.
            warn.append(f"Ensemble strategy needs >= 6 months of history (got {n}); using baseline instead.")
        else:
            result = _forecast_ensemble_full(history, history_periods, horizon)
            values = [round(v * scenario_mult, 2) for v in result.values]
            commit = [round(v * scenario_mult, 2) for v in result.commit_values]
            best_case = [round(v * scenario_mult, 2) for v in result.best_case_values]
            assumptions = list(result.assumptions)
            if scenario != SCENARIO_BASE:
                assumptions.append(
                    f"Scenario '{scenario}' applied: ×{scenario_mult:.2f} multiplier on base forecast."
                )
            return ForecastResult(
                forecast_type=forecast_type,
                scenario=scenario,
                strategy_used=STRATEGY_ENSEMBLE,
                periods=result.periods,
                values=values,
                lower_bound=[round(v * scenario_mult, 2) for v in result.lower_bound],
                upper_bound=[round(v * scenario_mult, 2) for v in result.upper_bound],
                backtest_mae=result.backtest_mae,
                backtest_rmse=result.backtest_rmse,
                backtest_mape=result.backtest_mape,
                confidence_interval=confidence_interval,
                assumptions=assumptions,
                warnings=warn + list(result.warnings),
                history_months=n,
                horizon_months=horizon,
                commit_values=commit,
                best_case_values=best_case,
                component_weights=result.component_weights,
            )

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
