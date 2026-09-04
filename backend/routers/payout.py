"""
backend/routers/payout.py
=========================
Payout API — tiered commission calculation, team summary, rep statements,
config inspection and override.

Endpoints
---------
POST /payout/calculate
GET  /payout/team-summary?period=
GET  /payout/statements/{rep_id}?periods=6
GET  /payout/config
PUT  /payout/config
GET  /payout/quota-suggestions/{rep_id}?period=&method=historical
GET  /payout/quota-ramp/{rep_id}?base_quota=
GET  /payout/quota-fairness?period=
"""
from __future__ import annotations

import re
import uuid
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.auth.dependencies import get_user_context, require_permission
from backend.auth.models import UserContext
from backend.auth.tenant import get_current_company_id, get_tenant_context
from backend.models import Rep, Revenue, Quota, Deal, Rule as RuleModel, PlanAssignment, UserProfile, PayoutRecord
from backend.payout.engine import (
    CommissionTier,
    DEFAULT_PAYOUT_CONFIG,
    PayoutConfig,
    PayoutEngine,
    SpiffRule,
    ClawbackRule,
    compute_payout,
    build_payout_config_from_rules,
)
from backend.payout.audit_trail_service import upsert_payout_trace

router = APIRouter(
    prefix="/payout",
    tags=["Payout"],
    dependencies=[Depends(require_permission("view_payouts")), Depends(get_tenant_context)],
)

# ── Module-level config singleton (session-scoped override) ──────────────
_active_config: PayoutConfig = deepcopy(DEFAULT_PAYOUT_CONFIG)


# ── Pydantic request/response helpers ────────────────────────────────────

class CommissionTierRequest(BaseModel):
    min_attainment_pct: float
    max_attainment_pct: float
    rate: float


class PayoutConfigRequest(BaseModel):
    tiers: list[CommissionTierRequest]
    accelerator_rate: float = 0.02
    team_bonus: float = 2000.0
    team_bonus_threshold_pct: float = 100.0
    team_bonus_min_win_rate_pct: float = 55.0
    team_bonus_min_deals: int = 3
    cap_multiplier: Optional[float] = None


class PayoutCalculateRequest(BaseModel):
    rep_id: uuid.UUID
    period: str  # "YYYY-MM" or "YYYY-Q1"
    config_override: Optional[PayoutConfigRequest] = None


# ── Period helpers ─────────────────────────────────────────────────────────

def _quarter_to_months(period: str) -> tuple[str, str]:
    """Convert 'YYYY-Q1' → ('YYYY-01', 'YYYY-03') etc."""
    m = re.match(r"^(\d{4})-Q([1-4])$", period)
    if not m:
        raise ValueError(f"Invalid period format: {period}. Use YYYY-MM or YYYY-Q1..Q4")
    year, q = int(m.group(1)), int(m.group(2))
    start_month = (q - 1) * 3 + 1
    end_month = start_month + 2
    return f"{year}-{start_month:02d}", f"{year}-{end_month:02d}"


def _period_to_range(period: str) -> tuple[str, str]:
    """Return (start_period, end_period) in YYYY-MM format."""
    if re.match(r"^\d{4}-\d{2}$", period):
        return period, period
    if re.match(r"^\d{4}-Q[1-4]$", period):
        return _quarter_to_months(period)
    if re.match(r"^\d{4}$", period):
        return f"{period}-01", f"{period}-12"
    raise ValueError(f"Unrecognised period format: '{period}'. Use YYYY-MM, YYYY-Q1, or YYYY.")


def _resolve_period_alias(period: Optional[str]) -> tuple[Optional[str], Optional[str], str]:
    """Resolve named period aliases used by the UI into canonical month ranges.

    Returns (start_period, end_period, normalized_label).
    """
    raw = (period or "").strip()
    if not raw:
        return None, None, "all-time"

    key = raw.lower()
    if key in {"all", "all-time", "all time", "all_time"}:
        return None, None, "all-time"
    if key in {"ytd", "year-to-date", "year to date"}:
        today = date.today()
        start = f"{today.year:04d}-01"
        end = f"{today.year:04d}-{today.month:02d}"
        return start, end, f"{today.year}-YTD"

    start, end = _period_to_range(raw)
    return start, end, raw


def _override_config(req: Optional[PayoutConfigRequest]) -> Optional[PayoutConfig]:
    if req is None:
        return None
    return PayoutConfig(
        tiers=[CommissionTier(t.min_attainment_pct, t.max_attainment_pct, t.rate) for t in req.tiers],
        accelerator_rate=req.accelerator_rate,
        team_bonus=req.team_bonus,
        team_bonus_threshold_pct=req.team_bonus_threshold_pct,
        team_bonus_min_win_rate_pct=req.team_bonus_min_win_rate_pct,
        team_bonus_min_deals=req.team_bonus_min_deals,
        cap_multiplier=req.cap_multiplier,
    )


# ── DB helpers ────────────────────────────────────────────────────────────

async def _load_plan_configs(db: AsyncSession) -> dict[uuid.UUID, PayoutConfig]:
    """Return plan_id → PayoutConfig built from DB Rule rows."""
    rule_rows = (await db.execute(select(RuleModel))).scalars().all()
    rules_by_plan: dict[uuid.UUID, list] = {}
    for rule in rule_rows:
        rules_by_plan.setdefault(rule.plan_id, []).append(rule)
    return {pid: build_payout_config_from_rules(rules) for pid, rules in rules_by_plan.items()}


async def _rep_revenue_for_period(
    db: AsyncSession,
    rep_id: uuid.UUID,
    start_period: str,
    end_period: str,
) -> float:
    val = (
        await db.execute(
            select(func.sum(Revenue.amount))
            .where(Revenue.rep_id == rep_id)
            .where(Revenue.period >= start_period)
            .where(Revenue.period <= end_period)
        )
    ).scalar()
    return float(val or 0)


async def _rep_revenue_all_time(db: AsyncSession, rep_id: uuid.UUID) -> float:
    val = (
        await db.execute(
            select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep_id)
        )
    ).scalar()
    return float(val or 0.0)


