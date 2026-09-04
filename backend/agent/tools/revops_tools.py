"""
backend/agent/tools/revops_tools.py
=====================================
RevOps-specific agent tools for quota risk, pipeline coverage,
deal slip analysis, ARR trajectory, and rep ramp status.
"""
from __future__ import annotations

import calendar
from datetime import date
import re
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.metrics import calculators
from backend.models import Rep, Deal, Quota, Activity


def _as_tool_result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


async def get_quota_risk_summary(db: AsyncSession) -> dict[str, Any]:
    """Identify reps at quota risk: low attainment + thin pipeline + recent inactivity."""
    rep_rows = (await db.execute(select(Rep))).scalars().all()
    at_risk = []

    for rep in rep_rows:
        filters = {"rep_id": rep.id}
        rev = await calculators.get_total_revenue(db, filters)
        quota = await calculators.get_total_quota(db, filters)
        pipeline = await calculators.get_open_pipeline(db, filters)

        if quota["value"] <= 0:
            continue

        attainment = rev["value"] / quota["value"] * 100
        coverage = pipeline["value"] / quota["value"] if quota["value"] > 0 else 0

        if attainment < 60 and coverage < 2.0:
            at_risk.append({
                "rep_id": str(rep.id),
                "rep_name": rep.name,
                "region": rep.region,
                "attainment_pct": round(attainment, 1),
                "pipeline_coverage": round(coverage, 2),
                "revenue": round(rev["value"], 2),
                "quota": round(quota["value"], 2),
                "risk_signals": _build_risk_signals(attainment, coverage),
            })

    at_risk.sort(key=lambda x: x["attainment_pct"])
    warnings: list[str] = []
    if at_risk:
        warnings.append(f"{len(at_risk)} reps are currently quota-at-risk.")

    data = {
        "at_risk_rep_count": len(at_risk),
        "at_risk_reps": at_risk[:10],
        "definition": "Reps with < 60% quota attainment AND < 2× pipeline coverage are flagged as quota-at-risk.",
        "benchmark": "Healthy: >80% of reps ≥ 80% attainment at period midpoint",
    }
    return _as_tool_result(
        "get_quota_risk_summary",
        "warning" if warnings else "success",
        data,
        warnings,
        ["reps", "revenue", "quotas", "deals"],
    )


def _build_risk_signals(attainment: float, coverage: float) -> list[str]:
    signals = []
    if attainment < 40:
        signals.append("Critical: attainment below 40%")
    elif attainment < 60:
        signals.append("Warning: attainment below 60%")
    if coverage < 1.0:
        signals.append("Severe: pipeline coverage < 1× quota (will miss even at 100% conversion)")
    elif coverage < 2.0:
        signals.append("Warning: pipeline coverage < 2× quota (insufficient buffer)")
    return signals


async def get_pipeline_coverage_check(db: AsyncSession) -> dict[str, Any]:
    """Check weighted and unweighted pipeline coverage vs quota benchmarks."""
    weighted = await calculators.get_weighted_pipeline_coverage(db)
    unweighted = await calculators.get_open_pipeline(db)
    quota = await calculators.get_total_quota(db)

    quota_val = quota["value"]
    unweighted_val = unweighted["value"]
    unweighted_coverage = round(unweighted_val / quota_val, 2) if quota_val > 0 else 0

    weighted_ratio = weighted.get("ratio", 0)

    health = "healthy"
    if weighted_ratio < 2.0 or unweighted_coverage < 3.0:
        health = "at_risk"
    elif weighted_ratio < 3.0 or unweighted_coverage < 4.0:
        health = "watch"

    recommendations = _pipeline_recommendations(unweighted_coverage, weighted_ratio)
    warnings: list[str] = []
    if health != "healthy":
        warnings.append(
            f"Pipeline coverage health is {health}: {unweighted_coverage:.2f}x unweighted, {weighted_ratio:.2f}x weighted."
        )

    data = {
        "unweighted_pipeline": round(unweighted_val, 2),
        "unweighted_coverage_ratio": unweighted_coverage,
        "weighted_pipeline": weighted.get("weighted_pipeline", 0),
        "weighted_coverage_ratio": weighted_ratio,
        "quota": round(quota_val, 2),
        "health_status": health,
        "benchmarks": {
            "unweighted_minimum": "4× quarterly quota",
            "weighted_minimum": "3× quarterly quota",
            "current_unweighted_ok": unweighted_coverage >= 4.0,
            "current_weighted_ok": weighted_ratio >= 3.0,
        },
        "recommendations": recommendations,
    }
    return _as_tool_result(
        "get_pipeline_coverage_check",
        "warning" if warnings else "success",
        data,
        warnings,
        ["deals", "quotas", "revenue"],
    )


