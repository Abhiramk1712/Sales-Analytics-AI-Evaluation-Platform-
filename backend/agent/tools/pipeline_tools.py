"""
backend/agent/tools/pipeline_tools.py
=======================================
Agent tools for ML model pipeline management and readiness scoring.
All functions return the standard {tool_name, status, data, warnings, sources} contract.
"""
from __future__ import annotations

import traceback
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.models import Rep, Revenue, Deal, MLPrediction


_MIN_MONTHS_FOR_FORECAST = 6
_MIN_DEALS_FOR_SCORING = 10


async def check_model_readiness(db: AsyncSession) -> dict[str, Any]:
    """
    Assess whether each model type has sufficient data to train/predict.
    Returns readiness scores per model type.
    """
    try:
        # Forecast readiness: count distinct revenue periods
        period_count = (await db.execute(
            select(func.count(Revenue.period.distinct()))
        )).scalar() or 0

        # Deal scoring readiness: count closed deals
        closed_deals = (await db.execute(
            select(func.count(Deal.id)).where(Deal.stage.in_(["Closed Won", "Closed Lost"]))
        )).scalar() or 0

        # Rep clustering readiness: count reps with revenue
        reps_with_revenue = (await db.execute(
            select(func.count(Revenue.rep_id.distinct()))
        )).scalar() or 0

        # Check if predictions already exist
        has_predictions = (await db.execute(
            select(func.count(MLPrediction.id)).where(
                (func.lower(MLPrediction.model_name).like("%deal%")) |
                (func.lower(MLPrediction.entity_type) == "deal")
            )
        )).scalar() or 0

        forecast_ready = period_count >= _MIN_MONTHS_FOR_FORECAST
        scoring_ready = closed_deals >= _MIN_DEALS_FOR_SCORING
        clustering_ready = reps_with_revenue >= 3

        readiness = {
            "forecast": {
                "ready": forecast_ready,
                "reason": f"{period_count} revenue periods (need ≥{_MIN_MONTHS_FOR_FORECAST})" if not forecast_ready else f"{period_count} periods available",
                "score": min(1.0, period_count / _MIN_MONTHS_FOR_FORECAST),
            },
            "deal_scoring": {
                "ready": scoring_ready,
                "reason": f"{closed_deals} closed deals (need ≥{_MIN_DEALS_FOR_SCORING})" if not scoring_ready else f"{closed_deals} closed deals available",
                "score": min(1.0, closed_deals / _MIN_DEALS_FOR_SCORING),
            },
            "rep_clustering": {
                "ready": clustering_ready,
                "reason": f"{reps_with_revenue} reps with revenue (need ≥3)" if not clustering_ready else f"{reps_with_revenue} reps available",
                "score": min(1.0, reps_with_revenue / 3),
            },
        }
        overall_ready = forecast_ready or scoring_ready
        existing_predictions = int(has_predictions)

        return {
            "tool_name": "check_model_readiness",
            "status": "success",
            "data": {
                "overall_ready": overall_ready,
                "existing_predictions": existing_predictions,
                "models": readiness,
            },
            "warnings": [] if overall_ready else ["Insufficient data for model training; at least revenue history is required."],
            "sources": ["revenue_table", "deals_table", "ml_predictions_table"],
        }
    except Exception as exc:
        return {
            "tool_name": "check_model_readiness",
            "status": "error",
            "data": {},
            "warnings": [f"Readiness check failed: {exc}", traceback.format_exc()],
            "sources": [],
        }


async def run_ml_pipeline(db: AsyncSession) -> dict[str, Any]:
    """
    Trigger ML model training pipeline: forecast + deal scoring + clustering.
    Uses the existing TrainingPipeline with demo_mode support.
    """
    try:
        from backend.ml.training_pipeline import get_global_pipeline
        from backend.ml.forecasting import RevenueForecastModel
        from backend.ml.deal_scoring import DealScoringModel
        from backend.ml.rep_clustering import RepClusteringModel
        from backend.models import Revenue
        import pandas as pd

        pipeline = get_global_pipeline()
        results: dict[str, Any] = {}
        warnings: list[str] = []

        if not pipeline.can_train_in_request():
            return {
                "tool_name": "run_ml_pipeline",
                "status": "skipped",
                "data": {"reason": "TRAINING_DEMO_MODE is False; training must run offline."},
                "warnings": ["Set TRAINING_DEMO_MODE=True to enable in-request training."],
                "sources": [],
            }

        # Forecast training
        try:
            revenue_rows = (await db.execute(
                select(Revenue.period, func.sum(Revenue.amount).label("total"))
                .group_by(Revenue.period)
                .order_by(Revenue.period)
            )).all()
            if len(revenue_rows) >= _MIN_MONTHS_FOR_FORECAST:
                series = pd.Series(
                    {r.period: float(r.total) for r in revenue_rows}
                )
                model = RevenueForecastModel()
                model.fit(series)
                forecast = model.predict()
                pipeline.register_training_run("forecast", "success", {"periods": forecast.periods, "metrics": forecast.metrics})
                results["forecast"] = {
                    "status": "trained",
                    "periods": forecast.periods,
                    "forecast": forecast.forecast,
                    "metrics": forecast.metrics,
                    "ensemble_weights": forecast.ensemble_weights,
                }
            else:
                warnings.append(f"Forecast skipped: only {len(revenue_rows)} revenue periods (need ≥{_MIN_MONTHS_FOR_FORECAST}).")
                results["forecast"] = {"status": "skipped", "reason": f"need ≥{_MIN_MONTHS_FOR_FORECAST} periods"}
        except Exception as exc:
            warnings.append(f"Forecast training failed: {exc}")
            results["forecast"] = {"status": "failed", "error": str(exc)}

        # Deal scoring training
        try:
            from sqlalchemy import select as _sel
            from backend.models import Deal as _Deal, Rep as _Rep, Account as _Account
            import numpy as np

            deal_rows = (await db.execute(_sel(_Deal))).scalars().all()
            if len(deal_rows) >= _MIN_DEALS_FOR_SCORING:
                scoring_model = DealScoringModel()
                train_result = await scoring_model.train(db)
                pipeline.register_training_run("deal_scoring", "success", train_result)
                results["deal_scoring"] = {"status": "trained", **train_result}
            else:
                warnings.append(f"Deal scoring skipped: only {len(deal_rows)} deals (need ≥{_MIN_DEALS_FOR_SCORING}).")
                results["deal_scoring"] = {"status": "skipped", "reason": f"need ≥{_MIN_DEALS_FOR_SCORING} deals"}
        except Exception as exc:
            warnings.append(f"Deal scoring training failed: {exc}")
            results["deal_scoring"] = {"status": "failed", "error": str(exc)}

        # Rep clustering
        try:
            clustering_model = RepClusteringModel()
            cluster_result = await clustering_model.train(db)
            pipeline.register_training_run("rep_clustering", "success", cluster_result)
            results["rep_clustering"] = {"status": "trained", **cluster_result}
        except Exception as exc:
            warnings.append(f"Rep clustering failed: {exc}")
            results["rep_clustering"] = {"status": "failed", "error": str(exc)}

        overall_status = "success" if all(
            v.get("status") in ("trained", "skipped") for v in results.values()
        ) else "partial_failure"

        return {
            "tool_name": "run_ml_pipeline",
            "status": overall_status,
            "data": results,
            "warnings": warnings,
            "sources": ["revenue_table", "deals_table", "reps_table"],
        }
    except Exception as exc:
        return {
            "tool_name": "run_ml_pipeline",
            "status": "error",
            "data": {},
            "warnings": [f"ML pipeline failed: {exc}", traceback.format_exc()],
            "sources": [],
        }
