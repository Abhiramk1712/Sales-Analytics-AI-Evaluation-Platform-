"""
backend/agent/tools/payout_tools.py
=====================================
Agent tools for payout and commission calculation.
All functions return the standard {tool_name, status, data, warnings, sources} contract.
"""
from __future__ import annotations

from datetime import date
from difflib import SequenceMatcher
import math
import re
import traceback
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models import Rep, Revenue, Quota, Deal
from backend.payout import compute_payout
from backend.payout.engine import DEFAULT_PAYOUT_CONFIG
from backend.metrics import calculators


def _as_tool_result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _extract_target_attainment(message: str, default_value: float = 100.0) -> float:
    msg = _normalize(message)

    quota_scoped = [
        r"(?:quota|attainment)[^\d%]{0,24}(\d{2,3}(?:\.\d+)?)\s*%",
        r"(\d{2,3}(?:\.\d+)?)\s*%[^\n]{0,24}(?:quota|attainment)",
    ]
    for pattern in quota_scoped:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(200.0, float(m.group(1))))

    if any(k in msg for k in ["hit quota", "reach quota", "quota target", "attain quota"]):
        return 100.0

    explicit_target = re.search(r"target\s*(?:at|to)?\s*(\d{2,3}(?:\.\d+)?)\s*%", msg)
    if explicit_target:
        return max(0.0, min(200.0, float(explicit_target.group(1))))

    return default_value


def _extract_close_rate_lift_pct(message: str) -> float:
    msg = _normalize(message)
    patterns = [
        r"(?:close|win)\s*rate[^\d]{0,24}by\s*(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:improve|increase|lift)\s+(?:the\s+)?(?:close|win)\s*rate[^\d]{0,12}(\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(100.0, float(m.group(1))))
    return 0.0


def _extract_close_rate_target_pct(message: str) -> float | None:
    msg = _normalize(message)
    patterns = [
        r"(?:close|win)\s*rate[^\d]{0,24}to\s*(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:at|reach)\s*(\d{1,3}(?:\.\d+)?)\s*%\s*(?:close|win)\s*rate",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(100.0, float(m.group(1))))
    return None


def _extract_pipeline_lift_pct(message: str) -> float:
    msg = _normalize(message)
    patterns = [
        r"(?:pipeline|open\s+pipeline)[^\d]{0,30}(?:by|up)\s*([+-]?\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:increase|grow|boost|raise)\s+(?:the\s+)?(?:pipeline|open\s+pipeline)[^\d]{0,20}([+-]?\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(-100.0, min(500.0, float(m.group(1))))
    return 0.0


def _amount_with_suffix(raw_amount: str, suffix: str | None) -> float:
    value = float((raw_amount or "0").replace(",", ""))
    unit = (suffix or "").lower()
    if unit == "k":
        value *= 1_000.0
    elif unit == "m":
        value *= 1_000_000.0
    return value


def _extract_pipeline_delta_amount(message: str) -> float:
    msg = _normalize(message)
    patterns = [
        r"(?:pipeline|open\s+pipeline)[^\d$]{0,30}(?:by|add|plus|increase(?:d)?(?:\s+by)?|up)\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([km])?(?!\s*%)\b",
        r"add\s*\$?\s*([\d,]+(?:\.\d+)?)\s*([km])?\s*(?:to\s+)?(?:pipeline|open\s+pipeline)",
    ]
    best_value = 0.0
    for pattern in patterns:
        for m in re.finditer(pattern, msg):
            value = max(0.0, _amount_with_suffix(m.group(1), m.group(2)))
            has_suffix = bool((m.group(2) or "").strip())
            has_comma = "," in (m.group(1) or "")
            # Ignore tiny bare numbers likely captured from nearby time phrases (e.g., "by 2 weeks").
            if value < 1_000.0 and not has_suffix and not has_comma:
                continue
            if value > best_value:
                best_value = value
    return best_value


def _extract_deal_size_lift_pct(message: str) -> float:
    msg = _normalize(message)
    patterns = [
        r"(?:deal\s+size|average\s+deal\s+size|asp)[^\d]{0,30}(?:by|up)\s*(\d{1,3}(?:\.\d+)?)\s*%",
        r"(?:increase|grow|boost|raise)\s+(?:the\s+)?(?:deal\s+size|average\s+deal\s+size|asp)[^\d]{0,20}(\d{1,3}(?:\.\d+)?)\s*%",
    ]
    for pattern in patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(300.0, float(m.group(1))))
    return 0.0


def _extract_cycle_days_reduction(message: str) -> float:
    msg = _normalize(message)
    day_patterns = [
        r"(?:sales\s+cycle|cycle(?:\s+time)?)[^\d]{0,24}by\s*(\d{1,3}(?:\.\d+)?)\s*days?",
        r"(?:shorten|reduce|cut)\s+(?:the\s+)?(?:sales\s+cycle|cycle(?:\s+time)?)[^\d]{0,12}(\d{1,3}(?:\.\d+)?)\s*days?",
    ]
    for pattern in day_patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(365.0, float(m.group(1))))

    week_patterns = [
        r"(?:sales\s+cycle|cycle(?:\s+time)?)[^\d]{0,24}by\s*(\d{1,2}(?:\.\d+)?)\s*weeks?",
        r"(?:shorten|reduce|cut)\s+(?:the\s+)?(?:sales\s+cycle|cycle(?:\s+time)?)[^\d]{0,12}(\d{1,2}(?:\.\d+)?)\s*weeks?",
    ]
    for pattern in week_patterns:
        m = re.search(pattern, msg)
        if m:
            return max(0.0, min(365.0, float(m.group(1)) * 7.0))
    return 0.0