def _overlapping_quarters(start_period: str, end_period: str) -> list[tuple[str, float]]:
    """Return (YYYY-QN, fraction) for each quarter overlapping [start_period, end_period] (YYYY-MM).
    fraction = covered_months / 3  →  full quarter = 1.0, single month = 0.333…
    """
    sy, sm = int(start_period[:4]), int(start_period[5:7])
    ey, em = int(end_period[:4]), int(end_period[5:7])
    covered: dict[str, int] = {}
    y, m = sy, sm
    while (y, m) <= (ey, em):
        label = f"{y}-Q{(m - 1) // 3 + 1}"
        covered[label] = covered.get(label, 0) + 1
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return [(label, count / 3.0) for label, count in covered.items()]


def _months_in_range(start_period: str, end_period: str) -> list[str]:
    """Expand an inclusive YYYY-MM range into month keys."""
    sy, sm = int(start_period[:4]), int(start_period[5:7])
    ey, em = int(end_period[:4]), int(end_period[5:7])
    months: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return months


async def _rep_quota_for_period(
    db: AsyncSession,
    rep_id: uuid.UUID,
    start_period: str,
    end_period: str,
) -> float:
    """Resolve quota for a monthly range across monthly, quarterly, and annual quota grains.

    Preference order:
    1) Direct monthly rows (YYYY-MM) for the covered months
    2) Quarterly rows (YYYY-QN), proportionally allocated for partial quarter overlap
    3) Annual rows (YYYY), proportionally allocated by covered months in each year
    """
    month_keys = _months_in_range(start_period, end_period)
    if not month_keys:
        return 0.0

    # 1) Monthly quota rows take precedence when present.
    monthly_total = (
        await db.execute(
            select(func.sum(Quota.amount))
            .where(Quota.rep_id == rep_id)
            .where(Quota.period.in_(month_keys))
        )
    ).scalar()
    monthly_total_val = float(monthly_total or 0.0)
    if monthly_total_val > 0:
        return round(monthly_total_val, 2)

    # 2) Quarterly fallback with partial-quarter allocation support.
    quarters = _overlapping_quarters(start_period, end_period)
    full = [lbl for lbl, frac in quarters if frac >= 1.0]
    partial = [(lbl, frac) for lbl, frac in quarters if frac < 1.0]
    quarter_total = 0.0
    if full:
        full_val = (
            await db.execute(
                select(func.sum(Quota.amount))
                .where(Quota.rep_id == rep_id)
                .where(Quota.period.in_(full))
            )
        ).scalar()
        quarter_total += float(full_val or 0.0)
    for label, frac in partial:
        partial_val = (
            await db.execute(
                select(func.sum(Quota.amount))
                .where(Quota.rep_id == rep_id)
                .where(Quota.period == label)
            )
        ).scalar()
        quarter_total += float(partial_val or 0.0) * float(frac)
    if quarter_total > 0:
        return round(quarter_total, 2)

    # 3) Annual fallback: allocate quota by months covered in each year.
    months_by_year: dict[str, int] = {}
    for month in month_keys:
        year = month[:4]
        months_by_year[year] = months_by_year.get(year, 0) + 1

    annual_total = 0.0
    for year, covered_months in months_by_year.items():
        annual_val = (
            await db.execute(
                select(func.sum(Quota.amount))
                .where(Quota.rep_id == rep_id)
                .where(Quota.period == year)
            )
        ).scalar()
        annual_total += float(annual_val or 0.0) * (covered_months / 12.0)

    return round(annual_total, 2)


async def _rep_quota_all_time(db: AsyncSession, rep_id: uuid.UUID) -> float:
    val = (
        await db.execute(
            select(func.sum(Quota.amount)).where(Quota.rep_id == rep_id)
        )
    ).scalar()
    return float(val or 0.0)


async def _rep_deal_counts(
    db: AsyncSession,
    rep_id: uuid.UUID,
    start_period: str,
    end_period: str,
) -> tuple[int, int]:
    """Return (deals_won, deals_lost) where actual_close_date falls in period range."""
    start_date = date.fromisoformat(start_period + "-01")
    # End of last month in range
    end_year, end_mon = int(end_period[:4]), int(end_period[5:7])
    if end_mon == 12:
        end_date = date(end_year + 1, 1, 1)
    else:
        end_date = date(end_year, end_mon + 1, 1)

    won = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep_id)
            .where(Deal.stage == "Closed Won")
            .where(Deal.actual_close_date >= start_date)
            .where(Deal.actual_close_date < end_date)
        )
    ).scalar() or 0

    lost = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep_id)
            .where(Deal.stage == "Closed Lost")
            .where(Deal.actual_close_date >= start_date)
            .where(Deal.actual_close_date < end_date)
        )
    ).scalar() or 0

    return int(won), int(lost)


