"""Static model cards used by API/UI model governance views."""
from __future__ import annotations

MODEL_CARDS: dict[str, dict] = {
    "revenue_forecast": {
        "model_name": "revenue_forecast",
        "purpose": "Forecast monthly revenue to support planning, hiring, and quota strategy.",
        "business_question_answered": "Are we on track to hit revenue targets in the next 3-6 months?",
        "input_data": ["monthly revenue history", "pipeline-weighted indicators", "optional scenario assumptions"],
        "features_used": ["period index", "lagged revenue", "seasonality", "pipeline trend proxies"],
        "features_excluded": ["personally identifiable information", "post-period realized outcomes"],
        "validation_method": "rolling-origin backtesting with holdout folds",
        "metrics": ["MAE", "RMSE", "MAPE", "bias", "directional_accuracy"],
        "known_limitations": [
            "Accuracy degrades with short history (<18 months)",
            "Scenario assumptions can dominate final range",
            "Not a substitute for board-level committed guidance without human review",
        ],
        "recommended_usage": "Use for planning bands (commit/base/best-case) and weekly variance review.",
        "risk_warnings": ["Do not use as a standalone approval for finance commitments."],
    },
    "deal_scoring": {
        "model_name": "deal_scoring",
        "purpose": "Estimate probability of deal conversion and prioritize rescue actions.",
        "business_question_answered": "Which open opportunities are most likely to close and where is risk concentrated?",
        "input_data": ["deal attributes", "stage/probability", "activity cadence", "optional notes-derived features"],
        "features_used": ["amount", "days_in_pipeline", "stage_ordinal", "activity_count", "days_since_last_activity"],
        "features_excluded": ["closed_won/closed_lost terminal labels at inference", "direct payout outcomes"],
        "validation_method": "stratified cross-validation + calibration checks",
        "metrics": ["ROC-AUC", "precision", "recall", "F1"],
        "known_limitations": [
            "Class imbalance may understate minority class behavior",
            "Sensitive to stale stage hygiene",
        ],
        "recommended_usage": "Use for coaching queue prioritization and forecast confidence overlays.",
        "risk_warnings": ["Do not auto-close or auto-reject opportunities without manager review."],
    },
    "rep_clustering": {
        "model_name": "rep_clustering",
        "purpose": "Segment reps by performance profile for targeted enablement.",
        "business_question_answered": "What cohort-level behaviors explain performance differences across reps?",
        "input_data": ["attainment", "win-rate", "deal size", "pipeline coverage", "activity"],
        "features_used": ["attainment_pct", "win_rate", "avg_deal_size", "pipeline_coverage", "activity_rate"],
        "features_excluded": ["protected traits", "free-text PII"],
        "validation_method": "silhouette-driven k-selection + centroid diagnostics",
        "metrics": ["silhouette_score", "cluster_size_distribution"],
        "known_limitations": [
            "Clusters are descriptive, not causal",
            "Small teams can create unstable centroids",
        ],
        "recommended_usage": "Use for enablement planning and coaching program design.",
        "risk_warnings": ["Do not use clusters as sole input for compensation decisions."],
    },
    "churn_retention": {
        "model_name": "churn_retention",
        "purpose": "Estimate account retention risk and survival trajectory.",
        "business_question_answered": "Which accounts are at greatest retention risk in future periods?",
        "input_data": ["account tenure", "revenue activity", "renewal/churn signals"],
        "features_used": ["tenure", "recency", "revenue trend", "industry"],
        "features_excluded": ["sensitive personal attributes"],
        "validation_method": "survival-model diagnostics and cohort calibration",
        "metrics": ["c-index", "calibration_by_cohort"],
        "known_limitations": ["Needs enough historical churn events for stable fit."],
        "recommended_usage": "Use for customer success intervention planning.",
        "risk_warnings": ["Risk tiers should be reviewed with account context before action."],
    },
    "deal_slip": {
        "model_name": "deal_slip",
        "purpose": "Estimate risk that active deals will slip beyond expected close date.",
        "business_question_answered": "Which deals are likely to slip and how much revenue is exposed?",
        "input_data": ["open deal attributes", "activity recency", "stage progress"],
        "features_used": ["expected_close_date", "days_until_close", "activity recency", "stage"],
        "features_excluded": ["post-close outcomes"],
        "validation_method": "historical delay pattern replay",
        "metrics": ["slip_precision", "slip_recall", "at_risk_value_coverage"],
        "known_limitations": ["Forecast quality drops when expected close dates are incomplete."],
        "recommended_usage": "Use for pipeline rescue planning and exec risk commentary.",
        "risk_warnings": ["Should not directly trigger comp penalties without manager validation."],
    },
}


def get_model_card(model_name: str) -> dict | None:
    return MODEL_CARDS.get(model_name)
