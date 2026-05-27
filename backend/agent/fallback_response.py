from __future__ import annotations

from typing import Any


def _format_currency(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:,.0f}"


def _truncate(text: str, max_chars: int = 140) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3] + "..."


def _collect_key_metrics(evidence: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in evidence:
        tool_name = item.get("tool_name")
        data = item.get("data")
        if not isinstance(data, (dict, list)):
            continue

        if tool_name == "get_sales_kpis":
            revenue = float(data.get("total_revenue", 0.0) or 0.0)
            attainment = float(data.get("attainment_pct", 0.0) or 0.0)
            coverage = float(data.get("pipeline_coverage", 0.0) or 0.0)
            lines.append(
                f"total revenue is {_format_currency(revenue)}, quota attainment is {attainment:.1f}%, and pipeline coverage is {coverage:.2f}x"
            )
        elif tool_name == "get_pipeline_summary":
            pipeline = float(data.get("open_pipeline", 0.0) or 0.0)
            coverage = float(data.get("pipeline_coverage", 0.0) or 0.0)
            lines.append(f"open pipeline is {_format_currency(pipeline)} with coverage {coverage:.2f}x")
        elif tool_name == "get_revenue_by_region":
            rows: list[dict[str, Any]] = []
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, dict) and isinstance(data.get("rows"), list):
                rows = [r for r in data.get("rows", []) if isinstance(r, dict)]
            if rows:
                normalized = [
                    {
                        "region": str(r.get("region") or "Unknown"),
                        "revenue": float(r.get("revenue", 0.0) or 0.0),
                    }
                    for r in rows
                ]
                total = sum(r["revenue"] for r in normalized)
                top = max(normalized, key=lambda r: r["revenue"])
                if total > 0:
                    share = (top["revenue"] / total) * 100.0
                    lines.append(
                        f"strongest region is {top['region']} at {_format_currency(top['revenue'])} ({share:.1f}% of regional revenue mix)"
                    )
        elif tool_name == "get_forecast_summary":
            pred = data.get("prediction", {}) if isinstance(data.get("prediction"), dict) else {}
            fc = pred.get("forecast_values", []) if isinstance(pred.get("forecast_values", []), list) else []
            if fc:
                lines.append(f"latest forecast next value is {_format_currency(float(fc[0]))}")
            accuracy = data.get("accuracy_backtest") if isinstance(data.get("accuracy_backtest"), dict) else {}
            if accuracy:
                status = str(accuracy.get("status") or "")
                if status == "ok":
                    mape = accuracy.get("mape")
                    folds = int(accuracy.get("folds", 0) or 0)
                    if mape is not None:
                        lines.append(f"overall forecast backtest accuracy is MAPE {float(mape):.1f}% across {folds} folds")
                else:
                    lines.append("overall forecast backtest accuracy is unavailable due to insufficient monthly history")

            quarter_accuracy = data.get("quarter_accuracy") if isinstance(data.get("quarter_accuracy"), dict) else {}
            if quarter_accuracy:
                period = str(quarter_accuracy.get("period") or "requested quarter")
                q_status = str(quarter_accuracy.get("status") or "")
                if q_status == "ok":
                    q_mape = quarter_accuracy.get("mape")
                    q_points = int(quarter_accuracy.get("points", 0) or 0)
                    if q_mape is not None:
                        lines.append(
                            f"{period} forecast accuracy is MAPE {float(q_mape):.1f}% over {q_points} monthly points"
                        )
                else:
                    lines.append(f"{period} forecast accuracy could not be computed confidently with available history")

            forecast_message = str(data.get("message") or "").strip()
            if forecast_message:
                lines.append(_truncate(forecast_message, max_chars=120))
        elif tool_name == "get_deal_risk_summary":
            hi = int(data.get("high_risk_count", 0) or 0)
            med = int(data.get("medium_risk_count", 0) or 0)
            low = int(data.get("low_risk_count", 0) or 0)
            lines.append(f"deal risk distribution is high={hi}, medium={med}, low={low}")
        elif tool_name == "get_deal_slip_analysis":
            slip_count = int(data.get("slip_risk_count", 0) or 0)
            slip_pct = float(data.get("slip_risk_pct", 0.0) or 0.0)
            at_risk_value = float(data.get("total_amount_at_risk", 0.0) or 0.0)
            lines.append(
                f"deal slip analysis shows {slip_count} deals at slip risk ({slip_pct:.1f}%), with about {_format_currency(at_risk_value)} at risk"
            )
            top_deals = data.get("top_at_risk_deals", []) if isinstance(data.get("top_at_risk_deals"), list) else []
            if top_deals:
                top = top_deals[0]
                top_name = top.get("deal_name") or "top risk deal"
                score = float(top.get("slip_risk_score", 0.0) or 0.0)
                lines.append(f"highest slip-risk deal is {top_name} with score {score:.2f}")
        elif tool_name == "get_deal_velocity_trends":
            direction = str(data.get("direction") or "flat")
            change_pct = float(data.get("change_pct", 0.0) or 0.0)
            latest = data.get("latest") if isinstance(data.get("latest"), dict) else {}
            latest_period = latest.get("period") or "latest period"
            latest_velocity = float(latest.get("deal_velocity", 0.0) or 0.0)
            latest_cycle = latest.get("avg_cycle_days")
            lines.append(
                f"deal velocity trend is {direction} ({change_pct:.1f}% change), latest velocity is {_format_currency(latest_velocity)} in {latest_period}"
            )
            if latest_cycle is not None:
                lines.append(f"latest average sales cycle is {float(latest_cycle):.1f} days")
        elif tool_name == "get_top_reps":
            rows: list[dict[str, Any]] = []
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, dict) and isinstance(data.get("rows"), list):
                rows = [r for r in data.get("rows", []) if isinstance(r, dict)]
            if rows:
                top = rows[0]
                top_name = top.get("name") or "top rep"
                top_att = float(top.get("attainment_pct", 0.0) or 0.0)
                lines.append(f"top rep is {top_name} at {top_att:.1f}% attainment")
                avg_att = sum(float(r.get("attainment_pct", 0.0) or 0.0) for r in rows) / len(rows)
                lines.append(f"average attainment across top cohort is {avg_att:.1f}%")
        elif tool_name == "get_underperforming_reps":
            rows = []
            if isinstance(data, list):
                rows = [r for r in data if isinstance(r, dict)]
            elif isinstance(data, dict) and isinstance(data.get("rows"), list):
                rows = [r for r in data.get("rows", []) if isinstance(r, dict)]
            if rows:
                lines.append(f"{len(rows)} reps are currently below target attainment")
                worst = min(rows, key=lambda r: float(r.get("attainment_pct", 0.0) or 0.0))
                lines.append(
                    f"lowest-attainment rep in the flagged group is {worst.get('name', 'unknown')} at {float(worst.get('attainment_pct', 0.0) or 0.0):.1f}%"
                )
            else:
                lines.append("no reps are currently flagged as underperforming by the configured threshold")
        elif tool_name == "get_payout_summary":
            summary = data.get("summary", {}) if isinstance(data.get("summary"), dict) else {}
            total = float(summary.get("total_payout", 0.0) or 0.0)
            rep_count = int(summary.get("rep_count", 0) or 0)
            if rep_count > 0:
                lines.append(f"team payout is {_format_currency(total)} across {rep_count} reps")
        elif tool_name == "get_pipeline_coverage_check":
            weighted = float(data.get("weighted_coverage_ratio", 0.0) or 0.0)
            unweighted = float(data.get("unweighted_coverage_ratio", 0.0) or 0.0)
            lines.append(f"pipeline coverage is {unweighted:.2f}x unweighted and {weighted:.2f}x weighted")
        elif tool_name == "get_pipeline_rescue_what_if":
            scenario = data.get("scenario") if isinstance(data.get("scenario"), dict) else {}
            impact = data.get("incremental_impact") if isinstance(data.get("incremental_impact"), dict) else {}
            deals = data.get("priority_deals") if isinstance(data.get("priority_deals"), list) else []
            reps = data.get("priority_reps") if isinstance(data.get("priority_reps"), list) else []

            top_n = int(scenario.get("top_n_at_risk_deals", 0) or 0)
            target_cov = float(scenario.get("target_weighted_coverage", 0.0) or 0.0)
            current_cov = float(scenario.get("current_weighted_coverage", 0.0) or 0.0)
            projected_cov = float(scenario.get("weighted_coverage_after_rescue", 0.0) or 0.0)
            remaining_gap = float(scenario.get("remaining_weighted_gap", 0.0) or 0.0)

            exp_rev = float(impact.get("expected_incremental_closed_revenue", 0.0) or 0.0)
            best_rev = float(impact.get("best_case_incremental_closed_revenue", 0.0) or 0.0)
            att_lift = float(impact.get("quota_attainment_lift_expected_pct_points", 0.0) or 0.0)
            additional_gross = impact.get("additional_gross_pipeline_needed_at_same_efficiency")

            lines.append(
                f"rescuing top {top_n} at-risk deals moves weighted coverage from {current_cov:.2f}x toward {target_cov:.2f}x, reaching {projected_cov:.2f}x"
            )
            lines.append(
                f"expected incremental closed revenue is {_format_currency(exp_rev)} (best case {_format_currency(best_rev)}), with expected attainment lift {att_lift:+.1f} points"
            )

            if remaining_gap > 0:
                lines.append(f"remaining weighted coverage gap after rescue is {_format_currency(remaining_gap)}")
            else:
                lines.append("target weighted coverage is reached with the selected at-risk deal rescue set")

            if additional_gross is not None:
                lines.append(
                    f"additional gross pipeline needed at current conversion efficiency is {_format_currency(float(additional_gross))}"
                )

            if deals:
                top_deals = "; ".join(
                    f"{d.get('deal_name', 'Deal')} ({_format_currency(float(d.get('weighted_value', 0.0) or 0.0))} weighted)"
                    for d in deals[:3]
                )
                lines.append(f"priority deals to rescue first: {top_deals}")

            if reps:
                top_reps = "; ".join(
                    f"{r.get('rep_name', 'Rep')} ({_format_currency(float(r.get('weighted_value', 0.0) or 0.0))} weighted across {int(r.get('deals', 0) or 0)} deals)"
                    for r in reps[:3]
                )
                lines.append(f"priority reps to focus coaching and deal support: {top_reps}")
        elif tool_name == "get_team_pipeline_coverage_attainment":
            criteria = data.get("criteria") if isinstance(data.get("criteria"), dict) else {}
            min_cov_raw = criteria.get("min_pipeline_coverage")
            max_cov_raw = criteria.get("max_pipeline_coverage")
            max_att_raw = criteria.get("max_attainment_pct")
            min_att_raw = criteria.get("min_attainment_pct")
            sort_by = str(criteria.get("sort_by") or "match_priority")
            top_n = int(criteria.get("limit", 0) or 0)

            min_cov = float(min_cov_raw) if min_cov_raw is not None else None
            max_cov = float(max_cov_raw) if max_cov_raw is not None else None
            max_att = float(max_att_raw) if max_att_raw is not None else None
            min_att = float(min_att_raw) if min_att_raw is not None else None

            criteria_text = str(data.get("criteria_text") or "").strip()
            if not criteria_text:
                criteria_parts: list[str] = []
                if min_cov is not None and max_cov is not None:
                    criteria_parts.append(f"coverage between {min_cov:.1f}x and {max_cov:.1f}x")
                elif min_cov is not None:
                    criteria_parts.append(f"coverage >= {min_cov:.1f}x")
                elif max_cov is not None:
                    criteria_parts.append(f"coverage <= {max_cov:.1f}x")

                if max_att is not None and min_att is not None:
                    logic = "or" if str(criteria.get("attainment_logic") or "and").lower() == "or" else "and"
                    criteria_parts.append(f"attainment <= {max_att:.0f}% {logic} attainment >= {min_att:.0f}%")
                elif max_att is not None:
                    criteria_parts.append(f"attainment <= {max_att:.0f}%")
                elif min_att is not None:
                    criteria_parts.append(f"attainment >= {min_att:.0f}%")

                criteria_text = ", ".join(criteria_parts)

            matches = data.get("matches") if isinstance(data.get("matches"), list) else []
            if matches:
                previews: list[str] = []
                for row in matches[:3]:
                    if not isinstance(row, dict):
                        continue
                    team_name = str(row.get("team_name") or "Team")
                    cov = float(row.get("pipeline_coverage", 0.0) or 0.0)
                    att = float(row.get("attainment_pct", 0.0) or 0.0)
                    previews.append(f"{team_name} ({cov:.2f}x coverage, {att:.1f}% attainment)")
                if previews:
                    if sort_by == "coverage_desc" and top_n > 0:
                        descriptor = f"top {top_n} teams by pipeline coverage"
                        if criteria_text:
                            lines.append(f"{descriptor} ({criteria_text}): " + "; ".join(previews))
                        else:
                            lines.append(f"{descriptor}: " + "; ".join(previews))
                    else:
                        descriptor = "teams with high pipeline coverage but low quota attainment"
                        if criteria_text:
                            lines.append(f"{descriptor} ({criteria_text}): " + "; ".join(previews))
                        else:
                            lines.append(descriptor + ": " + "; ".join(previews))
            else:
                if criteria_text:
                    lines.append(f"no teams currently match high-coverage/low-attainment criteria ({criteria_text})")
                else:
                    lines.append("no teams currently match high-coverage/low-attainment criteria")
        elif tool_name == "get_quota_risk_summary":
            risk_count = int(data.get("at_risk_rep_count", 0) or 0)
            lines.append(f"quota risk scan identified {risk_count} at-risk reps")
        elif tool_name == "get_arr_trajectory":
            nrr = float(data.get("nrr_pct", 0.0) or 0.0)
            growth = float(data.get("arr_growth_pct", 0.0) or 0.0)
            health = data.get("health_assessment", "unknown")
            lines.append(f"ARR trajectory health is {health} with NRR {nrr:.1f}% and ARR growth {growth:.1f}%")
        elif tool_name == "get_rep_ramp_status":
            ramping = int(data.get("ramping_rep_count", 0) or 0)
            lines.append(f"{ramping} reps are currently in ramp")
        elif tool_name == "get_rep_quota_bonus_what_if":
            matched_rep = data.get("matched_rep") if isinstance(data.get("matched_rep"), dict) else None
            scenario = data.get("quota_target_scenario") if isinstance(data.get("quota_target_scenario"), dict) else {}
            scenario_inputs = data.get("scenario_inputs") if isinstance(data.get("scenario_inputs"), dict) else {}
            if matched_rep:
                rep_name = matched_rep.get("name") or "rep"
                gap = float(scenario.get("gap_to_target", 0.0) or 0.0)
                target_bonus = float(scenario.get("projected_bonus_if_target_hit", 0.0) or 0.0)
                target_payout = float(scenario.get("projected_payout_if_target_hit", 0.0) or 0.0)
                lines.append(
                    f"for {rep_name}, estimated payout at target is {_format_currency(target_payout)} with projected bonus {_format_currency(target_bonus)}"
                )
                if gap > 0:
                    lines.append(f"remaining revenue needed to target is {_format_currency(gap)}")

            if scenario_inputs:
                input_parts: list[str] = []
                close_lift = float(scenario_inputs.get("close_rate_lift_pct", 0.0) or 0.0)
                close_target = scenario_inputs.get("close_rate_target_pct")
                cycle_cut = float(scenario_inputs.get("sales_cycle_days_reduction", 0.0) or 0.0)
                pipeline_lift = float(scenario_inputs.get("pipeline_lift_pct", 0.0) or 0.0)
                pipeline_add = float(scenario_inputs.get("pipeline_delta_amount", 0.0) or 0.0)
                deal_size_lift = float(scenario_inputs.get("deal_size_lift_pct", 0.0) or 0.0)
                if close_target is not None:
                    input_parts.append(f"win-rate target {float(close_target):.1f}%")
                elif close_lift > 0:
                    input_parts.append(f"close-rate lift {close_lift:.1f}%")
                if cycle_cut > 0:
                    input_parts.append(f"sales-cycle reduction {cycle_cut:.1f} days")
                if pipeline_lift > 0:
                    input_parts.append(f"pipeline lift {pipeline_lift:.1f}%")
                if pipeline_add > 0:
                    input_parts.append(f"pipeline add {_format_currency(pipeline_add)}")
                if deal_size_lift > 0:
                    input_parts.append(f"deal-size lift {deal_size_lift:.1f}%")
                if input_parts:
                    lines.append("scenario inputs understood: " + ", ".join(input_parts))

            driver = data.get("driver_scenario") if isinstance(data.get("driver_scenario"), dict) else {}
            baseline = driver.get("baseline") if isinstance(driver.get("baseline"), dict) else {}
            projected = driver.get("projected") if isinstance(driver.get("projected"), dict) else {}
            impact = driver.get("impact") if isinstance(driver.get("impact"), dict) else {}
            if baseline and projected and impact:
                att_before = float(baseline.get("attainment_pct", 0.0) or 0.0)
                att_after = float(projected.get("attainment_pct", 0.0) or 0.0)
                att_delta = float(impact.get("attainment_delta_pct_points", 0.0) or 0.0)
                payout_delta = float(impact.get("payout_delta", 0.0) or 0.0)
                bonus_delta = float(impact.get("bonus_delta", 0.0) or 0.0)
                slip_before = float(baseline.get("slip_risk_pct", 0.0) or 0.0)
                slip_after = float(projected.get("slip_risk_pct", 0.0) or 0.0)
                slip_delta = float(impact.get("slip_risk_delta_pct_points", 0.0) or 0.0)
                lines.append(
                    f"what-if impact: attainment {att_before:.1f}% -> {att_after:.1f}% ({att_delta:+.1f} pts), payout delta {_format_currency(payout_delta)}, bonus delta {_format_currency(bonus_delta)}"
                )
                lines.append(
                    f"deal-slippage risk shifts from {slip_before:.1f}% to {slip_after:.1f}% ({slip_delta:+.1f} pts)"
                )

            team = data.get("team_scenario") if isinstance(data.get("team_scenario"), dict) else {}
            team_baseline = team.get("baseline") if isinstance(team.get("baseline"), dict) else {}
            team_projected = team.get("projected") if isinstance(team.get("projected"), dict) else {}
            team_impact = team.get("impact") if isinstance(team.get("impact"), dict) else {}
            if team_baseline and team_projected and team_impact:
                base_payout = float(team_baseline.get("total_payout", 0.0) or 0.0)
                proj_payout = float(team_projected.get("total_payout", 0.0) or 0.0)
                payout_delta = float(team_impact.get("payout_delta", 0.0) or 0.0)
                att_before = float(team_baseline.get("attainment_pct", 0.0) or 0.0)
                att_after = float(team_projected.get("attainment_pct", 0.0) or 0.0)
                att_delta = float(team_impact.get("attainment_delta_pct_points", 0.0) or 0.0)
                lines.append(
                    f"team-level what-if: payout potential {_format_currency(base_payout)} -> {_format_currency(proj_payout)} ({_format_currency(payout_delta)} delta)"
                )
                lines.append(
                    f"team attainment shifts {att_before:.1f}% -> {att_after:.1f}% ({att_delta:+.1f} pts)"
                )

            actions = data.get("action_plan") if isinstance(data.get("action_plan"), list) else []
            if actions:
                lines.append("next-best actions: " + " | ".join(_truncate(str(a), max_chars=120) for a in actions[:2]))
        elif tool_name == "get_plans_rules_catalog":
            plan_count = int(data.get("plan_count", 0) or 0)
            rule_count = int(data.get("rule_count", 0) or 0)
            lines.append(f"compensation catalog currently has {plan_count} plans and {rule_count} rules")

            plans = data.get("plans") if isinstance(data.get("plans"), list) else []
            if plans:
                preview = []
                for p in plans[:5]:
                    if not isinstance(p, dict):
                        continue
                    name = str(p.get("name") or "Unnamed Plan")
                    rcount = int(p.get("rule_count", 0) or 0)
                    preview.append(f"{name} ({rcount} rules)")
                if preview:
                    lines.append("available plans include: " + "; ".join(preview))

            rules = data.get("rules") if isinstance(data.get("rules"), list) else []
            if rules:
                rule_preview = []
                for r in rules[:5]:
                    if not isinstance(r, dict):
                        continue
                    rule_name = str(r.get("name") or "rule")
                    plan_name = str(r.get("plan_name") or "unknown plan")
                    metric = str(r.get("metric_name") or "metric")
                    rule_preview.append(f"{rule_name} ({plan_name}, metric={metric})")
                if rule_preview:
                    lines.append("sample rules: " + "; ".join(rule_preview))
        elif tool_name == "get_plan_performance_summary":
            matched = data.get("matched_plan") if isinstance(data.get("matched_plan"), dict) else None
            perf = data.get("performance") if isinstance(data.get("performance"), dict) else None
            if matched and perf:
                plan_name = matched.get("name") or "plan"
                period = perf.get("period") or data.get("period_used") or "selected period"
                total_revenue = float(perf.get("total_revenue", 0.0) or 0.0)
                total_quota = float(perf.get("total_quota", 0.0) or 0.0)
                attainment = float(perf.get("attainment_pct", 0.0) or 0.0)
                lines.append(
                    f"{plan_name} performance for {period}: revenue {_format_currency(total_revenue)}, quota {_format_currency(total_quota)}, attainment {attainment:.1f}%"
                )

                top_reps = data.get("top_reps") if isinstance(data.get("top_reps"), list) else []
                if top_reps:
                    top = top_reps[0]
                    top_name = top.get("name") or "top rep"
                    top_revenue = float(top.get("revenue", 0.0) or 0.0)
                    lines.append(f"top contributor in this plan is {top_name} with {_format_currency(top_revenue)} revenue")
            else:
                candidates = data.get("candidate_plans") if isinstance(data.get("candidate_plans"), list) else []
                if candidates:
                    lines.append("I could not uniquely match a plan name. Available plans include: " + "; ".join(str(c) for c in candidates[:5]))
        elif tool_name == "get_metric_definition":
            metric_name = str(data.get("name") or data.get("metric_name") or "metric")
            description = _truncate(str(data.get("description") or ""), max_chars=160)
            formula = _truncate(str(data.get("formula") or ""), max_chars=160)
            if description:
                lines.append(f"{metric_name} definition: {description}")
            if formula:
                lines.append(f"{metric_name} formula: {formula}")
        elif tool_name == "retrieve_knowledge_context":
            chunks: list[dict[str, Any]] = []
            if isinstance(data, list):
                chunks = [c for c in data if isinstance(c, dict)]
            if chunks:
                highlights: list[str] = []
                for chunk in chunks[:2]:
                    heading = str(chunk.get("heading") or chunk.get("source_document") or "knowledge base")
                    snippet = _truncate(str(chunk.get("content") or ""), max_chars=120)
                    if snippet:
                        highlights.append(f"{heading}: {snippet}")
                if highlights:
                    lines.append("knowledge base guidance includes " + " | ".join(highlights))
    return lines


