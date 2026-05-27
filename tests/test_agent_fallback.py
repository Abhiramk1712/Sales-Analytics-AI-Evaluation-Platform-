from backend.agent.fallback_response import build_deterministic_response


def test_deterministic_fallback_uses_evidence_metrics():
    evidence = [
        {
            "tool_name": "get_sales_kpis",
            "data": {
                "total_revenue": 1250000,
                "attainment_pct": 94.2,
                "pipeline_coverage": 1.45,
            },
        }
    ]
    reply = build_deterministic_response(
        intent="metric_question",
        evidence=evidence,
        tools_used=["get_sales_kpis"],
        warnings=[],
    )
    assert "deterministic evidence-backed summary" in reply
    assert "total revenue" in reply
    assert "Tools used" in reply


def test_deterministic_fallback_without_evidence_is_safe():
    reply = build_deterministic_response(
        intent="metric_question",
        evidence=[],
        tools_used=[],
        warnings=[],
    )
    assert "Insufficient data" in reply


def test_deterministic_fallback_includes_deal_slip_and_quota_whatif_lines():
    evidence = [
        {
            "tool_name": "get_deal_slip_analysis",
            "data": {
                "slip_risk_count": 4,
                "slip_risk_pct": 22.2,
                "total_amount_at_risk": 480000,
                "top_at_risk_deals": [{"deal_name": "Acme Renewal", "slip_risk_score": 0.81}],
            },
        },
        {
            "tool_name": "get_rep_quota_bonus_what_if",
            "data": {
                "matched_rep": {"name": "Alex Johnson"},
                "quota_target_scenario": {
                    "gap_to_target": 125000,
                    "projected_payout_if_target_hit": 14500,
                    "projected_bonus_if_target_hit": 2000,
                },
                "action_plan": [
                    "Increase qualified pipeline generation.",
                    "Raise win rate on late-stage deals.",
                ],
            },
        },
    ]

    reply = build_deterministic_response(
        intent="rep_quota_whatif",
        evidence=evidence,
        tools_used=["get_deal_slip_analysis", "get_rep_quota_bonus_what_if"],
        warnings=[],
    )

    assert "deal slip analysis" in reply
    assert "estimated payout at target" in reply
    assert "next-best actions" in reply


def test_deterministic_fallback_includes_deal_velocity_trend_lines():
    evidence = [
        {
            "tool_name": "get_deal_velocity_trends",
            "data": {
                "direction": "down",
                "change_pct": -12.4,
                "latest": {
                    "period": "2026-04",
                    "deal_velocity": 152000,
                    "avg_cycle_days": 47.3,
                },
            },
        }
    ]

    reply = build_deterministic_response(
        intent="deal_velocity_trends",
        evidence=evidence,
        tools_used=["get_deal_velocity_trends"],
        warnings=[],
    )

    assert "deal velocity trend is down" in reply
    assert "latest average sales cycle is" in reply


def test_deterministic_fallback_includes_forecast_accuracy_lines():
    evidence = [
        {
            "tool_name": "get_forecast_summary",
            "data": {
                "accuracy_backtest": {
                    "status": "ok",
                    "folds": 24,
                    "mape": 8.6,
                    "mae": 18000,
                    "rmse": 24500,
                },
                "quarter_accuracy": {
                    "status": "ok",
                    "period": "2026-Q3",
                    "points": 3,
                    "mape": 7.9,
                    "mae": 15000,
                    "rmse": 18200,
                },
            },
        }
    ]

    reply = build_deterministic_response(
        intent="forecast_question",
        evidence=evidence,
        tools_used=["get_forecast_summary"],
        warnings=[],
    )

    assert "2026-Q3 forecast accuracy is MAPE" in reply
    assert "overall forecast backtest accuracy is MAPE" in reply


