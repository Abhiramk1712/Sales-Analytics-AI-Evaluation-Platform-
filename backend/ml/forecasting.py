"""
ml/forecasting.py
=================
Revenue Forecasting Model
--------------------------
Uses an ensemble of:
  1. SARIMAX  — captures seasonality and trend
  2. Ridge Regression — uses engineered lag/calendar features
  3. GradientBoostingRegressor — non-linear pattern capture (C1d)

Academic note: This demonstrates time-series forecasting for sales data,
including feature engineering, model evaluation (RMSE, MAE, MAPE), and
confidence interval construction.

C1 enhancements:
  C1a: Bootstrap residual CIs (asymmetric p10/p90)
  C1b: Three-lane forecast — Commit / Base / Best Case (quantile regression)
  C1d: GBR added to SARIMAX+Ridge ensemble
"""
import numpy as np
import pandas as pd
from typing import Optional
from dataclasses import dataclass, field
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error, mean_squared_error
from statsmodels.tsa.statespace.sarimax import SARIMAX
from backend.ml.evaluation import rolling_origin_backtest
from backend.ml.model_registry import MODEL_REVENUE_FORECAST
import warnings
warnings.filterwarnings("ignore")

_FORECAST_MODEL_VERSION = "ensemble_v3"  # bumped for C1d


@dataclass
class ForecastResult:
    periods:          list[str]
    forecast:         list[float]
    lower_ci:         list[float]
    upper_ci:         list[float]
    commit_lane:      list[float]   = field(default_factory=list)  # C1b p20
    best_case_lane:   list[float]   = field(default_factory=list)  # C1b p80
    ensemble_weights: dict          = field(default_factory=dict)
    metrics:          dict          = field(default_factory=dict)
    model_info:       str           = ""


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
    min_periods_sarimax: int = 12
    min_periods_ridge: int = 6

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
    def predict(self) -> ForecastResult:
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

        # ── C1a: Bootstrap residual CIs (500 resamples, asymmetric p10/p90) ─
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

        # ── C1b: Three-lane forecast (Commit / Base / Best Case) ─────────
        # Commit = p20, Base = p50 (median), Best Case = p80
        commit     = np.percentile(boot_forecasts, 20, axis=0)
        best_case  = np.percentile(boot_forecasts, 80, axis=0)
        # Base lane is the ensemble point estimate (already computed as `ensemble`)

        # Generate future period labels
        last_period = self.series_.index[-1]
        try:
            last_dt = pd.Period(last_period, "M")
            periods = [(last_dt + i + 1).strftime("%Y-%m") for i in range(self.horizon)]
        except Exception:
            periods = [f"T+{i+1}" for i in range(self.horizon)]

        result = ForecastResult(
            periods          = periods,
            forecast         = [round(float(v), 2) for v in ensemble],
            lower_ci         = [round(float(v), 2) for v in lower_ci],
            upper_ci         = [round(float(v), 2) for v in upper_ci],
            commit_lane      = [round(float(v), 2) for v in commit],
            best_case_lane   = [round(float(v), 2) for v in best_case],
            ensemble_weights = {
                "sarimax": float(self.w_sarimax),
                "ridge":   float(self.w_ridge),
                "gbr":     float(self.w_gbr),
            },
            metrics          = self.metrics_,
            model_info       = self._model_info,
        )
        if warning:
            result.model_info = f"{result.model_info} | {warning}"
        return result


