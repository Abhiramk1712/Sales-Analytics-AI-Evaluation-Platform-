"""
backend/ml/evaluation.py
==========================
ML model evaluation and testing utilities
"""
from typing import Dict, Any, Optional
import numpy as np
import pandas as pd


def classification_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    y_pred_proba: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Compute classification evaluation metrics.
    
    Args:
        y_true: Ground truth labels
        y_pred: Predicted labels
        y_pred_proba: Predicted probabilities (optional, for AUC)
    
    Returns:
        Dictionary with metrics
    """
    from sklearn.metrics import accuracy_score, precision_score, recall_score, f1_score
    
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, average='weighted', zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, average='weighted', zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, average='weighted', zero_division=0)),
    }
    
    # Add AUC if probabilities provided
    if y_pred_proba is not None:
        from sklearn.metrics import roc_auc_score
        try:
            metrics["auc"] = float(roc_auc_score(y_true, y_pred_proba))
        except Exception:
            metrics["auc"] = None
    
    return metrics


def clustering_summary(
    labels: np.ndarray,
    data: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Summarize clustering results.
    
    Args:
        labels: Cluster labels
        data: Optional feature data for silhouette score
    
    Returns:
        Dictionary with clustering summary
    """
    unique_labels = np.unique(labels)
    label_counts = np.bincount(labels.astype(int))
    
    summary = {
        "cluster_count": int(len(unique_labels)),
        "cluster_sizes": label_counts.tolist(),
        "largest_cluster": int(np.max(label_counts)),
        "smallest_cluster": int(np.min(label_counts)),
    }
    
    # Add silhouette score if data provided
    if data is not None:
        try:
            from sklearn.metrics import silhouette_score
            summary["silhouette_score"] = float(silhouette_score(data, labels))
        except Exception:
            summary["silhouette_score"] = None
    
    return summary


def rolling_origin_backtest(series: pd.Series, min_train_size: int = 18) -> Dict[str, Any]:
    """Simple one-step rolling-origin backtest for monthly series."""
    values = np.asarray(series, dtype=float)
    if len(values) < (min_train_size + 3):
        return {
            "status": "insufficient_history",
            "warnings": ["Insufficient history for rolling-origin backtest"],
        }

    actuals = []
    preds = []
    for i in range(min_train_size, len(values)):
        train = values[:i]
        actual = values[i]
        # Naive one-step forecast from last observed point.
        pred = float(train[-1])
        actuals.append(float(actual))
        preds.append(pred)

    y_true = np.array(actuals)
    y_pred = np.array(preds)
    err = y_pred - y_true
    mae = float(np.mean(np.abs(err)))
    rmse = float(np.sqrt(np.mean(np.square(err))))
    mape = float(np.mean(np.abs(err / (y_true + 1e-9))) * 100)
    bias = float(np.mean(err))

    return {
        "status": "ok",
        "folds": len(actuals),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "bias": round(bias, 4),
        "warnings": [],
    }


def detect_drift(
    baseline_metrics: Dict[str, Any],
    current_metrics: Dict[str, Any],
    thresholds: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """
    Compare current model metrics against a baseline (training-time) snapshot.

    Returns a drift report with per-metric deltas and an overall ``drifted`` flag.

    Parameters
    ----------
    baseline_metrics:
        Metrics dict recorded at training time, e.g.
        ``{"mae": 1200, "rmse": 1800, "mape": 8.5}``
    current_metrics:
        Latest backtest metrics dict with the same keys.
    thresholds:
        Relative degradation thresholds per metric (0–1 fraction).
        Defaults: mae/rmse/mape → 20 % regression triggers drift.
    """
    DEFAULT_THRESHOLDS: Dict[str, float] = {
        "mae": 0.20,
        "rmse": 0.20,
        "mape": 0.20,
        "accuracy": 0.10,   # lower is worse for accuracy
        "f1": 0.10,
        "auc": 0.05,
    }
    thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    metrics_compared: list[Dict[str, Any]] = []
    drift_flags: list[str] = []
    warnings: list[str] = []

    for key, baseline_val in baseline_metrics.items():
        if not isinstance(baseline_val, (int, float)) or baseline_val is None:
            continue
        current_val = current_metrics.get(key)
        if current_val is None or not isinstance(current_val, (int, float)):
            warnings.append(f"Metric '{key}' not available in current snapshot; skipping")
            continue

        baseline_f = float(baseline_val)
        current_f = float(current_val)
        threshold = thresholds.get(key, 0.20)

        # For error metrics (mae, rmse, mape, bias) higher is worse.
        # For score metrics (accuracy, f1, auc, precision, recall) lower is worse.
        score_metrics = {"accuracy", "f1", "auc", "precision", "recall"}
        is_score = key.lower() in score_metrics

        if baseline_f == 0:
            relative_change = 0.0
        else:
            relative_change = (current_f - baseline_f) / abs(baseline_f)

        # Drift triggers when an error metric grows by > threshold
        # or a score metric falls by > threshold
        drifted_metric = (
            (not is_score and relative_change > threshold)
            or (is_score and relative_change < -threshold)
        )
        if drifted_metric:
            drift_flags.append(key)

        metrics_compared.append({
            "metric": key,
            "baseline": round(baseline_f, 4),
            "current": round(current_f, 4),
            "relative_change_pct": round(relative_change * 100, 2),
            "threshold_pct": round(threshold * 100, 1),
            "drifted": drifted_metric,
        })

    overall_drifted = len(drift_flags) > 0
    severity = "none"
    if len(drift_flags) == 1:
        severity = "low"
    elif len(drift_flags) == 2:
        severity = "medium"
    elif len(drift_flags) >= 3:
        severity = "high"

    return {
        "drifted": overall_drifted,
        "severity": severity,
        "drifted_metrics": drift_flags,
        "metrics_compared": metrics_compared,
        "warnings": warnings,
    }
