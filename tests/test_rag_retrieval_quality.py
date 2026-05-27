"""
tests/test_rag_retrieval_quality.py
=====================================
Tests for hybrid RAG retriever: alias expansion, heading boost, source prioritization.
"""
from __future__ import annotations

import pytest

from backend.rag.retriever import Retriever, _expand_query, QUERY_ALIASES


class TestQueryExpansion:
    def test_expands_attainment_synonym(self):
        expanded = _expand_query("What is quota attainment?")
        assert "attainment" in expanded
        assert "quota achieved" in expanded or "target achievement" in expanded

    def test_expands_payout_synonym(self):
        expanded = _expand_query("How is payout calculated?")
        assert "commission" in expanded or "compensation" in expanded

    def test_no_expansion_for_generic(self):
        expanded = _expand_query("Hello world")
        assert expanded == "Hello world"

    def test_alias_dict_has_key_entries(self):
        assert "quota attainment" in QUERY_ALIASES
        assert "pipeline coverage" in QUERY_ALIASES
        assert "payout" in QUERY_ALIASES
        assert "nrr" in QUERY_ALIASES


class TestRetriever:
    @pytest.fixture
    def chunks(self):
        return [
            {
                "content": "Quota attainment is revenue divided by quota target times 100.",
                "source_document": "metric_definitions.md",
                "heading": "Quota Attainment",
            },
            {
                "content": "Pipeline coverage is open pipeline divided by quota.",
                "source_document": "revops_kpi_guide.md",
                "heading": "Pipeline Coverage",
            },
            {
                "content": "Payout is calculated based on commission rate and attainment tier.",
                "source_document": "payout_methodology.md",
                "heading": "Commission Calculation",
            },
            {
                "content": "Forecast uses SARIMAX model for revenue prediction.",
                "source_document": "forecasting_assumptions.md",
                "heading": "Forecasting Method",
            },
            {
                "content": "Deal stage hygiene: expected close date should be realistic.",
                "source_document": "pipeline_inspection_guide.md",
                "heading": "Stage Hygiene",
            },
        ]

    def test_retrieves_relevant_chunk(self, chunks):
        r = Retriever(chunks)
        results = r.retrieve("what is quota attainment", top_k=3)
        assert len(results) > 0
        top_sources = [res["source_document"] for res in results]
        assert "metric_definitions.md" in top_sources

    def test_payout_query_hits_methodology(self, chunks):
        r = Retriever(chunks)
        results = r.retrieve("how is commission calculated", top_k=3)
        assert any("payout_methodology" in res.get("source_document", "") for res in results)

    def test_empty_chunks_returns_empty(self):
        r = Retriever([])
        results = r.retrieve("what is NRR", top_k=3)
        assert results == []

    def test_empty_query_returns_empty(self, chunks):
        r = Retriever(chunks)
        results = r.retrieve("", top_k=3)
        assert results == []

    def test_results_have_score(self, chunks):
        r = Retriever(chunks)
        results = r.retrieve("pipeline coverage", top_k=2)
        for res in results:
            assert "score" in res
            assert isinstance(res["score"], float)

    def test_results_have_source_document(self, chunks):
        r = Retriever(chunks)
        results = r.retrieve("forecast", top_k=2)
        for res in results:
            assert "source_document" in res
