"""
DB-backed metric calculators used by API routes, reports, and agent tools.
"""
from __future__ import annotations

from typing import Any, Optional
from datetime import datetime, timedelta, date
import re
from sqlalchemy import select, func, and_, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Revenue, Quota, Deal, Rep, ArrWaterfallEntry, Booking, ChurnEvent
from backend.utils.period_filters import build_closed_deal_period_filter

CLOSED_STAGES = ("Closed Won", "Closed Lost")


def _to_date(val: Any) -> Optional[date]:
    """Convert a string 'YYYY-MM-DD' or date object to a Python date, or return None."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return date.fromisoformat(str(val)[:10])
    except (ValueError, TypeError):
        return None


def _normalize_filters(filters: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Normalize filter dict — always parse start_date/end_date to Python date objects."""
    if not filters:
        return {}
    result = dict(filters)
    if "start_date" in result:
        result["start_date"] = _to_date(result["start_date"])
    if "end_date" in result:
        result["end_date"] = _to_date(result["end_date"])
    return result


def _period_bounds(filters: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    start_period = str(start_date)[:7] if start_date else None
    end_period = str(end_date)[:7] if end_date else None
    return start_period, end_period


def _overlapping_quarter_labels(start_period: str, end_period: str) -> list[str]:
    """Return YYYY-QN labels for all quarters overlapping the given YYYY-MM range."""
    sy, sm = int(start_period[:4]), int(start_period[5:7])
    ey, em = int(end_period[:4]), int(end_period[5:7])
    seen: list[str] = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        label = f"{y}-Q{(m - 1) // 3 + 1}"
        if label not in seen:
            seen.append(label)
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return seen


def _rep_filters(filters: dict[str, Any], warnings: list[str]) -> list[Any]:
    clauses = []
    if filters.get("region"):
        clauses.append(Rep.region == filters["region"])
    if filters.get("team_id"):
        clauses.append(Rep.team_id == filters["team_id"])
    if filters.get("rep_id"):
        clauses.append(Rep.id == filters["rep_id"])
    if filters.get("stage"):
        warnings.append("Filter 'stage' is not applicable to rep-level scope and was ignored")
    return clauses


def _deal_filters(filters: dict[str, Any], warnings: list[str]) -> list[Any]:
    """General deal filters. Date bounds use Deal.created_at (pipeline/open-deal context)."""
    clauses = []
    if filters.get("stage"):
        clauses.append(Deal.stage == filters["stage"])
    if filters.get("product"):
        clauses.append(Deal.product == filters["product"])
    if filters.get("rep_id"):
        clauses.append(Deal.rep_id == filters["rep_id"])
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        start_dt = datetime.strptime(str(start_date)[:10], "%Y-%m-%d").date()
        clauses.append(cast(Deal.created_at, Date) >= start_dt)
    if end_date:
        end_dt = datetime.strptime(str(end_date)[:10], "%Y-%m-%d").date()
        clauses.append(cast(Deal.created_at, Date) <= end_dt)
    if filters.get("region") or filters.get("team_id"):
        warnings.append("Region/team deal filters are applied through rep joins when available")
    return clauses


def _closed_deal_filters(filters: dict[str, Any], warnings: list[str]) -> list[Any]:
    """Deal filters for closed-deal context. Date bounds use Deal.actual_close_date."""
    clauses = []
    if filters.get("product"):
        clauses.append(Deal.product == filters["product"])
    if filters.get("rep_id"):
        clauses.append(Deal.rep_id == filters["rep_id"])
    # Use actual_close_date — semantically correct for win-rate / closed-revenue queries
    clauses.extend(build_closed_deal_period_filter(filters))
    if filters.get("region") or filters.get("team_id"):
        warnings.append("Region/team deal filters are applied through rep joins when available")
    return clauses


async def get_total_revenue(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    start_period, end_period = _period_bounds(filters)

    q = select(func.sum(Revenue.amount)).select_from(Revenue).join(Rep, Revenue.rep_id == Rep.id, isouter=True)
    clauses = _rep_filters(filters, warnings)
    if start_period:
        clauses.append(Revenue.period >= start_period)
    if end_period:
        clauses.append(Revenue.period <= end_period)
    if clauses:
        q = q.where(and_(*clauses))

    value = (await db.execute(q)).scalar() or 0
    return {"value": float(value), "warnings": warnings, "sources": ["revenue"]}


async def get_total_quota(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    start_period, end_period = _period_bounds(filters)

    q = select(func.sum(Quota.amount)).select_from(Quota).join(Rep, Quota.rep_id == Rep.id, isouter=True)
    clauses = _rep_filters(filters, warnings)

    if start_period and end_period:
        # Quota rows are stored as YYYY-QN; convert the monthly range to quarter labels
        quarter_labels = _overlapping_quarter_labels(start_period, end_period)
        clauses.append(Quota.period.in_(quarter_labels))
    elif start_period:
        quarter_labels = _overlapping_quarter_labels(start_period, start_period)
        clauses.append(Quota.period.in_(quarter_labels))
    elif end_period:
        quarter_labels = _overlapping_quarter_labels(end_period, end_period)
        clauses.append(Quota.period.in_(quarter_labels))

    if clauses:
        q = q.where(and_(*clauses))

    value = (await db.execute(q)).scalar() or 0
    return {"value": float(value), "warnings": warnings, "sources": ["quotas"]}


async def get_quota_attainment(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    revenue = await get_total_revenue(db, filters)
    quota = await get_total_quota(db, filters)
    warnings = [*revenue["warnings"], *quota["warnings"]]

    if quota["value"] <= 0:
        warnings.append("No quota available for selected filters")
        return {"value": 0.0, "warnings": warnings, "sources": ["revenue", "quotas"]}

    return {
        "value": round((revenue["value"] / quota["value"]) * 100, 2),
        "warnings": warnings,
        "sources": ["revenue", "quotas"],
    }


async def get_win_rate(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    # Use actual_close_date for closed-deal period semantics
    base_clauses = _closed_deal_filters(filters, warnings)

    won_q = select(func.count(Deal.id)).where(Deal.stage == "Closed Won")
    lost_q = select(func.count(Deal.id)).where(Deal.stage == "Closed Lost")

    if filters.get("region") or filters.get("team_id"):
        won_q = won_q.join(Rep, Deal.rep_id == Rep.id, isouter=True)
        lost_q = lost_q.join(Rep, Deal.rep_id == Rep.id, isouter=True)
        won_q = won_q.where(and_(*_rep_filters(filters, warnings)))
        lost_q = lost_q.where(and_(*_rep_filters(filters, warnings)))

    if base_clauses:
        won_q = won_q.where(and_(*base_clauses))
        lost_q = lost_q.where(and_(*base_clauses))

    won = (await db.execute(won_q)).scalar() or 0
    lost = (await db.execute(lost_q)).scalar() or 0
    total = won + lost
    value = round((won / total) * 100, 2) if total else 0.0
    if total == 0:
        warnings.append("No closed deals found for win-rate calculation")

    return {
        "value": value,
        "won": int(won),
        "lost": int(lost),
        "warnings": warnings,
        "sources": ["deals"],
    }


async def get_open_pipeline(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    Open pipeline value.

    Period semantics (Phase 7): when start_date/end_date are provided,
    pipeline = deals expected to close within the selected period
    (using Deal.expected_close_date), regardless of current stage.
    Without period filters, returns all open (non-closed) deals.
    """
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    start_date = filters.get("start_date")
    end_date = filters.get("end_date")

    if start_date or end_date:
        # Period-scoped: open deals whose expected_close_date falls in window
        q = select(func.sum(Deal.amount)).where(~Deal.stage.in_(CLOSED_STAGES))
        if filters.get("rep_id"):
            q = q.where(Deal.rep_id == filters["rep_id"])
        if filters.get("product"):
            q = q.where(Deal.product == filters["product"])
        if filters.get("stage"):
            q = q.where(Deal.stage == filters["stage"])
        if start_date:
            q = q.where(Deal.expected_close_date >= _to_date(start_date))
        if end_date:
            q = q.where(Deal.expected_close_date <= _to_date(end_date))
        warnings.append(
            "Pipeline scoped to deals with expected_close_date in the selected period."
        )
    else:
        # No period: all open deals
        q = select(func.sum(Deal.amount)).where(~Deal.stage.in_(CLOSED_STAGES))
        clauses = _deal_filters(filters, warnings)
        if clauses:
            q = q.where(and_(*clauses))

    if filters.get("region") or filters.get("team_id"):
        q = q.join(Rep, Deal.rep_id == Rep.id, isouter=True).where(and_(*_rep_filters(filters, warnings)))

    value = (await db.execute(q)).scalar() or 0
    return {"value": float(value), "warnings": warnings, "sources": ["deals"]}


async def get_pipeline_coverage(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    pipeline = await get_open_pipeline(db, filters)
    quota = await get_total_quota(db, filters)
    warnings = [*pipeline["warnings"], *quota["warnings"]]

    quota_period_sample_q = select(Quota.period).limit(1)
    rep_clauses = _rep_filters(filters, warnings=[])
    if rep_clauses:
        quota_period_sample_q = quota_period_sample_q.join(Rep, Quota.rep_id == Rep.id, isouter=True).where(and_(*rep_clauses))
    sample_period = (await db.execute(quota_period_sample_q)).scalar()

    quota_grain = "unknown"
    if isinstance(sample_period, str):
        if re.match(r"^\d{4}-\d{2}$", sample_period):
            quota_grain = "monthly"
        elif re.match(r"^\d{4}-Q[1-4]$", sample_period):
            quota_grain = "quarterly"
        elif re.match(r"^\d{4}$", sample_period):
            quota_grain = "annual"

    if quota_grain == "unknown":
        warnings.append("Pipeline coverage depends on quota grain: monthly, quarterly, or annual")

    if quota["value"] <= 0:
        warnings.append("Quota is zero or missing; pipeline coverage set to 0")
        return {"value": 0.0, "warnings": warnings, "sources": ["deals", "quotas"]}

    return {
        "value": round(pipeline["value"] / quota["value"], 4),
        "warnings": warnings,
        "sources": ["deals", "quotas"],
    }


async def get_average_deal_size(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    # Use actual_close_date for closed-deal period semantics
    q = select(func.avg(Deal.amount)).where(Deal.stage == "Closed Won")
    clauses = _closed_deal_filters(filters, warnings)
    if filters.get("region") or filters.get("team_id"):
        q = q.join(Rep, Deal.rep_id == Rep.id, isouter=True).where(and_(*_rep_filters(filters, warnings)))
    if clauses:
        q = q.where(and_(*clauses))

    value = (await db.execute(q)).scalar() or 0
    if value == 0:
        warnings.append("No closed won deals found for average deal size")
    return {"value": float(value), "warnings": warnings, "sources": ["deals"]}


async def get_revenue_by_region(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    start_period, end_period = _period_bounds(filters)

    q = (
        select(Rep.region, func.sum(Revenue.amount).label("revenue"))
        .select_from(Revenue)
        .join(Rep, Revenue.rep_id == Rep.id, isouter=True)
        .group_by(Rep.region)
    )
    clauses = _rep_filters(filters, warnings)
    if start_period:
        clauses.append(Revenue.period >= start_period)
    if end_period:
        clauses.append(Revenue.period <= end_period)
    if clauses:
        q = q.where(and_(*clauses))

    rows = (await db.execute(q)).all()
    data = [{"region": row.region or "Unknown", "revenue": float(row.revenue or 0)} for row in rows]
    if not data:
        warnings.append("No revenue rows found for selected filters")
    return {"data": data, "warnings": warnings, "sources": ["revenue", "reps"]}


async def get_rep_performance(
    db: AsyncSession,
    rep_id: Optional[str] = None,
    rep_name: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    rep_q = select(Rep)
    if rep_id:
        rep_q = rep_q.where(Rep.id == rep_id)
    if rep_name:
        rep_q = rep_q.where(func.lower(Rep.name) == rep_name.lower())
    rep = (await db.execute(rep_q)).scalars().first()
    if not rep:
        return {"data": None, "warnings": ["Rep not found"], "sources": ["reps"]}

    scoped = {**filters, "rep_id": rep.id}
    revenue = await get_total_revenue(db, scoped)
    quota = await get_total_quota(db, scoped)
    win_rate = await get_win_rate(db, scoped)
    pipeline = await get_open_pipeline(db, scoped)
    avg_deal = await get_average_deal_size(db, scoped)

    attainment = 0.0
    if quota["value"] > 0:
        attainment = round((revenue["value"] / quota["value"]) * 100, 2)
    else:
        warnings.append("Quota missing for rep")

    data = {
        "rep_id": str(rep.id),
        "name": rep.name,
        "region": rep.region,
        "revenue": revenue["value"],
        "quota": quota["value"],
        "attainment_pct": attainment,
        "win_rate": win_rate["value"],
        "deals_won": win_rate.get("won", 0),
        "deals_lost": win_rate.get("lost", 0),
        "open_pipeline": pipeline["value"],
        "average_deal_size": avg_deal["value"],
    }
    warnings.extend(revenue["warnings"] + quota["warnings"] + win_rate["warnings"] + pipeline["warnings"] + avg_deal["warnings"])
    return {"data": data, "warnings": warnings, "sources": ["revenue", "quotas", "deals", "reps"]}


async def get_top_reps(db: AsyncSession, limit: int = 5, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    reps = (await db.execute(select(Rep))).scalars().all()

    rows = []
    for rep in reps:
        perf = await get_rep_performance(db, rep_id=str(rep.id), filters=filters)
        if perf["data"]:
            rows.append(perf["data"])
        warnings.extend(perf["warnings"])

    rows.sort(key=lambda x: x["attainment_pct"], reverse=True)
    return {"data": rows[:limit], "warnings": warnings, "sources": ["reps", "revenue", "quotas", "deals"]}


async def get_underperforming_reps(
    db: AsyncSession,
    threshold_pct: float = 75,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    top = await get_top_reps(db, limit=1000, filters=filters)
    under = [row for row in top["data"] if row["attainment_pct"] < threshold_pct]
    warnings = list(top["warnings"])
    if not under:
        warnings.append("No underperforming reps found for selected filters")
    return {"data": under, "warnings": warnings, "sources": top["sources"]}


# ─────────────────────────────────────────────────────────────
# RevOps metrics
# ─────────────────────────────────────────────────────────────

async def get_nrr(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """
    Net Revenue Retention: (start + expansion - contraction - churn) / start × 100.

    Uses actual ``revenue_type`` values when present in the revenue table.
    Falls back to a heuristic approximation when revenue_type is not populated,
    labelling the result as a fallback estimate.

    revenue_type expected values:
      renewal | new_biz | expansion | contraction | churn
    """
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    start_period, end_period = _period_bounds(filters)

    # Try to pull revenue_type-classified rows
    q = select(Revenue.amount, Revenue.revenue_type).select_from(Revenue)
    if start_period:
        q = q.where(Revenue.period >= start_period)
    if end_period:
        q = q.where(Revenue.period <= end_period)

    rows = (await db.execute(q)).all()
    if not rows:
        warnings.append("No revenue rows found for NRR calculation")
        return {"value": 0.0, "nrr_pct": 0.0, "fallback_mode": True, "warnings": warnings, "sources": ["revenue"]}

    # Check how many rows have revenue_type populated
    typed_rows = [(float(r.amount or 0), r.revenue_type) for r in rows if r.revenue_type]
    fallback_mode = len(typed_rows) < len(rows) * 0.5  # if <50% typed, fall back

    if fallback_mode:
        total = sum(float(r.amount or 0) for r in rows)
        warnings.append(
            f"[FALLBACK] NRR approximated: only {len(typed_rows)}/{len(rows)} revenue rows have "
            "revenue_type populated. Use actual revenue_type for accurate NRR. "
            "Approximation: renewal=75%, expansion=15%, contraction=5%, churn=5%."
        )
        mrr_start = total * 0.75
        expansion = total * 0.15
        contraction = total * 0.05
        churn = total * 0.05
    else:
        # Use actual revenue_type breakdown
        mrr_start = sum(a for a, t in typed_rows if t in ("renewal", "new_biz")) or sum(float(r.amount or 0) for r in rows) * 0.75
        expansion = sum(a for a, t in typed_rows if t == "expansion")
        contraction = sum(abs(a) for a, t in typed_rows if t == "contraction")
        churn = sum(abs(a) for a, t in typed_rows if t == "churn")
        if len(typed_rows) < len(rows):
            warnings.append(
                f"[PARTIAL] {len(rows) - len(typed_rows)} revenue rows without revenue_type excluded from NRR."
            )

    nrr = ((mrr_start + expansion - contraction - churn) / mrr_start * 100) if mrr_start > 0 else 0.0
    return {
        "value": round(nrr, 2),
        "nrr_pct": round(nrr, 2),
        "fallback_mode": fallback_mode,
        "components": {
            "mrr_start": round(mrr_start, 2),
            "expansion": round(expansion, 2),
            "contraction": round(-contraction, 2),
            "churn": round(-churn, 2),
        },
        "warnings": warnings,
        "sources": ["revenue"],
    }


async def get_grr(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Gross Revenue Retention: (start - contraction - churn) / start × 100. Capped at 100%."""
    nrr_data = await get_nrr(db, filters)
    comps = nrr_data.get("components", {})
    mrr_start = comps.get("mrr_start", 0.0)
    contraction = abs(comps.get("contraction", 0.0))
    churn = abs(comps.get("churn", 0.0))
    grr = ((mrr_start - contraction - churn) / mrr_start * 100) if mrr_start > 0 else 0.0
    grr = min(grr, 100.0)
    return {
        "value": round(grr, 2),
        "grr_pct": round(grr, 2),
        "fallback_mode": nrr_data.get("fallback_mode", True),
        "warnings": nrr_data["warnings"],
        "sources": ["revenue"],
    }


async def get_arr_growth_rate(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Year-over-year ARR growth rate."""
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    # Aggregate monthly revenue → compute rolling 12-month ARR windows
    q = select(Revenue.period, func.sum(Revenue.amount).label("monthly_rev")).group_by(Revenue.period).order_by(Revenue.period)
    rows = (await db.execute(q)).all()
    if len(rows) < 13:
        warnings.append(f"Only {len(rows)} periods available; need ≥ 13 for YoY ARR growth")
        return {"value": 0.0, "arr_growth_pct": 0.0, "warnings": warnings, "sources": ["revenue"]}

    periods = [(r.period, float(r.monthly_rev or 0)) for r in rows]
    # Latest 12 months vs previous 12 months
    latest_12 = sum(v for _, v in periods[-12:])
    prev_12 = sum(v for _, v in periods[-24:-12]) if len(periods) >= 24 else sum(v for _, v in periods[:-12])
    if prev_12 <= 0:
        warnings.append("Previous 12-month ARR is zero; cannot compute growth rate")
        return {"value": 0.0, "arr_growth_pct": 0.0, "warnings": warnings, "sources": ["revenue"]}

    growth = (latest_12 - prev_12) / prev_12 * 100
    return {
        "value": round(growth, 2),
        "arr_growth_pct": round(growth, 2),
        "arr_current_12m": round(latest_12, 2),
        "arr_prior_12m": round(prev_12, 2),
        "warnings": warnings,
        "sources": ["revenue"],
    }


async def get_sales_cycle_days(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Average days from deal creation to Closed Won."""
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    q = select(Deal.created_at, Deal.actual_close_date).where(Deal.stage == "Closed Won").where(Deal.actual_close_date.isnot(None))
    clauses = _deal_filters(filters, warnings)
    if clauses:
        q = q.where(and_(*clauses))

    rows = (await db.execute(q)).all()
    if not rows:
        warnings.append("No Closed Won deals with actual_close_date found")
        return {"value": 0.0, "avg_days": 0.0, "warnings": warnings, "sources": ["deals"]}

    cycle_days = []
    for row in rows:
        created = row.created_at
        closed = row.actual_close_date
        if created and closed:
            # created_at is datetime, actual_close_date is date
            created_d = created.date() if hasattr(created, "date") else created
            days = (closed - created_d).days
            if 0 <= days <= 365:  # exclude outliers > 1 year
                cycle_days.append(days)

    if not cycle_days:
        warnings.append("No valid cycle-day pairs after filtering outliers")
        return {"value": 0.0, "avg_days": 0.0, "warnings": warnings, "sources": ["deals"]}

    avg = round(sum(cycle_days) / len(cycle_days), 1)
    return {
        "value": avg,
        "avg_days": avg,
        "sample_size": len(cycle_days),
        "warnings": warnings,
        "sources": ["deals"],
    }


async def get_activity_ratio(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Average activities per open deal."""
    from backend.models import Activity
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    OPEN_STAGES = ("Prospecting", "Qualification", "Proposal", "Negotiation")
    open_count_q = select(func.count(Deal.id)).where(Deal.stage.in_(OPEN_STAGES))
    open_count = (await db.execute(open_count_q)).scalar() or 0

    if open_count == 0:
        warnings.append("No open deals found for activity ratio")
        return {"value": 0.0, "ratio": 0.0, "open_deals": 0, "warnings": warnings, "sources": ["deals", "activities"]}

    activity_count_q = select(func.count(Activity.id)).join(Deal, Activity.deal_id == Deal.id).where(Deal.stage.in_(OPEN_STAGES))
    activity_count = (await db.execute(activity_count_q)).scalar() or 0

    ratio = round(activity_count / open_count, 2)
    return {
        "value": ratio,
        "ratio": ratio,
        "open_deals": open_count,
        "total_activities_on_open_deals": activity_count,
        "warnings": warnings,
        "sources": ["deals", "activities"],
    }


async def get_weighted_pipeline_coverage(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Weighted pipeline (stage probability weighted) vs quota remaining."""
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    OPEN_STAGES = ("Prospecting", "Qualification", "Proposal", "Negotiation")
    q = select(Deal.amount, Deal.close_probability).where(Deal.stage.in_(OPEN_STAGES))
    rows = (await db.execute(q)).all()

    weighted = sum(
        float(r.amount or 0) * (float(r.close_probability or 0) / 100)
        for r in rows
    )

    quota_result = await get_total_quota(db, filters)
    quota_val = quota_result["value"]
    warnings.extend(quota_result["warnings"])

    if quota_val <= 0:
        warnings.append("No quota found; weighted coverage ratio cannot be computed")
        return {"value": 0.0, "weighted_pipeline": round(weighted, 2), "quota": 0.0, "ratio": 0.0, "warnings": warnings, "sources": ["deals", "quotas"]}

    ratio = round(weighted / quota_val, 2)
    return {
        "value": ratio,
        "ratio": ratio,
        "weighted_pipeline": round(weighted, 2),
        "quota": round(quota_val, 2),
        "benchmark_3x": ratio >= 3.0,
        "warnings": warnings,
        "sources": ["deals", "quotas"],
    }


async def get_quota_attainment_distribution(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Distribution of reps across attainment tiers: <50, 50-75, 75-100, 100-120, >120."""
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    reps = (await db.execute(select(Rep))).scalars().all()
    if not reps:
        return {"data": {}, "warnings": ["No reps found"], "sources": ["reps"]}

    tiers: dict[str, int] = {"below_50": 0, "50_to_75": 0, "75_to_100": 0, "100_to_120": 0, "above_120": 0}
    for rep in reps:
        scoped = {**filters, "rep_id": rep.id}
        rev = await get_total_revenue(db, scoped)
        quota = await get_total_quota(db, scoped)
        if quota["value"] <= 0:
            continue
        att = (rev["value"] / quota["value"]) * 100
        if att < 50:
            tiers["below_50"] += 1
        elif att < 75:
            tiers["50_to_75"] += 1
        elif att < 100:
            tiers["75_to_100"] += 1
        elif att <= 120:
            tiers["100_to_120"] += 1
        else:
            tiers["above_120"] += 1

    total_reps = sum(tiers.values())
    tier_pct = {k: round(v / total_reps * 100, 1) if total_reps > 0 else 0 for k, v in tiers.items()}

    return {
        "data": {
            "counts": tiers,
            "percentages": tier_pct,
            "total_reps_with_quota": total_reps,
        },
        "warnings": warnings,
        "sources": ["reps", "revenue", "quotas"],
    }


# ── ARR Waterfall ─────────────────────────────────────────────────────────

async def calc_arr_waterfall(
    db: AsyncSession,
    period: str,
) -> dict[str, Any]:
    """Aggregate ARR waterfall components for a single period across all reps."""
    rows = (
        await db.execute(
            select(ArrWaterfallEntry).where(ArrWaterfallEntry.period == period)
        )
    ).scalars().all()

    if not rows:
        # Fallback: derive from bookings + churn_events if waterfall table is empty
        return await _derive_waterfall_for_period(db, period)

    agg = {
        "new_logo": sum(float(r.mrr_new or 0) for r in rows) * 12,
        "expansion": sum(float(r.mrr_expansion or 0) for r in rows) * 12,
        "contraction": sum(float(r.mrr_contraction or 0) for r in rows) * 12,
        "churn": sum(float(r.mrr_churn or 0) for r in rows) * 12,
        "renewal": sum(float(r.mrr_renewal or 0) for r in rows) * 12,
        "net_new_arr": sum(float(r.mrr_net or 0) for r in rows) * 12,
        "arr_start": sum(float(r.arr_start or 0) for r in rows),
        "arr_end": sum(float(r.arr_end or 0) for r in rows),
    }
    return {"period": period, **agg, "data_source": "arr_waterfall"}


async def _derive_waterfall_for_period(
    db: AsyncSession,
    period: str,
) -> dict[str, Any]:
    """Derive waterfall from bookings + churn_events when arr_waterfall table is empty.
    Falls back to revenue-derived ARR when bookings/churn tables are also empty.
    """
    new_logo = (
        await db.execute(
            select(func.sum(Booking.arr))
            .where(Booking.revenue_type == "new_logo")
            .where(func.to_char(Booking.booking_date, "YYYY-MM") == period)
        )
    ).scalar() or 0

    expansion = (
        await db.execute(
            select(func.sum(Booking.arr))
            .where(Booking.revenue_type == "expansion")
            .where(func.to_char(Booking.booking_date, "YYYY-MM") == period)
        )
    ).scalar() or 0

    churn = abs(
        (
            await db.execute(
                select(func.sum(ChurnEvent.arr_change))
                .where(ChurnEvent.event_type == "full_churn")
                .where(ChurnEvent.period == period)
            )
        ).scalar() or 0
    )

    contraction = abs(
        (
            await db.execute(
                select(func.sum(ChurnEvent.arr_change))
                .where(ChurnEvent.event_type == "partial_contraction")
                .where(ChurnEvent.period == period)
            )
        ).scalar() or 0
    )

    # Renewal: bookings with revenue_type = renewal
    renewal = (
        await db.execute(
            select(func.sum(Booking.arr))
            .where(Booking.revenue_type == "renewal")
            .where(func.to_char(Booking.booking_date, "YYYY-MM") == period)
        )
    ).scalar() or 0

    net_new_arr = float(new_logo) + float(expansion) + float(renewal) - float(churn) - float(contraction)

    # If all sources are empty, fall back to revenue-derived ARR approximation
    if new_logo == 0 and expansion == 0 and churn == 0 and contraction == 0 and renewal == 0:
        monthly_rev = (
            await db.execute(
                select(func.sum(Revenue.amount)).where(Revenue.period == period)
            )
        ).scalar() or 0
        arr_approx = float(monthly_rev) * 12
        # Prior period for ARR start
        try:
            yr, mo = period.split("-")
            prev_mo = int(mo) - 1
            prev_yr = int(yr)
            if prev_mo == 0:
                prev_mo = 12
                prev_yr -= 1
            prev_period = f"{prev_yr}-{prev_mo:02d}"
            prior_rev = (
                await db.execute(
                    select(func.sum(Revenue.amount)).where(Revenue.period == prev_period)
                )
            ).scalar() or 0
            arr_start = float(prior_rev) * 12
        except Exception:
            arr_start = arr_approx
        return {
            "period": period,
            "new_logo": arr_approx,   # treat all as new_logo approximation
            "expansion": 0.0,
            "contraction": 0.0,
            "churn": 0.0,
            "renewal": 0.0,
            "net_new_arr": arr_approx - arr_start,
            "arr_start": arr_start,
            "arr_end": arr_approx,
            "data_source": "derived_from_revenue_approx",
        }

    return {
        "period": period,
        "new_logo": float(new_logo),
        "expansion": float(expansion),
        "contraction": float(contraction),
        "churn": float(churn),
        "renewal": float(renewal),
        "net_new_arr": net_new_arr,
        "arr_start": 0.0,  # not computable without prior period
        "arr_end": 0.0,
        "data_source": "derived_from_bookings_churn",
    }


async def calc_arr_waterfall_series(
    db: AsyncSession,
    months: int = 12,
) -> list[dict[str, Any]]:
    """Return waterfall data for the last N months, ascending by period."""
    # Get available periods from arr_waterfall table
    periods_in_db = (
        await db.execute(
            select(ArrWaterfallEntry.period)
            .distinct()
            .order_by(ArrWaterfallEntry.period.desc())
            .limit(months)
        )
    ).scalars().all()

    # If arr_waterfall table has no data, fall back to computing periods from Revenue
    if not periods_in_db:
        periods_in_db = (
            await db.execute(
                select(Revenue.period)
                .distinct()
                .order_by(Revenue.period.desc())
                .limit(months)
            )
        ).scalars().all()

    periods_in_db = sorted(set(periods_in_db))[-months:]

    results = []
    for period in periods_in_db:
        entry = await calc_arr_waterfall(db, period)
        results.append(entry)

    return results


# ── Deal Velocity ─────────────────────────────────────────────────────────

async def calc_deal_velocity(
    db: AsyncSession,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Deal velocity = pipeline_value × win_rate × avg_deal_size⁻¹ / avg_cycle_days.
    Also returns breakdown by stage and per-rep averages.
    """
    filters = _normalize_filters(filters)
    warnings: list[str] = []

    clauses: list[Any] = []
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    if start_date:
        clauses.append(cast(Deal.created_at, Date) >= _to_date(start_date))
    if end_date:
        clauses.append(cast(Deal.created_at, Date) <= _to_date(end_date))
    if filters.get("rep_id"):
        clauses.append(Deal.rep_id == filters["rep_id"])

    # Closed-won deals for cycle time
    won_rows = (
        await db.execute(
            select(
                Deal.stage,
                Deal.amount,
                Deal.created_at,
                Deal.actual_close_date,
            )
            .where(Deal.stage == "Closed Won")
            .where(Deal.actual_close_date.isnot(None))
            .where(*clauses) if clauses else
            select(
                Deal.stage,
                Deal.amount,
                Deal.created_at,
                Deal.actual_close_date,
            )
            .where(Deal.stage == "Closed Won")
            .where(Deal.actual_close_date.isnot(None))
        )
    ).all()

    # All closed deals for win rate
    all_closed = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
        )
    ).scalar() or 0
    won_count = len(won_rows)
    win_rate = (won_count / all_closed) if all_closed > 0 else 0.0

    if not won_rows:
        return {
            "deal_velocity": 0.0,
            "avg_cycle_days": None,
            "avg_deal_size": 0.0,
            "win_rate": win_rate,
            "total_won": 0,
            "total_closed": int(all_closed),
            "by_stage": [],
            "warnings": ["No closed-won deals with close dates found"],
        }

    cycle_days = []
    amounts = []
    for r in won_rows:
        if r.created_at and r.actual_close_date:
            days = (r.actual_close_date - r.created_at.date()).days
            if days >= 0:
                cycle_days.append(days)
        amounts.append(float(r.amount or 0))

    avg_cycle = sum(cycle_days) / len(cycle_days) if cycle_days else None
    avg_deal = sum(amounts) / len(amounts) if amounts else 0.0

    # Pipeline value (open deals)
    pipeline_val = (
        await db.execute(
            select(func.sum(Deal.amount))
            .where(~Deal.stage.in_(["Closed Won", "Closed Lost"]))
        )
    ).scalar() or 0

    # Velocity = (pipeline × win_rate) / avg_cycle_days
    velocity = (
        (float(pipeline_val) * win_rate) / avg_cycle
        if avg_cycle and avg_cycle > 0 else 0.0
    )

    # By-stage breakdown (pipeline only)
    stage_rows = (
        await db.execute(
            select(Deal.stage, func.count(Deal.id).label("n"), func.sum(Deal.amount).label("val"))
            .where(~Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .group_by(Deal.stage)
        )
    ).all()
    by_stage = [
        {"stage": r.stage, "count": r.n, "total_value": float(r.val or 0)}
        for r in stage_rows
    ]

    return {
        "deal_velocity": round(velocity, 2),
        "avg_cycle_days": round(avg_cycle, 1) if avg_cycle else None,
        "avg_deal_size": round(avg_deal, 2),
        "win_rate": round(win_rate * 100, 2),
        "total_won": won_count,
        "total_closed": int(all_closed),
        "pipeline_value": float(pipeline_val),
        "by_stage": by_stage,
        "warnings": warnings,
    }




async def get_pipeline_hygiene(
    db: AsyncSession,
    filters: Optional[dict[str, Any]] = None,
    stale_days: int = 30,
) -> dict[str, Any]:
    """
    Stage hygiene checks for open pipeline.

    Returns counts and deal IDs for:
    - missing_close_date: open deals with no expected_close_date
    - overdue: open deals whose expected_close_date is in the past
    - stale: open deals with no activity update in stale_days
    - high_prob_early_stage: deals in Prospecting/Qualification with prob >= 70
    """
    filters = _normalize_filters(filters)
    warnings: list[str] = []
    today = date.today()

    open_q = select(Deal).where(~Deal.stage.in_(CLOSED_STAGES))
    rep_clauses = []
    if filters.get("region") or filters.get("team_id") or filters.get("rep_id"):
        rep_clauses = _rep_filters(filters, warnings)
    if rep_clauses:
        open_q = open_q.join(Rep, Deal.rep_id == Rep.id, isouter=True).where(and_(*rep_clauses))

    open_deals = (await db.execute(open_q)).scalars().all()

    missing_close_date = []
    overdue = []
    high_prob_early = []
    stale_threshold = today - timedelta(days=stale_days)

    for deal in open_deals:
        if deal.expected_close_date is None:
            missing_close_date.append(str(deal.id))
        elif deal.expected_close_date < today:
            overdue.append(str(deal.id))

        if deal.stage in ("Prospecting", "Qualification") and (deal.close_probability or 0) >= 70:
            high_prob_early.append({
                "deal_id": str(deal.id),
                "stage": deal.stage,
                "close_probability": deal.close_probability,
            })

    return {
        "total_open_deals": len(open_deals),
        "missing_close_date_count": len(missing_close_date),
        "missing_close_date_ids": missing_close_date[:20],
        "overdue_count": len(overdue),
        "overdue_ids": overdue[:20],
        "high_prob_early_stage_count": len(high_prob_early),
        "high_prob_early_stage": high_prob_early[:20],
        "stale_threshold_days": stale_days,
        "warnings": warnings,
        "sources": ["deals"],
    }