def test_deterministic_fallback_includes_plan_performance_lines():
    evidence = [
        {
            "tool_name": "get_plan_performance_summary",
            "data": {
                "matched_plan": {"plan_id": "abc", "name": "FY2026 Plan 1"},
                "performance": {
                    "period": "2026",
                    "total_revenue": 920000,
                    "total_quota": 1000000,
                    "attainment_pct": 92.0,
                },
                "top_reps": [{"name": "James Tucker", "revenue": 165000}],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="plan_performance_question",
        evidence=evidence,
        tools_used=["get_plan_performance_summary"],
        warnings=[],
    )

    assert "FY2026 Plan 1 performance" in reply
    assert "top contributor" in reply


def test_deterministic_fallback_includes_team_pipeline_coverage_attainment_lines():
    evidence = [
        {
            "tool_name": "get_team_pipeline_coverage_attainment",
            "data": {
                "criteria": {
                    "min_pipeline_coverage": 4.0,
                    "max_attainment_pct": 80.0,
                },
                "matches": [
                    {
                        "team_name": "West Team",
                        "pipeline_coverage": 5.2,
                        "attainment_pct": 63.1,
                    },
                    {
                        "team_name": "North Team",
                        "pipeline_coverage": 4.8,
                        "attainment_pct": 58.0,
                    },
                ],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="pipeline_coverage_check",
        evidence=evidence,
        tools_used=["get_team_pipeline_coverage_attainment"],
        warnings=[],
    )

    assert "teams with high pipeline coverage but low quota attainment" in reply
    assert "West Team" in reply


def test_deterministic_fallback_includes_driver_whatif_lines():
    evidence = [
        {
            "tool_name": "get_rep_quota_bonus_what_if",
            "data": {
                "matched_rep": {"name": "James Tucker"},
                "quota_target_scenario": {
                    "gap_to_target": 45000,
                    "projected_payout_if_target_hit": 18800,
                    "projected_bonus_if_target_hit": 2500,
                },
                "scenario_inputs": {
                    "close_rate_lift_pct": 10.0,
                    "sales_cycle_days_reduction": 15.0,
                    "pipeline_lift_pct": 20.0,
                    "pipeline_delta_amount": 0.0,
                    "deal_size_lift_pct": 12.0,
                },
                "driver_scenario": {
                    "baseline": {"attainment_pct": 76.2, "slip_risk_pct": 28.0},
                    "projected": {"attainment_pct": 84.1, "slip_risk_pct": 17.5},
                    "impact": {
                        "attainment_delta_pct_points": 7.9,
                        "payout_delta": 2100,
                        "bonus_delta": 600,
                        "slip_risk_delta_pct_points": -10.5,
                    },
                },
                "action_plan": [
                    "Increase MEDDICC quality on stage 3+ opportunities.",
                    "Pull forward legal/security reviews to cut cycle time.",
                ],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="rep_quota_whatif",
        evidence=evidence,
        tools_used=["get_rep_quota_bonus_what_if"],
        warnings=[],
    )

    assert "what-if impact" in reply
    assert "deal-slippage risk shifts" in reply
    assert "scenario inputs understood" in reply


def test_deterministic_fallback_includes_pipeline_rescue_whatif_lines():
    evidence = [
        {
            "tool_name": "get_pipeline_rescue_what_if",
            "data": {
                "scenario": {
                    "top_n_at_risk_deals": 15,
                    "target_weighted_coverage": 1.0,
                    "current_weighted_coverage": 0.68,
                    "weighted_coverage_after_rescue": 0.78,
                    "remaining_weighted_gap": 2215000,
                },
                "incremental_impact": {
                    "expected_incremental_closed_revenue": 975800,
                    "best_case_incremental_closed_revenue": 2560000,
                    "quota_attainment_lift_expected_pct_points": 9.8,
                    "additional_gross_pipeline_needed_at_same_efficiency": 5810000,
                },
                "priority_deals": [
                    {"deal_name": "Iterate Rich Solutions Deal", "weighted_value": 131500},
                    {"deal_name": "Evolve Cross-Platform Web Services Deal", "weighted_value": 125700},
                ],
                "priority_reps": [
                    {"rep_name": "Lisa Kelley", "weighted_value": 325900, "deals": 3},
                    {"rep_name": "Cindy Hayes", "weighted_value": 173200, "deals": 3},
                ],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="pipeline_rescue_whatif",
        evidence=evidence,
        tools_used=["get_pipeline_rescue_what_if"],
        warnings=[],
    )

    assert "Weighted coverage impact:" in reply
    assert "Expected incremental closed revenue:" in reply
    assert "Priority Deals (First 5):" in reply
    assert "Priority Reps (First 5):" in reply


def test_deterministic_fallback_pipeline_rescue_uses_fixed_executive_structure():
    evidence = [
        {
            "tool_name": "get_pipeline_rescue_what_if",
            "data": {
                "scenario": {
                    "top_n_at_risk_deals": 15,
                    "target_weighted_coverage": 1.0,
                    "current_weighted_coverage": 0.68,
                    "weighted_coverage_after_rescue": 0.78,
                    "remaining_weighted_gap": 2215000,
                },
                "incremental_impact": {
                    "expected_incremental_closed_revenue": 975800,
                    "best_case_incremental_closed_revenue": 2560000,
                    "quota_attainment_before_pct": 121.1,
                    "quota_attainment_after_expected_pct": 139.2,
                    "quota_attainment_lift_expected_pct_points": 18.1,
                    "weighted_to_gross_efficiency_pct": 38.1,
                    "additional_gross_pipeline_needed_at_same_efficiency": 5810000,
                },
                "priority_deals": [
                    {
                        "deal_name": "Iterate Rich Solutions Deal",
                        "rep_name": "Cindy Hayes",
                        "weighted_value": 131500,
                        "amount": 252900,
                    }
                ],
                "priority_reps": [
                    {
                        "rep_name": "Lisa Kelley",
                        "weighted_value": 325900,
                        "deals": 3,
                    }
                ],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="pipeline_rescue_whatif",
        evidence=evidence,
        tools_used=["get_pipeline_rescue_what_if"],
        warnings=[],
    )

    assert "deterministic evidence-backed scenario analysis" in reply
    assert "Baseline:" in reply
    assert "Scenario (Rescue Top 15 At-Risk Deals Toward 1.00x Weighted Coverage):" in reply
    assert "Gap To Target:" in reply
    assert "Priority Deals (First 5):" in reply
    assert "Priority Reps (First 5):" in reply
    assert "Recommended Actions:" in reply


def test_deterministic_fallback_pipeline_rescue_calls_out_input_mismatch_before_scenario():
    evidence = [
        {
            "tool_name": "get_pipeline_rescue_what_if",
            "data": {
                "scenario": {
                    "top_n_at_risk_deals": 15,
                    "target_weighted_coverage": 1.0,
                    "requested_baseline_weighted_coverage": 0.68,
                    "current_weighted_coverage": 4.44,
                    "weighted_coverage_after_rescue": 5.11,
                    "remaining_weighted_gap": 0,
                },
                "input_reconciliation": {
                    "requested_baseline_weighted_coverage": 0.68,
                    "actual_current_weighted_coverage": 4.44,
                    "delta_weighted_coverage": 3.76,
                    "baseline_mismatch": True,
                    "target_explicit_from_prompt": True,
                    "target_already_met_pre_rescue": True,
                    "used_actual_baseline_for_calculation": True,
                },
                "incremental_impact": {
                    "expected_incremental_closed_revenue": 3050000,
                    "best_case_incremental_closed_revenue": 5900000,
                    "quota_attainment_before_pct": 66.7,
                    "quota_attainment_after_expected_pct": 134.4,
                    "quota_attainment_lift_expected_pct_points": 67.7,
                },
                "priority_deals": [],
                "priority_reps": [],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="pipeline_rescue_whatif",
        evidence=evidence,
        tools_used=["get_pipeline_rescue_what_if"],
        warnings=[],
    )

    assert "Input Reconciliation:" in reply
    assert "Prompt baseline 0.68x does not match actual current 4.44x" in reply
    assert "Prompt target 1.00x is already met before rescue based on current 4.44x" in reply
    assert "Calculations below use the actual current weighted coverage baseline from live data" in reply
    assert reply.index("Input Reconciliation:") < reply.index("Scenario (Rescue Top 15 At-Risk Deals Toward 1.00x Weighted Coverage):")


def test_pipeline_rescue_mismatch_warning_not_duplicated_in_data_quality_notes_and_singular_deal_wording():
    evidence = [
        {
            "tool_name": "get_pipeline_rescue_what_if",
            "data": {
                "scenario": {
                    "top_n_at_risk_deals": 20,
                    "target_weighted_coverage": 1.2,
                    "requested_baseline_weighted_coverage": 0.60,
                    "current_weighted_coverage": 0.68,
                    "weighted_coverage_after_rescue": 0.91,
                    "remaining_weighted_gap": 2900000,
                },
                "input_reconciliation": {
                    "requested_baseline_weighted_coverage": 0.60,
                    "actual_current_weighted_coverage": 0.68,
                    "delta_weighted_coverage": 0.08,
                    "baseline_mismatch": True,
                    "target_explicit_from_prompt": True,
                    "target_already_met_pre_rescue": False,
                    "used_actual_baseline_for_calculation": True,
                },
                "incremental_impact": {
                    "expected_incremental_closed_revenue": 2210000,
                    "best_case_incremental_closed_revenue": 4400000,
                    "quota_attainment_before_pct": 121.1,
                    "quota_attainment_after_expected_pct": 143.4,
                    "quota_attainment_lift_expected_pct_points": 22.3,
                    "weighted_to_gross_efficiency_pct": 50.2,
                    "additional_gross_pipeline_needed_at_same_efficiency": 5770000,
                },
                "priority_deals": [],
                "priority_reps": [
                    {
                        "rep_name": "Heather Pratt",
                        "weighted_value": 184900,
                        "deals": 1,
                    }
                ],
            },
        }
    ]

    warnings = [
        "Scenario input mismatch: requested weighted coverage baseline 0.60x differs from actual current 0.68x. Calculations use the actual baseline.",
        "Pipeline coverage health is at_risk: 1.59x unweighted, 0.68x weighted.",
    ]

    reply = build_deterministic_response(
        intent="pipeline_rescue_whatif",
        evidence=evidence,
        tools_used=["get_pipeline_rescue_what_if"],
        warnings=warnings,
    )

    assert "Input Reconciliation:" in reply
    assert "Scenario input mismatch:" not in reply
    assert "Pipeline coverage health is at_risk: 1.59x unweighted, 0.68x weighted." in reply
    assert "1. Heather Pratt - 1 deal, $184.9K weighted impact." in reply


def test_deterministic_fallback_includes_team_whatif_lines_when_rep_missing():
    evidence = [
        {
            "tool_name": "get_rep_quota_bonus_what_if",
            "data": {
                "matched_rep": None,
                "scenario_inputs": {
                    "pipeline_delta_amount": 500000,
                    "pipeline_lift_pct": 0.0,
                },
                "team_scenario": {
                    "baseline": {
                        "total_payout": 120000,
                        "attainment_pct": 78.0,
                    },
                    "projected": {
                        "total_payout": 136000,
                        "attainment_pct": 84.5,
                    },
                    "impact": {
                        "payout_delta": 16000,
                        "attainment_delta_pct_points": 6.5,
                    },
                },
                "action_plan": [
                    "Specify rep name for personalized payout projection.",
                ],
            },
        }
    ]

    reply = build_deterministic_response(
        intent="rep_quota_whatif",
        evidence=evidence,
        tools_used=["get_rep_quota_bonus_what_if"],
        warnings=[],
    )

    assert "team-level what-if" in reply
    assert "team attainment shifts" in reply