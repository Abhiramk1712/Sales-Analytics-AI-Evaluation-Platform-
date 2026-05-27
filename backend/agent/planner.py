"""
Agent intent classification and planning.
"""
from __future__ import annotations

import re

from backend.agent.state import AgentState
from backend.metrics import get_global_registry


class IntentPlanner:
    def __init__(self) -> None:
        self.metrics_registry = get_global_registry()

    def classify(self, message: str) -> str:
        msg = (message or "").strip().lower()
        if not msg:
            return "unknown"

        metric_names = [m.name.replace("_", " ") for m in self.metrics_registry.list_all()]

        # Data operations — check before generic keywords
        if any(k in msg for k in ["ingest", "upload", "load data", "import data", "load csv", "load pdf", "load file"]):
            return "ingestion_request"

        if any(k in msg for k in ["run pipeline", "end to end", "end-to-end", "automate pipeline", "process data", "full pipeline", "automated workflow", "run full sales", "sales performance pipeline", "full analysis", "sales performance analysis", "analyze this month", "full revops", "revops report", "end-to-end sales", "full workflow", "analyze sales performance", "generate full revops", "run end-to-end", "full sales tracker", "sales tracker pipeline"]):
            return "sales_performance_workflow"

        what_if_trigger = (
            "what if" in msg
            or msg.startswith("if ")
            or bool(re.search(r"\bif\b.+\b(then|how|would|impact|change)\b", msg))
            or "scenario" in msg
            or "sensitivity" in msg
        )

        # Org-level pipeline rescue scenarios should not be routed to
        # rep-specific quota what-if logic.
        rescue_signals = (
            "rescu" in msg
            or "at-risk deal" in msg
            or "at risk deal" in msg
            or "slipping" in msg
            or "slip" in msg
            or bool(re.search(r"\btop\s+\d{1,2}\s+(?:at[-\s]?risk\s+)?deals?\b", msg))
            or "prioritize" in msg
        )
        if what_if_trigger and "coverage" in msg and rescue_signals:
            return "pipeline_rescue_whatif"

        what_if_keywords = [
            "quota",
            "attainment",
            "bonus",
            "payout",
            "close rate",
            "win rate",
            "conversion",
            "sales cycle",
            "cycle",
            "pipeline",
            "deal size",
            "asp",
            "slip",
            "slippage",
            "revenue",
            "forecast",
        ]
        if what_if_trigger and any(k in msg for k in what_if_keywords):
            return "rep_quota_whatif"

        if (
            "plan" in msg
            and any(k in msg for k in ["performance", "attainment", "quota", "fy", "fiscal year"])
            and "what if" not in msg
        ):
            return "plan_performance_question"

        if (
            any(k in msg for k in ["plan", "plans", "rule", "rules", "comp plan", "compensation plan"])
            and any(k in msg for k in ["list", "show", "available", "catalog", "all", "plan", "rule"])
            and "what if" not in msg
            and "data quality" not in msg
        ):
            return "plan_performance_question"

        if (
            (
                "what if" in msg
                and any(
                    k in msg
                    for k in [
                        "quota",
                        "attainment",
                        "bonus",
                        "payout",
                        "close rate",
                        "win rate",
                        "sales cycle",
                        "cycle",
                        "slip",
                        "slippage",
                    ]
                )
            )
            or (
                "quota" in msg
                and any(
                    k in msg
                    for k in [
                        "bonus",
                        "hits quota",
                        "hit quota",
                        "reach quota",
                        "quota target",
                        "attain quota",
                        "attain their quota",
                        "steps",
                        "what should",
                        "what can",
                        "how can",
                    ]
                )
            )
        ):
            return "rep_quota_whatif"

        if any(k in msg for k in ["payout", "commission", "compensation", "calculate pay", "sales pay", "incentive"]):
            return "payout_request"

        if any(k in msg for k in ["retrain", "train model", "update forecast", "update model", "fit model", "run training", "ml model", "machine learning model", "model training"]):
            return "model_training_request"

        if any(k in msg for k in ["data quality", "data validation", "quality check", "validation status", "data issues"]):
            return "data_quality_request"

        business_diag_markers = [
            "business performance",
            "company performance",
            "business health",
            "company health",
            "overall performance",
            "performance snapshot",
            "business outlook",
            "performance outlook",
            "business insights",
            "executive insights",
            "sales health",
            "go-to-market health",
            "gtm health",
            "where are we at risk",
            "how is the business doing",
            "how are we doing",
            "diagnose performance",
        ]
        has_business_subject = any(k in msg for k in ["business", "company", "sales org", "gtm", "go to market"])
        has_business_diag = any(k in msg for k in business_diag_markers)
        if (
            has_business_diag
            or (
                has_business_subject
                and any(k in msg for k in ["health", "performance", "outlook", "risks", "insights", "diagnostic"])
                and not any(k in msg for k in ["summary", "report", "what if", "plan performance"])
            )
        ):
            return "business_diagnostic_question"

        # ── RevOps intents (must be checked before generic metric/forecast) ──
        if any(k in msg for k in ["quota risk", "quota at risk", "at-risk reps", "reps at risk", "missing quota", "behind on quota"]):
            return "quota_risk"

        if (
            any(k in msg for k in ["team", "teams"])
            and "coverage" in msg
            and any(k in msg for k in ["attainment", "quota"])
        ):
            return "pipeline_coverage_check"

        if any(k in msg for k in ["pipeline coverage", "pipeline health", "pipeline check", "coverage ratio", "3x", "4x coverage", "pipeline vs quota"]):
            return "pipeline_coverage_check"

        if any(k in msg for k in ["deal slip", "slipping deals", "overdue deals", "deals at risk", "late deals", "missed close date", "slip risk", "overdue or slipping", "deals overdue"]):
            return "deal_slip_analysis"
        if "deal" in msg and ("slipping" in msg or "at risk of slipping" in msg or "slip" in msg):
            return "deal_slip_analysis"

        if any(k in msg for k in ["deal velocity", "sales velocity", "velocity trend", "velocity trends", "deal cycle trend", "cycle time trend"]):
            return "deal_velocity_trends"

        if any(k in msg for k in ["arr trajectory", "arr growth", "arr trend", "nrr", "grr", "net revenue retention", "gross revenue retention", "arr bridge", "revenue waterfall", "arr waterfall"]):
            return "arr_trajectory"

        if any(k in msg for k in ["ramp status", "rep ramp", "ramping reps", "new reps", "ramp period", "ramp schedule", "time to ramp", "still ramping", "ramps", "ramp check"]):
            return "rep_ramp_status"
        # ─────────────────────────────────────────────────────────────────────

        if any(p in msg for p in ["what does", "definition", "define", "meaning", "how is"]) and any(m in msg for m in metric_names):
            return "definition_question"

        if "what is quota attainment" in msg or "what does quota attainment mean" in msg:
            return "definition_question"

        if any(k in msg for k in ["forecast", "projection", "predict"]):
            return "forecast_question"

        if any(k in msg for k in ["anomaly", "outlier", "spike", "unusual"]):
            return "anomaly_question"

        if any(k in msg for k in [
            "business summary",
            "sales summary",
            "company summary",
            "summary of the company",
            "company overview",
            "business overview",
            "sales overview",
        ]) or ("summary" in msg and any(k in msg for k in ["business", "sales", "company"])):
            return "report_request"

        if any(k in msg for k in ["report", "executive summary", "manager summary", "rep summary"]):
            return "report_request"

        if any(k in msg for k in ["top reps", "underperforming", "rep performance", "which reps", "sales rep"]):
            return "rep_performance"

        if any(m in msg for m in metric_names) or any(k in msg for k in ["quota", "revenue", "win rate", "pipeline", "attainment"]):
            return "metric_question"

        if any(k in msg for k in ["sales", "crm", "deals", "pipeline"]):
            return "general_sales_question"

        # For unknown intents that have sales-adjacent keywords, route to RAG
        if any(k in msg for k in [
            "how", "what", "explain", "describe", "why", "when", "who", "where",
            "methodology", "definition", "formula", "calculation", "glossary",
        ]):
            return "definition_question"

        return "unknown"

    def plan(self, message: str) -> AgentState:
        state = AgentState(user_message=message)
        state.intent = self.classify(message)
        return state
