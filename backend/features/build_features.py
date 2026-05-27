from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import Rep, Revenue, Quota, Deal, Activity, Account
from backend.metrics import calculators


async def build_rep_month_features(db: AsyncSession, period: Optional[str] = None) -> dict[str, Any]:
    warnings: list[str] = []
    reps = (await db.execute(select(Rep))).scalars().all()
    rows = []

    for rep in reps:
        filters = {"rep_id": rep.id}
        if period:
            filters["start_date"] = f"{period}-01"
            filters["end_date"] = f"{period}-31"

        revenue = await calculators.get_total_revenue(db, filters)
        quota = await calculators.get_total_quota(db, filters)
        pipeline = await calculators.get_open_pipeline(db, filters)
        coverage = await calculators.get_pipeline_coverage(db, filters)
        win_rate = await calculators.get_win_rate(db, filters)
        avg_deal = await calculators.get_average_deal_size(db, filters)

        cycle = (
            await db.execute(
                select(func.avg(func.extract("day", Deal.actual_close_date - Deal.created_at))).where(
                    Deal.rep_id == rep.id,
                    Deal.actual_close_date.isnot(None),
                )
            )
        ).scalar()
        if cycle is None:
            warnings.append(f"avg_sales_cycle unavailable for rep {rep.name}")

        activities = (
            await db.execute(
                select(func.count(Activity.id)).join(Deal, Activity.deal_id == Deal.id).where(Deal.rep_id == rep.id)
            )
        ).scalar() or 0

        attainment = (revenue["value"] / quota["value"] * 100) if quota["value"] else None
        if attainment is None:
            warnings.append(f"quota missing for rep {rep.name}")

        rows.append(
            {
                "rep_id": str(rep.id),
                "rep_name": rep.name,
                "period": period or "all_time",
                "revenue": revenue["value"],
                "quota": quota["value"],
                "attainment_pct": attainment,
                "open_pipeline": pipeline["value"],
                "pipeline_coverage": coverage["value"],
                "win_rate": win_rate["value"],
                "avg_deal_size": avg_deal["value"],
                "avg_sales_cycle": float(cycle) if cycle is not None else None,
                "activity_count": int(activities),
                "risk_score": None,
            }
        )

    return {"data": pd.DataFrame(rows), "warnings": warnings}


async def build_deal_features(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Deal.id, Deal.rep_id, Deal.stage, Deal.product, Deal.amount, Deal.created_at, Deal.updated_at)
        )
    ).all()
    df = pd.DataFrame([dict(r._mapping) for r in rows])
    return {"data": df, "warnings": ["No deals available"] if df.empty else []}


async def build_account_features(db: AsyncSession) -> dict[str, Any]:
    rows = (await db.execute(select(Account.id, Account.name, Account.industry, Account.employee_count, Account.annual_revenue))).all()
    df = pd.DataFrame([dict(r._mapping) for r in rows])
    return {"data": df, "warnings": ["No accounts available"] if df.empty else []}


async def build_pipeline_snapshot_features(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(Deal.stage, func.count(Deal.id).label("deal_count"), func.sum(Deal.amount).label("pipeline_value"))
            .where(~Deal.stage.in_(["Closed Won", "Closed Lost"]))
            .group_by(Deal.stage)
        )
    ).all()
    df = pd.DataFrame([dict(r._mapping) for r in rows])
    return {"data": df, "warnings": ["No open pipeline data"] if df.empty else []}
