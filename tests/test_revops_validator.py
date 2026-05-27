"""Tests for RevOps business-rule validator (backend/validation/revops_rules.py)."""
from __future__ import annotations

import csv
import os
import tempfile
from pathlib import Path

import pytest

from backend.validation.revops_rules import RevOpsBusinessRuleValidator


def _make_company_dir(files: dict[str, list[dict]]) -> str:
    """Create a temporary directory with CSV files."""
    d = tempfile.mkdtemp()
    for name, rows in files.items():
        path = Path(d) / name
        if rows:
            with open(path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        else:
            path.write_text("")
    return d


def test_hard_fail_quota_vs_revenue_extreme():
    """Quota > 20× revenue should produce a hard-fail violation."""
    d = _make_company_dir({
        "quotas.csv": [{"rep_id": "R1", "amount": "2000000", "period": "2024-01"}],
        "revenue.csv": [{"rep_id": "R1", "amount": "1000", "period": "2024-01", "revenue_type": "new_logo"}],
        "deals.csv": [],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    summary = result.summary()
    assert summary["hard_fail_count"] >= 1, f"Expected hard fail, got: {summary}"


def test_hard_fail_quota_vs_revenue_ok():
    """Reasonable quota/revenue ratio should not trigger hard-fail."""
    d = _make_company_dir({
        "quotas.csv": [{"rep_id": "R1", "amount": "100000", "period": "2024-01"}],
        "revenue.csv": [{"rep_id": "R1", "amount": "80000", "period": "2024-01", "revenue_type": "renewal"}],
        "deals.csv": [],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    violations = [v for v in result.violations if v.rule == "quota_vs_revenue"]
    assert len(violations) == 0


def test_hard_fail_closed_deal_missing_close_date():
    """Closed-won/lost deals without actual_close_date should fail stage progression rule."""
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [
            {
                "deal_id": "D1", "rep_id": "R1", "stage": "Closed Won",
                "amount": "50000", "created_date": "2024-01-01",
                "expected_close_date": "2024-03-01", "actual_close_date": "",
            }
        ],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    rules = {v.rule for v in result.violations}
    assert "stage_progression" in rules


def test_hard_fail_date_order():
    """Deal where close date < created date should fail date_order rule."""
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [
            {
                "deal_id": "D1", "id": "D1", "rep_id": "R1", "stage": "Closed Won",
                "amount": "10000", "created_at": "2024-06-01",
                "expected_close_date": "2024-08-01", "actual_close_date": "2024-01-01",
            }
        ],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    rules = {v.rule for v in result.violations}
    assert "date_order" in rules


def test_hard_fail_negative_arr():
    """Open deals with negative amount should fail negative_arr rule."""
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [
            {
                "deal_id": "D1", "rep_id": "R1", "stage": "Qualification",
                "amount": "-5000", "created_date": "2024-01-01",
                "expected_close_date": "2024-06-01", "actual_close_date": "",
            }
        ],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    rules = {v.rule for v in result.violations}
    assert "negative_arr" in rules


def test_warn_rep_without_quota():
    """Rep with no quota entry should trigger warn rule."""
    d = _make_company_dir({
        "quotas.csv": [{"rep_id": "R2", "amount": "100000", "period": "2024-01"}],
        "revenue.csv": [],
        "deals.csv": [],
        "activities.csv": [],
        "reps.csv": [{"id": "R1", "name": "Alice"}, {"id": "R2", "name": "Bob"}],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    warn_rules = {v.rule for v in result.warnings}
    assert "rep_without_quota" in warn_rules


def test_summary_structure():
    """summary() should return expected keys."""
    d = _make_company_dir({"quotas.csv": [], "revenue.csv": [], "deals.csv": [], "activities.csv": []})
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    s = result.summary()
    for key in ("hard_fail_count", "warn_count", "passed", "violations", "warnings"):
        assert key in s, f"Missing key: {key}"


def test_warn_manager_span_excessive_direct_reports():
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [],
        "activities.csv": [],
        "rep_hierarchy.csv": [
            {"rep_id": f"R{i}", "manager_rep_id": "M1", "role": "Account Executive", "level": "L5"}
            for i in range(10)
        ] + [{"rep_id": "M1", "manager_rep_id": "", "role": "Sales Manager", "level": "L4"}],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    warn_rules = {v.rule for v in result.warnings}
    assert "manager_span" in warn_rules


def test_warn_role_mix_missing_leadership_or_ic():
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [],
        "activities.csv": [],
        "rep_hierarchy.csv": [
            {"rep_id": "R1", "manager_rep_id": "", "role": "Account Executive", "level": "L5"},
            {"rep_id": "R2", "manager_rep_id": "", "role": "Senior Account Executive", "level": "L5"},
        ],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    warn_rules = {v.rule for v in result.warnings}
    assert "role_mix" in warn_rules


def test_all_pass_clean_data():
    """Clean, consistent dataset should produce zero hard-fail violations."""
    d = _make_company_dir({
        "quotas.csv": [{"rep_id": "R1", "quota_amount": "120000", "period": "2024-01"}],
        "revenue.csv": [{"rep_id": "R1", "amount": "100000", "period": "2024-01", "revenue_type": "renewal"}],
        "deals.csv": [
            {
                "deal_id": "D1", "rep_id": "R1", "stage": "Closed Won",
                "amount": "50000", "created_date": "2024-01-01",
                "expected_close_date": "2024-03-01", "actual_close_date": "2024-03-15",
            }
        ],
        "activities.csv": [{"deal_id": "D1", "rep_id": "R1", "activity_date": "2024-02-01", "activity_type": "call"}],
    })
    result = RevOpsBusinessRuleValidator().validate(Path(d))
    assert result.summary()["hard_fail_count"] == 0


def test_custom_manager_span_threshold_warns_earlier():
    d = _make_company_dir({
        "quotas.csv": [],
        "revenue.csv": [],
        "deals.csv": [],
        "activities.csv": [],
        "rep_hierarchy.csv": [
            {"rep_id": "R1", "manager_rep_id": "M1", "role": "Account Executive", "level": "L5"},
            {"rep_id": "R2", "manager_rep_id": "M1", "role": "Account Executive", "level": "L5"},
            {"rep_id": "R3", "manager_rep_id": "M1", "role": "Account Executive", "level": "L5"},
            {"rep_id": "R4", "manager_rep_id": "M1", "role": "Account Executive", "level": "L5"},
            {"rep_id": "M1", "manager_rep_id": "", "role": "Sales Manager", "level": "L4"},
        ],
    })
    result = RevOpsBusinessRuleValidator(manager_span_warn_threshold=3).validate(Path(d))
    warn_rules = {v.rule for v in result.warnings}
    assert "manager_span" in warn_rules


def test_custom_quota_ratio_threshold_hard_fail():
    d = _make_company_dir({
        "quotas.csv": [{"rep_id": "R1", "amount": "200000", "period": "2024-01"}],
        "revenue.csv": [{"rep_id": "R1", "amount": "100000", "period": "2024-01", "revenue_type": "renewal"}],
        "deals.csv": [],
        "activities.csv": [],
    })
    result = RevOpsBusinessRuleValidator(max_quota_to_revenue_ratio=1.5).validate(Path(d))
    rules = {v.rule for v in result.violations}
    assert "quota_vs_revenue" in rules
