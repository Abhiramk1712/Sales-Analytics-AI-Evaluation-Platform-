"""
backend/workflows/sales_tracker_workflow.py
===========================================
Canonical end-to-end Sales Tracker workflow.

Steps:
  1. Validate data quality
  2. Build quota/attainment snapshots
  3. Calculate governed metrics
  4. Run deal risk scoring
  5. Run rep clustering
  6. Run revenue forecast
  7. Calculate payouts
  8. Generate executive summary report
  9. Build audit trail
  10. Return enterprise readiness summary

Usage:
    result = await run_sales_tracker_workflow(db, period="2025-Q1")
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.services.sales_performance_service import SalesPerformanceService
from backend.workflows.store import create_workflow, complete_workflow, fail_workflow


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_sales_tracker_workflow(
    db: AsyncSession,
    period: Optional[str] = None,
    company_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the full sales tracker workflow.
    Returns a workflow result dict with step-by-step results and audit trail.
    """
    workflow_id = f"wf_{uuid.uuid4().hex[:12]}"
    steps_completed: List[str] = []
    steps_failed: List[str] = []
    all_warnings: List[str] = []
    audit_trail: List[Dict[str, Any]] = []
    step_results: Dict[str, Any] = {}

    wf_record = create_workflow(
        workflow_id=workflow_id,
        pipeline="sales_tracker",
        period=period,
        company_id=company_id,
    )

    def _log_step(name: str, result: Any, warnings: Optional[List[str]] = None) -> None:
        steps_completed.append(name)
        step_results[name] = result
        if warnings:
            all_warnings.extend(warnings)
        audit_trail.append({
            "step": name,
            "status": "ok",
            "timestamp": _now_iso(),
            "warnings": warnings or [],
        })

    def _fail_step(name: str, error: str) -> None:
        steps_failed.append(name)
        all_warnings.append(f"Step '{name}' failed: {error}")
        audit_trail.append({
            "step": name,
            "status": "failed",
            "error": error,
            "timestamp": _now_iso(),
        })

    # ── Step 1: Sales performance metrics ──
    try:
        svc = SalesPerformanceService(db)
        perf = await svc.get_full_summary(period=period)
        step_results["performance"] = perf
        _log_step("sales_performance", perf, perf.get("warnings", []))
    except Exception as e:
        _fail_step("sales_performance", str(e))

    # ── Step 2: Data quality check ──
    try:
        from backend.routers.data_quality import _build_checks
        dq_checks = await _build_checks(db)
        errors = [c for c in dq_checks if c.get("status") == "FAIL"]
        warns = [c for c in dq_checks if c.get("status") == "WARN"]
        step_results["data_quality"] = {
            "total_checks": len(dq_checks),
            "errors": len(errors),
            "warnings": len(warns),
            "checks": dq_checks,
        }
        dq_warnings = [f"DQ FAIL: {c.get('check_name')}" for c in errors[:3]]
        _log_step("data_quality", step_results["data_quality"], dq_warnings)
    except Exception as e:
        _fail_step("data_quality", str(e))

    # ── Step 3: Deal risk scoring ──
    try:
        from backend.agent.tools.ml_tools import get_deal_risk_summary
        risk = await get_deal_risk_summary(db)
        step_results["deal_risk"] = risk.get("data", {})
        _log_step("deal_risk", step_results["deal_risk"], risk.get("warnings", []))
    except Exception as e:
        _fail_step("deal_risk", str(e))

    # ── Step 4: Rep clustering ──
    try:
        from backend.agent.tools.ml_tools import get_rep_clusters_summary
        clusters = await get_rep_clusters_summary(db)
        step_results["rep_clusters"] = clusters.get("data", {})
        _log_step("rep_clusters", step_results["rep_clusters"], clusters.get("warnings", []))
    except Exception as e:
        _fail_step("rep_clusters", str(e))

    # ── Step 5: Revenue forecast ──
    try:
        from backend.agent.tools.ml_tools import get_forecast_summary
        forecast = await get_forecast_summary(db)
        step_results["forecast"] = forecast.get("data", {})
        _log_step("forecast", step_results["forecast"], forecast.get("warnings", []))
    except Exception as e:
        _fail_step("forecast", str(e))

    # ── Step 6: Payout summary ──
    try:
        from backend.payout import compute_payout
        payout_result = await compute_payout(db, period=period)
        step_results["payouts"] = payout_result
        _log_step("payouts", payout_result, payout_result.get("warnings", []))
    except Exception as e:
        _fail_step("payouts", str(e))

    # ── Step 7: Report generation ──
    try:
        from backend.reports.report_generator import ReportGenerator
        rg = ReportGenerator(db)
        report = await rg.generate_report("executive_weekly", period=period or "")
        step_results["report"] = {
            "report_type": "executive_weekly",
            "period": period,
            "markdown_preview": (report.get("markdown") or "")[:400],
            "warnings": report.get("warnings", []),
        }
        _log_step("report", step_results["report"], report.get("warnings", []))
    except Exception as e:
        _fail_step("report", str(e))

    # ── Finalize ──
    result = {
        "workflow_id": workflow_id,
        "pipeline": "sales_tracker",
        "period": period,
        "company_id": company_id,
        "steps_completed": steps_completed,
        "steps_failed": steps_failed,
        "step_results": step_results,
        "warnings": all_warnings,
        "audit_trail": audit_trail,
        "status": "completed" if not steps_failed else "partial",
        "completed_at": _now_iso(),
    }

    if steps_failed and len(steps_failed) >= len(steps_completed):
        fail_workflow(workflow_id, errors=all_warnings)
    else:
        complete_workflow(
            workflow_id,
            result=result,
            steps_completed=steps_completed,
        )

    return result
