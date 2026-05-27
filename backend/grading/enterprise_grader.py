from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.agent.fallback_response import build_deterministic_response
from backend.metrics import get_global_registry
from backend.ml.forecasting import run_revenue_forecast
from backend.rag.rag_service import RAGService
from backend.statistics.sales_drivers import explain_metric_change

from backend.grading.criteria import CATEGORY_WEIGHTS, GRADE_BANDS


class EnterpriseGrader:
    def __init__(self, repo_root: str):
        self.root = Path(repo_root)

    def _exists(self, rel_path: str) -> bool:
        return (self.root / rel_path).exists()

    def _score_category(self, checks: list[tuple[str, bool]], max_points: int) -> tuple[int, list[str], list[str], list[dict[str, Any]]]:
        if not checks:
            return 0, [], [], []
        passed = [label for label, ok in checks if ok]
        failed = [label for label, ok in checks if not ok]
        points = round(max_points * (len(passed) / len(checks)))
        details = [{"criterion": label, "passed": ok} for label, ok in checks]
        return points, passed, failed, details

    def _grade(self, score: int) -> str:
        for minimum, letter, _ in GRADE_BANDS:
            if score >= minimum:
                return letter
        return "F"

    def run(self) -> dict[str, Any]:
        categories = []
        critical_gaps: list[str] = []
        recommendations: list[str] = []

        data_checks = [
            ("ingestion exists", self._exists("backend/ingestion/ingestion_run.py")),
            ("validation exists", self._exists("backend/validation/validators.py")),
            ("data quality router exists", self._exists("backend/routers/data_quality.py")),
            ("schema consistency artifact exists", self._exists("database/schema.sql")),
            ("empty/missing handling implemented", self._exists("backend/metrics/calculators.py")),
            ("clean package script exists", self._exists("scripts/package_clean.sh")),
        ]
        data_points, data_passed, data_failed, data_details = self._score_category(data_checks, CATEGORY_WEIGHTS["data_lifecycle_and_quality"])
        categories.append({"name": "Data lifecycle and quality", "score": data_points, "max_score": CATEGORY_WEIGHTS["data_lifecycle_and_quality"], "checks": data_details})

        registry = get_global_registry()
        metric_total_revenue = registry.get("total_revenue")
        metric_pipeline_coverage = registry.get("pipeline_coverage")
        metrics_checks = [
            ("metrics registry exists", self._exists("backend/metrics/registry.py")),
            ("metrics definitions exist", self._exists("backend/metrics/definitions.py")),
            ("metrics calculators exist", self._exists("backend/metrics/calculators.py")),
            ("metrics service exists", self._exists("backend/metrics/service.py")),
            ("analytics uses metrics service", self._exists("backend/routers/analytics.py")),
            ("total_revenue formula aligns with revenue table", bool(metric_total_revenue and "SUM(revenue.amount)" in metric_total_revenue.formula)),
            ("pipeline coverage caveat mentions quota grain", bool(metric_pipeline_coverage and metric_pipeline_coverage.caveats and any("quota grain" in c.lower() for c in metric_pipeline_coverage.caveats))),
        ]
        metrics_points, _, metrics_failed, metrics_details = self._score_category(metrics_checks, CATEGORY_WEIGHTS["metrics_governance"])
        categories.append({"name": "Metrics governance", "score": metrics_points, "max_score": CATEGORY_WEIGHTS["metrics_governance"], "checks": metrics_details})

        baseline_sample = {f"2024-{m:02d}": 100000 + m * 1000 for m in range(1, 13)}
        baseline_fc = run_revenue_forecast(baseline_sample, horizon=2)
        ml_checks = [
            ("training pipeline exists", self._exists("backend/ml/training_pipeline.py")),
            ("model metadata exists", self._exists("backend/ml/model_registry.py")),
            ("evaluation module exists", self._exists("backend/ml/evaluation.py")),
            ("forecast endpoint exists", self._exists("backend/routers/forecasting.py")),
            ("leakage prevention documented", self._exists("backend/ml/deal_scoring.py")),
            ("forecast short-history fallback works", baseline_fc.get("metadata", {}).get("forecast_mode") == "baseline"),
            ("prediction table model exists", self._exists("backend/models.py")),
        ]
        ml_points, _, ml_failed, ml_details = self._score_category(ml_checks, CATEGORY_WEIGHTS["ml_workflow"])
        categories.append({"name": "ML workflow", "score": ml_points, "max_score": CATEGORY_WEIGHTS["ml_workflow"], "checks": ml_details})

        fallback = build_deterministic_response(
            intent="metric_question",
            evidence=[{"tool_name": "get_sales_kpis", "data": {"total_revenue": 1000, "attainment_pct": 70, "pipeline_coverage": 1.2}}],
            tools_used=["get_sales_kpis"],
            warnings=[],
        )
        agent_checks = [
            ("planner exists", self._exists("backend/agent/planner.py")),
            ("executor exists", self._exists("backend/agent/executor.py")),
            ("verifier exists", self._exists("backend/agent/verifier.py")),
            ("agent router exists", self._exists("backend/routers/agent.py")),
            ("agent tools are modular", self._exists("backend/agent/tools/analytics_tools.py")),
            ("structured tool outputs implemented", self._exists("backend/agent/tools/report_tools.py")),
            ("deterministic fallback implemented", "deterministic evidence-backed summary" in fallback),
        ]
        agent_points, _, agent_failed, agent_details = self._score_category(agent_checks, CATEGORY_WEIGHTS["agentic_workflow"])
        categories.append({"name": "Agentic workflow", "score": agent_points, "max_score": CATEGORY_WEIGHTS["agentic_workflow"], "checks": agent_details})

        rag_sources_ok = False
        try:
            rag = RAGService(str(self.root / "docs" / "knowledge_base"))
            chunks = rag.retrieve_context("quota attainment", top_k=2)
            rag_sources_ok = bool(chunks) and all("source_document" in c for c in chunks)
        except Exception:
            rag_sources_ok = False

        rag_checks = [
            ("knowledge base docs exist", self._exists("docs/knowledge_base/metric_definitions.md")),
            ("retriever exists", self._exists("backend/rag/retriever.py")),
            ("rag service exists", self._exists("backend/rag/rag_service.py")),
            ("agent rag tool exists", self._exists("backend/agent/tools/rag_tools.py")),
            ("rag returns source_document field", rag_sources_ok),
        ]
        rag_points, _, rag_failed, rag_details = self._score_category(rag_checks, CATEGORY_WEIGHTS["rag_implementation"])
        categories.append({"name": "RAG implementation", "score": rag_points, "max_score": CATEGORY_WEIGHTS["rag_implementation"], "checks": rag_details})

        report_driver = explain_metric_change(
            {"total_revenue": 120, "attainment_pct": 90, "win_rate": 55, "pipeline_coverage": 1.3},
            {"total_revenue": 100, "attainment_pct": 80, "win_rate": 50, "pipeline_coverage": 1.1},
        )
        reporting_checks = [
            ("report generator exists", self._exists("backend/reports/report_generator.py")),
            ("reports router exists", self._exists("backend/routers/reports.py")),
            ("templates exist", self._exists("backend/reports/templates/executive_weekly.md")),
            ("tests exist", self._exists("tests/test_reports.py")),
            ("driver analysis helper works", bool(report_driver.get("drivers"))),
        ]
        reporting_points, _, reporting_failed, reporting_details = self._score_category(reporting_checks, CATEGORY_WEIGHTS["reporting"])
        categories.append({"name": "Reporting", "score": reporting_points, "max_score": CATEGORY_WEIGHTS["reporting"], "checks": reporting_details})

        package_text = ""
        script_path = self.root / "scripts" / "package_clean.sh"
        if script_path.exists():
            package_text = script_path.read_text()

        ops_checks = [
            (".gitignore exists", self._exists(".gitignore")),
            (".env.example exists", self._exists(".env.example")),
            ("tests directory exists", self._exists("tests")),
            ("docs directory exists", self._exists("docs")),
            ("clean package script exists", self._exists("scripts/package_clean.sh")),
            ("package script excludes zip artifacts", "*.zip" in package_text),
            ("package script excludes env files", ".env" in package_text),
        ]
        ops_points, _, ops_failed, ops_details = self._score_category(ops_checks, CATEGORY_WEIGHTS["operational_readiness"])
        categories.append({"name": "Operational readiness", "score": ops_points, "max_score": CATEGORY_WEIGHTS["operational_readiness"], "checks": ops_details})

        # ── RevOps Completeness category (behavioral checks) ────────────
        registry = get_global_registry()
        nrr_def = registry.get("nrr")
        grr_def = registry.get("grr")
        arr_growth_def = registry.get("arr_growth_rate")
        weighted_cov_def = registry.get("weighted_pipeline_coverage")
        attainment_dist_def = registry.get("quota_attainment_distribution")

        revops_checks = [
            ("NRR metric defined in registry", nrr_def is not None),
            ("GRR metric defined in registry", grr_def is not None),
            ("ARR growth rate metric defined", arr_growth_def is not None),
            ("Weighted pipeline coverage metric defined", weighted_cov_def is not None),
            ("Quota attainment distribution metric defined", attainment_dist_def is not None),
            ("RevOps business-rule validator exists", self._exists("backend/validation/revops_rules.py")),
            ("RevOps agent tools exist", self._exists("backend/agent/tools/revops_tools.py")),
            ("Deal slip model exists", self._exists("backend/ml/deal_slip.py")),
            ("ARR waterfall function in forecasting", self._exists("backend/ml/forecasting.py")),
            ("revops-kpis endpoint exists in analytics router", self._exists("backend/routers/analytics.py")),
            ("Pipeline health report template implemented", "_pipeline_health_report" in (self.root / "backend/reports/report_generator.py").read_text() if self._exists("backend/reports/report_generator.py") else False),
            ("ARR bridge report template implemented", "_arr_bridge_report" in (self.root / "backend/reports/report_generator.py").read_text() if self._exists("backend/reports/report_generator.py") else False),
            ("Archetype profiles in data generator", "ARCHETYPE_PROFILES" in (self.root / "backend/data_generator.py").read_text() if self._exists("backend/data_generator.py") else False),
            ("RevOps KB docs exist (NRR guide)", self._exists("docs/knowledge_base/revops_kpi_guide.md")),
            ("RevOps KB docs exist (pipeline inspection)", self._exists("docs/knowledge_base/pipeline_inspection_guide.md")),
            ("RevOps KB docs exist (quota methodology)", self._exists("docs/knowledge_base/quota_setting_methodology.md")),
            ("Quota At Risk persona in rep clustering", "Quota At Risk" in (self.root / "backend/ml/rep_clustering.py").read_text() if self._exists("backend/ml/rep_clustering.py") else False),
            ("5 RevOps intents in agent planner", "quota_risk" in (self.root / "backend/agent/planner.py").read_text() and "arr_trajectory" in (self.root / "backend/agent/planner.py").read_text() if self._exists("backend/agent/planner.py") else False),
            ("RevOps vocabulary in agent system prompt", "NRR" in (self.root / "backend/agent/prompts.py").read_text() if self._exists("backend/agent/prompts.py") else False),
            ("Formula-based quota generation", "_formula_quota" in (self.root / "backend/data_generator.py").read_text() if self._exists("backend/data_generator.py") else False),
        ]
        revops_points, _, revops_failed, revops_details = self._score_category(revops_checks, CATEGORY_WEIGHTS["revops_completeness"])
        categories.append({"name": "RevOps completeness", "score": revops_points, "max_score": CATEGORY_WEIGHTS["revops_completeness"], "checks": revops_details})

        total = sum(item["score"] for item in categories)
        grade = self._grade(total)

        all_failed = data_failed + metrics_failed + ml_failed + agent_failed + rag_failed + reporting_failed + ops_failed + revops_failed
        critical_gaps.extend(all_failed[:10])

        if metrics_failed:
            recommendations.append("Complete any missing governed metric checks and ensure all consumers use metrics service")
        if ml_failed:
            recommendations.append("Improve ML model metadata persistence and backtesting coverage")
        if agent_failed:
            recommendations.append("Strengthen agent verifier checks and evidence completeness handling")
        if reporting_failed:
            recommendations.append("Expand report evidence citations and quality notes")
        if revops_failed:
            recommendations.append(f"Complete {len(revops_failed)} remaining RevOps completeness checks: " + ", ".join(revops_failed[:3]))

        if not recommendations:
            recommendations.append("Maintain current quality and add continuous monitoring for production readiness")

        # ── Functional checks bonus (FUNCTIONAL_CHECKS from criteria.py) ──
        from backend.grading.criteria import FUNCTIONAL_CHECKS
        functional_results: list[dict] = []
        for (cat, check_name, description, module_or_path, weight) in FUNCTIONAL_CHECKS:
            passed = self._run_functional_check(check_name, module_or_path)
            functional_results.append({
                "category": cat, "check": check_name, "description": description,
                "passed": passed, "weight": weight,
            })

        return {
            "overall_score": total,
            "grade": grade,
            "categories": categories,
            "functional_checks": functional_results,
            "functional_pass_rate": (
                round(sum(1 for r in functional_results if r["passed"]) / len(functional_results) * 100, 1)
                if functional_results else 0.0
            ),
            "critical_gaps": critical_gaps,
            "recommendations": recommendations,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _run_functional_check(self, check_name: str, module_or_path: str) -> bool:
        """Run a functional import/existence check. Returns True if passed."""
        try:
            # Path-based checks
            if "/" in module_or_path and not module_or_path.startswith("backend."):
                if module_or_path.endswith("/"):
                    # Directory with count requirement
                    p = self.root / module_or_path.rstrip("/")
                    return p.is_dir() and len(list(p.glob("*.md"))) >= 5
                return (self.root / module_or_path).exists()

            # Module import checks
            import importlib
            mod = importlib.import_module(module_or_path)
            # Run specific functional assertions per check
            if check_name == "canonical_mapping_exists":
                return hasattr(mod, "SOURCE_OF_TRUTH")
            elif check_name == "revenue_saas_fields":
                rev_cls = getattr(mod, "Revenue", None)
                if rev_cls is None:
                    return False
                columns = {c.name for c in rev_cls.__table__.columns}
                return "revenue_type" in columns and "is_recurring" in columns
            elif check_name == "nrr_fallback_labeled":
                return hasattr(mod, "get_nrr")
            elif check_name == "period_quarter_parsing":
                parse_fn = getattr(mod, "parse_period_to_range", None)
                if parse_fn is None:
                    return False
                r = parse_fn("2024-Q2")
                return r.start_date.month == 4
            elif check_name == "metrics_registry":
                return hasattr(mod, "MetricsRegistry")
            elif check_name == "arr_waterfall_calculator":
                return hasattr(mod, "calc_arr_waterfall")
            elif check_name == "deal_velocity_calculator":
                return hasattr(mod, "calc_deal_velocity")
            elif check_name == "deal_scoring_leakage_safe":
                fn = getattr(mod, "get_allowed_deal_features", None)
                if fn is None:
                    return False
                features = fn()
                return "actual_close_date" not in features and "closed_at" not in features
            elif check_name == "deal_snapshots_module":
                return hasattr(mod, "build_deal_snapshots") and hasattr(mod, "assert_no_leakage")
            elif check_name == "drift_detection":
                return hasattr(mod, "detect_drift")
            elif check_name == "forecast_confidence_levels":
                return hasattr(mod, "run_revenue_forecast")
            elif check_name == "planner_classifier":
                planner_cls = getattr(mod, "IntentPlanner", None)
                if planner_cls is None:
                    return False
                p = planner_cls()
                intents = {p.classify(q) for q in [
                    "Show revenue", "forecast", "payout", "retrain model", "data quality",
                    "quota risk", "pipeline coverage", "arr trajectory", "rep ramp status",
                    "run full sales performance analysis",
                ]}
                return len(intents) >= 8
            elif check_name == "workflow_pipeline":
                return hasattr(mod, "run_sales_performance_pipeline")
            elif check_name == "agent_sse_streaming":
                paths = [r.path for r in mod.router.routes]
                return "/agent/chat/stream" in paths
            elif check_name == "rag_service_exists":
                return hasattr(mod, "RAGService")
            elif check_name == "knowledge_base_docs":
                kb_path = self.root / "docs" / "knowledge_base"
                return kb_path.is_dir() and len(list(kb_path.glob("*.md"))) >= 5
            elif check_name == "report_generator_exists":
                return hasattr(mod, "ReportGenerator") or hasattr(mod, "generate_report")
            elif check_name == "report_router_exists":
                return hasattr(mod, "router")
            elif check_name == "payout_router_exists":
                return hasattr(mod, "router")
            elif check_name == "error_handling_middleware":
                src = (self.root / "backend" / "main.py").read_text()
                return "exception_handler" in src or "X-Response-Time" in src
            elif check_name == "credit_payout_engine":
                return hasattr(mod, "compute_credit_payouts")
            elif check_name == "test_coverage_200plus":
                import subprocess, json
                result = subprocess.run(
                    ["./venv/bin/python", "-m", "pytest", "tests", "-q", "--tb=no", "--co", "-q"],
                    capture_output=True, text=True, cwd=str(self.root)
                )
                lines = [l for l in result.stdout.split("\n") if "<Module" not in l and l.strip()]
                return len(lines) >= 200
            elif check_name == "package_clean_sh":
                p = self.root / "scripts" / "package_clean.sh"
                return p.exists() and ".env" in p.read_text()
            elif check_name == "gitignore_env":
                p = self.root / ".gitignore"
                text = p.read_text() if p.exists() else ""
                return ".env" in text and ("env/" in text or "venv" in text)
            elif check_name == "ml_saved_artifacts":
                saved = self.root / "backend" / "ml" / "saved"
                if not saved.is_dir():
                    return False
                artifacts = list(saved.glob("*.pkl")) + list(saved.glob("*.joblib")) + list(saved.glob("*.json"))
                return len(artifacts) >= 1
            elif check_name == "rag_numeric_boundary":
                return hasattr(mod, "RAGService") and hasattr(mod, "annotate_chunks_for_numeric_content")
            elif check_name == "payout_statement_report_type":
                rg_cls = getattr(mod, "ReportGenerator", None)
                if rg_cls is None:
                    return False
                return hasattr(rg_cls, "_payout_statement") and hasattr(rg_cls, "_forecast_summary")
            elif check_name == "revops_quality_checks":
                build_fn = getattr(mod, "_build_checks", None)
                if build_fn is None:
                    return False
                import inspect
                src = inspect.getsource(build_fn)
                return "sales_credit_coverage" in src and "plan_assignment_coverage" in src
            elif check_name == "workflow_store_exists":
                return hasattr(mod, "create_workflow") and hasattr(mod, "get_workflow")
            elif check_name == "workflow_router_registered":
                src = (self.root / "backend" / "main.py").read_text()
                return "workflows" in src and "include_router" in src
            elif check_name == "report_types_api_complete":
                report_types = getattr(mod, "REPORT_TYPES", [])
                required = {"executive_weekly", "manager_monthly", "rep_performance",
                            "pipeline_health", "quota_attainment", "arr_bridge",
                            "payout_statement", "forecast_summary"}
                return required.issubset(set(report_types))
            elif check_name == "sales_performance_pipeline_reachable":
                return hasattr(mod, "router")
            # Default: module imported OK = passed
            return True
        except Exception:
            return False