def _extract_fiscal_year(message: str) -> int | None:
    msg = _normalize(message)
    fy_match = re.search(r"\bfy\s*(20\d{2})\b", msg)
    if fy_match:
        return int(fy_match.group(1))

    year_match = re.search(r"\b(20\d{2})\b", msg)
    if year_match:
        return int(year_match.group(1))
    return None


def _risk_band_from_pct(value: float) -> str:
    if value >= 35.0:
        return "high"
    if value >= 15.0:
        return "medium"
    return "low"


def _quota_scaled_effective_pipeline(
    open_pipeline: float,
    quota: float,
    gap_to_target: float,
    revenue: float,
) -> tuple[float, float]:
    quota_component = quota * 4.0 if quota > 0 else 0.0
    cap = max(250_000.0, quota_component, gap_to_target * 6.0, revenue * 8.0)
    effective = min(max(open_pipeline, 0.0), cap)
    return effective, cap


_STOPWORDS = {
    "if",
    "for",
    "what",
    "when",
    "where",
    "why",
    "how",
    "hits",
    "hit",
    "quota",
    "bonus",
    "payout",
    "earn",
    "earns",
    "would",
    "should",
    "could",
    "their",
    "they",
    "them",
    "this",
    "that",
    "at",
    "in",
    "on",
    "to",
    "target",
}


def _extract_requested_rep_name(message: str) -> str | None:
    msg = _normalize(message)
    patterns = [
        r"(?:if|for)\s+([a-z]+(?:\s+[a-z]+){1,2})\s+(?:hits?|hit|reaches?|reach|improves?|improve|increases?|increase|reduces?|reduce|shortens?|shorten|earns?|earn|gets?|get|would|can|should|has)\b",
        r"(?:bonus|payout|quota|attainment)\s+(?:for|of)\s+([a-z]+(?:\s+[a-z]+){1,2})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, msg)
        if not m:
            continue
        candidate = " ".join((m.group(1) or "").split())
        tokens = [t for t in re.findall(r"[a-z]{2,}", candidate) if t not in _STOPWORDS]
        if len(tokens) >= 2:
            return " ".join(tokens[:3])
    return None


def _name_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize(a), _normalize(b)).ratio()


async def _find_rep_from_message(
    db: AsyncSession,
    message: str,
) -> tuple[Rep | None, list[dict[str, Any]], str | None]:
    reps = (await db.execute(select(Rep))).scalars().all()
    requested_name = _extract_requested_rep_name(message)
    if not reps:
        return None, [], requested_name

    msg = _normalize(message)
    for rep in reps:
        rep_name = _normalize(rep.name or "")
        if rep_name and rep_name in msg:
            return rep, [], requested_name

    scored: list[tuple[float, int, float, Rep]] = []

    tokens = {t for t in re.findall(r"[a-zA-Z]{3,}", msg) if t.lower() not in _STOPWORDS}
    for rep in reps:
        rep_name_norm = _normalize(rep.name or "")
        if not rep_name_norm:
            continue
        name_tokens = {t for t in re.findall(r"[a-zA-Z]{3,}", rep_name_norm) if t.lower() not in _STOPWORDS}
        overlap = len(tokens.intersection(name_tokens))
        fuzzy = _name_similarity(requested_name, rep_name_norm)
        scored.append((fuzzy, overlap, max(fuzzy, overlap / max(len(name_tokens), 1)), rep))

    if requested_name and scored:
        fuzzy_sorted = sorted(scored, key=lambda x: (x[0], x[1]), reverse=True)
        top = fuzzy_sorted[0]
        second_score = fuzzy_sorted[1][0] if len(fuzzy_sorted) > 1 else 0.0
        if top[0] >= 0.90 and (top[0] - second_score) >= 0.05:
            return top[3], [], requested_name

    if tokens and scored:
        overlap_sorted = sorted(scored, key=lambda x: (x[1], x[0]), reverse=True)
        top_overlap = overlap_sorted[0]
        second_overlap = overlap_sorted[1][1] if len(overlap_sorted) > 1 else -1
        if top_overlap[1] >= 2 and top_overlap[1] > second_overlap:
            return top_overlap[3], [], requested_name

    suggestions: list[dict[str, Any]] = []
    if requested_name and scored:
        for fuzzy, _, _, rep in sorted(scored, key=lambda x: (x[0], x[1]), reverse=True):
            if fuzzy < 0.35:
                continue
            suggestions.append(
                {
                    "rep_id": str(rep.id),
                    "name": rep.name,
                    "match_score": round(float(fuzzy), 2),
                }
            )
            if len(suggestions) >= 3:
                break

    if not suggestions:
        for rep in sorted(reps, key=lambda r: _normalize(r.name or ""))[:3]:
            suggestions.append(
                {
                    "rep_id": str(rep.id),
                    "name": rep.name,
                    "match_score": None,
                }
            )

    return None, suggestions, requested_name


