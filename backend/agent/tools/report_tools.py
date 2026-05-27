"""
Agent report tools backed by report generator.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.reports.report_generator import ReportGenerator


def _result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


async def generate_executive_summary_text(
    db: AsyncSession,
    period: str,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report = await ReportGenerator.generate_report(db, "executive_weekly", period, "Executive", filters)
    return _result(
        "generate_executive_summary_text",
        "warning" if report["warnings"] else "success",
        {"markdown": report["markdown"], "metrics_used": report["metrics_used"]},
        report["warnings"],
        ["metrics_service", "reports"],
    )


async def generate_manager_summary_text(
    db: AsyncSession,
    period: str,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report = await ReportGenerator.generate_report(db, "manager_monthly", period, "Manager", filters)
    return _result(
        "generate_manager_summary_text",
        "warning" if report["warnings"] else "success",
        {"markdown": report["markdown"], "metrics_used": report["metrics_used"]},
        report["warnings"],
        ["metrics_service", "reports"],
    )


async def generate_rep_summary_text(
    db: AsyncSession,
    period: str,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    report = await ReportGenerator.generate_report(db, "rep_performance", period, "Rep", filters)
    return _result(
        "generate_rep_summary_text",
        "warning" if report["warnings"] else "success",
        {"markdown": report["markdown"], "metrics_used": report["metrics_used"]},
        report["warnings"],
        ["metrics_service", "reports"],
    )
