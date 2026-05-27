CATEGORY_WEIGHTS = {
    # A: Data lifecycle & quality (canonical models, ingestion, NRR/GRR accuracy)
    "data_lifecycle_and_quality": 15,
    # B: Metrics governance (evidence-backed KPIs, fallback labeling, no invented numbers)
    "metrics_governance": 15,
    # C: ML workflow (deal scoring leakage safety, forecasting hardening, drift detection)
    "ml_workflow": 12,
    # D: Agentic workflow (planner/executor/verifier, workflow orchestration, SSE streaming)
    "agentic_workflow": 15,
    # E: RAG boundary discipline (definition-only RAG, source labeling, correct boundaries)
    "rag_implementation": 8,
    # F: Reporting & RevOps completeness (8 report types, real metrics, payout breakdown)
    "reporting": 10,
    # G: Operational readiness (error handling, period parsing, packaging, docs, tests)
    "operational_readiness": 15,
    # Legacy category preserved for backward compatibility
    "revops_completeness": 10,
}

GRADE_BANDS = [
    (90, "A", "enterprise-ready demo/pilot candidate"),
    (80, "B", "strong, minor gaps"),
    (70, "C", "functional but not enterprise-ready"),
    (60, "D", "partial implementation"),
    (0, "F", "incomplete/high risk"),
]

# Functional checks used by enterprise_grader.py
# Each check: (category, check_name, description, required_module_or_path, weight)
FUNCTIONAL_CHECKS = [
    # A — Data lifecycle
    ("data_lifecycle_and_quality", "canonical_mapping_exists",
     "backend/transformations/canonical_mapping.py exists and exports SOURCE_OF_TRUTH",
     "backend.transformations.canonical_mapping", 3),
    ("data_lifecycle_and_quality", "revenue_saas_fields",
     "Revenue model has revenue_type, contract_term_months, is_recurring fields",
     "backend.models.Revenue", 3),
    ("data_lifecycle_and_quality", "nrr_fallback_labeled",
     "get_nrr() returns fallback_mode flag and [FALLBACK] label when revenue_type absent",
     "backend.metrics.calculators", 3),
    ("data_lifecycle_and_quality", "period_quarter_parsing",
     "parse_period_to_range supports YYYY-Q1..Q4 format",
     "backend.utils.date_ranges", 3),
    ("data_lifecycle_and_quality", "data_quality_router",
     "Data quality router exists and has /data-quality/summary endpoint",
     "backend.routers.data_quality", 3),

    # B — Metrics governance
    ("metrics_governance", "metrics_registry",
     "MetricsRegistry exists with list_all() and get() methods",
     "backend.metrics.registry", 3),
    ("metrics_governance", "arr_waterfall_calculator",
     "calc_arr_waterfall function exists in calculators",
     "backend.metrics.calculators", 3),
    ("metrics_governance", "deal_velocity_calculator",
     "calc_deal_velocity function exists in calculators",
     "backend.metrics.calculators", 3),
    ("metrics_governance", "quota_attainment_distribution",
     "get_quota_attainment_distribution exists",
     "backend.metrics.calculators", 3),
    ("metrics_governance", "evidence_citations_in_reports",
     "ReportGenerator returns evidence_citations in output",
     "backend.reports.report_generator", 3),

    # C — ML workflow
    ("ml_workflow", "deal_scoring_leakage_safe",
     "DealScorer uses get_allowed_deal_features() excluding terminal stages",
     "backend.ml.deal_scoring", 4),
    ("ml_workflow", "deal_snapshots_module",
     "backend/features/deal_snapshots.py exists with build_deal_snapshots()",
     "backend.features.deal_snapshots", 4),
    ("ml_workflow", "drift_detection",
     "detect_drift() exists in ml/evaluation.py",
     "backend.ml.evaluation", 2),
    ("ml_workflow", "forecast_confidence_levels",
     "run_revenue_forecast returns confidence field (high/medium/low)",
     "backend.ml.forecasting", 2),

    # D — Agentic workflow
    ("agentic_workflow", "planner_classifier",
     "IntentPlanner.classify() covers ≥10 intent types",
     "backend.agent.planner", 5),
    ("agentic_workflow", "workflow_pipeline",
     "sales_performance_pipeline.py exists with run_sales_performance_pipeline()",
     "backend.agent.workflows.sales_performance_pipeline", 5),
    ("agentic_workflow", "agent_sse_streaming",
     "/agent/chat/stream endpoint exists in agent router",
     "backend.routers.agent", 5),

    # E — RAG
    ("rag_implementation", "rag_service_exists",
     "backend/rag/rag_service.py exists",
     "backend.rag.rag_service", 4),
    ("rag_implementation", "knowledge_base_docs",
     "docs/knowledge_base/ has ≥5 markdown documents",
     "docs/knowledge_base", 4),

    # F — Reporting
    ("reporting", "report_generator_exists",
     "ReportGenerator supports executive_summary report type",
     "backend.reports.report_generator", 3),
    ("reporting", "report_router_exists",
     "Reports router exists",
     "backend.routers.reports", 3),
    ("reporting", "payout_router_exists",
     "Payout router with SPIFF/clawback endpoints exists",
     "backend.routers.payout", 4),

    # G — Operational readiness
    ("operational_readiness", "error_handling_middleware",
     "main.py has global exception handler with structured JSON responses",
     "backend.main", 5),
    ("operational_readiness", "credit_payout_engine",
     "credit_payout_engine.py exists with compute_credit_payouts()",
     "backend.payout.credit_payout_engine", 5),
    ("operational_readiness", "test_coverage_200plus",
     "Test suite has ≥200 passing tests",
     "tests/", 5),
    ("operational_readiness", "package_clean_sh",
     "scripts/package_clean.sh exists and excludes .env",
     "scripts/package_clean.sh", 5),
    ("operational_readiness", "gitignore_env",
     ".gitignore excludes .env and env/ directories",
     ".gitignore", 5),

    # H — Functional runtime checks (file/module presence with behavioral assertions)
    ("operational_readiness", "ml_saved_artifacts",
     "backend/ml/saved/ directory exists with at least one model artifact",
     "backend/ml/saved/", 4),
    ("operational_readiness", "rag_numeric_boundary",
     "RAGService.retrieve_context_annotated() exists for numeric-claim flagging",
     "backend.rag.rag_service", 4),
    ("operational_readiness", "payout_statement_report_type",
     "ReportGenerator supports payout_statement and forecast_summary report types",
     "backend.reports.report_generator", 4),
    ("operational_readiness", "revops_quality_checks",
     "data_quality router includes sales_credit_coverage check",
     "backend.routers.data_quality", 4),

    # I — Phase 12/13 new checks
    ("operational_readiness", "workflow_store_exists",
     "backend/workflows/store.py exists with create_workflow and get_workflow functions",
     "backend.workflows.store", 4),
    ("operational_readiness", "workflow_router_registered",
     "workflows router registered in main.py",
     "backend.main", 3),
    ("reporting", "report_types_api_complete",
     "/reports/types endpoint returns all 8 canonical report types",
     "backend.reports.types", 5),
    ("agentic_workflow", "sales_performance_pipeline_reachable",
     "run_sales_performance_pipeline callable from workflows router",
     "backend.routers.workflows", 4),
]

