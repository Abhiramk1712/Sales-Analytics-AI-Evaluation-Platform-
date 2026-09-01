"""
backend/agent/workflows/sales_performance_pipeline.py
======================================================
Agentic multi-step sales performance analysis workflow.

This pipeline orchestrates a sequence of analytical steps that together
produce a holistic sales performance snapshot for a given period.

WORKFLOW STEPS (in order)
--------------------------
1.  resolve_period        — Normalize the period string; default to current month
2.  fetch_metrics         — Pull revenue, quota, win rate, pipeline, ARR/NRR
3.  fetch_forecast        — Run or retrieve revenue forecast + confidence
4.  evaluate_forecasting  — Backtest metrics for the period
5.  score_deals           — Batch ML deal scoring for open deals
6.  cluster_reps          — Rep clustering (performance tiers)
7.  check_data_quality    — Run data quality checks, surface issues
8.  compute_payouts       — Credit-level payout computation for period
9.  generate_report       — Assemble exec-summary report with all data
10. grade_enterprise      — Run enterprise grader; emit category scores

FALLBACKS
---------
Each step catches errors independently. If a step fails, the pipeline
records the error and marks that step's output as `status: "failed"` so
downstream steps still receive a partial result. The final response
always includes a `step_results` dict with per-step status.

The pipeline does NOT make up numbers. If real data is unavailable for
a step, that step's output is tagged:
  {"status": "no_data", "reason": "...", "data": null}
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, date
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from backend.utils.date_ranges import parse_period_to_range

logger = logging.getLogger(__name__)


# ── Step helpers ──────────────────────────────────────────────────────────

def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "data": data}

def _fail(step: str, exc: Exception) -> dict[str, Any]:
    logger.warning("Workflow step '%s' failed: %s", step, exc)
    return {"status": "failed", "error": str(exc), "data": None}

def _no_data(reason: str) -> dict[str, Any]:
    return {"status": "no_data", "reason": reason, "data": None}


# ── Individual steps ──────────────────────────────────────────────────────

async def _step_resolve_period(period: Optional[str]) -> dict[str, Any]:
    """Normalize period to YYYY-MM; default to current month.

    NOTE: parse_period_to_range returns strings, not date objects.
    """
    try:
        if period is None:
            period = date.today().strftime("%Y-%m")
        else:
            period = period.strip()
        pr = parse_period_to_range(period)
        # start_date / end_date are already strings (YYYY-MM-DD)
        start = pr.start_date if isinstance(pr.start_date, str) else pr.start_date.isoformat()
        end   = pr.end_date   if isinstance(pr.end_date,   str) else pr.end_date.isoformat()
        return _ok({
            "period": period,
            "start_date": start,
            "end_date": end,
        })
    except Exception as e:
        fallback = date.today().strftime("%Y-%m")
        logger.warning("Period parse failed (%s); falling back to %s", e, fallback)
        pr = parse_period_to_range(fallback)
        start = pr.start_date if isinstance(pr.start_date, str) else pr.start_date.isoformat()
        end   = pr.end_date   if isinstance(pr.end_date,   str) else pr.end_date.isoformat()
        return _ok({
            "period": fallback,
            "start_date": start,
            "end_date": end,
            "warning": f"Period '{period}' could not be parsed; using {fallback}",
        })


async def _step_fetch_metrics(db: AsyncSession, period: str) -> dict[str, Any]:
    """Pull core KPIs from the metrics calculators.

    Calculators accept `filters` dict with start_date/end_date, not `period=`.
    Falls back gracefully for each metric independently.
    """
    try:
        from backend.metrics.calculators import (
            get_total_revenue,
            get_quota_attainment,
            get_win_rate,
            get_open_pipeline,   # get_pipeline_value does not exist; use get_open_pipeline
            get_nrr,
            get_grr,
            get_arr_growth_rate,
        )
        from backend.utils.date_ranges import parse_period_to_range

        pr = parse_period_to_range(period)
        start = pr.start_date if isinstance(pr.start_date, str) else pr.start_date.isoformat()
        end   = pr.end_date   if isinstance(pr.end_date,   str) else pr.end_date.isoformat()
        filters = {"start_date": start, "end_date": end}
        # Pipeline is a point-in-time snapshot — no period filter
        pipeline_filters: dict = {}

        results: dict[str, Any] = {}
        for name, fn, f in [
            ("total_revenue",    get_total_revenue,    filters),
            ("quota_attainment", get_quota_attainment, filters),
            ("win_rate",         get_win_rate,         filters),
            ("open_pipeline",    get_open_pipeline,    pipeline_filters),
            ("nrr",              get_nrr,              filters),
            ("grr",              get_grr,              filters),
            ("arr_growth_rate",  get_arr_growth_rate,  filters),
        ]:
            try:
                results[name] = await fn(db=db, filters=f if f else None)
            except Exception as e:
                results[name] = {"value": None, "warnings": [str(e)], "error": str(e)}
        return _ok(results)
    except Exception as e:
        return _fail("fetch_metrics", e)


async def _step_fetch_forecast(db: AsyncSession) -> dict[str, Any]:
    """Run revenue forecast for the next 12 months.

    run_revenue_forecast(revenue_by_period, horizon) — does not take db directly.
    We fetch the revenue series from DB first.
    """
    try:
        from backend.ml.forecasting import run_revenue_forecast
        from backend.models import Revenue
        from sqlalchemy import select, func

        rows = (await db.execute(
            select(Revenue.period, func.sum(Revenue.amount).label("amt"))
            .group_by(Revenue.period)
            .order_by(Revenue.period)
        )).all()

        if not rows:
            return _no_data("No revenue data available for forecasting.")

        rev_by_period = {r.period: float(r.amt) for r in rows}
        result = run_revenue_forecast(rev_by_period, horizon=12)
        return _ok(result)
    except Exception as e:
        return _fail("fetch_forecast", e)


async def _step_evaluate_forecasting(db: AsyncSession) -> dict[str, Any]:
    """Run backtest evaluation to surface model accuracy."""
    try:
        from backend.ml.evaluation import rolling_origin_backtest
        from backend.models import Revenue
        from sqlalchemy import select, func
        import pandas as pd

        rows = (await db.execute(select(Revenue.period, func.sum(Revenue.amount).label("amt")).group_by(Revenue.period).order_by(Revenue.period))).all()
        if not rows:
            return _no_data("No revenue data available for backtest.")
        series = pd.Series({r.period: float(r.amt) for r in rows})
        backtest = rolling_origin_backtest(series)
        return _ok(backtest)
    except Exception as e:
        return _fail("evaluate_forecasting", e)


async def _step_score_deals(db: AsyncSession) -> dict[str, Any]:
    """Score all open deals using the ML deal scorer with leakage-safe snapshots."""
    try:
        from backend.ml.deal_scoring import run_deal_scoring
        from backend.models import Deal, Activity
        from sqlalchemy import select

        # Fetch all deals (open + closed for training; open for scoring)
        deals = (await db.execute(select(Deal))).scalars().all()

        if not deals:
            return _no_data("No deals to score.")

        # Fetch activities for snapshot enrichment
        activities = (await db.execute(select(Activity))).scalars().all()
        activity_dicts = [
            {
                "deal_id":       str(a.deal_id) if a.deal_id else None,
                "activity_date": a.activity_date.isoformat() if a.activity_date else None,
            }
            for a in activities
        ]

        deal_dicts = [
            {
                "id":                  str(d.id),
                "amount":              float(d.amount or 0),
                "stage":               d.stage or "Prospecting",
                "created_at":          d.created_at.isoformat() if d.created_at else None,
                "expected_close_date": d.expected_close_date.isoformat() if d.expected_close_date else None,
                "actual_close_date":   d.actual_close_date.isoformat() if d.actual_close_date else None,
                "rep_id":              str(d.rep_id) if d.rep_id else None,
                "product":             d.product,
            }
            for d in deals
        ]
        result = run_deal_scoring(deal_dicts, activities=activity_dicts)
        scored = result.get("scored_deals", [])
        return _ok({
            "deal_count":         len(deals),
            "total_scored":       len(scored),
            "scores_sample":      scored[:10],
            "leakage_violations": result.get("leakage_violations", []),
            "warnings":           result.get("warnings", []),
        })
    except Exception as e:
        return _fail("score_deals", e)


async def _step_cluster_reps(db: AsyncSession) -> dict[str, Any]:
    """Cluster reps into performance tiers.

    run_rep_clustering(reps: list[dict]) — not RepClusterer class.
    We fetch rep performance data from DB first.
    """
    try:
        from backend.ml.rep_clustering import run_rep_clustering
        from backend.models import Rep, Revenue, Quota, Deal
        from sqlalchemy import select, func

        reps = (await db.execute(select(Rep))).scalars().all()
        if not reps:
            return _no_data("No reps found for clustering.")

        rep_dicts = []
        for rep in reps:
            rev = float((await db.execute(
                select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id)
            )).scalar() or 0)
            quota = float((await db.execute(
                select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id)
            )).scalar() or 0)
            won = int((await db.execute(
                select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won")
            )).scalar() or 0)
            lost = int((await db.execute(
                select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost")
            )).scalar() or 0)
            rep_dicts.append({
                "rep_id":        str(rep.id),
                "name":          rep.name,
                "total_revenue": rev,
                "quota":         quota,
                "attainment":    (rev / quota * 100) if quota > 0 else 0,
                "deals_won":     won,
                "deals_lost":    lost,
                "win_rate":      (won / max(1, won + lost)) * 100,
            })

        result = run_rep_clustering(rep_dicts)
        return _ok(result)
    except Exception as e:
        return _fail("cluster_reps", e)


async def _step_check_data_quality(db: AsyncSession) -> dict[str, Any]:
    """Run data quality checks across core tables."""
    try:
        from backend.routers.data_quality import _build_checks
        checks = await _build_checks(db)
        error_count = sum(1 for c in checks if c["status"] == "FAIL")
        warning_count = sum(1 for c in checks if c["status"] == "WARN")
        score = max(0, 100 - (error_count * 15) - (warning_count * 5))
        status = "FAIL" if error_count > 0 else ("WARN" if warning_count > 0 else "PASS")
        failed = [c for c in checks if c["status"] in ("FAIL", "WARN")]
        return _ok({
            "score":         score,
            "status":        status,
            "issue_count":   len(failed),
            "issues":        failed[:20],
            "total_checks":  len(checks),
        })
    except Exception as e:
        return _fail("check_data_quality", e)


async def _step_compute_payouts(db: AsyncSession, period: str) -> dict[str, Any]:
    """Compute credit-level payouts for all reps."""
    try:
        from backend.payout.credit_payout_engine import compute_credit_payouts
        payouts = await compute_credit_payouts(db=db, period=period)
        return _ok([p.to_dict() for p in payouts])
    except Exception as e:
        return _fail("compute_payouts", e)


async def _step_generate_report(
    db: AsyncSession,
    period: str,
    step_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Assemble an executive summary report from all step outputs."""
    try:
        from backend.reports.report_generator import generate_report
        report = await generate_report(
            db=db,
            report_type="executive_summary",
            period=period,
            extra_context=step_results,
        )
        return _ok(report)
    except Exception as e:
        return _fail("generate_report", e)