def _select_relevant_lines(intent: str, lines: list[str]) -> list[str]:
    if not lines:
        return []

    # Deduplicate while preserving order.
    deduped: list[str] = []
    seen: set[str] = set()
    for line in lines:
        key = line.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(line)

    default_limit = 6
    priority_terms: list[str] = []

    if intent == "pipeline_rescue_whatif":
        priority_terms = [
            "moves weighted coverage",
            "expected incremental closed revenue",
            "remaining weighted coverage gap",
            "target weighted coverage is reached",
            "additional gross pipeline needed",
            "priority deals to rescue first",
            "priority reps to focus coaching",
            "pipeline coverage is",
            "deal slip analysis",
        ]
        default_limit = 9
    elif intent == "rep_quota_whatif":
        priority_terms = [
            "estimated payout at target",
            "scenario inputs understood",
            "what-if impact",
            "deal-slippage risk shifts",
            "team-level what-if",
            "team attainment shifts",
            "remaining revenue needed to target",
            "deal slip analysis",
            "next-best actions",
            "performance for",
            "top contributor",
            "quota risk scan identified",
            "pipeline coverage",
        ]
        default_limit = 8
    elif intent == "plan_performance_question":
        priority_terms = [
            "performance for",
            "top contributor",
            "pipeline coverage",
            "quota risk",
        ]
        default_limit = 5
    elif intent == "deal_velocity_trends":
        priority_terms = [
            "deal velocity trend",
            "latest average sales cycle",
            "pipeline coverage",
        ]
        default_limit = 5
    elif intent == "forecast_question":
        priority_terms = [
            "forecast accuracy is mape",
            "overall forecast backtest accuracy",
            "latest forecast next value",
            "deal risk distribution",
        ]
        default_limit = 6
    elif intent == "business_diagnostic_question":
        priority_terms = [
            "total revenue is",
            "overall forecast backtest accuracy",
            "forecast accuracy is mape",
            "pipeline coverage is",
            "quota risk scan identified",
            "deal slip analysis",
            "deal velocity trend",
            "top rep is",
            "below target attainment",
            "strongest region is",
            "knowledge base guidance includes",
        ]
        default_limit = 10

    if not priority_terms:
        return deduped[:default_limit]

    def _rank(line: str) -> tuple[int, int]:
        lower = line.lower()
        for idx, term in enumerate(priority_terms):
            if term in lower:
                return idx, 0
        return len(priority_terms), 1

    ranked = sorted(enumerate(deduped), key=lambda p: (_rank(p[1]), p[0]))
    selected = [line for _, line in ranked[:default_limit]]

    # Keep original appearance order for better readability.
    selected_set = {s.lower() for s in selected}
    ordered = [line for line in deduped if line.lower() in selected_set]
    return ordered