async def get_rep_quota_bonus_what_if(
    db: AsyncSession,
    message: str,
    target_attainment_pct: float | None = None,
) -> dict[str, Any]:
    """Estimate bonus/payout at target attainment and provide concrete next-step actions."""
    warnings: list[str] = []
    target_pct = target_attainment_pct if target_attainment_pct is not None else _extract_target_attainment(message)
    target_pct = max(0.0, min(200.0, float(target_pct)))
    close_rate_lift_pct = _extract_close_rate_lift_pct(message)
    close_rate_target_pct = _extract_close_rate_target_pct(message)
    cycle_days_reduction = _extract_cycle_days_reduction(message)
    pipeline_lift_pct = _extract_pipeline_lift_pct(message)
    pipeline_delta_amount = _extract_pipeline_delta_amount(message)
    deal_size_lift_pct = _extract_deal_size_lift_pct(message)
    fiscal_year = _extract_fiscal_year(message)

    perf_filters: dict[str, Any] | None = None
    if fiscal_year is not None:
        perf_filters = {
            "start_date": f"{fiscal_year}-01-01",
            "end_date": f"{fiscal_year}-12-31",
        }

    rep, candidate_reps, requested_rep_name = await _find_rep_from_message(db, message)
    if rep is None:
        has_driver = any(
            [
                close_rate_lift_pct > 0,
                close_rate_target_pct is not None,
                cycle_days_reduction > 0,
                pipeline_lift_pct > 0,
                pipeline_delta_amount > 0,
                deal_size_lift_pct > 0,
            ]
        )
        team_scenario: dict[str, Any] | None = None
        if has_driver:
            total_revenue_res = await calculators.get_total_revenue(db, filters=perf_filters)
            total_quota_res = await calculators.get_total_quota(db, filters=perf_filters)
            open_pipeline_res = await calculators.get_open_pipeline(db, filters=perf_filters)
            team_win_rate_res = await calculators.get_win_rate(db, filters=perf_filters)

            warnings.extend(total_revenue_res.get("warnings", []))
            warnings.extend(total_quota_res.get("warnings", []))
            warnings.extend(open_pipeline_res.get("warnings", []))
            warnings.extend(team_win_rate_res.get("warnings", []))

            team_revenue = float(total_revenue_res.get("value", 0.0) or 0.0)
            team_quota = float(total_quota_res.get("value", 0.0) or 0.0)
            team_open_pipeline = float(open_pipeline_res.get("value", 0.0) or 0.0)
            team_win_rate = float(team_win_rate_res.get("value", 0.0) or 0.0)

            if close_rate_target_pct is not None:
                projected_team_win_rate = close_rate_target_pct
            elif close_rate_lift_pct > 0:
                projected_team_win_rate = min(100.0, team_win_rate * (1.0 + close_rate_lift_pct / 100.0))
            else:
                projected_team_win_rate = team_win_rate

            if team_win_rate <= 0:
                team_win_rate = 25.0
                if close_rate_target_pct is None and close_rate_lift_pct <= 0:
                    projected_team_win_rate = team_win_rate
                warnings.append("Team win rate unavailable for scenario window; assumed 25% baseline.")

            projected_team_open_pipeline = max(
                0.0,
                team_open_pipeline * (1.0 + (pipeline_lift_pct / 100.0)) + pipeline_delta_amount,
            )
            if deal_size_lift_pct > 0:
                projected_team_open_pipeline *= 1.0 + (deal_size_lift_pct / 100.0)

            throughput_factor = 1.0
            if cycle_days_reduction > 0:
                # Team-level approximation when cycle baselines are unknown.
                throughput_factor = min(1.4, 1.0 + (cycle_days_reduction / 60.0))

            baseline_expected_open_revenue = min(team_open_pipeline, team_open_pipeline * (team_win_rate / 100.0))
            projected_expected_open_revenue = min(
                projected_team_open_pipeline,
                projected_team_open_pipeline * (projected_team_win_rate / 100.0) * throughput_factor,
            )
            incremental_revenue = max(0.0, projected_expected_open_revenue - baseline_expected_open_revenue)

            payout_summary = await get_payout_summary(
                db,
                period_prefix=str(fiscal_year) if fiscal_year is not None else None,
            )
            team_total_payout = float(
                ((payout_summary.get("data") or {}).get("summary") or {}).get("total_payout", 0.0) or 0.0
            )

            effective_commission_rate = (team_total_payout / team_revenue) if team_revenue > 0 else 0.08
            if team_revenue <= 0:
                warnings.append("Team revenue baseline missing; assumed 8% effective commission rate for payout projection.")

            projected_team_revenue_uncapped = team_revenue + incremental_revenue
            if team_quota > 0:
                projected_team_revenue = min(projected_team_revenue_uncapped, team_quota * 2.0)
                if projected_team_revenue < projected_team_revenue_uncapped:
                    warnings.append("Team projection capped at 200% quota to avoid over-extrapolation.")
            else:
                projected_team_revenue = projected_team_revenue_uncapped

            effective_incremental_revenue = max(0.0, projected_team_revenue - team_revenue)
            projected_team_payout = team_total_payout + (effective_incremental_revenue * effective_commission_rate)
            baseline_team_attainment = (team_revenue / team_quota * 100.0) if team_quota > 0 else 0.0
            projected_team_attainment = (projected_team_revenue / team_quota * 100.0) if team_quota > 0 else 0.0

            team_scenario = {
                "period_scope": str(fiscal_year) if fiscal_year is not None else "all available periods",
                "baseline": {
                    "revenue": round(team_revenue, 2),
                    "quota": round(team_quota, 2),
                    "attainment_pct": round(baseline_team_attainment, 2),
                    "open_pipeline": round(team_open_pipeline, 2),
                    "win_rate_pct": round(team_win_rate, 2),
                    "total_payout": round(team_total_payout, 2),
                },
                "projected": {
                    "revenue": round(projected_team_revenue, 2),
                    "attainment_pct": round(projected_team_attainment, 2),
                    "open_pipeline": round(projected_team_open_pipeline, 2),
                    "win_rate_pct": round(projected_team_win_rate, 2),
                    "total_payout": round(projected_team_payout, 2),
                },
                "impact": {
                    "revenue_delta": round(projected_team_revenue - team_revenue, 2),
                    "attainment_delta_pct_points": round(projected_team_attainment - baseline_team_attainment, 2),
                    "open_pipeline_delta": round(projected_team_open_pipeline - team_open_pipeline, 2),
                    "payout_delta": round(projected_team_payout - team_total_payout, 2),
                },
                "assumptions": [
                    "Team-level estimate uses current win-rate and payout efficiency as baseline.",
                    "Cycle-time reduction is modeled as a bounded throughput uplift when explicit team cycle baselines are unavailable.",
                    "Use a specific rep name for personalized bonus and payout projections.",
                ],
            }

        if requested_rep_name:
            warnings.append(
                f"Requested rep '{requested_rep_name.title()}' was not found in the current dataset."
            )
        else:
            warnings.append("Rep name not detected from question. Include a full rep name for a personalized quota/bonus what-if.")

        if candidate_reps:
            candidate_names = ", ".join(str(c.get("name") or "") for c in candidate_reps if c.get("name"))
        else:
            candidate_names = ""

        if requested_rep_name and candidate_names:
            first_action = (
                f"'{requested_rep_name.title()}' is not in this dataset. Try one of these names: {candidate_names}."
            )
        elif requested_rep_name:
            first_action = (
                f"'{requested_rep_name.title()}' is not in this dataset. Ask with one of the available rep names."
            )
        else:
            first_action = "Ask with a full rep name exactly as it appears in the dataset to get a personalized bonus projection."

        return _as_tool_result(
            "get_rep_quota_bonus_what_if",
            "warning",
            {
                "target_attainment_pct": round(target_pct, 2),
                "requested_rep_name": requested_rep_name,
                "scenario_inputs": {
                    "fiscal_year": fiscal_year,
                    "close_rate_lift_pct": round(close_rate_lift_pct, 2),
                    "close_rate_target_pct": round(close_rate_target_pct, 2) if close_rate_target_pct is not None else None,
                    "sales_cycle_days_reduction": round(cycle_days_reduction, 2),
                    "pipeline_lift_pct": round(pipeline_lift_pct, 2),
                    "pipeline_delta_amount": round(pipeline_delta_amount, 2),
                    "deal_size_lift_pct": round(deal_size_lift_pct, 2),
                },
                "matched_rep": None,
                "candidate_reps": candidate_reps,
                "team_scenario": team_scenario,
                "action_plan": [
                    first_action,
                    "Include a target % (for example: 100% or 110%) to simulate accelerator impact.",
                    "For a broad org-level question, specify scope (team/region/company) so the what-if can be constrained.",
                ],
            },
            warnings,
            ["reps", "revenue", "quotas", "deals", "payout_engine"],
        )

    perf = await calculators.get_rep_performance(db, rep_id=str(rep.id), filters=perf_filters)
    perf_data = perf.get("data") or {}
    warnings.extend(perf.get("warnings") or [])

    revenue = float(perf_data.get("revenue") or 0.0)
    quota = float(perf_data.get("quota") or 0.0)
    open_pipeline = float(perf_data.get("open_pipeline") or 0.0)
    deals_won = int(perf_data.get("deals_won") or 0)
    deals_lost = int(perf_data.get("deals_lost") or 0)
    avg_deal_size = float(perf_data.get("average_deal_size") or 0.0)
    win_rate = float(perf_data.get("win_rate") or 0.0)
    current_attainment = float(perf_data.get("attainment_pct") or 0.0)

    open_deals_q = select(
        Deal.created_at,
        Deal.expected_close_date,
        Deal.close_probability,
        Deal.amount,
    ).where(
        Deal.rep_id == rep.id,
        ~Deal.stage.in_(("Closed Won", "Closed Lost")),
    )
    if fiscal_year is not None:
        open_deals_q = open_deals_q.where(
            Deal.expected_close_date.isnot(None),
            Deal.expected_close_date >= date(fiscal_year, 1, 1),
            Deal.expected_close_date <= date(fiscal_year, 12, 31),
        )
    open_rows = (await db.execute(open_deals_q)).all()
    open_deal_count = len(open_rows)
    overdue_open_deals = sum(1 for _, expected_close_date, _, _ in open_rows if expected_close_date and expected_close_date < date.today())
    baseline_slip_risk_pct = (overdue_open_deals / open_deal_count * 100.0) if open_deal_count > 0 else 0.0

    cycle_q = select(Deal.created_at, Deal.actual_close_date).where(
        Deal.rep_id == rep.id,
        Deal.stage == "Closed Won",
        Deal.created_at.isnot(None),
        Deal.actual_close_date.isnot(None),
    )
    if fiscal_year is not None:
        cycle_q = cycle_q.where(
            Deal.actual_close_date >= date(fiscal_year, 1, 1),
            Deal.actual_close_date <= date(fiscal_year, 12, 31),
        )
    cycle_rows = (await db.execute(cycle_q)).all()
    cycle_days_samples: list[float] = []
    for created_at, actual_close_date in cycle_rows:
        created_date = created_at.date() if hasattr(created_at, "date") else created_at
        if created_date is None or actual_close_date is None:
            continue
        days = float((actual_close_date - created_date).days)
        if days >= 0:
            cycle_days_samples.append(days)
    baseline_cycle_days = (sum(cycle_days_samples) / len(cycle_days_samples)) if cycle_days_samples else None

    if win_rate <= 0.0 and open_rows:
        probabilities = [float(prob) for _, _, prob, _ in open_rows if prob is not None]
        if probabilities:
            win_rate = max(5.0, min(95.0, sum(probabilities) / len(probabilities)))
            warnings.append("Win rate inferred from open-deal close probabilities due sparse closed-deal history.")

    if avg_deal_size <= 0.0 and open_pipeline > 0.0 and open_deal_count > 0:
        avg_deal_size = open_pipeline / open_deal_count
        if quota > 0:
            avg_deal_size = min(avg_deal_size, max(quota * 0.75, 25_000.0))
        warnings.append("Average deal size inferred from open pipeline due sparse closed-won history.")

    if baseline_cycle_days is None and open_rows:
        inferred_cycle_days: list[float] = []
        for created_at, expected_close_date, _, _ in open_rows:
            if created_at is None or expected_close_date is None:
                continue
            created_date = created_at.date() if hasattr(created_at, "date") else created_at
            if created_date is None:
                continue
            days = float((expected_close_date - created_date).days)
            if days >= 0:
                inferred_cycle_days.append(days)
        if inferred_cycle_days:
            baseline_cycle_days = sum(inferred_cycle_days) / len(inferred_cycle_days)
            warnings.append("Average sales cycle estimated from open deals due sparse closed-won history.")

    target_revenue = revenue if quota <= 0 else quota * (target_pct / 100.0)
    gap_to_target = max(0.0, target_revenue - revenue)
    effective_open_pipeline, projection_pipeline_cap = _quota_scaled_effective_pipeline(
        open_pipeline=open_pipeline,
        quota=quota,
        gap_to_target=gap_to_target,
        revenue=revenue,
    )
    if effective_open_pipeline < open_pipeline:
        warnings.append(
            f"Projection used quota-scaled effective pipeline ${effective_open_pipeline:,.0f} (from ${open_pipeline:,.0f}) to avoid outlier-driven saturation."
        )

    practical_deal_size = max(avg_deal_size, 1.0)
    deals_needed = int(math.ceil(gap_to_target / practical_deal_size)) if gap_to_target > 0 else 0

    current_payout = compute_payout(revenue, quota, deals_won, deals_lost)
    scenario_deals_won = max(deals_won, DEFAULT_PAYOUT_CONFIG.team_bonus_min_deals)
    projected_payout = compute_payout(target_revenue, max(quota, target_revenue), scenario_deals_won, deals_lost)

    if close_rate_target_pct is not None:
        projected_win_rate = close_rate_target_pct
    elif close_rate_lift_pct > 0:
        projected_win_rate = min(100.0, win_rate * (1.0 + close_rate_lift_pct / 100.0))
    else:
        projected_win_rate = win_rate

    projected_open_pipeline = max(
        0.0,
        effective_open_pipeline * (1.0 + (pipeline_lift_pct / 100.0)) + pipeline_delta_amount,
    )
    if deal_size_lift_pct > 0:
        projected_open_pipeline *= 1.0 + (deal_size_lift_pct / 100.0)

    projected_pipeline_cap = projection_pipeline_cap * 2.0
    if projected_open_pipeline > projected_pipeline_cap:
        projected_open_pipeline = projected_pipeline_cap
        warnings.append("Projected open pipeline capped to avoid outlier-driven saturation in what-if simulation.")

    projected_cycle_days = baseline_cycle_days
    throughput_factor = 1.0
    if cycle_days_reduction > 0:
        if baseline_cycle_days and baseline_cycle_days > 0:
            projected_cycle_days = max(1.0, baseline_cycle_days - cycle_days_reduction)
            throughput_factor = min(1.6, baseline_cycle_days / projected_cycle_days)
        else:
            warnings.append("Could not estimate baseline sales cycle days for this rep; cycle-time what-if impact is approximate.")

    baseline_expected_open_revenue = min(effective_open_pipeline, effective_open_pipeline * (win_rate / 100.0))
    projected_expected_open_revenue = min(
        projected_open_pipeline,
        projected_open_pipeline * (projected_win_rate / 100.0) * throughput_factor,
    )
    incremental_revenue = max(0.0, projected_expected_open_revenue - baseline_expected_open_revenue)
    projected_revenue_with_drivers_uncapped = revenue + incremental_revenue
    if quota > 0:
        max_projected_revenue = quota * 2.0
        projected_revenue_with_drivers = min(projected_revenue_with_drivers_uncapped, max_projected_revenue)
        if projected_revenue_with_drivers < projected_revenue_with_drivers_uncapped:
            warnings.append("Driver projection capped at 200% quota to avoid over-extrapolation.")
    else:
        projected_revenue_with_drivers = projected_revenue_with_drivers_uncapped
    projected_attainment_with_drivers = (
        (projected_revenue_with_drivers / quota) * 100.0 if quota > 0 else 0.0
    )

    baseline_expected_open_wins = open_deal_count * (win_rate / 100.0)
    projected_deal_count = (
        open_deal_count * max(projected_open_pipeline, 1.0) / max(effective_open_pipeline, 1.0)
        if open_deal_count > 0
        else 0.0
    )
    projected_expected_open_wins = projected_deal_count * (projected_win_rate / 100.0) * throughput_factor
    projected_deals_won_with_drivers = int(math.ceil(deals_won + max(0.0, projected_expected_open_wins - baseline_expected_open_wins)))

    projected_driver_payout = compute_payout(
        projected_revenue_with_drivers,
        max(quota, projected_revenue_with_drivers) if quota <= 0 else quota,
        projected_deals_won_with_drivers,
        deals_lost,
    )

    win_rate_factor = (win_rate / projected_win_rate) if projected_win_rate > 0 and projected_win_rate >= win_rate and win_rate > 0 else 1.0
    cycle_factor = (
        (projected_cycle_days / baseline_cycle_days)
        if baseline_cycle_days and projected_cycle_days and projected_cycle_days <= baseline_cycle_days
        else 1.0
    )
    projected_slip_risk_pct = max(0.0, baseline_slip_risk_pct * win_rate_factor * cycle_factor)

    bonus_win_rate_req = DEFAULT_PAYOUT_CONFIG.team_bonus_min_win_rate_pct
    bonus_deals_req = DEFAULT_PAYOUT_CONFIG.team_bonus_min_deals
    bonus_attainment_req = DEFAULT_PAYOUT_CONFIG.team_bonus_threshold_pct

    action_plan: list[str] = []
    if gap_to_target <= 0:
        action_plan.append("Rep is already at or above the requested quota target; focus on accelerator and quality-of-revenue mix.")
    else:
        if effective_open_pipeline <= 0:
            action_plan.append("Build immediate qualified pipeline; there is currently no open pipeline coverage against the remaining quota gap.")
        elif effective_open_pipeline < gap_to_target:
            missing_pipeline = gap_to_target - effective_open_pipeline
            action_plan.append(
                f"Current open pipeline is short by ${missing_pipeline:,.0f} versus the quota gap; increase qualified pipeline generation this period."
            )
        else:
            required_conversion = (gap_to_target / effective_open_pipeline) * 100.0
            action_plan.append(
                f"To close the remaining gap, convert about {required_conversion:.1f}% of current open pipeline (assuming no new pipeline inflow)."
            )

        if deals_needed > 0:
            action_plan.append(f"Estimated additional closed-won deals needed: {deals_needed} (using current average deal size).")

    if deals_won < bonus_deals_req:
        action_plan.append(f"Bonus gate not yet met: needs at least {bonus_deals_req} closed-won deals (currently {deals_won}).")
    if win_rate < bonus_win_rate_req:
        action_plan.append(f"Bonus gate not yet met: raise win rate to at least {bonus_win_rate_req:.0f}% (currently {win_rate:.1f}%).")

    if close_rate_target_pct is not None:
        action_plan.append(
            f"Scenario assumes win-rate target at {close_rate_target_pct:.1f}% (current baseline {win_rate:.1f}%)."
        )
    elif close_rate_lift_pct > 0:
        action_plan.append(
            f"Scenario assumes close-rate lift of {close_rate_lift_pct:.1f}% (win rate {win_rate:.1f}% -> {projected_win_rate:.1f}%)."
        )
    if cycle_days_reduction > 0 and baseline_cycle_days:
        action_plan.append(
            f"Scenario assumes sales-cycle reduction of {cycle_days_reduction:.1f} days ({baseline_cycle_days:.1f} -> {projected_cycle_days:.1f} days)."
        )
    if pipeline_lift_pct > 0 or pipeline_delta_amount > 0:
        action_plan.append(
            f"Scenario assumes open pipeline increases to ${projected_open_pipeline:,.0f} from ${open_pipeline:,.0f}."
        )
    if deal_size_lift_pct > 0:
        action_plan.append(
            f"Scenario assumes average deal size grows by {deal_size_lift_pct:.1f}%."
        )

    if quota <= 0:
        warnings.append("Quota is missing or zero; what-if payout and bonus estimates are low-confidence.")

    if (
        close_rate_lift_pct <= 0
        and close_rate_target_pct is None
        and cycle_days_reduction <= 0
        and pipeline_lift_pct <= 0
        and pipeline_delta_amount <= 0
        and deal_size_lift_pct <= 0
    ):
        warnings.append(
            "No recognized what-if drivers were detected (close/win rate, sales cycle, pipeline, deal size); projection defaults to baseline."
        )

    data = {
        "target_attainment_pct": round(target_pct, 2),
        "scenario_inputs": {
            "fiscal_year": fiscal_year,
            "close_rate_lift_pct": round(close_rate_lift_pct, 2),
            "close_rate_target_pct": round(close_rate_target_pct, 2) if close_rate_target_pct is not None else None,
            "sales_cycle_days_reduction": round(cycle_days_reduction, 2),
            "pipeline_lift_pct": round(pipeline_lift_pct, 2),
            "pipeline_delta_amount": round(pipeline_delta_amount, 2),
            "deal_size_lift_pct": round(deal_size_lift_pct, 2),
        },
        "matched_rep": {
            "rep_id": str(rep.id),
            "name": rep.name,
            "region": rep.region,
        },
        "current_state": {
            "revenue": round(revenue, 2),
            "quota": round(quota, 2),
            "attainment_pct": round(current_attainment, 2),
            "open_pipeline": round(open_pipeline, 2),
            "projection_open_pipeline_effective": round(effective_open_pipeline, 2),
            "deals_won": deals_won,
            "deals_lost": deals_lost,
            "win_rate": round(win_rate, 2),
            "average_deal_size": round(avg_deal_size, 2),
            "current_payout": round(float(current_payout.get("payout") or 0.0), 2),
            "current_bonus": round(float(current_payout.get("bonus") or 0.0), 2),
            "open_deal_count": open_deal_count,
            "overdue_open_deals": overdue_open_deals,
            "slip_risk_pct": round(baseline_slip_risk_pct, 2),
            "avg_sales_cycle_days": round(float(baseline_cycle_days), 2) if baseline_cycle_days is not None else None,
        },
        "quota_target_scenario": {
            "target_revenue": round(target_revenue, 2),
            "gap_to_target": round(gap_to_target, 2),
            "estimated_deals_needed": deals_needed,
            "projected_payout_if_target_hit": round(float(projected_payout.get("payout") or 0.0), 2),
            "projected_bonus_if_target_hit": round(float(projected_payout.get("bonus") or 0.0), 2),
            "projected_accelerator_if_target_hit": round(float(projected_payout.get("accelerator") or 0.0), 2),
            "bonus_requirements": {
                "attainment_pct_min": bonus_attainment_req,
                "win_rate_pct_min": bonus_win_rate_req,
                "deals_won_min": bonus_deals_req,
            },
        },
        "driver_scenario": {
            "period_scope": str(fiscal_year) if fiscal_year is not None else "all available periods",
            "baseline": {
                "attainment_pct": round(current_attainment, 2),
                "payout": round(float(current_payout.get("payout") or 0.0), 2),
                "bonus": round(float(current_payout.get("bonus") or 0.0), 2),
                "slip_risk_pct": round(baseline_slip_risk_pct, 2),
                "win_rate_pct": round(win_rate, 2),
                "open_pipeline": round(effective_open_pipeline, 2),
                "avg_sales_cycle_days": round(float(baseline_cycle_days), 2) if baseline_cycle_days is not None else None,
            },
            "projected": {
                "attainment_pct": round(projected_attainment_with_drivers, 2),
                "payout": round(float(projected_driver_payout.get("payout") or 0.0), 2),
                "bonus": round(float(projected_driver_payout.get("bonus") or 0.0), 2),
                "slip_risk_pct": round(projected_slip_risk_pct, 2),
                "win_rate_pct": round(projected_win_rate, 2),
                "open_pipeline": round(projected_open_pipeline, 2),
                "avg_sales_cycle_days": round(float(projected_cycle_days), 2) if projected_cycle_days is not None else None,
                "projected_revenue": round(projected_revenue_with_drivers, 2),
            },
            "impact": {
                "attainment_delta_pct_points": round(projected_attainment_with_drivers - current_attainment, 2),
                "payout_delta": round(
                    float(projected_driver_payout.get("payout") or 0.0) - float(current_payout.get("payout") or 0.0),
                    2,
                ),
                "bonus_delta": round(
                    float(projected_driver_payout.get("bonus") or 0.0) - float(current_payout.get("bonus") or 0.0),
                    2,
                ),
                "slip_risk_delta_pct_points": round(projected_slip_risk_pct - baseline_slip_risk_pct, 2),
                "slip_risk_band_before": _risk_band_from_pct(baseline_slip_risk_pct),
                "slip_risk_band_after": _risk_band_from_pct(projected_slip_risk_pct),
                "open_pipeline_delta": round(projected_open_pipeline - open_pipeline, 2),
                "incremental_revenue_from_drivers": round(incremental_revenue, 2),
            },
            "assumptions": [
                "Projected revenue impact is estimated from open-pipeline conversion changes.",
                "Sales-cycle reduction is converted into a bounded throughput factor (max 1.6x).",
                "Slip-risk projection is a directional estimate based on win-rate and cycle-time improvements.",
            ],
        },
        "action_plan": action_plan,
    }

    return _as_tool_result(
        "get_rep_quota_bonus_what_if",
        "warning" if warnings else "success",
        data,
        warnings,
        ["reps", "revenue", "quotas", "deals", "payout_engine"],
    )


