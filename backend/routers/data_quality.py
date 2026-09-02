from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.auth.dependencies import require_permission
from backend.auth.tenant import get_tenant_context
from backend.config import settings
from backend.models import (
    Account,
    Booking,
    Deal,
    Manager,
    PayoutRecord,
    Plan,
    PlanAssignment,
    PlanCascadeRule,
    Position,
    Quota,
    Rep,
    Revenue,
    Rule,
    SalesCredit,
    Team,
    UserProfile,
    UserTerritoryAssignment,
)

router = APIRouter(
    prefix="/data-quality",
    tags=["Data Quality"],
    dependencies=[Depends(require_permission("view_data_quality")), Depends(get_tenant_context)],
)


def _status_from_count(count: int, fail: bool = False) -> str:
    if count <= 0:
        return "PASS"
    return "FAIL" if fail else "WARN"


_CRITICAL_FAIL_CHECKS = {
    "empty_table_reps",
    "empty_table_deals",
    "empty_table_revenue",
    "orphaned_revenue_records",
    "orphaned_deals",
    "negative_revenue",
    "manager_hierarchy_cycles",
}

_CHECK_REMEDIATION: dict[str, str] = {
    "missing_required_rep_name": "Populate rep names from source HRIS/CRM before ingestion.",
    "duplicate_rep_email": "Deduplicate reps on canonical email and keep one active user profile per email.",
    "null_foreign_key_deal_rep": "Backfill deal.rep_id using owner mapping from CRM exports.",
    "orphaned_revenue_records": "Repair revenue->rep mappings before payout or model workflows.",
    "orphaned_deals": "Attach orphan deals to valid accounts or archive invalid records.",
    "negative_revenue": "Only contraction/churn rows may be negative; fix other negative entries upstream.",
    "invalid_deal_dates": "Correct close dates so actual_close_date is not earlier than created_at.",
    "missing_quota": "Add quota records or configure approved fallback quota policy for active reps.",
    "sales_credit_coverage": "Load sales_credit rows for closed-won deals to avoid payout fallback mode.",
    "plan_assignment_coverage": "Assign a compensation plan to each active user profile.",
    "revenue_type_coverage": "Classify revenue rows by revenue_type for NRR/GRR and ARR diagnostics.",
    "booking_records_missing": "Ingest booking events for closed-won deals before ARR waterfall reporting.",
    "plan_cascade_coverage": "Define cascade rules for leadership-owned plans so downstream reps inherit policy.",
    "duplicate_deals": "Deduplicate duplicate deals by account/rep/name/amount/date fingerprint.",
    "missing_company_scope_context": "Provide X-Company-ID or company_id in requests outside demo mode.",
    "territory_missing": "Assign territories to active users to enable territory-aware reporting and quotas.",
    "plan_without_rules": "Attach at least one rule to every active compensation plan.",
    "payout_missing_source_record": "Ensure payout rows are traceable to sales credits or documented fallback traces.",
    "manager_hierarchy_cycles": "Break manager cycles; hierarchy must be a DAG for rollups and approvals.",
    "model_training_data_too_small": "Increase historical data volume before retraining production models.",
    "forecast_period_too_short": "Use at least 6 monthly points for forecast-only trend diagnostics.",
}

_CHECK_ENTITY: dict[str, str] = {
    "missing_required_rep_name": "rep",
    "duplicate_rep_email": "rep",
    "null_foreign_key_deal_rep": "deal",
    "orphaned_revenue_records": "revenue",
    "orphaned_deals": "deal",
    "negative_revenue": "revenue",
    "invalid_deal_dates": "deal",
    "missing_quota": "quota",
    "sales_credit_coverage": "sales_credit",
    "plan_assignment_coverage": "plan_assignment",
    "revenue_type_coverage": "revenue",
    "booking_records_missing": "booking",
    "plan_cascade_coverage": "plan_cascade_rule",
    "duplicate_deals": "deal",
    "missing_company_scope_context": "tenant_context",
    "territory_missing": "territory_assignment",
    "plan_without_rules": "rule",
    "payout_missing_source_record": "payout",
    "manager_hierarchy_cycles": "manager_hierarchy",
    "model_training_data_too_small": "ml_training_data",
    "forecast_period_too_short": "forecast_input",
}


