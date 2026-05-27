"""
backend/ml/explainability.py
============================
Model explainability utilities for deal scoring.

Primary mode uses SHAP when available. Fallback mode uses model feature
importances from tree estimators.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backend.ml.deal_scoring import DealScoringModel, _build_features


def _clean_feature_name(name: str) -> str:
    text = str(name)
    if "__" in text:
        text = text.split("__", 1)[1]
    return text


def _get_fitted_estimator(calibrated_model: Any) -> Any:
    """Extract fitted base estimator from CalibratedClassifierCV if possible."""
    if hasattr(calibrated_model, "calibrated_classifiers_") and calibrated_model.calibrated_classifiers_:
        candidate = calibrated_model.calibrated_classifiers_[0]
        if hasattr(candidate, "estimator"):
            return candidate.estimator
        if hasattr(candidate, "base_estimator"):
            return candidate.base_estimator
    if hasattr(calibrated_model, "estimator"):
        return calibrated_model.estimator
    if hasattr(calibrated_model, "base_estimator"):
        return calibrated_model.base_estimator
    return calibrated_model


def global_feature_importance(model: DealScoringModel, X: pd.DataFrame) -> dict[str, Any]:
    """
    Return global feature importance, preferring SHAP and falling back to tree importances.

    Parameters
    ----------
    model:
        Trained DealScoringModel
    X:
        Raw deal DataFrame (same schema as scoring/training input)
    """
    if X.empty:
        return {"method": "none", "features": [], "warnings": ["No rows available for explainability"]}

    pre = model.pipeline.named_steps["pre"]
    calibrated = model.pipeline.named_steps["model"]
    estimator = _get_fitted_estimator(calibrated)

    X_model = _build_features(X)
    X_trans = pre.transform(X_model)
    feature_names = [
        _clean_feature_name(name)
        for name in pre.get_feature_names_out()
    ]

    method = "tree_importance"
    warnings: list[str] = []

    try:
        import shap  # type: ignore

        explainer = shap.TreeExplainer(estimator)
        shap_values = explainer.shap_values(X_trans)
        if isinstance(shap_values, list):
            # Binary classification often returns [class0, class1]
            sv = np.asarray(shap_values[-1], dtype=float)
        else:
            sv = np.asarray(shap_values, dtype=float)
        importances = np.mean(np.abs(sv), axis=0)
        method = "shap"
    except Exception as exc:
        warnings.append(f"SHAP unavailable, used tree feature_importances_ fallback: {exc}")
        if hasattr(estimator, "feature_importances_"):
            importances = np.asarray(estimator.feature_importances_, dtype=float)
        else:
            importances = np.zeros(len(feature_names), dtype=float)
            warnings.append("Estimator has no feature_importances_; returning zeros.")

    rows = [
        {
            "feature": feature_names[i] if i < len(feature_names) else f"feature_{i}",
            "importance": round(float(importances[i]), 6),
        }
        for i in range(min(len(importances), len(feature_names)))
    ]
    rows.sort(key=lambda r: r["importance"], reverse=True)

    return {
        "method": method,
        "features": rows,
        "sample_size": int(len(X)),
        "warnings": warnings,
    }


def explain_deal_prediction(model: DealScoringModel, X_row: pd.DataFrame) -> dict[str, Any]:
    """
    Return local explanation for one deal row.

    Output includes top positive/negative contributors and model probability.
    """
    if X_row.empty:
        return {"warnings": ["No deal row provided for explanation"]}

    row = X_row.iloc[[0]].copy()
    pre = model.pipeline.named_steps["pre"]
    calibrated = model.pipeline.named_steps["model"]
    estimator = _get_fitted_estimator(calibrated)

    X_model = _build_features(row)
    X_trans = pre.transform(X_model)
    feature_names = [_clean_feature_name(name) for name in pre.get_feature_names_out()]

    prob = float(model.pipeline.predict_proba(X_model)[0, 1])

    contributions = None
    method = "tree_importance"
    warnings: list[str] = []

    try:
        import shap  # type: ignore

        explainer = shap.TreeExplainer(estimator)
        shap_values = explainer.shap_values(X_trans)
        if isinstance(shap_values, list):
            sv = np.asarray(shap_values[-1], dtype=float)
        else:
            sv = np.asarray(shap_values, dtype=float)
        contributions = sv[0]
        method = "shap"
    except Exception as exc:
        warnings.append(f"SHAP unavailable, used feature_importances_ proxy: {exc}")
        if hasattr(estimator, "feature_importances_"):
            fi = np.asarray(estimator.feature_importances_, dtype=float)
            val = np.asarray(X_trans[0], dtype=float)
            # Proxy contribution: scaled feature value times global importance.
            contributions = fi * val
        else:
            contributions = np.zeros(len(feature_names), dtype=float)
            warnings.append("Estimator has no feature_importances_; local contributions set to zero.")

    rows = []
    if contributions is not None:
        for idx, c in enumerate(contributions):
            rows.append(
                {
                    "feature": feature_names[idx] if idx < len(feature_names) else f"feature_{idx}",
                    "contribution": round(float(c), 6),
                    "direction": "increase_win_probability" if float(c) >= 0 else "decrease_win_probability",
                }
            )

    rows.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    top_rows = rows[:8]

    return {
        "deal_id": str(row.get("deal_id", row.get("id", "unknown")).iloc[0]),
        "method": method,
        "win_probability": round(prob, 6),
        "top_factors": top_rows,
        "warnings": warnings,
    }
