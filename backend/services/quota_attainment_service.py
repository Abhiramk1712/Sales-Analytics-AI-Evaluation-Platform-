"""
backend/services/quota_attainment_service.py
============================================
Canonical quota and attainment service.

This service intentionally handles the revenue/quota grain mismatch:
- Revenue is monthly (Revenue.period = YYYY-MM)
- Quota may be monthly, quarterly, or annual (Quota.period)

Returned values include warnings and calculation trace details so downstream
APIs/reports can remain auditable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Quota, Revenue
from backend.utils.identity_mapping import get_rep_for_user


# ── Period helpers ─────────────────────────────────────────────────────────


def normalize_period(period: Optional[str]) -> Optional[str]:
    """
    Normalize to one of:
      YYYY-MM, YYYY-QN, YYYY

    Supports:
      YYYY-MM, YYYY, YYYY-QN, Q2 2024, 2024 Q2,
      this month, last month, this quarter, last quarter,
      this year, last year, YTD.
    """
    if not period:
        return None

    p = str(period).strip()
    lower = p.lower()
    today = date.today()

    if lower == "this month":
        return today.strftime("%Y-%m")
    if lower == "last month":
        year = today.year if today.month > 1 else today.year - 1
        month = today.month - 1 if today.month > 1 else 12
        return f"{year:04d}-{month:02d}"
    if lower == "this quarter":
        q = (today.month - 1) // 3 + 1
        return f"{today.year:04d}-Q{q}"
    if lower == "last quarter":
        q = (today.month - 1) // 3 + 1
        if q == 1:
            return f"{today.year - 1:04d}-Q4"
        return f"{today.year:04d}-Q{q - 1}"
    if lower in ("this year", "ytd", "year to date"):
        return f"{today.year:04d}"
    if lower == "last year":
        return f"{today.year - 1:04d}"

    # Already canonical
    if re.match(r"^\d{4}-\d{2}$", p):
        return p
    m = re.match(r"^(\d{4})/(\d{2})$", p)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if re.match(r"^\d{4}-Q[1-4]$", p):
        return p
    if re.match(r"^\d{4}$", p):
        return p

    # Natural quarter forms: Q2 2024 / 2024 Q2
    m = re.match(r"^Q([1-4])\s+(\d{4})$", p, re.IGNORECASE)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.match(r"^(\d{4})\s+Q([1-4])$", p, re.IGNORECASE)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"

    raise ValueError(
        f"Unrecognised period format: '{period}'. "
        "Use YYYY-MM, YYYY-Q1..Q4, YYYY, or natural forms like 'this quarter'."
    )


def period_grain(period: str) -> str:
    p = normalize_period(period) or period
    if re.match(r"^\d{4}-\d{2}$", p):
        return "monthly"
    if re.match(r"^\d{4}-Q[1-4]$", p):
        return "quarterly"
    if re.match(r"^\d{4}$", p):
        return "annual"
    return "unknown"


def _quarter_for_month(month_period: str) -> str:
    year = int(month_period[:4])
    month = int(month_period[5:7])
    quarter = (month - 1) // 3 + 1
    return f"{year:04d}-Q{quarter}"


def period_to_months(period: str) -> List[str]:
    """Expand normalized period to YYYY-MM month keys used by Revenue.period."""
    if not period:
        return []

    raw = str(period).strip().lower()
    today = date.today()
    if raw in ("ytd", "year to date"):
        return [f"{today.year:04d}-{m:02d}" for m in range(1, today.month + 1)]

    p = normalize_period(period) or period
    if re.match(r"^\d{4}-\d{2}$", p):
        return [p]
    if re.match(r"^\d{4}-Q[1-4]$", p):
        year = int(p[:4])
        q = int(p[6])
        start = (q - 1) * 3 + 1
        return [f"{year:04d}-{m:02d}" for m in range(start, start + 3)]
    if re.match(r"^\d{4}$", p):
        year = int(p)
        return [f"{year:04d}-{m:02d}" for m in range(1, 13)]
    return []


def quota_period_matches(quota_period: str, target_period: str) -> bool:
    """Return True if quota period overlaps the target period months."""
    try:
        q_months = set(period_to_months(quota_period))
        t_months = set(period_to_months(target_period))
    except ValueError:
        return False
    return bool(q_months & t_months)


# ── Data structures ────────────────────────────────────────────────────────


@dataclass
class AttainmentResult:
    period: str
    period_grain: str
    rep_id: Optional[Any] = None
    team_id: Optional[Any] = None
    revenue: float = 0.0
    quota: float = 0.0
    attainment_pct: float = 0.0
    quota_source: str = "direct"
    fallback_mode: bool = False
    warnings: List[str] = field(default_factory=list)
    calculation_trace: Dict[str, Any] = field(default_factory=dict)


# ── Query helpers ──────────────────────────────────────────────────────────


async def _sum_quota(
    db: AsyncSession,
    periods: List[str],
    rep_id: Optional[Any] = None,
) -> float:
    if not periods:
        return 0.0
    stmt = select(func.coalesce(func.sum(Quota.amount), 0.0)).where(Quota.period.in_(periods))
    if rep_id is not None:
        stmt = stmt.where(Quota.rep_id == rep_id)
    return float((await db.execute(stmt)).scalar() or 0.0)


# ── Public API ─────────────────────────────────────────────────────────────


async def get_quota_for_period(
    db: AsyncSession,
    period: str,
    rep_id: Optional[Any] = None,
    user_id: Optional[Any] = None,
) -> Tuple[float, str, List[str]]:
    """
    Resolve quota with explicit source/fallback semantics.

    Returns: (quota, quota_source, warnings)
    quota_source values:
      direct
      allocated_from_quarterly
      allocated_from_annual
      rolled_up_from_monthly
      rolled_up_from_quarterly
      rolled_up_from_annual
      none
    """
    warnings: List[str] = []
    norm = normalize_period(period)
    if not norm:
        return 0.0, "none", ["period is None or empty"]

    # Optional UserProfile -> Rep mapping by email.
    if rep_id is None and user_id is not None:
        user_rep = await get_rep_for_user(db, user_id)
        if user_rep:
            rep_id = user_rep.id
        else:
            warnings.append(f"No rep mapping found for user_id={user_id}")

    grain = period_grain(norm)

    if grain == "monthly":
        direct = await _sum_quota(db, [norm], rep_id=rep_id)
        if direct > 0:
            return direct, "direct", warnings

        q_label = _quarter_for_month(norm)
        quarterly = await _sum_quota(db, [q_label], rep_id=rep_id)
        if quarterly > 0:
            warnings.append(f"No monthly quota for {norm}; allocated {q_label} quota / 3")
            return round(quarterly / 3.0, 2), "allocated_from_quarterly", warnings

        annual = await _sum_quota(db, [norm[:4]], rep_id=rep_id)
        if annual > 0:
            warnings.append(f"No monthly/quarterly quota for {norm}; allocated annual quota / 12")
            return round(annual / 12.0, 2), "allocated_from_annual", warnings

        warnings.append(f"No quota data found for period={norm}, rep_id={rep_id}")
        return 0.0, "none", warnings

    if grain == "quarterly":
        direct = await _sum_quota(db, [norm], rep_id=rep_id)
        if direct > 0:
            return direct, "direct", warnings

        months = period_to_months(norm)
        monthly = await _sum_quota(db, months, rep_id=rep_id)
        if monthly > 0:
            warnings.append(f"No quarterly quota for {norm}; rolled up from monthly quotas")
            return monthly, "rolled_up_from_monthly", warnings

        annual = await _sum_quota(db, [norm[:4]], rep_id=rep_id)
        if annual > 0:
            warnings.append(f"No quarterly/monthly quota for {norm}; allocated annual quota / 4")
            return round(annual / 4.0, 2), "allocated_from_annual", warnings

        warnings.append(f"No quota data found for period={norm}, rep_id={rep_id}")
        return 0.0, "none", warnings

    if grain == "annual":
        direct = await _sum_quota(db, [norm], rep_id=rep_id)
        if direct > 0:
            return direct, "direct", warnings

        q_labels = [f"{norm}-Q{q}" for q in range(1, 5)]
        quarterly = await _sum_quota(db, q_labels, rep_id=rep_id)
        if quarterly > 0:
            warnings.append(f"No annual quota for {norm}; rolled up from quarterly quotas")
            return quarterly, "rolled_up_from_quarterly", warnings

        months = period_to_months(norm)
        monthly = await _sum_quota(db, months, rep_id=rep_id)
        if monthly > 0:
            warnings.append(f"No annual/quarterly quota for {norm}; rolled up from monthly quotas")
            return monthly, "rolled_up_from_monthly", warnings

        warnings.append(f"No quota data found for period={norm}, rep_id={rep_id}")
        return 0.0, "none", warnings

    warnings.append(f"Unsupported period grain for quota resolution: {norm}")
    return 0.0, "none", warnings


async def get_revenue_for_period(
    db: AsyncSession,
    period: str,
    rep_id: Optional[Any] = None,
) -> Tuple[float, List[str]]:
    """Sum revenue using Revenue.period month keys."""
    warnings: List[str] = []
    months = period_to_months(period)
    if not months:
        return 0.0, [f"Could not expand period '{period}' to month keys"]

    stmt = select(func.coalesce(func.sum(Revenue.amount), 0.0)).where(Revenue.period.in_(months))
    if rep_id is not None:
        stmt = stmt.where(Revenue.rep_id == rep_id)

    total = float((await db.execute(stmt)).scalar() or 0.0)
    return total, warnings


def calculate_attainment(revenue: float, quota: float) -> float:
    if quota <= 0:
        return 0.0
    return round((revenue / quota) * 100.0, 2)


async def get_rep_attainment(
    db: AsyncSession,
    rep_id: Any,
    period: str,
) -> AttainmentResult:
    norm = normalize_period(period) or period
    grain = period_grain(norm)

    revenue, revenue_warnings = await get_revenue_for_period(db, norm, rep_id=rep_id)
    quota, quota_source, quota_warnings = await get_quota_for_period(db, norm, rep_id=rep_id)
    attainment_pct = calculate_attainment(revenue, quota)

    return AttainmentResult(
        period=norm,
        period_grain=grain,
        rep_id=rep_id,
        revenue=revenue,
        quota=quota,
        attainment_pct=attainment_pct,
        quota_source=quota_source,
        fallback_mode=(quota_source != "direct"),
        warnings=[*revenue_warnings, *quota_warnings],
        calculation_trace={
            "period_input": period,
            "period_normalized": norm,
            "revenue_month_keys": period_to_months(norm),
            "revenue_formula": "SUM(revenue.amount) WHERE revenue.period IN month_keys",
            "quota_resolution": quota_source,
            "attainment_formula": f"({revenue} / {quota}) * 100",
        },
    )


async def get_company_attainment(
    db: AsyncSession,
    period: str,
) -> AttainmentResult:
    norm = normalize_period(period) or period
    grain = period_grain(norm)

    revenue, revenue_warnings = await get_revenue_for_period(db, norm)
    quota, quota_source, quota_warnings = await get_quota_for_period(db, norm)
    attainment_pct = calculate_attainment(revenue, quota)

    return AttainmentResult(
        period=norm,
        period_grain=grain,
        revenue=revenue,
        quota=quota,
        attainment_pct=attainment_pct,
        quota_source=quota_source,
        fallback_mode=(quota_source != "direct"),
        warnings=[*revenue_warnings, *quota_warnings],
        calculation_trace={
            "period_input": period,
            "period_normalized": norm,
            "revenue_month_keys": period_to_months(norm),
            "revenue_formula": "SUM(revenue.amount) WHERE revenue.period IN month_keys",
            "quota_resolution": quota_source,
            "attainment_formula": f"({revenue} / {quota}) * 100",
        },
    )
