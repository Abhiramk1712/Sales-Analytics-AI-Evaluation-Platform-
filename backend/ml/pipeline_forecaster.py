"""
backend/ml/pipeline_forecaster.py
===================================
C2 — Pipeline Funnel Forecaster

Stage-specific conversion rate model with a 90-day rolling forecast.
Inputs: pipeline stages + amounts + close dates.
Output: expected bookings per week/month for the next 90 days.

Algorithm
---------
1. Compute rolling-30-day win/close rates per stage from historical deals.
2. For each open deal, expected value = amount × P(close|stage) × time_decay.
3. Aggregate into weekly buckets over a 90-day horizon.
4. Return raw per-deal contributions + aggregate forecast.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

STAGE_CLOSE_PROBS: dict[str, float] = {
    "Prospecting":        0.05,
    "Qualification":      0.10,
    "Needs Analysis":     0.20,
    "Value Proposition":  0.30,
    "Proposal":           0.50,
    "Negotiation":        0.70,
    "Commit":             0.85,
    "Closed Won":         1.00,
    "Closed Lost":        0.00,
}

DEFAULT_PROB = 0.15   # fallback for unknown stages


def _stage_prob(stage: str | None, stage_probs: dict[str, float]) -> float:
    if not stage:
        return DEFAULT_PROB
    return stage_probs.get(stage, stage_probs.get(stage.strip().title(), DEFAULT_PROB))


def _time_decay(expected_close: date | None, today: date, half_life_days: float = 45.0) -> float:
    """
    Decay factor: deals past expected close date retain less weight.
    Deals closing in the future have decay=1.0; overdue deals decay toward 0.
    """
    if expected_close is None:
        return 0.5
    days_overdue = (today - expected_close).days
    if days_overdue <= 0:
        return 1.0
    return math.exp(-math.log(2) * days_overdue / half_life_days)


def _week_bucket(close_date: date | None, today: date, horizon_days: int = 90) -> int | None:
    """Return week index (0–12) or None if outside horizon."""
    if close_date is None:
        return None
    delta = (close_date - today).days
    if delta < 0 or delta > horizon_days:
        return None
    return delta // 7


def _period_label(today: date, week_index: int) -> str:
    start = today + timedelta(weeks=week_index)
    return start.strftime("%Y-W%U")


def forecast_pipeline(
    open_deals: list[dict[str, Any]],
    historical_win_rates: dict[str, float] | None = None,
    today: date | None = None,
    horizon_days: int = 90,
) -> dict[str, Any]:
    """
    Forecast bookings from an open pipeline over the next ``horizon_days``.

    Parameters
    ----------
    open_deals : list of dicts, each containing:
        - id            : deal identifier
        - stage         : pipeline stage name
        - amount        : deal value (float)
        - expected_close: expected close date (date | str "YYYY-MM-DD" | None)
    historical_win_rates : optional override dict {stage: float} (0–1)
    today  : reference date (default: date.today())
    horizon_days : forecast window (default 90)

    Returns
    -------
    dict:
        weekly_forecast : {label: expected_value}  # 13 weekly buckets
        total_expected  : float
        deal_contributions : list of {id, stage, expected_value, probability}
        stage_summary   : {stage: {count, total_pipeline, expected_value}}
        assumptions     : list[str]
        warnings        : list[str]
    """
    today = today or date.today()
    n_weeks = horizon_days // 7 + 1
    stage_probs = {**STAGE_CLOSE_PROBS, **(historical_win_rates or {})}
    warnings: list[str] = []

    weekly_buckets: dict[int, float] = {w: 0.0 for w in range(n_weeks)}
    deal_contributions: list[dict] = []
    stage_summary: dict[str, dict] = {}

    for deal in open_deals:
        d_id    = deal.get("id", "?")
        stage   = deal.get("stage") or "Unknown"
        amount  = float(deal.get("amount") or 0.0)
        raw_ec  = deal.get("expected_close")

        # Parse close date
        if isinstance(raw_ec, date):
            ec = raw_ec
        elif isinstance(raw_ec, str):
            try:
                ec = date.fromisoformat(raw_ec[:10])
            except Exception:
                ec = None
                warnings.append(f"Deal {d_id}: invalid expected_close '{raw_ec}'")
        else:
            ec = None

        prob  = _stage_prob(stage, stage_probs)
        decay = _time_decay(ec, today)
        ev    = round(amount * prob * decay, 2)

        week = _week_bucket(ec, today, horizon_days)
        if week is not None:
            weekly_buckets[week] = round(weekly_buckets[week] + ev, 2)

        deal_contributions.append({
            "id":             d_id,
            "stage":          stage,
            "amount":         amount,
            "expected_close": ec.isoformat() if ec else None,
            "probability":    prob,
            "decay":          round(decay, 4),
            "expected_value": ev,
        })

        # Stage summary
        if stage not in stage_summary:
            stage_summary[stage] = {"count": 0, "total_pipeline": 0.0, "expected_value": 0.0}
        stage_summary[stage]["count"]          += 1
        stage_summary[stage]["total_pipeline"] = round(stage_summary[stage]["total_pipeline"] + amount, 2)
        stage_summary[stage]["expected_value"] = round(stage_summary[stage]["expected_value"] + ev, 2)

    weekly_forecast = {
        _period_label(today, w): round(weekly_buckets[w], 2)
        for w in range(n_weeks)
    }
    total_expected = round(sum(weekly_buckets.values()), 2)

    return {
        "weekly_forecast":    weekly_forecast,
        "total_expected":     total_expected,
        "deal_contributions": deal_contributions,
        "stage_summary":      stage_summary,
        "horizon_days":       horizon_days,
        "reference_date":     today.isoformat(),
        "assumptions": [
            "Stage probabilities: " + ", ".join(f"{s}={p:.0%}" for s, p in sorted(stage_probs.items()) if s not in ("Closed Won", "Closed Lost")),
            f"Time decay: half-life = 45 days past expected close date.",
            f"Deals without expected_close are excluded from weekly buckets.",
        ],
        "warnings": warnings,
    }
