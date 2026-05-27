from backend.agent.answer_quality import compute_answer_quality


def test_answer_quality_shape_and_ranges():
    quality = compute_answer_quality(
        intent="metric_question",
        tools_used=["get_sales_kpis", "get_pipeline_summary"],
        evidence_results=[
            {
                "tool_name": "get_sales_kpis",
                "status": "success",
                "data": {"total_revenue": 1000, "generated_at": "2026-05-01T00:00:00Z"},
                "warnings": [],
            },
            {
                "tool_name": "get_pipeline_summary",
                "status": "warning",
                "data": {"pipeline_coverage": 1.2},
                "warnings": ["pipeline is thin"],
            },
        ],
        warnings=["pipeline is thin"],
        verified=True,
        reply="Grounded summary",
    )

    assert 0 <= quality["score"] <= 100
    assert quality["level"] in {"high", "medium", "low"}
    dims = quality["dimensions"]
    assert 0 <= dims["coverage"]["score"] <= 100
    assert 0 <= dims["confidence"]["score"] <= 100
    assert 0 <= dims["freshness"]["score"] <= 100
    assert quality["calibration"]["version"] == "v2-business-priority"


def test_answer_quality_is_low_for_unverified_insufficient_reply():
    quality = compute_answer_quality(
        intent="unknown",
        tools_used=[],
        evidence_results=[],
        warnings=["No tools were called to gather evidence"],
        verified=False,
        reply="Insufficient data available to answer this confidently.",
    )

    assert quality["level"] == "low"
    assert quality["score"] < 60


def test_answer_quality_rag_is_capped_to_medium_or_low_confidence():
    quality = compute_answer_quality(
        intent="definition_question",
        tools_used=["retrieve_knowledge_context"],
        evidence_results=[
            {
                "tool_name": "retrieve_knowledge_context",
                "status": "success",
                "data": [{"content": "definition", "generated_at": "2025-01-01T00:00:00Z"}],
                "warnings": [],
            }
        ],
        warnings=["Answered via RAG fallback — no live tool data available"],
        verified=False,
        reply="Based on knowledge base definitions and methodology",
        used_rag=True,
    )

    assert quality["dimensions"]["confidence"]["signals"]["used_rag"] is True
    assert quality["dimensions"]["confidence"]["score"] <= 58


def test_answer_quality_rewards_source_diversity():
    quality_low_sources = compute_answer_quality(
        intent="business_diagnostic_question",
        tools_used=["a", "b", "c", "d"],
        evidence_results=[
            {"tool_name": "a", "status": "success", "data": {"x": 1}, "sources": ["revenue"]},
            {"tool_name": "b", "status": "success", "data": {"x": 2}, "sources": ["revenue"]},
            {"tool_name": "c", "status": "success", "data": {"x": 3}, "sources": ["revenue"]},
            {"tool_name": "d", "status": "success", "data": {"x": 4}, "sources": ["revenue"]},
        ],
        warnings=[],
        verified=True,
        reply="Grounded",
    )

    quality_high_sources = compute_answer_quality(
        intent="business_diagnostic_question",
        tools_used=["a", "b", "c", "d"],
        evidence_results=[
            {"tool_name": "a", "status": "success", "data": {"x": 1}, "sources": ["revenue"]},
            {"tool_name": "b", "status": "success", "data": {"x": 2}, "sources": ["deals"]},
            {"tool_name": "c", "status": "success", "data": {"x": 3}, "sources": ["quotas"]},
            {"tool_name": "d", "status": "success", "data": {"x": 4}, "sources": ["ml_predictions"]},
        ],
        warnings=[],
        verified=True,
        reply="Grounded",
    )

    assert quality_high_sources["dimensions"]["coverage"]["signals"]["source_count"] > quality_low_sources["dimensions"]["coverage"]["signals"]["source_count"]
    assert quality_high_sources["dimensions"]["coverage"]["score"] >= quality_low_sources["dimensions"]["coverage"]["score"]


def test_answer_quality_penalizes_stale_signals():
    old_quality = compute_answer_quality(
        intent="forecast_question",
        tools_used=["get_forecast_summary"],
        evidence_results=[
            {
                "tool_name": "get_forecast_summary",
                "status": "success",
                "data": {"predicted_at": "2020-01-01T00:00:00Z", "accuracy_backtest": {"status": "ok", "mape": 9.1}},
                "sources": ["ml_predictions"],
            }
        ],
        warnings=[],
        verified=True,
        reply="Forecast summary",
    )

    assert old_quality["dimensions"]["freshness"]["score"] <= 35
