from __future__ import annotations

from typing import Any


def calculate_rep_risk_score(features: dict[str, Any]) -> float:
    attainment = float(features.get("attainment_pct", 0))
    win_rate = float(features.get("win_rate", 0))
    pipeline_coverage = float(features.get("pipeline_coverage", 0))

    score = 100 - (0.5 * attainment) - (0.3 * win_rate) - (20 * pipeline_coverage)
    return max(0.0, min(100.0, round(score, 2)))


def classify_rep_risk(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def explain_rep_risk(features: dict[str, Any]) -> dict:
    score = calculate_rep_risk_score(features)
    label = classify_rep_risk(score)
    reasons = []
    if float(features.get("attainment_pct", 0)) < 75:
        reasons.append("Low quota attainment")
    if float(features.get("win_rate", 0)) < 25:
        reasons.append("Low win rate")
    if float(features.get("pipeline_coverage", 0)) < 1.5:
        reasons.append("Weak pipeline coverage")
    if not reasons:
        reasons.append("No major risk signals detected")
    return {"score": score, "risk_level": label, "reasons": reasons}