async def _step_grade_enterprise(db: AsyncSession) -> dict[str, Any]:
    """Run enterprise grading and return category scores."""
    try:
        from backend.grading.enterprise_grader import EnterpriseGrader
        grader = EnterpriseGrader()
        grade = grader.run()
        return _ok(grade)
    except Exception as e:
        return _fail("grade_enterprise", e)


# ── Main orchestrator ─────────────────────────────────────────────────────

async def run_sales_performance_pipeline(
    db: AsyncSession,
    period: Optional[str] = None,
    options: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Run the full 10-step sales performance pipeline.

    Parameters
    ----------
    db      : async DB session
    period  : YYYY-MM / YYYY / YYYY-QN (defaults to current month)
    options : optional dict with keys:
              skip_steps: list[str]   — step names to skip
              only_steps: list[str]   — run only these steps (overrides skip)

    Returns
    -------
    {
        "pipeline_version": "1.0",
        "period": "YYYY-MM",
        "generated_at": "ISO-8601",
        "step_results": { step_name: {status, data|error} },
        "summary": { high-level digest }
    }
    """
    opts = options or {}
    skip = set(opts.get("skip_steps", []))
    only = set(opts.get("only_steps", []))
    started_at = datetime.now(timezone.utc)

    step_results: dict[str, dict[str, Any]] = {}

    # Step 1 — resolve period
    step_results["resolve_period"] = await _step_resolve_period(period)
    resolved_period: str = (
        step_results["resolve_period"].get("data", {}).get("period")
        or date.today().strftime("%Y-%m")
    )

    # Define remaining steps
    async def maybe(name: str, coro):
        if only and name not in only:
            return
        if name in skip:
            step_results[name] = {"status": "skipped", "data": None}
            return
        step_results[name] = await coro

    await maybe("fetch_metrics",        _step_fetch_metrics(db, resolved_period))
    await maybe("fetch_forecast",       _step_fetch_forecast(db))
    await maybe("evaluate_forecasting", _step_evaluate_forecasting(db))
    await maybe("score_deals",          _step_score_deals(db))
    await maybe("cluster_reps",         _step_cluster_reps(db))
    await maybe("check_data_quality",   _step_check_data_quality(db))
    await maybe("compute_payouts",      _step_compute_payouts(db, resolved_period))
    await maybe("generate_report",      _step_generate_report(db, resolved_period, step_results))
    await maybe("grade_enterprise",     _step_grade_enterprise(db))

    # Build summary
    ok_steps  = [k for k, v in step_results.items() if v.get("status") == "ok"]
    fail_steps = [k for k, v in step_results.items() if v.get("status") == "failed"]

    metrics_data = step_results.get("fetch_metrics", {}).get("data") or {}
    summary: dict[str, Any] = {
        "period":          resolved_period,
        "steps_completed": len(ok_steps),
        "steps_failed":    len(fail_steps),
        "failed_steps":    fail_steps,
        "revenue":         metrics_data.get("total_revenue"),
        "quota_attainment":metrics_data.get("quota_attainment"),
        "win_rate":        metrics_data.get("win_rate"),
        "elapsed_ms":      int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000),
    }

    return {
        "pipeline_version": "1.0",
        "period":           resolved_period,
        "generated_at":     started_at.isoformat(),
        "step_results":     step_results,
        "summary":          summary,
    }
