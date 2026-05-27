"""
Build chart payloads from agent evidence for chat UI rendering.
"""
from __future__ import annotations

from typing import Any


DEFAULT_COLORS = [
    "#378ADD",
    "#2DA44E",
    "#D85A30",
    "#8B5CF6",
]


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _chart(
    chart_id: str,
    title: str,
    chart_type: str,
    data: list[dict[str, Any]],
    x_key: str,
    series: list[dict[str, Any]],
    unit: str = "number",
) -> dict[str, Any]:
    return {
        "id": chart_id,
        "title": title,
        "type": chart_type,
        "data": data,
        "xKey": x_key,
        "series": series,
        "unit": unit,
        "height": 220,
    }


def build_chart_payloads(intent: str, evidence_results: list[dict[str, Any]], max_charts: int = 3) -> list[dict[str, Any]]:
    charts: list[dict[str, Any]] = []

    for result in evidence_results:
        tool_name = str(result.get("tool_name") or "")
        data = result.get("data")

        if not isinstance(data, dict):
            continue

        if tool_name == "get_plan_performance_summary":
            perf = data.get("performance") if isinstance(data.get("performance"), dict) else {}
            total_revenue = _to_float(perf.get("total_revenue"))
            total_quota = _to_float(perf.get("total_quota"))
            plan_name = str((data.get("matched_plan") or {}).get("name") or "Plan")

            if total_quota > 0 or total_revenue > 0:
                charts.append(
                    _chart(
                        chart_id="plan-quota-components",
                        title="Plan Quota Components",
                        chart_type="stacked-bar",
                        data=[
                            {
                                "plan": plan_name,
                                "revenue": total_revenue,
                                "remaining_quota": max(total_quota - total_revenue, 0.0),
                            }
                        ],
                        x_key="plan",
                        series=[
                            {"key": "revenue", "label": "Revenue", "color": DEFAULT_COLORS[0], "stackId": "quota"},
                            {"key": "remaining_quota", "label": "Remaining Quota", "color": DEFAULT_COLORS[2], "stackId": "quota"},
                        ],
                        unit="currency",
                    )
                )

            monthly = data.get("monthly_revenue") if isinstance(data.get("monthly_revenue"), list) else []
            monthly_rows = []
            for item in monthly[-12:]:
                if not isinstance(item, dict):
                    continue
                period = str(item.get("period") or item.get("month") or item.get("label") or "")
                if not period:
                    continue
                monthly_rows.append({
                    "period": period,
                    "revenue": _to_float(item.get("revenue")),
                })
            if monthly_rows:
                charts.append(
                    _chart(
                        chart_id="plan-monthly-revenue",
                        title="Plan Monthly Revenue",
                        chart_type="bar",
                        data=monthly_rows,
                        x_key="period",
                        series=[{"key": "revenue", "label": "Revenue", "color": DEFAULT_COLORS[0]}],
                        unit="currency",
                    )
                )

            top_reps = data.get("top_reps") if isinstance(data.get("top_reps"), list) else []
            top_rows = []
            for rep in top_reps[:6]:
                if not isinstance(rep, dict):
                    continue
                name = str(rep.get("name") or "Rep")
                top_rows.append({"rep": name, "revenue": _to_float(rep.get("revenue"))})
            if top_rows:
                charts.append(
                    _chart(
                        chart_id="plan-top-reps",
                        title="Top Reps In Plan",
                        chart_type="bar",
                        data=top_rows,
                        x_key="rep",
                        series=[{"key": "revenue", "label": "Revenue", "color": DEFAULT_COLORS[1]}],
                        unit="currency",
                    )
                )

        elif tool_name == "get_forecast_summary":
            accuracy = data.get("accuracy_backtest") if isinstance(data.get("accuracy_backtest"), dict) else {}
            quarter = data.get("quarter_accuracy") if isinstance(data.get("quarter_accuracy"), dict) else {}

            mape_rows: list[dict[str, Any]] = []
            if str(accuracy.get("status") or "") == "ok":
                overall_mape = accuracy.get("mape")
                if overall_mape is not None:
                    mape_rows.append({"scope": "Overall", "mape_pct": _to_float(overall_mape)})

            if str(quarter.get("status") or "") == "ok":
                quarter_mape = quarter.get("mape")
                quarter_label = str(quarter.get("period") or "Requested Quarter")
                if quarter_mape is not None:
                    mape_rows.append({"scope": quarter_label, "mape_pct": _to_float(quarter_mape)})

            if mape_rows:
                charts.append(
                    _chart(
                        chart_id="forecast-accuracy-mape",
                        title="Forecast Accuracy (MAPE %)",
                        chart_type="bar",
                        data=mape_rows,
                        x_key="scope",
                        series=[{"key": "mape_pct", "label": "MAPE %", "color": DEFAULT_COLORS[0]}],
                        unit="percent",
                    )
                )

        elif tool_name == "get_deal_velocity_trends":
            points = data.get("trend_points") if isinstance(data.get("trend_points"), list) else []
            velocity_rows = []
            cycle_rows = []
            for point in points:
                if not isinstance(point, dict):
                    continue
                period = str(point.get("period") or "")
                if not period:
                    continue
                velocity_rows.append({"period": period, "deal_velocity": _to_float(point.get("deal_velocity"))})
                cycle_days = point.get("avg_cycle_days")
                if cycle_days is not None:
                    cycle_rows.append({"period": period, "avg_cycle_days": _to_float(cycle_days)})

            if len(velocity_rows) >= 2:
                charts.append(
                    _chart(
                        chart_id="deal-velocity-trend",
                        title="Deal Velocity Trend",
                        chart_type="line",
                        data=velocity_rows,
                        x_key="period",
                        series=[{"key": "deal_velocity", "label": "Deal Velocity", "color": DEFAULT_COLORS[0]}],
                        unit="currency",
                    )
                )
            if len(cycle_rows) >= 2:
                charts.append(
                    _chart(
                        chart_id="sales-cycle-trend",
                        title="Average Sales Cycle Trend",
                        chart_type="line",
                        data=cycle_rows,
                        x_key="period",
                        series=[{"key": "avg_cycle_days", "label": "Cycle Days", "color": DEFAULT_COLORS[2]}],
                        unit="days",
                    )
                )

        elif tool_name == "get_rep_quota_bonus_what_if":
            driver = data.get("driver_scenario") if isinstance(data.get("driver_scenario"), dict) else {}
            baseline = driver.get("baseline") if isinstance(driver.get("baseline"), dict) else {}
            projected = driver.get("projected") if isinstance(driver.get("projected"), dict) else {}
            if baseline and projected:
                baseline_payout = _to_float(baseline.get("payout"))
                projected_payout = _to_float(projected.get("payout"))
                baseline_bonus = _to_float(baseline.get("bonus"))
                projected_bonus = _to_float(projected.get("bonus"))

                charts.append(
                    _chart(
                        chart_id="whatif-payout-components",
                        title="What-If Payout Components",
                        chart_type="stacked-bar",
                        data=[
                            {
                                "scenario": "Baseline",
                                "base_payout": max(baseline_payout - baseline_bonus, 0.0),
                                "bonus": max(baseline_bonus, 0.0),
                            },
                            {
                                "scenario": "Projected",
                                "base_payout": max(projected_payout - projected_bonus, 0.0),
                                "bonus": max(projected_bonus, 0.0),
                            },
                        ],
                        x_key="scenario",
                        series=[
                            {"key": "base_payout", "label": "Base Payout", "color": DEFAULT_COLORS[0], "stackId": "payout"},
                            {"key": "bonus", "label": "Bonus", "color": DEFAULT_COLORS[1], "stackId": "payout"},
                        ],
                        unit="currency",
                    )
                )

                payout_rows = [
                    {"scenario": "Baseline", "payout": baseline_payout},
                    {"scenario": "Projected", "payout": projected_payout},
                ]
                charts.append(
                    _chart(
                        chart_id="whatif-payout",
                        title="What-If Payout Comparison",
                        chart_type="bar",
                        data=payout_rows,
                        x_key="scenario",
                        series=[{"key": "payout", "label": "Payout", "color": DEFAULT_COLORS[0]}],
                        unit="currency",
                    )
                )

                pct_rows = [
                    {
                        "metric": "Attainment %",
                        "baseline": _to_float(baseline.get("attainment_pct")),
                        "projected": _to_float(projected.get("attainment_pct")),
                    },
                    {
                        "metric": "Slip Risk %",
                        "baseline": _to_float(baseline.get("slip_risk_pct")),
                        "projected": _to_float(projected.get("slip_risk_pct")),
                    },
                ]
                charts.append(
                    _chart(
                        chart_id="whatif-impact-percent",
                        title="What-If Impact (%)",
                        chart_type="bar",
                        data=pct_rows,
                        x_key="metric",
                        series=[
                            {"key": "baseline", "label": "Baseline", "color": DEFAULT_COLORS[2]},
                            {"key": "projected", "label": "Projected", "color": DEFAULT_COLORS[1]},
                        ],
                        unit="percent",
                    )
                )

            team = data.get("team_scenario") if isinstance(data.get("team_scenario"), dict) else {}
            team_base = team.get("baseline") if isinstance(team.get("baseline"), dict) else {}
            team_proj = team.get("projected") if isinstance(team.get("projected"), dict) else {}
            if team_base and team_proj:
                team_rows = [
                    {"scenario": "Baseline", "total_payout": _to_float(team_base.get("total_payout"))},
                    {"scenario": "Projected", "total_payout": _to_float(team_proj.get("total_payout"))},
                ]
                charts.append(
                    _chart(
                        chart_id="team-whatif-payout",
                        title="Team Payout Potential Shift",
                        chart_type="bar",
                        data=team_rows,
                        x_key="scenario",
                        series=[{"key": "total_payout", "label": "Total Payout", "color": DEFAULT_COLORS[0]}],
                        unit="currency",
                    )
                )

        elif tool_name == "get_pipeline_coverage_check":
            coverage_rows = [
                {"coverage": "Unweighted", "ratio": _to_float(data.get("unweighted_coverage_ratio"))},
                {"coverage": "Weighted", "ratio": _to_float(data.get("weighted_coverage_ratio"))},
            ]
            if any(r["ratio"] > 0 for r in coverage_rows):
                charts.append(
                    _chart(
                        chart_id="pipeline-coverage",
                        title="Pipeline Coverage Ratio",
                        chart_type="bar",
                        data=coverage_rows,
                        x_key="coverage",
                        series=[{"key": "ratio", "label": "Coverage (x)", "color": DEFAULT_COLORS[0]}],
                        unit="number",
                    )
                )

        elif tool_name == "get_quota_risk_summary":
            reps = data.get("at_risk_reps") if isinstance(data.get("at_risk_reps"), list) else []
            rows = []
            for rep in reps[:8]:
                if not isinstance(rep, dict):
                    continue
                name = str(rep.get("rep_name") or rep.get("name") or "Rep")
                rows.append({"rep": name, "attainment_pct": _to_float(rep.get("attainment_pct"))})
            if rows:
                charts.append(
                    _chart(
                        chart_id="quota-risk-at-risk-reps",
                        title="At-Risk Reps: Attainment %",
                        chart_type="bar",
                        data=rows,
                        x_key="rep",
                        series=[{"key": "attainment_pct", "label": "Attainment %", "color": DEFAULT_COLORS[2]}],
                        unit="percent",
                    )
                )

        elif tool_name == "get_deal_slip_analysis":
            open_deals = int(_to_float(data.get("open_deals_analyzed")))
            at_risk = int(_to_float(data.get("slip_risk_count")))
            if open_deals > 0:
                charts.append(
                    _chart(
                        chart_id="deal-slip-risk-breakdown",
                        title="Deal Slip Risk Breakdown",
                        chart_type="pie",
                        data=[
                            {"name": "At Risk", "value": max(at_risk, 0), "fill": DEFAULT_COLORS[2]},
                            {"name": "On Track", "value": max(open_deals - at_risk, 0), "fill": DEFAULT_COLORS[1]},
                        ],
                        x_key="name",
                        series=[{"key": "value", "label": "Deals"}],
                        unit="number",
                    )
                )

            deals = data.get("top_at_risk_deals") if isinstance(data.get("top_at_risk_deals"), list) else []
            rows = []
            for deal in deals[:6]:
                if not isinstance(deal, dict):
                    continue
                name = str(deal.get("deal_name") or "Deal")
                score = _to_float(deal.get("slip_risk_score")) * 100.0
                rows.append({"deal": name, "slip_risk_score_pct": score})
            if rows:
                charts.append(
                    _chart(
                        chart_id="deal-slip-top-risk",
                        title="Top Slip-Risk Deals",
                        chart_type="bar",
                        data=rows,
                        x_key="deal",
                        series=[{"key": "slip_risk_score_pct", "label": "Slip Risk %", "color": DEFAULT_COLORS[2]}],
                        unit="percent",
                    )
                )

        elif tool_name == "get_payout_summary":
            rows = data.get("rows") if isinstance(data.get("rows"), list) else []
            chart_rows = []
            for row in rows[:6]:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "Rep")
                chart_rows.append({"rep": name, "payout": _to_float(row.get("payout"))})
            if chart_rows:
                charts.append(
                    _chart(
                        chart_id="payout-top-reps",
                        title="Payout By Rep",
                        chart_type="bar",
                        data=chart_rows,
                        x_key="rep",
                        series=[{"key": "payout", "label": "Payout", "color": DEFAULT_COLORS[0]}],
                        unit="currency",
                    )
                )

    if intent == "plan_performance_question":
        preferred_order = ["plan-monthly-revenue", "plan-quota-components", "plan-top-reps"]
    elif intent == "rep_quota_whatif":
        preferred_order = ["whatif-payout-components", "whatif-payout", "whatif-impact-percent", "team-whatif-payout", "pipeline-coverage"]
    elif intent == "deal_slip_analysis":
        preferred_order = ["deal-slip-risk-breakdown", "deal-slip-top-risk", "pipeline-coverage"]
    elif intent == "deal_velocity_trends":
        preferred_order = ["deal-velocity-trend", "sales-cycle-trend"]
    elif intent == "forecast_question":
        preferred_order = ["forecast-accuracy-mape", "pipeline-coverage", "deal-slip-risk-breakdown"]
    elif intent == "business_diagnostic_question":
        preferred_order = ["forecast-accuracy-mape", "pipeline-coverage", "deal-slip-risk-breakdown", "quota-risk-at-risk-reps"]
    else:
        preferred_order = []

    if preferred_order:
        rank = {chart_id: idx for idx, chart_id in enumerate(preferred_order)}
        charts.sort(key=lambda c: rank.get(str(c.get("id")), len(rank) + 1))

    unique: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for chart in charts:
        chart_id = str(chart.get("id") or "")
        if not chart_id or chart_id in seen_ids:
            continue
        if not chart.get("data"):
            continue
        seen_ids.add(chart_id)
        unique.append(chart)

    return unique[: max(1, max_charts)]