def _enrich_check(check: dict[str, Any]) -> dict[str, Any]:
    name = str(check.get("name") or "")
    status = str(check.get("status") or "PASS").upper()

    if status == "PASS":
        severity = "info"
    elif status == "WARN":
        severity = "warning"
    else:
        severity = "critical" if name in _CRITICAL_FAIL_CHECKS else "error"

    return {
        **check,
        "severity": severity,
        "affected_entity": _CHECK_ENTITY.get(name, "dataset"),
        "remediation": _CHECK_REMEDIATION.get(name, "Review source mappings and correct upstream records."),
    }


async def _build_checks(db: AsyncSession) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    # Empty table checks
    table_counts = {
        "teams": int((await db.execute(select(func.count()).select_from(Team))).scalar() or 0),
        "reps": int((await db.execute(select(func.count()).select_from(Rep))).scalar() or 0),
        "accounts": int((await db.execute(select(func.count()).select_from(Account))).scalar() or 0),
        "deals": int((await db.execute(select(func.count()).select_from(Deal))).scalar() or 0),
        "revenue": int((await db.execute(select(func.count()).select_from(Revenue))).scalar() or 0),
        "quotas": int((await db.execute(select(func.count()).select_from(Quota))).scalar() or 0),
    }
    for table, count in table_counts.items():
        checks.append(
            {
                "name": f"empty_table_{table}",
                "status": "FAIL" if count == 0 else "PASS",
                "message": f"Table '{table}' has {count} rows",
                "affected_rows": 0 if count > 0 else 1,
            }
        )

    # Missing required fields
    missing_rep_name = int((await db.execute(select(func.count(Rep.id)).where((Rep.name.is_(None)) | (Rep.name == "")))).scalar() or 0)
    checks.append({
        "name": "missing_required_rep_name",
        "status": _status_from_count(missing_rep_name, fail=True),
        "message": "Reps with missing name",
        "affected_rows": missing_rep_name,
    })

    # Duplicates
    dup_rep_email = int((await db.execute(select(func.count()).select_from(
        select(Rep.email).where(Rep.email.isnot(None)).group_by(Rep.email).having(func.count() > 1).subquery()
    ))).scalar() or 0)
    checks.append({
        "name": "duplicate_rep_email",
        "status": _status_from_count(dup_rep_email, fail=True),
        "message": "Duplicate rep emails",
        "affected_rows": dup_rep_email,
    })

    # Null FKs and orphans
    null_deal_rep = int((await db.execute(select(func.count(Deal.id)).where(Deal.rep_id.is_(None)))).scalar() or 0)
    checks.append({
        "name": "null_foreign_key_deal_rep",
        "status": _status_from_count(null_deal_rep),
        "message": "Deals with null rep_id",
        "affected_rows": null_deal_rep,
    })

    orphan_revenue = int((await db.execute(
        select(func.count()).select_from(Revenue).outerjoin(Rep, Revenue.rep_id == Rep.id).where(Rep.id.is_(None))
    )).scalar() or 0)
    checks.append({
        "name": "orphaned_revenue_records",
        "status": _status_from_count(orphan_revenue, fail=True),
        "message": "Revenue rows without matching rep",
        "affected_rows": orphan_revenue,
    })

    orphan_deals = int((await db.execute(
        select(func.count()).select_from(Deal).outerjoin(Account, Deal.account_id == Account.id).where(Account.id.is_(None))
    )).scalar() or 0)
    checks.append({
        "name": "orphaned_deals",
        "status": _status_from_count(orphan_deals, fail=True),
        "message": "Deals without matching account",
        "affected_rows": orphan_deals,
    })

    # Revenue sanity
    # Negative rows are valid for churn/contraction adjustments; fail only unexpected negatives.
    allowed_negative_types = ("churn", "contraction")
    negative_revenue_total = int((await db.execute(select(func.count(Revenue.id)).where(Revenue.amount < 0))).scalar() or 0)
    negative_revenue_unexpected = int((await db.execute(
        select(func.count(Revenue.id)).where(
            Revenue.amount < 0,
            (Revenue.revenue_type.is_(None)) | (~Revenue.revenue_type.in_(allowed_negative_types)),
        )
    )).scalar() or 0)
    negative_revenue_valid = max(0, negative_revenue_total - negative_revenue_unexpected)

    if negative_revenue_unexpected > 0:
        message = (
            f"Revenue rows with unexpected negative amount: {negative_revenue_unexpected} "
            "(excluding churn/contraction adjustments)"
        )
    elif negative_revenue_valid > 0:
        message = (
            f"{negative_revenue_valid} negative revenue rows are valid churn/contraction adjustments"
        )
    else:
        message = "No negative revenue rows"

    checks.append({
        "name": "negative_revenue",
        "status": _status_from_count(negative_revenue_unexpected, fail=True),
        "message": message,
        "affected_rows": negative_revenue_unexpected,
    })

    # Date validity
    invalid_deal_dates = int((await db.execute(
        select(func.count(Deal.id)).where(Deal.actual_close_date.isnot(None), Deal.actual_close_date < Deal.created_at)
    )).scalar() or 0)
    checks.append({
        "name": "invalid_deal_dates",
        "status": _status_from_count(invalid_deal_dates),
        "message": "Deals where actual_close_date is before created_at",
        "affected_rows": invalid_deal_dates,
    })

    # Unmapped reps/teams and missing quota
    unmapped_reps = int((await db.execute(select(func.count(Rep.id)).where(Rep.team_id.is_(None)))).scalar() or 0)
    checks.append({
        "name": "unmapped_reps",
        "status": _status_from_count(unmapped_reps),
        "message": "Reps without mapped team",
        "affected_rows": unmapped_reps,
    })

    unmapped_teams = int((await db.execute(select(func.count(Team.id)).where(Team.region.is_(None)))).scalar() or 0)
    checks.append({
        "name": "unmapped_teams",
        "status": _status_from_count(unmapped_teams),
        "message": "Teams without region",
        "affected_rows": unmapped_teams,
    })

    reps_without_quota_total = int((await db.execute(
        select(func.count()).select_from(Rep).outerjoin(Quota, Rep.id == Quota.rep_id).where(Quota.id.is_(None))
    )).scalar() or 0)

    # Only warn for active reps that are producing revenue without any quota.
    reps_without_quota_active = int((await db.execute(
        select(func.count()).select_from(
            select(Rep.id)
            .select_from(Rep)
            .outerjoin(Quota, Rep.id == Quota.rep_id)
            .outerjoin(Revenue, Rep.id == Revenue.rep_id)
            .where(Quota.id.is_(None))
            .group_by(Rep.id)
            .having(func.coalesce(func.sum(Revenue.amount), 0) > 0)
            .subquery()
        )
    )).scalar() or 0)

    if reps_without_quota_active > 0:
        quota_message = "Active reps with no quota records"
    elif reps_without_quota_total > 0:
        quota_message = (
            f"{reps_without_quota_total} inactive reps have no quota records; not impacting current attainment"
        )
    else:
        quota_message = "All reps have quota records"

    checks.append({
        "name": "missing_quota",
        "status": _status_from_count(reps_without_quota_active),
        "message": quota_message,
        "affected_rows": reps_without_quota_active,
    })

    # ── RevOps-aware checks ───────────────────────────────────────────────

    # SalesCredit coverage — reps-with-closed-won-deals who have a SalesCredit row
    # ID chain: Deal.rep_id → Rep.id → UserProfile.external_id ("USR-{rep_id[:8]}") → UserProfile.id = SalesCredit.user_id
    reps_with_cw = set(
        str(r) for (r,) in (await db.execute(
            select(func.distinct(Deal.rep_id)).where(Deal.stage == "Closed Won")
        )).all()
    )
    # Map Rep.id → UserProfile.id via external_id = "USR-{rep_id[:8]}"
    up_rows = (await db.execute(select(UserProfile.id, UserProfile.external_id))).all()
    rep_id_to_up_id: dict[str, str] = {}
    for up_id, ext_id in up_rows:
        if ext_id and ext_id.startswith("USR-"):
            prefix = ext_id[4:]
            for rid in reps_with_cw:
                if rid.startswith(prefix) or rid.replace("-", "").startswith(prefix.replace("-", "")):
                    rep_id_to_up_id[rid] = str(up_id)
                    break

    credited_up_ids = set(
        str(r) for (r,) in (await db.execute(
            select(func.distinct(SalesCredit.user_id))
        )).all()
    )
    if reps_with_cw:
        covered = {rid for rid, upid in rep_id_to_up_id.items() if upid in credited_up_ids}
        coverage_pct = round(len(covered) / len(reps_with_cw) * 100, 1)
        sc_status = "PASS" if coverage_pct >= 80 else ("WARN" if coverage_pct >= 40 else "FAIL")
        uncovered = len(reps_with_cw) - len(covered)
        checks.append({
            "name": "sales_credit_coverage",
            "status": sc_status,
            "message": (
                f"{len(covered)} of {len(reps_with_cw)} reps with closed-won deals have SalesCredit records ({coverage_pct}% rep coverage). "
                + ("Payout engine will use rep-level fallback for uncovered reps." if uncovered else "Full coverage.")
            ),
            "affected_rows": uncovered,
        })
    else:
        checks.append({
            "name": "sales_credit_coverage",
            "status": "WARN",
            "message": "No closed-won deals found; cannot assess SalesCredit coverage.",
            "affected_rows": 0,
        })

    # PlanAssignment coverage — fraction of UserProfiles with a plan assigned
    user_count = int((await db.execute(select(func.count()).select_from(UserProfile))).scalar() or 0)
    assigned_count = int((await db.execute(
        select(func.count(func.distinct(PlanAssignment.user_id)))
    )).scalar() or 0)
    if user_count > 0:
        plan_pct = round(assigned_count / user_count * 100, 1)
        pa_status = "PASS" if plan_pct >= 80 else ("WARN" if plan_pct >= 50 else "FAIL")
        checks.append({
            "name": "plan_assignment_coverage",
            "status": pa_status,
            "message": (
                f"{plan_pct}% of users have a plan assignment. "
                "Payout engine will use fallback rates for unassigned users."
            ),
            "affected_rows": max(0, user_count - assigned_count),
        })
    else:
        checks.append({
            "name": "plan_assignment_coverage",
            "status": "WARN",
            "message": "No user profiles found; cannot assess plan assignment coverage.",
            "affected_rows": 0,
        })

    # Revenue type coverage — fraction of revenue rows with revenue_type set
    rev_total = int((await db.execute(select(func.count()).select_from(Revenue))).scalar() or 0)
    rev_typed = int((await db.execute(
        select(func.count(Revenue.id)).where(Revenue.revenue_type.isnot(None))
    )).scalar() or 0)
    if rev_total > 0:
        type_pct = round(rev_typed / rev_total * 100, 1)
        rt_status = "PASS" if type_pct >= 70 else ("WARN" if type_pct >= 30 else "FAIL")
        checks.append({
            "name": "revenue_type_coverage",
            "status": rt_status,
            "message": (
                f"{type_pct}% of revenue rows have a revenue_type (new_biz/renewal/expansion/etc.). "
                "NRR/GRR metrics will use [FALLBACK] estimation for untyped rows."
            ),
            "affected_rows": rev_total - rev_typed,
        })

    # Booking coverage — closed-won deals that have a corresponding booking row
    closed_won_count = int((await db.execute(
        select(func.count(Deal.id)).where(Deal.stage == "Closed Won")
    )).scalar() or 0)
    booking_count = int((await db.execute(select(func.count()).select_from(Booking))).scalar() or 0)
    if closed_won_count > 0 and booking_count == 0:
        checks.append({
            "name": "booking_records_missing",
            "status": "WARN",
            "message": (
                f"No booking records found despite {closed_won_count} closed-won deals. "
                "ARR waterfall and revenue recognition metrics may be inaccurate."
            ),
            "affected_rows": closed_won_count,
        })

    # Plan cascade coverage — fraction of executive/director users who have at least
    # one PlanCascadeRule defined so their plans flow down to their reports.
    # Only positions with rank <= 3 (executive/vp/director) are expected to own cascade rules.
    exec_users = (
        await db.execute(
            select(func.count(func.distinct(UserProfile.id)))
            .join(Position, UserProfile.position_id == Position.id)
            .where(Position.rank <= 3)
        )
    ).scalar() or 0
    exec_users = int(exec_users)
    if exec_users > 0:
        covered = int((
            await db.execute(
                select(func.count(func.distinct(PlanCascadeRule.owner_user_id)))
            )
        ).scalar() or 0)
        cascade_pct = round(covered / exec_users * 100, 1)
        cs_status = "PASS" if cascade_pct >= 80 else ("WARN" if cascade_pct >= 40 else "FAIL")
        checks.append({
            "name": "plan_cascade_coverage",
            "status": cs_status,
            "message": (
                f"{cascade_pct}% of executive/director users have at least one plan cascade rule. "
                "Reps under uncovered managers will not inherit global comp rules."
            ),
            "affected_rows": max(0, exec_users - covered),
        })
    else:
        checks.append({
            "name": "plan_cascade_coverage",
            "status": "WARN",
            "message": (
                "No executive or director-level positions found (rank ≤ 3). "
                "Assign rank values to positions to enable cascade rule validation."
            ),
            "affected_rows": 0,
        })

    # Duplicate deal fingerprints
    duplicate_deals = int((await db.execute(select(func.count()).select_from(
        select(
            Deal.account_id,
            Deal.rep_id,
            Deal.name,
            Deal.amount,
            Deal.expected_close_date,
        )
        .group_by(
            Deal.account_id,
            Deal.rep_id,
            Deal.name,
            Deal.amount,
            Deal.expected_close_date,
        )
        .having(func.count() > 1)
        .subquery()
    ))).scalar() or 0)
    checks.append({
        "name": "duplicate_deals",
        "status": _status_from_count(duplicate_deals, fail=True),
        "message": "Potential duplicate deals detected by account/rep/name/amount/expected_close_date",
        "affected_rows": duplicate_deals,
    })

    # Tenant context readiness check (schema migration pending, but request-level scope still required)
    checks.append({
        "name": "missing_company_scope_context",
        "status": "PASS" if settings.DEMO_MODE else "WARN",
        "message": (
            "Demo mode default company scoping is active."
            if settings.DEMO_MODE
            else "Production requests should include X-Company-ID or company_id for tenant-safe reads."
        ),
        "affected_rows": 0 if settings.DEMO_MODE else 1,
    })

    users_without_territory = int((await db.execute(
        select(func.count())
        .select_from(UserProfile)
        .outerjoin(UserTerritoryAssignment, UserProfile.id == UserTerritoryAssignment.user_id)
        .where(UserTerritoryAssignment.id.is_(None))
    )).scalar() or 0)
    checks.append({
        "name": "territory_missing",
        "status": _status_from_count(users_without_territory),
        "message": "Users without a territory assignment",
        "affected_rows": users_without_territory,
    })

    plans_without_rules = int((await db.execute(
        select(func.count())
        .select_from(Plan)
        .outerjoin(Rule, Plan.id == Rule.plan_id)
        .where(Rule.id.is_(None))
    )).scalar() or 0)
    checks.append({
        "name": "plan_without_rules",
        "status": _status_from_count(plans_without_rules, fail=True),
        "message": "Compensation plans without any assigned rule",
        "affected_rows": plans_without_rules,
    })

    payout_count = int((await db.execute(select(func.count()).select_from(PayoutRecord))).scalar() or 0)
    sales_credit_count = int((await db.execute(select(func.count()).select_from(SalesCredit))).scalar() or 0)
    payout_missing_source = payout_count if payout_count > 0 and sales_credit_count == 0 else 0
    checks.append({
        "name": "payout_missing_source_record",
        "status": _status_from_count(payout_missing_source, fail=True),
        "message": "Payout rows exist without sales credit source records in current dataset",
        "affected_rows": payout_missing_source,
    })

    # Manager hierarchy cycle detection
    mgr_rows = (await db.execute(select(Manager.user_id, Manager.manager_user_id))).all()
    manager_by_user: dict[str, str] = {}
    for user_id, manager_user_id in mgr_rows:
        if user_id and manager_user_id:
            manager_by_user[str(user_id)] = str(manager_user_id)

    cycle_count = 0
    for user_id in manager_by_user:
        seen: set[str] = set()
        cur = user_id
        while cur in manager_by_user:
            if cur in seen:
                cycle_count += 1
                break
            seen.add(cur)
            cur = manager_by_user[cur]

    checks.append({
        "name": "manager_hierarchy_cycles",
        "status": _status_from_count(cycle_count, fail=True),
        "message": "Manager hierarchy cycle detection",
        "affected_rows": cycle_count,
    })

    revenue_periods = int((await db.execute(select(func.count(func.distinct(Revenue.period))))).scalar() or 0)
    if revenue_periods < 12:
        mt_status = "FAIL"
    elif revenue_periods < 18:
        mt_status = "WARN"
    else:
        mt_status = "PASS"
    checks.append({
        "name": "model_training_data_too_small",
        "status": mt_status,
        "message": f"Distinct revenue periods available for model training: {revenue_periods}",
        "affected_rows": max(0, 18 - revenue_periods),
    })

    checks.append({
        "name": "forecast_period_too_short",
        "status": "FAIL" if revenue_periods < 6 else "PASS",
        "message": f"Forecast diagnostics require >= 6 monthly periods (current: {revenue_periods})",
        "affected_rows": max(0, 6 - revenue_periods),
    })

    checks = [_enrich_check(c) for c in checks]

    return checks


