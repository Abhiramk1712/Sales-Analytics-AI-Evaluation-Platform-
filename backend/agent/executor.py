"""
Agent executor that calls tools based on planner intent.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from backend.agent.state import AgentState
from backend.agent.tools.analytics_tools import (
    get_pipeline_summary,
    get_rep_performance_summary,
    get_revenue_by_region,
    get_sales_kpis,
    get_team_pipeline_coverage_attainment,
    get_top_reps,
    get_underperforming_reps,
)
from backend.agent.tools.metric_tools import get_metric_definition, list_metrics
from backend.agent.tools.ml_tools import (
    get_deal_risk_summary,
    get_forecast_summary,
    get_rep_clusters_summary,
)
from backend.agent.tools.plan_tools import get_plan_performance_summary, get_plans_rules_catalog
from backend.agent.tools.rag_tools import retrieve_knowledge_context
from backend.agent.tools.report_tools import (
    generate_executive_summary_text,
    generate_manager_summary_text,
    generate_rep_summary_text,
)


from backend.agent.tools.ingestion_tools import discover_sources, check_data_quality, execute_ingestion
from backend.agent.tools.payout_tools import get_payout_summary
from backend.agent.tools.payout_tools import get_rep_quota_bonus_what_if
from backend.agent.tools.pipeline_tools import check_model_readiness, run_ml_pipeline
from backend.agent.tools.revops_tools import (
    get_quota_risk_summary,
    get_pipeline_coverage_check,
    get_pipeline_rescue_what_if,
    get_deal_slip_analysis,
    get_deal_velocity_trends,
    get_arr_trajectory,
    get_rep_ramp_status,
)


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def _extract_pipeline_coverage_between_from_message(message: str) -> tuple[float, float] | None:
    msg = (message or "").lower()
    patterns = [
        r"(?:pipeline\s+coverage|coverage)[^\d]{0,24}(?:between|from)\s*(\d+(?:\.\d+)?)\s*x?\s*(?:and|to|-)\s*(\d+(?:\.\d+)?)\s*x?",
        r"(?:pipeline\s+coverage|coverage)[^\d]{0,24}(\d+(?:\.\d+)?)\s*x?\s*(?:to|-)\s*(\d+(?:\.\d+)?)\s*x?",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if not m:
            continue
        first = _clamp(float(m.group(1)), 0.1, 25.0)
        second = _clamp(float(m.group(2)), 0.1, 25.0)
        return (first, second) if first <= second else (second, first)
    return None


def _extract_min_pipeline_coverage_from_message(message: str) -> float | None:
    between = _extract_pipeline_coverage_between_from_message(message)
    if between:
        return between[0]

    msg = (message or "").lower()
    patterns = [
        r"(?:pipeline\s+coverage|coverage)[^\d]{0,24}(?:>=|>|at\s+least|above|over|minimum(?:\s+of)?|min)\s*(\d+(?:\.\d+)?)\s*x?",
        r"(?:pipeline\s+coverage|coverage)[^\d]{0,24}(\d+(?:\.\d+)?)\s*x\+",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            if "%" in msg[m.end() : m.end() + 2]:
                continue
            return _clamp(float(m.group(1)), 0.1, 25.0)
    return None


def _extract_max_pipeline_coverage_from_message(message: str) -> float | None:
    between = _extract_pipeline_coverage_between_from_message(message)
    if between:
        return between[1]

    msg = (message or "").lower()
    patterns = [
        r"(?:pipeline\s+coverage|coverage)[^\d]{0,24}(?:<=|<|at\s+most|below|under|less\s+than|max(?:imum)?(?:\s+of)?)\s*(\d+(?:\.\d+)?)\s*x?",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            if "%" in msg[m.end() : m.end() + 2]:
                continue
            return _clamp(float(m.group(1)), 0.1, 25.0)
    return None


def _extract_max_attainment_pct_from_message(message: str) -> float | None:
    msg = (message or "").lower()
    patterns = [
        r"(?:quota\s+attainment|attainment)[^\d]{0,24}(?:<=|<|at\s+most|below|under|less\s+than|max(?:imum)?(?:\s+of)?)\s*(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:quota\s+attainment|attainment)[^\d]{0,24}(\d{1,3}(?:\.\d+)?)\s*%\s*(?:or\s+lower|or\s+less|max)",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return _clamp(float(m.group(1)), 0.0, 200.0)
    return None


def _extract_min_attainment_pct_from_message(message: str) -> float | None:
    msg = (message or "").lower()
    patterns = [
        r"(?:quota\s+attainment|attainment)[^\d]{0,24}(?:>=|>|at\s+least|above|over|more\s+than|min(?:imum)?(?:\s+of)?)\s*(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:quota\s+attainment|attainment)[^\d]{0,24}(\d{1,3}(?:\.\d+)?)\s*%\s*(?:or\s+higher|or\s+more|min)",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return _clamp(float(m.group(1)), 0.0, 200.0)

    m = re.search(r"\bor\s+(?:>=|>|at\s+least|above|over|more\s+than)\s*(\d{1,3}(?:\.\d+)?)\s*%", msg)
    if m:
        return _clamp(float(m.group(1)), 0.0, 200.0)

    return None


def _extract_top_n_from_message(message: str) -> int | None:
    msg = (message or "").lower()
    m = re.search(r"\btop\s+(\d{1,2})\b", msg)
    if m:
        return max(1, min(25, int(m.group(1))))
    return None


class ToolExecutor:
    async def execute_for_intent(self, state: AgentState, db_session: Optional[Any] = None) -> AgentState:
        if db_session is None:
            state.warnings.append("Database session missing; cannot execute DB-backed tools")
            return state

        intent = state.intent or "unknown"
        message = state.user_message

        results: list[dict[str, Any]] = []

        if intent == "metric_question":
            results.append(await get_sales_kpis(db_session))
            results.append(await get_pipeline_summary(db_session))
            results.append(await get_revenue_by_region(db_session))
            results.append(retrieve_knowledge_context(message, top_k=3))

        elif intent == "rep_performance":
            results.append(await get_top_reps(db_session, limit=5))
            results.append(await get_underperforming_reps(db_session, threshold_pct=75))

        elif intent == "rep_quota_whatif":
            results.append(await get_rep_quota_bonus_what_if(db_session, message=message))
            if "plan" in message.lower():
                results.append(await get_plan_performance_summary(db_session, message=message))
            results.append(await get_quota_risk_summary(db_session))
            results.append(await get_pipeline_coverage_check(db_session))

        elif intent == "plan_performance_question":
            lower = message.lower()
            wants_catalog = any(
                k in lower
                for k in [
                    "list",
                    "show",
                    "available",
                    "catalog",
                    "all plans",
                    "plans",
                    "rules",
                    "rule",
                ]
            )
            wants_performance = (
                any(k in lower for k in ["performance", "attainment", "quota", "revenue"])
                or bool(re.search(r"\b(fy\s*20\d{2}|20\d{2}|q[1-4]|this quarter|last quarter|this month|last month)\b", lower))
            )

            if wants_catalog:
                results.append(await get_plans_rules_catalog(db_session))

            if wants_performance or not wants_catalog:
                results.append(await get_plan_performance_summary(db_session, message=message))

        elif intent == "definition_question":
            results.append(list_metrics())
            for candidate in ["quota_attainment", "pipeline_coverage", "win_rate", "total_revenue"]:
                if candidate.replace("_", " ") in message.lower() or candidate in message.lower():
                    results.append(get_metric_definition(candidate))
                    break
            results.append(retrieve_knowledge_context(message, top_k=5))

        elif intent == "forecast_question":
            results.append(await get_forecast_summary(db_session, message=message))
            results.append(await get_deal_risk_summary(db_session))

        elif intent == "business_diagnostic_question":
            lower = message.lower()
            results.append(await get_sales_kpis(db_session))
            results.append(await get_pipeline_summary(db_session))
            results.append(await get_forecast_summary(db_session, message=message))
            results.append(await get_quota_risk_summary(db_session))
            results.append(await get_pipeline_coverage_check(db_session))
            results.append(await get_deal_slip_analysis(db_session))
            if any(k in lower for k in ["velocity", "cycle", "trend", "momentum", "outlook"]):
                results.append(await get_deal_velocity_trends(db_session, months=6))
            results.append(await get_top_reps(db_session, limit=5))
            results.append(await get_underperforming_reps(db_session, threshold_pct=80))
            results.append(await get_revenue_by_region(db_session))
            results.append(retrieve_knowledge_context(message, top_k=3))

        elif intent == "anomaly_question":
            results.append(await get_sales_kpis(db_session))
            results.append(await get_deal_risk_summary(db_session))

        elif intent == "report_request":
            results.append(await get_sales_kpis(db_session))
            lower = message.lower()
            if "manager" in lower:
                results.append(await generate_manager_summary_text(db_session, period="this month"))
            elif "rep" in lower:
                results.append(await generate_rep_summary_text(db_session, period="this month"))
            else:
                results.append(await generate_executive_summary_text(db_session, period="this month"))
            results.append(retrieve_knowledge_context(message, top_k=5))

        elif intent == "general_sales_question":
            results.append(await get_sales_kpis(db_session))
            results.append(await get_top_reps(db_session, limit=3))
            lower = message.lower()
            if any(k in lower for k in ["summary", "overview"]):
                results.append(await generate_executive_summary_text(db_session, period="this month"))
            results.append(retrieve_knowledge_context(message, top_k=3))

        else:
            deferred_intents = {
                "payout_request",
                "data_quality_request",
                "model_training_request",
                "ingestion_request",
                "pipeline_request",
                "quota_risk",
                "pipeline_coverage_check",
                "deal_slip_analysis",
                "pipeline_rescue_whatif",
                "deal_velocity_trends",
                "plan_performance_question",
                "arr_trajectory",
                "rep_ramp_status",
                "sales_performance_workflow",
            }
            if intent not in deferred_intents:
                state.warnings.append("Unable to classify intent confidently")
                results.append(list_metrics())
                results.append(retrieve_knowledge_context(message, top_k=5))

        # ── New intents: data ops and payout ──────────────────────────────
        if intent == "payout_request":
            results.append(await get_payout_summary(db_session))
            if any(k in message.lower() for k in ["quota", "bonus", "target", "attain", "hit"]):
                results.append(await get_rep_quota_bonus_what_if(db_session, message=message))
            results.append(await get_sales_kpis(db_session))

        elif intent == "data_quality_request":
            results.append(await get_sales_kpis(db_session))
            # Surface quality awareness via KPIs and model readiness
            results.append(await check_model_readiness(db_session))

        elif intent == "model_training_request":
            results.append(await check_model_readiness(db_session))
            results.append(await run_ml_pipeline(db_session))
            results.append(await get_forecast_summary(db_session, message=message))

        elif intent == "ingestion_request":
            # For agent-driven ingestion the source dir must be in message context;
            # we surface readiness + existing KPIs so the LLM can craft a useful response.
            results.append(await get_sales_kpis(db_session))
            results.append(await check_model_readiness(db_session))
            state.pipeline_status = "planned"
            state.requires_confirmation = True
            state.confirmation_prompt = (
                "To proceed with ingestion, please provide: source directory or file path, "
                "company name, and load mode (full_reload / upsert / append)."
            )

        elif intent == "pipeline_request":
            # End-to-end pipeline: surface readiness check first, then the LLM will guide the user.
            results.append(await check_model_readiness(db_session))
            results.append(await get_payout_summary(db_session))
            results.append(await get_sales_kpis(db_session))
            state.pipeline_status = "planned"
            state.requires_confirmation = True
            state.confirmation_prompt = (
                "Ready to run the full pipeline. Please confirm: (1) source directory, "
                "(2) company name, (3) load mode (full_reload / upsert / append), "
                "(4) whether to reset the database."
            )

        # ── RevOps intents ─────────────────────────────────────────────────
        elif intent == "quota_risk":
            results.append(await get_quota_risk_summary(db_session))
            results.append(await get_sales_kpis(db_session))

        elif intent == "pipeline_coverage_check":
            lower = message.lower()
            if "team" in lower and any(k in lower for k in ["quota", "attainment", "coverage", "pipeline"]):
                min_cov = _extract_min_pipeline_coverage_from_message(message)
                max_cov = _extract_max_pipeline_coverage_from_message(message)
                max_att = _extract_max_attainment_pct_from_message(message)
                min_att = _extract_min_attainment_pct_from_message(message)
                top_n = _extract_top_n_from_message(message)

                if min_cov is None and max_cov is None:
                    if top_n and "coverage" in lower:
                        min_cov = 0.0
                    else:
                        min_cov = 4.0

                if max_att is None and min_att is None:
                    max_att = 80.0

                attainment_logic = "or" if (max_att is not None and min_att is not None and " or " in lower) else "and"
                limit = top_n or 10
                sort_by = "coverage_desc" if top_n else "match_priority"

                results.append(
                    await get_team_pipeline_coverage_attainment(
                        db_session,
                        min_pipeline_coverage=min_cov,
                        max_pipeline_coverage=max_cov,
                        max_attainment_pct=max_att,
                        min_attainment_pct=min_att,
                        attainment_logic=attainment_logic,
                        sort_by=sort_by,
                        limit=limit,
                    )
                )
            results.append(await get_pipeline_coverage_check(db_session))
            results.append(await get_sales_kpis(db_session))

        elif intent == "deal_slip_analysis":
            results.append(await get_deal_slip_analysis(db_session))
            results.append(await get_pipeline_coverage_check(db_session))

        elif intent == "pipeline_rescue_whatif":
            results.append(await get_pipeline_rescue_what_if(db_session, message=message))
            results.append(await get_pipeline_coverage_check(db_session))
            results.append(await get_deal_slip_analysis(db_session))

        elif intent == "deal_velocity_trends":
            results.append(await get_deal_velocity_trends(db_session, months=6))
            results.append(await get_pipeline_coverage_check(db_session))

        elif intent == "arr_trajectory":
            results.append(await get_arr_trajectory(db_session))
            results.append(await get_sales_kpis(db_session))

        elif intent == "rep_ramp_status":
            results.append(await get_rep_ramp_status(db_session))
            results.append(await get_quota_risk_summary(db_session))

        elif intent == "sales_performance_workflow":
            from backend.agent.workflows.sales_performance_pipeline import run_sales_performance_pipeline
            # Extract optional period from message (e.g., "Q2 2024", "last month", "this month")
            period_match = re.search(r"(Q[1-4]\s*\d{4}|this month|last month|last quarter|this quarter|ytd|\d{4})", message, re.IGNORECASE)
            period = period_match.group(0) if period_match else "this quarter"
            pipeline_result = await run_sales_performance_pipeline(db=db_session, period=period)
            results.append({
                "tool_name": "sales_performance_pipeline",
                "data": pipeline_result,
                "warnings": [s["error"] for s in pipeline_result.get("steps", {}).values() if not s.get("ok") and s.get("error")],
            })

        state.evidence_results = results
        state.tools_called = [r.get("tool_name", "unknown") for r in results]
        state.evidence = {r.get("tool_name", "unknown"): r.get("data") for r in results}
        for result in results:
            state.warnings.extend(result.get("warnings", []))

        return state
