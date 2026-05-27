"""
tests/test_audit_trace.py
===========================
Tests for MetricTrace audit trail and no-hallucination guard.
"""
from __future__ import annotations

import pytest

from backend.audit.trace import (
    MetricTrace,
    extract_numeric_claims,
    verify_response_against_evidence,
)


class TestMetricTrace:
    def test_basic_trace(self):
        t = MetricTrace("total_revenue")
        t.set_value(125_000.0)
        t.set_formula("SUM(revenue.amount) WHERE date IN 2024-01")
        t.set_period("2024-01")
        t.add_source("revenue", row_count=450)
        d = t.to_dict()
        assert d["metric_name"] == "total_revenue"
        assert d["value"] == 125_000.0
        assert d["formula"].startswith("SUM")
        assert d["source_tables"][0]["table"] == "revenue"
        assert d["source_tables"][0]["row_count"] == 450
        assert d["fallback_mode"] is False
        assert d["confidence"] == 1.0

    def test_fallback_sets_mode(self):
        t = MetricTrace("nrr")
        t.set_fallback("revenue_type field missing", confidence=0.6)
        d = t.to_dict()
        assert d["fallback_mode"] is True
        assert d["confidence"] == 0.6
        assert "revenue_type" in d["fallback_reason"]

    def test_warning_deduplication(self):
        t = MetricTrace("win_rate")
        t.add_warning("No closed-lost deals found")
        t.add_warning("No closed-lost deals found")
        assert len(t.warnings) == 1

    def test_confidence_clamped(self):
        t = MetricTrace("pipeline")
        t.set_confidence(2.5)
        assert t.confidence == 1.0
        t.set_confidence(-0.1)
        assert t.confidence == 0.0

    def test_chaining(self):
        t = MetricTrace("arr")
        d = (
            t.set_value(1_200_000)
            .set_formula("SUM(arr)")
            .set_period("2024-Q1")
            .add_source("monthly_finance")
            .add_warning("partial data")
            .to_dict()
        )
        assert d["value"] == 1_200_000


class TestExtractNumericClaims:
    def test_dollar_amount(self):
        claims = extract_numeric_claims("Revenue was $125,000 last month.")
        assert any("125" in c for c in claims)

    def test_percentage(self):
        claims = extract_numeric_claims("Attainment is 87.5% this quarter.")
        assert any("87.5" in c for c in claims)

    def test_no_numbers(self):
        claims = extract_numeric_claims("No numbers here.")
        assert claims == []

    def test_large_plain_number(self):
        claims = extract_numeric_claims("We closed 2500 deals.")
        assert any("2500" in c for c in claims)


class TestVerifyResponseAgainstEvidence:
    def test_supported_number_passes(self):
        evidence = [{"data": {"total_revenue": 125000}}]
        ok, violations = verify_response_against_evidence(
            "Revenue was $125,000 this quarter.", evidence
        )
        assert ok is True
        assert violations == []

    def test_hallucinated_number_fails_strict(self):
        evidence = [{"data": {"total_revenue": 50000}}]
        ok, violations = verify_response_against_evidence(
            "Revenue was $999,999 this quarter.", evidence, strict=True
        )
        assert ok is False
        assert len(violations) > 0

    def test_no_numbers_always_passes(self):
        evidence = [{"data": {}}]
        ok, violations = verify_response_against_evidence(
            "The attainment definition is quota achieved divided by quota target.", evidence
        )
        assert ok is True

    def test_small_number_ignored_non_strict(self):
        evidence = [{"data": {}}]
        ok, violations = verify_response_against_evidence(
            "Three reps hit quota.", evidence, strict=False
        )
        assert ok is True