def _summarize_warnings(warnings: list[str], limit: int = 4) -> list[str]:
    if not warnings:
        return []

    deduped: list[str] = []
    seen: set[str] = set()
    for warning in warnings:
        cleaned = " ".join(str(warning or "").split())
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(cleaned)

    notes: list[str] = []

    rolled_up_count = sum(1 for w in deduped if "quota_source=rolled_up_from_quarterly" in w)
    if rolled_up_count:
        notes.append(f"{rolled_up_count} reps used rolled-up quarterly quota mappings.")

    has_sparse_closed = any(
        token in w
        for w in deduped
        for token in [
            "No closed deals found for win-rate calculation",
            "No closed won deals found for average deal size",
            "Average deal size inferred",
            "Win rate inferred",
            "Average sales cycle estimated",
        ]
    )
    if has_sparse_closed:
        notes.append("Sparse closed-deal history in the selected period; some rates/sizes/cycle values were inferred.")

    projection_warning = next((w for w in deduped if "Projection used quota-scaled effective pipeline" in w), None)
    if projection_warning:
        notes.append(_truncate(projection_warning, max_chars=130))

    annual_quota_warning = next((w for w in deduped if "No annual quota" in w), None)
    if annual_quota_warning:
        notes.append(annual_quota_warning)

    key_warning_terms = [
        "quota-at-risk",
        "no mapped reps",
        "pipeline coverage health",
        "No safe knowledge base chunks",
        "RAG guardrail",
    ]
    for warning in deduped:
        if any(term in warning for term in key_warning_terms):
            notes.append(warning)

    # Include additional short warnings if space remains, skipping noisy internals.
    skip_terms = [
        "quota_source=rolled_up_from_quarterly",
        "Pipeline scoped to deals",
        "Returned deterministic",
    ]
    for warning in deduped:
        if len(notes) >= limit:
            break
        if any(term in warning for term in skip_terms):
            continue
        if warning not in notes:
            notes.append(_truncate(warning, max_chars=130))

    # Final dedupe + limit.
    final: list[str] = []
    final_seen: set[str] = set()
    for note in notes:
        key = note.lower()
        if key in final_seen:
            continue
        final_seen.add(key)
        final.append(note)
        if len(final) >= limit:
            break

    return final


