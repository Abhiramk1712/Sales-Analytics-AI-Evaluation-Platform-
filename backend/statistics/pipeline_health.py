from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def pipeline_coverage_status(open_pipeline: float, quota: float) -> dict:
    if quota <= 0:
        return {"coverage": 0.0, "status": "unknown", "warnings": ["Quota is zero or missing"]}

    coverage = open_pipeline / (quota / 4)
    if coverage >= 3:
        status = "strong"
    elif coverage >= 2:
        status = "healthy"
    elif coverage >= 1:
        status = "watch"
    else:
        status = "risk"
    return {"coverage": round(coverage, 3), "status": status, "warnings": []}


def stale_pipeline_detection(deals_df: pd.DataFrame, days_threshold: int = 30) -> dict:
    if deals_df.empty or "updated_at" not in deals_df.columns:
        return {"stale_deals": [], "warnings": ["No deal update timestamps available"]}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    stale = []
    for _, row in deals_df.iterrows():
        updated_at = row.get("updated_at")
        if updated_at is None:
            continue
        age = (now - pd.to_datetime(updated_at).to_pydatetime()).days
        if age >= days_threshold:
            stale.append({"deal_id": str(row.get("id", "unknown")), "days_stale": age})
    return {"stale_deals": stale, "warnings": []}


def stage_slippage_summary(deals_df: pd.DataFrame) -> dict:
    if deals_df.empty or "stage" not in deals_df.columns:
        return {"summary": [], "warnings": ["Missing stage data"]}

    summary = (
        deals_df.groupby("stage", dropna=False)
        .size()
        .reset_index(name="count")
        .sort_values("count", ascending=False)
        .to_dict(orient="records")
    )
    return {"summary": summary, "warnings": []}
