"""
backend/ml/forecasting_lab/explainability.py
============================================
Business-friendly narrative helpers for forecast model outputs.
"""
from __future__ import annotations

from typing import Any


def business_assumptions_for_model(model_name: str) -> list[str]:
    assumptions_map = {
        "naive": ["Assumes near-term demand is stable relative to the latest month."],
        "moving_average": ["Assumes short-term fluctuations smooth out over a 3-period average."],
        "seasonal_naive": ["Assumes current period behaves like the same season last year."],
        "ets": ["Assumes trend/seasonality can be captured by exponential smoothing components."],
        "ridge": ["Assumes recent lagged values and trend explain short-term movement."],
        "sarimax": ["Assumes autoregressive and seasonal components drive near-term dynamics."],
        "lstm": ["Assumes non-linear sequential patterns are learnable from historical sequences."],
    }
    return assumptions_map.get(model_name, ["Uses model-selected statistical patterns from historical data."])


def build_business_explanation(
    target: str,
    selected_model: str,
    backtest_metrics: dict[str, Any],
    history_months: int,
) -> list[str]:
    mape = backtest_metrics.get("mape")
    direction = backtest_metrics.get("directional_accuracy")

    quality_msg = "Backtest quality unavailable."
    if isinstance(mape, (int, float)):
        if mape < 10:
            quality_msg = "Backtest indicates strong error performance (MAPE < 10%)."
        elif mape < 20:
            quality_msg = "Backtest indicates moderate reliability (MAPE between 10% and 20%)."
        else:
            quality_msg = "Backtest indicates higher uncertainty (MAPE > 20%)."

    direction_msg = ""
    if isinstance(direction, (int, float)):
        direction_msg = f"Directional accuracy is {direction:.1f}%, indicating trend consistency."

    coverage_msg = (
        "History coverage is strong (>= 36 months)."
        if history_months >= 36
        else "History coverage is limited; uncertainty bands should be emphasized."
    )

    return [
        f"Selected model for {target} forecasting: {selected_model}.",
        quality_msg,
        direction_msg,
        coverage_msg,
        *business_assumptions_for_model(selected_model),
    ]
