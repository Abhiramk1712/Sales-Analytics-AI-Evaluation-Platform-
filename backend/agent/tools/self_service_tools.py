"""
backend/agent/tools/self_service_tools.py
==========================================
Identity-scoped tools for the AI Agent's sales_rep / sales_manager access
tier. Every other tool in backend/agent/tools/ operates company-wide, with
no rep_id/team parameter at all -- fine for executive/revops_admin, who
hold view_payouts/run_agent_workflow, but not safe to hand to a sales_rep
or sales_manager, whose real permissions (view_own_metrics, view_own_payout)
only ever cover their own -- or their team's -- numbers.

These tools take an explicit rep_id / list of rep_ids and never look beyond
it. backend/agent/executor.py's role-based routing (see
_execute_self_service) is what guarantees a sales_rep/sales_manager's
evidence pool is built ONLY from these functions -- the scoping lives here
in what data gets fetched, not in trusting the LLM to stay in its lane once
handed company-wide results.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.metrics import calculators
from backend.models import Deal, Quota, Rep, Revenue
from backend.payout import compute_payout


def _as_tool_result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


async def _rep_own_plan_config(db: AsyncSession, rep: Rep):
    """Resolve one rep's own assigned Plan/Rule config, same resolution
    path as /payout/team-summary and the fixed get_payout_summary --
    None when the rep has no PlanAssignment, so compute_payout() falls
    back to its own default engine exactly as it already does for any
    unassigned rep."""
    from backend.models import PlanAssignment, UserProfile
    from backend.routers.payout import _load_plan_configs

    user = (await db.execute(select(UserProfile).where(func.lower(UserProfile.email) == (rep.email or "").lower()))).scalars().first()
    if not user:
        return None
    assignment = (await db.execute(select(PlanAssignment).where(PlanAssignment.user_id == user.id))).scalars().first()
    if not assignment:
        return None
    plan_configs = await _load_plan_configs(db)
    return plan_configs.get(assignment.plan_id)


async def get_my_performance_summary(db: AsyncSession, rep_id: str) -> dict[str, Any]:
    """This rep's own revenue/quota/attainment/win-rate/pipeline -- the
    sales_rep equivalent of get_top_reps, scoped to exactly one rep_id
    supplied by the caller (never derived from free text)."""
    result = await calculators.get_rep_performance(db, rep_id=rep_id)
    if not result["data"]:
        return _as_tool_result("get_my_performance_summary", "warning", None, ["Rep not found"], ["reps"])
    return _as_tool_result(
        "get_my_performance_summary", "warning" if result["warnings"] else "success",
        result["data"], result["warnings"], ["reps", "revenue", "quotas", "deals"],
    )


async def get_my_payout_summary(db: AsyncSession, rep_id: str) -> dict[str, Any]:
    """This rep's own payout, computed with their real assigned plan
    config (see _rep_own_plan_config) -- the same fix applied to
    get_payout_summary, scoped to one rep instead of the whole company."""
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalars().first()
    if not rep:
        return _as_tool_result("get_my_payout_summary", "warning", None, ["Rep not found"], ["reps"])

    revenue = float((await db.execute(select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id))).scalar() or 0.0)
    quota = float((await db.execute(select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id))).scalar() or 0.0)
    deals_won = int((await db.execute(select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won"))).scalar() or 0)
    deals_lost = int((await db.execute(select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost"))).scalar() or 0)

    cfg = await _rep_own_plan_config(db, rep)
    result = compute_payout(revenue, quota, deals_won, deals_lost, cfg)
    data = {
        "rep_id": str(rep.id),
        "name": rep.name,
        "revenue": round(revenue, 2),
        "quota": round(quota, 2),
        **result,
    }
    return _as_tool_result("get_my_payout_summary", "success", data, [], ["payout_engine", "revenue_table", "quota_table", "deals_table"])


async def get_team_performance_summary(db: AsyncSession, rep_ids: list[str]) -> dict[str, Any]:
    """A sales_manager's own team, aggregated the same way
    TeamCommandCenterPage.jsx does client-side -- summed revenue/quota/
    attainment plus each member's own row, scoped to exactly the rep_ids
    the caller's team picker resolved (never the whole company)."""
    if not rep_ids:
        return _as_tool_result("get_team_performance_summary", "warning", None, ["No team selected"], ["reps"])

    rows = []
    warnings: list[str] = []
    for rep_id in rep_ids:
        perf = await calculators.get_rep_performance(db, rep_id=rep_id)
        if perf["data"]:
            rows.append(perf["data"])
        warnings.extend(perf["warnings"])

    total_revenue = sum(r["revenue"] for r in rows)
    total_quota = sum(r["quota"] for r in rows)
    data = {
        "team_size": len(rows),
        "total_revenue": round(total_revenue, 2),
        "total_quota": round(total_quota, 2),
        "team_attainment_pct": round((total_revenue / total_quota) * 100, 2) if total_quota > 0 else 0.0,
        "reps": sorted(rows, key=lambda r: r["attainment_pct"], reverse=True),
    }
    return _as_tool_result("get_team_performance_summary", "warning" if warnings else "success", data, warnings, ["reps", "revenue", "quotas", "deals"])


async def get_team_payout_summary(db: AsyncSession, rep_ids: list[str]) -> dict[str, Any]:
    """A sales_manager's own team's payout, each member computed with
    their own real plan config -- same approach as get_my_payout_summary,
    looped over the team roster instead of one rep."""
    if not rep_ids:
        return _as_tool_result("get_team_payout_summary", "warning", None, ["No team selected"], ["reps"])

    rows = []
    total_payout = 0.0
    for rep_id in rep_ids:
        row = await get_my_payout_summary(db, rep_id)
        if row["data"]:
            rows.append(row["data"])
            total_payout += float(row["data"].get("payout") or 0.0)

    data = {
        "team_size": len(rows),
        "total_payout": round(total_payout, 2),
        "rows": sorted(rows, key=lambda r: r.get("payout", 0.0), reverse=True),
    }
    return _as_tool_result("get_team_payout_summary", "success", data, [], ["payout_engine", "revenue_table", "quota_table", "deals_table"])
