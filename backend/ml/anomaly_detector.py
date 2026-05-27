"""
backend/ml/anomaly_detector.py
================================
C7 — Sales Anomaly Detector

Dual-method: Z-score (per-metric) + IsolationForest (multivariate).
Feeds `explain_metric_change()` with significance scores.

Outputs anomaly flags, scores, and which features drove each anomaly.
"""
from __future__ import annotations

from typing import Any

import numpy as np


# ── Z-score detector ─────────────────────────────────────────────────────

def _zscore_flags(
    values: list[float],
    labels: list[str],
    threshold: float = 2.5,
) -> list[dict[str, Any]]:
    """Return z-score anomaly flags for a single metric time-series."""
    if len(values) < 4:
        return []
    arr = np.array(values, dtype=float)
    mu, sigma = arr.mean(), arr.std()
    if sigma < 1e-9:
        return []
    z_scores = (arr - mu) / sigma
    flags: list[dict] = []
    for i, (label, z) in enumerate(zip(labels, z_scores)):
        if abs(z) >= threshold:
            flags.append({
                "period":    label,
                "value":     round(float(values[i]), 2),
                "z_score":   round(float(z), 3),
                "direction": "above" if z > 0 else "below",
            })
    return flags


# ── IsolationForest detector ─────────────────────────────────────────────

def _isolation_forest_scores(
    feature_matrix: list[list[float]],
    contamination: float = 0.10,
) -> list[float]:
    """Return anomaly score per row (higher = more anomalous, range 0–1)."""
    try:
        from sklearn.ensemble import IsolationForest
        X = np.array(feature_matrix)
        model = IsolationForest(
            contamination=contamination,
            n_estimators=200,
            random_state=42,
        )
        raw_scores = model.fit(X).score_samples(X)
        # Normalise to [0, 1] where 1 = most anomalous
        normed = (raw_scores - raw_scores.max()) / (raw_scores.min() - raw_scores.max() + 1e-9)
        return [round(float(s), 4) for s in normed]
    except Exception:
        return [0.0] * len(feature_matrix)


# ── Feature importance for anomaly explanation ───────────────────────────

def _top_drivers(row: list[float], feature_names: list[str], baseline: list[float]) -> list[dict]:
    """Return top 3 features by relative deviation from baseline."""
    deviations: list[tuple[str, float]] = []
    for name, val, base in zip(feature_names, row, baseline):
        base_safe = base if abs(base) > 1e-9 else 1.0
        rel = abs((val - base) / base_safe)
        deviations.append((name, rel))
    deviations.sort(key=lambda x: x[1], reverse=True)
    return [{"feature": n, "relative_deviation": round(d, 4)} for n, d in deviations[:3]]


# ── Public API ────────────────────────────────────────────────────────────

def detect_anomalies(
    records: list[dict[str, Any]],
    feature_keys: list[str] | None = None,
    label_key: str = "period",
    contamination: float = 0.10,
    zscore_threshold: float = 2.5,
) -> dict[str, Any]:
    """
    Detect anomalies in a tabular time-series of sales metrics.

    Parameters
    ----------
    records         : list of dicts, each representing a period/rep snapshot.
                      Must contain numeric values for feature_keys.
    feature_keys    : which keys to use as features (default: all numeric keys)
    label_key       : key used to identify each record (e.g. "period", "rep_id")
    contamination   : expected anomaly fraction for IsolationForest (default 0.10)
    zscore_threshold: Z-score cutoff per-feature (default 2.5)

    Returns
    -------
    dict:
        anomalies       : list[{label, if_score, zscore_flags, top_drivers, is_anomaly}]
        anomaly_count   : int
        feature_keys    : list[str]
        method          : "zscore+isolation_forest"
        warnings        : list[str]
    """
    warnings: list[str] = []
    if not records:
        return {"anomalies": [], "anomaly_count": 0, "warnings": ["No records provided."]}

    # Resolve feature keys
    if feature_keys is None:
        sample = records[0]
        feature_keys = [k for k, v in sample.items() if k != label_key and isinstance(v, (int, float))]
    if not feature_keys:
        return {"anomalies": [], "anomaly_count": 0, "warnings": ["No numeric feature keys found."]}

    # Build matrix
    labels: list[str] = [str(r.get(label_key, str(i))) for i, r in enumerate(records)]
    matrix: list[list[float]] = []
    for rec in records:
        row = [float(rec.get(k) or 0.0) for k in feature_keys]
        matrix.append(row)

    # Baseline (column medians)
    arr = np.array(matrix)
    baseline = [float(np.median(arr[:, j])) for j in range(len(feature_keys))]

    # IsolationForest scores
    if_scores = _isolation_forest_scores(matrix, contamination=contamination)

    # Z-score flags per feature
    per_feature_flags: dict[str, list[dict]] = {}
    for j, key in enumerate(feature_keys):
        col_vals = [matrix[i][j] for i in range(len(matrix))]
        per_feature_flags[key] = _zscore_flags(col_vals, labels, threshold=zscore_threshold)

    # Assemble per-record result
    # Mark anomaly if IF score >= 0.5 OR any feature z-score flag
    zscore_hit: dict[str, bool] = {lbl: False for lbl in labels}
    for flags in per_feature_flags.values():
        for f in flags:
            zscore_hit[f["period"]] = True

    anomalies: list[dict] = []
    for i, (label, row) in enumerate(zip(labels, matrix)):
        is_anom = if_scores[i] >= 0.5 or zscore_hit.get(label, False)
        anomalies.append({
            "label":         label,
            "if_score":      if_scores[i],
            "zscore_flags":  [f for fl in per_feature_flags.values() for f in fl if f["period"] == label],
            "top_drivers":   _top_drivers(row, feature_keys, baseline),
            "is_anomaly":    is_anom,
        })

    anomaly_count = sum(1 for a in anomalies if a["is_anomaly"])

    return {
        "anomalies":      anomalies,
        "anomaly_count":  anomaly_count,
        "feature_keys":   feature_keys,
        "baseline":       {k: round(v, 2) for k, v in zip(feature_keys, baseline)},
        "method":         "zscore+isolation_forest",
        "contamination":  contamination,
        "warnings":       warnings,
    }
