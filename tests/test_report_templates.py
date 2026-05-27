"""
Tests for backend/reports/report_generator.py — render_template() and
template routing for plan_performance and territory_performance.
"""
import pytest
from backend.reports.report_generator import ReportGenerator, _JINJA_ENV


# ── Template rendering basics ─────────────────────────────────────────────

def test_render_template_returns_string():
    output = ReportGenerator.render_template("executive_weekly.md", {
        "period": "Q2 2024",
        "generated_at": "2024-06-01T00:00:00Z",
        "warnings": [],
        "metrics": {
            "total_revenue": {"value": 1_200_000, "confidence": "high"},
            "open_pipeline": {"value": 800_000, "confidence": "high"},
            "attainment_pct": {"value": 94.5, "confidence": "high"},
            "win_rate": {"value": 42.1, "confidence": "medium"},
            "avg_deal_size": {"value": 28_000, "confidence": "high"},
        },
        "top_reps": [],
        "hygiene": {"overdue_count": 0, "missing_close_date_count": 0, "high_prob_early_stage_count": 0, "total_open_deals": 0, "stale_threshold_days": 30},
        "forecast": {},
    })
    assert isinstance(output, str)
    assert len(output) > 10


def test_render_unknown_template_returns_error_string():
    output = ReportGenerator.render_template("does_not_exist.md", {})
    assert "error" in output.lower() or "Template" in output


def test_render_rep_performance_template():
    output = ReportGenerator.render_template("rep_performance.md", {
        "rep": {"name": "Alice Zhang"},
        "period": "Q1 2025",
        "generated_at": "2025-01-01T00:00:00Z",
        "metrics": {
            "revenue": 200_000, "quota": 180_000, "attainment_pct": 111.1,
            "deals_won": 12, "win_rate": 48.0, "avg_deal_size": 16_666,
        },
        "payout": {"payout_amount": 18_000, "base_commission": 14_400, "accelerator_amount": 2_000, "bonus_amount": 1_600, "clawback_amount": 0, "final_payout": 18_000, "base_rate": 0.08, "formula_trace": ["Base commission: 8%", "Accelerator: 1.1x at 111% attainment"]},
        "won_deals": [],
        "open_deals": [],
    })
    assert isinstance(output, str)
    assert "Alice Zhang" in output


def test_render_plan_performance_template():
    output = ReportGenerator.render_template("plan_performance.md", {
        "plan": {"name": "Enterprise AE Plan", "effective_start_date": "2024-01-01", "effective_end_date": "2024-12-31"},
        "period": "Q2 2024",
        "generated_at": "2024-06-01T00:00:00Z",
        "metrics": {
            "total_revenue": 900_000, "total_quota": 1_000_000, "attainment_pct": 90.0,
            "rep_count": 10, "reps_at_quota": 4, "reps_near_quota": 3, "reps_below_quota": 3,
            "confidence": "high",
        },
        "rules": [
            {"name": "AE Standard", "metric_name": "revenue", "threshold_min": 0, "threshold_max": 100, "rate": 0.08, "bonus_amount": 0},
        ],
        "reps": [],
        "warnings": [],
    })
    assert isinstance(output, str)
    assert "Enterprise AE Plan" in output


def test_render_territory_performance_template():
    output = ReportGenerator.render_template("territory_performance.md", {
        "territory": {"name": "West Coast", "region": "West", "segment": "Enterprise"},
        "period": "Q2 2024",
        "generated_at": "2024-06-01T00:00:00Z",
        "metrics": {
            "total_revenue": 500_000, "deals_won": 15, "win_rate": 44.5,
            "avg_deal_size": 33_333, "open_pipeline": 800_000, "confidence": "high",
        },
        "hygiene": {"total_open_deals": 20, "overdue_count": 3, "missing_close_date_count": 2, "high_prob_early_stage_count": 1, "stale_threshold_days": 30},
        "reps": [{"name": "Bob Lee", "revenue": 200_000, "deals_won": 6, "attainment_pct": 88.0}],
        "sub_territories": [],
        "warnings": [],
    })
    assert isinstance(output, str)
    assert "West Coast" in output


def test_render_forecast_summary_template():
    output = ReportGenerator.render_template("forecast_summary.md", {
        "period": "2024-Q2",
        "audience": "CRO",
        "generated_at": "2024-06-01T00:00:00Z",
        "model_info": "SARIMAX + Ridge",
        "confidence": "high",
        "history_months": 36,
        "rows": [
            {"period": "2024-07", "forecast": 1000000, "lower_ci": 900000, "upper_ci": 1100000},
        ],
        "metrics": {"MAE": 12000, "RMSE": 18000, "MAPE": 5.6},
        "warnings": [],
        "sources": ["revenue", "model_runs"],
        "kpis": {},
    })
    assert isinstance(output, str)
    assert "Revenue Forecast Summary" in output
    assert "SARIMAX + Ridge" in output


def test_render_payout_statement_template():
    output = ReportGenerator.render_template("payout_statement.md", {
        "period": "2024-Q2",
        "audience": "Finance",
        "generated_at": "2024-06-01T00:00:00Z",
        "total_payout": 250000.55,
        "rep_count": 10,
        "fallback_count": 2,
        "rows": [
            {
                "rep_id_short": "abcd1234…",
                "period": "2024-Q2",
                "credited_amount": 500000,
                "quota": 450000,
                "attainment": 111.1,
                "base_commission": 35000,
                "final_payout": 42000,
                "mode": "credit-level",
            },
        ],
        "warnings": [],
        "sources": ["payouts", "sales_credits"],
        "kpis": {},
    })
    assert isinstance(output, str)
    assert "Payout Statement" in output
    assert "250,000.55" in output


# ── Jinja2 environment ────────────────────────────────────────────────────

def test_jinja_env_has_templates_dir():
    from pathlib import Path
    from backend.reports.report_generator import _TEMPLATES_DIR
    assert _TEMPLATES_DIR.exists()
    assert _TEMPLATES_DIR.is_dir()


def test_jinja_env_lists_known_templates():
    templates = _JINJA_ENV.loader.list_templates()
    for expected in ("executive_weekly.md", "rep_performance.md", "forecast_summary.md", "payout_statement.md"):
        assert expected in templates, f"Missing template: {expected}"
