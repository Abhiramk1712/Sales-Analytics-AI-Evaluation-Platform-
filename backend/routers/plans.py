"""
backend/routers/plans.py
========================
Plans, Rules, and Territory performance endpoints.

GET /plans
GET /plans/{plan_id}
GET /plans/{plan_id}/rules
GET /plans/{plan_id}/assignments
GET /plans/{plan_id}/performance
GET /rules
GET /rules/{rule_id}
GET /rules/{rule_id}/impact
GET /territories
GET /territories/{territory_id}/performance
"""
from __future__ import annotations

from datetime import date, datetime
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models import (
    Deal,
    Plan,
    PlanAssignment,
    Quota,
    Rep,
    Revenue,
    Rule,
    SalesCredit,
    Territory,
    UserProfile,
    UserTerritoryAssignment,
)
from backend.services.quota_attainment_service import (
    get_quota_for_period,
    normalize_period,
    period_to_months,
)
from backend.auth.dependencies import require_permission
from backend.auth.tenant import get_tenant_context
from backend.utils.identity_mapping import get_rep_ids_for_user_ids
from backend.utils.date_ranges import parse_period_to_range

router = APIRouter(
    prefix="/plans",
    tags=["Plans & Rules"],
    dependencies=[Depends(require_permission("view_plans")), Depends(get_tenant_context)],
)
territory_router = APIRouter(
    prefix="/territories",
    tags=["Territories"],
    dependencies=[Depends(require_permission("view_dashboard")), Depends(get_tenant_context)],
)


def _months_for_period(period: str | None) -> list[str]:
    """Resolve period input to canonical YYYY-MM month keys for revenue filters."""
    if not period:
        return []
    try:
        norm = normalize_period(period)
    except ValueError:
        return []
    return period_to_months(norm or "")


def _is_all_time_period(period: str | None) -> bool:
    """Recognize all-time aliases that should bypass strict period normalization."""
    if not period:
        return False
    return period.strip().lower() in {"all time", "all-time", "all", "alltime"}


def _region_bucket(value: str | None) -> str | None:
    """Map region labels to coarse buckets so fallback assignment stays consistent."""
    if not value:
        return None

    token = value.strip().lower()
    if token in {"north america", "north-america", "na", "americas", "east", "west", "central"}:
        return "north_america"
    if "north america" in token:
        return "north_america"
    if token in {"emea", "europe", "middle east", "africa"} or "emea" in token:
        return "emea"
    if token in {"apac", "asia pacific", "anz", "asia"} or "apac" in token:
        return "apac"
    if token in {"latam", "latin america"} or "latam" in token:
        return "latam"

    return token


async def _collect_territory_scope_ids(db: AsyncSession, territory_id: uuid.UUID) -> list[uuid.UUID]:
    """Return territory id plus all descendants for hierarchy-aware rollups."""
    seen: set[uuid.UUID] = {territory_id}
    scope_ids: list[uuid.UUID] = [territory_id]
    frontier: list[uuid.UUID] = [territory_id]

    while frontier:
        child_ids = (
            await db.execute(select(Territory.id).where(Territory.parent_territory_id.in_(frontier)))
        ).scalars().all()
        next_frontier: list[uuid.UUID] = []
        for child_id in child_ids:
            if child_id in seen:
                continue
            seen.add(child_id)
            scope_ids.append(child_id)
            next_frontier.append(child_id)
        frontier = next_frontier

    return scope_ids


# ── Plans ──────────────────────────────────────────────────────────────────


