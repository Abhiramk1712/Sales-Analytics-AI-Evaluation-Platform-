"""
ml/forecasting.py
=================
Revenue forecast entry point used by the rest of the app: run_revenue_forecast()
(carry-forward / linear-trend / full-ensemble by history length).

The SARIMAX+Ridge+GBR ensemble model itself — RevenueForecastModel — used to
be defined in this module. It now lives in backend/ml/forecasting_engine.py
(moved verbatim; see that module's docstring and _EnsembleFit), and
run_revenue_forecast()'s full-ensemble branch delegates to it. This module
kept only what's still genuinely its own: the two hand-rolled short-history
fallback branches and the public dict-shaped API every real caller already
depends on.

build_arr_waterfall() used to live here too. It computed arr_start[period] =
total_revenue[period] * 12 independently for every period and then added
new_logo/expansion/contraction/churn again on top -- double-counting
components already inside that period's total_revenue, and never carrying
arr_end forward as the next period's arr_start. Both real callers
(backend/routers/forecasting.py's arr_waterfall() endpoint and
backend/agent/tools/revops_tools.py's get_arr_trajectory()) now use
backend.metrics.calculators.calc_arr_waterfall_series(), which reads the
canonical `arr_waterfall` DB table and has correct bridge continuity.
"""
import numpy as np
import pandas as pd
from backend.ml.evaluation import rolling_origin_backtest
from backend.ml.model_registry import MODEL_REVENUE_FORECAST
import warnings
warnings.filterwarnings("ignore")  # SARIMAX/GBR convergence warnings from the delegated ensemble fit

_FORECAST_MODEL_VERSION = "ensemble_v3"  # bumped for C1d


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

    # The SARIMAX+Ridge+GBR ensemble lives in forecasting_engine.py — this
    # module's own copy was deleted once this was the only remaining branch
    # that used it (pipeline_tools.py, the other caller, now imports the
    # forecasting_engine copy directly too). This is the only branch of
    # run_revenue_forecast that ever used it — the <6mo and 6-23mo branches
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