async def _rep_deal_counts_all_time(db: AsyncSession, rep_id: uuid.UUID) -> tuple[int, int]:
    won = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep_id)
            .where(Deal.stage == "Closed Won")
        )
    ).scalar() or 0

    lost = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep_id)
            .where(Deal.stage == "Closed Lost")
        )
    ).scalar() or 0

    return int(won), int(lost)


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.post("/calculate")
async def calculate_payout(
    req: PayoutCalculateRequest,
    db: AsyncSession = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    """Calculate payout for a single rep for a given period."""
    start_p, end_p = _period_to_range(req.period)

    rep = (await db.execute(select(Rep).where(Rep.id == req.rep_id))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail=f"Rep {req.rep_id} not found")

    revenue = await _rep_revenue_for_period(db, req.rep_id, start_p, end_p)
    quota = await _rep_quota_for_period(db, req.rep_id, start_p, end_p)
    deals_won, deals_lost = await _rep_deal_counts(db, req.rep_id, start_p, end_p)

    cfg = _override_config(req.config_override) or _active_config
    result = compute_payout(revenue, quota, deals_won, deals_lost, cfg)

    user_row = (
        await db.execute(select(UserProfile).where(UserProfile.email == rep.email))
    ).scalars().first()
    uid = user_row.id if user_row else None

    # A real, persisted payout already exists for this rep+period -- prefer
    # it over this endpoint's own compute_payout() estimate, the same
    # precedent _quarterly_payout_baseline() established for
    # /payout/statements (and now /payout/team-summary too). Skipped for an
    # explicit config_override request, since that's deliberately asking
    # "what would this look like under a different config" -- a real
    # committed record answering a different question shouldn't silently
    # override that.
    existing_payout_id: Optional[str] = None
    if uid is not None and not req.config_override:
        real_record = (
            await db.execute(
                select(PayoutRecord)
                .where(PayoutRecord.user_id == uid)
                .where(PayoutRecord.period == req.period)
            )
        ).scalars().first()
        if real_record is not None:
            existing_payout_id = str(real_record.id)
            real_total = float(real_record.payout_amount or 0.0)
            result = {
                **result,
                "payout": real_total,
                "commission_rate": float(real_record.commission_rate or 0.0),
                "fallback_used": bool(real_record.fallback_used),
                "bonus": round(real_total - result.get("base_commission", 0.0) - result.get("accelerator", 0.0), 2),
            }

    audit = upsert_payout_trace(
        company_id=company_id,
        period=req.period,
        rep_id=str(req.rep_id),
        user_id=str(uid) if uid else None,
        plan_id=None,
        rule_id=result.get("rules_applied", [None])[0],
        sales_credit_id=None,
        credited_amount=revenue,
        quota=quota,
        attainment_pct=result.get("attainment_pct", 0.0),
        base_commission=result.get("base_commission", 0.0),
        accelerator_amount=result.get("accelerator", 0.0),
        spiff_amount=result.get("spiff_total", 0.0),
        clawback_amount=result.get("clawback_total", 0.0),
        final_payout=result.get("payout", 0.0),
        calculation_trace_json={
            "mode": "single_rep_calculation",
            "config_override": bool(req.config_override),
            "rules_applied": result.get("rules_applied", []),
            "commission_rate": result.get("commission_rate"),
            "confidence": result.get("confidence"),
            "fallback_used": result.get("fallback_used"),
        },
        source_records_json={
            "period_start": start_p,
            "period_end": end_p,
            "deals_won": deals_won,
            "deals_lost": deals_lost,
            "rep_name": rep.name,
        },
        computed_by=ctx.user_id or "system-demo",
        existing_payout_id=existing_payout_id,
    )

    return {
        "payout_id": audit["payout_id"],
        "company_id": company_id,
        "rep_id": str(req.rep_id),
        "rep_name": rep.name,
        "period": req.period,
        "period_start": start_p,
        "period_end": end_p,
        "revenue": revenue,
        "quota": quota,
        "deals_won": deals_won,
        "deals_lost": deals_lost,
        **result,
    }


@router.get("/team-summary")
async def team_payout_summary(
    period: Optional[str] = Query(None, description="e.g. '2025-Q1', '2025-04', 'ytd', or 'all-time'"),
    db: AsyncSession = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    ctx: UserContext = Depends(get_user_context),
) -> dict[str, Any]:
    """Compute payout for every rep in the DB for the given period."""
    start_p, end_p, period_label = _resolve_period_alias(period)

    # Only include quota-carrying sellers (exclude Executive/Leadership)
    from backend.routers.analytics import _selling_rep_ids
    selling_ids = await _selling_rep_ids(db)
    all_reps = (await db.execute(select(Rep))).scalars().all()
    reps = [r for r in all_reps if not selling_ids or str(r.id) in selling_ids]

    # Load plan configs once for all reps
    plan_configs = await _load_plan_configs(db)

    # Build user_id → plan_id map once
    pa_rows = (await db.execute(select(PlanAssignment))).scalars().all()
    user_to_plan: dict[uuid.UUID, uuid.UUID] = {}
    for pa in pa_rows:
        if pa.user_id not in user_to_plan:
            user_to_plan[pa.user_id] = pa.plan_id

    # Build rep email → user_id map
    user_rows_all = (await db.execute(select(UserProfile.email, UserProfile.id))).all()
    email_to_user_id = {(u.email or "").lower(): u.id for u in user_rows_all}

    rows: list[dict[str, Any]] = []
    for rep in reps:
        if start_p and end_p:
            revenue = await _rep_revenue_for_period(db, rep.id, start_p, end_p)
            quota = await _rep_quota_for_period(db, rep.id, start_p, end_p)
            deals_won, deals_lost = await _rep_deal_counts(db, rep.id, start_p, end_p)
        else:
            revenue = await _rep_revenue_all_time(db, rep.id)
            quota = await _rep_quota_all_time(db, rep.id)
            deals_won, deals_lost = await _rep_deal_counts_all_time(db, rep.id)
        uid = email_to_user_id.get((rep.email or "").lower())
        plan_id = user_to_plan.get(uid) if uid else None
        cfg = plan_configs.get(plan_id) if plan_id else None
        cfg = cfg or _active_config
        result = compute_payout(revenue, quota, deals_won, deals_lost, cfg)

        # A real, persisted payout already exists for this rep+period -- it
        # is the source of truth (computed by credit_payout_engine.py), the
        # same precedent _quarterly_payout_baseline() established for
        # /payout/statements. Prefer it over this endpoint's own
        # compute_payout() estimate, and register the audit-trail entry
        # under the PayoutRecord's own id so this doesn't create a second,
        # independently-approvable entry for a payout that already has one
        # (see seed_from_db_record in backend/routers/payout_audit.py).
        existing_payout_id: Optional[str] = None
        if uid is not None:
            real_record = (
                await db.execute(
                    select(PayoutRecord)
                    .where(PayoutRecord.user_id == uid)
                    .where(PayoutRecord.period == period_label)
                )
            ).scalars().first()
            if real_record is not None:
                existing_payout_id = str(real_record.id)
                real_total = float(real_record.payout_amount or 0.0)
                result = {
                    **result,
                    "payout": real_total,
                    "commission_rate": float(real_record.commission_rate or 0.0),
                    "fallback_used": bool(real_record.fallback_used),
                    # bonus absorbs whatever base_commission/accelerator (both
                    # still reasonable independent estimates) don't already
                    # account for, so the breakdown sums exactly to the real
                    # total -- same reconciliation rep_payout_statements uses.
                    "bonus": round(real_total - result.get("base_commission", 0.0) - result.get("accelerator", 0.0), 2),
                }

        audit = upsert_payout_trace(
            company_id=company_id,
            period=period_label,
            rep_id=str(rep.id),
            user_id=str(uid) if uid else None,
            plan_id=str(plan_id) if plan_id else None,
            rule_id=result.get("rules_applied", [None])[0],
            sales_credit_id=None,
            credited_amount=revenue,
            quota=quota,
            attainment_pct=result.get("attainment_pct", 0.0),
            base_commission=result.get("base_commission", 0.0),
            accelerator_amount=result.get("accelerator", 0.0),
            spiff_amount=result.get("spiff_total", 0.0),
            clawback_amount=result.get("clawback_total", 0.0),
            final_payout=result.get("payout", 0.0),
            calculation_trace_json={
                "mode": "team_summary",
                "period": period_label,
                "rules_applied": result.get("rules_applied", []),
                "confidence": result.get("confidence"),
                "fallback_used": result.get("fallback_used"),
            },
            source_records_json={
                "deals_won": deals_won,
                "deals_lost": deals_lost,
                "rep_name": rep.name,
                "rep_email": rep.email,
            },
            computed_by=ctx.user_id or "system-demo",
            existing_payout_id=existing_payout_id,
        )
        rows.append({
            "payout_id": audit["payout_id"],
            "rep_id": str(rep.id),
            "rep_name": rep.name,
            "region": rep.region,
            "revenue": revenue,
            "quota": quota,
            "attainment_pct": result["attainment_pct"],
            "commission": result["base_commission"],
            "accelerator": result["accelerator"],
            "bonus": result["bonus"],
            "total_payout": result["payout"],
            "commission_rate": result["commission_rate"],
            "confidence": "high" if not result["fallback_used"] else "medium",
            "fallback_used": result["fallback_used"],
            "rules_applied": result["rules_applied"],
            "lifecycle_state": audit.get("lifecycle_state", "draft"),
        })

    rows.sort(key=lambda r: r["total_payout"], reverse=True)
    total_payout = sum(r["total_payout"] for r in rows)
    avg_attainment = (sum(r["attainment_pct"] for r in rows) / len(rows)) if rows else 0.0
    reps_at_quota = sum(1 for r in rows if r["attainment_pct"] >= 100)
    reps_below_80 = sum(1 for r in rows if r["attainment_pct"] < 80)

    return {
        "period": period_label,
        "summary": {
            "total_payout": round(total_payout, 2),
            "avg_attainment_pct": round(avg_attainment, 2),
            "reps_at_quota": reps_at_quota,
            "reps_below_80": reps_below_80,
            "rep_count": len(rows),
        },
        "rows": rows,
    }


async def _quarterly_payout_baseline(
    db: AsyncSession,
    rep_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
    quarter_label: str,
    rep_cfg: PayoutConfig,
) -> dict[str, Any]:
    """The correct, quarterly-grain payout baseline for one quarter.

    Commission tiers and the flat per-tier bonus are defined at quarterly
    grain (Quota rows are quarterly; Rule.bonus_amount is a once-a-quarter
    figure) — they only mean anything evaluated cumulatively across the
    whole quarter, never against one month's revenue in isolation. Prefers
    the real PayoutRecord for the quarter (the actual source of truth,
    computed by credit_payout_engine.py) when one exists; falls back to
    compute_payout() run once against quarterly-aggregated revenue/quota/
    deals (e.g. for the current in-flight quarter, before a real payout run
    has happened for it) — never against a single month's numbers, which is
    the bug this replaced.
    """
    q_start, q_end = _quarter_to_months(quarter_label)
    quarter_revenue = await _rep_revenue_for_period(db, rep_id, q_start, q_end)
    quarter_quota = await _rep_quota_for_period(db, rep_id, q_start, q_end)

    real_record = None
    if user_id is not None:
        real_record = (
            await db.execute(
                select(PayoutRecord)
                .where(PayoutRecord.user_id == user_id)
                .where(PayoutRecord.period == quarter_label)
            )
        ).scalars().first()

    if real_record is not None:
        commission_rate = float(real_record.commission_rate or 0.0)
        payout_amount = float(real_record.payout_amount or 0.0)
        fallback_used = bool(real_record.fallback_used)
    else:
        deals_won, deals_lost = await _rep_deal_counts(db, rep_id, q_start, q_end)
        result = compute_payout(quarter_revenue, quarter_quota, deals_won, deals_lost, rep_cfg)
        commission_rate = result["commission_rate"]
        payout_amount = result["payout"]
        fallback_used = result["fallback_used"]

    tier_label = "Below threshold"
    for tier in rep_cfg.tiers:
        if abs(tier.rate - commission_rate) < 1e-9:
            tier_label = f"{tier.min_attainment_pct:.0f}–{tier.max_attainment_pct:.0f}% @ {tier.rate:.0%} (quarterly)"
            break

    return {
        "quarter_revenue": quarter_revenue,
        "quarter_quota": quarter_quota,
        "commission_rate": commission_rate,
        "payout_amount": payout_amount,
        "tier_label": tier_label,
        "fallback_used": fallback_used,
        "accelerator_rate": rep_cfg.accelerator_rate,
    }


@router.get("/statements/{rep_id}")
async def rep_payout_statements(
    rep_id: uuid.UUID,
    periods: int = Query(6, ge=1, le=24, description="Number of recent months"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return last N monthly payout periods for a rep.

    Each month's commission/accelerator/bonus is this month's share (by
    revenue) of its quarter's real payout — not independently re-evaluated
    from this one month's isolated attainment. See
    _quarterly_payout_baseline() for why: the plan's tiers and flat bonus
    are quarterly-grain rules, and evaluating them against a single month
    let a rep's one strong month trigger a bonus their quarter as a whole
    never earned (confirmed live: a rep at 98.4% cumulative quarterly
    attainment — no bonus in the real payout record — showed a full bonus
    in this endpoint for the one month of that quarter where isolated
    monthly attainment happened to clear 100%).
    """
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail=f"Rep {rep_id} not found")

    # Load plan config for this rep once (outside the per-period loop)
    plan_configs = await _load_plan_configs(db)
    user_row = (await db.execute(select(UserProfile).where(UserProfile.email == rep.email))).scalars().first()
    user_id_for_rep = user_row.id if user_row else None
    pa_row = (
        (await db.execute(select(PlanAssignment).where(PlanAssignment.user_id == user_id_for_rep))).scalars().first()
        if user_id_for_rep else None
    )
    user_plan_id = pa_row.plan_id if pa_row else None
    rep_cfg = plan_configs.get(user_plan_id) if user_plan_id else None
    rep_cfg = rep_cfg or _active_config

    # Get all distinct periods this rep has revenue in, last N
    period_rows = (
        await db.execute(
            select(Revenue.period)
            .where(Revenue.rep_id == rep_id)
            .group_by(Revenue.period)
            .order_by(Revenue.period.desc())
            .limit(periods)
        )
    ).scalars().all()

    statements: list[dict[str, Any]] = []
    quarter_cache: dict[str, dict[str, Any]] = {}
    for p in sorted(period_rows):  # ascending chronological
        revenue = await _rep_revenue_for_period(db, rep_id, p, p)
        quota = await _rep_quota_for_period(db, rep_id, p, p)

        quarter_label = _overlapping_quarters(p, p)[0][0]
        if quarter_label not in quarter_cache:
            quarter_cache[quarter_label] = await _quarterly_payout_baseline(
                db, rep_id, user_id_for_rep, quarter_label, rep_cfg
            )
        baseline = quarter_cache[quarter_label]

        weight = (revenue / baseline["quarter_revenue"]) if baseline["quarter_revenue"] > 0 else (1.0 / 3.0)
        commission_rate = baseline["commission_rate"]
        base_commission = round(revenue * commission_rate, 2)
        accelerator_est = max(0.0, baseline["quarter_revenue"] - baseline["quarter_quota"]) * baseline["accelerator_rate"]
        accelerator = round(accelerator_est * weight, 2)
        payout_amount = round(baseline["payout_amount"] * weight, 2)
        # bonus is the remainder that makes commission + accelerator + bonus
        # equal this month's share of the quarter's real payout exactly, to
        # the cent — the real PayoutRecord doesn't break its total down into
        # these components, so bonus absorbs whatever commission/accelerator
        # (both independently, reasonably estimated) don't already account for.
        bonus = round(payout_amount - base_commission - accelerator, 2)

        month_attainment = round((100.0 * revenue / quota), 2) if quota > 0 else 0.0

        statements.append({
            "period": p,
            "revenue": revenue,
            "quota": quota,
            "attainment_pct": month_attainment,
            "tier_applied": baseline["tier_label"],
            "commission": base_commission,
            "base_commission": base_commission,
            "commission_rate": commission_rate,
            "accelerator": accelerator,
            "bonus": bonus,
            "payout": payout_amount,
            "total_payout": payout_amount,
            "confidence": "high" if not baseline["fallback_used"] else "medium",
            "fallback_used": baseline["fallback_used"],
            "quarter": quarter_label,
        })

    return {
        "rep_id": str(rep_id),
        "rep_name": rep.name,
        "periods_returned": len(statements),
        "statements": statements,
    }


@router.get("/config")
async def get_payout_config() -> dict[str, Any]:
    """Return the current active payout config (in-session singleton)."""
    return {
        "tiers": [
            {
                "min_attainment_pct": t.min_attainment_pct,
                "max_attainment_pct": t.max_attainment_pct,
                "rate": t.rate,
                "rate_pct": round(t.rate * 100, 1),
            }
            for t in _active_config.tiers
        ],
        "accelerator_rate": _active_config.accelerator_rate,
        "team_bonus": _active_config.team_bonus,
        "team_bonus_threshold_pct": _active_config.team_bonus_threshold_pct,
        "team_bonus_min_win_rate_pct": _active_config.team_bonus_min_win_rate_pct,
        "team_bonus_min_deals": _active_config.team_bonus_min_deals,
        "cap_multiplier": _active_config.cap_multiplier,
        "is_default": _active_config == DEFAULT_PAYOUT_CONFIG,
    }


@router.put("/config")
async def update_payout_config(
    req: PayoutConfigRequest,
    _: UserContext = Depends(require_permission("manage_rules")),
) -> dict[str, Any]:
    """Override the active payout config for this server session."""
    global _active_config
    if not req.tiers:
        raise HTTPException(status_code=422, detail="At least one commission tier is required")
    _active_config = _override_config(req)  # type: ignore[assignment]
    return {"message": "Payout config updated", "tier_count": len(req.tiers)}


# ── Quota tools (Sprint 3.7) ──────────────────────────────────────────────

@router.get("/quota-suggestions/{rep_id}")
async def quota_suggestions(
    rep_id: uuid.UUID,
    period: str = Query(None, description="Target period e.g. 2026-Q2"),
    method: str = Query("historical", description="historical | top_down"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Suggest a data-driven quota for a rep."""
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    # Pull last 4 quarters of monthly revenue (12 months)
    revenue_rows = (
        await db.execute(
            select(Revenue.period, Revenue.amount)
            .where(Revenue.rep_id == rep_id)
            .order_by(Revenue.period.desc())
            .limit(12)
        )
    ).all()

    if not revenue_rows:
        return {
            "rep_id": str(rep_id),
            "rep_name": rep.name,
            "suggested_quota": None,
            "method": method,
            "confidence": "low",
            "basis": "No historical revenue available",
            "ramp_adjusted": False,
        }

    total_rev = sum(float(r.amount or 0) for r in revenue_rows)
    months = len(revenue_rows)
    avg_monthly = total_rev / months if months else 0.0
    annualised = avg_monthly * 12

    if method == "historical":
        growth_factor = 1.15
        suggested = annualised * growth_factor
        basis = f"12-month avg ${avg_monthly:,.0f}/mo × 12 × 1.15 growth factor"
    else:
        # top_down: company average
        total_reps = (await db.execute(select(func.count(Rep.id)))).scalar() or 1
        company_rev = (await db.execute(select(func.sum(Revenue.amount)))).scalar() or 0
        company_annual = float(company_rev) * (12 / months)
        suggested = company_annual * 1.15 / total_reps
        basis = f"Company-wide annual revenue / {total_reps} reps × 1.15"

    # Ramp adjustment for new reps (< 6 months)
    ramp_adjusted = False
    if rep.hire_date:
        months_since_hire = (date.today() - rep.hire_date).days // 30
        ramp_pct = {0: 0, 1: 0, 2: 0, 3: 0.25, 4: 0.50, 5: 0.75}.get(
            min(months_since_hire, 5), 1.0
        )
        if ramp_pct < 1.0:
            suggested = suggested * ramp_pct
            ramp_adjusted = True
            basis += f" (ramp-adjusted {ramp_pct:.0%})"

    return {
        "rep_id": str(rep_id),
        "rep_name": rep.name,
        "suggested_quota": round(suggested, 2),
        "method": method,
        "confidence": "high" if months >= 6 else "medium",
        "basis": basis,
        "ramp_adjusted": ramp_adjusted,
        "history_months_used": months,
    }


@router.get("/quota-ramp/{rep_id}")
async def quota_ramp_schedule(
    rep_id: uuid.UUID,
    base_quota: float = Query(..., description="Full annual quota amount"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return a 12-month ramp schedule for a rep based on hire date."""
    rep = (await db.execute(select(Rep).where(Rep.id == rep_id))).scalars().first()
    if not rep:
        raise HTTPException(status_code=404, detail="Rep not found")

    # Standard ramp: month 1-2 = 0%, month 3 = 25%, 4 = 50%, 5 = 75%, 6+ = 100%
    RAMP = {1: 0.0, 2: 0.0, 3: 0.25, 4: 0.50, 5: 0.75}

    schedule = []
    for month_num in range(1, 13):
        ramp_pct = RAMP.get(month_num, 1.0)
        monthly_quota = (base_quota / 12) * ramp_pct
        schedule.append({
            "month": month_num,
            "ramp_pct": ramp_pct,
            "expected_quota": round(monthly_quota, 2),
            "full_quota": round(base_quota / 12, 2),
        })

    months_since_hire = None
    if rep.hire_date:
        months_since_hire = (date.today() - rep.hire_date).days // 30

    return {
        "rep_id": str(rep_id),
        "rep_name": rep.name,
        "hire_date": rep.hire_date.isoformat() if rep.hire_date else None,
        "months_since_hire": months_since_hire,
        "base_annual_quota": base_quota,
        "ramp_schedule": schedule,
    }


@router.get("/quota-fairness")
async def quota_fairness(
    period: Optional[str] = Query(None, description="Period filter e.g. 2025-Q1, ytd, all-time"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Check quota equity across reps. Returns Gini coefficient and outliers."""
    start_p, end_p, period_label = _resolve_period_alias(period)

    rep_ids = (
        await db.execute(select(Quota.rep_id).group_by(Quota.rep_id))
    ).scalars().all()

    rows: list[dict[str, Any]] = []
    for rid in rep_ids:
        if start_p and end_p:
            quota_total = await _rep_quota_for_period(db, rid, start_p, end_p)
        else:
            quota_total = await _rep_quota_all_time(db, rid)
        rows.append({"rep_id": rid, "total": float(quota_total or 0.0)})

    if not rows:
        return {"gini": None, "mean_quota": 0.0, "std_quota": 0.0, "outliers": [], "fairness_score": 0.0}

    import numpy as np  # already in requirements

    quotas = sorted([float(r["total"] or 0) for r in rows])
    n = len(quotas)
    mean_q = float(np.mean(quotas))
    std_q = float(np.std(quotas))

    # Gini coefficient
    diff_sum = sum(abs(a - b) for i, a in enumerate(quotas) for b in quotas[i + 1:])
    gini = diff_sum / (n * sum(quotas)) if sum(quotas) > 0 else 0.0

    # Outliers: > 2 std devs from mean
    outlier_rep_ids = []
    for row in rows:
        if abs(float(row["total"] or 0) - mean_q) > 2 * std_q and std_q > 0:
            rep = (await db.execute(select(Rep).where(Rep.id == row["rep_id"]))).scalars().first()
            outlier_rep_ids.append({
                "rep_id": str(row["rep_id"]),
                "rep_name": rep.name if rep else "Unknown",
                "quota": float(row["total"]),
                "deviation_std": round((float(row["total"]) - mean_q) / std_q, 2),
            })

    fairness_score = max(0.0, round(100 * (1 - gini), 1))

    return {
        "period": period_label,
        "rep_count": n,
        "mean_quota": round(mean_q, 2),
        "std_quota": round(std_q, 2),
        "gini": round(gini, 4),
        "fairness_score": fairness_score,
        "outliers": outlier_rep_ids,
        "methodology": "Gini coefficient on per-rep total quota; outliers are >2σ from mean",
    }


# ── SPIFF / Clawback management ───────────────────────────────────────────

class SpiffRuleRequest(BaseModel):
    name: str
    amount: float
    trigger_metric: str = "win_rate"
    trigger_threshold: float = 0.0
    is_active: bool = True


class ClawbackRuleRequest(BaseModel):
    name: str
    penalty_pct: float
    trigger_metric: str = "win_rate"
    trigger_below: float = 0.0
    is_active: bool = True


@router.get("/spiffs")
async def list_spiffs() -> dict[str, Any]:
    """List all active SPIFF rules in the current session config."""
    return {
        "spiff_rules": [
            {
                "name": s.name,
                "amount": s.amount,
                "trigger_metric": s.trigger_metric,
                "trigger_threshold": s.trigger_threshold,
                "is_active": s.is_active,
            }
            for s in (_active_config.spiff_rules or [])
        ],
        "count": len(_active_config.spiff_rules or []),
    }


@router.post("/spiffs")
async def add_spiff(
    req: SpiffRuleRequest,
    _: UserContext = Depends(require_permission("manage_rules")),
) -> dict[str, Any]:
    """Add a SPIFF rule to the active session config."""
    global _active_config
    spiff = SpiffRule(
        name=req.name,
        amount=req.amount,
        trigger_metric=req.trigger_metric,
        trigger_threshold=req.trigger_threshold,
        is_active=req.is_active,
    )
    # Replace existing SPIFF with same name if any
    existing = [s for s in (_active_config.spiff_rules or []) if s.name != req.name]
    existing.append(spiff)
    _active_config.spiff_rules = existing
    return {"message": f"SPIFF '{req.name}' added", "total_spiffs": len(existing)}


@router.delete("/spiffs/{spiff_name}")
async def remove_spiff(
    spiff_name: str,
    _: UserContext = Depends(require_permission("manage_rules")),
) -> dict[str, Any]:
    """Remove a SPIFF rule by name."""
    global _active_config
    before = len(_active_config.spiff_rules or [])
    _active_config.spiff_rules = [s for s in (_active_config.spiff_rules or []) if s.name != spiff_name]
    removed = before - len(_active_config.spiff_rules)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"SPIFF '{spiff_name}' not found")
    return {"message": f"SPIFF '{spiff_name}' removed"}


@router.get("/clawbacks")
async def list_clawbacks() -> dict[str, Any]:
    """List all active clawback rules in the current session config."""
    return {
        "clawback_rules": [
            {
                "name": c.name,
                "penalty_pct": c.penalty_pct,
                "trigger_metric": c.trigger_metric,
                "trigger_below": c.trigger_below,
                "is_active": c.is_active,
            }
            for c in (_active_config.clawback_rules or [])
        ],
        "count": len(_active_config.clawback_rules or []),
    }


@router.post("/clawbacks")
async def add_clawback(
    req: ClawbackRuleRequest,
    _: UserContext = Depends(require_permission("manage_rules")),
) -> dict[str, Any]:
    """Add a clawback rule to the active session config."""
    global _active_config
    cb = ClawbackRule(
        name=req.name,
        penalty_pct=req.penalty_pct,
        trigger_metric=req.trigger_metric,
        trigger_below=req.trigger_below,
        is_active=req.is_active,
    )
    existing = [c for c in (_active_config.clawback_rules or []) if c.name != req.name]
    existing.append(cb)
    _active_config.clawback_rules = existing
    return {"message": f"Clawback '{req.name}' added", "total_clawbacks": len(existing)}


@router.delete("/clawbacks/{clawback_name}")
async def remove_clawback(
    clawback_name: str,
    _: UserContext = Depends(require_permission("manage_rules")),
) -> dict[str, Any]:
    """Remove a clawback rule by name."""
    global _active_config
    before = len(_active_config.clawback_rules or [])
    _active_config.clawback_rules = [c for c in (_active_config.clawback_rules or []) if c.name != clawback_name]
    removed = before - len(_active_config.clawback_rules)
    if removed == 0:
        raise HTTPException(status_code=404, detail=f"Clawback '{clawback_name}' not found")
    return {"message": f"Clawback '{clawback_name}' removed"}


# ── Payout Forecast ───────────────────────────────────────────────────────

@router.get("/forecast")
async def payout_forecast(
    periods: int = Query(4, ge=1, le=12, description="Number of future quarters to forecast"),
    rep_id: Optional[uuid.UUID] = Query(None, description="Filter to a single rep UUID"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Project future commission payouts per rep using revenue forecasting + plan rules.

    For each rep:
    1. Fetches historical monthly revenue (last 24 months).
    2. Runs the ML ensemble revenue forecast for the next `periods` quarters.
    3. Applies the rep's plan-specific commission tiers to projected revenue
       against their most recent quarterly quota as the baseline.
    4. Returns per-rep projected payouts with tier breakdown.
    """
    from backend.ml.forecasting import run_revenue_forecast

    # Load plan configs and rep→user mappings once
    plan_configs = await _load_plan_configs(db)
    user_rows_all = (await db.execute(select(UserProfile.email, UserProfile.id))).all()
    email_to_uid: dict[str, uuid.UUID] = {(u.email or "").lower(): u.id for u in user_rows_all}
    pa_rows = (await db.execute(select(PlanAssignment))).scalars().all()
    uid_to_plan: dict[uuid.UUID, uuid.UUID] = {pa.user_id: pa.plan_id for pa in pa_rows}

    reps_q = select(Rep)
    if rep_id:
        reps_q = reps_q.where(Rep.id == rep_id)
    reps = (await db.execute(reps_q)).scalars().all()

    if not reps:
        raise HTTPException(status_code=404, detail="No reps found")

    # Determine future quarter labels starting from today
    from datetime import date as _date
    today = _date.today()
    current_q = (today.month - 1) // 3 + 1
    future_quarters: list[str] = []
    yr, qn = today.year, current_q + 1  # start from next quarter
    if qn > 4:
        yr += 1
        qn = 1
    for _ in range(periods):
        future_quarters.append(f"{yr}-Q{qn}")
        qn += 1
        if qn > 4:
            yr += 1
            qn = 1

    rep_forecasts: list[dict[str, Any]] = []

    for rep in reps:
        # Fetch last 24 months of revenue
        rev_rows = (
            await db.execute(
                select(Revenue.period, func.sum(Revenue.amount).label("total"))
                .where(Revenue.rep_id == rep.id)
                .group_by(Revenue.period)
                .order_by(Revenue.period.desc())
                .limit(24)
            )
        ).all()

        revenue_by_month: dict[str, float] = {r.period: float(r.total or 0) for r in rev_rows}

        # Fetch most recent quarterly quota as a baseline
        latest_quota_row = (
            await db.execute(
                select(Quota.period, func.sum(Quota.amount).label("total"))
                .where(Quota.rep_id == rep.id)
                .group_by(Quota.period)
                .order_by(Quota.period.desc())
                .limit(1)
            )
        ).first()
        baseline_quarterly_quota = float(latest_quota_row.total or 0) if latest_quota_row else 0.0

        # Get plan config for this rep
        uid = email_to_uid.get((rep.email or "").lower())
        plan_id = uid_to_plan.get(uid) if uid else None
        cfg = plan_configs.get(plan_id) if plan_id else None
        cfg = cfg or _active_config
        plan_name = None
        if plan_id:
            from backend.models import Plan as PlanModel
            plan_obj = (await db.execute(select(PlanModel).where(PlanModel.id == plan_id))).scalars().first()
            plan_name = plan_obj.name if plan_obj else None

        # Run revenue forecast — needs at least 6 months
        if len(revenue_by_month) < 3:
            # Not enough history — carry forward last known monthly average
            avg_monthly = sum(revenue_by_month.values()) / len(revenue_by_month) if revenue_by_month else 0.0
            quarterly_forecasts = [avg_monthly * 3] * periods
            forecast_confidence = "low"
        else:
            try:
                forecast_result = run_revenue_forecast(revenue_by_month)
                # Aggregate monthly forecast into quarters matching future_quarters
                # run_revenue_forecast returns a dict (see backend/ml/forecasting.py),
                # not an object — forecast_result.forecast raised AttributeError on
                # every call, silently caught by the except below, so this endpoint
                # had never actually run the real ensemble forecast; it always fell
                # through to the seasonal-heuristic fallback regardless of how much
                # history a rep had. Confirmed live before this fix: every rep in a
                # 12-rep company with 8-24 months of history came back "medium"
                # confidence, never "high" — the only way that combination happens.
                monthly_proj: list[float] = list(forecast_result["forecast_values"])
                quarterly_forecasts = []
                for i in range(periods):
                    q_rev = sum(monthly_proj[i * 3: i * 3 + 3]) if len(monthly_proj) >= (i + 1) * 3 else (
                        sum(monthly_proj[i * 3:]) or (avg_monthly * 3 if (avg_monthly := sum(revenue_by_month.values()) / len(revenue_by_month)) else 0)
                    )
                    quarterly_forecasts.append(q_rev)
                forecast_confidence = "high" if len(revenue_by_month) >= 12 else "medium"
            except Exception:
                # Growth-adjusted seasonal fallback: use archetype seasonal_quarterly
                # pattern so manager/SDR forecasts reflect realistic cadence.
                avg_monthly = sum(revenue_by_month.values()) / len(revenue_by_month) if revenue_by_month else 0.0
                avg_quarterly = avg_monthly * 3

                # Detect archetype from plan name for seasonal weighting
                _archetype_seasonal: dict[int, float] = {1: 0.80, 2: 0.95, 3: 1.10, 4: 1.15}
                if plan_name:
                    pn_lower = (plan_name or "").lower()
                    if "insurance" in pn_lower:
                        _archetype_seasonal = {1: 0.82, 2: 0.95, 3: 1.05, 4: 1.18}
                    elif "smb" in pn_lower:
                        _archetype_seasonal = {1: 0.85, 2: 0.95, 3: 1.05, 4: 1.15}
                    elif "overlay" in pn_lower or "specialist" in pn_lower:
                        _archetype_seasonal = {1: 0.78, 2: 0.95, 3: 1.12, 4: 1.15}
                    elif "field" in pn_lower:
                        _archetype_seasonal = {1: 0.80, 2: 0.95, 3: 1.10, 4: 1.15}

                # Compute seasonal mean so scaling preserves the avg_quarterly level
                seasonal_mean = sum(_archetype_seasonal.values()) / len(_archetype_seasonal)
                # Small YoY growth nudge — 5% annualised for a realistic trend
                annual_growth_rate = 0.05
                quarterly_forecasts = []
                for i in range(periods):
                    q_label = future_quarters[i] if i < len(future_quarters) else ""
                    # Determine calendar quarter number from label (e.g. "2025-Q3" → 3)
                    try:
                        cal_q = int(q_label.split("-Q")[1]) if "-Q" in q_label else ((i % 4) + 1)
                    except (IndexError, ValueError):
                        cal_q = (i % 4) + 1
                    season_factor = _archetype_seasonal.get(cal_q, 1.0) / seasonal_mean
                    growth_factor_q = (1 + annual_growth_rate) ** (i / 4)
                    quarterly_forecasts.append(round(avg_quarterly * season_factor * growth_factor_q, 2))

                forecast_confidence = "medium" if len(revenue_by_month) >= 6 else "low"

        # Apply plan commission rules to each projected quarter
        quarter_rows: list[dict[str, Any]] = []
        for i, q_label in enumerate(future_quarters):
            proj_rev = quarterly_forecasts[i] if i < len(quarterly_forecasts) else 0.0
            won_est = max(1, int(proj_rev / 20000))  # rough deal count estimate
            result = compute_payout(proj_rev, baseline_quarterly_quota, won_est, 0, cfg)

            tier_label = "n/a"
            for tier in cfg.tiers:
                if tier.min_attainment_pct <= result["attainment_pct"] < tier.max_attainment_pct:
                    tier_label = f"{tier.min_attainment_pct:.0f}–{tier.max_attainment_pct:.0f}% @ {tier.rate:.0%}"
                    break

            quarter_rows.append({
                "period": q_label,
                "projected_revenue": round(proj_rev, 2),
                "quota": round(baseline_quarterly_quota, 2),
                "projected_attainment_pct": result["attainment_pct"],
                "projected_base_commission": result["base_commission"],
                "projected_accelerator": result["accelerator"],
                "projected_bonus": result["bonus"],
                "projected_total_payout": result["payout"],
                "commission_rate": result["commission_rate"],
                "tier_applied": tier_label,
            })

        rep_forecasts.append({
            "rep_id": str(rep.id),
            "rep_name": rep.name,
            "plan_name": plan_name,
            "forecast_confidence": forecast_confidence,
            "history_months": len(revenue_by_month),
            "baseline_quarterly_quota": round(baseline_quarterly_quota, 2),
            "quarters": quarter_rows,
            "total_projected_payout": round(sum(q["projected_total_payout"] for q in quarter_rows), 2),
        })

    rep_forecasts.sort(key=lambda r: r["total_projected_payout"], reverse=True)
    team_total = sum(r["total_projected_payout"] for r in rep_forecasts)

    return {
        "periods": future_quarters,
        "period_count": periods,
        "rep_count": len(rep_forecasts),
        "team_projected_payout": round(team_total, 2),
        "reps": rep_forecasts,
    }


@router.get("/audit/{company_name}")
def get_payout_audit(
    company_name: str,
    period: str = Query("", description="Quarter to audit, e.g. '2026-Q1'. Empty = most recent."),
    tolerance: float = Query(1.0, description="Acceptable $ delta between stored and engine payout."),
    mape_threshold: float = Query(0.40, description="Forecast MAPE above this flags a rep as high-error."),
) -> dict[str, Any]:
    """
    Run the full payout-math + ML-forecast audit for a company directory.

    Returns a structured audit report with:
    - Per-rep: plan, rules, quota, revenue, attainment, CSV vs engine payout, bugs
    - Per-rep: forecast strategy, backtest MAE/MAPE, next-Q revenue projection
    - Summary: reps_ok / reps_with_bugs / estimated_lost_commission / next-Q company forecast

    The audit reads the CSV layer directly (no DB required) so it reflects the
    exact data on disk, independently of what is in the database.
    """
    from pathlib import Path as _Path
    from backend.audit.payout_audit import audit_company

    company_dir = _Path("companies") / company_name
    if not company_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail=f"Company directory not found: companies/{company_name}",
        )

    report = audit_company(
        company_dir,
        period=period,
        payout_delta_tolerance=tolerance,
        mape_warn_threshold=mape_threshold,
    )
    return report.to_dict()