def _build_pipeline_rescue_whatif_response(
    evidence: list[dict[str, Any]],
    tools_used: list[str],
    warnings: list[str],
) -> str | None:
    rescue_data: dict[str, Any] | None = None
    coverage_data: dict[str, Any] | None = None
    slip_data: dict[str, Any] | None = None

    for item in evidence:
        tool = str(item.get("tool_name") or "")
        data = item.get("data") if isinstance(item.get("data"), dict) else None
        if not data:
            continue
        if tool == "get_pipeline_rescue_what_if":
            rescue_data = data
        elif tool == "get_pipeline_coverage_check":
            coverage_data = data
        elif tool == "get_deal_slip_analysis":
            slip_data = data

    if not rescue_data:
        return None

    scenario = rescue_data.get("scenario") if isinstance(rescue_data.get("scenario"), dict) else {}
    reconciliation = (
        rescue_data.get("input_reconciliation")
        if isinstance(rescue_data.get("input_reconciliation"), dict)
        else {}
    )
    impact = rescue_data.get("incremental_impact") if isinstance(rescue_data.get("incremental_impact"), dict) else {}
    deals = rescue_data.get("priority_deals") if isinstance(rescue_data.get("priority_deals"), list) else []
    reps = rescue_data.get("priority_reps") if isinstance(rescue_data.get("priority_reps"), list) else []
    slip_universe = rescue_data.get("slip_universe") if isinstance(rescue_data.get("slip_universe"), dict) else {}

    top_n = int(scenario.get("top_n_at_risk_deals", 0) or 0)
    target_cov = float(scenario.get("target_weighted_coverage", 0.0) or 0.0)
    current_cov = float(scenario.get("current_weighted_coverage", 0.0) or 0.0)
    projected_cov = float(scenario.get("weighted_coverage_after_rescue", 0.0) or 0.0)
    remaining_gap = float(scenario.get("remaining_weighted_gap", 0.0) or 0.0)
    requested_baseline = reconciliation.get("requested_baseline_weighted_coverage")
    if requested_baseline is None:
        requested_baseline = scenario.get("requested_baseline_weighted_coverage")

    requested_baseline = float(requested_baseline) if requested_baseline is not None else None
    baseline_delta = reconciliation.get("delta_weighted_coverage")
    baseline_delta = float(baseline_delta) if baseline_delta is not None else None
    baseline_mismatch = bool(reconciliation.get("baseline_mismatch", False))
    target_already_met = bool(reconciliation.get("target_already_met_pre_rescue", False))

    expected_rev = float(impact.get("expected_incremental_closed_revenue", 0.0) or 0.0)
    best_case_rev = float(impact.get("best_case_incremental_closed_revenue", 0.0) or 0.0)
    att_before = float(impact.get("quota_attainment_before_pct", 0.0) or 0.0)
    att_after = float(impact.get("quota_attainment_after_expected_pct", 0.0) or 0.0)
    att_lift = float(impact.get("quota_attainment_lift_expected_pct_points", 0.0) or 0.0)
    efficiency = float(impact.get("weighted_to_gross_efficiency_pct", 0.0) or 0.0)
    additional_gross_needed = impact.get("additional_gross_pipeline_needed_at_same_efficiency")

    sections: list[str] = [
        "Here is a deterministic evidence-backed scenario analysis.",
        "",
        "Baseline:",
    ]

    if coverage_data:
        unweighted = float(coverage_data.get("unweighted_coverage_ratio", 0.0) or 0.0)
        weighted = float(coverage_data.get("weighted_coverage_ratio", 0.0) or 0.0)
        sections.append(f"- Pipeline coverage is {unweighted:.2f}x unweighted and {weighted:.2f}x weighted.")
    else:
        sections.append(f"- Current weighted pipeline coverage is {current_cov:.2f}x.")

    if slip_data:
        slip_count = int(slip_data.get("slip_risk_count", 0) or 0)
        slip_pct = float(slip_data.get("slip_risk_pct", 0.0) or 0.0)
        sections.append(f"- Deal slip analysis shows {slip_count} deals at risk ({slip_pct:.1f}%).")
    elif slip_universe:
        slip_count = int(slip_universe.get("slip_risk_count", 0) or 0)
        slip_pct = float(slip_universe.get("slip_risk_pct", 0.0) or 0.0)
        sections.append(f"- Slip-risk universe includes {slip_count} at-risk deals ({slip_pct:.1f}%).")

    if baseline_mismatch or target_already_met:
        sections.extend(["", "Input Reconciliation:"])
        if baseline_mismatch and requested_baseline is not None:
            delta_label = f" ({baseline_delta:+.2f}x delta)" if baseline_delta is not None else ""
            sections.append(
                f"- Prompt baseline {requested_baseline:.2f}x does not match actual current {current_cov:.2f}x{delta_label}."
            )
        if target_already_met:
            sections.append(
                f"- Prompt target {target_cov:.2f}x is already met before rescue based on current {current_cov:.2f}x."
            )
        sections.append("- Calculations below use the actual current weighted coverage baseline from live data.")

    sections.extend([
        "",
        f"Scenario (Rescue Top {top_n} At-Risk Deals Toward {target_cov:.2f}x Weighted Coverage):",
        f"- Expected incremental closed revenue: {_format_currency(expected_rev)}.",
        f"- Best-case incremental closed revenue: {_format_currency(best_case_rev)}.",
        f"- Quota attainment impact: {att_before:.1f}% -> {att_after:.1f}% ({att_lift:+.1f} pts expected).",
        f"- Weighted coverage impact: {current_cov:.2f}x -> {projected_cov:.2f}x.",
    ])

    sections.extend(["", "Gap To Target:"])
    if remaining_gap > 0:
        sections.append(f"- Remaining weighted coverage gap: {_format_currency(remaining_gap)}.")
    else:
        sections.append("- Target weighted coverage reached with the selected rescue set.")

    if additional_gross_needed is not None:
        sections.append(
            f"- Additional gross pipeline needed at current {efficiency:.1f}% weighted efficiency: "
            f"{_format_currency(float(additional_gross_needed))}."
        )

    sections.extend(["", "Priority Deals (First 5):"])
    for idx, deal in enumerate(deals[:5], start=1):
        name = str(deal.get("deal_name") or "Deal")
        rep_name = str(deal.get("rep_name") or "Unknown")
        weighted_val = float(deal.get("weighted_value", 0.0) or 0.0)
        amount = float(deal.get("amount", 0.0) or 0.0)
        sections.append(
            f"{idx}. {name} ({rep_name}) - {_format_currency(weighted_val)} weighted on {_format_currency(amount)} gross."
        )

    sections.extend(["", "Priority Reps (First 5):"])
    for idx, rep in enumerate(reps[:5], start=1):
        rep_name = str(rep.get("rep_name") or "Rep")
        deals_count = int(rep.get("deals", 0) or 0)
        weighted_val = float(rep.get("weighted_value", 0.0) or 0.0)
        deal_label = "deal" if deals_count == 1 else "deals"
        sections.append(
            f"{idx}. {rep_name} - {deals_count} {deal_label}, {_format_currency(weighted_val)} weighted impact."
        )

    sections.extend([
        "",
        "Recommended Actions:",
        "- Run rescue plays first on high weighted-value deals in Proposal/Negotiation stages.",
        "- Assign daily deal-desk support to top-priority reps until close-date risk is reduced.",
        "- Recompute weighted coverage weekly and expand rescue scope if remaining gap persists.",
    ])

    if tools_used:
        sections.append("")
        sections.append(f"Tools used: {', '.join(tools_used)}.")

    warning_input = list(warnings)
    if baseline_mismatch or target_already_met:
        warning_input = [w for w in warning_input if "Scenario input mismatch:" not in str(w)]

    warning_notes = _summarize_warnings(warning_input, limit=4)
    if warning_notes:
        sections.append("")
        sections.append("Data quality notes:")
        for note in warning_notes:
            sections.append(f"- {note}")

    return "\n".join(sections)