# ── Convenience function used by the API ──────────────────────────────────
def run_revenue_forecast(revenue_by_period: dict[str, float], horizon: int = 6) -> dict:
    """
    Parameters
    ----------
    revenue_by_period : {"2024-01": 120000.0, ...}  sorted oldest→newest
    horizon           : months ahead to forecast
    """
    if not revenue_by_period:
        raise ValueError("No historical revenue data available.")

    history_len = len(revenue_by_period)
    if history_len < 6:
        last_value = float(list(revenue_by_period.values())[-1])
        periods = []
        try:
            last_dt = pd.Period(list(revenue_by_period.keys())[-1], "M")
            periods = [(last_dt + i + 1).strftime("%Y-%m") for i in range(horizon)]
        except Exception:
            periods = [f"T+{i+1}" for i in range(horizon)]

        baseline = [round(last_value, 2) for _ in range(horizon)]
        lower = [round(last_value * 0.8, 2) for _ in range(horizon)]
        upper = [round(last_value * 1.2, 2) for _ in range(horizon)]
        commit_l    = [round(last_value * 0.85, 2) for _ in range(horizon)]
        bestcase_l  = [round(last_value * 1.15, 2) for _ in range(horizon)]
        return {
            "historical": {p: v for p, v in revenue_by_period.items()},
            "forecast_periods": periods,
            "forecast_values": baseline,
            "lower_ci": lower,
            "upper_ci": upper,
            "commit_lane":    commit_l,
            "best_case_lane": bestcase_l,
            "ensemble_weights": {"sarimax": 0.0, "ridge": 0.0, "gbr": 0.0, "baseline": 1.0},
            "model_metrics": {},
            "model_info": "Baseline carry-forward (< 6 months history)",
            "model_used": "carry_forward",
            "metadata": {
                "forecast_mode": "baseline",
                "history_months": history_len,
                "confidence": "low",
            },
            "warnings": ["Fewer than 6 months of history. Using carry-forward baseline."],
        }

    if history_len < 24:
        # Use linear trend extrapolation for 6-23 months of data
        import numpy as np
        values = list(revenue_by_period.values())
        x = np.arange(len(values), dtype=float)
        y = np.array(values, dtype=float)
        try:
            slope, intercept = np.polyfit(x, y, 1)
        except Exception:
            slope, intercept = 0.0, float(values[-1])

        periods = []
        try:
            last_dt = pd.Period(list(revenue_by_period.keys())[-1], "M")
            periods = [(last_dt + i + 1).strftime("%Y-%m") for i in range(horizon)]
        except Exception:
            periods = [f"T+{i+1}" for i in range(horizon)]

        baseline = []
        lower = []
        upper = []
        commit_l = []
        bestcase_l = []
        for i in range(horizon):
            proj = max(0.0, intercept + slope * (len(values) + i))
            ci_spread = 0.15 + 0.03 * i  # widen CI per period
            baseline.append(round(proj, 2))
            lower.append(round(proj * (1 - ci_spread), 2))
            upper.append(round(proj * (1 + ci_spread), 2))
            commit_l.append(round(proj * (1 - ci_spread * 0.6), 2))
            bestcase_l.append(round(proj * (1 + ci_spread * 0.6), 2))

        return {
            "historical": {p: v for p, v in revenue_by_period.items()},
            "forecast_periods": periods,
            "forecast_values": baseline,
            "lower_ci": lower,
            "upper_ci": upper,
            "commit_lane":    commit_l,
            "best_case_lane": bestcase_l,
            "ensemble_weights": {"sarimax": 0.0, "ridge": 0.0, "gbr": 0.0, "trend": 1.0},
            "model_metrics": {"slope_per_month": round(float(slope), 2)},
            "model_info": "Linear trend extrapolation (6-23 months history)",
            "model_used": "linear_trend",
            "metadata": {
                "forecast_mode": "trend",
                "history_months": history_len,
                "confidence": "medium",
            },
            "warnings": [f"Using linear trend with {history_len} months of history. Full ensemble requires 24+ months."],
        }

    series = pd.Series(revenue_by_period)
    # Ensure proper monthly PeriodIndex so SARIMAX uses correct frequency
    try:
        series.index = pd.PeriodIndex(series.index, freq="M").to_timestamp()
    except Exception:
        series.index = pd.Index(series.index)  # fall back gracefully

    # Fill any missing months by forward-filling (warn if gap detected)
    gap_warnings: list[str] = []
    if isinstance(series.index, pd.DatetimeIndex):
        try:
            full_range = pd.date_range(series.index.min(), series.index.max(), freq="MS")
            if len(full_range) > len(series):
                n_gaps = len(full_range) - len(series)
                gap_warnings.append(
                    f"[DATA GAP] {n_gaps} missing month(s) detected in revenue history; "
                    "forward-filled for forecasting accuracy."
                )
                series = series.reindex(full_range).ffill()
        except Exception:
            pass

    # Consolidation Phase 2: the SARIMAX+Ridge+GBR ensemble now lives in
    # forecasting_engine.py (moved verbatim in Phase 1; numeric parity against
    # this module's own original class is pinned by
    # test_ensemble_strategy_matches_forecasting_py_original_numerically in
    # tests/test_forecasting_engine.py). This is the only branch of
    # run_revenue_forecast that used the class — the <6mo and 6-23mo branches
    # above are hand-rolled and untouched. Local import: forecasting_engine.py
    # does not import this module, so this stays a one-way dependency rather
    # than a cycle, and importing it only where it's used keeps every other
    # code path in this file free of the added import cost.
    from backend.ml.forecasting_engine import RevenueForecastModel as _EnsembleModel
    model = _EnsembleModel(horizon=horizon).fit(series)
    result = model.predict()
    # periods: computed the same way RevenueForecastModel's own predict() used
    # to compute them internally (last fitted period + 1..horizon) — the new
    # class's result object no longer carries periods itself (see
    # forecasting_engine._EnsembleFit), so this reproduces that exact logic
    # against model.series_ (the same fitted series) rather than duplicating
    # a second, potentially-diverging period-generation implementation.
    try:
        last_dt = pd.Period(model.series_.index[-1], "M")
        periods = [(last_dt + i + 1).strftime("%Y-%m") for i in range(horizon)]
    except Exception:
        periods = [f"T+{i+1}" for i in range(horizon)]
    backtest = rolling_origin_backtest(series)
    warnings_list: list[str] = gap_warnings[:]
    if backtest.get("status") != "ok":
        warnings_list.extend(backtest.get("warnings", []))
    if history_len < 24:
        warnings_list.append(f"Only {history_len} months of history; forecasts are low-confidence.")

    confidence = "high" if history_len >= 36 else ("medium" if history_len >= 24 else "low")

    return {
        "historical":        {p: v for p, v in revenue_by_period.items()},
        "forecast_periods":  periods,
        "forecast_values":   result.values,
        "lower_ci":          result.lower_ci,
        "upper_ci":          result.upper_ci,
        "commit_lane":       result.commit,       # C1b
        "best_case_lane":    result.best_case,    # C1b
        "ensemble_weights":  result.weights,
        "model_metrics":     result.metrics,
        "model_info":        result.model_info,
        # The other two branches (<6mo baseline, 6-23mo trend) both include
        # this key; this branch didn't, so a caller reading result["model_used"]
        # got a value for short/medium history and silently None for the
        # actual full ensemble — the one case it matters most for. No caller
        # reads it today (checked), but the three branches promise the same
        # shape and this is the one place that broke it.
        "model_used":        _FORECAST_MODEL_VERSION,
        "metadata": {
            "forecast_mode": "model",
            "history_months": history_len,
            "horizon": horizon,
            "model_name": MODEL_REVENUE_FORECAST,
            "model_version": _FORECAST_MODEL_VERSION,
            "confidence": confidence,
            "backtest_mae": backtest.get("mae"),
            "backtest_rmse": backtest.get("rmse"),
            "backtest_mape": backtest.get("mape"),
            "bias": backtest.get("bias"),
            "confidence_level": "90%",
            "generated_at": pd.Timestamp.now().isoformat(),
        },
        "warnings": warnings_list,
    }