@router.get("/checks")
async def data_quality_checks(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    raw_checks = await _build_checks(db)
    checks = [c if c.get("severity") else _enrich_check(c) for c in raw_checks]
    return {
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/summary")
async def data_quality_summary(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    from backend.company_context import get_active_company
    raw_checks = await _build_checks(db)
    checks = [c if c.get("severity") else _enrich_check(c) for c in raw_checks]
    critical_count = sum(1 for c in checks if c.get("severity") == "critical")
    error_count = sum(1 for c in checks if c.get("severity") == "error")
    warning_count = sum(1 for c in checks if c.get("severity") == "warning")
    score = max(0, 100 - (critical_count * 20) - (error_count * 12) - (warning_count * 4))
    overall_status = "fail" if (critical_count + error_count) > 0 else ("warning" if warning_count > 0 else "pass")

    return {
        "company": get_active_company() or settings.DEMO_DEFAULT_COMPANY,
        "status": overall_status,
        "score": score,
        "checks": [
            {
                "name": c.get("name", ""),
                "status": "fail" if c.get("status") == "FAIL" else ("warning" if c.get("status") == "WARN" else "pass"),
                "details": c.get("message", ""),
                "affected_rows": c.get("affected_rows", 0),
                "severity": c.get("severity", "info"),
                "remediation": c.get("remediation", ""),
            }
            for c in checks
        ],
        "critical_count": critical_count,
        "error_count": error_count,
        "warning_count": warning_count,
        "blocks_sensitive_actions": critical_count > 0,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }