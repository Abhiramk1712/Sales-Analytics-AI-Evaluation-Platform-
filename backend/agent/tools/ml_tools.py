"""
ML summary tools for agent evidence collection.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.ml.evaluation import rolling_origin_backtest
from backend.models import MLPrediction, Revenue


def _result(tool_name: str, status: str, data: Any, warnings: list[str], sources: list[str]) -> dict[str, Any]:
    return {
        "tool_name": tool_name,
        "status": status,
        "data": data,
        "warnings": warnings,
        "sources": sources,
    }


def _parse_period_to_year_month(period_label: str) -> tuple[int, int] | None:
    label = (period_label or "").strip()
    month_match = re.match(r"^(\d{4})-(\d{2})(?:-\d{2})?$", label)
    if month_match:
        year = int(month_match.group(1))
        month = int(month_match.group(2))
        if 1 <= month <= 12:
            return year, month

    quarter_match = re.match(r"^(\d{4})-Q([1-4])$", label, re.IGNORECASE)
    if quarter_match:
        year = int(quarter_match.group(1))
        quarter = int(quarter_match.group(2))
        # Map quarter labels to first month for chronological sorting.
        return year, (quarter - 1) * 3 + 1

    return None


def _extract_requested_quarter(message: str) -> tuple[int | None, int | None]:
    text = (message or "").lower()
    quarter_match = re.search(r"(?:\bq\s*([1-4])\b|\bquarter\s*([1-4])\b)", text)
    if not quarter_match:
        return None, None

    quarter = int(quarter_match.group(1) or quarter_match.group(2))
    year_match = re.search(r"\b(?:fy\s*)?(20\d{2})\b", text)
    year = int(year_match.group(1)) if year_match else None
    return year, quarter


def _resolve_default_quarter_year(monthly_rows: list[dict[str, Any]], quarter: int) -> int | None:
    if quarter < 1 or quarter > 4:
        return None

    start_month = (quarter - 1) * 3 + 1
    quarter_months = {start_month, start_month + 1, start_month + 2}

    by_year: dict[int, set[int]] = {}
    for row in monthly_rows:
        year = int(row.get("year") or 0)
        month = int(row.get("month") or 0)
        if month in quarter_months:
            by_year.setdefault(year, set()).add(month)

    if not by_year:
        return None

    fully_observed = [year for year, months in by_year.items() if quarter_months.issubset(months)]
    if fully_observed:
        return max(fully_observed)

    partially_observed = [year for year, months in by_year.items() if months]
    if partially_observed:
        return max(partially_observed)

    return None


def _compute_naive_quarter_accuracy(
    monthly_rows: list[dict[str, Any]],
    year: int,
    quarter: int,
) -> dict[str, Any]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    period_label = f"{year}-Q{quarter}"

    points: list[dict[str, Any]] = []
    for idx in range(1, len(monthly_rows)):
        row = monthly_rows[idx]
        month = int(row.get("month") or 0)
        if int(row.get("year") or 0) != year or month < start_month or month > end_month:
            continue
        actual = float(row.get("value") or 0.0)
        predicted = float(monthly_rows[idx - 1].get("value") or 0.0)
        points.append({
            "period": str(row.get("period") or ""),
            "actual": actual,
            "predicted": predicted,
            "error": predicted - actual,
        })

    if not points:
        return {
            "status": "no_points",
            "period": period_label,
            "points": 0,
            "warnings": ["No monthly observations were available for the requested quarter."],
        }

    errors = [float(p["error"]) for p in points]
    actuals = [float(p["actual"]) for p in points]
    n = len(errors)
    mae = sum(abs(e) for e in errors) / n
    rmse = (sum((e * e) for e in errors) / n) ** 0.5
    mape = (sum(abs(e) / (abs(a) + 1e-9) for e, a in zip(errors, actuals)) / n) * 100.0
    bias = sum(errors) / n

    return {
        "status": "ok",
        "period": period_label,
        "points": n,
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "bias": round(bias, 4),
        "warnings": [],
    }


async def get_forecast_summary(db: AsyncSession, message: str | None = None) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(MLPrediction)
            .where(MLPrediction.model_name.ilike("%forecast%"))
            .order_by(MLPrediction.predicted_at.desc())
            .limit(10)
        )
    ).scalars().all()
    warnings: list[str] = []
    sources = ["ml_predictions"]

    latest = rows[0] if rows else None
    if latest:
        confidence = latest.prediction.get("confidence") if isinstance(latest.prediction, dict) else None
        if confidence in (None, "low"):
            warnings.append("Forecast confidence is low or unavailable")
    else:
        warnings.append("Forecast metadata not found. This may be expected before first training/prediction run.")

    revenue_rows = (
        await db.execute(
            select(Revenue.period, func.sum(Revenue.amount).label("total"))
            .group_by(Revenue.period)
            .order_by(Revenue.period)
        )
    ).all()

    monthly_rows: list[dict[str, Any]] = []
    unparsed_periods = 0
    for row in revenue_rows:
        period_raw = str(row.period or "")
        parsed = _parse_period_to_year_month(period_raw)
        if not parsed:
            unparsed_periods += 1
            continue
        year, month = parsed
        monthly_rows.append(
            {
                "period": period_raw,
                "year": year,
                "month": month,
                "value": float(row.total or 0.0),
            }
        )

    monthly_rows.sort(key=lambda r: (int(r["year"]), int(r["month"]), str(r["period"])))
    if monthly_rows:
        sources.append("revenue")

    if unparsed_periods:
        warnings.append(
            f"Skipped {unparsed_periods} revenue periods with unsupported format while scoring forecast accuracy."
        )

    accuracy_backtest: dict[str, Any] = {
        "status": "insufficient_history",
        "warnings": ["Insufficient history for rolling-origin backtest"],
    }
    if monthly_rows:
        import pandas as pd

        series = pd.Series(
            [float(r["value"]) for r in monthly_rows],
            index=[str(r["period"]) for r in monthly_rows],
        )
        accuracy_backtest = rolling_origin_backtest(series)
        if accuracy_backtest.get("status") != "ok":
            warnings.append("Forecast accuracy backtest requires at least 21 monthly observations.")
    else:
        warnings.append("No revenue history found for forecast backtest accuracy.")

    requested_year, requested_quarter = _extract_requested_quarter(message or "")
    quarter_accuracy: dict[str, Any] | None = None
    if requested_quarter is not None:
        inferred_quarter_year: int | None = None
        if requested_year is None and monthly_rows:
            inferred_quarter_year = _resolve_default_quarter_year(monthly_rows, requested_quarter)
            if inferred_quarter_year is not None:
                requested_year = inferred_quarter_year
            else:
                requested_year = max(int(r["year"]) for r in monthly_rows)

        if requested_year is None:
            quarter_accuracy = {
                "status": "no_points",
                "period": f"Q{requested_quarter}",
                "points": 0,
                "warnings": ["No revenue history available to evaluate requested quarter."],
            }
            warnings.append("Requested quarter forecast accuracy could not be computed because no revenue history was found.")
        else:
            quarter_accuracy = _compute_naive_quarter_accuracy(monthly_rows, requested_year, requested_quarter)
            if inferred_quarter_year is not None:
                quarter_accuracy["selection_basis"] = f"inferred latest fully observed Q{requested_quarter} year"
            if quarter_accuracy.get("status") != "ok":
                warnings.append(
                    f"Requested quarter {quarter_accuracy.get('period')} has insufficient monthly history for accuracy scoring."
                )

    payload: dict[str, Any] = {
        "history_months": len(monthly_rows),
        "accuracy_backtest": accuracy_backtest,
    }
    if quarter_accuracy is not None:
        payload["quarter_accuracy"] = quarter_accuracy

    if latest:
        payload.update(
            {
                "latest_model_version": latest.model_version,
                "predicted_at": latest.predicted_at.isoformat(),
                "prediction": latest.prediction,
            }
        )
    else:
        payload["message"] = "No persisted forecast runs were found"

    status = "warning" if warnings else "success"

    return _result(
        "get_forecast_summary",
        status,
        payload,
        warnings,
        sources,
    )


async def get_deal_risk_summary(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(MLPrediction)
            .where(MLPrediction.model_name.ilike("%deal%"))
            .order_by(MLPrediction.predicted_at.desc())
            .limit(100)
        )
    ).scalars().all()

    if not rows:
        return _result(
            "get_deal_risk_summary",
            "warning",
            {"high_risk_count": 0, "medium_risk_count": 0, "low_risk_count": 0},
            ["No persisted deal-scoring predictions found"],
            ["ml_predictions"],
        )

    risk_buckets = {"high": 0, "medium": 0, "low": 0}
    for row in rows:
        pred = row.prediction if isinstance(row.prediction, dict) else {}
        risk = str(pred.get("risk_level", "medium")).lower()
        if risk not in risk_buckets:
            risk = "medium"
        risk_buckets[risk] += 1

    return _result(
        "get_deal_risk_summary",
        "success",
        {
            "high_risk_count": risk_buckets["high"],
            "medium_risk_count": risk_buckets["medium"],
            "low_risk_count": risk_buckets["low"],
        },
        [],
        ["ml_predictions"],
    )


async def get_rep_clusters_summary(db: AsyncSession) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(MLPrediction)
            .where(MLPrediction.model_name.ilike("%cluster%"))
            .order_by(MLPrediction.predicted_at.desc())
            .limit(100)
        )
    ).scalars().all()

    if not rows:
        fallback_warning = "No persisted clustering results found. Fallback indicates clustering has not yet been run in this environment."
        return _result(
            "get_rep_clusters_summary",
            "warning",
            {"clusters": []},
            [fallback_warning],
            ["ml_predictions"],
        )

    counts: dict[str, int] = {}
    for row in rows:
        pred = row.prediction if isinstance(row.prediction, dict) else {}
        persona = pred.get("persona", "Unlabeled")
        counts[persona] = counts.get(persona, 0) + 1

    data = [{"name": name, "rep_count": count} for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)]
    return _result("get_rep_clusters_summary", "success", {"clusters": data}, [], ["ml_predictions"])


async def get_metric_anomaly_summary(db: AsyncSession, months: int = 12) -> dict[str, Any]:
    """
    Time-series anomaly detection on monthly revenue: which periods look
    statistically unusual against their own trailing history, and why.

    Feeds the agent's `anomaly_question` intent (see executor.py) — that
    branch previously called get_sales_kpis + get_deal_risk_summary, neither
    of which does anything resembling outlier/anomaly detection, despite the
    planner routing "anomaly", "outlier", "spike", "unusual" language there.

    Uses backend.ml.anomaly_detector.detect_anomalies (dual Z-score +
    IsolationForest, with per-anomaly driver attribution) — built, tested,
    but never called from anywhere before this.
    """
    from backend.ml.anomaly_detector import detect_anomalies

    revenue_rows = (
        await db.execute(
            select(Revenue.period, func.sum(Revenue.amount).label("total"))
            .group_by(Revenue.period)
            .order_by(Revenue.period)
        )
    ).all()

    records = [{"period": str(r.period), "revenue": float(r.total or 0.0)} for r in revenue_rows]
    if months:
        records = records[-months:]

    if len(records) < 4:
        return _result(
            "get_metric_anomaly_summary",
            "warning",
            {"periods_analyzed": len(records), "anomalies": [], "anomaly_count": 0},
            ["Not enough revenue history for anomaly detection (need at least 4 periods)."],
            ["revenue"],
        )

    detection = detect_anomalies(records, feature_keys=["revenue"], label_key="period")
    flagged = [a for a in detection["anomalies"] if a["is_anomaly"]]

    warnings: list[str] = []
    if flagged:
        warnings.append(f"{len(flagged)} of {len(records)} period(s) flagged as anomalous.")

    payload = {
        "periods_analyzed": len(records),
        "anomalies": flagged,
        "anomaly_count": len(flagged),
        "baseline": detection.get("baseline", {}),
        "method": detection.get("method"),
    }
    return _result(
        "get_metric_anomaly_summary",
        "warning" if flagged else "success",
        payload,
        warnings,
        ["revenue"],
    )
