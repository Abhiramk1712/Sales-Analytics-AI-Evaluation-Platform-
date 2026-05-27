from backend.agent.chart_payloads import build_chart_payloads


def test_build_plan_charts_from_evidence():
    evidence = [
        {
            "tool_name": "get_plan_performance_summary",
            "data": {
                "monthly_revenue": [
                    {"period": "2026-01", "revenue": 120000},
                    {"period": "2026-02", "revenue": 132500},
                ],
                "top_reps": [
                    {"name": "James Tucker", "revenue": 85000},
                    {"name": "Sonia Patel", "revenue": 79000},
                ],
            },
        }
    ]

    charts = build_chart_payloads("plan_performance_question", evidence)
    ids = {c["id"] for c in charts}
    assert "plan-monthly-revenue" in ids
    assert "plan-top-reps" in ids


def test_build_rep_whatif_charts_from_evidence():
    evidence = [
        {
            "tool_name": "get_rep_quota_bonus_what_if",
            "data": {
                "driver_scenario": {
                    "baseline": {"attainment_pct": 62.0, "payout": 9800, "bonus": 500, "slip_risk_pct": 30.0},
                    "projected": {"attainment_pct": 75.0, "payout": 12300, "bonus": 1200, "slip_risk_pct": 22.0},
                }
            },
        }
    ]

    charts = build_chart_payloads("rep_quota_whatif", evidence)
    ids = {c["id"] for c in charts}
    assert "whatif-payout-components" in ids
    assert "whatif-payout" in ids
    assert "whatif-impact-percent" in ids


def test_build_velocity_chart_from_evidence():
    evidence = [
        {
            "tool_name": "get_deal_velocity_trends",
            "data": {
                "trend_points": [
                    {"period": "2026-01", "deal_velocity": 120000, "avg_cycle_days": 42},
                    {"period": "2026-02", "deal_velocity": 133000, "avg_cycle_days": 39},
                ]
            },
        }
    ]

    charts = build_chart_payloads("deal_velocity_trends", evidence)
    ids = {c["id"] for c in charts}
    assert "deal-velocity-trend" in ids
    assert "sales-cycle-trend" in ids


def test_build_slip_risk_pie_chart_from_evidence():
    evidence = [
        {
            "tool_name": "get_deal_slip_analysis",
            "data": {
                "open_deals_analyzed": 20,
                "slip_risk_count": 6,
                "top_at_risk_deals": [
                    {"deal_name": "Deal A", "slip_risk_score": 0.8},
                    {"deal_name": "Deal B", "slip_risk_score": 0.7},
                ],
            },
        }
    ]

    charts = build_chart_payloads("deal_slip_analysis", evidence)
    ids = {c["id"] for c in charts}
    assert "deal-slip-risk-breakdown" in ids


def test_build_forecast_accuracy_chart_from_evidence():
    evidence = [
        {
            "tool_name": "get_forecast_summary",
            "data": {
                "accuracy_backtest": {"status": "ok", "mape": 9.4},
                "quarter_accuracy": {"status": "ok", "period": "2026-Q3", "mape": 7.8},
            },
        }
    ]

    charts = build_chart_payloads("forecast_question", evidence)
    ids = {c["id"] for c in charts}
    assert "forecast-accuracy-mape" in ids