def build_arr_waterfall(
    revenue_by_period: dict[str, float],
    revenue_by_type: dict[str, dict[str, float]] | None = None,
) -> dict:
    """
    Compute an ARR waterfall decomposition from monthly revenue data.

    Parameters
    ----------
    revenue_by_period : {"2024-01": 120000.0, ...}
    revenue_by_type   : {"2024-01": {"new_logo": 20000, "expansion": 5000, ...}, ...}
                        If None, approximates components from total revenue.

    Returns
    -------
    dict with:
        periods          : list of YYYY-MM strings
        arr_start        : starting ARR for each period
        arr_end          : ending ARR for each period
        new_logo         : new logo MRR × 12
        expansion        : expansion ARR
        contraction      : contraction ARR (negative)
        churn            : churn ARR (negative)
        renewal          : renewal ARR
        net_new_arr      : new_logo + expansion + contraction + churn
        nrr_rolling_12m  : rolling 12-month NRR %
    """
    if not revenue_by_period:
        return {"periods": [], "arr_start": [], "arr_end": [], "net_new_arr": [], "nrr_rolling_12m": []}

    periods_sorted = sorted(revenue_by_period.keys())
    totals = [revenue_by_period[p] for p in periods_sorted]

    # If typed data is provided, use it; otherwise approximate
    if revenue_by_type:
        new_logos     = [float(revenue_by_type.get(p, {}).get("new_logo", 0)) * 12 for p in periods_sorted]
        expansions    = [float(revenue_by_type.get(p, {}).get("expansion", 0)) * 12 for p in periods_sorted]
        contractions  = [-abs(float(revenue_by_type.get(p, {}).get("contraction", 0)) * 12) for p in periods_sorted]
        churns        = [-abs(float(revenue_by_type.get(p, {}).get("churn", 0)) * 12) for p in periods_sorted]
        renewals      = [float(revenue_by_type.get(p, {}).get("renewal", 0)) * 12 for p in periods_sorted]
    else:
        # Approximations based on industry benchmarks
        new_logos     = [round(t * 0.15, 2) for t in totals]
        expansions    = [round(t * 0.12, 2) for t in totals]
        contractions  = [round(-t * 0.04, 2) for t in totals]
        churns        = [round(-t * 0.05, 2) for t in totals]
        renewals      = [round(t * 0.78, 2) for t in totals]

    arr_start = []
    arr_end = []
    for i, total in enumerate(totals):
        start = total * 12
        arr_start.append(round(start, 2))
        arr_end.append(round(start + new_logos[i] + expansions[i] + contractions[i] + churns[i], 2))

    net_new_arr = [round(new_logos[i] + expansions[i] + contractions[i] + churns[i], 2) for i in range(len(periods_sorted))]

    # Rolling 12-month NRR
    nrr_12m = []
    for i in range(len(periods_sorted)):
        if i < 11:
            nrr_12m.append(None)
            continue
        window_start_arr = sum(arr_start[max(0, i - 11):i + 1]) / 12
        window_exp = sum(expansions[max(0, i - 11):i + 1])
        window_con = sum(contractions[max(0, i - 11):i + 1])
        window_churn = sum(churns[max(0, i - 11):i + 1])
        if window_start_arr > 0:
            nrr = round((window_start_arr + window_exp + window_con + window_churn) / window_start_arr * 100, 2)
        else:
            nrr = None
        nrr_12m.append(nrr)

    return {
        "periods":         periods_sorted,
        "arr_start":       arr_start,
        "arr_end":         arr_end,
        "new_logo":        new_logos,
        "expansion":       expansions,
        "contraction":     contractions,
        "churn":           churns,
        "renewal":         renewals,
        "net_new_arr":     net_new_arr,
        "nrr_rolling_12m": nrr_12m,
    }

