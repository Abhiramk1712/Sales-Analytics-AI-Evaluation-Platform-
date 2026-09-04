"""
Generate markdown reports from governed metrics and DB-backed evidence.
"""
from __future__ import annotations

import calendar
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy.ext.asyncio import AsyncSession

from backend.metrics.service import get_metrics_service
from backend.metrics import calculators
from backend.statistics.sales_drivers import explain_metric_change
from backend.utils.date_ranges import period_to_filter_dict, previous_period

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_JINJA_ENV = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(disabled_extensions=("md",)),
    trim_blocks=True,
    lstrip_blocks=True,
)


class ReportGenerator:
    @staticmethod
    def render_template(template_name: str, context: dict[str, Any]) -> str:
        """Render a Jinja2 .md template from the templates/ directory."""
        try:
            tmpl = _JINJA_ENV.get_template(template_name)
            return tmpl.render(**context)
        except Exception as exc:
            return f"# Template Render Error\n\nFailed to render `{template_name}`: {exc}\n"
    @staticmethod
    def _resolve_date_filters(period: str, incoming: dict[str, Any]) -> dict[str, Any]:
        """Parse period string and inject start_date/end_date into filters."""
        filters = {**incoming}
        parsed = period_to_filter_dict(period)
        if parsed:
            filters.setdefault("start_date", parsed["start_date"])
            filters.setdefault("end_date", parsed["end_date"])
        return filters

    @staticmethod
    async def generate_report(
        db: AsyncSession,
        report_type: str,
        period: str,
        audience: str,
        filters: Optional[dict[str, Any]] = None,
        ingestion_context: Optional[dict[str, Any]] = None,
        payout_context: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        filters = filters or {}
        filters = ReportGenerator._resolve_date_filters(period, filters)
        metrics_service = get_metrics_service()

        kpis = await metrics_service.get_kpis(db, filters=filters)
        top_reps = await calculators.get_top_reps(db, limit=5, filters=filters)
        underperformers = await calculators.get_underperforming_reps(db, threshold_pct=75, filters=filters)
        region_revenue = await calculators.get_revenue_by_region(db, filters=filters)

        metrics_used = [
            "total_revenue", "total_quota", "quota_attainment", "win_rate",
            "open_pipeline", "pipeline_coverage", "average_deal_size",
            "nrr", "grr", "arr_growth_rate", "sales_cycle_days",
            "activity_ratio", "weighted_pipeline_coverage", "quota_attainment_distribution",
        ]

        warnings = list(kpis.get("warnings", []))
        warnings.extend(top_reps.get("warnings", []))
        warnings.extend(underperformers.get("warnings", []))
        warnings.extend(region_revenue.get("warnings", []))

        citations = {
            "summary": sorted(set(kpis.get("sources", []))),
            "key_metrics": sorted(set(kpis.get("sources", []))),
            "what_changed": sorted(set(region_revenue.get("sources", []))),
            "risks": sorted(set(underperformers.get("sources", []))),
            "opportunities": sorted(set(top_reps.get("sources", []))),
            "quality": ["warnings"],
        }

        pop_explanation = {"top_positive_drivers": [], "top_negative_drivers": [], "warnings": []}
        try:
            prev = previous_period(period)
            prev_filters = ReportGenerator._resolve_date_filters(prev, filters={})
            prev_kpis = await metrics_service.get_kpis(db, filters=prev_filters)
            pop_explanation = explain_metric_change(kpis, prev_kpis)
        except Exception:
            pop_explanation = {"top_positive_drivers": [], "top_negative_drivers": [], "warnings": ["Previous comparable period unavailable"]}

        if report_type == "executive_weekly":
            markdown = ReportGenerator._executive_weekly(period, audience, kpis, top_reps["data"], underperformers["data"], region_revenue["data"], warnings + pop_explanation.get("warnings", []), citations, pop_explanation)
        elif report_type == "manager_monthly":
            markdown = ReportGenerator._manager_monthly(period, audience, kpis, top_reps["data"], underperformers["data"], warnings, citations)
        elif report_type == "rep_performance":
            markdown = ReportGenerator._rep_performance(period, audience, kpis, top_reps["data"], underperformers["data"], warnings, citations)
        elif report_type == "pipeline_health":
            pipeline_check = await calculators.get_open_pipeline(db, filters)
            weighted = await calculators.get_weighted_pipeline_coverage(db, filters)
            activity_ratio = await calculators.get_activity_ratio(db, filters)
            cycle_days = await calculators.get_sales_cycle_days(db, filters)
            markdown = ReportGenerator._pipeline_health_report(
                period, audience, kpis, pipeline_check, weighted, activity_ratio, cycle_days,
                top_reps["data"], underperformers["data"], warnings, citations
            )
        elif report_type == "quota_attainment":
            attainment_dist = await calculators.get_quota_attainment_distribution(db, filters)
            markdown = ReportGenerator._quota_attainment_report(
                period, audience, kpis, top_reps["data"], underperformers["data"],
                attainment_dist, warnings, citations
            )
        elif report_type == "arr_bridge":
            nrr = await calculators.get_nrr(db, filters)
            grr = await calculators.get_grr(db, filters)
            arr_growth = await calculators.get_arr_growth_rate(db, filters)
            markdown = ReportGenerator._arr_bridge_report(
                period, audience, kpis, nrr, grr, arr_growth, warnings, citations
            )
        elif report_type in ("executive_summary", "exec_summary"):
            # Alias for executive_weekly — used by the workflow pipeline
            markdown = ReportGenerator._executive_weekly(period, audience or "executive", kpis, top_reps["data"], underperformers["data"], region_revenue["data"], warnings + pop_explanation.get("warnings", []), citations, pop_explanation)
        elif report_type == "payout_statement":
            from backend.payout.credit_payout_engine import compute_credit_payouts
            from backend.utils.date_ranges import parse_period_to_range
            pr = parse_period_to_range(period)
            payout_period = pr.start_date[:7] if pr else (period[:7] if period else "unknown")
            payout_results = await compute_credit_payouts(db=db, period=payout_period)
            markdown = ReportGenerator._payout_statement(period, audience, payout_results, kpis, warnings, citations)
        elif report_type == "forecast_summary":
            from backend.ml.forecasting import run_revenue_forecast
            from sqlalchemy import select
            from backend.models import Revenue as RevModel
            rows = (await db.execute(select(RevModel))).scalars().all()
            rev_by_period: dict[str, float] = {}
            for row in rows:
                rev_by_period[row.period] = rev_by_period.get(row.period, 0.0) + float(row.amount or 0)
            fc = run_revenue_forecast(rev_by_period, horizon=6) if rev_by_period else {}
            markdown = ReportGenerator._forecast_summary(period, audience, fc, kpis, warnings, citations)
        elif report_type == "plan_performance":
            from sqlalchemy import select as _select
            from backend.models import Plan, Rule, PlanAssignment
            plan_id = filters.get("plan_id")
            plan_q = _select(Plan)
            if plan_id:
                plan_q = plan_q.where(Plan.id == plan_id)
            plan_obj = (await db.execute(plan_q)).scalars().first()
            plan_dict = {
                "name": plan_obj.name if plan_obj else "—",
                "effective_start_date": str(plan_obj.effective_start_date or "") if plan_obj else "",
                "effective_end_date": str(plan_obj.effective_end_date or "") if plan_obj else "",
            }
            rules_rows = []
            if plan_obj:
                rules_raw = (await db.execute(_select(Rule).where(Rule.plan_id == plan_obj.id))).scalars().all()
                for r in rules_raw:
                    rules_rows.append({
                        "name": r.name, "metric_name": r.metric_name or "",
                        "threshold_min": float(r.threshold_min or 0),
                        "threshold_max": float(r.threshold_max or 999),
                        "rate": float(r.rate or 0), "bonus_amount": float(r.bonus_amount or 0),
                    })
            n_reps = len(top_reps.get("data", []))
            reps_at = sum(1 for r in top_reps.get("data", []) if r.get("attainment_pct", 0) >= 100)
            reps_near = sum(1 for r in top_reps.get("data", []) if 75 <= r.get("attainment_pct", 0) < 100)
            reps_below = sum(1 for r in top_reps.get("data", []) if r.get("attainment_pct", 0) < 75)
            plan_metrics = {
                "total_revenue": float(kpis.get("total_revenue", 0)),
                "total_quota": float(kpis.get("total_quota", 0)),
                "attainment_pct": float(kpis.get("attainment_pct", 0)),
                "rep_count": n_reps,
                "reps_at_quota": reps_at,
                "reps_near_quota": reps_near,
                "reps_below_quota": reps_below,
                "confidence": "high",
            }
            markdown = ReportGenerator.render_template("plan_performance.md", {
                "plan": plan_dict, "period": period, "generated_at": datetime.now(timezone.utc).isoformat(),
                "metrics": plan_metrics, "rules": rules_rows,
                "reps": [{"name": r.get("name", ""), "revenue": r.get("revenue", 0),
                           "quota": r.get("quota", 0), "attainment_pct": r.get("attainment_pct", 0),
                           "payout_amount": 0} for r in top_reps.get("data", [])],
                "warnings": warnings,
            })
        elif report_type == "territory_performance":
            from sqlalchemy import select as _select
            from backend.models import Territory
            territory_id = filters.get("territory_id")
            terr_q = _select(Territory)
            if territory_id:
                terr_q = terr_q.where(Territory.id == territory_id)
            terr_obj = (await db.execute(terr_q)).scalars().first()
            terr_dict = {
                "name": terr_obj.name if terr_obj else "—",
                "region": terr_obj.region if terr_obj else "",
                "segment": terr_obj.segment if terr_obj else "",
            }
            hygiene_data = await calculators.get_pipeline_hygiene(db, filters)
            terr_metrics = {
                "total_revenue": float(kpis.get("total_revenue", 0)),
                "deals_won": int(kpis.get("deals_won", 0)),
                "win_rate": float(kpis.get("win_rate", 0)),
                "avg_deal_size": float(kpis.get("average_deal_size", 0)),
                "open_pipeline": float(kpis.get("open_pipeline", 0)),
                "confidence": "high",
            }
            markdown = ReportGenerator.render_template("territory_performance.md", {
                "territory": terr_dict, "period": period, "generated_at": datetime.now(timezone.utc).isoformat(),
                "metrics": terr_metrics, "hygiene": hygiene_data,
                "reps": [{"name": r.get("name", ""), "revenue": r.get("revenue", 0),
                           "deals_won": r.get("deals_won", 0), "attainment_pct": r.get("attainment_pct", 0)}
                          for r in top_reps.get("data", [])],
                "sub_territories": [],
                "warnings": warnings,
            })
        elif report_type == "executive_sales_summary":
            key_metrics = {
                "total_revenue": float(kpis.get("total_revenue", 0)),
                "total_quota": float(kpis.get("total_quota", 0)),
                "quota_attainment": float(kpis.get("attainment_pct", 0)),
                "open_pipeline": float(kpis.get("open_pipeline", 0)),
                "win_rate": float(kpis.get("win_rate", 0)),
            }
            risks = [
                f"{len(underperformers.get('data', []))} reps are below 75% attainment"
                if underperformers.get("data") else "No major rep-attainment outliers were detected",
                "Pipeline coverage should remain above 3.0x for predictable quarter close",
            ]
            recommendations = [
                "Review top 10 late-stage deals with slip risk and assign executive sponsors.",
                "Run weekly forecast-variance review with RevOps and Finance.",
                "Prioritize enablement for underperforming rep cohorts.",
            ]
            markdown = ReportGenerator.render_template("executive_sales_summary.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "key_metrics": key_metrics,
                "risks": risks,
                "recommendations": recommendations,
                "top_reps": top_reps.get("data", [])[:5],
                "assumptions": [
                    "Metrics are generated from currently loaded tenant/company context.",
                    "Forecasting confidence depends on historical period coverage.",
                ],
                "warnings": warnings,
            })
        elif report_type == "payout_audit_report":
            from backend.payout.credit_payout_engine import compute_credit_payouts
            from backend.utils.date_ranges import parse_period_to_range

            pr = parse_period_to_range(period)
            payout_period = pr.start_date[:7] if pr else (period[:7] if period else "unknown")
            payout_rows = await compute_credit_payouts(db=db, period=payout_period)
            total_payout = float(sum(p.final_payout for p in payout_rows))
            fallback_count = int(sum(1 for p in payout_rows if p.fallback_mode != "none"))
            markdown = ReportGenerator.render_template("payout_audit_report.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "total_payout": total_payout,
                "rep_count": len(payout_rows),
                "fallback_count": fallback_count,
                "rows": payout_rows[:20],
                "risks": [
                    "Fallback payouts indicate missing source-credit fidelity" if fallback_count else "No fallback payouts detected",
                ],
                "recommendations": [
                    "Ensure payout approvals require full trace and source records.",
                    "Lock approved payout periods to prevent silent restatement.",
                ],
                "assumptions": ["Payout records reflect currently loaded plan/rule configuration."],
                "warnings": warnings,
            })
        elif report_type == "forecast_confidence_report":
            from sqlalchemy import select
            from backend.models import Revenue as RevModel
            from backend.ml.forecasting import run_revenue_forecast

            rows = (await db.execute(select(RevModel))).scalars().all()
            rev_by_period: dict[str, float] = {}
            for row in rows:
                rev_by_period[row.period] = rev_by_period.get(row.period, 0.0) + float(row.amount or 0)

            fc = run_revenue_forecast(rev_by_period, horizon=6) if rev_by_period else {}
            metrics = fc.get("model_metrics", {}) if isinstance(fc, dict) else {}
            markdown = ReportGenerator.render_template("forecast_confidence_report.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "forecast": fc,
                "metrics": metrics,
                "risks": [
                    "Forecast confidence is limited when monthly history is below 18 periods.",
                    "High MAPE should trigger scenario review before commitments.",
                ],
                "recommendations": [
                    "Use conservative scenario for committed plans when confidence is medium/low.",
                    "Refresh with latest closed-won and pipeline updates weekly.",
                ],
                "assumptions": ["Forecast uses available revenue/pipeline history and configured model ensemble."],
                "warnings": warnings + list(fc.get("warnings", []) if isinstance(fc, dict) else []),
            })
        elif report_type == "data_quality_report":
            from backend.routers.data_quality import _build_checks

            checks = await _build_checks(db)
            critical = [c for c in checks if c.get("severity") == "critical"]
            error = [c for c in checks if c.get("severity") == "error"]
            warning = [c for c in checks if c.get("severity") == "warning"]
            markdown = ReportGenerator.render_template("data_quality_report.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "critical": critical,
                "error": error,
                "warning": warning,
                "checks": checks,
                "risks": [
                    "Critical quality issues block payout approvals and model retraining." if critical else "No critical quality gate blockers detected.",
                ],
                "recommendations": [
                    "Resolve critical issues before approval/training operations.",
                    "Track warning trends weekly with data owners.",
                ],
                "assumptions": ["Quality checks were run against the current tenant dataset snapshot."],
                "warnings": warnings,
            })
        elif report_type == "model_monitoring_report":
            from sqlalchemy import select
            from backend.models import ModelRunRecord

            model_rows = (await db.execute(
                select(ModelRunRecord).order_by(ModelRunRecord.trained_at.desc()).limit(100)
            )).scalars().all()

            latest_by_model: dict[str, Any] = {}
            for row in model_rows:
                latest_by_model.setdefault(row.model_name, row)

            models = []
            for name, row in latest_by_model.items():
                metrics_json = row.metrics or {}
                models.append({
                    "model_name": name,
                    "model_version": row.model_version,
                    "trained_at": row.trained_at.isoformat() if row.trained_at else None,
                    "metrics": metrics_json,
                    "warnings": row.limitations or [],
                })

            markdown = ReportGenerator.render_template("model_monitoring_report.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "models": models,
                "risks": ["Models with stale training dates or weak validation metrics require review."],
                "recommendations": [
                    "Set retraining cadence with explicit promotion criteria.",
                    "Document model card risk warnings in exec readouts.",
                ],
                "assumptions": ["Monitoring view is based on latest model run metadata in this environment."],
                "warnings": warnings,
            })
        elif report_type == "revops_risk_report":
            pipeline = await calculators.get_open_pipeline(db, filters)
            weighted = await calculators.get_weighted_pipeline_coverage(db, filters)
            summary = {
                "total_revenue": float(kpis.get("total_revenue", 0)),
                "quota_attainment": float(kpis.get("attainment_pct", 0)),
                "open_pipeline": float(kpis.get("open_pipeline", 0)),
                "pipeline_open": float(pipeline.get("value", 0)) if isinstance(pipeline, dict) else 0.0,
                "weighted_coverage_ratio": float(weighted.get("ratio", 0)) if isinstance(weighted, dict) else 0.0,
            }
            markdown = ReportGenerator.render_template("revops_risk_report.md", {
                "period": period,
                "company": filters.get("company") or filters.get("company_id") or "active_company",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "data_freshness": datetime.now(timezone.utc).isoformat(),
                "summary": summary,
                "underperformers": underperformers.get("data", [])[:15],
                "risks": [
                    "Quota risk increases when weighted coverage remains below 1.0x.",
                    "Rep attainment variance can indicate territory or plan misalignment.",
                ],
                "recommendations": [
                    "Run deal rescue and activity-coverage playbooks on at-risk cohorts.",
                    "Align plan/rule design with realistic ramp and territory capacity.",
                ],
                "assumptions": ["Risk insights are directional and should be validated with manager context."],
                "warnings": warnings + pipeline.get("warnings", []) + weighted.get("warnings", []),
            })
        else:
            raise ValueError(f"Unsupported report type: {report_type}")

        # Append optional sections
        if ingestion_context:
            markdown += ReportGenerator._ingestion_quality_appendix(ingestion_context)
        if payout_context:
            markdown += ReportGenerator._payout_rationale(payout_context)

        return {
            "report_type": report_type,
            "period": period,
            "audience": audience,
            "markdown": markdown,
            "metrics_used": metrics_used,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "warnings": sorted(set(warnings)),
            "evidence_citations": citations,
        }

    @staticmethod
    def _source_line(sources: list[str]) -> str:
        return f"Sources: {', '.join(sources)}" if sources else "Sources: unavailable"

    @staticmethod
    def _ingestion_quality_appendix(ctx: dict[str, Any]) -> str:
        quality = ctx.get("quality_gate", {})
        status = quality.get("overall_status", "unknown")
        confidence = quality.get("confidence", 1.0)
        issues = quality.get("issues", [])
        relationship_quality = quality.get("relationship_quality", {})
        rows_loaded = ctx.get("db_rows_loaded", {})
        source_count = len(ctx.get("source_manifest", []))
        load_mode = ctx.get("load_mode", "full_reload")

        issue_lines = "\n".join(
            f"- [{i['severity'].upper()}] {i['message']}" for i in issues
        ) if issues else "- No quality issues detected."

        rows_lines = "\n".join(
            f"- {entity}: {count:,} rows" for entity, count in rows_loaded.items()
        ) if rows_loaded else "- No DB load counts available."

        rel_required = int(relationship_quality.get("required_unresolved", 0) or 0)
        rel_optional = int(relationship_quality.get("optional_unresolved", 0) or 0)
        rel_applied = bool(relationship_quality.get("applied", False))
        rel_lines = (
            f"- Relationship penalties applied: {'yes' if rel_applied else 'no'}\n"
            f"- Required unresolved relationships: {rel_required}\n"
            f"- Optional unresolved relationships: {rel_optional}"
        )

        return f"""

---

## Appendix: Ingestion Quality Report

- **Sources Processed**: {source_count}
- **Load Mode**: {load_mode}
- **Quality Status**: {status.upper()}
- **Data Confidence**: {confidence:.0%}

### Issues
{issue_lines}

### Rows Loaded
{rows_lines}

### Relationship Resolution Quality
{rel_lines}
"""

    @staticmethod
    def _payout_rationale(ctx: dict[str, Any]) -> str:
        summary = ctx.get("summary", {})
        rows = ctx.get("rows", [])[:5]  # Top 5 reps
        fallback_count = summary.get("fallback_count", 0)
        low_conf = summary.get("low_confidence_count", 0)

        rep_lines = ""
        for r in rows:
            rules = "; ".join(r.get("rules_applied", []))
            conf = r.get("confidence", 1.0)
            conf_label = "high" if conf >= 0.9 else ("medium" if conf >= 0.6 else "low")
            fallback_flag = " [FALLBACK]" if r.get("fallback_used") else ""
            rep_lines += f"- **{r['name']}**: ${r.get('payout', 0):,.2f} | {rules} | confidence={conf_label}{fallback_flag}\n"

        return f"""

---

## Appendix: Payout Rationale

- **Total Payout**: ${summary.get('total_payout', 0):,.2f}
- **Reps Using Fallback**: {fallback_count}
- **Low Confidence Payouts**: {low_conf}

### Top Rep Breakdowns
{rep_lines or '- No rep payout data available.'}

> Note: Fallback payouts use a flat 5% commission rate when quota or revenue data is missing.
"""

    @staticmethod
    def _payout_statement(
        period: str,
        audience: str,
        payout_results: list,
        kpis: dict[str, Any],
        warnings: list[str],
        citations: dict[str, list[str]],
    ) -> str:
        """Generate a payout statement report from CreditPayoutResult list."""
        total = sum(p.final_payout for p in payout_results)
        fallback_count = sum(1 for p in payout_results if p.fallback_mode != "none")
        rows = []
        for p in sorted(payout_results, key=lambda x: -x.final_payout)[:20]:
            mode = f"[FALLBACK: {p.fallback_mode}]" if p.fallback_mode != "none" else "credit-level"
            rows.append(
                {
                    "rep_id_short": f"{p.rep_id[:8]}…",
                    "period": p.period,
                    "credited_amount": float(p.credited_amount),
                    "quota": float(p.quota),
                    "attainment": float(p.attainment),
                    "base_commission": float(p.base_commission),
                    "final_payout": float(p.final_payout),
                    "mode": mode,
                }
            )
        return ReportGenerator.render_template(
            "payout_statement.md",
            {
                "period": period,
                "audience": audience,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_payout": float(total),
                "rep_count": len(payout_results),
                "fallback_count": fallback_count,
                "rows": rows,
                "warnings": warnings[:5],
                "sources": sorted(set(citations.get("summary", []) + citations.get("quality", []))),
                "kpis": kpis,
            },
        )

    @staticmethod
    def _forecast_summary(
        period: str,
        audience: str,
        fc: dict[str, Any],
        kpis: dict[str, Any],
        warnings: list[str],
        citations: dict[str, list[str]],
    ) -> str:
        """Generate a forecast summary report from run_revenue_forecast output."""
        meta = fc.get("metadata", {})
        model_info = fc.get("model_info", "N/A")
        confidence = meta.get("confidence", "unknown")
        history_months = meta.get("history_months", 0)

        periods = fc.get("forecast_periods", [])
        values = fc.get("forecast_values", [])
        lower = fc.get("lower_ci", [])
        upper = fc.get("upper_ci", [])

        rows = []
        for p, v, lo, hi in zip(periods, values, lower, upper):
            rows.append(
                {
                    "period": p,
                    "forecast": float(v),
                    "lower_ci": float(lo),
                    "upper_ci": float(hi),
                }
            )

        metrics = fc.get("model_metrics", {})

        all_warnings = warnings + fc.get("warnings", [])
        return ReportGenerator.render_template(
            "forecast_summary.md",
            {
                "period": period,
                "audience": audience,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "model_info": model_info,
                "confidence": confidence,
                "history_months": history_months,
                "rows": rows,
                "metrics": {
                    "MAE": metrics.get("MAE"),
                    "RMSE": metrics.get("RMSE"),
                    "MAPE": metrics.get("MAPE"),
                },
                "warnings": all_warnings[:5],
                "sources": sorted(set(citations.get("summary", []) + citations.get("quality", []))),
                "kpis": kpis,
            },
        )

    @staticmethod
    def _executive_weekly(period: str, audience: str, kpis: dict[str, Any], top_reps: list[dict[str, Any]], under: list[dict[str, Any]], region_revenue: list[dict[str, Any]], warnings: list[str], citations: dict[str, list[str]], pop_explanation: dict[str, Any]) -> str:
        region_lines = "\n".join([f"  - {r.get('region','?')}: ${r.get('revenue',0):,.0f}" for r in region_revenue]) or "  - No regional data"
        top_lines = "\n".join([f"- {r.get('name','?')}: {r.get('attainment',0):.1f}% attainment" for r in top_reps]) or "- No top reps identified"
        under_lines = "\n".join([f"- {r.get('name','?')}: {r.get('attainment',0):.1f}% attainment" for r in under]) or "- No underperformers identified"
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data quality warnings"
        positive_lines = "\n".join([
            f"- {d['label']}: {d['direction']} by {d['delta_pct'] if d['delta_pct'] is not None else d['delta']}"
            for d in pop_explanation.get("top_positive_drivers", [])
        ]) or "- No positive drivers identified"
        negative_lines = "\n".join([
            f"- {d['label']}: {d['direction']} by {d['delta_pct'] if d['delta_pct'] is not None else d['delta']}"
            for d in pop_explanation.get("top_negative_drivers", [])
        ]) or "- No negative drivers identified"

        return f"""# Executive Weekly Report ({period})

Audience: {audience}

## Executive Summary
Revenue performance is grounded in governed metrics with evidence from revenue, quota, and deal tables.
_ {ReportGenerator._source_line(citations.get('summary', []))} _

## Key Metrics
| Metric | Value |
|---|---|
| Total Revenue | ${kpis['total_revenue']:,.0f} |
| Total Quota | ${kpis['total_quota']:,.0f} |
| Quota Attainment | {kpis['attainment_pct']:.1f}% |
| Win Rate | {kpis['win_rate']:.1f}% |
| Open Pipeline | ${kpis['open_pipeline']:,.0f} |
| Pipeline Coverage | {kpis['pipeline_coverage']:.2f}× |

_ {ReportGenerator._source_line(citations.get('key_metrics', []))} _

## Forecast vs Target
| Submission | Value |
|---|---|
| Target (Quota) | ${kpis['total_quota']:,.0f} |
| Commit (Closed Won + Negotiation) | ~${kpis['total_revenue'] * 1.05:,.0f} |
| Best Case (Commit + Proposal) | ~${kpis['open_pipeline'] * 0.45 + kpis['total_revenue']:,.0f} |
| Model Forecast | See /ml/forecast/revenue |

## What Changed
- Regional revenue mix:
{region_lines}
- Top positive drivers:
{positive_lines}
- Top negative drivers:
{negative_lines}
_ {ReportGenerator._source_line(citations.get('what_changed', []))} _

## Risks
{under_lines}
_ {ReportGenerator._source_line(citations.get('risks', []))} _

## Opportunities
{top_lines}
_ {ReportGenerator._source_line(citations.get('opportunities', []))} _

## Recommended Actions
1. Coach reps below attainment threshold and review blocked deals.
2. Prioritize late-stage opportunities with high pipeline value.
3. Improve stage hygiene for better forecast confidence.
4. Review ARR bridge for churn signals — see /analytics/revops-kpis.

## Data Quality Notes
{warning_lines}
_ {ReportGenerator._source_line(citations.get('quality', []))} _
"""

    @staticmethod
    def _manager_monthly(period: str, audience: str, kpis: dict[str, Any], top_reps: list[dict[str, Any]], under: list[dict[str, Any]], warnings: list[str], citations: dict[str, list[str]]) -> str:
        top_lines = "\n".join([f"- {r['name']} ({r['region'] or 'Unknown'}): {r['attainment_pct']:.1f}%" for r in top_reps[:5]]) or "- No top reps available"
        under_lines = "\n".join([f"- {r['name']}: {r['attainment_pct']:.1f}%" for r in under[:5]]) or "- No at-risk reps"
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data warnings"

        return f"""# Manager Monthly Report ({period})

Audience: {audience}

## Team Performance
- Quota Attainment: {kpis['attainment_pct']:.1f}%
- Win Rate: {kpis['win_rate']:.1f}%
- Open Pipeline: ${kpis['open_pipeline']:,.0f}
_ {ReportGenerator._source_line(citations.get('key_metrics', []))} _

## Top Reps
{top_lines}
_ {ReportGenerator._source_line(citations.get('opportunities', []))} _

## At-Risk Reps
{under_lines}
_ {ReportGenerator._source_line(citations.get('risks', []))} _

## Pipeline Risks
- Pipeline coverage is {kpis['pipeline_coverage']:.2f}x against quota baseline.
- Track stalled opportunities and validate stage progression consistency.

## Coaching Recommendations
1. Weekly 1:1 for each at-risk rep with deal-level action plans.
2. Shadow top rep workflows for qualification and negotiation stages.
3. Enforce activity recency and stage-exit criteria in pipeline reviews.

## Data Quality Notes
{warning_lines}
_ {ReportGenerator._source_line(citations.get('quality', []))} _
"""

    @staticmethod
    def _rep_performance(period: str, audience: str, kpis: dict[str, Any], top_reps: list[dict[str, Any]], under: list[dict[str, Any]], warnings: list[str], citations: dict[str, list[str]]) -> str:
        subject = top_reps[0] if top_reps else (under[0] if under else None)
        subject_name = subject["name"] if subject else "Selected Rep"
        subject_revenue = subject.get("revenue", 0.0) if subject else 0.0
        subject_quota = subject.get("quota", 0.0) if subject else 0.0
        subject_attainment = subject.get("attainment_pct", 0.0) if subject else 0.0
        subject_pipeline = subject.get("open_pipeline", 0.0) if subject else 0.0
        subject_win_rate = subject.get("win_rate", 0.0) if subject else 0.0
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data warnings"

        risk_level = "LOW" if subject_attainment >= 100 else ("MEDIUM" if subject_attainment >= 75 else "HIGH")

        return f"""# Rep Performance Report ({period})

Audience: {audience}
Rep: {subject_name}

## Revenue
- Revenue: ${subject_revenue:,.0f}
- Quota: ${subject_quota:,.0f}
- Quota Attainment: {subject_attainment:.1f}%
_ {ReportGenerator._source_line(citations.get('key_metrics', []))} _

## Win/Loss Analysis
- Win Rate: {subject_win_rate:.1f}%
- Deals Won: {subject.get('deals_won', 0)}
- Deals Lost: {subject.get('deals_lost', 0)}

## Pipeline
- Open Pipeline: ${subject_pipeline:,.0f}
_ {ReportGenerator._source_line(citations.get('summary', []))} _

## Activities
- Activity evidence is derived from deal and stage progress signals.

## Risk Level
- {risk_level}

## Recommendations
1. Focus effort on late-stage deals with clear close plans.
2. Increase qualified pipeline creation if attainment is below target.
3. Improve activity consistency for faster cycle progression.

## Data Quality Notes
{warning_lines}
_ {ReportGenerator._source_line(citations.get('quality', []))} _
"""

    @staticmethod
    def _pipeline_health_report(
        period: str, audience: str, kpis: dict[str, Any],
        pipeline_check: dict[str, Any], weighted: dict[str, Any],
        activity_ratio: dict[str, Any], cycle_days: dict[str, Any],
        top_reps: list[dict[str, Any]], under: list[dict[str, Any]],
        warnings: list[str], citations: dict[str, list[str]],
    ) -> str:
        pipeline_val = pipeline_check.get("value", 0)
        quota_val = kpis.get("total_quota", 1) or 1
        raw_coverage = round(pipeline_val / quota_val, 2)
        weighted_ratio = weighted.get("ratio", 0)
        weighted_val = weighted.get("weighted_pipeline", 0)
        activity = activity_ratio.get("ratio", 0)
        open_deals = activity_ratio.get("open_deals", 0)
        avg_cycle = cycle_days.get("avg_days", 0)
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data quality warnings"

        health = "Healthy" if raw_coverage >= 4.0 and weighted_ratio >= 3.0 else (
            "Watch" if raw_coverage >= 2.5 else "At Risk"
        )

        return f"""# Pipeline Health Report ({period})

Audience: {audience}

## Pipeline Health Status: {health}

## Coverage Summary
| Metric | Value | Benchmark | Status |
|---|---|---|---|
| Raw Pipeline | ${pipeline_val:,.0f} | -- | -- |
| Raw Coverage Ratio | {raw_coverage:.2f}x | >= 4x | {"OK" if raw_coverage >= 4.0 else "LOW"} |
| Weighted Pipeline | ${weighted_val:,.0f} | -- | -- |
| Weighted Coverage Ratio | {weighted_ratio:.2f}x | >= 3x | {"OK" if weighted_ratio >= 3.0 else "LOW"} |
| Total Quota | ${quota_val:,.0f} | -- | -- |

## Deal Engagement
- Open Deals: {open_deals}
- Activity-to-Deal Ratio: {activity:.1f} (benchmark: >= 3.0)
- Avg Sales Cycle: {avg_cycle:.0f} days

## At-Risk Reps (Pipeline Thin)
{chr(10).join([f"- {r['name']}: {r['attainment_pct']:.1f}% attainment, ${r.get('open_pipeline', 0):,.0f} pipeline" for r in under[:5]]) or "- No at-risk reps identified"}

## Recommended Actions
1. {"Increase top-of-funnel activity — raw coverage below 4x." if raw_coverage < 4.0 else "Pipeline volume is healthy."}
2. {"Improve stage quality — weighted coverage below 3x." if weighted_ratio < 3.0 else "Stage quality is adequate."}
3. {"Boost engagement — activity ratio below 3 activities/deal." if activity < 3.0 else "Engagement levels are healthy."}
4. Review all deals with expected_close_date in the past.

## Data Quality Notes
{warning_lines}
"""

    @staticmethod
    def _quota_attainment_report(
        period: str, audience: str, kpis: dict[str, Any],
        top_reps: list[dict[str, Any]], under: list[dict[str, Any]],
        attainment_dist: dict[str, Any], warnings: list[str],
        citations: dict[str, list[str]],
    ) -> str:
        dist_data = attainment_dist.get("data", {})
        counts = dist_data.get("counts", {})
        pcts = dist_data.get("percentages", {})
        total_reps = dist_data.get("total_reps_with_quota", 0)
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data quality warnings"

        health_reps = (counts.get("100_to_120", 0) + counts.get("above_120", 0))
        health_pct = round(health_reps / max(total_reps, 1) * 100, 1)
        at_risk_reps = counts.get("below_50", 0)

        return f"""# Quota Attainment Report ({period})

Audience: {audience}

## Overall Performance
- Company Attainment: {kpis['attainment_pct']:.1f}%
- Reps At or Above 100%: {health_reps} ({health_pct}%) -- benchmark: >= 60%
- Reps Below 50%: {at_risk_reps} -- benchmark: < 10%

## Attainment Distribution
| Tier | Count | % of Reps | Benchmark |
|---|---|---|---|
| Above 120% | {counts.get('above_120', 0)} | {pcts.get('above_120', 0):.1f}% | 10-20% |
| 100-120% | {counts.get('100_to_120', 0)} | {pcts.get('100_to_120', 0):.1f}% | 25-35% |
| 75-100% | {counts.get('75_to_100', 0)} | {pcts.get('75_to_100', 0):.1f}% | 20-30% |
| 50-75% | {counts.get('50_to_75', 0)} | {pcts.get('50_to_75', 0):.1f}% | 10-15% |
| Below 50% | {counts.get('below_50', 0)} | {pcts.get('below_50', 0):.1f}% | < 10% |

## Top Performers
{chr(10).join([f"- {r['name']}: {r['attainment_pct']:.1f}%" for r in top_reps[:5]]) or "- No top performers available"}

## Coaching Queue (Below 75% Attainment)
{chr(10).join([f"- {r['name']}: {r['attainment_pct']:.1f}%" for r in under[:5]]) or "- No reps below threshold"}

## Recommendations
{f"- {at_risk_reps} reps below 50%: review quota calibration and pipeline health" if at_risk_reps > 0 else "- Quota distribution is within benchmark"}
{f"- Only {health_pct:.0f}% of reps at 100%+: investigate systemic quota or territory issues" if health_pct < 60 else "- Healthy share of reps at 100%+ attainment"}

## Data Quality Notes
{warning_lines}
"""

    @staticmethod
    def _arr_bridge_report(
        period: str, audience: str, kpis: dict[str, Any],
        nrr: dict[str, Any], grr: dict[str, Any], arr_growth: dict[str, Any],
        warnings: list[str], citations: dict[str, list[str]],
    ) -> str:
        nrr_val = nrr.get("nrr_pct", 0)
        grr_val = grr.get("grr_pct", 0)
        growth_val = arr_growth.get("arr_growth_pct", 0)
        arr_current = arr_growth.get("arr_current_12m", 0)
        arr_prior = arr_growth.get("arr_prior_12m", 0)
        comps = nrr.get("components", {})
        warning_lines = "\n".join([f"- {w}" for w in sorted(set(warnings))]) or "- No data quality warnings"

        nrr_status = "Excellent" if nrr_val >= 120 else ("Healthy" if nrr_val >= 100 else ("Watch" if nrr_val >= 90 else "At Risk"))
        grr_status = "Healthy" if grr_val >= 85 else ("Watch" if grr_val >= 75 else "At Risk")

        return f"""# ARR Bridge Report ({period})

Audience: {audience}

## ARR Summary
| Metric | Value | Benchmark | Status |
|---|---|---|---|
| Current 12M ARR | ${arr_current:,.0f} | -- | -- |
| Prior 12M ARR | ${arr_prior:,.0f} | -- | -- |
| ARR Growth Rate (YoY) | {growth_val:.1f}% | >= 20% | {"OK" if growth_val >= 20 else "LOW"} |
| Net Revenue Retention (NRR) | {nrr_val:.1f}% | >= 110% | {nrr_status} |
| Gross Revenue Retention (GRR) | {grr_val:.1f}% | >= 85% | {grr_status} |

## ARR Waterfall Components (Estimated)
| Component | Amount |
|---|---|
| Renewal MRR | ${comps.get('mrr_start', 0):,.0f} |
| Expansion | +${comps.get('expansion', 0):,.0f} |
| Contraction | -${abs(comps.get('contraction', 0)):,.0f} |
| Churn | -${abs(comps.get('churn', 0)):,.0f} |

> Note: Waterfall components are approximated. Re-generate with archetype profiles for precise decomposition.

## Recommendations
{f"- GRR {grr_val:.1f}% is below 85% benchmark: investigate churn root causes and customer success gaps." if grr_val < 85 else "- GRR is healthy: focus on expansion to push NRR above 110%."}
{f"- ARR growth {growth_val:.1f}% is below 20% target: review new logo acquisition and expansion programs." if growth_val < 20 else f"- ARR growth is strong at {growth_val:.1f}%."}

## Data Quality Notes
{warning_lines}
"""


# Module-level convenience function so workflow pipeline can call generate_report(db, ...)
async def generate_report(
    db,
    report_type: str = "executive_summary",
    period: str | None = None,
    audience: str = "executive",
    extra_context: dict | None = None,
) -> dict:
    """Module-level wrapper for ReportGenerator.generate_report."""
    from datetime import date
    period = period or date.today().strftime("%Y-%m")
    return await ReportGenerator.generate_report(
        db=db,
        report_type=report_type,
        period=period,
        audience=audience,
    )
