"""
Governed metrics service backed by SQLAlchemy async queries.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.metrics.registry import get_global_registry
from backend.metrics import calculators


class MetricsService:
    def __init__(self) -> None:
        self.registry = get_global_registry()

    def get_metric_info(self, name: str) -> dict[str, Any]:
        metric = self.registry.get_required(name)
        return {
            "name": metric.name,
            "display_name": metric.display_name,
            "description": metric.description,
            "formula": metric.formula,
            "required_fields": metric.required_fields,
            "grain": metric.grain,
            "owner": metric.owner,
            "caveats": metric.caveats or [],
        }

    def list_metrics(self) -> list[dict[str, Any]]:
        return [self.get_metric_info(m.name) for m in self.registry.list_all()]

    async def get_metric_value(
        self,
        db: AsyncSession,
        name: str,
        filters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self.registry.get_required(name)

        handlers = {
            "total_revenue": calculators.get_total_revenue,
            "total_quota": calculators.get_total_quota,
            "quota_attainment": calculators.get_quota_attainment,
            "win_rate": calculators.get_win_rate,
            "pipeline_coverage": calculators.get_pipeline_coverage,
            "average_deal_size": calculators.get_average_deal_size,
            "open_pipeline": calculators.get_open_pipeline,
            "forecasted_revenue": calculators.get_total_revenue,
            "rep_risk_score": calculators.get_underperforming_reps,
            "sales_cycle_length": calculators.get_average_deal_size,
        }

        handler = handlers.get(name)
        if not handler:
            raise ValueError(f"No calculator implemented for metric '{name}'")

        result = await handler(db, filters=filters)
        return {
            "metric": self.get_metric_info(name),
            "result": result,
        }

    async def get_kpis(self, db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        total_revenue = await calculators.get_total_revenue(db, filters)
        total_quota = await calculators.get_total_quota(db, filters)
        quota_attainment = await calculators.get_quota_attainment(db, filters)
        win_rate = await calculators.get_win_rate(db, filters)
        open_pipeline = await calculators.get_open_pipeline(db, filters)
        pipeline_coverage = await calculators.get_pipeline_coverage(db, filters)

        warnings = (
            total_revenue["warnings"]
            + total_quota["warnings"]
            + quota_attainment["warnings"]
            + win_rate["warnings"]
            + open_pipeline["warnings"]
            + pipeline_coverage["warnings"]
        )

        return {
            "total_revenue": total_revenue["value"],
            "total_quota": total_quota["value"],
            "attainment_pct": quota_attainment["value"],
            "open_pipeline": open_pipeline["value"],
            "win_rate": win_rate["value"],
            "deals_won": win_rate.get("won", 0),
            "deals_lost": win_rate.get("lost", 0),
            "pipeline_coverage": pipeline_coverage["value"],
            "warnings": warnings,
            "sources": ["revenue", "quotas", "deals"],
        }


_global_metrics_service = MetricsService()


def get_metrics_service() -> MetricsService:
    return _global_metrics_service