def build_deterministic_response(
    intent: str,
    evidence: list[dict[str, Any]],
    tools_used: list[str],
    warnings: list[str],
) -> str:
    if intent == "pipeline_rescue_whatif":
        specialized = _build_pipeline_rescue_whatif_response(
            evidence=evidence,
            tools_used=tools_used,
            warnings=warnings,
        )
        if specialized:
            return specialized

    evidence_lines = _collect_key_metrics(evidence)
    if not evidence_lines:
        return "Insufficient data available to answer this confidently."

    key_lines = _select_relevant_lines(intent, evidence_lines)
    action_items: list[str] = []
    narrative_lines: list[str] = []
    for line in key_lines:
        if line.lower().startswith("next-best actions:"):
            payload = line.split(":", 1)[1] if ":" in line else ""
            actions = [a.strip() for a in payload.split("|") if a.strip()]
            action_items.extend(actions[:3])
        else:
            narrative_lines.append(line)

    sections: list[str] = ["Here is a deterministic evidence-backed summary.", "", "Key takeaways:"]
    for line in narrative_lines:
        sections.append(f"- {line}")

    if action_items:
        sections.append("")
        sections.append("next-best actions:")
        for action in action_items:
            sections.append(f"- {action}")

    if tools_used:
        sections.append("")
        sections.append(f"Tools used: {', '.join(tools_used)}.")

    warning_notes = _summarize_warnings(warnings, limit=4)
    if warning_notes:
        sections.append("")
        sections.append("Data quality notes:")
        for note in warning_notes:
            sections.append(f"- {note}")

    return "\n".join(sections)