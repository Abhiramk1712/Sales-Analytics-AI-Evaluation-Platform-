"""
Plan performance tools for agent evidence collection.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Plan, Rule
from backend.routers.plans import get_plan_performance


def _result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


def _normalize(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _extract_plan_number(message: str) -> int | None:
    msg = _normalize(message)
    match = re.search(r"\bplan\s*#?\s*(\d+)\b", msg)
    if match:
        return int(match.group(1))
    return None


def _extract_period(message: str) -> str | None:
    msg = _normalize(message)

    natural_aliases = [
        "all time",
        "this quarter",
        "last quarter",
        "this month",
        "last month",
        "this year",
        "last year",
        "ytd",
        "qtd",
        "mtd",
    ]
    for alias in natural_aliases:
        if alias in msg:
            return alias

    fy_match = re.search(r"\bfy\s*(20\d{2})\b", msg)
    if fy_match:
        return fy_match.group(1)

    year_match = re.search(r"\b(20\d{2})\b", msg)
    if year_match:
        return year_match.group(1)

    quarter_match = re.search(r"\b(20\d{2})\s*-?\s*q([1-4])\b", msg)
    if quarter_match:
        return f"{quarter_match.group(1)}-Q{quarter_match.group(2)}"

    month_match = re.search(r"\b(20\d{2})[-/](0[1-9]|1[0-2])\b", msg)
    if month_match:
        return f"{month_match.group(1)}-{month_match.group(2)}"

    return None


def _plan_match_score(plan_name: str, message: str) -> int:
    norm_name = _normalize(plan_name)
    norm_msg = _normalize(message)

    score = 0
    plan_number = _extract_plan_number(norm_msg)
    fy_match = re.search(r"\bfy\s*(20\d{2})\b", norm_msg)

    if plan_number is not None and f"plan {plan_number}" in norm_name:
        score += 12

    if fy_match and fy_match.group(1) in norm_name:
        score += 8

    if norm_name in norm_msg:
        score += 20

    # Token overlap (lightweight fuzzy scoring)
    msg_tokens = {t for t in re.findall(r"[a-z0-9]+", norm_msg) if len(t) >= 2}
    name_tokens = {t for t in re.findall(r"[a-z0-9]+", norm_name) if len(t) >= 2}
    score += len(msg_tokens.intersection(name_tokens))

    return score


async def _resolve_plan(db: AsyncSession, message: str) -> tuple[Plan | None, list[str], list[str]]:
    plans = (await db.execute(select(Plan).order_by(Plan.name))).scalars().all()
    if not plans:
        return None, [], ["No plans found in database."]

    scored: list[tuple[int, Plan]] = []
    for plan in plans:
        score = _plan_match_score(plan.name or "", message)
        if score > 0:
            scored.append((score, plan))

    if not scored:
        candidates = [p.name for p in plans[:5]]
        return None, candidates, ["Could not match the request to a known plan name."]

    scored.sort(key=lambda x: (x[0], x[1].name or ""), reverse=True)
    best_score = scored[0][0]
    top_matches = [plan for score, plan in scored if score == best_score]

    if len(top_matches) > 1 and best_score < 15:
        candidates = [p.name for p in top_matches[:5]]
        return None, candidates, ["Plan reference is ambiguous. Please specify full plan name."]

    return top_matches[0], [p.name for p in plans[:5]], []


async def get_plan_performance_summary(db: AsyncSession, message: str) -> dict[str, Any]:
    """Resolve plan from user message and return performance summary for the inferred period."""
    plan, candidates, resolve_warnings = await _resolve_plan(db, message)
    if plan is None:
        return _result(
            "get_plan_performance_summary",
            "warning",
            {
                "matched_plan": None,
                "period_used": _extract_period(message),
                "performance": None,
                "candidate_plans": candidates,
            },
            resolve_warnings,
            ["plans", "plan_assignments", "revenue", "quotas"],
        )

    period = _extract_period(message)

    try:
        perf = await get_plan_performance(plan_id=str(plan.id), period=period, db=db)
    except Exception as exc:
        return _result(
            "get_plan_performance_summary",
            "error",
            {
                "matched_plan": {"plan_id": str(plan.id), "name": plan.name},
                "period_used": period,
                "performance": None,
            },
            [f"Failed to compute plan performance: {exc}"],
            ["plans", "plan_assignments", "revenue", "quotas"],
        )

    warnings = list(perf.get("warnings", []))
    if perf.get("rep_count", 0) == 0:
        warnings.append("No mapped reps found for this plan in the selected period.")

    data = {
        "matched_plan": {
            "plan_id": str(plan.id),
            "name": plan.name,
        },
        "period_used": period,
        "performance": {
            "total_revenue": float(perf.get("total_revenue", 0.0) or 0.0),
            "total_quota": float(perf.get("total_quota", 0.0) or 0.0),
            "attainment_pct": float(perf.get("attainment_pct", 0.0) or 0.0),
            "assigned_users": int(perf.get("assigned_users", 0) or 0),
            "rep_count": int(perf.get("rep_count", 0) or 0),
            "period": perf.get("period") or period,
        },
        "top_reps": list(perf.get("top_reps", []))[:5],
        "monthly_revenue": list(perf.get("monthly_revenue", []))[-6:],
    }

    return _result(
        "get_plan_performance_summary",
        "warning" if warnings else "success",
        data,
        sorted(set(warnings)),
        ["plans", "plan_assignments", "users", "revenue", "quotas"],
    )


async def get_plans_rules_catalog(
    db: AsyncSession,
    max_plans: int = 25,
    max_rules: int = 80,
) -> dict[str, Any]:
    """Return a compact catalog of available plans and rules for discovery questions."""
    plan_rows = (await db.execute(select(Plan).order_by(Plan.name))).scalars().all()
    rule_rows = (await db.execute(select(Rule).order_by(Rule.plan_id, Rule.name))).scalars().all()

    plan_name_by_id: dict[Any, str] = {
        p.id: p.name or "Unnamed Plan"
        for p in plan_rows
    }

    rules_per_plan: dict[Any, int] = {}
    for rule in rule_rows:
        rules_per_plan[rule.plan_id] = rules_per_plan.get(rule.plan_id, 0) + 1

    plans_payload = [
        {
            "plan_id": str(p.id),
            "name": p.name,
            "scope": p.scope,
            "effective_start_date": p.effective_start_date.isoformat() if p.effective_start_date else None,
            "effective_end_date": p.effective_end_date.isoformat() if p.effective_end_date else None,
            "rule_count": int(rules_per_plan.get(p.id, 0)),
        }
        for p in plan_rows[: max(1, int(max_plans))]
    ]

    rules_payload = [
        {
            "rule_id": str(r.id),
            "plan_id": str(r.plan_id),
            "plan_name": plan_name_by_id.get(r.plan_id, "Unknown Plan"),
            "name": r.name,
            "metric_name": r.metric_name,
            "threshold_min": float(r.threshold_min) if r.threshold_min is not None else None,
            "threshold_max": float(r.threshold_max) if r.threshold_max is not None else None,
            "rate": float(r.rate) if r.rate is not None else None,
            "bonus_amount": float(r.bonus_amount) if r.bonus_amount is not None else None,
        }
        for r in rule_rows[: max(1, int(max_rules))]
    ]

    warnings: list[str] = []
    if not plan_rows:
        warnings.append("No compensation plans found in database.")
    if not rule_rows:
        warnings.append("No compensation rules found in database.")

    data = {
        "plan_count": len(plan_rows),
        "rule_count": len(rule_rows),
        "plans": plans_payload,
        "rules": rules_payload,
    }

    return _result(
        "get_plans_rules_catalog",
        "warning" if warnings else "success",
        data,
        warnings,
        ["plans", "rules"],
    )
