"""
backend/ml/forecasting_lab/models.py
====================================
Forecast model adapters used by the forecasting lab.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from backend.ml.forecasting_engine import forecast as engine_forecast
from backend.ml.lstm_forecaster import run_lstm_forecast

from .datasets import future_periods_from_history


DEFAULT_MODELS = [
    "naive",
    "moving_average",
    "seasonal_naive",
    "ets",
    "holt_winters",
    "ridge",
    "sarimax",
]


SEASONAL_BUSINESS_PRIORS: dict[str, dict[int, float]] = {
    # Quarter month slots: 1=quarter start, 2=quarter middle, 3=quarter end.
    # Pipeline and payout typically accelerate into quarter-end close.
    "pipeline": {1: 0.90, 2: 1.00, 3: 1.12},
    "payout": {1: 0.88, 2: 1.00, 3: 1.14},
}


SEASONAL_SHAPE_CONSTRAINTS: dict[str, dict[str, float]] = {
    # These bounds damp abrupt quarter-boundary cliffs while preserving
    # quarter-end uplift expected for sales motion.
    "pipeline": {
        "mid_min_gap": 0.015,
        "mid_max_gap": 0.070,
        "end_min_gap": 0.020,
        "end_max_gap": 0.085,
        "compress": 0.72,
        "rollover_min_ratio": 0.86,
        "clip_low": 0.88,
        "clip_high": 1.16,
    },
    "payout": {
        "mid_min_gap": 0.020,
        "mid_max_gap": 0.090,
        "end_min_gap": 0.030,
        "end_max_gap": 0.105,
        "compress": 0.80,
        "rollover_min_ratio": 0.80,
        "clip_low": 0.86,
        "clip_high": 1.18,
    },
}


def candidate_models(include_lstm: bool = True) -> list[str]:
    models = list(DEFAULT_MODELS)
    if include_lstm:
        models.append("lstm")
    return models


def _bounds(values: list[float], pct: float = 0.1) -> tuple[list[float], list[float]]:
    lo = [round(max(0.0, float(v) * (1.0 - pct)), 2) for v in values]
    hi = [round(max(0.0, float(v) * (1.0 + pct)), 2) for v in values]
    return lo, hi


def _quarter_slot(period: str) -> int | None:
    try:
        month = int(str(period)[5:7])
    except Exception:
        return None
    if month < 1 or month > 12:
        return None
    return ((month - 1) % 3) + 1


def _quarter_seasonality_factors(
    history_values: list[float],
    history_periods: list[str],
    target: str,
) -> dict[int, float]:
    fallback = SEASONAL_BUSINESS_PRIORS.get(target, {1: 1.0, 2: 1.0, 3: 1.0})

    if not history_values or not history_periods:
        return fallback

    n = min(len(history_values), len(history_periods))
    recent_values = history_values[max(0, n - 36):n]
    recent_periods = history_periods[max(0, n - 36):n]

    buckets: dict[int, list[float]] = {1: [], 2: [], 3: []}
    for period, value in zip(recent_periods, recent_values):
        slot = _quarter_slot(period)
        if slot is None:
            continue
        val = float(value)
        if np.isfinite(val) and val >= 0.0:
            buckets[slot].append(val)

    pooled = [v for slot_vals in buckets.values() for v in slot_vals if v > 0.0]
    if len(pooled) < 6:
        return fallback

    pooled_median = float(np.median(pooled))
    if pooled_median <= 0.0:
        return fallback

    factors: dict[int, float] = {}
    # Blend learned factors with business priors to reduce volatility while
    # preserving company-specific seasonality.
    obs_count = len(pooled)
    alpha = float(np.clip(obs_count / 24.0, 0.35, 0.80))
    for slot in (1, 2, 3):
        prior = float(fallback[slot])
        if buckets[slot]:
            ratio = float(np.median(buckets[slot])) / pooled_median
            learned = float(np.clip(ratio, 0.82, 1.22))
            factors[slot] = float(alpha * learned + (1.0 - alpha) * prior)
        else:
            factors[slot] = prior

    # Sales/business guardrails: preserve quarter-end uplift while bounding
    # month-to-month jumps at quarter boundaries.
    if target in {"pipeline", "payout"}:
        cfg = SEASONAL_SHAPE_CONSTRAINTS[target]
        m1, m2, m3 = factors[1], factors[2], factors[3]

        m2 = max(m2, m1 + float(cfg["mid_min_gap"]))
        m2 = min(m2, m1 + float(cfg["mid_max_gap"]))

        m3 = max(m3, m2 + float(cfg["end_min_gap"]))
        m3 = min(m3, m2 + float(cfg["end_max_gap"]))

        factors = {1: m1, 2: m2, 3: m3}

    mean_factor = float(np.mean(list(factors.values())))
    if mean_factor <= 0.0:
        return fallback

    normalized = {slot: float(val / mean_factor) for slot, val in factors.items()}

    # Compress around 1.0 to reduce quarter-boundary cliffs in forward paths
    # while keeping relative shape (slot3 > slot2 > slot1).
    cfg = SEASONAL_SHAPE_CONSTRAINTS.get(target)
    if cfg is not None:
        compress = float(cfg["compress"])
        normalized = {slot: 1.0 + (val - 1.0) * compress for slot, val in normalized.items()}

        m1, m2, m3 = normalized[1], normalized[2], normalized[3]
        m2 = max(m2, m1 + float(cfg["mid_min_gap"]))
        m2 = min(m2, m1 + float(cfg["mid_max_gap"]))
        m3 = max(m3, m2 + float(cfg["end_min_gap"]))
        m3 = min(m3, m2 + float(cfg["end_max_gap"]))
        normalized = {1: m1, 2: m2, 3: m3}

        clip_low = float(cfg["clip_low"])
        clip_high = float(cfg["clip_high"])
    else:
        clip_low = 0.82
        clip_high = 1.22

    return {slot: float(np.clip(val, clip_low, clip_high)) for slot, val in normalized.items()}


def _apply_target_seasonality(
    base_values: list[float],
    history_values: list[float],
    history_periods: list[str],
    target: str,
) -> list[float]:
    if target not in {"pipeline", "payout"}:
        return base_values
    if not base_values:
        return base_values

    periods = future_periods_from_history(history_periods, len(base_values))
    factors = _quarter_seasonality_factors(history_values, history_periods, target=target)

    adjusted: list[float] = []
    for i, value in enumerate(base_values):
        slot = _quarter_slot(periods[i]) if i < len(periods) else None
        factor = factors.get(slot, 1.0) if slot is not None else 1.0
        adjusted.append(max(0.0, float(value) * float(factor)))

    # Preserve overall level while adding seasonal shape.
    base_mean = float(np.mean(base_values))
    adjusted_mean = float(np.mean(adjusted)) if adjusted else 0.0
    if adjusted_mean > 0.0:
        scale = base_mean / adjusted_mean
        adjusted = [max(0.0, v * scale) for v in adjusted]

    # Guard against unrealistic quarter rollover cliffs (slot3 -> slot1).
    cfg = SEASONAL_SHAPE_CONSTRAINTS.get(target)
    min_ratio = float((cfg or {}).get("rollover_min_ratio", 0.0))
    if min_ratio > 0.0:
        bounded = list(adjusted)
        for i in range(1, len(bounded)):
            prev_slot = _quarter_slot(periods[i - 1]) if (i - 1) < len(periods) else None
            curr_slot = _quarter_slot(periods[i]) if i < len(periods) else None
            if prev_slot == 3 and curr_slot == 1 and bounded[i - 1] > 0.0:
                bounded[i] = max(bounded[i], bounded[i - 1] * min_ratio)

        bounded_mean = float(np.mean(bounded)) if bounded else 0.0
        if bounded_mean > 0.0:
            scale = base_mean / bounded_mean
            adjusted = [max(0.0, v * scale) for v in bounded]
        else:
            adjusted = bounded

    return [round(float(v), 2) for v in adjusted]


def _naive_forecast(
    history_values: list[float],
    horizon: int,
    history_periods: list[str] | None = None,
    target: str = "revenue",
) -> list[float]:
    if not history_values:
        return [0.0] * horizon
    arr = np.asarray(history_values, dtype=float)
    last = float(arr[-1])

    # Keep the model simple, but avoid implausibly flat trajectories when recent
    # history shows directional movement.
    if len(arr) >= 4:
        # Use a 12-month window (or all available) for drift estimation.
        # Use MEDIAN of period-over-period deltas rather than mean — this makes
        # the forecast robust to spike months that inflate mean drift (CV > 1.0).
        window = min(len(arr) - 1, 12)
        deltas = np.diff(arr[-window - 1:])
        non_zero = [float(d) for d in deltas if abs(float(d)) > 1e-9]
        if non_zero:
            # Median drift is more stable when volatility is high.
            drift_median = float(np.median(non_zero))
            drift_mean = float(np.mean(non_zero))
            # Blend: use more median weight when the coefficient of variation is high.
            delta_std = float(np.std(non_zero))
            delta_mean_abs = max(abs(drift_mean), 1e-9)
            cv = delta_std / delta_mean_abs
            median_weight = float(np.clip(cv / 2.0, 0.3, 0.85))
            drift = median_weight * drift_median + (1.0 - median_weight) * drift_mean
            drift_cap = max(abs(last) * 0.08, 1.0)
            drift = float(np.clip(drift, -drift_cap, drift_cap))
            base = [max(last + drift * (i + 1), 0.0) for i in range(horizon)]
            return _apply_target_seasonality(
                base_values=base,
                history_values=history_values,
                history_periods=history_periods or [],
                target=target,
            )

    base = [max(last, 0.0) for _ in range(horizon)]
    return _apply_target_seasonality(
        base_values=base,
        history_values=history_values,
        history_periods=history_periods or [],
        target=target,
    )


def _holt_winters_forecast(
    history_values: list[float],
    horizon: int,
    history_periods: list[str] | None = None,
    target: str = "revenue",
) -> list[float]:
    """
    Double exponential smoothing with additive damped trend (Holt's linear method).

    This model explicitly tracks both level and trend direction, making it
    the strongest single-model choice for directional accuracy on monotonic
    or gradually-changing series. The damping factor φ prevents unbounded
    extrapolation on decelerating trends.

    Parameters
    ----------
    alpha : level smoothing (0 < α < 1) — learned from data via CV
    beta  : trend smoothing (0 < β < 1) — learned from data via CV
    phi   : damping factor (0.8 < φ < 1.0) — subdues trend at long horizons
    """
    if not history_values:
        return [0.0] * horizon

    arr = np.asarray(history_values, dtype=float)
    n = len(arr)

    if n < 4:
        return _naive_forecast(history_values, horizon, history_periods=history_periods, target=target)

    # ── Grid-search best α, β via leave-last-k-out (k = min(6, n//5)) ──────
    best_alpha, best_beta, best_phi = 0.3, 0.1, 0.90
    best_sse = float("inf")
    k_cv = min(6, max(2, n // 5))

    for alpha in (0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7):
        for beta in (0.05, 0.10, 0.15, 0.20, 0.30):
            for phi in (0.80, 0.85, 0.90, 0.95, 1.0):
                # Initialise on training portion
                train = arr[: n - k_cv]
                if len(train) < 2:
                    continue
                level = float(train[0])
                trend = float(train[1] - train[0])
                sse = 0.0
                for t in range(1, len(train)):
                    prev_level = level
                    level = alpha * float(train[t]) + (1 - alpha) * (level + phi * trend)
                    trend = beta * (level - prev_level) + (1 - beta) * phi * trend
                # Forecast k_cv ahead, compare to held-out
                l_f, b_f = level, trend
                for h in range(k_cv):
                    pred = l_f + sum(phi**j for j in range(1, h + 2)) * b_f
                    actual = float(arr[n - k_cv + h])
                    sse += (pred - actual) ** 2
                if sse < best_sse:
                    best_sse = sse
                    best_alpha, best_beta, best_phi = alpha, beta, phi

    alpha, beta, phi = best_alpha, best_beta, best_phi

    # ── Fit on full history ─────────────────────────────────────────────────
    level = float(arr[0])
    trend = float(arr[1] - arr[0]) if n >= 2 else 0.0
    for t in range(1, n):
        prev_level = level
        level = alpha * float(arr[t]) + (1 - alpha) * (level + phi * trend)
        trend = beta * (level - prev_level) + (1 - beta) * phi * trend

    # ── Project forward ─────────────────────────────────────────────────────
    damped_sum = 0.0
    base: list[float] = []
    for h in range(1, horizon + 1):
        damped_sum += phi ** h
        base.append(max(0.0, level + damped_sum * trend))

    base = [round(v, 2) for v in base]
    return _apply_target_seasonality(
        base_values=base,
        history_values=history_values,
        history_periods=history_periods or [],
        target=target,
    )


def _moving_average_forecast(history_values: list[float], horizon: int, window: int = 3) -> list[float]:
    if not history_values:
        return [0.0] * horizon
    arr = np.asarray(history_values, dtype=float)
    avg = float(np.mean(arr[-window:]))

    # Estimate a short-term trend from the window's own period-over-period
    # changes so the forecast is not unconditionally flat on a growing series.
    trend = 0.0
    if len(arr) >= window + 1:
        window_deltas = np.diff(arr[-(window + 1):])
        trend_raw = float(np.mean(window_deltas))
        # Cap trend at 15% of avg per period to prevent runaway extrapolation.
        trend_cap = max(abs(avg) * 0.15, 1.0)
        trend = float(np.clip(trend_raw, -trend_cap, trend_cap))

    values = [round(max(avg + trend * (i + 1), 0.0), 2) for i in range(horizon)]
    return values


def _seasonal_naive_forecast(history_values: list[float], horizon: int, season: int = 12) -> tuple[list[float], list[str]]:
    warnings: list[str] = []
    arr = np.asarray(history_values, dtype=float)
    if len(arr) < season:
        warnings.append("Seasonal-naive fallback to naive due to short history.")
        return _naive_forecast(history_values, horizon), warnings

    values = []
    for i in range(horizon):
        values.append(float(arr[-season + (i % season)]))
    return [round(max(v, 0.0), 2) for v in values], warnings


def _engine_model_forecast(
    history_values: list[float],
    history_periods: list[str],
    target: str,
    horizon: int,
    strategy: str,
) -> dict[str, Any]:
    result = engine_forecast(
        history=history_values,
        history_periods=history_periods,
        horizon=horizon,
        forecast_type=target,
        scenario="base",
        confidence_interval=0.8,
        strategy_override=strategy,
    ).to_dict()

    return {
        "model": strategy,
        "strategy_used": result.get("strategy_used", strategy),
        "periods": result.get("periods") or future_periods_from_history(history_periods, horizon),
        "values": [round(float(v), 2) for v in result.get("values", [])],
        "lower_bound": [round(float(v), 2) for v in result.get("lower_bound", [])],
        "upper_bound": [round(float(v), 2) for v in result.get("upper_bound", [])],
        "assumptions": list(result.get("assumptions", [])),
        "warnings": list(result.get("warnings", [])),
    }


def run_model_forecast(
    model_name: str,
    history_values: list[float],
    history_periods: list[str],
    target: str,
    horizon: int,
    include_lstm: bool = True,
) -> dict[str, Any]:
    """Run a single candidate model forecast and return a normalized payload."""
    periods = future_periods_from_history(history_periods, horizon)

    if model_name == "naive":
        values = _naive_forecast(
            history_values,
            horizon,
            history_periods=history_periods,
            target=target,
        )
        lo, hi = _bounds(values, pct=0.12)
        assumptions = ["Projects from latest value with bounded recent drift when trend is present."]
        if target in {"pipeline", "payout"}:
            assumptions.append("Applies quarter-month seasonality profile calibrated from recent history.")
        return {
            "model": "naive",
            "strategy_used": "naive",
            "periods": periods,
            "values": values,
            "lower_bound": lo,
            "upper_bound": hi,
            "assumptions": assumptions,
            "warnings": [],
        }

    if model_name == "moving_average":
        values = _moving_average_forecast(history_values, horizon)
        values = _apply_target_seasonality(
            base_values=values,
            history_values=history_values,
            history_periods=history_periods,
            target=target,
        )
        lo, hi = _bounds(values, pct=0.1)
        return {
            "model": "moving_average",
            "strategy_used": "moving_average",
            "periods": periods,
            "values": values,
            "lower_bound": lo,
            "upper_bound": hi,
            "assumptions": ["Uses trailing 3-period average as projection baseline."],
            "warnings": [],
        }

    if model_name == "seasonal_naive":
        values, warns = _seasonal_naive_forecast(history_values, horizon)
        values = _apply_target_seasonality(
            base_values=values,
            history_values=history_values,
            history_periods=history_periods,
            target=target,
        )
        lo, hi = _bounds(values, pct=0.1)
        return {
            "model": "seasonal_naive",
            "strategy_used": "seasonal_naive",
            "periods": periods,
            "values": values,
            "lower_bound": lo,
            "upper_bound": hi,
            "assumptions": ["Repeats values from the same season in the prior year."],
            "warnings": warns,
        }

    if model_name == "holt_winters":
        values = _holt_winters_forecast(
            history_values,
            horizon,
            history_periods=history_periods,
            target=target,
        )
        lo, hi = _bounds(values, pct=0.12)
        return {
            "model": "holt_winters",
            "strategy_used": "holt_winters",
            "periods": periods,
            "values": values,
            "lower_bound": lo,
            "upper_bound": hi,
            "assumptions": [
                "Double exponential smoothing with additive damped trend (Holt's method).",
                "α, β, φ optimised via leave-last-k-out CV on full history.",
                "φ < 1.0 damps trend at long horizons to avoid over-projection.",
            ],
            "warnings": [],
        }

    if model_name in {"ets", "ridge", "sarimax"}:
        try:
            payload = _engine_model_forecast(
                history_values=history_values,
                history_periods=history_periods,
                target=target,
                horizon=horizon,
                strategy=model_name,
            )
            if target in {"pipeline", "payout"}:
                payload["values"] = _apply_target_seasonality(
                    base_values=list(payload.get("values", [])),
                    history_values=history_values,
                    history_periods=history_periods,
                    target=target,
                )
                payload["lower_bound"] = _apply_target_seasonality(
                    base_values=list(payload.get("lower_bound", [])),
                    history_values=history_values,
                    history_periods=history_periods,
                    target=target,
                )
                payload["upper_bound"] = _apply_target_seasonality(
                    base_values=list(payload.get("upper_bound", [])),
                    history_values=history_values,
                    history_periods=history_periods,
                    target=target,
                )
            return payload
        except Exception as exc:
            values = _naive_forecast(
                history_values,
                horizon,
                history_periods=history_periods,
                target=target,
            )
            lo, hi = _bounds(values, pct=0.15)
            return {
                "model": model_name,
                "strategy_used": "naive_fallback",
                "periods": periods,
                "values": values,
                "lower_bound": lo,
                "upper_bound": hi,
                "assumptions": ["Fallback after model failure."],
                "warnings": [f"{model_name} failed: {exc}"],
            }

    if model_name == "lstm":
        if not include_lstm:
            values = _naive_forecast(
                history_values,
                horizon,
                history_periods=history_periods,
                target=target,
            )
            lo, hi = _bounds(values, pct=0.15)
            return {
                "model": "lstm",
                "strategy_used": "disabled",
                "periods": periods,
                "values": values,
                "lower_bound": lo,
                "upper_bound": hi,
                "assumptions": ["LSTM disabled by request; returned naive baseline."],
                "warnings": ["LSTM disabled by request."],
            }

        lstm_result = run_lstm_forecast(history_values, horizon=horizon)
        values = [round(float(v), 2) for v in lstm_result.get("forecast_values", [])]
        lo, hi = _bounds(values, pct=0.15)
        return {
            "model": "lstm",
            "strategy_used": lstm_result.get("strategy_used", "lstm"),
            "periods": periods,
            "values": values,
            "lower_bound": lo,
            "upper_bound": hi,
            "assumptions": ["Sequence model trained on scaled historical time series."],
            "warnings": list(lstm_result.get("warnings", [])),
            "lstm_backtest": lstm_result.get("backtest", {}),
            "torch_available": bool(lstm_result.get("torch_available", False)),
        }

    values = _naive_forecast(
        history_values,
        horizon,
        history_periods=history_periods,
        target=target,
    )
    lo, hi = _bounds(values, pct=0.2)
    return {
        "model": model_name,
        "strategy_used": "naive_fallback",
        "periods": periods,
        "values": values,
        "lower_bound": lo,
        "upper_bound": hi,
        "assumptions": ["Unknown model name; fallback to naive."],
        "warnings": [f"Unknown model '{model_name}'"],
    }