async def get_payout_summary(db: AsyncSession, period_prefix: str | None = None) -> dict[str, Any]:
    """Compute payout for all reps and return a summary with explainability fields."""
    try:
        reps = (await db.execute(select(Rep))).scalars().all()
        rows: list[dict[str, Any]] = []
        total_payout = 0.0
        total_revenue = 0.0
        total_quota = 0.0
        fallback_count = 0
        low_confidence_count = 0

        for rep in reps:
            revenue_q = select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id)
            quota_q = select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id)
            if period_prefix:
                revenue_q = revenue_q.where(Revenue.period.like(f"{period_prefix}%"))
                quota_q = quota_q.where(Quota.period.like(f"{period_prefix}%"))

            rep_revenue = float((await db.execute(revenue_q)).scalar() or 0.0)
            rep_quota = float((await db.execute(quota_q)).scalar() or 0.0)

            won_q = select(func.count()).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won")
            lost_q = select(func.count()).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost")
            deals_won = int((await db.execute(won_q)).scalar() or 0)
            deals_lost = int((await db.execute(lost_q)).scalar() or 0)

            result = compute_payout(rep_revenue, rep_quota, deals_won, deals_lost)
            total_payout += result["payout"]
            total_revenue += rep_revenue
            total_quota += rep_quota

            if result["fallback_used"]:
                fallback_count += 1
            if result["confidence"] < 0.6:
                low_confidence_count += 1

            rows.append({
                "rep_id": str(rep.id),
                "name": rep.name,
                "revenue": round(rep_revenue, 2),
                "quota": round(rep_quota, 2),
                "payout": result["payout"],
                "attainment_pct": result["attainment_pct"],
                "commission_rate": result["commission_rate"],
                "accelerator": result["accelerator"],
                "bonus": result["bonus"],
                "win_rate": result["win_rate"],
                "confidence": result["confidence"],
                "fallback_used": result["fallback_used"],
                "rules_applied": result["rules_applied"],
            })

        rows.sort(key=lambda r: r["payout"], reverse=True)

        overall_attainment = (100.0 * total_revenue / total_quota) if total_quota > 0 else 0.0
        return {
            "tool_name": "get_payout_summary",
            "status": "success",
            "data": {
                "period_prefix": period_prefix,
                "summary": {
                    "total_revenue": round(total_revenue, 2),
                    "total_quota": round(total_quota, 2),
                    "total_payout": round(total_payout, 2),
                    "overall_attainment_pct": round(overall_attainment, 2),
                    "rep_count": len(rows),
                    "fallback_count": fallback_count,
                    "low_confidence_count": low_confidence_count,
                },
                "rows": rows,
            },
            "warnings": (
                [f"{fallback_count} rep(s) used fallback payout calculation (missing quota/revenue data)."]
                if fallback_count else []
            ),
            "sources": ["payout_engine", "revenue_table", "quota_table", "deals_table"],
        }
    except Exception as exc:
        return {
            "tool_name": "get_payout_summary",
            "status": "error",
            "data": {},
            "warnings": [f"Payout computation failed: {exc}", traceback.format_exc()],
            "sources": [],
        }
