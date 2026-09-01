"""
All sales analytics REST endpoints.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.database import get_db
from backend.auth.dependencies import require_permission
from backend.auth.tenant import get_tenant_context
from backend.models import (
    Rep,
    Deal,
    Revenue,
    Account,
    Quota,
    Team,
    UserProfile,
    UserTerritoryAssignment,
    Territory,
    Plan,
    Rule,
    PlanAssignment,
    Activity,
    ArrWaterfallEntry,
    Position,
    Manager,
    Product,
    RepProductAssignment,
    PlanCascadeRule,
)
from backend.metrics.service import get_metrics_service
from backend.metrics import calculators
from backend.utils.date_ranges import parse_period_to_range
from backend.services.sales_performance_service import SalesPerformanceService
from backend.payout import compute_payout

router = APIRouter(
    prefix="/analytics",
    tags=["Analytics"],
    dependencies=[Depends(require_permission("view_dashboard")), Depends(get_tenant_context)],
)

# Position levels that don't carry a direct quota / closing responsibility.
# Reps at these levels appear in the org hierarchy but not in performance views.
_NON_SELLING_LEVELS: frozenset[str] = frozenset({"Executive", "Senior Leadership", "Leadership"})


async def _selling_rep_ids(db: AsyncSession) -> set[str]:
    """Return the set of rep IDs whose position is quota-carrying (IC or Management)."""
    from backend.models import UserProfile, Position
    user_rows = (await db.execute(select(UserProfile.email, UserProfile.position_id))).all()
    pos_ids = [u.position_id for u in user_rows if u.position_id]
    if not pos_ids:
        return set()  # no position data → include everyone
    pos_rows = (await db.execute(select(Position))).scalars().all()
    non_selling_pos_ids = {str(p.id) for p in pos_rows if p.level in _NON_SELLING_LEVELS}
    excluded_emails = {
        (u.email or "").lower()
        for u in user_rows
        if u.position_id and str(u.position_id) in non_selling_pos_ids
    }
    rep_rows = (await db.execute(select(Rep))).scalars().all()
    return {str(r.id) for r in rep_rows if (r.email or "").lower() not in excluded_emails}


def _period_to_filters(period: str | None) -> dict | None:
    """Parse period string → filters dict, or None for no date filter.
    Returns None for empty/None period AND for 'all time' variants.
    """
    if not period:
        return None
    try:
        period_range = parse_period_to_range(period)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if period_range is None:
        return None
    return {"start_date": period_range.start_date, "end_date": period_range.end_date}


def _confidence_label(coverage: float) -> str:
    if coverage >= 0.95:
        return "high"
    if coverage >= 0.65:
        return "medium"
    return "low"


@router.get("/kpis")
async def get_kpis(period: str = Query(None, description="e.g. '2025-04'"), db: AsyncSession = Depends(get_db)):
    filters = _period_to_filters(period)

    metrics = get_metrics_service()
    kpis = await metrics.get_kpis(db, filters=filters)
    open_deal_count = (await db.execute(select(func.count(Deal.id)).where(~Deal.stage.in_(["Closed Won", "Closed Lost"])))).scalar() or 0

    has_revenue = float(kpis.get("total_revenue", 0) or 0) > 0
    has_quota = float(kpis.get("total_quota", 0) or 0) > 0
    has_deals = int(kpis.get("deals_won", 0) or 0) + int(kpis.get("deals_lost", 0) or 0) + int(open_deal_count or 0) > 0
    coverage = sum(1 for v in [has_revenue, has_quota, has_deals] if v) / 3.0

    return {
        "total_revenue": kpis["total_revenue"],
        "total_quota": kpis["total_quota"] if kpis["total_quota"] else 1.0,
        "attainment_pct": kpis["attainment_pct"],
        "open_pipeline": kpis["open_pipeline"],
        "open_deal_count": open_deal_count,
        "win_rate": kpis["win_rate"],
        "deals_won": kpis["deals_won"],
        "deals_lost": kpis["deals_lost"],
        "warnings": kpis["warnings"],
        "data_available": has_revenue and has_deals,
        "confidence": _confidence_label(coverage),
        "generated_from": {
            "revenue": "source" if has_revenue else "fallback",
            "quota": "source" if has_quota else "fallback",
            "deals": "source" if has_deals else "fallback",
        },
        "fallback_used": not has_quota,
    }


@router.get("/revenue/monthly")
async def monthly_revenue(
    months: int = Query(18, le=36),
    period: str = Query(None, description="e.g. '2025-Q2', '2025', '2025-04'"),
    db: AsyncSession = Depends(get_db),
):
    q = select(Revenue.period, func.sum(Revenue.amount).label("total")).group_by(Revenue.period)

    if period:
        try:
            period_range = parse_period_to_range(period)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if period_range is not None:
            start_ym = period_range.start_date[:7]
            end_ym = period_range.end_date[:7]
            q = q.where(Revenue.period >= start_ym, Revenue.period <= end_ym)

    rows = (await db.execute(q.order_by(Revenue.period))).all()
    if not period:
        rows = rows[-months:]
    return [{"period": r.period, "revenue": float(r.total)} for r in rows]


@router.get("/pipeline/stages")
async def pipeline_stages(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(
                Deal.stage,
                func.count().label("count"),
                func.sum(Deal.amount).label("value"),
                func.avg(Deal.close_probability).label("avg_prob"),
            ).group_by(Deal.stage)
        )
    ).all()
    return [
        {
            "stage": r.stage,
            "count": r.count,
            "value": float(r.value or 0),
            "avg_probability": round(float(r.avg_prob or 0), 1),
        }
        for r in rows
    ]


@router.get("/reps/performance")
async def rep_performance(
    period: str = Query(None, description="e.g. '2025-Q2', '2025', '2025-04'"),
    db: AsyncSession = Depends(get_db),
):
    perf_filters = _period_to_filters(period) or {}

    selling_ids = await _selling_rep_ids(db)
    reps = (await db.execute(select(Rep))).scalars().all()

    # Batch-load position titles via UserProfile (matched by email)
    user_rows = (await db.execute(
        select(UserProfile.email, UserProfile.position_id)
    )).all()
    position_id_by_email = {(u.email or "").lower(): u.position_id for u in user_rows if u.position_id}

    positions_by_id: dict[str, Position] = {}
    if position_id_by_email:
        pos_rows = (await db.execute(select(Position))).scalars().all()
        positions_by_id = {str(p.id): p for p in pos_rows}

    results = []
    for rep in reps:
        pos_id = position_id_by_email.get((rep.email or "").lower())
        pos = positions_by_id.get(str(pos_id)) if pos_id else None
        is_quota_carrying = not selling_ids or str(rep.id) in selling_ids
        # Skip executive/leadership roles that don't carry a quota bag
        if not is_quota_carrying:
            continue
        perf = await calculators.get_rep_performance(db, rep_id=str(rep.id), filters=perf_filters or None)
        if perf["data"]:
            data = perf["data"]
            data["email"] = rep.email
            data["position"] = pos.name if pos else None
            data["position_level"] = pos.level if pos else None
            data["quota_carrying"] = is_quota_carrying
            results.append(data)

    return sorted(results, key=lambda x: -x["attainment_pct"])


@router.get("/reps/leadership")
async def leadership_rollup(
    period: str = Query(None, description="e.g. '2025-Q2', '2025-Q4', '2025-04'"),
    db: AsyncSession = Depends(get_db),
):
    """Return aggregated team metrics for all leadership / management positions.
    Each row represents a rollup of that person's direct and indirect reports.
    """
    perf_filters = _period_to_filters(period) or {}

    # Load users, positions, manager relationships, reps in one pass
    user_rows = (await db.execute(
        select(UserProfile.id, UserProfile.name, UserProfile.email, UserProfile.position_id)
    )).all()
    pos_rows = (await db.execute(select(Position))).scalars().all()
    pos_by_id = {str(p.id): p for p in pos_rows}
    mgr_rows = (await db.execute(select(Manager))).scalars().all()
    rep_rows = (await db.execute(select(Rep))).scalars().all()
    plan_rows = (await db.execute(select(PlanAssignment))).scalars().all()
    plan_meta_rows = (await db.execute(select(Plan))).scalars().all()

    # Lookup tables
    user_by_id: dict[str, Any] = {str(u.id): u for u in user_rows}
    rep_by_email: dict[str, Rep] = {(r.email or "").lower(): r for r in rep_rows}
    plan_id_by_user: dict[str, str] = {str(pa.user_id): str(pa.plan_id) for pa in plan_rows}
    plan_name_by_id: dict[str, str] = {str(p.id): p.name for p in plan_meta_rows}

    # Build manager_user_id → [direct report user_ids]
    direct_reports: dict[str, list[str]] = {}
    for m in mgr_rows:
        if m.manager_user_id:
            direct_reports.setdefault(str(m.manager_user_id), []).append(str(m.user_id))

    def all_subordinate_user_ids(uid: str, visited: set | None = None) -> list[str]:
        """Recursively collect every user_id under uid in the hierarchy."""
        if visited is None:
            visited = set()
        if uid in visited:
            return []
        visited.add(uid)
        result: list[str] = []
        for sub in direct_reports.get(uid, []):
            result.append(sub)
            result.extend(all_subordinate_user_ids(sub, visited))
        return result

    _LEADERSHIP_LEVELS = {"Executive", "Senior Leadership", "Leadership"}

    results: list[dict] = []
    for u in user_rows:
        pos = pos_by_id.get(str(u.position_id)) if u.position_id else None
        if not pos or pos.level not in _LEADERSHIP_LEVELS:
            continue

        sub_uids = all_subordinate_user_ids(str(u.id))
        # Collect rep_ids for all subordinates that have a reps entry
        sub_rep_ids: list[str] = []
        for sub_uid in sub_uids:
            sub_u = user_by_id.get(sub_uid)
            if sub_u:
                sub_rep = rep_by_email.get((sub_u.email or "").lower())
                if sub_rep:
                    sub_rep_ids.append(str(sub_rep.id))

        total_revenue = 0.0
        total_quota = 0.0
        total_won = 0
        total_lost = 0
        attainments: list[float] = []

        for rep_id in sub_rep_ids:
            perf = await calculators.get_rep_performance(db, rep_id=rep_id, filters=perf_filters or None)
            d = perf.get("data") or {}
            rev = float(d.get("revenue") or 0)
            quota = float(d.get("quota") or 0)
            total_revenue += rev
            total_quota += quota
            total_won += int(d.get("deals_won") or 0)
            total_lost += int(d.get("deals_lost") or 0)
            if quota > 0:
                attainments.append(rev / quota * 100)

        team_attainment = (total_revenue / total_quota * 100) if total_quota > 0 else 0.0
        win_rate = (total_won / (total_won + total_lost) * 100) if (total_won + total_lost) > 0 else 0.0
        avg_rep_attainment = sum(attainments) / len(attainments) if attainments else 0.0

        plan_id = plan_id_by_user.get(str(u.id))
        plan_name = plan_name_by_id.get(plan_id) if plan_id else None

        # Own rep row (leadership may also be in the reps table)
        own_rep = rep_by_email.get((u.email or "").lower())

        results.append({
            "user_id": str(u.id),
            "rep_id": str(own_rep.id) if own_rep else None,
            "name": u.name,
            "email": u.email,
            "position": pos.name,
            "position_level": pos.level,
            "position_rank": pos.rank,
            "plan_name": plan_name or "No plan assigned",
            "team_revenue": round(total_revenue, 2),
            "team_quota": round(total_quota, 2),
            "team_attainment_pct": round(team_attainment, 2),
            "avg_rep_attainment_pct": round(avg_rep_attainment, 2),
            "win_rate": round(win_rate, 2),
            "team_rep_count": len(sub_rep_ids),
            "team_won_deals": total_won,
            "team_lost_deals": total_lost,
            "is_rollup": True,
        })

    results.sort(key=lambda x: x["position_rank"])
    return results


@router.get("/deals/top")
async def top_deals(limit: int = Query(20, le=100), stage: str = None, db: AsyncSession = Depends(get_db)):
    q = (
        select(Deal, Rep.name.label("rep_name"), Account.name.label("company"), Account.industry)
        .join(Rep, isouter=True)
        .join(Account, isouter=True)
    )
    if stage:
        q = q.where(Deal.stage == stage)
    q = q.order_by(Deal.amount.desc()).limit(limit)
    rows = (await db.execute(q)).all()
    return [
        {
            "deal_id": str(r.Deal.id),
            "company": r.company,
            "rep": r.rep_name,
            "stage": r.Deal.stage,
            "amount": float(r.Deal.amount),
            "close_prob": r.Deal.close_probability,
            "industry": r.industry,
            "product": r.Deal.product,
            "expected_close": str(r.Deal.expected_close_date),
        }
        for r in rows
    ]


@router.get("/payouts")
async def payouts(period_prefix: str = Query(None, description="Optional period prefix like '2026' or '2026-Q2'"), db: AsyncSession = Depends(get_db)):
    reps = (await db.execute(select(Rep))).scalars().all()
    rows = []
    total_payout = 0.0
    total_revenue = 0.0
    total_quota = 0.0

    for rep in reps:
        revenue_q = select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id)
        quota_q = select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id)
        if period_prefix:
            revenue_q = revenue_q.where(Revenue.period.like(f"{period_prefix}%"))
            quota_q = quota_q.where(Quota.period.like(f"{period_prefix}%"))

        rep_revenue = float((await db.execute(revenue_q)).scalar() or 0.0)
        rep_quota = float((await db.execute(quota_q)).scalar() or 0.0)

        won_q = select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won")
        lost_q = select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost")
        deals_won = int((await db.execute(won_q)).scalar() or 0)
        deals_lost = int((await db.execute(lost_q)).scalar() or 0)

        payout_result = compute_payout(rep_revenue, rep_quota, deals_won, deals_lost)

        total_payout += payout_result["payout"]
        total_revenue += rep_revenue
        total_quota += rep_quota

        # Map confidence float → label
        conf_val = payout_result["confidence"]
        if conf_val >= 0.9:
            confidence_label = "high"
        elif conf_val >= 0.6:
            confidence_label = "medium"
        else:
            confidence_label = "low"

        rows.append(
            {
                "rep_id": str(rep.id),
                "name": rep.name,
                "email": rep.email,
                "region": rep.region,
                "revenue": round(rep_revenue, 2),
                "quota": round(rep_quota, 2),
                "attainment_pct": payout_result["attainment_pct"],
                "deals_won": deals_won,
                "deals_lost": deals_lost,
                "win_rate": payout_result["win_rate"],
                "commission_rate": payout_result["commission_rate"],
                "base_commission": payout_result["base_commission"],
                "accelerator": payout_result["accelerator"],
                "bonus": payout_result["bonus"],
                "payout": payout_result["payout"],
                "confidence": confidence_label,
                "fallback_used": payout_result["fallback_used"],
                "rules_applied": payout_result["rules_applied"],
            }
        )

    rows.sort(key=lambda r: r["payout"], reverse=True)
    fallback_count = sum(1 for r in rows if r.get("fallback_used"))
    confidence_values = [r.get("confidence") for r in rows]
    low_confidence_count = sum(1 for c in confidence_values if c == "low")
    coverage = 0.0
    if rows:
        coverage = 1.0 - (fallback_count / len(rows))

    return {
        "period_prefix": period_prefix,
        "summary": {
            "total_revenue": round(total_revenue, 2),
            "total_quota": round(total_quota, 2),
            "total_payout": round(total_payout, 2),
            "overall_attainment_pct": round((100.0 * total_revenue / total_quota), 2) if total_quota > 0 else 0.0,
            "rep_count": len(rows),
            "fallback_count": fallback_count,
            "low_confidence_count": low_confidence_count,
        },
        "rows": rows,
        "data_available": len(rows) > 0,
        "confidence": _confidence_label(coverage) if rows else "low",
        "generated_from": {
            "revenue": "source" if total_revenue > 0 else "fallback",
            "quota": "source" if total_quota > 0 else "fallback",
            "deals": "source",
        },
        "fallback_used": fallback_count > 0,
    }


@router.get("/org-structure")
async def org_structure(db: AsyncSession = Depends(get_db)):
    """Territory -> Team -> Member hierarchy with RevOps performance rollups."""
    teams = (await db.execute(select(Team))).scalars().all()
    reps = (await db.execute(select(Rep))).scalars().all()

    if not reps:
        return {
            "territories": [],
            "summary": {
                "territory_count": 0,
                "team_count": len(teams),
                "member_count": 0,
                "revenue_total": 0.0,
                "quota_total": 0.0,
                "attainment_pct": 0.0,
            },
            "data_available": False,
        }

    team_by_id = {str(t.id): t for t in teams}

    users = (await db.execute(select(UserProfile.id, UserProfile.email, UserProfile.team_id))).all()
    user_id_by_email = {
        (u.email or "").strip().lower(): str(u.id)
        for u in users
        if (u.email or "").strip()
    }
    user_team_by_id = {str(u.id): str(u.team_id) if u.team_id else "" for u in users}

    uta_rows = (await db.execute(select(UserTerritoryAssignment.user_id, UserTerritoryAssignment.territory_id, UserTerritoryAssignment.is_primary))).all()
    territory_rows = (await db.execute(select(Territory.id, Territory.name, Territory.region))).all()
    territory_name_by_id = {str(t.id): (t.name or t.region or "Unassigned") for t in territory_rows}

    territory_choices_by_user: dict[str, list[tuple[bool, str]]] = {}
    for row in uta_rows:
        uid = str(row.user_id)
        tid = str(row.territory_id)
        territory_choices_by_user.setdefault(uid, []).append((bool(row.is_primary), tid))

    rep_revenue_rows = (await db.execute(select(Revenue.rep_id, func.sum(Revenue.amount)).group_by(Revenue.rep_id))).all()
    rep_quota_rows = (await db.execute(select(Quota.rep_id, func.sum(Quota.amount)).group_by(Quota.rep_id))).all()
    won_rows = (await db.execute(select(Deal.rep_id, func.count()).where(Deal.stage == "Closed Won").group_by(Deal.rep_id))).all()
    lost_rows = (await db.execute(select(Deal.rep_id, func.count()).where(Deal.stage == "Closed Lost").group_by(Deal.rep_id))).all()

    revenue_by_rep = {str(r[0]): float(r[1] or 0.0) for r in rep_revenue_rows}
    quota_by_rep = {str(r[0]): float(r[1] or 0.0) for r in rep_quota_rows}
    won_by_rep = {str(r[0]): int(r[1] or 0) for r in won_rows}
    lost_by_rep = {str(r[0]): int(r[1] or 0) for r in lost_rows}

    territories: dict[str, dict] = {}

    for rep in reps:
        rep_id = str(rep.id)
        rep_email = (rep.email or "").strip().lower()
        user_id = user_id_by_email.get(rep_email, "")

        territory_name = ""
        if user_id and user_id in territory_choices_by_user:
            choices = sorted(territory_choices_by_user[user_id], key=lambda x: (not x[0]))
            chosen_tid = choices[0][1] if choices else ""
            territory_name = territory_name_by_id.get(chosen_tid, "")

        team_id = str(rep.team_id) if rep.team_id else ""
        if not team_id and user_id:
            team_id = user_team_by_id.get(user_id, "")

        team = team_by_id.get(team_id)
        team_name = team.name if team else "Unassigned Team"
        territory_name = territory_name or rep.region or (team.region if team else "") or "Unassigned"

        revenue = revenue_by_rep.get(rep_id, 0.0)
        quota = quota_by_rep.get(rep_id, 0.0)
        deals_won = won_by_rep.get(rep_id, 0)
        deals_lost = lost_by_rep.get(rep_id, 0)
        payout_result = compute_payout(revenue, quota, deals_won, deals_lost)

        territory_bucket = territories.setdefault(
            territory_name,
            {
                "territory": territory_name,
                "teams": {},
                "revenue": 0.0,
                "quota": 0.0,
                "payout": 0.0,
            },
        )
        team_bucket = territory_bucket["teams"].setdefault(
            team_name,
            {
                "team_id": team_id,
                "team_name": team_name,
                "region": team.region if team else rep.region,
                "members": [],
                "revenue": 0.0,
                "quota": 0.0,
                "payout": 0.0,
            },
        )

        member_payload = {
            "rep_id": rep_id,
            "name": rep.name,
            "email": rep.email,
            "region": rep.region,
            "revenue": round(revenue, 2),
            "quota": round(quota, 2),
            "attainment_pct": round(payout_result["attainment_pct"], 2),
            "win_rate": round(payout_result["win_rate"], 2),
            "deals_won": deals_won,
            "deals_lost": deals_lost,
            "payout": round(payout_result["payout"], 2),
            "confidence": _confidence_label(float(payout_result["confidence"])),
            "fallback_used": bool(payout_result["fallback_used"]),
        }
        team_bucket["members"].append(member_payload)
        team_bucket["revenue"] += revenue
        team_bucket["quota"] += quota
        team_bucket["payout"] += float(payout_result["payout"])

        territory_bucket["revenue"] += revenue
        territory_bucket["quota"] += quota
        territory_bucket["payout"] += float(payout_result["payout"])

    territory_list = []
    for territory_name, bucket in territories.items():
        team_list = []
        for _, t in bucket["teams"].items():
            t["members"] = sorted(t["members"], key=lambda m: m["revenue"], reverse=True)
            t["member_count"] = len(t["members"])
            t["attainment_pct"] = round((t["revenue"] / t["quota"] * 100.0), 2) if t["quota"] > 0 else 0.0
            t["revenue"] = round(t["revenue"], 2)
            t["quota"] = round(t["quota"], 2)
            t["payout"] = round(t["payout"], 2)
            team_list.append(t)
        team_list.sort(key=lambda team: team["revenue"], reverse=True)

        territory_list.append(
            {
                "territory": territory_name,
                "teams": team_list,
                "team_count": len(team_list),
                "member_count": sum(team["member_count"] for team in team_list),
                "revenue": round(bucket["revenue"], 2),
                "quota": round(bucket["quota"], 2),
                "payout": round(bucket["payout"], 2),
                "attainment_pct": round((bucket["revenue"] / bucket["quota"] * 100.0), 2) if bucket["quota"] > 0 else 0.0,
            }
        )

    territory_list.sort(key=lambda t: t["revenue"], reverse=True)
    revenue_total = sum(t["revenue"] for t in territory_list)
    quota_total = sum(t["quota"] for t in territory_list)

    return {
        "territories": territory_list,
        "summary": {
            "territory_count": len(territory_list),
            "team_count": sum(t["team_count"] for t in territory_list),
            "member_count": sum(t["member_count"] for t in territory_list),
            "revenue_total": round(revenue_total, 2),
            "quota_total": round(quota_total, 2),
            "attainment_pct": round((revenue_total / quota_total * 100.0), 2) if quota_total > 0 else 0.0,
        },
        "data_available": len(territory_list) > 0,
    }


@router.get("/plans-governance")
async def plans_governance(db: AsyncSession = Depends(get_db)):
    """Plan/rule governance and assignment coverage snapshot."""
    plans = (await db.execute(select(Plan))).scalars().all()
    rules = (await db.execute(select(Rule))).scalars().all()
    assignments = (await db.execute(select(PlanAssignment))).scalars().all()
    users = (await db.execute(select(UserProfile.id, UserProfile.name, UserProfile.email, UserProfile.team_id))).all()
    teams = (await db.execute(select(Team))).scalars().all()

    team_name_by_id = {str(t.id): t.name for t in teams}
    plan_by_id = {str(p.id): p for p in plans}

    rules_by_plan: dict[str, list[Rule]] = {}
    for rule in rules:
        rules_by_plan.setdefault(str(rule.plan_id), []).append(rule)

    assignments_by_plan: dict[str, list[PlanAssignment]] = {}
    assigned_user_ids: set[str] = set()
    for assignment in assignments:
        pid = str(assignment.plan_id)
        assignments_by_plan.setdefault(pid, []).append(assignment)
        assigned_user_ids.add(str(assignment.user_id))

    user_team_by_id = {str(u.id): str(u.team_id) if u.team_id else "" for u in users}
    user_name_by_id = {str(u.id): u.name for u in users}
    user_email_by_id = {str(u.id): u.email for u in users}

    plan_rows = []
    for plan in plans:
        pid = str(plan.id)
        plan_rules = sorted(rules_by_plan.get(pid, []), key=lambda r: (float(r.threshold_min or 0), float(r.threshold_max or 0)))
        plan_assignments = assignments_by_plan.get(pid, [])

        assigned_members = []
        for pa in plan_assignments:
            uid = str(pa.user_id)
            tid = user_team_by_id.get(uid, "")
            assigned_members.append(
                {
                    "user_id": uid,
                    "name": user_name_by_id.get(uid, "Unknown"),
                    "email": user_email_by_id.get(uid, ""),
                    "team_name": team_name_by_id.get(tid, "Unassigned Team"),
                }
            )

        plan_rows.append(
            {
                "plan_id": pid,
                "external_id": plan.external_id,
                "name": plan.name,
                "description": plan.description,
                "effective_start_date": str(plan.effective_start_date) if plan.effective_start_date else None,
                "effective_end_date": str(plan.effective_end_date) if plan.effective_end_date else None,
                "rule_count": len(plan_rules),
                "assigned_user_count": len(plan_assignments),
                "rules": [
                    {
                        "rule_id": str(rule.id),
                        "name": rule.name,
                        "metric_name": rule.metric_name,
                        "threshold_min": float(rule.threshold_min or 0),
                        "threshold_max": float(rule.threshold_max or 0),
                        "rate": float(rule.rate or 0),
                        "bonus_amount": float(rule.bonus_amount or 0),
                    }
                    for rule in plan_rules
                ],
                "assigned_members": assigned_members,
            }
        )

    plan_rows.sort(key=lambda p: p["assigned_user_count"], reverse=True)

    all_user_ids = {str(u.id) for u in users}
    unassigned_user_ids = sorted(all_user_ids - assigned_user_ids)

    assignment_coverage_pct = (len(assigned_user_ids) / len(all_user_ids) * 100.0) if all_user_ids else 0.0
    avg_rules_per_plan = (len(rules) / len(plans)) if plans else 0.0

    return {
        "summary": {
            "plan_count": len(plans),
            "rule_count": len(rules),
            "assignment_count": len(assignments),
            "assigned_user_count": len(assigned_user_ids),
            "unassigned_user_count": len(unassigned_user_ids),
            "assignment_coverage_pct": round(assignment_coverage_pct, 2),
            "avg_rules_per_plan": round(avg_rules_per_plan, 2),
        },
        "plans": plan_rows,
        "unassigned_users": [
            {
                "user_id": uid,
                "name": user_name_by_id.get(uid, "Unknown"),
                "email": user_email_by_id.get(uid, ""),
                "team_name": team_name_by_id.get(user_team_by_id.get(uid, ""), "Unassigned Team"),
            }
            for uid in unassigned_user_ids
        ],
        "data_available": len(plans) > 0 or len(rules) > 0 or len(assignments) > 0,
    }


@router.get("/reps/{rep_id}/profile")
async def rep_profile(rep_id: str, db: AsyncSession = Depends(get_db)):
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalar_one_or_none()
    if rep is None:
        raise HTTPException(status_code=404, detail="Rep not found")

    team = None
    if rep.team_id:
        team = (await db.execute(select(Team).where(Team.id == rep.team_id))).scalar_one_or_none()

    # Monthly revenue trend
    revenue_rows = (await db.execute(
        select(Revenue.period, func.sum(Revenue.amount).label("total"))
        .where(Revenue.rep_id == rep.id)
        .group_by(Revenue.period)
        .order_by(Revenue.period)
    )).all()
    monthly_trend = [{"period": r.period, "revenue": float(r.total)} for r in revenue_rows]

    # Plans: distinct products from deals with deal counts and total value
    product_rows = (await db.execute(
        select(Deal.product, func.count(Deal.id).label("deal_count"), func.sum(Deal.amount).label("value"))
        .where(Deal.rep_id == rep.id, Deal.product.isnot(None))
        .group_by(Deal.product)
        .order_by(func.sum(Deal.amount).desc())
    )).all()
    plans = [{"name": p.product, "deal_count": p.deal_count, "value": float(p.value or 0)} for p in product_rows]

    # Product-level performance (used for rep-product drilldown experiences).
    product_stage_rows = (
        await db.execute(
            select(
                Deal.product,
                Deal.stage,
                func.count(Deal.id).label("deal_count"),
                func.coalesce(func.sum(Deal.amount), 0.0).label("value"),
            )
            .where(Deal.rep_id == rep.id, Deal.product.isnot(None))
            .group_by(Deal.product, Deal.stage)
        )
    ).all()
    product_perf_map: dict[str, dict[str, Any]] = {}
    for row in product_stage_rows:
        product = row.product or "Unknown"
        bucket = product_perf_map.setdefault(
            product,
            {
                "product": product,
                "deals_total": 0,
                "deals_won": 0,
                "deals_lost": 0,
                "deals_open": 0,
                "revenue": 0.0,
                "open_pipeline": 0.0,
            },
        )
        count = int(row.deal_count or 0)
        value = float(row.value or 0.0)
        bucket["deals_total"] += count
        if row.stage == "Closed Won":
            bucket["deals_won"] += count
            bucket["revenue"] += value
        elif row.stage == "Closed Lost":
            bucket["deals_lost"] += count
        else:
            bucket["deals_open"] += count
            bucket["open_pipeline"] += value

    total_revenue = float(
        (
            await db.execute(
                select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id)
            )
        ).scalar()
        or 0
    )

    product_performance = []
    for item in product_perf_map.values():
        closed = item["deals_won"] + item["deals_lost"]
        item["win_rate"] = round((item["deals_won"] / closed * 100.0), 2) if closed > 0 else 0.0
        item["revenue"] = round(item["revenue"], 2)
        item["open_pipeline"] = round(item["open_pipeline"], 2)
        item["product_mix_pct"] = round((item["revenue"] / total_revenue * 100.0), 2) if total_revenue > 0 else 0.0
        product_performance.append(item)
    product_performance.sort(key=lambda x: x["revenue"], reverse=True)

    # Totals
    total_quota = float((await db.execute(
        select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id)
    )).scalar() or 0)
    attainment_pct = (100.0 * total_revenue / total_quota) if total_quota > 0 else 0.0

    deals_won = int((await db.execute(
        select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won")
    )).scalar() or 0)
    won_deal_value = float((await db.execute(
        select(func.sum(Deal.amount)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won")
    )).scalar() or 0.0)
    deals_lost = int((await db.execute(
        select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost")
    )).scalar() or 0)
    open_pipeline = float((await db.execute(
        select(func.sum(Deal.amount)).where(
            Deal.rep_id == rep.id,
            ~Deal.stage.in_(["Closed Won", "Closed Lost"])
        )
    )).scalar() or 0)
    win_rate = (100.0 * deals_won / max(1, deals_won + deals_lost))
    average_deal_size = (won_deal_value / deals_won) if deals_won > 0 else 0.0

    # Commission tier
    if attainment_pct >= 120:
        commission_tier = "Accelerated (10%)"
    elif attainment_pct >= 100:
        commission_tier = "On-Target (8%)"
    elif attainment_pct >= 80:
        commission_tier = "Ramping (5%)"
    else:
        commission_tier = "Below Threshold (3%)"

    # Rank by total revenue among all reps
    rev_subq = (
        select(Revenue.rep_id, func.sum(Revenue.amount).label("rev"))
        .group_by(Revenue.rep_id)
        .subquery()
    )
    higher_count = int((await db.execute(
        select(func.count()).select_from(rev_subq).where(rev_subq.c.rev > total_revenue)
    )).scalar() or 0)
    rank = higher_count + 1
    total_reps = int((await db.execute(select(func.count(Rep.id)))).scalar() or 0)

    # Position / role from UserProfile + Position table (matched by email)
    position_title: str | None = None
    position_level: str | None = None
    role_in_hierarchy: str | None = None
    manager_name: str | None = None
    reports_to_id: str | None = None

    user_profile = (await db.execute(
        select(UserProfile).where(UserProfile.email == rep.email)
    )).scalar_one_or_none()

    if user_profile:
        if user_profile.position_id:
            pos = (await db.execute(
                select(Position).where(Position.id == user_profile.position_id)
            )).scalar_one_or_none()
            if pos:
                position_title = pos.name
                position_level = pos.level

        # Manager chain via managers table
        mgr_row = (await db.execute(
            select(Manager).where(Manager.user_id == user_profile.id)
        )).scalar_one_or_none()
        if mgr_row and mgr_row.manager_user_id:
            mgr_user = (await db.execute(
                select(UserProfile).where(UserProfile.id == mgr_row.manager_user_id)
            )).scalar_one_or_none()
            if mgr_user:
                manager_name = mgr_user.name
                reports_to_id = str(mgr_row.manager_user_id)

    # Role from hierarchy is the position title itself
    role_in_hierarchy = position_title

    # Assigned products from rep_product_assignments
    assigned_products: list[dict] = []
    prod_rows = (await db.execute(
        select(RepProductAssignment, Product)
        .join(Product, RepProductAssignment.product_id == Product.id, isouter=True)
        .where(RepProductAssignment.rep_id == rep.id)
        .order_by(RepProductAssignment.is_primary.desc())
    )).all()
    for rpa, prod in prod_rows:
        assigned_products.append({
            "product_id": str(prod.id) if prod else None,
            "name": prod.name if prod else rpa.specialization,
            "sku": prod.product_sku if prod else None,
            "is_primary": rpa.is_primary,
            "specialization": rpa.specialization,
        })

    # Backfill product_performance for assigned products with no deals yet
    perf_names_lower = {p["product"].lower() for p in product_performance}
    for rpa, prod in prod_rows:
        prod_name = prod.name if prod else rpa.specialization
        if prod_name and prod_name.lower() not in perf_names_lower:
            product_performance.append({
                "product": prod_name,
                "deals_total": 0,
                "deals_won": 0,
                "deals_lost": 0,
                "deals_open": 0,
                "revenue": 0.0,
                "open_pipeline": 0.0,
                "win_rate": 0.0,
                "product_mix_pct": 0.0,
            })
            perf_names_lower.add(prod_name.lower())

    # Assigned plan/rule details for rep-plan and rule explainability popups.
    assigned_plans: list[dict[str, Any]] = []
    assigned_rules: list[dict[str, Any]] = []
    if user_profile:
        plan_assignments = (
            await db.execute(select(PlanAssignment).where(PlanAssignment.user_id == user_profile.id))
        ).scalars().all()
        plan_ids = [pa.plan_id for pa in plan_assignments]
        plan_rows = []
        if plan_ids:
            plan_rows = (
                await db.execute(select(Plan).where(Plan.id.in_(plan_ids)))
            ).scalars().all()
        plan_by_id = {str(p.id): p for p in plan_rows}

        for pa in plan_assignments:
            plan_obj = plan_by_id.get(str(pa.plan_id))
            assigned_plans.append(
                {
                    "plan_id": str(pa.plan_id),
                    "name": plan_obj.name if plan_obj else "Unknown Plan",
                    "scope": plan_obj.scope if plan_obj else None,
                    "effective_start_date": str(pa.effective_start_date) if pa.effective_start_date else None,
                    "effective_end_date": str(pa.effective_end_date) if pa.effective_end_date else None,
                }
            )

        if plan_ids:
            rule_rows = (
                await db.execute(select(Rule).where(Rule.plan_id.in_(plan_ids)).order_by(Rule.plan_id, Rule.threshold_min))
            ).scalars().all()
            for rule in rule_rows:
                plan_obj = plan_by_id.get(str(rule.plan_id))
                assigned_rules.append(
                    {
                        "rule_id": str(rule.id),
                        "plan_id": str(rule.plan_id),
                        "plan_name": plan_obj.name if plan_obj else "Unknown Plan",
                        "name": rule.name,
                        "metric_name": rule.metric_name,
                        "threshold_min": float(rule.threshold_min or 0.0) if rule.threshold_min is not None else None,
                        "threshold_max": float(rule.threshold_max or 0.0) if rule.threshold_max is not None else None,
                        "rate": float(rule.rate or 0.0) if rule.rate is not None else None,
                        "bonus_amount": float(rule.bonus_amount or 0.0) if rule.bonus_amount is not None else None,
                    }
                )

    return {
        "rep_id": str(rep.id),
        "name": rep.name,
        "email": rep.email,
        "region": rep.region,
        "hire_date": str(rep.hire_date) if rep.hire_date else None,
        "team_name": team.name if team else None,
        "position": position_title,
        "position_level": position_level,
        "role": role_in_hierarchy,
        "manager_name": manager_name,
        "monthly_trend": monthly_trend,
        "plans": plans,
        "product_performance": product_performance,
        "assigned_products": assigned_products,
        "assigned_plans": assigned_plans,
        "assigned_rules": assigned_rules,
        "performance": {
            "revenue": round(total_revenue, 2),
            "quota": round(total_quota, 2),
            "attainment_pct": round(attainment_pct, 2),
            "deals_won": deals_won,
            "deals_lost": deals_lost,
            "win_rate": round(win_rate, 2),
            "open_pipeline": round(open_pipeline, 2),
            "average_deal_size": round(average_deal_size, 2),
        },
        "commission_tier": commission_tier,
        "plan_name": assigned_plans[0]["name"] if assigned_plans else None,
        "ramp_factor": None,  # populated below
        "ramp_status": None,
        "rank": rank,
        "total_reps": total_reps,
    }

    # Add ramp data from RepRamp table if available
    try:
        from backend.models import RepRamp as _RepRamp
        ramp_row = (await db.execute(
            select(_RepRamp)
            .where(_RepRamp.rep_id == rep.id)
            .order_by(_RepRamp.period.desc())
            .limit(1)
        )).scalars().first()
        if ramp_row:
            rf = float(ramp_row.ramp_factor or 1.0)
            profile_resp["ramp_factor"] = round(rf, 4)
            profile_resp["ramp_status"] = "fully_ramped" if rf >= 1.0 else "ramping"
    except Exception:
        pass

    return profile_resp


@router.get("/revops-kpis")
async def get_revops_kpis(
    period: str = Query(None, description="e.g. '2025-04' or '2025-Q2'"),
    db: AsyncSession = Depends(get_db),
):
    """RevOps KPI panel: NRR, GRR, ARR growth, sales cycle, activity ratio, weighted pipeline coverage, attainment distribution."""
    filters = _period_to_filters(period)

    nrr = await calculators.get_nrr(db, filters)
    grr = await calculators.get_grr(db, filters)
    arr_growth = await calculators.get_arr_growth_rate(db, filters)
    cycle_days = await calculators.get_sales_cycle_days(db, filters)
    activity_ratio = await calculators.get_activity_ratio(db, filters)
    weighted_coverage = await calculators.get_weighted_pipeline_coverage(db, filters)
    attainment_dist = await calculators.get_quota_attainment_distribution(db, filters)

    all_warnings = (
        nrr["warnings"] + grr["warnings"] + arr_growth["warnings"] +
        cycle_days["warnings"] + activity_ratio["warnings"] +
        weighted_coverage["warnings"] + attainment_dist["warnings"]
    )

    return {
        "nrr_pct": nrr["nrr_pct"],
        "grr_pct": grr["grr_pct"],
        "grr_methodology": "GRR = (prior_12m_recognized_revenue - churn_revenue) / prior_12m_recognized_revenue × 100. Excludes new logo and expansion.",
        "nrr_methodology": "NRR = (prior_12m_recognized_revenue + expansion - contraction - churn) / prior_12m_recognized_revenue × 100.",
        "arr_growth_pct": arr_growth["arr_growth_pct"],
        "arr_current_12m": arr_growth.get("arr_current_12m", 0),
        "arr_prior_12m": arr_growth.get("arr_prior_12m", 0),
        "avg_sales_cycle_days": cycle_days["avg_days"],
        "activity_ratio": activity_ratio["ratio"],
        "open_deals": activity_ratio["open_deals"],
        "weighted_pipeline_coverage": weighted_coverage["ratio"],
        "weighted_pipeline_value": weighted_coverage["weighted_pipeline"],
        "pipeline_at_benchmark_3x": weighted_coverage.get("benchmark_3x", False),
        "attainment_distribution": attainment_dist["data"],
        "nrr_components": nrr.get("components", {}),
        "warnings": all_warnings,
    }


# ── ARR Waterfall ─────────────────────────────────────────────────────────

@router.get("/arr-waterfall")
async def arr_waterfall_series(
    months: int = Query(12, ge=1, le=36, description="Number of trailing months"),
    db: AsyncSession = Depends(get_db),
):
    """Return ARR waterfall for each of the last N months."""
    data = await calculators.calc_arr_waterfall_series(db, months=months)
    return {
        "months": months,
        "count": len(data),
        "data": data,
        "periods": [d["period"] for d in data],
        "new_logo": [d["new_logo"] for d in data],
        "expansion": [d["expansion"] for d in data],
        "contraction": [d["contraction"] for d in data],
        "churn": [d["churn"] for d in data],
        "renewal": [d["renewal"] for d in data],
        "net_new_arr": [d["net_new_arr"] for d in data],
        "arr_start": [d["arr_start"] for d in data],
        "arr_end": [d["arr_end"] for d in data],
    }


@router.get("/arr-waterfall/{period}")
async def arr_waterfall_period(
    period: str,
    db: AsyncSession = Depends(get_db),
):
    """Return ARR waterfall for a single period (YYYY-MM)."""
    if not period or len(period) != 7:
        raise HTTPException(status_code=400, detail="Period must be YYYY-MM format")
    return await calculators.calc_arr_waterfall(db, period)


# ── Deal Velocity ─────────────────────────────────────────────────────────

@router.get("/deal-velocity")
async def deal_velocity(
    period: str = Query(None, description="e.g. '2025-Q2' or '2025-04'"),
    rep_id: str = Query(None, description="Filter to single rep UUID"),
    db: AsyncSession = Depends(get_db),
):
    """Deal velocity metrics with optional period and rep filters."""
    import uuid as _uuid
    filters: dict = _period_to_filters(period) or {}
    if rep_id:
        try:
            filters["rep_id"] = _uuid.UUID(rep_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid rep_id UUID")
    return await calculators.calc_deal_velocity(db, filters=filters)


# ── Rep scorecard (activity + deal history) ───────────────────────────────

@router.get("/reps/{rep_id}/activities")
async def rep_activities(
    rep_id: str,
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Recent activities logged for a rep."""
    import uuid as _uuid
    try:
        rid = _uuid.UUID(rep_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rep_id UUID")

    rep = (await db.execute(select(Rep).where(Rep.id == rid))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    rows = (
        await db.execute(
            select(Activity)
            .where(Activity.rep_id == rid)
            .order_by(Activity.activity_date.desc())
            .limit(limit)
        )
    ).scalars().all()

    return {
        "rep_id": rep_id,
        "rep_name": rep.name,
        "count": len(rows),
        "activities": [
            {
                "id": str(a.id),
                "deal_id": str(a.deal_id),
                "type": a.type,
                "outcome": a.outcome,
                "notes": a.notes,
                "activity_date": a.activity_date.isoformat() if a.activity_date else None,
            }
            for a in rows
        ],
    }


@router.get("/reps/{rep_id}/deals")
async def rep_deals(
    rep_id: str,
    stage: str = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Deals owned by a rep, optionally filtered by stage."""
    import uuid as _uuid
    try:
        rid = _uuid.UUID(rep_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid rep_id UUID")

    rep = (await db.execute(select(Rep).where(Rep.id == rid))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    q = (
        select(Deal, Account.name.label("account_name"))
        .join(Account, isouter=True)
        .where(Deal.rep_id == rid)
    )
    if stage:
        q = q.where(Deal.stage == stage)
    q = q.order_by(Deal.created_at.desc()).limit(limit)
    rows = (await db.execute(q)).all()

    return {
        "rep_id": rep_id,
        "rep_name": rep.name,
        "count": len(rows),
        "deals": [
            {
                "deal_id": str(r.Deal.id),
                "name": r.Deal.name,
                "account": r.account_name,
                "stage": r.Deal.stage,
                "amount": float(r.Deal.amount or 0),
                "close_probability": r.Deal.close_probability,
                "expected_close_date": str(r.Deal.expected_close_date) if r.Deal.expected_close_date else None,
                "actual_close_date": str(r.Deal.actual_close_date) if r.Deal.actual_close_date else None,
                "product": r.Deal.product,
                "created_at": r.Deal.created_at.isoformat() if r.Deal.created_at else None,
            }
            for r in rows
        ],
    }


# ── Territory analytics ───────────────────────────────────────────────────

@router.get("/territories")
async def list_territories(db: AsyncSession = Depends(get_db)):
    """List all territories with rep counts and revenue rollup."""
    territories = (await db.execute(select(Territory))).scalars().all()
    result = []
    for t in territories:
        # Get rep count via UserTerritoryAssignment
        uta_count = (
            await db.execute(
                select(func.count(UserTerritoryAssignment.id))
                .where(UserTerritoryAssignment.territory_id == t.id)
            )
        ).scalar() or 0
        result.append({
            "territory_id": str(t.id),
            "name": t.name,
            "code": t.territory_code,
            "region": t.region,
            "segment": t.segment,
            "rep_count": uta_count,
        })
    return {"territories": result, "count": len(result)}


@router.get("/territories/{territory_id}")
async def territory_detail(
    territory_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Territory detail with assigned reps and revenue rollup."""
    import uuid as _uuid
    try:
        tid = _uuid.UUID(territory_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid territory_id UUID")

    t = (await db.execute(select(Territory).where(Territory.id == tid))).scalars().first()
    if not t:
        raise HTTPException(status_code=404, detail="Territory not found")

    uta_rows = (
        await db.execute(
            select(UserTerritoryAssignment, UserProfile.name.label("user_name"), UserProfile.email)
            .join(UserProfile)
            .where(UserTerritoryAssignment.territory_id == tid)
        )
    ).all()

    members = [
        {
            "user_id": str(r.UserTerritoryAssignment.user_id),
            "name": r.user_name,
            "email": r.email,
            "is_primary": r.UserTerritoryAssignment.is_primary,
        }
        for r in uta_rows
    ]

    return {
        "territory_id": territory_id,
        "name": t.name,
        "code": t.territory_code,
        "region": t.region,
        "segment": t.segment,
        "member_count": len(members),
        "members": members,
    }


# ── Cohort analysis ───────────────────────────────────────────────────────

@router.get("/cohort/deals")
async def deal_cohort_analysis(
    cohort_by: str = Query("close_month", description="Group by: close_month | product | region"),
    db: AsyncSession = Depends(get_db),
):
    """
    Cohort win-rate and revenue analysis.
    Groups closed deals by the chosen dimension.
    """
    VALID_COHORT_BY = {"close_month", "product", "region"}
    if cohort_by not in VALID_COHORT_BY:
        raise HTTPException(status_code=400, detail=f"cohort_by must be one of {VALID_COHORT_BY}")

    if cohort_by == "close_month":
        # Group by YYYY-MM of actual_close_date
        won_rows = (
            await db.execute(
                select(
                    func.to_char(Deal.actual_close_date, "YYYY-MM").label("cohort"),
                    func.count(Deal.id).label("count"),
                    func.sum(Deal.amount).label("revenue"),
                )
                .where(Deal.stage == "Closed Won")
                .where(Deal.actual_close_date.isnot(None))
                .group_by(func.to_char(Deal.actual_close_date, "YYYY-MM"))
                .order_by(func.to_char(Deal.actual_close_date, "YYYY-MM"))
            )
        ).all()
        lost_rows = (
            await db.execute(
                select(
                    func.to_char(Deal.actual_close_date, "YYYY-MM").label("cohort"),
                    func.count(Deal.id).label("count"),
                )
                .where(Deal.stage == "Closed Lost")
                .where(Deal.actual_close_date.isnot(None))
                .group_by(func.to_char(Deal.actual_close_date, "YYYY-MM"))
            )
        ).all()
        lost_by_cohort = {r.cohort: int(r.count) for r in lost_rows}
        cohort_data = [
            {
                "cohort": r.cohort,
                "deals_won": int(r.count),
                "deals_lost": lost_by_cohort.get(r.cohort, 0),
                "win_rate": round(100 * int(r.count) / max(int(r.count) + lost_by_cohort.get(r.cohort, 0), 1), 1),
                "revenue": float(r.revenue or 0),
                "avg_deal_size": round(float(r.revenue or 0) / max(int(r.count), 1), 2),
            }
            for r in won_rows
        ]

    elif cohort_by == "product":
        won_rows = (
            await db.execute(
                select(
                    Deal.product.label("cohort"),
                    func.count(Deal.id).label("count"),
                    func.sum(Deal.amount).label("revenue"),
                )
                .where(Deal.stage == "Closed Won")
                .group_by(Deal.product)
                .order_by(func.sum(Deal.amount).desc())
            )
        ).all()
        lost_rows = (
            await db.execute(
                select(Deal.product.label("cohort"), func.count(Deal.id).label("count"))
                .where(Deal.stage == "Closed Lost")
                .group_by(Deal.product)
            )
        ).all()
        lost_by_cohort = {r.cohort: int(r.count) for r in lost_rows}
        cohort_data = [
            {
                "cohort": r.cohort or "Unknown",
                "deals_won": int(r.count),
                "deals_lost": lost_by_cohort.get(r.cohort, 0),
                "win_rate": round(100 * int(r.count) / max(int(r.count) + lost_by_cohort.get(r.cohort, 0), 1), 1),
                "revenue": float(r.revenue or 0),
                "avg_deal_size": round(float(r.revenue or 0) / max(int(r.count), 1), 2),
            }
            for r in won_rows
        ]

    else:  # region
        won_rows = (
            await db.execute(
                select(
                    Rep.region.label("cohort"),
                    func.count(Deal.id).label("count"),
                    func.sum(Deal.amount).label("revenue"),
                )
                .join(Rep, isouter=True)
                .where(Deal.stage == "Closed Won")
                .group_by(Rep.region)
                .order_by(func.sum(Deal.amount).desc())
            )
        ).all()
        lost_rows = (
            await db.execute(
                select(Rep.region.label("cohort"), func.count(Deal.id).label("count"))
                .join(Rep, isouter=True)
                .where(Deal.stage == "Closed Lost")
                .group_by(Rep.region)
            )
        ).all()
        lost_by_cohort = {r.cohort: int(r.count) for r in lost_rows}
        cohort_data = [
            {
                "cohort": r.cohort or "Unknown",
                "deals_won": int(r.count),
                "deals_lost": lost_by_cohort.get(r.cohort, 0),
                "win_rate": round(100 * int(r.count) / max(int(r.count) + lost_by_cohort.get(r.cohort, 0), 1), 1),
                "revenue": float(r.revenue or 0),
                "avg_deal_size": round(float(r.revenue or 0) / max(int(r.count), 1), 2),
            }
            for r in won_rows
        ]

    return {
        "cohort_by": cohort_by,
        "cohorts": cohort_data,
        "count": len(cohort_data),
    }


# ── Win-loss analysis ─────────────────────────────────────────────────────

@router.get("/win-loss")
async def win_loss_analysis(
    period: str = Query(None, description="e.g. '2025-Q2'"),
    db: AsyncSession = Depends(get_db),
):
    """Breakdown of wins vs losses by stage, product, region, and rep."""
    filters_clauses: list[Any] = []
    if period:
        pf = _period_to_filters(period)
        if pf:
            from datetime import date as _date
            filters_clauses.append(Deal.actual_close_date >= _date.fromisoformat(pf["start_date"]))
            filters_clauses.append(Deal.actual_close_date <= _date.fromisoformat(pf["end_date"]))

    # By stage (last stage before close — simplified: just the stage of the deal)
    by_stage = (
        await db.execute(
            select(
                Deal.stage,
                func.count(Deal.id).label("count"),
                func.sum(Deal.amount).label("revenue"),
            )
            .where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .where(*filters_clauses if filters_clauses else [True])
            .group_by(Deal.stage)
        )
    ).all()

    # By product
    by_product = (
        await db.execute(
            select(
                Deal.product,
                Deal.stage,
                func.count(Deal.id).label("count"),
                func.sum(Deal.amount).label("revenue"),
            )
            .where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .where(*filters_clauses if filters_clauses else [True])
            .group_by(Deal.product, Deal.stage)
            .order_by(func.sum(Deal.amount).desc())
        )
    ).all()

    # By rep (top 10 by win count)
    by_rep = (
        await db.execute(
            select(
                Rep.name.label("rep_name"),
                Deal.stage,
                func.count(Deal.id).label("count"),
                func.sum(Deal.amount).label("revenue"),
            )
            .join(Rep, isouter=True)
            .where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .where(*filters_clauses if filters_clauses else [True])
            .group_by(Rep.name, Deal.stage)
            .order_by(func.sum(Deal.amount).desc())
            .limit(40)
        )
    ).all()

    def _pivot(rows, key, key_field="cohort"):
        """Pivot stage rows into won/lost per key."""
        d: dict[str, dict] = {}
        for r in rows:
            k = getattr(r, key, None) or "Unknown"
            if k not in d:
                d[k] = {"won_count": 0, "lost_count": 0, "won_revenue": 0.0, "lost_revenue": 0.0}
            if r.stage == "Closed Won":
                d[k]["won_count"] += int(r.count)
                d[k]["won_revenue"] += float(r.revenue or 0)
            else:
                d[k]["lost_count"] += int(r.count)
                d[k]["lost_revenue"] += float(r.revenue or 0)
        result = []
        for k, v in d.items():
            total = v["won_count"] + v["lost_count"]
            result.append({
                key_field: k,
                **v,
                "win_rate": round(100 * v["won_count"] / max(total, 1), 1),
            })
        return sorted(result, key=lambda x: -x["won_revenue"])

    won_total = sum(int(r.count) for r in by_stage if r.stage == "Closed Won")
    lost_total = sum(int(r.count) for r in by_stage if r.stage == "Closed Lost")

    return {
        "period": period,
        "summary": {
            "deals_won": won_total,
            "deals_lost": lost_total,
            "win_rate": round(100 * won_total / max(won_total + lost_total, 1), 1),
        },
        "by_product": _pivot(by_product, "product"),
        "by_rep": _pivot(by_rep, "rep_name", "rep_name"),
    }


@router.get("/manager-tree")
async def manager_tree(db: AsyncSession = Depends(get_db)):
    """Return manager hierarchy as a tree with rank/position data for each node."""
    users = (await db.execute(select(UserProfile))).scalars().all()
    managers = (await db.execute(select(Manager))).scalars().all()
    positions = (await db.execute(select(Position))).scalars().all()
    plans = (await db.execute(select(Plan))).scalars().all()

    pos_by_id = {str(p.id): p for p in positions}
    plan_count_by_owner: dict[str, int] = {}
    for pl in plans:
        if pl.owner_user_id:
            k = str(pl.owner_user_id)
            plan_count_by_owner[k] = plan_count_by_owner.get(k, 0) + 1

    # manager_user_id -> list of user_ids that report to them
    reports_map: dict[str, list[str]] = {}
    user_manager: dict[str, str | None] = {}
    for m in managers:
        uid = str(m.user_id)
        mid = str(m.manager_user_id) if m.manager_user_id else None
        user_manager[uid] = mid
        if mid:
            reports_map.setdefault(mid, []).append(uid)

    user_by_id = {str(u.id): u for u in users}

    def build_node(uid: str, depth: int = 0) -> dict:
        u = user_by_id[uid]
        pos = pos_by_id.get(str(u.position_id)) if u.position_id else None
        direct_report_ids = reports_map.get(uid, [])
        return {
            "id": uid,
            "name": u.name,
            "email": u.email,
            "rank": pos.rank if pos else 99,
            "rank_label": pos.rank_label if pos else None,
            "position_name": pos.name if pos else None,
            "plan_count": plan_count_by_owner.get(uid, 0),
            "cascade_rule_count": 0,  # populated below if needed
            "reports": [build_node(cid, depth + 1) for cid in direct_report_ids],
        }

    # Find roots: users with no manager or whose manager_user_id doesn't exist in users
    all_uids = set(user_by_id.keys())
    roots = [uid for uid in all_uids if user_manager.get(uid) is None or user_manager[uid] not in all_uids]
    # Sort roots by rank ascending (executives first)
    roots.sort(key=lambda uid: (
        pos_by_id[str(user_by_id[uid].position_id)].rank
        if user_by_id[uid].position_id and str(user_by_id[uid].position_id) in pos_by_id
        else 99
    ))

    return {"nodes": [build_node(uid) for uid in roots]}


@router.get("/positions")
async def list_positions(db: AsyncSession = Depends(get_db)):
    """Return all positions with rank data."""
    rows = (await db.execute(select(Position).order_by(Position.rank, Position.name))).scalars().all()
    return {
        "positions": [
            {
                "id": str(p.id),
                "name": p.name,
                "level": getattr(p, "level", None),
                "rank": p.rank,
                "rank_label": p.rank_label,
            }
            for p in rows
        ]
    }


@router.patch("/positions/{position_id}/rank")
async def update_position_rank(
    position_id: str,
    payload: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update the rank (and optional rank_label) on a position."""
    import uuid as _uuid
    try:
        pid = _uuid.UUID(position_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid position id") from exc
    pos = await db.get(Position, pid)
    if pos is None:
        raise HTTPException(status_code=404, detail="Position not found")
    new_rank = int(payload.get("rank", pos.rank))
    if new_rank not in range(1, 100):
        raise HTTPException(status_code=400, detail="rank must be 1–99")
    pos.rank = new_rank
    if "rank_label" in payload:
        pos.rank_label = str(payload["rank_label"]) if payload["rank_label"] else None
    await db.commit()
    await db.refresh(pos)
    return {"id": str(pos.id), "name": pos.name, "rank": pos.rank, "rank_label": pos.rank_label}


@router.get("/plan-cascade-rules")
async def list_plan_cascade_rules(db: AsyncSession = Depends(get_db)):
    """Return all plan cascade rules with plan and owner details."""
    rules = (
        await db.execute(
            select(PlanCascadeRule)
            .order_by(PlanCascadeRule.priority)
        )
    ).scalars().all()

    result = []
    for r in rules:
        plan = await db.get(Plan, r.plan_id)
        owner = await db.get(UserProfile, r.owner_user_id)
        owner_pos = None
        if owner and owner.position_id:
            owner_pos = await db.get(Position, owner.position_id)
        result.append({
            "id": str(r.id),
            "plan_id": str(r.plan_id),
            "plan_name": plan.name if plan else str(r.plan_id),
            "plan_scope": plan.scope if plan else "individual",
            "owner_user_id": str(r.owner_user_id),
            "owner_name": owner.name if owner else str(r.owner_user_id),
            "owner_rank": owner_pos.rank if owner_pos else 99,
            "owner_rank_label": owner_pos.rank_label if owner_pos else None,
            "cascade_scope": r.cascade_scope,
            "min_rank": r.min_rank,
            "max_rank": r.max_rank,
            "priority": r.priority,
            "effective_start_date": r.effective_start_date.isoformat() if r.effective_start_date else None,
            "effective_end_date": r.effective_end_date.isoformat() if r.effective_end_date else None,
        })
    return {"rules": result}



@router.get("/sales-performance")
async def get_sales_performance(
    period: str = Query(None, description="Period: YYYY-MM, YYYY-QN, YYYY, or 'this quarter'"),
    db: AsyncSession = Depends(get_db),
):
    """
    Unified sales performance endpoint.
    Returns consistent metrics used by Dashboard, Payouts, Reports, and Agent.
    All numbers sourced from the same canonical SalesPerformanceService.
    """
    svc = SalesPerformanceService(db)
    return await svc.get_full_summary(period=period)


@router.get("/drivers")
async def revenue_drivers(
    period: str = Query(None, description="Current period, e.g. 'this quarter', '2025-Q2'"),
    compare_period: str = Query(None, description="Previous period to compare against (defaults to period before)"),
    db: AsyncSession = Depends(get_db),
):
    """B6: Period-over-period revenue driver decomposition via sales_drivers.py."""
    from backend.statistics.sales_drivers import explain_metric_change
    from backend.metrics.service import get_metrics_service

    metrics_svc = get_metrics_service()

    def _parse(p: str | None) -> dict | None:
        if not p:
            return None
        try:
            pr = parse_period_to_range(p)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if pr is None:
            return None
        return {"start_date": pr.start_date, "end_date": pr.end_date}

    current_filters = _parse(period)
    prev_filters = _parse(compare_period)

    # Auto-derive previous period if not provided
    if current_filters and not prev_filters:
        from datetime import date, timedelta
        start = date.fromisoformat(current_filters["start_date"])
        end = date.fromisoformat(current_filters["end_date"])
        span_days = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span_days - 1)
        prev_filters = {"start_date": prev_start.isoformat(), "end_date": prev_end.isoformat()}

    current_kpis = await metrics_svc.get_kpis(db, filters=current_filters)
    previous_kpis = await metrics_svc.get_kpis(db, filters=prev_filters) if prev_filters else {}

    result = explain_metric_change(current_kpis, previous_kpis)
    return {
        "current_period": period or "all time",
        "compare_period": compare_period or ("prior period" if prev_filters else "none"),
        **result,
    }


@router.get("/anomalies")
async def detect_anomalies(
    period: str = Query(None, description="Period to analyze, e.g. '2025-Q2'"),
    db: AsyncSession = Depends(get_db),
):
    """B7: Rep-level anomaly detection using IsolationForest on revenue/quota/win-rate feature matrix."""
    import numpy as np
    from sklearn.ensemble import IsolationForest

    perf_filters = _period_to_filters(period) or {}

    selling_ids = await _selling_rep_ids(db)
    reps = (await db.execute(select(Rep))).scalars().all()
    features: list[list[float]] = []
    rep_ids: list[str] = []
    rep_names: list[str] = []

    for rep in reps:
        if selling_ids and str(rep.id) not in selling_ids:
            continue
        perf = await calculators.get_rep_performance(db, rep_id=str(rep.id), filters=perf_filters or None)
        d = perf.get("data")
        if not d:
            continue
        rev = float(d.get("revenue") or 0)
        quota = float(d.get("quota") or 0)
        attainment = float(d.get("attainment_pct") or 0)
        win_rate = float(d.get("win_rate") or 0)
        pipeline = float(d.get("open_pipeline") or 0)
        features.append([rev, quota, attainment, win_rate, pipeline])
        rep_ids.append(str(rep.id))
        rep_names.append(rep.name or "")

    if len(features) < 4:
        return {"anomalies": [], "total_reps": len(features), "warnings": ["Not enough data for anomaly detection (need ≥4 reps)"]}

    X = np.array(features, dtype=float)
    # Normalize each column to [0,1] range to prevent scale bias
    col_range = X.max(axis=0) - X.min(axis=0)
    col_range[col_range == 0] = 1.0
    X_norm = (X - X.min(axis=0)) / col_range

    clf = IsolationForest(n_estimators=100, contamination=0.15, random_state=42)
    scores = clf.fit_predict(X_norm)
    anomaly_scores = clf.score_samples(X_norm)

    anomalies = []
    for i, (rep_id, name) in enumerate(zip(rep_ids, rep_names)):
        if scores[i] == -1:
            f = features[i]
            anomalies.append({
                "rep_id": rep_id,
                "name": name,
                "anomaly_score": round(float(anomaly_scores[i]), 4),
                "revenue": round(f[0], 2),
                "quota": round(f[1], 2),
                "attainment_pct": round(f[2], 2),
                "win_rate": round(f[3], 4),
                "open_pipeline": round(f[4], 2),
            })

    anomalies.sort(key=lambda x: x["anomaly_score"])
    return {
        "period": period or "all time",
        "total_reps_analyzed": len(rep_ids),
        "anomaly_count": len(anomalies),
        "anomalies": anomalies,
    }
