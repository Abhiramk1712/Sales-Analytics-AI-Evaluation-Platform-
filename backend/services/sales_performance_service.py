"""
backend/services/sales_performance_service.py
=============================================
Single source of truth for all sales performance metrics.

Every endpoint, report, and agent tool that needs core KPIs should call this
service so numbers are consistent across the whole platform.

Each metric result carries:
  value, formula, period, filters, source_tables, data_source,
  confidence, fallback_mode, warnings, generated_at
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.metrics import calculators as calc
from backend.services.quota_attainment_service import (
    get_company_attainment,
    get_revenue_for_period,
    get_quota_for_period,
    normalize_period,
    period_grain,
)
from backend.utils.date_ranges import parse_period_to_range


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _metric(
    name: str,
    value: Any,
    formula: str,
    period: Optional[str],
    source_tables: List[str],
    *,
    confidence: float = 1.0,
    fallback_mode: bool = False,
    warnings: Optional[List[str]] = None,
    filters: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "metric_name": name,
        "value": value,
        "formula": formula,
        "period": period,
        "filters": filters or {},
        "source_tables": source_tables,
        "data_source": "database",
        "confidence": confidence,
        "fallback_mode": fallback_mode,
        "warnings": warnings or [],
        "generated_at": _now_iso(),
    }


class SalesPerformanceService:
    """
    Unified sales performance service.

    Usage:
        svc = SalesPerformanceService(db)
        result = await svc.get_full_summary(period="2025-Q2")
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_summary(
        self,
        period: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Return the key summary KPIs for a period.
        Used by the Dashboard and Agent.
        """
        norm_period = normalize_period(period) if period else None
        calc_filters = {}
        if norm_period:
            pr = parse_period_to_range(norm_period)
            if pr:
                calc_filters = {"start_date": pr.start_date, "end_date": pr.end_date}
        if filters:
            calc_filters.update(filters)

        all_warnings: List[str] = []

        # Revenue
        rev_result = await calc.get_total_revenue(self.db, calc_filters)
        revenue = rev_result.get("value", 0.0)

        # Quota + attainment (use canonical service for period-aware resolution)
        if norm_period:
            att = await get_company_attainment(self.db, norm_period)
            quota = att.quota
            attainment_pct = att.attainment_pct
            quota_source = att.quota_source
            all_warnings.extend(att.warnings)
        else:
            quota_result = await calc.get_total_quota(self.db, calc_filters)
            quota = quota_result.get("value", 0.0)
            att_result = await calc.get_quota_attainment(self.db, calc_filters)
            attainment_pct = att_result.get("value", 0.0)
            quota_source = "direct"

        # Pipeline
        pipeline_result = await calc.get_open_pipeline(self.db, calc_filters)
        pipeline = pipeline_result.get("value", 0.0)

        # Win rate
        win_result = await calc.get_win_rate(self.db, calc_filters)
        win_rate = win_result.get("value", 0.0)

        # Avg deal size
        ads_result = await calc.get_average_deal_size(self.db, calc_filters)
        avg_deal_size = ads_result.get("value", 0.0)

        # Pipeline coverage
        cov_result = await calc.get_pipeline_coverage(self.db, calc_filters)
        pipeline_coverage = cov_result.get("value", 0.0)

        # NRR / GRR
        nrr_result = await calc.get_nrr(self.db, calc_filters)
        grr_result = await calc.get_grr(self.db, calc_filters)
        nrr = nrr_result.get("value", 0.0)
        grr = grr_result.get("value", 0.0)
        if nrr_result.get("fallback_mode"):
            all_warnings.append("NRR calculated in fallback mode — revenue_type field may be missing")
        if grr_result.get("fallback_mode"):
            all_warnings.append("GRR calculated in fallback mode")

        return {
            "period": norm_period,
            "period_grain": period_grain(norm_period) if norm_period else None,
            "metrics": {
                "total_revenue": _metric(
                    "total_revenue", revenue,
                    "SUM(revenue.amount) WHERE date IN period",
                    norm_period, ["revenue"], warnings=rev_result.get("warnings", [])
                ),
                "quota": _metric(
                    "quota", quota,
                    "SUM(quotas.amount) WHERE period MATCHES target",
                    norm_period, ["quotas"],
                    fallback_mode=(quota_source != "direct"),
                    warnings=[f"quota_source={quota_source}"] if quota_source != "direct" else []
                ),
                "attainment_pct": _metric(
                    "quota_attainment", attainment_pct,
                    "revenue / quota * 100",
                    norm_period, ["revenue", "quotas"],
                    fallback_mode=(quota_source != "direct"),
                ),
                "open_pipeline": _metric(
                    "open_pipeline", pipeline,
                    "SUM(deals.amount) WHERE stage NOT IN closed_stages",
                    norm_period, ["deals"]
                ),
                "pipeline_coverage": _metric(
                    "pipeline_coverage", pipeline_coverage,
                    "open_pipeline / quota",
                    norm_period, ["deals", "quotas"]
                ),
                "win_rate": _metric(
                    "win_rate", win_rate,
                    "closed_won_count / (closed_won_count + closed_lost_count) * 100",
                    norm_period, ["deals"]
                ),
                "avg_deal_size": _metric(
                    "avg_deal_size", avg_deal_size,
                    "SUM(amount) / COUNT(*) WHERE stage = closed_won",
                    norm_period, ["deals"]
                ),
                "nrr": _metric(
                    "nrr", nrr,
                    "(beginning_arr + expansion - contraction - churn) / beginning_arr * 100",
                    norm_period, ["revenue", "monthly_finance"],
                    fallback_mode=nrr_result.get("fallback_mode", False),
                    warnings=nrr_result.get("warnings", [])
                ),
                "grr": _metric(
                    "grr", grr,
                    "(beginning_arr - contraction - churn) / beginning_arr * 100",
                    norm_period, ["revenue", "monthly_finance"],
                    fallback_mode=grr_result.get("fallback_mode", False),
                ),
            },
            "warnings": all_warnings,
            "generated_at": _now_iso(),
        }

    async def get_rep_summaries(
        self,
        period: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return per-rep performance for a period using the canonical calculator."""
        norm_period = normalize_period(period) if period else None
        calc_filters = {}
        if norm_period:
            pr = parse_period_to_range(norm_period)
            if pr:
                calc_filters = {"start_date": pr.start_date, "end_date": pr.end_date}
        if filters:
            calc_filters.update(filters)

        result = await calc.get_top_reps(self.db, limit=limit, filters=calc_filters)
        return result.get("reps", [])

    async def get_full_summary(
        self,
        period: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Return the unified company-level sales performance object.
        Used by /analytics/sales-performance endpoint.
        """
        summary = await self.get_summary(period=period, filters=filters)
        reps = await self.get_rep_summaries(period=period, filters=filters)

        norm_period = normalize_period(period) if period else None
        calc_filters = {}
        if norm_period:
            pr = parse_period_to_range(norm_period)
            if pr:
                calc_filters = {"start_date": pr.start_date, "end_date": pr.end_date}
        if filters:
            calc_filters.update(filters)

        top_reps_result = await calc.get_top_reps(self.db, limit=5, filters=calc_filters)
        under_result = await calc.get_underperforming_reps(self.db, filters=calc_filters)

        return {
            "period": norm_period,
            "summary": summary,
            "reps": reps,
            "top_reps": top_reps_result.get("reps", []),
            "underperforming_reps": under_result.get("reps", []),
            "warnings": summary.get("warnings", []),
            "audit_trail": {
                "generated_at": _now_iso(),
                "period": norm_period,
                "data_source": "database",
                "service": "SalesPerformanceService",
            },
        }