@router.get("")
async def list_plans(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """List all compensation plans."""
    plans = (await db.execute(select(Plan).order_by(Plan.name))).scalars().all()
    return {
        "plans": [
            {
                "id": str(p.id),
                "name": p.name,
                "scope": p.scope,
                "effective_start_date": p.effective_start_date.isoformat() if p.effective_start_date else None,
                "effective_end_date": p.effective_end_date.isoformat() if p.effective_end_date else None,
                "owner_user_id": str(p.owner_user_id) if p.owner_user_id else None,
            }
            for p in plans
        ]
    }


@router.get("/{plan_id}")
async def get_plan(plan_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Get a single plan by ID."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid plan ID") from exc
    plan = await db.get(Plan, pid)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    return {
        "id": str(plan.id),
        "name": plan.name,
        "scope": plan.scope,
        "effective_start_date": plan.effective_start_date.isoformat() if plan.effective_start_date else None,
        "effective_end_date": plan.effective_end_date.isoformat() if plan.effective_end_date else None,
        "owner_user_id": str(plan.owner_user_id) if plan.owner_user_id else None,
    }


@router.get("/{plan_id}/rules")
async def get_plan_rules(plan_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Get all rules for a plan."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid plan ID") from exc
    rules = (
        await db.execute(select(Rule).where(Rule.plan_id == pid).order_by(Rule.name))
    ).scalars().all()
    return {
        "plan_id": plan_id,
        "rules": [
            {
                "id": str(r.id),
                "name": r.name,
                "metric_name": r.metric_name,
                "threshold_min": float(r.threshold_min) if r.threshold_min is not None else None,
                "threshold_max": float(r.threshold_max) if r.threshold_max is not None else None,
                "rate": float(r.rate) if r.rate is not None else None,
                "bonus_amount": float(r.bonus_amount) if r.bonus_amount is not None else None,
            }
            for r in rules
        ],
    }


@router.get("/{plan_id}/assignments")
async def get_plan_assignments(plan_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Get users assigned to a plan."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid plan ID") from exc
    assignments = (
        await db.execute(select(PlanAssignment).where(PlanAssignment.plan_id == pid))
    ).scalars().all()
    result = []
    for a in assignments:
        user = await db.get(UserProfile, a.user_id)
        result.append({
            "assignment_id": str(a.id),
            "user_id": str(a.user_id),
            "user_name": user.name if user else str(a.user_id),
            "effective_start_date": a.effective_start_date.isoformat() if a.effective_start_date else None,
            "effective_end_date": a.effective_end_date.isoformat() if a.effective_end_date else None,
        })
    return {"plan_id": plan_id, "assignments": result, "total": len(result)}


@router.get("/{plan_id}/performance")
async def get_plan_performance(
    plan_id: str,
    period: str = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return performance summary for a plan in a given period."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(plan_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid plan ID") from exc
    plan = await db.get(Plan, pid)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Get assigned users
    assignments = (
        await db.execute(select(PlanAssignment).where(PlanAssignment.plan_id == pid))
    ).scalars().all()
    user_ids = [a.user_id for a in assignments]

    # UserProfile ↔ Rep mapping is email-based in this schema.
    rep_ids = await get_rep_ids_for_user_ids(db, user_ids)

    all_time_requested = _is_all_time_period(period)
    effective_period = None if all_time_requested else period

    months = _months_for_period(effective_period)
    rev_stmt = select(func.coalesce(func.sum(Revenue.amount), 0.0))
    if rep_ids:
        rev_stmt = rev_stmt.where(Revenue.rep_id.in_(rep_ids))
    else:
        rev_stmt = rev_stmt.where(Revenue.rep_id.is_(None))  # no mapped reps
    if months:
        rev_stmt = rev_stmt.where(Revenue.period.in_(months))
    total_rev = float((await db.execute(rev_stmt)).scalar() or 0.0)

    # Canonical quota semantics via quota_attainment_service (monthly/quarterly/annual fallbacks).
    total_quota = 0.0
    quota_warnings: list[str] = []
    if effective_period and rep_ids:
        for rep_id in rep_ids:
            rep_quota, q_source, q_warnings = await get_quota_for_period(db, effective_period, rep_id=rep_id)
            total_quota += float(rep_quota or 0.0)
            if q_source != "direct":
                quota_warnings.append(f"rep={rep_id}: quota_source={q_source}")
            quota_warnings.extend(q_warnings)
    elif rep_ids:
        # No period given: sum every quota row for these reps, which is exactly
        # what the revenue query above does when `months` is empty. Previously
        # this branch required all_time to be asked for explicitly, so a request
        # with no period returned revenue over all time divided by a quota of
        # zero — the Plans tab showed $2.9M revenue at 0.0% attainment.
        # Both sides of the ratio must share a grain or the ratio is meaningless.
        quota_rows = (
            await db.execute(
                select(Quota.rep_id, func.coalesce(func.sum(Quota.amount), 0.0).label("quota"))
                .where(Quota.rep_id.in_(rep_ids))
                .group_by(Quota.rep_id)
            )
        ).all()
        total_quota = sum(float(r.quota or 0.0) for r in quota_rows)

    attainment = round((total_rev / total_quota * 100), 2) if total_quota > 0 else 0.0

    monthly_stmt = (
        select(Revenue.period, func.coalesce(func.sum(Revenue.amount), 0.0).label("revenue"))
        .group_by(Revenue.period)
        .order_by(Revenue.period)
    )
    if rep_ids:
        monthly_stmt = monthly_stmt.where(Revenue.rep_id.in_(rep_ids))
    else:
        monthly_stmt = monthly_stmt.where(Revenue.rep_id.is_(None))
    if months:
        monthly_stmt = monthly_stmt.where(Revenue.period.in_(months))
    monthly_rows = (await db.execute(monthly_stmt)).all()

    top_rep_stmt = (
        select(Rep.id, Rep.name, func.coalesce(func.sum(Revenue.amount), 0.0).label("revenue"))
        .join(Revenue, Revenue.rep_id == Rep.id)
        .group_by(Rep.id, Rep.name)
        .order_by(func.coalesce(func.sum(Revenue.amount), 0.0).desc())
        .limit(5)
    )
    if rep_ids:
        top_rep_stmt = top_rep_stmt.where(Rep.id.in_(rep_ids))
    else:
        top_rep_stmt = top_rep_stmt.where(Rep.id.is_(None))
    if months:
        top_rep_stmt = top_rep_stmt.where(Revenue.period.in_(months))
    top_rep_rows = (await db.execute(top_rep_stmt)).all()

    return {
        "plan_id": plan_id,
        "plan_name": plan.name,
        "period": period,
        "assigned_users": len(user_ids),
        "rep_count": len(rep_ids),
        "total_revenue": total_rev,
        "total_quota": total_quota,
        # Compatibility aliases consumed by existing frontend pages.
        "revenue": total_rev,
        "quota": total_quota,
        "attainment_pct": attainment,
        "monthly_revenue": [
            {"period": r.period, "revenue": float(r.revenue or 0.0)}
            for r in monthly_rows
        ],
        "top_reps": [
            {
                "rep_id": str(r.id),
                "name": r.name,
                "revenue": float(r.revenue or 0.0),
            }
            for r in top_rep_rows
        ],
        "warnings": (
            quota_warnings if quota_warnings else ([] if total_quota > 0 else ["No quota data found for this plan/period"])
        ),
    }


# ── Rules ──────────────────────────────────────────────────────────────────


@router.get("/rules/all")
async def list_all_rules(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """List all compensation rules across all plans."""
    rules = (await db.execute(select(Rule).order_by(Rule.plan_id, Rule.name))).scalars().all()
    return {
        "rules": [
            {
                "id": str(r.id),
                "plan_id": str(r.plan_id),
                "name": r.name,
                "metric_name": r.metric_name,
                "rate": float(r.rate) if r.rate is not None else None,
                "threshold_min": float(r.threshold_min) if r.threshold_min is not None else None,
                "threshold_max": float(r.threshold_max) if r.threshold_max is not None else None,
                "bonus_amount": float(r.bonus_amount) if r.bonus_amount is not None else None,
            }
            for r in rules
        ]
    }


# ── Territories ────────────────────────────────────────────────────────────


@territory_router.get("")
async def list_territories(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """List all territories."""
    terr = (await db.execute(select(Territory).order_by(Territory.name))).scalars().all()
    return {
        "territories": [
            {
                "id": str(t.id),
                "name": t.name,
                "region": t.region,
                "segment": t.segment,
                "territory_code": t.territory_code,
            }
            for t in terr
        ]
    }


@territory_router.get("/{territory_id}/performance")
async def get_territory_performance(
    territory_id: str,
    period: str = Query(None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return performance metrics for a territory."""
    import uuid as _uuid
    try:
        tid = _uuid.UUID(territory_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid territory ID") from exc
    territory = await db.get(Territory, tid)
    if territory is None:
        raise HTTPException(status_code=404, detail="Territory not found")

    all_time_requested = _is_all_time_period(period)
    effective_period = None if all_time_requested else period

    # Resolve territory hierarchy so parent territory views include child assignments.
    scope_territory_ids = await _collect_territory_scope_ids(db, tid)

    # Get reps assigned to this territory scope.
    assignments = (
        await db.execute(
            select(UserTerritoryAssignment).where(
                UserTerritoryAssignment.territory_id.in_(scope_territory_ids)
            )
        )
    ).scalars().all()
    user_ids = [a.user_id for a in assignments]

    rep_ids = await get_rep_ids_for_user_ids(db, user_ids)
    used_region_fallback = False

    # Fallback for datasets without users/user_territory_assignments.
    if not rep_ids:
        scope_rows = (
            await db.execute(
                select(Territory.region, Territory.name).where(Territory.id.in_(scope_territory_ids))
            )
        ).all()
        scope_buckets = {
            bucket
            for region, name in scope_rows
            for bucket in [_region_bucket(region), _region_bucket(name)]
            if bucket
        }
        if scope_buckets:
            rep_rows = (await db.execute(select(Rep.id, Rep.region))).all()
            rep_ids = [r.id for r in rep_rows if _region_bucket(r.region) in scope_buckets]
            used_region_fallback = bool(rep_ids)

    if not rep_ids:
        return {
            "territory_id": territory_id,
            "territory_name": territory.name,
            "region": territory.region,
            "segment": territory.segment,
            "period": period,
            "assigned_reps": 0,
            "total_revenue": 0.0,
            "total_quota": 0.0,
            "closed_won_deals": 0,
            "revenue": 0.0,
            "deals_won": 0,
            "win_rate": 0.0,
            "open_pipeline": 0.0,
            "pipeline_hygiene": {
                "overdue_count": 0,
                "missing_close_date_count": 0,
                "high_prob_early_stage_count": 0,
            },
            "reps": [],
            "warnings": ["No reps assigned to this territory"],
        }

    # Revenue
    date_filter: dict[str, date] = {}
    if effective_period:
        try:
            pr = parse_period_to_range(effective_period)
            if pr:
                date_filter = {
                    "start": datetime.strptime(pr.start_date, "%Y-%m-%d").date(),
                    "end": datetime.strptime(pr.end_date, "%Y-%m-%d").date(),
                }
        except ValueError:
            pass

    months = _months_for_period(effective_period)

    rev_stmt = select(func.coalesce(func.sum(Revenue.amount), 0.0)).where(Revenue.rep_id.in_(rep_ids))
    if months:
        rev_stmt = rev_stmt.where(Revenue.period.in_(months))
    total_rev = float((await db.execute(rev_stmt)).scalar() or 0.0)

    # Deals
    deal_stmt = select(func.count(Deal.id)).where(
        Deal.rep_id.in_(rep_ids),
        Deal.stage == "Closed Won",
    )
    if date_filter:
        deal_stmt = deal_stmt.where(
            Deal.actual_close_date >= date_filter["start"],
            Deal.actual_close_date <= date_filter["end"],
        )
    deal_count = int((await db.execute(deal_stmt)).scalar() or 0)

    lost_stmt = select(func.count(Deal.id)).where(
        Deal.rep_id.in_(rep_ids),
        Deal.stage == "Closed Lost",
    )
    if date_filter:
        lost_stmt = lost_stmt.where(
            Deal.actual_close_date >= date_filter["start"],
            Deal.actual_close_date <= date_filter["end"],
        )
    lost_count = int((await db.execute(lost_stmt)).scalar() or 0)

    open_stmt = select(func.coalesce(func.sum(Deal.amount), 0.0)).where(
        Deal.rep_id.in_(rep_ids),
        ~Deal.stage.in_(["Closed Won", "Closed Lost"]),
    )
    if date_filter:
        open_stmt = open_stmt.where(
            Deal.expected_close_date >= date_filter["start"],
            Deal.expected_close_date <= date_filter["end"],
        )
    open_pipeline = float((await db.execute(open_stmt)).scalar() or 0.0)

    today = date.today()
    overdue_count = int(
        (
            await db.execute(
                select(func.count(Deal.id)).where(
                    Deal.rep_id.in_(rep_ids),
                    ~Deal.stage.in_(["Closed Won", "Closed Lost"]),
                    Deal.expected_close_date.is_not(None),
                    Deal.expected_close_date < today,
                )
            )
        ).scalar()
        or 0
    )
    missing_close_date_count = int(
        (
            await db.execute(
                select(func.count(Deal.id)).where(
                    Deal.rep_id.in_(rep_ids),
                    ~Deal.stage.in_(["Closed Won", "Closed Lost"]),
                    Deal.expected_close_date.is_(None),
                )
            )
        ).scalar()
        or 0
    )
    high_prob_early_stage_count = int(
        (
            await db.execute(
                select(func.count(Deal.id)).where(
                    Deal.rep_id.in_(rep_ids),
                    Deal.stage.in_(["Prospecting", "Qualification"]),
                    Deal.close_probability >= 70,
                )
            )
        ).scalar()
        or 0
    )

    rep_rows = (
        await db.execute(select(Rep.id, Rep.name).where(Rep.id.in_(rep_ids)))
    ).all()
    rep_name_by_id = {str(r.id): r.name for r in rep_rows}

    rep_revenue_rows = (
        await db.execute(
            select(Revenue.rep_id, func.coalesce(func.sum(Revenue.amount), 0.0).label("revenue"))
            .where(Revenue.rep_id.in_(rep_ids))
            .group_by(Revenue.rep_id)
        )
    ).all()
    if months:
        rep_revenue_rows = (
            await db.execute(
                select(Revenue.rep_id, func.coalesce(func.sum(Revenue.amount), 0.0).label("revenue"))
                .where(Revenue.rep_id.in_(rep_ids), Revenue.period.in_(months))
                .group_by(Revenue.rep_id)
            )
        ).all()
    revenue_by_rep = {str(r.rep_id): float(r.revenue or 0.0) for r in rep_revenue_rows}

    won_by_rep = {
        str(r.rep_id): int(r.count or 0)
        for r in (
            await db.execute(
                select(Deal.rep_id, func.count(Deal.id).label("count"))
                .where(Deal.rep_id.in_(rep_ids), Deal.stage == "Closed Won")
                .group_by(Deal.rep_id)
            )
        ).all()
    }
    lost_by_rep = {
        str(r.rep_id): int(r.count or 0)
        for r in (
            await db.execute(
                select(Deal.rep_id, func.count(Deal.id).label("count"))
                .where(Deal.rep_id.in_(rep_ids), Deal.stage == "Closed Lost")
                .group_by(Deal.rep_id)
            )
        ).all()
    }
    open_by_rep = {
        str(r.rep_id): float(r.amount or 0.0)
        for r in (
            await db.execute(
                select(Deal.rep_id, func.coalesce(func.sum(Deal.amount), 0.0).label("amount"))
                .where(Deal.rep_id.in_(rep_ids), ~Deal.stage.in_(["Closed Won", "Closed Lost"]))
                .group_by(Deal.rep_id)
            )
        ).all()
    }

    quota_by_rep: dict[str, float] = {}
    if effective_period:
        for rep_id in rep_ids:
            rep_quota, _source, _warnings = await get_quota_for_period(db, effective_period, rep_id=rep_id)
            quota_by_rep[str(rep_id)] = float(rep_quota or 0.0)
    else:
        quota_rows = (
            await db.execute(
                select(Quota.rep_id, func.coalesce(func.sum(Quota.amount), 0.0).label("quota"))
                .where(Quota.rep_id.in_(rep_ids))
                .group_by(Quota.rep_id)
            )
        ).all()
        quota_by_rep = {str(r.rep_id): float(r.quota or 0.0) for r in quota_rows}

    reps = []
    for rep_id in rep_ids:
        rid = str(rep_id)
        rep_revenue = revenue_by_rep.get(rid, 0.0)
        rep_quota = quota_by_rep.get(rid, 0.0)
        rep_won = won_by_rep.get(rid, 0)
        rep_lost = lost_by_rep.get(rid, 0)
        rep_total_closed = rep_won + rep_lost
        rep_win_rate = (rep_won / rep_total_closed * 100.0) if rep_total_closed > 0 else 0.0
        rep_attainment = (rep_revenue / rep_quota * 100.0) if rep_quota > 0 else 0.0
        reps.append(
            {
                "rep_id": rid,
                "name": rep_name_by_id.get(rid, rid),
                "revenue": round(rep_revenue, 2),
                "quota": round(rep_quota, 2),
                "attainment_pct": round(rep_attainment, 2),
                "deals_won": rep_won,
                "deals_lost": rep_lost,
                "win_rate": round(rep_win_rate, 2),
                "open_pipeline": round(open_by_rep.get(rid, 0.0), 2),
            }
        )

    total_quota = sum(quota_by_rep.values())
    win_rate = (deal_count / (deal_count + lost_count) * 100.0) if (deal_count + lost_count) > 0 else 0.0

    return {
        "territory_id": territory_id,
        "territory_name": territory.name,
        "region": territory.region,
        "segment": getattr(territory, "segment", None),
        "period": period,
        "assigned_reps": len(rep_ids),
        "total_revenue": total_rev,
        "total_quota": round(total_quota, 2),
        "closed_won_deals": deal_count,
        # Compatibility aliases consumed by frontend territory page.
        "revenue": round(total_rev, 2),
        "deals_won": deal_count,
        "win_rate": round(win_rate, 2),
        "open_pipeline": round(open_pipeline, 2),
        "pipeline_hygiene": {
            "overdue_count": overdue_count,
            "missing_close_date_count": missing_close_date_count,
            "high_prob_early_stage_count": high_prob_early_stage_count,
        },
        "reps": reps,
        "warnings": ["Rep coverage inferred from region due to missing territory assignments"] if used_region_fallback else ([] if rep_ids else ["No reps assigned to this territory"]),
    }
