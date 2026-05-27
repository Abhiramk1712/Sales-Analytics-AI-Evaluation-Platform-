"""
tests/test_quality_gates.py
============================
Tests for ingestion quality gate evaluation and canonical transforms.
"""
import pytest
from backend.ingestion.intelligent_ingestion import (
    evaluate_quality_gates,
    _canonicalize_period,
    _canonicalize_stage,
)


# ── Period canonicalization ───────────────────────────────────────────────

class TestCanonicalizePeriod:
    def test_already_yyyy_mm(self):
        assert _canonicalize_period("2024-03") == "2024-03"

    def test_already_quarterly(self):
        assert _canonicalize_period("2024-Q2") == "2024-Q2"

    def test_q_prefix_format(self):
        assert _canonicalize_period("Q1/2025") == "2025-Q1"
        assert _canonicalize_period("Q3 2024") == "2024-Q3"

    def test_iso_date_to_month(self):
        assert _canonicalize_period("2024-07-15") == "2024-07"

    def test_us_date_to_month(self):
        assert _canonicalize_period("03/15/2024") == "2024-03"

    def test_empty_returns_empty(self):
        assert _canonicalize_period("") == ""

    def test_none_returns_empty(self):
        assert _canonicalize_period(None) == ""

    def test_unknown_format_passthrough(self):
        result = _canonicalize_period("2024FY")
        assert result == "2024FY"  # Unknown formats pass through unchanged


# ── Stage canonicalization ────────────────────────────────────────────────

class TestCanonicalizeStage:
    def test_closed_won_variants(self):
        for variant in ["closed won", "CLOSED WON", "Closed Won", "closed_won", "won"]:
            assert _canonicalize_stage(variant) == "Closed Won", f"Failed for: {variant}"

    def test_closed_lost_variants(self):
        for variant in ["closed lost", "CLOSED LOST", "Closed Lost", "closed_lost", "lost"]:
            assert _canonicalize_stage(variant) == "Closed Lost", f"Failed for: {variant}"

    def test_proposal_variants(self):
        assert _canonicalize_stage("proposal") == "Proposal"
        assert _canonicalize_stage("PROPOSAL") == "Proposal"

    def test_qualification_variants(self):
        assert _canonicalize_stage("qualification") == "Qualification"

    def test_empty_returns_default(self):
        assert _canonicalize_stage("") == "Qualification"

    def test_none_returns_default(self):
        assert _canonicalize_stage(None) == "Qualification"

    def test_unknown_value_passthrough(self):
        assert _canonicalize_stage("Discovery") == "Discovery"


# ── Quality gate evaluation ───────────────────────────────────────────────

def _minimal_dataset(**overrides):
    import uuid
    team_id = str(uuid.uuid4())
    rep_id = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    deal_id = str(uuid.uuid4())
    base = {
        "teams": [{"id": team_id, "name": "Sales Team", "region": "West"}],
        "reps": [{"id": rep_id, "team_id": team_id, "name": "Alice", "email": "alice@x.com", "region": "West", "hire_date": "2020-01-01"}],
        "accounts": [{"id": account_id, "name": "Acme", "industry": "Tech", "employee_count": "100", "annual_revenue": "1000000"}],
        "deals": [{"id": deal_id, "account_id": account_id, "rep_id": rep_id, "name": "Big Deal", "product": "X", "stage": "Closed Won", "amount": "50000", "close_probability": "100", "expected_close_date": None, "actual_close_date": "2024-01-15", "created_at": "2023-12-01T00:00:00"}],
        "activities": [],
        "quotas": [{"rep_id": rep_id, "period": "2024-Q1", "amount": "75000"}],
        "revenue": [{"rep_id": rep_id, "period": "2024-01", "amount": "50000"}],
    }
    base.update(overrides)
    return base


class TestEvaluateQualityGates:
    def test_clean_dataset_returns_ok(self):
        result = evaluate_quality_gates(_minimal_dataset(), [])
        assert result["overall_status"] == "ok"
        assert result["confidence"] == 1.0
        assert not result["blocked"]
        assert result["issues"] == []

    def test_empty_reps_is_critical(self):
        ds = _minimal_dataset(reps=[])
        result = evaluate_quality_gates(ds, [])
        assert result["blocked"]
        assert result["overall_status"] == "critical"
        assert result["confidence"] == 0.0
        assert any(i["severity"] == "critical" for i in result["issues"])

    def test_empty_deals_is_high_severity(self):
        ds = _minimal_dataset(deals=[])
        result = evaluate_quality_gates(ds, [])
        assert not result["blocked"]  # high but not critical
        assert result["overall_status"] == "high"
        assert any(i["key"] == "empty_deals" for i in result["issues"])

    def test_empty_accounts_is_high_severity(self):
        ds = _minimal_dataset(accounts=[])
        result = evaluate_quality_gates(ds, [])
        assert any(i["key"] == "empty_accounts" for i in result["issues"])

    def test_empty_quotas_is_medium_severity(self):
        ds = _minimal_dataset(quotas=[])
        result = evaluate_quality_gates(ds, [])
        assert any(i["key"] == "empty_quotas" for i in result["issues"])
        assert result["overall_status"] in ("medium", "high")

    def test_missing_team_ref_is_high(self):
        import uuid
        rep_id = str(uuid.uuid4())
        account_id = str(uuid.uuid4())
        team_id = str(uuid.uuid4())
        ghost_team = str(uuid.uuid4())
        ds = _minimal_dataset(
            reps=[{"id": rep_id, "team_id": ghost_team, "name": "Bob", "email": "b@x.com", "region": "East", "hire_date": "2020-01-01"}],
            teams=[{"id": team_id, "name": "West Team", "region": "West"}],
        )
        result = evaluate_quality_gates(ds, [])
        assert any(i["key"] == "missing_team_refs" for i in result["issues"])

    def test_confidence_decreases_with_issues(self):
        ds_clean = _minimal_dataset()
        ds_dirty = _minimal_dataset(deals=[], accounts=[])
        clean_result = evaluate_quality_gates(ds_clean, [])
        dirty_result = evaluate_quality_gates(ds_dirty, [])
        assert dirty_result["confidence"] < clean_result["confidence"]

    def test_multiple_warnings_propagated(self):
        result = evaluate_quality_gates(_minimal_dataset(), ["w1", "w2"])
        assert "w1" in result["data_warnings"]
        assert "w2" in result["data_warnings"]

    def test_relationship_unresolved_required_penalizes_confidence(self):
        ds = _minimal_dataset()
        clean = evaluate_quality_gates(ds, [])
        penalized = evaluate_quality_gates(
            ds,
            [],
            relationship_resolution={
                "users_team_fk": {
                    "unresolved": 2,
                    "required": True,
                }
            },
        )
        assert penalized["confidence"] < clean["confidence"]
        assert any(i["key"] == "relationship_unresolved_required" for i in penalized["issues"])
        assert penalized["relationship_quality"]["required_unresolved"] == 2

    def test_relationship_unresolved_optional_is_low_severity(self):
        ds = _minimal_dataset()
        result = evaluate_quality_gates(
            ds,
            [],
            relationship_resolution={
                "activities_user_fk": {
                    "unresolved": 3,
                    "required": False,
                }
            },
        )
        issue = next(i for i in result["issues"] if i["key"] == "relationship_unresolved_optional")
        assert issue["severity"] == "low"
        assert result["relationship_quality"]["optional_unresolved"] == 3
