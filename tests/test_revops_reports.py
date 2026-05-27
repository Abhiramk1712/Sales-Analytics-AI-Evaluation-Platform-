"""Tests for RevOps report templates (backend/reports/report_generator.py)."""
from __future__ import annotations

import pytest

from backend.reports.report_generator import ReportGenerator


def test_pipeline_health_report_runs():
    result = ReportGenerator._pipeline_health_report(
        period="2024-Q1",
        audience="Sales Leadership",
        kpis={"total_quota": 1_000_000, "attainment_pct": 72.0},
        pipeline_check={"value": 3_800_000},
        weighted={"ratio": 2.4, "weighted_pipeline": 2_400_000},
        activity_ratio={"ratio": 2.8, "open_deals": 45},
        cycle_days={"avg_days": 62},
        top_reps=[{"name": "Alice", "attainment_pct": 118.0}],
        under=[{"name": "Bob", "attainment_pct": 44.0, "open_pipeline": 80_000}],
        warnings=["Approximated revenue type split"],
        citations={},
    )
    assert "Pipeline Health Report" in result
    assert "3.80" in result  # raw coverage ratio
    assert "Watch" in result or "At Risk" in result  # coverage below 4x


def test_pipeline_health_report_healthy():
    result = ReportGenerator._pipeline_health_report(
        period="2024-Q2",
        audience="CRO",
        kpis={"total_quota": 500_000, "attainment_pct": 95.0},
        pipeline_check={"value": 2_200_000},
        weighted={"ratio": 3.5, "weighted_pipeline": 1_750_000},
        activity_ratio={"ratio": 3.8, "open_deals": 30},
        cycle_days={"avg_days": 45},
        top_reps=[],
        under=[],
        warnings=[],
        citations={},
    )
    assert "Healthy" in result


def test_quota_attainment_report_runs():
    result = ReportGenerator._quota_attainment_report(
        period="2024-Q1",
        audience="VP Sales",
        kpis={"attainment_pct": 68.5},
        top_reps=[{"name": "Alice", "attainment_pct": 135.0}, {"name": "Carol", "attainment_pct": 112.0}],
        under=[{"name": "Dave", "attainment_pct": 42.0}],
        attainment_dist={
            "data": {
                "counts": {"above_120": 2, "100_to_120": 3, "75_to_100": 2, "50_to_75": 1, "below_50": 2},
                "percentages": {"above_120": 20.0, "100_to_120": 30.0, "75_to_100": 20.0, "50_to_75": 10.0, "below_50": 20.0},
                "total_reps_with_quota": 10,
            }
        },
        warnings=[],
        citations={},
    )
    assert "Quota Attainment Report" in result
    assert "Alice" in result
    assert "Dave" in result
    assert "68.5%" in result


def test_arr_bridge_report_runs():
    result = ReportGenerator._arr_bridge_report(
        period="2024-Q1",
        audience="Board",
        kpis={"total_quota": 2_000_000},
        nrr={"nrr_pct": 108.5, "components": {"mrr_start": 150_000, "expansion": 25_000, "contraction": 8_000, "churn": 5_000}},
        grr={"grr_pct": 91.2},
        arr_growth={"arr_growth_pct": 18.4, "arr_current_12m": 1_800_000, "arr_prior_12m": 1_520_000},
        warnings=["NRR approximated — no revenue_type column"],
        citations={},
    )
    assert "ARR Bridge Report" in result
    assert "108.5%" in result
    assert "91.2%" in result
    assert "18.4%" in result


def test_arr_bridge_report_healthy_status():
    result = ReportGenerator._arr_bridge_report(
        period="2024-Q2",
        audience="CRO",
        kpis={},
        nrr={"nrr_pct": 122.0, "components": {}},
        grr={"grr_pct": 88.0},
        arr_growth={"arr_growth_pct": 27.0, "arr_current_12m": 2_500_000, "arr_prior_12m": 1_970_000},
        warnings=[],
        citations={},
    )
    assert "Excellent" in result
    assert "Healthy" in result


def test_pipeline_health_report_no_at_risk_reps():
    result = ReportGenerator._pipeline_health_report(
        period="2024-Q3",
        audience="Manager",
        kpis={"total_quota": 600_000, "attainment_pct": 85.0},
        pipeline_check={"value": 2_500_000},
        weighted={"ratio": 3.2, "weighted_pipeline": 1_920_000},
        activity_ratio={"ratio": 4.1, "open_deals": 28},
        cycle_days={"avg_days": 38},
        top_reps=[],
        under=[],
        warnings=[],
        citations={},
    )
    assert "No at-risk reps identified" in result
