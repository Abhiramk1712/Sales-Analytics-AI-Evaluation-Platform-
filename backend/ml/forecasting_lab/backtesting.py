"""
backend/ml/forecasting_lab/backtesting.py
=========================================
Backtesting helpers for forecasting model comparison.
"""
from __future__ import annotations

from typing import Callable

import numpy as np


def evaluate_predictions(actual: list[float], predicted: list[float]) -> dict[str, float]:
    """Compute forecast quality metrics for equal-length vectors."""
    y_true = np.asarray(actual, dtype=float)
    y_pred = np.asarray(predicted, dtype=float)

    n = min(len(y_true), len(y_pred))
    if n == 0:
        return {
            "mae": float("inf"),
            "rmse": float("inf"),
            "mape": float("inf"),
            "smape": float("inf"),
            "bias": 0.0,
            "bias_pct": 0.0,
            "directional_accuracy": 0.0,
        }

    y_true = y_true[:n]
    y_pred = y_pred[:n]
    err = y_pred - y_true

    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(np.square(err))))

    # Ignore near-zero actuals for MAPE to avoid inflated/unstable scores.
    non_zero_mask = np.abs(y_true) > 1e-6
    if np.any(non_zero_mask):
        mape = float(np.mean(np.abs(err[non_zero_mask] / y_true[non_zero_mask])) * 100)
    else:
        mape = float("nan")

    smape_denom = (np.abs(y_true) + np.abs(y_pred)) / 2.0
    smape = float(np.mean(np.abs(err) / np.where(smape_denom == 0, 1e-9, smape_denom)) * 100)

    if not np.isfinite(mape):
        mape = smape

    bias = float(np.mean(err))
    bias_pct = float((bias / (np.mean(np.abs(y_true)) + 1e-9)) * 100)

    if n < 2:
        directional_accuracy = 0.0
    else:
        actual_delta = np.sign(np.diff(y_true))
        pred_delta = np.sign(np.diff(y_pred))
        directional_accuracy = float(np.mean(actual_delta == pred_delta) * 100)

    return {
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "smape": round(smape, 4),
        "bias": round(bias, 4),
        "bias_pct": round(bias_pct, 4),
        "directional_accuracy": round(directional_accuracy, 4),
    }


def holdout_backtest(
    history_values: list[float],
    history_periods: list[str],
    forecast_fn: Callable[[list[float], list[str], int], list[float]],
    min_train_size: int = 12,
    max_test_horizon: int = 6,
) -> dict:
    """
    Walk-forward cross-validation backtest across multiple folds.

    Uses up to 5 equally-spaced folds with a shorter per-fold horizon so that:
    - More directional comparisons are evaluated (reduces DA variance)
    - Both early, mid, and late growth regimes are covered
    - Flat-line models can't game a single near-flat recent window

    forecast_fn arguments:
    - train_values
    - train_periods
    - horizon
    """
    values = list(map(float, history_values))
    periods = list(history_periods)

    if len(values) < min_train_size + 3:
        return {
            "status": "insufficient_history",
            "warnings": ["Not enough history for holdout backtest"],
        }

    # Shorter per-fold horizon → more direction-change events captured.
    # Using n//10 (floor 3) gives ~5-6 steps per fold on a 58-month series,
    # whereas the old n//8 used 7 steps. More folds compensate for shorter windows.
    test_horizon = min(max(3, len(values) // 10), max_test_horizon)
    n_folds = min(5, (len(values) - min_train_size) // test_horizon)
    if n_folds < 1:
        n_folds = 1

    all_actuals: list[float] = []
    all_preds: list[float] = []
    fold_warnings: list[str] = []

    for fold in range(n_folds):
        # Distribute fold cut-points evenly from ~40% of data to end
        # so we cover early, mid, and late regimes in a single backtest.
        start_offset = max(min_train_size, len(values) // 3)
        span = len(values) - start_offset - test_horizon
        if span <= 0:
            cutoff = len(values) - test_horizon
        else:
            cutoff = start_offset + int(span * fold / max(n_folds - 1, 1))
            cutoff = min(cutoff, len(values) - test_horizon)

        if cutoff < min_train_size:
            continue

        train_vals = values[:cutoff]
        train_per = periods[:cutoff] if len(periods) >= cutoff else periods
        test_vals = values[cutoff: cutoff + test_horizon]

        try:
            preds = forecast_fn(train_vals, train_per, test_horizon)
        except Exception as exc:
            fold_warnings.append(f"Fold {fold + 1} failed: {exc}")
            continue

        all_actuals.extend(test_vals)
        all_preds.extend(preds[:len(test_vals)])

    if not all_actuals:
        return {
            "status": "error",
            "warnings": fold_warnings or ["All backtest folds failed."],
        }

    metrics = evaluate_predictions(all_actuals, all_preds)
    metrics.update({
        "status": "ok",
        "folds": n_folds,
        "warnings": fold_warnings,
    })
    return metrics
