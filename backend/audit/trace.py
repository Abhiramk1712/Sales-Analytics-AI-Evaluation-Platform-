"""
backend/audit/trace.py
======================
Audit trail helpers for metrics, reports, and agent answers.

Every important calculated value should pass through MetricTrace so it can be
explained to an end user, auditor, or QA process.

Design principle: traces are lightweight dicts — no DB writes, just attached
metadata that flows through the response chain.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MetricTrace:
    """
    Attach an audit trail to a calculated metric.

    Usage:
        t = MetricTrace("total_revenue")
        t.set_value(125000.0)
        t.set_formula("SUM(revenue.amount) WHERE date BETWEEN 2025-01-01 AND 2025-01-31")
        t.add_source("revenue", row_count=450)
        trace_dict = t.to_dict()
    """

    def __init__(self, metric_name: str) -> None:
        self.metric_name = metric_name
        self.value: Any = None
        self.formula: Optional[str] = None
        self.period: Optional[str] = None
        self.filters: Dict[str, Any] = {}
        self.source_tables: List[Dict[str, Any]] = []
        self.confidence: float = 1.0
        self.fallback_mode: bool = False
        self.fallback_reason: Optional[str] = None
        self.warnings: List[str] = []
        self.notes: List[str] = []
        self.generated_at: str = _now_iso()

    def set_value(self, value: Any) -> "MetricTrace":
        self.value = value
        return self

    def set_formula(self, formula: str) -> "MetricTrace":
        self.formula = formula
        return self

    def set_period(self, period: str) -> "MetricTrace":
        self.period = period
        return self

    def set_filters(self, filters: Dict[str, Any]) -> "MetricTrace":
        self.filters = filters
        return self

    def add_source(self, table: str, row_count: Optional[int] = None, columns: Optional[List[str]] = None) -> "MetricTrace":
        entry: Dict[str, Any] = {"table": table}
        if row_count is not None:
            entry["row_count"] = row_count
        if columns:
            entry["columns"] = columns
        self.source_tables.append(entry)
        return self

    def set_confidence(self, confidence: float) -> "MetricTrace":
        self.confidence = max(0.0, min(1.0, confidence))
        return self

    def set_fallback(self, reason: str, confidence: float = 0.5) -> "MetricTrace":
        self.fallback_mode = True
        self.fallback_reason = reason
        self.confidence = confidence
        return self

    def add_warning(self, warning: str) -> "MetricTrace":
        if warning not in self.warnings:
            self.warnings.append(warning)
        return self

    def add_note(self, note: str) -> "MetricTrace":
        self.notes.append(note)
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "metric_name": self.metric_name,
            "value": self.value,
            "formula": self.formula,
            "period": self.period,
            "filters": self.filters,
            "source_tables": self.source_tables,
            "confidence": self.confidence,
            "fallback_mode": self.fallback_mode,
            "fallback_reason": self.fallback_reason,
            "warnings": self.warnings,
            "notes": self.notes,
            "generated_at": self.generated_at,
        }


# ── No-hallucination guard ─────────────────────────────────────────────────

# Matches dollar amounts, percentages, and plain numbers ≥ 4 digits
_NUMERIC_RE = re.compile(r"\$[\d,]+(?:\.\d+)?|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d{4,}(?:\.\d+)?\b|\b\d+(?:\.\d+)?%")


def extract_numeric_claims(text: str) -> List[str]:
    """Return a list of numeric strings found in text."""
    return _NUMERIC_RE.findall(text or "")


def verify_response_against_evidence(
    response_text: str,
    evidence: List[Dict[str, Any]],
    *,
    strict: bool = False,
) -> tuple[bool, List[str]]:
    """
    Check that numeric claims in response_text exist somewhere in evidence.

    Returns (passed, list_of_violations).
    If strict=True, any unsupported number is a violation.
    If strict=False, only flag numbers that look like exact revenue/quota values
    (≥1000 or contain $).
    """
    claims = extract_numeric_claims(response_text)
    if not claims:
        return True, []

    # Build a flat string of all evidence values to search
    evidence_str = str(evidence).lower()

    violations: List[str] = []
    for claim in claims:
        # Normalise: remove $ and commas
        norm = claim.replace("$", "").replace(",", "")
        is_large = False
        try:
            num = float(norm.rstrip("%"))
            is_large = num >= 1000 or claim.startswith("$")
        except ValueError:
            pass

        if strict or is_large:
            # Check if this number (without $ and commas) appears in evidence
            if norm.rstrip("%") not in evidence_str and claim not in evidence_str:
                violations.append(claim)

    return len(violations) == 0, violations
