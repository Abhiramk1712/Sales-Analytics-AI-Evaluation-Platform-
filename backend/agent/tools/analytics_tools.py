"""
DB-backed analytics tools for the AI agent.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.metrics.service import get_metrics_service
from backend.metrics import calculators
from backend.models import Team, Rep


def _as_tool_result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


async def get_sales_kpis(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    service = get_metrics_service()
    data = await service.get_kpis(db, filters=filters)
    status = "warning" if data["warnings"] else "success"
    return _as_tool_result("get_sales_kpis", status, data, data["warnings"], data.get("sources", ["metrics"]))


async def get_top_reps(db: AsyncSession, limit: int = 5, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    result = await calculators.get_top_reps(db, limit=limit, filters=filters)
    status = "warning" if result["warnings"] else "success"
    return _as_tool_result("get_top_reps", status, result["data"], result["warnings"], result["sources"])


async def get_underperforming_reps(
    db: AsyncSession,
    threshold_pct: int = 75,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    result = await calculators.get_underperforming_reps(db, threshold_pct=threshold_pct, filters=filters)
    status = "warning" if result["warnings"] else "success"
    return _as_tool_result("get_underperforming_reps", status, result["data"], result["warnings"], result["sources"])


async def get_revenue_by_region(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    result = await calculators.get_revenue_by_region(db, filters=filters)
    status = "warning" if result["warnings"] else "success"
    return _as_tool_result("get_revenue_by_region", status, result["data"], result["warnings"], result["sources"])


async def get_pipeline_summary(db: AsyncSession, filters: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    open_pipeline = await calculators.get_open_pipeline(db, filters=filters)
    coverage = await calculators.get_pipeline_coverage(db, filters=filters)
    status = "warning" if open_pipeline["warnings"] or coverage["warnings"] else "success"
    return _as_tool_result(
        "get_pipeline_summary",
        status,
        {
            "open_pipeline": open_pipeline["value"],
            "pipeline_coverage": coverage["value"],
        },
        open_pipeline["warnings"] + coverage["warnings"],
        ["deals", "quotas"],
    )


async def get_rep_performance_summary(
    db: AsyncSession,
    rep_id: Optional[str] = None,
    rep_name: Optional[str] = None,
    filters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    result = await calculators.get_rep_performance(db, rep_id=rep_id, rep_name=rep_name, filters=filters)
    status = "error" if result["data"] is None else ("warning" if result["warnings"] else "success")
    return _as_tool_result("get_rep_performance_summary", status, result["data"], result["warnings"], result["sources"])


def _criteria_text(
    *,
    min_pipeline_coverage: float | None,
    max_pipeline_coverage: float | None,
    max_attainment_pct: float | None,
    min_attainment_pct: float | None,
    attainment_logic: str,
    sort_by: str,
    limit: int,
) -> str:
    parts: list[str] = []

    if min_pipeline_coverage is not None and max_pipeline_coverage is not None:
        parts.append(f"coverage between {min_pipeline_coverage:.1f}x and {max_pipeline_coverage:.1f}x")
    elif min_pipeline_coverage is not None and min_pipeline_coverage > 0:
        parts.append(f"coverage >= {min_pipeline_coverage:.1f}x")
    elif max_pipeline_coverage is not None:
        parts.append(f"coverage <= {max_pipeline_coverage:.1f}x")

    if max_attainment_pct is not None and min_attainment_pct is not None:
        connector = "or" if attainment_logic == "or" else "and"
        parts.append(f"attainment <= {max_attainment_pct:.0f}% {connector} attainment >= {min_attainment_pct:.0f}%")
    elif max_attainment_pct is not None:
        parts.append(f"attainment <= {max_attainment_pct:.0f}%")
    elif min_attainment_pct is not None:
        parts.append(f"attainment >= {min_attainment_pct:.0f}%")

    if sort_by == "coverage_desc":
        parts.append(f"ranked by highest coverage (top {limit})")

    return ", ".join(parts) if parts else "no explicit filters"


async def get_team_pipeline_coverage_attainment(
    db: AsyncSession,
    min_pipeline_coverage: float | None = 4.0,
    max_attainment_pct: float | None = 80.0,
    max_pipeline_coverage: float | None = None,
    min_attainment_pct: float | None = None,
    attainment_logic: str = "and",
    sort_by: str = "match_priority",
    limit: int = 10,
) -> dict[str, Any]:
    """Return teams with high pipeline coverage but low quota attainment."""
    max_rows = max(1, min(25, int(limit or 10)))
    att_logic = "or" if str(attainment_logic).lower() == "or" else "and"
    sort_pref = "coverage_desc" if str(sort_by).lower() == "coverage_desc" else "match_priority"

    min_cov = None if min_pipeline_coverage is None else max(0.0, min(25.0, float(min_pipeline_coverage)))
    max_cov = None if max_pipeline_coverage is None else max(0.0, min(25.0, float(max_pipeline_coverage)))
    max_att = None if max_attainment_pct is None else max(0.0, min(200.0, float(max_attainment_pct)))
    min_att = None if min_attainment_pct is None else max(0.0, min(200.0, float(min_attainment_pct)))

    if min_cov is not None and max_cov is not None and min_cov > max_cov:
        min_cov, max_cov = max_cov, min_cov

    if min_att is not None and max_att is not None and att_logic == "and" and min_att > max_att:
        min_att, max_att = max_att, min_att

    criteria_text = _criteria_text(
        min_pipeline_coverage=min_cov,
        max_pipeline_coverage=max_cov,
        max_attainment_pct=max_att,
        min_attainment_pct=min_att,
        attainment_logic=att_logic,
        sort_by=sort_pref,
        limit=max_rows,
    )

    teams = (await db.execute(select(Team))).scalars().all()
    warnings: list[str] = []

    if not teams:
        return _as_tool_result(
            "get_team_pipeline_coverage_attainment",
            "warning",
            {
                "criteria": {
                    "min_pipeline_coverage": min_cov,
                    "max_pipeline_coverage": max_cov,
                    "max_attainment_pct": max_att,
                    "min_attainment_pct": min_att,
                    "attainment_logic": att_logic,
                    "sort_by": sort_pref,
                    "limit": max_rows,
                },
                "criteria_text": criteria_text,
                "teams_evaluated": 0,
                "match_count": 0,
                "matches": [],
                "rows": [],
            },
            ["No teams found in database."],
            ["teams", "reps", "revenue", "quotas", "deals"],
        )

    rows: list[dict[str, Any]] = []
    for team in teams:
        rep_count = int(
            (await db.execute(select(func.count(Rep.id)).where(Rep.team_id == team.id))).scalar()
            or 0
        )
        scoped_filters = {"team_id": team.id}

        revenue_res = await calculators.get_total_revenue(db, filters=scoped_filters)
        quota_res = await calculators.get_total_quota(db, filters=scoped_filters)
        pipeline_res = await calculators.get_open_pipeline(db, filters=scoped_filters)

        quota_value = float(quota_res.get("value", 0.0) or 0.0)
        revenue_value = float(revenue_res.get("value", 0.0) or 0.0)
        pipeline_value = float(pipeline_res.get("value", 0.0) or 0.0)

        if quota_value <= 0:
            warnings.append(f"Team '{team.name}' has zero or missing quota; skipping attainment comparison.")
            continue

        attainment_pct = (revenue_value / quota_value) * 100.0
        pipeline_coverage = pipeline_value / quota_value

        coverage_match = True
        if min_cov is not None:
            coverage_match = coverage_match and pipeline_coverage >= min_cov
        if max_cov is not None:
            coverage_match = coverage_match and pipeline_coverage <= max_cov

        if max_att is not None and min_att is not None:
            if att_logic == "or":
                attainment_match = attainment_pct <= max_att or attainment_pct >= min_att
            else:
                attainment_match = attainment_pct <= max_att and attainment_pct >= min_att
        elif max_att is not None:
            attainment_match = attainment_pct <= max_att
        elif min_att is not None:
            attainment_match = attainment_pct >= min_att
        else:
            attainment_match = True

        is_match = coverage_match and attainment_match

        rows.append(
            {
                "team_id": str(team.id),
                "team_name": team.name,
                "region": team.region,
                "rep_count": rep_count,
                "revenue": round(revenue_value, 2),
                "quota": round(quota_value, 2),
                "attainment_pct": round(attainment_pct, 2),
                "open_pipeline": round(pipeline_value, 2),
                "pipeline_coverage": round(pipeline_coverage, 2),
                "high_pipeline_low_attainment": is_match,
                "coverage_match": coverage_match,
                "attainment_match": attainment_match,
            }
        )

    if sort_pref == "coverage_desc":
        rows.sort(
            key=lambda r: (
                -float(r.get("pipeline_coverage", 0.0) or 0.0),
                float(r.get("attainment_pct", 0.0) or 0.0),
            )
        )
    else:
        rows.sort(
            key=lambda r: (
                not bool(r.get("high_pipeline_low_attainment")),
                -float(r.get("pipeline_coverage", 0.0) or 0.0),
                float(r.get("attainment_pct", 0.0) or 0.0),
            )
        )

    matches = [r for r in rows if bool(r.get("high_pipeline_low_attainment"))]
    if not matches:
        warnings.append(f"No teams met criteria: {criteria_text}.")

    data = {
        "criteria": {
            "min_pipeline_coverage": min_cov,
            "max_pipeline_coverage": max_cov,
            "max_attainment_pct": max_att,
            "min_attainment_pct": min_att,
            "attainment_logic": att_logic,
            "sort_by": sort_pref,
            "limit": max_rows,
        },
        "criteria_text": criteria_text,
        "teams_evaluated": len(rows),
        "match_count": len(matches),
        "matches": matches[:max_rows],
        "rows": rows[: max(3, max_rows)],
    }

    return _as_tool_result(
        "get_team_pipeline_coverage_attainment",
        "warning" if warnings else "success",
        data,
        sorted(set(warnings)),
        ["teams", "reps", "revenue", "quotas", "deals"],
    )
