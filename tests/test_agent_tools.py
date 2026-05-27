import pytest

from backend.agent.tools.metric_tools import get_metric_definition, list_metrics
from backend.agent.tools.ml_tools import _resolve_default_quarter_year
from backend.agent.tools.payout_tools import (
    _extract_close_rate_lift_pct,
    _extract_close_rate_target_pct,
    _extract_cycle_days_reduction,
    _extract_pipeline_lift_pct,
    _extract_pipeline_delta_amount,
    _extract_deal_size_lift_pct,
    _extract_requested_rep_name,
    _quota_scaled_effective_pipeline,
)
from backend.agent.tools.rag_tools import retrieve_knowledge_context
import backend.agent.tools.rag_tools as rag_tools_module


def test_list_metrics_structured():
    result = list_metrics()
    assert result["tool_name"] == "list_metrics"
    assert result["status"] in {"success", "warning", "error"}
    assert isinstance(result["data"], list)


def test_get_metric_definition_unknown():
    result = get_metric_definition("does_not_exist")
    assert result["status"] == "error"
    assert result["data"] is None


def test_rag_tool_handles_empty_query():
    result = retrieve_knowledge_context("")
    assert result["status"] == "warning"
    assert result["data"] == []


def test_rag_sources_use_source_document(monkeypatch):
    class StubService:
        def retrieve_context(self, query, top_k=5):
            return [
                {"content": "x", "source_document": "metric_definitions.md", "score": 0.9},
                {"content": "y", "source_document": "sales_glossary.md", "score": 0.8},
            ]

    monkeypatch.setattr(rag_tools_module, "get_rag_service", lambda: StubService())
    result = retrieve_knowledge_context("quota")
    assert "metric_definitions.md" in result["sources"]
    assert "sales_glossary.md" in result["sources"]


def test_parse_close_rate_and_target_variants():
    assert _extract_close_rate_lift_pct("what if close rate improves by 12%") == 12.0
    assert _extract_close_rate_target_pct("set win rate to 38%") == 38.0


def test_parse_cycle_pipeline_and_deal_size_variants():
    assert _extract_cycle_days_reduction("shorten sales cycle by 2 weeks") == 14.0
    assert _extract_pipeline_lift_pct("pipeline up 25%") == 25.0
    assert _extract_pipeline_delta_amount("add $300k to pipeline") == 300000.0
    assert _extract_deal_size_lift_pct("increase average deal size by 15%") == 15.0


def test_extract_requested_rep_name_from_if_statement():
    assert _extract_requested_rep_name("If Alex Johnson hits 60% quota, what bonus do they earn?") == "alex johnson"


def test_extract_requested_rep_name_from_bonus_for_statement():
    assert _extract_requested_rep_name("What bonus for James Tucker at 110% quota?") == "james tucker"


def test_quota_scaled_effective_pipeline_caps_outliers():
    effective, cap = _quota_scaled_effective_pipeline(
        open_pipeline=22_000_000,
        quota=120_000,
        gap_to_target=110_000,
        revenue=11_000,
    )
    assert cap == 660000.0
    assert effective == 660000.0


def test_quota_scaled_effective_pipeline_keeps_normal_values():
    effective, cap = _quota_scaled_effective_pipeline(
        open_pipeline=180_000,
        quota=120_000,
        gap_to_target=30_000,
        revenue=90_000,
    )
    assert cap == 720000.0
    assert effective == 180000.0


def test_resolve_default_quarter_year_prefers_latest_fully_observed_quarter():
    rows = [
        {"year": 2025, "month": 7},
        {"year": 2025, "month": 8},
        {"year": 2025, "month": 9},
        {"year": 2026, "month": 7},
    ]
    assert _resolve_default_quarter_year(rows, quarter=3) == 2025


def test_resolve_default_quarter_year_uses_partial_when_full_not_available():
    rows = [
        {"year": 2026, "month": 7},
        {"year": 2026, "month": 8},
        {"year": 2025, "month": 7},
    ]
    assert _resolve_default_quarter_year(rows, quarter=3) == 2026