def _pipeline_recommendations(unweighted: float, weighted: float) -> list[str]:
    recs = []
    if unweighted < 4.0:
        recs.append(f"Increase pipeline generation: current {unweighted:.1f}× coverage is below 4× minimum")
    if weighted < 3.0:
        recs.append(f"Improve stage quality: weighted coverage {weighted:.1f}× is below 3× minimum (inspect Proposal+ stages)")
    if unweighted >= 4.0 and weighted < 2.5:
        recs.append("Pipeline volume is adequate but stage probabilities are low — run deal qualification review")
    return recs or ["Pipeline coverage is within acceptable benchmarks"]


def _extract_top_n_at_risk_deals(message: str, default_value: int = 15) -> int:
    msg = (message or "").lower()
    patterns = [
        r"\btop\s+(\d{1,2})\s+(?:at[-\s]?risk\s+)?deals?\b",
        r"\brescu(?:e|ing)\s+(?:the\s+)?top\s+(\d{1,2})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(1, min(30, int(m.group(1))))
    return max(1, min(30, int(default_value)))


def _extract_requested_weighted_coverage_baseline(message: str) -> float | None:
    msg = (message or "").lower()
    patterns = [
        # Example: "from 0.68x to 1.0x"
        r"\bfrom\s*(\d+(?:\.\d+)?)\s*x?\s*to\s*\d+(?:\.\d+)?\s*x?\b",
        # Example: "current weighted pipeline coverage is 0.60x"
        r"\b(?:currently|current)\s+(?:weighted\s+)?(?:pipeline\s+)?coverage\s*(?:at|is)?\s*(\d+(?:\.\d+)?)\s*x?\b",
        # Example: "currently at 0.68x weighted coverage"
        r"\b(?:currently|current)\s+(?:at\s+)?(\d+(?:\.\d+)?)\s*x\s*(?:weighted\s+)?(?:pipeline\s+)?coverage\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.1, min(25.0, float(m.group(1))))
    return None


def _extract_target_weighted_coverage_details(message: str, default_value: float = 1.0) -> tuple[float, bool]:
    msg = (message or "").lower()
    patterns = [
        # Example: "from 0.68x to 1.0x"
        r"\bfrom\s*\d+(?:\.\d+)?\s*x?\s*to\s*(\d+(?:\.\d+)?)\s*x?\b",
        # Example: "raise weighted pipeline coverage to 1.0x"
        r"(?:weighted\s+)?pipeline\s+coverage[^\d]{0,24}(?:to|at|reach|target(?:ing)?)\s*(\d+(?:\.\d+)?)\s*x?",
        # Example: "target 1.2x weighted coverage"
        r"\btarget\s*(\d+(?:\.\d+)?)\s*x\s*(?:weighted\s+)?(?:pipeline\s+)?coverage\b",
        # Example: "we target 1.20x this quarter"
        r"\btarget(?:ing)?\s*(\d+(?:\.\d+)?)\s*x\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.1, min(25.0, float(m.group(1)))), True
    return max(0.1, min(25.0, float(default_value))), False


def _extract_target_weighted_coverage(message: str, default_value: float = 1.0) -> float:
    value, _ = _extract_target_weighted_coverage_details(message, default_value=default_value)
    return value


def _month_bounds(period: str) -> tuple[date, date]:
    year = int(period[:4])
    month = int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


async def get_deal_velocity_trends(db: AsyncSession, months: int = 6) -> dict[str, Any]:
    """Return month-over-month deal velocity trend points and directional summary."""
    months = max(3, min(12, int(months or 6)))

    created_rows = (
        await db.execute(select(Deal.created_at).where(Deal.created_at.isnot(None)))
    ).scalars().all()
    periods = sorted({d.strftime("%Y-%m") for d in created_rows if d}, reverse=True)

    # Fallback to closed-won close dates when created_at is sparse.
    if not periods:
        close_dates = (
            await db.execute(
                select(Deal.actual_close_date)
                .where(Deal.stage == "Closed Won")
                .where(Deal.actual_close_date.isnot(None))
            )
        ).scalars().all()
        periods = sorted({d.strftime("%Y-%m") for d in close_dates if d}, reverse=True)

    if not periods:
        return _as_tool_result(
            "get_deal_velocity_trends",
            "warning",
            {
                "months_requested": months,
                "months_returned": 0,
                "trend_points": [],
                "direction": "flat",
                "change_pct": 0.0,
                "latest": None,
            },
            ["No deal history found to compute velocity trends."],
            ["deals"],
        )

    selected_periods = list(reversed(periods[:months]))
    trend_points: list[dict[str, Any]] = []
    warnings: list[str] = []

    for period in selected_periods:
        start_date, end_date = _month_bounds(period)
        payload = await calculators.calc_deal_velocity(
            db,
            filters={"start_date": start_date, "end_date": end_date},
        )
        trend_points.append(
            {
                "period": period,
                "deal_velocity": float(payload.get("deal_velocity", 0.0) or 0.0),
                "avg_cycle_days": payload.get("avg_cycle_days"),
                "win_rate": float(payload.get("win_rate", 0.0) or 0.0),
                "pipeline_value": float(payload.get("pipeline_value", 0.0) or 0.0),
                "total_won": int(payload.get("total_won", 0) or 0),
                "total_closed": int(payload.get("total_closed", 0) or 0),
            }
        )
        warnings.extend(payload.get("warnings", []))

    if any(int(p.get("total_won", 0) or 0) > 0 for p in trend_points):
        warnings = [w for w in warnings if w != "No closed-won deals with close dates found"]

    velocities = [float(p["deal_velocity"]) for p in trend_points]
    direction = "flat"
    change_pct = 0.0
    if len(velocities) >= 2:
        first = velocities[0]
        last = velocities[-1]
        if first > 0:
            change_pct = ((last - first) / first) * 100.0
        elif last > 0:
            change_pct = 100.0

        if last > first * 1.05:
            direction = "up"
        elif last < first * 0.95:
            direction = "down"

    if len(trend_points) < 2:
        warnings.append("Not enough monthly points for a robust trend direction.")

    data = {
        "months_requested": months,
        "months_returned": len(trend_points),
        "trend_points": trend_points,
        "direction": direction,
        "change_pct": round(change_pct, 2),
        "latest": trend_points[-1] if trend_points else None,
    }
    return _as_tool_result(
        "get_deal_velocity_trends",
        "warning" if warnings else "success",
        data,
        sorted(set(warnings)),
        ["deals", "quotas"],
    )


async def get_deal_slip_analysis(db: AsyncSession) -> dict[str, Any]:
    """Return deal slip risk summary using the DealSlipModel."""
    import pandas as pd
    from backend.ml.deal_slip import DealSlipModel

    deal_rows = (await db.execute(select(Deal))).scalars().all()
    activity_rows = (await db.execute(select(Activity))).scalars().all()

    if not deal_rows:
        return _as_tool_result(
            "get_deal_slip_analysis",
            "warning",
            {
                "open_deals_analyzed": 0,
                "slip_risk_count": 0,
                "slip_risk_pct": 0.0,
                "total_amount_at_risk": 0.0,
                "top_at_risk_deals": [],
            },
            ["No deals found in database for slip-risk analysis."],
            ["deals", "activities"],
        )

    deals_df = pd.DataFrame([{
        "id": str(d.id),
        "deal_id": str(d.id),
        "name": d.name or "",
        "stage": d.stage or "Prospecting",
        "amount": float(d.amount or 0),
        "close_probability": float(d.close_probability or 50),
        "expected_close_date": str(d.expected_close_date) if d.expected_close_date else None,
        "actual_close_date": str(d.actual_close_date) if d.actual_close_date else None,
        "created_at": d.created_at,
        "rep_id": str(d.rep_id),
    } for d in deal_rows])

    activities_df = pd.DataFrame([{
        "id": str(a.id),
        "deal_id": str(a.deal_id),
        "activity_date": str(a.activity_date) if a.activity_date else None,
    } for a in activity_rows])

    warnings: list[str] = []
    try:
        model = DealSlipModel()
        model.fit(deals_df, activities_df)
        results = model.predict(deals_df, activities_df)
    except Exception as exc:
        return _as_tool_result(
            "get_deal_slip_analysis",
            "error",
            {
                "open_deals_analyzed": 0,
                "slip_risk_count": 0,
                "slip_risk_pct": 0.0,
                "total_amount_at_risk": 0.0,
                "top_at_risk_deals": [],
            },
            [f"Deal slip model failed: {exc}"],
            ["deals", "activities", "ml.deal_slip"],
        )

    slipped = [r for r in results if r.slip_flag]
    total_at_risk_value = sum(r.amount for r in slipped)
    if slipped:
        warnings.append(f"{len(slipped)} open deals are currently at risk of slipping.")

    data = {
        "open_deals_analyzed": len(results),
        "slip_risk_count": len(slipped),
        "slip_risk_pct": round(len(slipped) / max(len(results), 1) * 100, 1),
        "total_amount_at_risk": round(total_at_risk_value, 2),
        "top_at_risk_deals": [
            {
                "deal_name": r.deal_name,
                "slip_risk_score": r.slip_risk_score,
                "expected_close_date": r.expected_close_date,
                "days_until_close": r.days_until_close,
                "stage": r.stage,
                "amount": r.amount,
                "risk_factors": r.top_risk_factors,
            }
            for r in slipped[:5]
        ],
    }
    return _as_tool_result(
        "get_deal_slip_analysis",
        "warning" if warnings else "success",
        data,
        warnings,
        ["deals", "activities", "ml.deal_slip"],
    )


async def get_pipeline_rescue_what_if(db: AsyncSession, message: str) -> dict[str, Any]:
    """
    Estimate impact of rescuing top N at-risk deals on weighted coverage,
    incremental closed revenue, and quota attainment.
    """
    import pandas as pd
    from backend.ml.deal_slip import DealSlipModel

    warnings: list[str] = []
    top_n = _extract_top_n_at_risk_deals(message, default_value=15)
    requested_baseline_weighted_coverage = _extract_requested_weighted_coverage_baseline(message)
    target_weighted_coverage, target_explicit_from_prompt = _extract_target_weighted_coverage_details(
        message,
        default_value=1.0,
    )

    deal_rows = (await db.execute(select(Deal))).scalars().all()
    activity_rows = (await db.execute(select(Activity))).scalars().all()
    rep_rows = (await db.execute(select(Rep))).scalars().all()
    rep_name_by_id = {str(r.id): (r.name or "Unknown") for r in rep_rows}

    if not deal_rows:
        return _as_tool_result(
            "get_pipeline_rescue_what_if",
            "warning",
            {
                "scenario": {
                    "top_n_at_risk_deals": top_n,
                    "target_weighted_coverage": target_weighted_coverage,
                },
                "priority_deals": [],
                "priority_reps": [],
            },
            ["No deals found in database for pipeline rescue scenario."],
            ["deals", "activities", "reps", "quotas", "revenue"],
        )

    deals_df = pd.DataFrame([{
        "id": str(d.id),
        "deal_id": str(d.id),
        "name": d.name or "",
        "stage": d.stage or "Prospecting",
        "amount": float(d.amount or 0),
        "close_probability": float(d.close_probability or 50),
        "expected_close_date": str(d.expected_close_date) if d.expected_close_date else None,
        "actual_close_date": str(d.actual_close_date) if d.actual_close_date else None,
        "created_at": d.created_at,
        "rep_id": str(d.rep_id),
    } for d in deal_rows])

    activities_df = pd.DataFrame([{
        "id": str(a.id),
        "deal_id": str(a.deal_id),
        "activity_date": str(a.activity_date) if a.activity_date else None,
    } for a in activity_rows])

    try:
        model = DealSlipModel()
        model.fit(deals_df, activities_df)
        predictions = model.predict(deals_df, activities_df)
    except Exception as exc:
        return _as_tool_result(
            "get_pipeline_rescue_what_if",
            "error",
            {
                "scenario": {
                    "top_n_at_risk_deals": top_n,
                    "target_weighted_coverage": target_weighted_coverage,
                },
                "priority_deals": [],
                "priority_reps": [],
            },
            [f"Pipeline rescue what-if failed: {exc}"],
            ["deals", "activities", "ml.deal_slip"],
        )

    deal_meta_by_id = {
        str(row.get("deal_id")): {
            "rep_id": str(row.get("rep_id") or ""),
            "close_probability": float(row.get("close_probability") or 0.0),
            "amount": float(row.get("amount") or 0.0),
            "stage": row.get("stage") or "Prospecting",
            "name": row.get("name") or "",
        }
        for row in deals_df.to_dict(orient="records")
    }

    at_risk_rows: list[dict[str, Any]] = []
    for pred in predictions:
        if not bool(pred.slip_flag):
            continue
        deal_id = str(pred.deal_id)
        meta = deal_meta_by_id.get(deal_id, {})
        amount = float(pred.amount if pred.amount is not None else meta.get("amount", 0.0))
        close_probability = max(0.0, min(100.0, float(meta.get("close_probability", 0.0))))
        weighted_value = amount * (close_probability / 100.0)
        slip_risk_score = float(pred.slip_risk_score or 0.0)
        rep_id = str(meta.get("rep_id") or "")
        rep_name = rep_name_by_id.get(rep_id, "Unknown")
        priority_score = weighted_value * slip_risk_score

        at_risk_rows.append(
            {
                "deal_id": deal_id,
                "deal_name": pred.deal_name or meta.get("name") or "Unknown deal",
                "rep_id": rep_id,
                "rep_name": rep_name,
                "stage": pred.stage or meta.get("stage") or "Prospecting",
                "amount": round(amount, 2),
                "close_probability": round(close_probability, 1),
                "weighted_value": round(weighted_value, 2),
                "slip_risk_score": round(slip_risk_score, 4),
                "days_until_close": pred.days_until_close,
                "priority_score": round(priority_score, 2),
                "risk_factors": list(pred.top_risk_factors or []),
            }
        )

    at_risk_rows.sort(
        key=lambda r: (
            float(r.get("slip_risk_score", 0.0)),
            float(r.get("weighted_value", 0.0)),
            float(r.get("amount", 0.0)),
        ),
        reverse=True,
    )

    selected = at_risk_rows[:top_n]
    selected_amount = sum(float(r.get("amount", 0.0)) for r in selected)
    selected_weighted = sum(float(r.get("weighted_value", 0.0)) for r in selected)

    weighted_cov = await calculators.get_weighted_pipeline_coverage(db)
    quota_total = await calculators.get_total_quota(db)
    revenue_total = await calculators.get_total_revenue(db)

    warnings.extend(weighted_cov.get("warnings", []))
    warnings.extend(quota_total.get("warnings", []))
    warnings.extend(revenue_total.get("warnings", []))

    current_weighted_coverage = float(weighted_cov.get("ratio", 0.0) or 0.0)
    current_weighted_pipeline = float(weighted_cov.get("weighted_pipeline", 0.0) or 0.0)
    quota_value = float(quota_total.get("value", 0.0) or 0.0)
    revenue_value = float(revenue_total.get("value", 0.0) or 0.0)

    baseline_delta = None
    baseline_mismatch = False
    if requested_baseline_weighted_coverage is not None:
        baseline_delta = current_weighted_coverage - requested_baseline_weighted_coverage
        # Surface meaningful prompt-vs-actual conflicts while ignoring tiny rounding noise.
        baseline_mismatch = abs(baseline_delta) >= 0.05
        if baseline_mismatch:
            warnings.append(
                "Scenario input mismatch: requested weighted coverage baseline "
                f"{requested_baseline_weighted_coverage:.2f}x differs from actual current "
                f"{current_weighted_coverage:.2f}x. Calculations use the actual baseline."
            )

    target_already_met_pre_rescue = False
    if target_explicit_from_prompt and current_weighted_coverage >= target_weighted_coverage:
        target_already_met_pre_rescue = True
        warnings.append(
            "Scenario input mismatch: requested weighted coverage target "
            f"{target_weighted_coverage:.2f}x is already met by current actual "
            f"{current_weighted_coverage:.2f}x. Rescue scenario still quantifies upside."
        )

    # Fallback when quota is missing but weighted coverage is available.
    if quota_value <= 0 and current_weighted_coverage > 0:
        quota_value = current_weighted_pipeline / current_weighted_coverage

    target_weighted_pipeline = target_weighted_coverage * quota_value if quota_value > 0 else 0.0
    weighted_gap_to_target = max(0.0, target_weighted_pipeline - current_weighted_pipeline)

    new_weighted_pipeline_expected = current_weighted_pipeline + selected_weighted
    weighted_coverage_after_expected = (
        new_weighted_pipeline_expected / quota_value if quota_value > 0 else 0.0
    )
    remaining_weighted_gap = max(0.0, target_weighted_pipeline - new_weighted_pipeline_expected)

    quota_attainment_before = (revenue_value / quota_value * 100.0) if quota_value > 0 else 0.0
    quota_attainment_after_expected = ((revenue_value + selected_weighted) / quota_value * 100.0) if quota_value > 0 else 0.0
    quota_attainment_after_best = ((revenue_value + selected_amount) / quota_value * 100.0) if quota_value > 0 else 0.0
    quota_attainment_lift_expected = quota_attainment_after_expected - quota_attainment_before
    quota_attainment_lift_best = quota_attainment_after_best - quota_attainment_before

    weighted_to_gross_efficiency = (selected_weighted / selected_amount) if selected_amount > 0 else 0.0
    additional_gross_needed = None
    if remaining_weighted_gap > 0 and weighted_to_gross_efficiency > 0:
        additional_gross_needed = remaining_weighted_gap / weighted_to_gross_efficiency

    rep_rollup: dict[str, dict[str, Any]] = {}
    for row in selected:
        rep_name = str(row.get("rep_name") or "Unknown")
        rep_entry = rep_rollup.setdefault(
            rep_name,
            {
                "rep_name": rep_name,
                "deals": 0,
                "amount": 0.0,
                "weighted_value": 0.0,
                "priority_score": 0.0,
            },
        )
        rep_entry["deals"] += 1
        rep_entry["amount"] += float(row.get("amount", 0.0) or 0.0)
        rep_entry["weighted_value"] += float(row.get("weighted_value", 0.0) or 0.0)
        rep_entry["priority_score"] += float(row.get("priority_score", 0.0) or 0.0)

    priority_reps = sorted(
        [
            {
                "rep_name": r["rep_name"],
                "deals": int(r["deals"]),
                "amount": round(float(r["amount"]), 2),
                "weighted_value": round(float(r["weighted_value"]), 2),
                "priority_score": round(float(r["priority_score"]), 2),
            }
            for r in rep_rollup.values()
        ],
        key=lambda r: float(r.get("priority_score", 0.0)),
        reverse=True,
    )

    if at_risk_rows:
        slip_pct = round(len(at_risk_rows) / max(len(predictions), 1) * 100.0, 1)
        warnings.append(f"{len(at_risk_rows)} open deals are currently at risk of slipping.")
    else:
        slip_pct = 0.0
        warnings.append("No at-risk deals were identified in the current open-deal universe.")

    if len(selected) < top_n:
        warnings.append(
            f"Requested top {top_n} at-risk deals, but only {len(selected)} are available."
        )

    if remaining_weighted_gap > 0 and quota_value > 0:
        warnings.append(
            f"Rescuing top {len(selected)} at-risk deals improves weighted coverage to {weighted_coverage_after_expected:.2f}x, "
            f"still below the {target_weighted_coverage:.2f}x target."
        )

    data = {
        "scenario": {
            "top_n_at_risk_deals": int(top_n),
            "target_weighted_coverage": round(target_weighted_coverage, 2),
            "requested_baseline_weighted_coverage": (
                round(requested_baseline_weighted_coverage, 2)
                if requested_baseline_weighted_coverage is not None
                else None
            ),
            "current_weighted_coverage": round(current_weighted_coverage, 2),
            "weighted_coverage_after_rescue": round(weighted_coverage_after_expected, 2),
            "current_weighted_pipeline": round(current_weighted_pipeline, 2),
            "target_weighted_pipeline": round(target_weighted_pipeline, 2),
            "weighted_gap_to_target": round(weighted_gap_to_target, 2),
            "remaining_weighted_gap": round(remaining_weighted_gap, 2),
            "target_reached": remaining_weighted_gap <= 1e-6,
        },
        "input_reconciliation": {
            "requested_baseline_weighted_coverage": (
                round(requested_baseline_weighted_coverage, 2)
                if requested_baseline_weighted_coverage is not None
                else None
            ),
            "actual_current_weighted_coverage": round(current_weighted_coverage, 2),
            "delta_weighted_coverage": round(baseline_delta, 2) if baseline_delta is not None else None,
            "baseline_mismatch": baseline_mismatch,
            "target_explicit_from_prompt": target_explicit_from_prompt,
            "target_already_met_pre_rescue": target_already_met_pre_rescue,
            "used_actual_baseline_for_calculation": True,
        },
        "incremental_impact": {
            "expected_incremental_closed_revenue": round(selected_weighted, 2),
            "best_case_incremental_closed_revenue": round(selected_amount, 2),
            "quota_attainment_before_pct": round(quota_attainment_before, 2),
            "quota_attainment_after_expected_pct": round(quota_attainment_after_expected, 2),
            "quota_attainment_after_best_case_pct": round(quota_attainment_after_best, 2),
            "quota_attainment_lift_expected_pct_points": round(quota_attainment_lift_expected, 2),
            "quota_attainment_lift_best_case_pct_points": round(quota_attainment_lift_best, 2),
            "weighted_to_gross_efficiency_pct": round(weighted_to_gross_efficiency * 100.0, 1),
            "additional_gross_pipeline_needed_at_same_efficiency": (
                round(additional_gross_needed, 2) if additional_gross_needed is not None else None
            ),
        },
        "slip_universe": {
            "open_deals_analyzed": len(predictions),
            "slip_risk_count": len(at_risk_rows),
            "slip_risk_pct": slip_pct,
        },
        "priority_deals": selected[:10],
        "priority_reps": priority_reps[:8],
    }

    status = "warning" if warnings else "success"
    return _as_tool_result(
        "get_pipeline_rescue_what_if",
        status,
        data,
        sorted(set(warnings)),
        ["deals", "activities", "reps", "quotas", "revenue", "ml.deal_slip"],
    )


async def get_arr_trajectory(db: AsyncSession) -> dict[str, Any]:
    """Return ARR growth trajectory, NRR, GRR, and waterfall components."""
    nrr = await calculators.get_nrr(db)
    grr = await calculators.get_grr(db)
    arr_growth = await calculators.get_arr_growth_rate(db)

    # Sourced from the canonical arr_waterfall table (same source
    # GET /ml/forecast/arr-waterfall and GET /analytics/arr-waterfall use) --
    # not the double-counted Revenue-reconstruction build_arr_waterfall() used
    # to call. See backend/routers/forecasting.py's arr_waterfall() docstring
    # for the mechanics of that bug.
    series = await calculators.calc_arr_waterfall_series(db, months=9999)
    periods = [s["period"] for s in series]
    net_new_arr_series = [s["net_new_arr"] for s in series]
    arr_start_series = [s["arr_start"] for s in series]
    expansion_series = [s["expansion"] for s in series]
    contraction_series = [s["contraction"] for s in series]
    churn_series = [s["churn"] for s in series]

    # Latest rolling 12-month NRR, computed from the continuous arr_start series.
    latest_nrr = None
    for i in range(len(periods) - 1, 10, -1):
        window_start_arr = sum(arr_start_series[i - 11:i + 1]) / 12
        if window_start_arr > 0:
            window_exp = sum(expansion_series[i - 11:i + 1])
            window_con = sum(contraction_series[i - 11:i + 1])
            window_churn = sum(churn_series[i - 11:i + 1])
            latest_nrr = round((window_start_arr + window_exp + window_con + window_churn) / window_start_arr * 100, 2)
            break

    health = _arr_health(nrr["nrr_pct"], arr_growth["arr_growth_pct"])
    warnings: list[str] = []
    if health in {"watch", "at_risk"}:
        warnings.append(f"ARR trajectory health is {health}.")

    data = {
        "nrr_pct": nrr["nrr_pct"],
        "grr_pct": grr["grr_pct"],
        "arr_growth_pct": arr_growth["arr_growth_pct"],
        "arr_current_12m": arr_growth.get("arr_current_12m", 0),
        "arr_prior_12m": arr_growth.get("arr_prior_12m", 0),
        "latest_rolling_nrr": latest_nrr,
        "waterfall_periods": periods[-6:],
        "net_new_arr_recent": net_new_arr_series[-6:],
        "health_assessment": health,
        "components": nrr.get("components", {}),
    }
    return _as_tool_result(
        "get_arr_trajectory",
        "warning" if warnings else "success",
        data,
        warnings,
        ["revenue", "arr_waterfall", "bookings", "churn_events"],
    )


def _arr_health(nrr: float, growth: float) -> str:
    if nrr >= 120 and growth >= 30:
        return "excellent"
    if nrr >= 100 and growth >= 15:
        return "healthy"
    if nrr >= 90 and growth >= 5:
        return "watch"
    return "at_risk"


async def get_rep_ramp_status(db: AsyncSession) -> dict[str, Any]:
    """Return ramp status for all reps based on hire date and attainment trajectory."""
    from datetime import date
    from backend.data_generator import _ramp_factor

    rep_rows = (await db.execute(select(Rep))).scalars().all()
    today = date.today()
    ramping = []
    fully_ramped = []

    for rep in rep_rows:
        hire_date = rep.hire_date
        if not hire_date:
            continue
        months_since_hire = (today.year - hire_date.year) * 12 + (today.month - hire_date.month)
        rf = _ramp_factor(hire_date, today)
        is_ramping = rf < 1.0

        filters = {"rep_id": rep.id}
        rev = await calculators.get_total_revenue(db, filters)
        quota = await calculators.get_total_quota(db, filters)
        attainment = (rev["value"] / quota["value"] * 100) if quota["value"] > 0 else 0

        entry = {
            "rep_id": str(rep.id),
            "rep_name": rep.name,
            "hire_date": str(hire_date),
            "months_since_hire": months_since_hire,
            "ramp_factor": rf,
            "is_ramping": is_ramping,
            "attainment_vs_ramped_quota": round(attainment, 1),
            "expected_ramp_completion_month": max(0, 6 - months_since_hire) if is_ramping else 0,
        }
        if is_ramping:
            ramping.append(entry)
        else:
            fully_ramped.append(entry)

    warnings: list[str] = []
    if len(ramping) > 0:
        warnings.append(f"{len(ramping)} reps are still in ramp period.")

    data = {
        "ramping_rep_count": len(ramping),
        "fully_ramped_rep_count": len(fully_ramped),
        "ramping_reps": sorted(ramping, key=lambda x: x["months_since_hire"]),
        "note": "Ramp schedule: 25% at month 0, 50% at month 2, 100% at month 6+",
    }
    return _as_tool_result(
        "get_rep_ramp_status",
        "warning" if warnings else "success",
        data,
        warnings,
        ["reps", "revenue", "quotas"],
    )
