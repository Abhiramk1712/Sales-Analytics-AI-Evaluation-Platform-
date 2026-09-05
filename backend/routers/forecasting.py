"""
ML model endpoints — forecasting, deal scoring, rep clustering.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.database import get_db
from backend.utils.json_safe import json_safe
from backend.auth.dependencies import require_permission
from backend.auth.models import UserContext
from backend.auth.tenant import get_current_company_id, get_tenant_context
from backend.company_context import get_active_company
from backend.models import (
    Revenue,
    Deal,
    Rep,
    Quota,
    Activity,
    Account,
    ModelRunRecord,
    MLPrediction,
    Booking,
    PayoutRecord,
    ArrWaterfallEntry,
    UserProfile,
    Position,
    Plan,
    PlanAssignment,
    POSITION_RANK_DIRECTOR,
    POSITION_RANK_VP,
)
from backend.ml.forecasting import run_revenue_forecast
from backend.metrics import calculators
from backend.ml.forecasting_engine import (
    FORECAST_TYPES,
    forecast as forecast_lab_run,
    forecast_multi_scenario as forecast_lab_multi_run,
)
from backend.ml.deal_scoring import DealScoringModel, build_feature_matrix, prepare_training_frame, run_deal_scoring
from backend.ml.explainability import explain_deal_prediction, global_feature_importance
from backend.ml.forecasting_lab import compare_models_for_target, list_supported_targets, run_forecast_for_target
from backend.ml.forecasting_lab.datasets import future_periods_from_history
from backend.ml.lstm_forecaster import run_lstm_forecast
from backend.ml.deal_slip import DealSlipModel
from backend.ml.rep_clustering import run_rep_clustering
from backend.ml.model_registry import ModelRun, MODEL_REVENUE_FORECAST, MODEL_DEAL_SCORING, MODEL_REP_CLUSTERING
from backend.ml.model_cards import MODEL_CARDS, get_model_card
from backend.ml.model_store import ModelStore
from backend.ml.evaluation import rolling_origin_backtest, detect_drift
from backend.validation.quality_gate import get_critical_issues

router = APIRouter(
    prefix="/ml",
    tags=["Machine Learning"],
    dependencies=[Depends(require_permission("view_forecasts")), Depends(get_tenant_context)],
)
store = ModelStore()


def _confidence_label(value: float) -> str:
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def _data_hash(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.md5(encoded).hexdigest()


async def _save_run(db: AsyncSession, run: ModelRun) -> None:
    summary = run.summary()
    trained_at = datetime.fromisoformat(summary.get("trained_at"))
    if trained_at.tzinfo is not None:
        trained_at = trained_at.astimezone(timezone.utc).replace(tzinfo=None)

    db.add(
        ModelRunRecord(
            model_name=summary.get("model_name"),
            model_version=summary.get("model_version"),
            trained_at=trained_at,
            training_rows=summary.get("training_rows"),
            feature_names=summary.get("feature_names"),
            target_name=summary.get("target"),
            metrics=summary.get("metrics"),
            limitations=summary.get("limitations"),
            artifact_path=summary.get("artifact_path"),
            data_hash=summary.get("data_hash"),
            notes=summary.get("notes"),
        )
    )
    await db.flush()
    # Keep local fallback for environments where DB persistence is unavailable.
    store.append_run(summary, company_id=get_active_company())


async def _persist_prediction(
    db: AsyncSession,
    model_name: str,
    entity_type: str,
    prediction: dict,
    model_version: str,
    confidence: float | None = None,
    entity_id: str | None = None,
) -> bool:
    """Persist prediction with lightweight dedupe against the latest row."""
    parsed_entity_id = None
    if entity_id:
        try:
            parsed_entity_id = uuid.UUID(str(entity_id))
        except Exception:
            parsed_entity_id = None

    latest = (
        await db.execute(
            select(MLPrediction)
            .where(
                MLPrediction.model_name == model_name,
                MLPrediction.entity_type == entity_type,
                MLPrediction.entity_id == parsed_entity_id,
                MLPrediction.model_version == model_version,
            )
            .order_by(MLPrediction.predicted_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if latest and isinstance(latest.prediction, dict) and latest.prediction == prediction:
        return False

    db.add(
        MLPrediction(
            model_name=model_name,
            entity_type=entity_type,
            entity_id=parsed_entity_id,
            prediction=prediction,
            confidence=confidence,
            model_version=model_version,
        )
    )
    return True


def build_prediction_summary(prediction_rows: list[MLPrediction]) -> dict:
    model_counts: dict[str, int] = {}
    latest_by_model: dict[str, str | None] = {}
    warning_counts: dict[str, int] = {}

    latest_forecast = None
    for row in prediction_rows:
        model_name = row.model_name
        model_counts[model_name] = model_counts.get(model_name, 0) + 1

        if row.predicted_at:
            ts = row.predicted_at.isoformat()
            if model_name not in latest_by_model or (latest_by_model[model_name] and ts > latest_by_model[model_name]):
                latest_by_model[model_name] = ts

        pred = row.prediction if isinstance(row.prediction, dict) else {}
        warnings = pred.get("warnings", []) if isinstance(pred.get("warnings", []), list) else []
        if warnings:
            warning_counts[model_name] = warning_counts.get(model_name, 0) + len(warnings)

        if model_name == "revenue_forecast":
            if latest_forecast is None or ((row.predicted_at or datetime.min) > (latest_forecast.predicted_at or datetime.min)):
                latest_forecast = row

    latest_forecast_payload = None
    if latest_forecast:
        latest_forecast_payload = {
            "predicted_at": latest_forecast.predicted_at.isoformat() if latest_forecast.predicted_at else None,
            "model_version": latest_forecast.model_version,
            "confidence": float(latest_forecast.confidence) if latest_forecast.confidence is not None else None,
            "prediction": latest_forecast.prediction,
        }

    return {
        "prediction_count": len(prediction_rows),
        "counts_by_model": model_counts,
        "latest_prediction_by_model": latest_by_model,
        "warning_counts_by_model": warning_counts,
        "latest_forecast": latest_forecast_payload,
    }


def _month_key_from_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.year:04d}-{value.month:02d}"


def _period_to_months(period: str | None) -> list[str]:
    s = str(period or "").strip()
    if len(s) == 7 and s[4] == "-" and s[5:7].isdigit():
        return [s]
    if len(s) == 7 and s[4:6] == "-Q" and s[6].isdigit():
        year = int(s[:4])
        quarter = int(s[6])
        if quarter not in (1, 2, 3, 4):
            return []
        start_month = (quarter - 1) * 3 + 1
        return [f"{year}-{m:02d}" for m in range(start_month, start_month + 3)]
    if len(s) == 4 and s.isdigit():
        year = int(s)
        return [f"{year}-{m:02d}" for m in range(1, 13)]
    return []


async def _load_revenue_history(db: AsyncSession) -> dict[str, float]:
    rows = (
        await db.execute(
            select(Revenue.period, func.sum(Revenue.amount).label("total"))
            .group_by(Revenue.period)
            .order_by(Revenue.period)
        )
    ).all()
    return {str(r.period): float(r.total or 0.0) for r in rows}


async def _load_history_for_forecast_type(
    db: AsyncSession,
    forecast_type: str,
) -> tuple[list[float], list[str], str, list[str]]:
    warnings: list[str] = []
    ft = forecast_type if forecast_type in FORECAST_TYPES else "revenue"
    if ft != forecast_type:
        warnings.append(f"Unsupported forecast_type '{forecast_type}'. Using 'revenue'.")

    series: dict[str, float] = {}
    source = "revenue"

    if ft == "revenue":
        series = await _load_revenue_history(db)
        source = "revenue"

    elif ft == "ARR":
        rows = (
            await db.execute(
                select(ArrWaterfallEntry.period, func.sum(ArrWaterfallEntry.arr_end).label("total"))
                .group_by(ArrWaterfallEntry.period)
                .order_by(ArrWaterfallEntry.period)
            )
        ).all()
        series = {str(r.period): float(r.total or 0.0) for r in rows}
        source = "arr_waterfall.arr_end"

    elif ft in {"pipeline", "commit", "best_case"}:
        rows = (
            await db.execute(
                select(
                    Deal.stage,
                    Deal.amount,
                    Deal.close_probability,
                    Deal.expected_close_date,
                    Deal.created_at,
                )
            )
        ).all()
        bucket: dict[str, float] = {}
        for row in rows:
            stage = str(row.stage or "")
            if stage in {"Closed Won", "Closed Lost"}:
                continue
            period = _month_key_from_date(row.expected_close_date) or _month_key_from_date(row.created_at)
            if not period:
                continue
            amount = float(row.amount or 0.0)
            probability = min(max(float(row.close_probability or 0.0), 0.0), 100.0) / 100.0
            if ft == "commit":
                amount *= probability
            elif ft == "best_case":
                amount *= min(1.0, probability * 1.2)
            bucket[period] = bucket.get(period, 0.0) + amount
        series = bucket
        source = "deals.open_pipeline"

    elif ft == "booking":
        booking_rows = (
            await db.execute(
                select(Booking.booking_date, func.sum(Booking.amount).label("total"))
                .group_by(Booking.booking_date)
                .order_by(Booking.booking_date)
            )
        ).all()
        bucket: dict[str, float] = {}
        for row in booking_rows:
            period = _month_key_from_date(row.booking_date)
            if period:
                bucket[period] = bucket.get(period, 0.0) + float(row.total or 0.0)
        series = bucket
        source = "bookings"

        if not series:
            rows = (
                await db.execute(
                    select(Deal.actual_close_date, func.sum(Deal.amount).label("total"))
                    .where(Deal.stage == "Closed Won")
                    .group_by(Deal.actual_close_date)
                    .order_by(Deal.actual_close_date)
                )
            ).all()
            for row in rows:
                period = _month_key_from_date(row.actual_close_date)
                if period:
                    series[period] = series.get(period, 0.0) + float(row.total or 0.0)
            if series:
                source = "deals.closed_won"
                warnings.append("No bookings table history found; used Closed Won deals as booking proxy.")

    elif ft == "payout":
        revenue_by_period = await _load_revenue_history(db)
        payout_rows = (
            await db.execute(
                select(PayoutRecord.period, func.sum(PayoutRecord.payout_amount).label("total"))
                .group_by(PayoutRecord.period)
                .order_by(PayoutRecord.period)
            )
        ).all()
        bucket: dict[str, float] = {}
        for row in payout_rows:
            months = _period_to_months(str(row.period or ""))
            if not months:
                continue

            total_amount = float(row.total or 0.0)
            weights: list[float]

            if len(months) == 1:
                weights = [1.0]
            else:
                # Distribute multi-month payout periods using realized revenue profile
                # so monthly payout history reflects business seasonality.
                revenue_profile = [max(0.0, float(revenue_by_period.get(m, 0.0))) for m in months]
                revenue_sum = sum(revenue_profile)
                if revenue_sum > 0:
                    weights = [r / revenue_sum for r in revenue_profile]
                elif len(months) == 3:
                    weights = [0.28, 0.33, 0.39]
                else:
                    weights = [1.0 / len(months) for _ in months]

            for month, weight in zip(months, weights):
                bucket[month] = bucket.get(month, 0.0) + total_amount * float(weight)
        series = bucket
        source = "payouts"

    elif ft == "quota_attainment":
        revenue_by_period = await _load_revenue_history(db)
        quota_rows = (
            await db.execute(
                select(Quota.period, func.sum(Quota.amount).label("total"))
                .group_by(Quota.period)
                .order_by(Quota.period)
            )
        ).all()

        quota_monthly: dict[str, float] = {}
        for row in quota_rows:
            months = _period_to_months(str(row.period or ""))
            if not months:
                continue
            alloc = float(row.total or 0.0) / float(len(months))
            for month in months:
                quota_monthly[month] = quota_monthly.get(month, 0.0) + alloc

        months_union = sorted(set(revenue_by_period.keys()) | set(quota_monthly.keys()))
        for month in months_union:
            quota_val = float(quota_monthly.get(month, 0.0))
            revenue_val = float(revenue_by_period.get(month, 0.0))
            series[month] = (revenue_val / quota_val * 100.0) if quota_val > 0 else 0.0
        source = "revenue+quota"

    if not series and ft != "revenue":
        fallback_series = await _load_revenue_history(db)
        if fallback_series:
            series = fallback_series
            source = "revenue_fallback"
            warnings.append(f"No '{ft}' history available. Fell back to revenue history.")

    periods = sorted([p for p in series.keys() if len(str(p)) == 7 and str(p)[4] == "-"])
    values = [round(float(series[p]), 2) for p in periods]
    return values, periods, source, warnings


class ForecastRunRequest(BaseModel):
    target: str = Field(default="revenue")
    horizon: int = Field(default=6, ge=1, le=24)
    scenario: str = Field(default="base")
    include_lstm: bool = Field(default=True)


async def _collect_deal_scoring_inputs(db: AsyncSession) -> tuple[list[dict], list[dict], int]:
    """Load deal rows plus activity events for scoring/evaluation/explainability endpoints."""
    rows = (await db.execute(select(Deal, Account.industry).join(Account, isouter=True))).all()
    if not rows:
        return [], [], 0

    activity_rows = (await db.execute(select(Activity.deal_id, Activity.activity_date, Activity.notes))).all()

    act_map: dict[str, dict] = {}
    activity_events: list[dict] = []
    for row in activity_rows:
        deal_id = str(row.deal_id)
        cur = act_map.setdefault(deal_id, {"count": 0, "last_act": None})
        cur["count"] += 1
        if row.activity_date and (cur["last_act"] is None or row.activity_date > cur["last_act"]):
            cur["last_act"] = row.activity_date

        activity_events.append(
            {
                "deal_id": deal_id,
                "activity_date": row.activity_date,
                "notes": row.notes or "",
            }
        )

    deals_data: list[dict] = []
    for row in rows:
        deal = row.Deal
        acts = act_map.get(str(deal.id), {})
        last_act = acts.get("last_act")
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        days_since = (now_utc_naive - last_act).days if last_act else 30

        deals_data.append(
            {
                "id": str(deal.id),
                "account_id": str(deal.account_id) if deal.account_id else None,
                "rep_id": str(deal.rep_id) if deal.rep_id else None,
                "stage": deal.stage,
                "amount": float(deal.amount or 0.0),
                "close_probability": deal.close_probability or 50,
                "industry": row.industry or "Unknown",
                "product": deal.product or "Unknown",
                "created_at": deal.created_at,
                "expected_close_date": deal.expected_close_date,
                "activity_count": acts.get("count", 0),
                "days_since_last_activity": days_since,
            }
        )

    return deals_data, activity_events, len(activity_rows)


@router.get("/forecast/lab")
async def forecast_lab(
    forecast_type: str = "revenue",
    scenario: str = "base",
    horizon: int = 6,
    confidence_interval: float = 0.8,
    include_multi_scenario: bool = False,
    strategy_override: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Forecasting lab endpoint for scenario analysis and strategy diagnostics.

    Supports the forecasting_engine strategy selector, per-type history loaders,
    and optional multi-scenario output (base / optimistic / conservative).
    """
    if horizon < 1 or horizon > 24:
        raise HTTPException(status_code=400, detail="horizon must be between 1 and 24")
    if confidence_interval not in {0.8, 0.9, 0.95}:
        raise HTTPException(status_code=400, detail="confidence_interval must be one of: 0.8, 0.9, 0.95")

    history, history_periods, source, source_warnings = await _load_history_for_forecast_type(db, forecast_type)
    if not history:
        raise HTTPException(status_code=404, detail=f"No historical data available for forecast_type '{forecast_type}'")

    base_result = forecast_lab_run(
        history=history,
        history_periods=history_periods,
        horizon=horizon,
        forecast_type=forecast_type,
        scenario=scenario,
        confidence_interval=confidence_interval,
        strategy_override=strategy_override,
    ).to_dict()

    warnings = list(dict.fromkeys(source_warnings + list(base_result.get("warnings", []))))
    base_result["warnings"] = warnings

    payload = {
        "forecast_type": base_result.get("forecast_type"),
        "scenario": base_result.get("scenario"),
        "strategy_used": base_result.get("strategy_used"),
        "history_months": base_result.get("history_months", len(history)),
        "horizon_months": base_result.get("horizon_months", horizon),
        "periods": base_result.get("periods", []),
        "values": base_result.get("values", []),
        "lower_bound": base_result.get("lower_bound", []),
        "upper_bound": base_result.get("upper_bound", []),
        "backtest": {
            "mae": base_result.get("backtest_mae"),
            "rmse": base_result.get("backtest_rmse"),
            "mape": base_result.get("backtest_mape"),
        },
        "confidence_interval": base_result.get("confidence_interval", confidence_interval),
        "assumptions": base_result.get("assumptions", []),
        "warnings": warnings,
        "generated_from": {"history_source": source},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if include_multi_scenario:
        multi = forecast_lab_multi_run(
            history=history,
            history_periods=history_periods,
            horizon=horizon,
            forecast_type=forecast_type,
            confidence_interval=confidence_interval,
        )
        payload["scenario_matrix"] = {name: result.to_dict() for name, result in multi.items()}

    try:
        confidence_num = 0.85 if len(history) >= 36 else (0.6 if len(history) >= 18 else 0.3)
        await _persist_prediction(
            db=db,
            model_name=f"forecast_lab_{payload['forecast_type']}",
            entity_type="company",
            prediction={
                "periods": payload["periods"],
                "values": payload["values"],
                "lower_bound": payload["lower_bound"],
                "upper_bound": payload["upper_bound"],
                "backtest": payload["backtest"],
                "scenario": payload["scenario"],
                "strategy_used": payload["strategy_used"],
                "warnings": payload["warnings"],
            },
            confidence=confidence_num,
            model_version=f"lab_{payload['strategy_used']}",
            entity_id=None,
        )
    except Exception:
        payload["warnings"] = sorted(set(payload["warnings"] + ["Prediction persistence failed for forecast lab output."]))

    return payload


@router.get("/forecast/targets")
async def forecast_targets(db: AsyncSession = Depends(get_db)):
    """List supported forecast targets with availability diagnostics."""
    targets = []
    for item in list_supported_targets():
        history, history_periods, source, warnings_list = await _load_history_for_forecast_type(db, item["target"])
        targets.append(
            {
                **item,
                "available": len(history) > 0,
                "history_months": len(history),
                "latest_period": history_periods[-1] if history_periods else None,
                "source": source,
                "warnings": warnings_list,
            }
        )

    return {
        "targets": targets,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/forecast/compare-models")
async def forecast_compare_models(
    request: ForecastRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Compare candidate forecasting models and return leaderboard diagnostics."""
    history, history_periods, source, source_warnings = await _load_history_for_forecast_type(db, request.target)
    if not history:
        raise HTTPException(status_code=404, detail=f"No historical data available for target '{request.target}'")

    payload = compare_models_for_target(
        history_values=history,
        history_periods=history_periods,
        target=request.target,
        horizon=request.horizon,
        include_lstm=request.include_lstm,
    )
    payload["warnings"] = sorted(set(source_warnings + list(payload.get("warnings", []))))
    payload["generated_from"] = {"history_source": source}
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.get("/forecast/model-leaderboard")
async def forecast_model_leaderboard(
    target: str = "revenue",
    horizon: int = 6,
    include_lstm: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Return model leaderboard summary for the given target/horizon."""
    history, history_periods, source, source_warnings = await _load_history_for_forecast_type(db, target)
    if not history:
        raise HTTPException(status_code=404, detail=f"No historical data available for target '{target}'")

    payload = compare_models_for_target(
        history_values=history,
        history_periods=history_periods,
        target=target,
        horizon=horizon,
        include_lstm=include_lstm,
    )
    return {
        "target": target,
        "horizon_months": horizon,
        "selected_model": payload.get("selected_model"),
        "leaderboard": payload.get("leaderboard", []),
        "backtest": payload.get("backtest", {}),
        "warnings": sorted(set(source_warnings + list(payload.get("warnings", [])))),
        "generated_from": {"history_source": source},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/forecast/run")
async def forecast_run(
    request: ForecastRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Run forecast for a target using selected best model and requested scenario."""
    history, history_periods, source, source_warnings = await _load_history_for_forecast_type(db, request.target)
    if not history:
        raise HTTPException(status_code=404, detail=f"No historical data available for target '{request.target}'")

    payload = run_forecast_for_target(
        history_values=history,
        history_periods=history_periods,
        target=request.target,
        horizon=request.horizon,
        scenario=request.scenario,
        include_lstm=request.include_lstm,
    )
    payload["warnings"] = sorted(set(source_warnings + list(payload.get("warnings", []))))
    payload["generated_from"] = {"history_source": source}
    payload["generated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.post("/forecast/scenario")
async def forecast_scenario(
    request: ForecastRunRequest,
    db: AsyncSession = Depends(get_db),
):
    """Scenario wrapper endpoint for base/optimistic/conservative forecast views."""
    return await forecast_run(request=request, db=db)


@router.get("/forecast/lstm")
async def forecast_lstm(
    target: str = "revenue",
    horizon: int = 6,
    db: AsyncSession = Depends(get_db),
):
    """Optional LSTM forecast endpoint with graceful torch fallback."""
    if horizon < 1 or horizon > 24:
        raise HTTPException(status_code=400, detail="horizon must be between 1 and 24")

    history, history_periods, source, source_warnings = await _load_history_for_forecast_type(db, target)
    if not history:
        raise HTTPException(status_code=404, detail=f"No historical data available for target '{target}'")

    lstm_payload = run_lstm_forecast(history, horizon=horizon)
    values = [round(float(v), 2) for v in lstm_payload.get("forecast_values", [])]
    periods = future_periods_from_history(history_periods, horizon)
    lower = [round(max(0.0, v * 0.9), 2) for v in values]
    upper = [round(max(0.0, v * 1.1), 2) for v in values]

    comparison = compare_models_for_target(
        history_values=history,
        history_periods=history_periods,
        target=target,
        horizon=horizon,
        include_lstm=True,
    )

    return {
        "target": target,
        "strategy_used": lstm_payload.get("strategy_used"),
        "history_months": lstm_payload.get("history_months", len(history)),
        "horizon_months": horizon,
        "periods": periods,
        "values": values,
        "lower_bound": lower,
        "upper_bound": upper,
        "backtest": lstm_payload.get("backtest", {}),
        "torch_available": bool(lstm_payload.get("torch_available", False)),
        "sequence_available": bool(lstm_payload.get("sequence_available", False)),
        "sequence_backend": lstm_payload.get("sequence_backend", "baseline"),
        "warnings": sorted(set(source_warnings + list(lstm_payload.get("warnings", [])))),
        "generated_from": {"history_source": source},
        "compare_models": {
            "selected_model": comparison.get("selected_model"),
            "leaderboard": comparison.get("leaderboard", []),
        },
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/forecast/revenue")
async def revenue_forecast(horizon: int = 6, db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(Revenue.period, func.sum(Revenue.amount).label("total"))
            .group_by(Revenue.period)
            .order_by(Revenue.period)
        )
    ).all()

    if not rows:
        return {
            "historical": {},
            "forecast_periods": [],
            "forecast_values": [],
            "lower_ci": [],
            "upper_ci": [],
            "ensemble_weights": {"sarimax": 0.0, "ridge": 0.0, "baseline": 0.0},
            "model_metrics": {},
            "model_info": "No revenue history available",
            "metadata": {
                "forecast_mode": "unavailable",
                "history_months": 0,
                "confidence": "low",
            },
            "data_available": False,
            "generated_from": {"revenue": "missing"},
            "fallback_used": True,
            "warnings": ["No historical revenue data available. Load revenue records to enable forecasting."],
        }

    revenue_by_period = {r.period: float(r.total) for r in rows}
    result = run_revenue_forecast(revenue_by_period, horizon=horizon)

    warnings = list(result.get("warnings", []))
    if len(rows) < 24 and "Insufficient history for full model forecast. Using low-confidence baseline forecast." not in warnings:
        warnings.append("Insufficient history for full model forecast. Using low-confidence baseline forecast.")

    model_run = ModelRun(
        model_name=MODEL_REVENUE_FORECAST,
        model_version=result.get("metadata", {}).get("model_version", "ensemble_v2"),
        training_rows=len(rows),
        feature_names=["period", "revenue"],
        target_name="revenue",
        metrics=result.get("model_metrics", {}),
        limitations=warnings,
        artifact_path="backend/ml/saved",
        data_hash=_data_hash(revenue_by_period),
    )
    await _save_run(db, model_run)

    try:
        forecast_payload = {
            "forecast_periods": result.get("forecast_periods", []),
            "forecast_values": result.get("forecast_values", []),
            "lower_ci": result.get("lower_ci", []),
            "upper_ci": result.get("upper_ci", []),
            "warnings": result.get("warnings", []),
        }
        confidence_label = result.get("metadata", {}).get("confidence", "medium")
        confidence_num = 0.3 if confidence_label == "low" else (0.6 if confidence_label == "medium" else 0.85)
        await _persist_prediction(
            db=db,
            model_name=MODEL_REVENUE_FORECAST,
            entity_type="company",
            entity_id=None,
            prediction=forecast_payload,
            confidence=confidence_num,
            model_version=model_run.model_version,
        )
    except Exception:
        warnings.append("Prediction persistence failed for revenue forecast; returning computed forecast only")

    forecast_meta = dict(result.get("metadata", {}))
    result["metadata"] = model_run.summary()
    result["metadata"]["forecast_mode"] = forecast_meta.get("forecast_mode", "model")
    result["metadata"]["history_months"] = forecast_meta.get("history_months", len(rows))
    result["metadata"]["confidence"] = forecast_meta.get("confidence", "medium")
    result["data_available"] = len(rows) > 0
    result["generated_from"] = {"revenue": "source" if len(rows) > 0 else "missing"}
    result["fallback_used"] = result["metadata"]["forecast_mode"] != "model"
    result["warnings"] = warnings
    return result


@router.get("/score/deals")
async def score_deals(
    optimize: bool = False,
    db: AsyncSession = Depends(get_db),
):
    deals_data, activity_events, activity_count = await _collect_deal_scoring_inputs(db)
    if not deals_data:
        raise HTTPException(404, "No deals found.")

    result = run_deal_scoring(
        deals_data,
        activities=activity_events,
        optimize=optimize,
    )

    optimization = result.get("optimization") if isinstance(result.get("optimization"), dict) else None
    model_version = "v2_opt" if optimization and optimization.get("status") == "optimized" else "v2"

    model_run = ModelRun(
        model_name=MODEL_DEAL_SCORING,
        model_version=model_version,
        training_rows=len(deals_data),
        feature_names=[
            "log_amount",
            "days_in_pipeline",
            "stage_ord",
            "activity_count",
            "days_since_last_activity",
            "industry",
            "product",
            "notes_sentiment_score",
            "notes_urgency_score",
            "notes_followup_signal",
            "notes_avg_length",
            "notes_token_count",
            "notes_positive_keyword_hits",
            "notes_negative_keyword_hits",
        ],
        target_name="closed_won",
        metrics={
            "cv_roc_auc": result.get("cv_roc_auc"),
            "cv_std": result.get("cv_std"),
            "best_cv_roc_auc": optimization.get("best_cv_roc_auc") if optimization else None,
        },
        limitations=[
            "Leakage prevention excludes terminal closed stages and post-outcome fields",
        ],
        artifact_path="backend/ml/saved/deal_scorer.pkl",
        data_hash=_data_hash({"row_count": len(deals_data)}),
    )
    await _save_run(db, model_run)

    try:
        for scored in result.get("scored_deals", []):
            prob = float(scored.get("win_probability", 0.0))
            payload = {
                "win_probability": prob,
                "risk_level": scored.get("risk_level", "medium"),
                "explanation": scored.get("explanation", ""),
            }
            await _persist_prediction(
                db=db,
                model_name=MODEL_DEAL_SCORING,
                entity_type="deal",
                entity_id=scored.get("deal_id"),
                prediction=payload,
                confidence=prob,
                model_version=model_run.model_version,
            )
    except Exception:
        result.setdefault("warnings", []).append("Prediction persistence failed for deal scoring; returning computed scores only")

    result["metadata"] = model_run.summary()
    cv = float(result.get("cv_roc_auc") or 0.0)
    result["data_available"] = len(deals_data) > 0
    result["confidence"] = _confidence_label(cv)
    result["generated_from"] = {
        "deals": "source",
        "activities": "source" if activity_count > 0 else "fallback",
    }
    result["fallback_used"] = cv <= 0.0
    result["warnings"] = result.get("warnings", [])
    return result


def _get_or_train_deal_model(
    deals_data: list[dict],
    activity_events: list[dict],
) -> tuple[DealScoringModel, list[str]]:
    """Load persisted deal model when available, otherwise train one from current data."""
    warnings_list: list[str] = []
    try:
        model = DealScoringModel.load()
        pre = model.pipeline.named_steps.get("pre")
        loaded_features = set(getattr(pre, "feature_names_in_", [])) if pre is not None else set()
        required = {"industry", "product", "notes_sentiment_score", "notes_urgency_score"}
        if loaded_features and not required.issubset(loaded_features):
            raise ValueError("Loaded model uses stale feature schema; retraining required.")
        return model, warnings_list
    except Exception:
        trained = run_deal_scoring(
            deals_data,
            activities=activity_events,
            optimize=False,
            return_model=True,
        )
        model = trained.get("_model")
        if model is None:
            raise HTTPException(status_code=422, detail="Unable to train deal scoring model for explainability.")
        warnings_list.extend(list(trained.get("warnings", [])))
        return model, warnings_list


@router.get("/explain/global-importance")
async def explain_global_importance(
    top_n: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """Global feature importance for deal scoring (SHAP preferred, fallback supported)."""
    deals_data, activity_events, _ = await _collect_deal_scoring_inputs(db)
    if not deals_data:
        raise HTTPException(status_code=404, detail="No deals found.")

    frame, prep_warnings, leakage_violations = prepare_training_frame(deals_data, activities=activity_events)
    if frame.empty:
        raise HTTPException(status_code=422, detail="Unable to build leakage-safe training frame.")

    train_df = frame[frame["target"].notna()].copy()
    if train_df.empty:
        raise HTTPException(status_code=422, detail="No labelled deals available for explainability.")

    model, model_warnings = _get_or_train_deal_model(deals_data, activity_events)
    importance = global_feature_importance(model, train_df)
    features = list(importance.get("features", []))[: max(1, top_n)]

    return {
        "method": importance.get("method", "unknown"),
        "top_features": features,
        "sample_size": int(importance.get("sample_size", len(train_df))),
        "warnings": sorted(
            set(
                prep_warnings
                + leakage_violations
                + model_warnings
                + list(importance.get("warnings", []))
            )
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/explain/deal/{deal_id}")
async def explain_single_deal(
    deal_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Local explanation for a specific deal prediction."""
    deals_data, activity_events, _ = await _collect_deal_scoring_inputs(db)
    if not deals_data:
        raise HTTPException(status_code=404, detail="No deals found.")

    frame, prep_warnings, leakage_violations = prepare_training_frame(deals_data, activities=activity_events)
    if frame.empty:
        raise HTTPException(status_code=422, detail="Unable to build leakage-safe training frame.")

    row = frame[(frame["deal_id"].astype(str) == str(deal_id)) | (frame["id"].astype(str) == str(deal_id))]
    if row.empty:
        raise HTTPException(status_code=404, detail=f"Deal '{deal_id}' not found in scoring frame.")

    model, model_warnings = _get_or_train_deal_model(deals_data, activity_events)
    explanation = explain_deal_prediction(model, row.iloc[[0]])

    return {
        **explanation,
        "warnings": sorted(set(prep_warnings + leakage_violations + model_warnings + list(explanation.get("warnings", [])))),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/evaluate/deal-scoring")
async def evaluate_deal_scoring(
    optimize: bool = False,
    test_size: float = 0.25,
    db: AsyncSession = Depends(get_db),
):
    """Evaluate deal scoring with confusion matrix and standard classification metrics."""
    from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score, roc_auc_score
    from sklearn.model_selection import train_test_split

    deals_data, activity_events, _ = await _collect_deal_scoring_inputs(db)
    if not deals_data:
        raise HTTPException(status_code=404, detail="No deals found.")

    frame, prep_warnings, leakage_violations = prepare_training_frame(deals_data, activities=activity_events)
    if frame.empty:
        raise HTTPException(status_code=422, detail="Unable to build leakage-safe training frame.")

    labelled = frame[frame["target"].notna()].copy()
    if len(labelled) < 30:
        raise HTTPException(status_code=422, detail="Need at least 30 labelled deals for evaluation.")

    X_train, X_test = train_test_split(
        labelled,
        test_size=min(max(test_size, 0.1), 0.4),
        random_state=42,
        stratify=labelled["target"].astype(int),
    )

    model = DealScoringModel()
    warnings_list = list(prep_warnings) + list(leakage_violations)
    optimization = None
    if optimize:
        try:
            optimization = model.optimize_hyperparameters(X_train)
        except Exception as exc:
            warnings_list.append(f"Hyperparameter optimization skipped: {exc}")

    model.fit(X_train)

    y_true = X_test["target"].astype(int).to_numpy()
    X_test_matrix = build_feature_matrix(X_test)
    y_prob = model.pipeline.predict_proba(X_test_matrix)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    cm = confusion_matrix(y_true, y_pred, labels=[0, 1]).tolist()
    precision = float(precision_score(y_true, y_pred, zero_division=0))
    recall = float(recall_score(y_true, y_pred, zero_division=0))
    f1 = float(f1_score(y_true, y_pred, zero_division=0))
    try:
        roc_auc = float(roc_auc_score(y_true, y_prob))
    except Exception:
        roc_auc = 0.0

    class_counts = labelled["target"].astype(int).value_counts().to_dict()
    win_ratio = float(class_counts.get(1, 0) / max(sum(class_counts.values()), 1))
    if win_ratio < 0.2 or win_ratio > 0.8:
        warnings_list.append("Class imbalance detected; consider threshold tuning and stratified monitoring.")

    return {
        "metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "roc_auc": round(roc_auc, 4),
            "cv_roc_auc": round(float(model.cv_roc_auc_), 4),
            "cv_std": round(float(model.cv_std_), 4),
        },
        "confusion_matrix": {
            "labels": ["closed_lost", "closed_won"],
            "matrix": cm,
        },
        "class_distribution": {
            "labelled_rows": int(len(labelled)),
            "train_rows": int(len(X_train)),
            "test_rows": int(len(X_test)),
            "counts": {"closed_lost": int(class_counts.get(0, 0)), "closed_won": int(class_counts.get(1, 0))},
            "win_ratio": round(win_ratio, 4),
        },
        "optimization": optimization,
        "warnings": sorted(set(warnings_list)),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/cluster/reps")
async def cluster_reps(db: AsyncSession = Depends(get_db)):
    reps = (await db.execute(select(Rep))).scalars().all()
    if len(reps) < 4:
        raise HTTPException(400, "Need at least 4 reps to cluster.")

    reps_data = []
    leadership_rows = []
    warnings = []

    role_rows = (
        await db.execute(
            select(UserProfile.email, Position.rank)
            .join(Position, UserProfile.position_id == Position.id, isouter=True)
        )
    ).all()
    rank_by_email = {
        str(email).strip().lower(): int(rank if rank is not None else 99)
        for email, rank in role_rows
        if email
    }

    global_plan_rows = (
        await db.execute(
            select(UserProfile.email)
            .join(PlanAssignment, PlanAssignment.user_id == UserProfile.id)
            .join(Plan, Plan.id == PlanAssignment.plan_id)
            .where(Plan.scope.in_(["global", "department"]))
        )
    ).all()
    global_plan_emails = {str(row.email).strip().lower() for row in global_plan_rows if row.email}

    leadership_tokens = (
        "chief", "ceo", "cro", "coo", "cfo", "cmo", "cto",
        "vp", "vice president", "svp", "evp", "director", "head",
    )

    for rep in reps:
        rev = (await db.execute(select(func.sum(Revenue.amount)).where(Revenue.rep_id == rep.id))).scalar() or 0
        quota = (await db.execute(select(func.sum(Quota.amount)).where(Quota.rep_id == rep.id))).scalar() or 1
        won = (await db.execute(select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won"))).scalar() or 0
        lost = (await db.execute(select(func.count(Deal.id)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Lost"))).scalar() or 0
        pipeline = (
            await db.execute(select(func.sum(Deal.amount)).where(Deal.rep_id == rep.id, ~Deal.stage.in_(["Closed Won", "Closed Lost"])))
        ).scalar() or 0
        deals_won_amt = (await db.execute(select(func.avg(Deal.amount)).where(Deal.rep_id == rep.id, Deal.stage == "Closed Won"))).scalar() or 0
        activities = (await db.execute(select(func.count(Activity.id)).join(Deal).where(Deal.rep_id == rep.id))).scalar() or 0

        cycle_rows = (
            await db.execute(
                select(func.avg(func.extract("day", Deal.actual_close_date - Deal.created_at))).where(
                    Deal.rep_id == rep.id,
                    Deal.actual_close_date.isnot(None),
                )
            )
        ).scalar()
        if cycle_rows is None:
            warnings.append(f"avg_sales_cycle fallback used for rep {rep.name}")
        avg_sales_cycle = float(cycle_rows) if cycle_rows else 45

        rep_payload = {
            "rep_id": str(rep.id),
            "name": rep.name,
            "attainment_pct": round(float(rev) / float(quota) * 100, 1),
            "win_rate": round(won / (won + lost) * 100, 1) if (won + lost) else 0,
            "avg_deal_size": float(deals_won_amt),
            "pipeline_coverage": round(float(pipeline) / float(quota), 3) if quota else 0,
            "avg_sales_cycle": avg_sales_cycle,
            "activity_rate": activities,
        }

        email_key = str(rep.email or "").strip().lower()
        rep_name_l = str(rep.name or "").strip().lower()
        pos_rank = rank_by_email.get(email_key, 99)
        # Only exclude VP+ from clustering (rank <= 2); keep managers/directors in cluster pool
        leadership_by_rank = pos_rank <= POSITION_RANK_VP
        leadership_by_plan = email_key in global_plan_emails
        leadership_by_title = any(token in rep_name_l for token in leadership_tokens)
        leadership_reasons = []
        if leadership_by_rank:
            leadership_reasons.append("position_rank")
        if leadership_by_plan:
            leadership_reasons.append("global_or_department_plan")
        if leadership_by_title:
            leadership_reasons.append("executive_title")

        if leadership_reasons:
            leadership_rows.append(
                {
                    "rep_id": str(rep.id),
                    "rep_name": rep.name,
                    "cluster_id": -1,
                    "persona": "Leadership Oversight",
                    "pca_x": 0.0,
                    "pca_y": 0.0,
                    "features": {
                        "attainment_pct": rep_payload["attainment_pct"],
                        "win_rate": rep_payload["win_rate"],
                        "avg_deal_size": rep_payload["avg_deal_size"],
                        "pipeline_coverage": rep_payload["pipeline_coverage"],
                        "avg_sales_cycle": round(1 / max(1.0, rep_payload["avg_sales_cycle"]), 2),
                        "activity_rate": rep_payload["activity_rate"],
                    },
                    "leadership_reason": leadership_reasons,
                }
            )
            continue

        reps_data.append(rep_payload)

    if len(reps_data) >= 4:
        result = run_rep_clustering(reps_data)
    else:
        warnings.append("Insufficient individual-contributor reps for k-means; used persona rules fallback.")
        fallback_clusters = []
        persona_to_cluster = {"Top Performer": 0, "Rising Star": 1, "Needs Coaching": 2}
        for row in reps_data:
            attainment = float(row.get("attainment_pct", 0.0))
            pipeline_cov = float(row.get("pipeline_coverage", 0.0))
            if attainment >= 100.0:
                persona = "Top Performer"
            elif attainment >= 75.0 or pipeline_cov >= 2.0:
                persona = "Rising Star"
            else:
                persona = "Needs Coaching"

            fallback_clusters.append(
                {
                    "rep_id": row["rep_id"],
                    "rep_name": row["name"],
                    "cluster_id": persona_to_cluster[persona],
                    "persona": persona,
                    "pca_x": 0.0,
                    "pca_y": 0.0,
                    "features": {
                        "attainment_pct": row["attainment_pct"],
                        "win_rate": row["win_rate"],
                        "avg_deal_size": row["avg_deal_size"],
                        "pipeline_coverage": row["pipeline_coverage"],
                        "avg_sales_cycle": round(1 / max(1.0, row["avg_sales_cycle"]), 2),
                        "activity_rate": row["activity_rate"],
                    },
                }
            )

        result = {
            "model_info": "Persona rules fallback (IC-only) with leadership exclusion",
            "diagnostics": {
                "optimal_k": None,
                "silhouette_scores": {},
                "pca_variance_pct": [],
                "persona_map": persona_to_cluster,
            },
            "clusters": fallback_clusters,
        }

    if leadership_rows:
        result["clusters"] = list(result.get("clusters", [])) + leadership_rows
        warnings.append(
            f"Excluded {len(leadership_rows)} leadership users from coaching personas (director+ rank, global/department plans, or executive titles)."
        )

    model_run = ModelRun(
        model_name=MODEL_REP_CLUSTERING,
        model_version="v1",
        training_rows=len(reps_data),
        feature_names=["attainment_pct", "win_rate", "avg_deal_size", "pipeline_coverage", "avg_sales_cycle", "activity_rate"],
        target_name=None,
        metrics={"optimal_k": result.get("diagnostics", {}).get("optimal_k")},
        limitations=warnings,
        artifact_path="backend/ml/saved/rep_clusters.pkl",
        data_hash=_data_hash({"row_count": len(reps_data)}),
    )
    await _save_run(db, model_run)

    try:
        for c in result.get("clusters", []):
            payload = {
                "cluster": c.get("cluster_id"),
                "persona": c.get("persona"),
                "summary": f"Assigned to persona {c.get('persona', 'unknown')}",
            }
            await _persist_prediction(
                db=db,
                model_name=MODEL_REP_CLUSTERING,
                entity_type="rep",
                entity_id=c.get("rep_id"),
                prediction=payload,
                confidence=None,
                model_version=model_run.model_version,
            )
    except Exception:
        warnings.append("Prediction persistence failed for rep clustering; returning computed clusters only")

    result["metadata"] = model_run.summary()
    result["data_available"] = len(result.get("clusters", [])) >= 1
    result["confidence"] = "medium" if len(reps_data) >= 4 else "low"
    result["generated_from"] = {
        "reps": "source",
        "revenue": "source",
        "quota": "source",
        "deals": "source",
    }
    result["fallback_used"] = False
    result["warnings"] = warnings
    return result


@router.post("/train/forecasting")
async def train_forecasting_model(
    db: AsyncSession = Depends(get_db),
    _: UserContext = Depends(require_permission("run_model_training")),
):
    critical = await get_critical_issues(db)
    if critical:
        raise HTTPException(status_code=409, detail={"message": "Model training blocked by critical data quality issues", "critical_issues": critical})
    result = await revenue_forecast(horizon=6, db=db)
    return {"status": "success", "message": "Forecasting model run completed", "metadata": result.get("metadata")}


@router.post("/train/deal-scoring")
async def train_deal_scoring_model(
    optimize: bool = False,
    db: AsyncSession = Depends(get_db),
    _: UserContext = Depends(require_permission("run_model_training")),
):
    critical = await get_critical_issues(db)
    if critical:
        raise HTTPException(status_code=409, detail={"message": "Model training blocked by critical data quality issues", "critical_issues": critical})
    result = await score_deals(optimize=optimize, db=db)
    return {"status": "success", "message": "Deal scoring model run completed", "metadata": result.get("metadata")}


@router.post("/train/rep-clustering")
async def train_rep_clustering_model(
    db: AsyncSession = Depends(get_db),
    _: UserContext = Depends(require_permission("run_model_training")),
):
    critical = await get_critical_issues(db)
    if critical:
        raise HTTPException(status_code=409, detail={"message": "Model training blocked by critical data quality issues", "critical_issues": critical})
    result = await cluster_reps(db=db)
    return {"status": "success", "message": "Rep clustering model run completed", "metadata": result.get("metadata")}


@router.get("/model-runs")
async def model_runs(
    db: AsyncSession = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
):
    def _task_type(model_name: str) -> str:
        lowered = (model_name or "").lower()
        if "forecast" in lowered:
            return "forecasting"
        if "deal" in lowered:
            return "classification"
        if "cluster" in lowered:
            return "clustering"
        return "ml"

    def _algorithm(model_name: str) -> str:
        lowered = (model_name or "").lower()
        if "forecast" in lowered:
            return "ensemble"
        if "deal" in lowered:
            return "random_forest_calibrated"
        if "cluster" in lowered:
            return "kmeans"
        return "unspecified"

    def _confidence(metrics: dict[str, Any]) -> str:
        if not metrics:
            return "low"
        if "mape" in metrics:
            mape = float(metrics.get("mape") or 0.0)
            if mape <= 0.1:
                return "high"
            if mape <= 0.2:
                return "medium"
            return "low"
        if "cv_roc_auc" in metrics:
            auc = float(metrics.get("cv_roc_auc") or 0.0)
            return _confidence_label(auc)
        return "medium"

    rows = (
        await db.execute(
            select(ModelRunRecord)
            .order_by(ModelRunRecord.trained_at.desc())
            .limit(100)
        )
    ).scalars().all()
    if rows:
        return {
            "model_runs": [
                {
                    "model_run_id": str(row.id),
                    "company_id": company_id,
                    "model_name": row.model_name,
                    "model_version": row.model_version,
                    "task_type": _task_type(row.model_name),
                    "train_period_start": None,
                    "train_period_end": None,
                    "test_period_start": None,
                    "test_period_end": None,
                    "feature_set_version": row.model_version,
                    "dataset_hash": row.data_hash,
                    "algorithm": _algorithm(row.model_name),
                    "hyperparameters_json": {},
                    "metrics_json": row.metrics or {},
                    "drift_status": "unknown",
                    "confidence_label": _confidence(row.metrics or {}),
                    "created_at": row.trained_at.isoformat() if row.trained_at else None,
                    "approved_for_demo": True,
                    "approved_for_production": False,
                    "trained_at": row.trained_at.isoformat() if row.trained_at else None,
                    "training_rows": row.training_rows,
                    "feature_names": row.feature_names or [],
                    "target": row.target_name,
                    "metrics": row.metrics or {},
                    "limitations": row.limitations or [],
                    "artifact_path": row.artifact_path,
                    "data_hash": row.data_hash,
                    "notes": row.notes,
                }
                for row in rows
            ],
            "active_company": get_active_company(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    fallback_runs = []
    for run in store.load_runs(company_id=company_id):
        metrics_json = run.get("metrics") or {}
        fallback_runs.append(
            {
                "model_run_id": run.get("trained_at") or run.get("model_name"),
                "company_id": company_id,
                "model_name": run.get("model_name"),
                "model_version": run.get("model_version"),
                "task_type": _task_type(str(run.get("model_name") or "")),
                "train_period_start": None,
                "train_period_end": None,
                "test_period_start": None,
                "test_period_end": None,
                "feature_set_version": run.get("model_version"),
                "dataset_hash": run.get("data_hash"),
                "algorithm": _algorithm(str(run.get("model_name") or "")),
                "hyperparameters_json": {},
                "metrics_json": metrics_json,
                "drift_status": "unknown",
                "confidence_label": _confidence(metrics_json),
                "created_at": run.get("trained_at"),
                "approved_for_demo": True,
                "approved_for_production": False,
                **run,
            }
        )

    return {
        "model_runs": fallback_runs,
        "active_company": get_active_company(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/model-cards")
async def model_cards() -> dict[str, Any]:
    return {
        "model_cards": list(MODEL_CARDS.values()),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/model-cards/{model_name}")
async def model_card(model_name: str) -> dict[str, Any]:
    card = get_model_card(model_name)
    if not card:
        raise HTTPException(status_code=404, detail=f"Model card '{model_name}' not found")
    return card


@router.get("/model-monitoring/summary")
async def model_monitoring_summary(
    db: AsyncSession = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(ModelRunRecord)
            .order_by(ModelRunRecord.trained_at.desc())
            .limit(200)
        )
    ).scalars().all()

    latest_by_model: dict[str, ModelRunRecord] = {}
    for row in rows:
        if row.model_name not in latest_by_model:
            latest_by_model[row.model_name] = row

    health_rows: list[dict[str, Any]] = []
    for model_name, row in latest_by_model.items():
        metrics = row.metrics or {}
        confidence = "medium"
        validation_metric = None
        drift_status = "unknown"
        warning_messages: list[str] = []

        if "mape" in metrics:
            validation_metric = {"name": "mape", "value": metrics.get("mape")}
            confidence = _confidence_label(1 - min(1.0, float(metrics.get("mape") or 1.0)))
            if float(metrics.get("mape") or 0.0) > 0.2:
                warning_messages.append("MAPE is above 20%; review forecast assumptions and data freshness.")
        elif "cv_roc_auc" in metrics:
            validation_metric = {"name": "cv_roc_auc", "value": metrics.get("cv_roc_auc")}
            confidence = _confidence_label(float(metrics.get("cv_roc_auc") or 0.0))
            if float(metrics.get("cv_roc_auc") or 0.0) < 0.65:
                warning_messages.append("Deal scoring ROC-AUC below 0.65; retraining and feature QA recommended.")

        recommended_action = (
            "Monitor"
            if not warning_messages
            else "Review data quality, retrain model, and validate with backtesting before promotion"
        )

        health_rows.append(
            {
                "company_id": company_id,
                "model_name": model_name,
                "model_version": row.model_version,
                "last_trained_date": row.trained_at.isoformat() if row.trained_at else None,
                "validation_metric": validation_metric,
                "drift_status": drift_status,
                "confidence": confidence,
                "warning_messages": warning_messages,
                "recommended_action": recommended_action,
            }
        )

    return {
        "company_id": company_id,
        "models": sorted(health_rows, key=lambda r: r.get("model_name", "")),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/predictions/summary")
async def predictions_summary(db: AsyncSession = Depends(get_db)):
    prediction_rows = (
        await db.execute(
            select(MLPrediction)
            .order_by(MLPrediction.predicted_at.desc())
            .limit(1000)
        )
    ).scalars().all()

    latest_run = (
        await db.execute(
            select(ModelRunRecord)
            .order_by(ModelRunRecord.trained_at.desc())
            .limit(1)
        )
    ).scalars().first()

    summary = build_prediction_summary(prediction_rows)
    summary["last_successful_run"] = (
        {
            "model_name": latest_run.model_name,
            "model_version": latest_run.model_version,
            "trained_at": latest_run.trained_at.isoformat() if latest_run.trained_at else None,
            "limitations": latest_run.limitations or [],
            "metrics": latest_run.metrics or {},
        }
        if latest_run
        else None
    )
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    return summary


@router.get("/forecast/arr-waterfall")
async def arr_waterfall(db: AsyncSession = Depends(get_db)):
    """ARR waterfall decomposition: new logo, expansion, contraction, churn, renewal per month.

    Sourced from the canonical `arr_waterfall` table via
    backend.metrics.calculators.calc_arr_waterfall_series -- the same source
    GET /analytics/arr-waterfall uses. This used to reconstruct components from
    Revenue.revenue_type via build_arr_waterfall(), which computed
    arr_start[period] = total_revenue[period] * 12 independently for every
    period and then added new_logo/expansion/contraction/churn again on top --
    double-counting components already inside that same period's total_revenue,
    and never carrying arr_end forward as the next period's arr_start. Confirmed
    live: April 2026 showed $2.8M "net new ARR" against a $4.7M ARR base in a
    single month, and May's arr_start didn't match April's arr_end.
    """
    # No `months` param on this endpoint historically -- it always returns
    # every available period. calc_arr_waterfall_series's `months` is a
    # `.limit()`, so a generous constant preserves that "all periods" behavior.
    series = await calculators.calc_arr_waterfall_series(db, months=9999)
    if not series:
        raise HTTPException(status_code=404, detail="No revenue data available")

    waterfall: dict[str, list] = {
        "periods":     [s["period"] for s in series],
        "arr_start":   [s["arr_start"] for s in series],
        "arr_end":     [s["arr_end"] for s in series],
        "new_logo":    [s["new_logo"] for s in series],
        "expansion":   [s["expansion"] for s in series],
        "contraction": [s["contraction"] for s in series],
        "churn":       [s["churn"] for s in series],
        "renewal":     [s["renewal"] for s in series],
        "net_new_arr": [s["net_new_arr"] for s in series],
    }

    # Rolling 12-month NRR, computed from the same (now-continuous) arr_start series.
    arr_start_series = waterfall["arr_start"]
    expansion_series = waterfall["expansion"]
    contraction_series = waterfall["contraction"]
    churn_series = waterfall["churn"]
    nrr_12m: list[float | None] = []
    for i in range(len(waterfall["periods"])):
        if i < 11:
            nrr_12m.append(None)
            continue
        window_start_arr = sum(arr_start_series[i - 11:i + 1]) / 12
        window_exp = sum(expansion_series[i - 11:i + 1])
        window_con = sum(contraction_series[i - 11:i + 1])
        window_churn = sum(churn_series[i - 11:i + 1])
        nrr_12m.append(
            round((window_start_arr + window_exp + window_con + window_churn) / window_start_arr * 100, 2)
            if window_start_arr > 0 else None
        )
    waterfall["nrr_rolling_12m"] = nrr_12m

    # net_mrr as an alias for net_new_arr / 12, for frontend compatibility.
    waterfall["net_mrr"] = [round(v / 12, 2) if v else 0 for v in waterfall["net_new_arr"]]

    return {
        "waterfall": waterfall,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/score/deal-slip")
async def deal_slip_risk(db: AsyncSession = Depends(get_db)):
    """Identify open deals at risk of slipping past their expected close date."""
    deal_rows = (await db.execute(select(Deal))).scalars().all()
    activity_rows = (await db.execute(select(Activity))).scalars().all()

    if not deal_rows:
        raise HTTPException(status_code=404, detail="No deals found")

    import pandas as pd
    deals_df = pd.DataFrame([{
        "id": str(d.id),
        "deal_id": str(d.id),
        "name": d.name or "",
        "stage": d.stage or "Prospecting",
        "amount": float(d.amount or 0),
        "close_probability": float(d.close_probability or 50),
        "expected_close_date": str(d.expected_close_date) if d.expected_close_date else None,
        "actual_close_date": str(d.actual_close_date) if d.actual_close_date else None,
        "created_at": d.created_at,
        "rep_id": str(d.rep_id),
    } for d in deal_rows])

    activities_df = pd.DataFrame([{
        "id": str(a.id),
        "deal_id": str(a.deal_id),
        "activity_date": str(a.activity_date) if a.activity_date else None,
    } for a in activity_rows])

    model = DealSlipModel()
    model.fit(deals_df, activities_df)
    results = model.predict(deals_df, activities_df)

    slip_count = sum(1 for r in results if r.slip_flag)
    return {
        "total_open_deals": len(results),
        "slip_risk_count": slip_count,
        "slip_risk_pct": round(slip_count / max(len(results), 1) * 100, 1),
        "deals_at_risk": [
            {
                "deal_id": r.deal_id,
                "deal_name": r.deal_name,
                "slip_risk_score": r.slip_risk_score,
                "slip_flag": r.slip_flag,
                "expected_close_date": r.expected_close_date,
                "days_until_close": r.days_until_close,
                "stage": r.stage,
                "amount": r.amount,
                "top_risk_factors": r.top_risk_factors,
            }
            for r in results[:20]  # top 20 at-risk
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/forecast/accuracy")
async def forecast_accuracy(
    rep_id: str = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Rolling-origin backtest accuracy for the revenue forecast model.
    Optional rep_id filters to that rep's revenue history.
    """
    import uuid as _uuid
    import pandas as pd

    q = select(Revenue.period, func.sum(Revenue.amount).label("total")).group_by(Revenue.period)
    if rep_id:
        try:
            rid = _uuid.UUID(rep_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid rep_id UUID")
        q = q.where(Revenue.rep_id == rid)
    rows = (await db.execute(q.order_by(Revenue.period))).all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No revenue data found" + (f" for rep {rep_id}" if rep_id else ""),
        )

    series = pd.Series(
        {r.period: float(r.total or 0) for r in rows}
    )
    backtest = rolling_origin_backtest(series)
    history_months = len(series)

    return {
        "rep_id": rep_id,
        "history_months": history_months,
        "backtest": backtest,
        "confidence": "high" if history_months >= 36 else ("medium" if history_months >= 18 else "low"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── Model drift detection ─────────────────────────────────────────────────

@router.get("/drift")
async def model_drift_report(
    model_name: str = "revenue_forecast",
    rep_id: str | None = None,
    db: AsyncSession = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
):
    """
    Compare the model's training-time metrics (from ModelStore) against the
    current rolling-origin backtest performance.  Returns a drift report with
    per-metric deltas and an overall ``drifted`` flag.

    The baseline is scoped to this company -- unscoped, this compared every
    tenant's current backtest against whichever company's run happened to be
    the globally most recent one in ModelStore (confirmed live: techo-solutions
    and insurex both got the identical baseline_trained_at/training_rows).
    """
    # 1. Load baseline from model store
    all_runs = store.load_runs(company_id=company_id)
    # Take the latest run matching the requested model_name
    matching = [r for r in all_runs if r.get("model_name") == model_name]
    if not matching:
        raise HTTPException(
            status_code=404,
            detail=f"No stored training run found for model '{model_name}'. "
                   f"Train the model first so baseline metrics are recorded.",
        )
    baseline_run = matching[-1]  # most recent
    baseline_metrics: dict = baseline_run.get("metrics", {})

    if not baseline_metrics:
        return {
            "drift_status": "no_baseline",
            "model_name": model_name,
            "message": f"Training run for '{model_name}' has no metrics recorded. Run model training first to establish a baseline.",
            "features": [],
            "drift_results": [],
            "baseline_metrics": {},
            "current_metrics": {},
        }

    # 2. Build current backtest
    rid: uuid.UUID | None = None
    if rep_id:
        try:
            rid = uuid.UUID(rep_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="rep_id must be a valid UUID")

    q = select(
        Revenue.period,
        func.sum(Revenue.amount).label("total"),
    ).group_by(Revenue.period)
    if rid:
        q = q.where(Revenue.rep_id == rid)
    rows = (await db.execute(q.order_by(Revenue.period))).all()

    if not rows:
        raise HTTPException(
            status_code=404,
            detail="No revenue data found" + (f" for rep {rep_id}" if rep_id else ""),
        )

    import pandas as pd
    series = pd.Series({r.period: float(r.total or 0) for r in rows})
    current_backtest = rolling_origin_backtest(series)

    if current_backtest.get("status") != "ok":
        return {
            "model_name": model_name,
            "rep_id": rep_id,
            "drifted": False,
            "severity": "unknown",
            "reason": current_backtest.get("status", "unknown"),
            "warnings": current_backtest.get("warnings", []),
            "baseline_trained_at": baseline_run.get("trained_at"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    # 3. Run drift detection
    drift = detect_drift(baseline_metrics, current_backtest)

    return {
        "model_name": model_name,
        "rep_id": rep_id,
        "baseline_trained_at": baseline_run.get("trained_at"),
        "baseline_training_rows": baseline_run.get("training_rows"),
        "history_months": len(series),
        "current_backtest_folds": current_backtest.get("folds"),
        **drift,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── C2: Pipeline funnel forecast ──────────────────────────────────────────

@router.get("/forecast/pipeline-funnel")
async def pipeline_funnel_forecast(
    horizon_days: int = 90,
    db: AsyncSession = Depends(get_db),
):
    """
    90-day rolling pipeline forecast.
    Converts open deals to expected bookings using stage-specific close probabilities
    and time decay past expected close date.
    """
    from backend.ml.pipeline_forecaster import forecast_pipeline

    rows = (await db.execute(
        select(
            Deal.id, Deal.stage, Deal.amount, Deal.expected_close_date,
        ).where(Deal.stage.notin_(["Closed Won", "Closed Lost"]))
    )).all()

    open_deals = [
        {
            "id":             str(r.id),
            "stage":          r.stage,
            "amount":         float(r.amount or 0.0),
            "expected_close": r.expected_close_date,
        }
        for r in rows
    ]

    result = forecast_pipeline(open_deals, horizon_days=horizon_days)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result


# ── C3: Churn / retention forecast ───────────────────────────────────────

@router.get("/forecast/churn-risk")
async def churn_risk_forecast(db: AsyncSession = Depends(get_db)):
    """
    Kaplan-Meier + Weibull AFT churn/retention model.
    Returns survival curve, cohort retention, and per-account churn risk tier.
    """
    from backend.ml.churn_forecaster import fit_survival_model
    from backend.models import Account

    rows = (await db.execute(
        select(Account.id, Account.name, Account.industry, Account.created_at)
    )).all()

    if not rows:
        return {"error": "No account data available.", "warnings": []}

    # Determine tenure and churn status from revenue activity
    rev_rows = (await db.execute(
        select(Revenue.account_id, func.max(Revenue.period).label("last_rev_period"))
        .group_by(Revenue.account_id)
    )).all() if rows else []
    last_rev: dict = {str(r.account_id): r.last_rev_period for r in rev_rows}

    from datetime import date as _date, datetime as _datetime
    today = _date.today()

    records = []
    for r in rows:
        created_dt = r.created_at
        if isinstance(created_dt, str):
            try:
                created_dt = _datetime.fromisoformat(created_dt)
            except Exception:
                created_dt = None
        tenure_months = (
            (today.year - created_dt.year) * 12 + (today.month - created_dt.month)
            if created_dt else 12
        )
        last_rev_period = last_rev.get(str(r.id))
        churned = False
        if last_rev_period:
            try:
                last_yr, last_mo = int(last_rev_period[:4]), int(last_rev_period[5:7])
                months_since_rev = (today.year - last_yr) * 12 + (today.month - last_mo)
                churned = months_since_rev >= 3
            except Exception:
                pass

        records.append({
            "account_id":    str(r.id),
            "tenure_months": max(1, tenure_months),
            "churned":       churned,
            "cohort":        f"{created_dt.year}-Q{(created_dt.month-1)//3+1}" if created_dt else "unknown",
        })

    result = fit_survival_model(records)
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    # The survival model yields inf for a curve that never drops below 0.5 —
    # an infinite median tenure — and JSON cannot express it. Sanitise at the
    # boundary rather than per field; the field that broke was one nobody had
    # considered.
    return json_safe(result)


# ── C5: Rep attainment forecast ───────────────────────────────────────────

@router.get("/forecast/rep-attainment")
async def rep_attainment_forecast(
    db: AsyncSession = Depends(get_db),
):
    """
    Per-rep attainment forecast for the current quarter.

    Returns commit (P20) / base (P50) / upside (P80) attainment projections
    alongside:
      - quota_gap          : revenue still needed to hit 100% quota
      - expected_from_pipe : pipeline × win-rate (expected close value)
      - pipe_needed        : gap that cannot be covered by current pipeline
      - momentum           : trailing 2-month attainment trend
      - top_focus_deals    : top 3 open deals by weighted value (amount × prob)
      - motivation_label   : motivational signal derived from trajectory
    """
    from backend.ml.attainment_forecaster import forecast_rep_attainment
    from datetime import date as _date

    # ── Current quarter window ──────────────────────────────────────────────
    today = _date.today()
    q_month_start = ((today.month - 1) // 3) * 3 + 1
    q_start = _date(today.year, q_month_start, 1)
    q_end_month = q_month_start + 2
    import calendar as _calendar
    q_end = _date(today.year, q_end_month, _calendar.monthrange(today.year, q_end_month)[1])
    days_into_quarter = max(1, (today - q_start).days + 1)
    days_in_quarter = (q_end - q_start).days + 1
    quarter_num = (q_month_start - 1) // 3 + 1
    current_quarter_label = f"{today.year}-Q{quarter_num}"
    # Monthly period labels for revenue QTD filter
    q_months = [f"{today.year}-{m:02d}" for m in range(q_month_start, q_month_start + 3) if m <= today.month]

    # ── Prior quarter window (for momentum) ────────────────────────────────
    prev_q_num = quarter_num - 1 if quarter_num > 1 else 4
    prev_q_year = today.year if quarter_num > 1 else today.year - 1
    prev_quarter_label = f"{prev_q_year}-Q{prev_q_num}"

    # ── Load reps ──────────────────────────────────────────────────────────
    reps = (await db.execute(select(Rep))).scalars().all()

    # ── Load all signals in batch ──────────────────────────────────────────
    # Revenue QTD per rep (monthly format: 2026-04, 2026-05)
    rev_qtd_rows = (await db.execute(
        select(Revenue.rep_id, func.sum(Revenue.amount).label("total"))
        .where(Revenue.period.in_(q_months))
        .group_by(Revenue.rep_id)
    )).all()
    rev_qtd: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in rev_qtd_rows}

    # Revenue prior quarter per rep (monthly format)
    prev_q_month_start = (prev_q_num - 1) * 3 + 1
    prev_q_months = [f"{prev_q_year}-{m:02d}" for m in range(prev_q_month_start, prev_q_month_start + 3)]
    rev_prev_rows = (await db.execute(
        select(Revenue.rep_id, func.sum(Revenue.amount).label("total"))
        .where(Revenue.period.in_(prev_q_months))
        .group_by(Revenue.rep_id)
    )).all()
    rev_prev: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in rev_prev_rows}

    # Quota current quarter per rep (quarterly format: 2026-Q2)
    quota_rows = (await db.execute(
        select(Quota.rep_id, func.sum(Quota.amount).label("total"))
        .where(Quota.period == current_quarter_label)
        .group_by(Quota.rep_id)
    )).all()
    quota_map: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in quota_rows}

    # Quota prior quarter per rep (quarterly format)
    quota_prev_rows = (await db.execute(
        select(Quota.rep_id, func.sum(Quota.amount).label("total"))
        .where(Quota.period == prev_quarter_label)
        .group_by(Quota.rep_id)
    )).all()
    quota_prev_map: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in quota_prev_rows}

    # Open pipeline per rep — only deals expected to close this quarter (for attainment forecast)
    pipe_rows = (await db.execute(
        select(Deal.rep_id, func.sum(Deal.amount).label("total"))
        .where(Deal.stage.notin_(["Closed Won", "Closed Lost"]))
        .where(Deal.expected_close_date >= q_start)
        .where(Deal.expected_close_date <= q_end)
        .group_by(Deal.rep_id)
    )).all()
    pipe_map: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in pipe_rows}

    # All open pipeline (for display in gap card)
    all_pipe_rows = (await db.execute(
        select(Deal.rep_id, func.sum(Deal.amount).label("total"))
        .where(Deal.stage.notin_(["Closed Won", "Closed Lost"]))
        .group_by(Deal.rep_id)
    )).all()
    all_pipe_map: dict[str, float] = {str(r.rep_id): float(r.total or 0) for r in all_pipe_rows}

    # Win rate YTD (last 12 months) per rep
    won_rows = (await db.execute(
        select(Deal.rep_id, func.count().label("cnt"))
        .where(Deal.stage == "Closed Won")
        .group_by(Deal.rep_id)
    )).all()
    lost_rows = (await db.execute(
        select(Deal.rep_id, func.count().label("cnt"))
        .where(Deal.stage == "Closed Lost")
        .group_by(Deal.rep_id)
    )).all()
    won_map: dict[str, int] = {str(r.rep_id): int(r.cnt) for r in won_rows}
    lost_map: dict[str, int] = {str(r.rep_id): int(r.cnt) for r in lost_rows}

    # Avg closed-won deal size per rep
    avg_deal_rows = (await db.execute(
        select(Deal.rep_id, func.avg(Deal.amount).label("avg"))
        .where(Deal.stage == "Closed Won")
        .group_by(Deal.rep_id)
    )).all()
    avg_deal_map: dict[str, float] = {str(r.rep_id): float(r.avg or 0) for r in avg_deal_rows}

    # Activities this month per rep
    act_month_rows = (await db.execute(
        select(Activity.deal_id, func.count().label("cnt"))
        .where(Activity.activity_date >= _date(today.year, today.month, 1))
        .group_by(Activity.deal_id)
    )).all()
    # Map deal → rep
    deal_rep_rows = (await db.execute(select(Deal.id, Deal.rep_id))).all()
    deal_rep_map: dict[str, str] = {str(r.id): str(r.rep_id) for r in deal_rep_rows}
    act_mtd: dict[str, int] = {}
    for ar in act_month_rows:
        rid = deal_rep_map.get(str(ar.deal_id))
        if rid:
            act_mtd[rid] = act_mtd.get(rid, 0) + int(ar.cnt)

    # Top open deals per rep (for focus list)
    open_deal_rows = (await db.execute(
        select(Deal.id, Deal.rep_id, Deal.name, Deal.amount, Deal.close_probability,
               Deal.stage, Deal.expected_close_date)
        .where(Deal.stage.notin_(["Closed Won", "Closed Lost"]))
        .order_by(Deal.amount.desc())
    )).all()

    rep_deals: dict[str, list[dict]] = {}
    for d in open_deal_rows:
        rid = str(d.rep_id)
        rep_deals.setdefault(rid, []).append({
            "deal_id": str(d.id),
            "name": d.name or "Unnamed",
            "amount": float(d.amount or 0),
            "close_probability": float(d.close_probability or 50),
            "stage": d.stage or "Prospecting",
            "expected_close_date": str(d.expected_close_date) if d.expected_close_date else None,
            "weighted_value": float(d.amount or 0) * float(d.close_probability or 50) / 100.0,
        })

    # ── Build per-rep signals + enriched outputs ───────────────────────────
    rep_signals: list[dict] = []
    rep_meta: dict[str, dict] = {}

    for rep in reps:
        rid = str(rep.id)
        quota = max(quota_map.get(rid, 0.0), 1.0)
        quota_prev = max(quota_prev_map.get(rid, 0.0), 1.0)
        rev_qtd_val = rev_qtd.get(rid, 0.0)
        rev_prev_val = rev_prev.get(rid, 0.0)
        pipe = pipe_map.get(rid, 0.0)
        won = won_map.get(rid, 0)
        lost = lost_map.get(rid, 0)
        win_rate = won / (won + lost) if (won + lost) > 0 else 0.25
        avg_deal = avg_deal_map.get(rid, 0.0)
        acts_mtd = float(act_mtd.get(rid, 0))
        attain_prev = rev_prev_val / quota_prev

        # Current quarter run-rate attainment (pace)
        paced_attainment = (rev_qtd_val / quota) * (days_in_quarter / max(days_into_quarter, 1))

        signal = {
            "rep_id":                   rid,
            "pipeline_coverage":        pipe / quota,         # Q2-dated pipe / quota
            "win_rate_ytd":             win_rate,
            "activities_mtd":           acts_mtd,
            "deals_created_mtd":        float(len(rep_deals.get(rid, []))),
            "avg_deal_size":            avg_deal,
            "days_into_quarter":        float(days_into_quarter),
            "quota":                    quota,
            "attainment_prior_quarter": attain_prev,
            "qtd_attainment":           rev_qtd_val / quota,  # fraction already achieved
            "ramp_factor":              1.0,
        }
        rep_signals.append(signal)

        # Enrichment for response
        all_pipe = all_pipe_map.get(rid, pipe)          # full pipeline (for display)
        expected_from_pipe = all_pipe * win_rate
        quota_gap = max(0.0, quota - rev_qtd_val)
        pipe_needed = max(0.0, quota_gap - expected_from_pipe)

        # Momentum: compare current pace vs prior quarter attainment
        if attain_prev > 0.05:
            momentum_delta = paced_attainment - attain_prev
            if momentum_delta >= 0.10:
                momentum = "accelerating"
            elif momentum_delta >= 0.02:
                momentum = "steady_up"
            elif momentum_delta >= -0.05:
                momentum = "stable"
            elif momentum_delta >= -0.15:
                momentum = "slowing"
            else:
                momentum = "at_risk"
        else:
            momentum = "new_rep"

        # Top 3 focus deals sorted by weighted value
        sorted_deals = sorted(rep_deals.get(rid, []), key=lambda d: d["weighted_value"], reverse=True)[:3]

        rep_meta[rid] = {
            "rep_name":          rep.name,
            "quota":             round(quota, 2),
            "revenue_qtd":       round(rev_qtd_val, 2),
            "attainment_qtd_pct": round(rev_qtd_val / quota * 100, 1),
            "paced_attainment_pct": round(paced_attainment * 100, 1),
            "quota_gap":         round(quota_gap, 2),
            "expected_from_pipe": round(expected_from_pipe, 2),
            "pipe_needed":       round(pipe_needed, 2),
            "open_pipeline":     round(all_pipe, 2),
            "win_rate_pct":      round(win_rate * 100, 1),
            "attainment_prev_q_pct": round(attain_prev * 100, 1),
            "momentum":          momentum,
            "days_into_quarter": days_into_quarter,
            "days_in_quarter":   days_in_quarter,
            "top_focus_deals":   sorted_deals,
        }

    # ── Run attainment forecaster ──────────────────────────────────────────
    result = forecast_rep_attainment(rep_signals)

    # ── Merge signals back into forecast rows ──────────────────────────────
    enriched_reps: list[dict] = []
    all_base_attainments = []

    for fc in result.get("forecasts", []):
        rid = str(fc.get("rep_id", ""))
        meta = rep_meta.get(rid, {})
        base_pct = round(float(fc.get("base", 0.0)) * 100, 1)
        commit_pct = round(float(fc.get("commit", 0.0)) * 100, 1)
        upside_pct = round(float(fc.get("upside", 0.0)) * 100, 1)
        all_base_attainments.append(base_pct)

        # Motivational label based on trajectory
        momentum = meta.get("momentum", "stable")
        if base_pct >= 100:
            motivation = "on_track"
            motivation_msg = "On pace to hit quota. Push for accelerator — every dollar above 100% earns more."
        elif base_pct >= 85:
            motivation = "close"
            motivation_msg = f"Projected {base_pct}% attainment. Close your top deal and you're there."
        elif base_pct >= 70 and momentum in ("accelerating", "steady_up"):
            motivation = "building"
            motivation_msg = f"Momentum is {momentum.replace('_', ' ')}. Keep the pace and you can close the gap."
        elif base_pct >= 50:
            motivation = "needs_focus"
            motivation_msg = "Pipeline coverage is the lever. Add 1–2 deals to expected-close this quarter."
        else:
            motivation = "at_risk"
            motivation_msg = "Early-stage risk. Prioritise your highest-probability deals and increase activity cadence."

        enriched_reps.append({
            "rep_id":          rid,
            "rep_name":        meta.get("rep_name", rid),
            "quota":           meta.get("quota"),
            "revenue_qtd":     meta.get("revenue_qtd"),
            "attainment_qtd_pct": meta.get("attainment_qtd_pct"),
            "paced_attainment_pct": meta.get("paced_attainment_pct"),
            "forecast": {
                "commit_pct":  commit_pct,
                "base_pct":    base_pct,
                "upside_pct":  upside_pct,
                "model_info":  result.get("model_info", "heuristic"),
            },
            "pipeline_gap": {
                "quota_gap":          meta.get("quota_gap"),
                "expected_from_pipe": meta.get("expected_from_pipe"),
                "pipe_needed":        meta.get("pipe_needed"),
                "open_pipeline":      meta.get("open_pipeline"),
                "win_rate_pct":       meta.get("win_rate_pct"),
            },
            "momentum":         meta.get("momentum"),
            "attainment_prev_q_pct": meta.get("attainment_prev_q_pct"),
            "top_focus_deals":  meta.get("top_focus_deals", []),
            "motivation_label": motivation,
            "motivation_msg":   motivation_msg,
            "risk_flag":        fc.get("risk_flag", False),
            "days_into_quarter": meta.get("days_into_quarter"),
            "days_in_quarter":   meta.get("days_in_quarter"),
        })

    # Sort by base forecast descending (top performers first)
    enriched_reps.sort(key=lambda r: r["forecast"]["base_pct"], reverse=True)

    # Add peer rank
    for i, r in enumerate(enriched_reps, start=1):
        r["peer_rank"] = i
        r["peer_total"] = len(enriched_reps)

    # Team summary
    if all_base_attainments:
        import statistics as _stats
        team_avg = round(_stats.mean(all_base_attainments), 1)
        on_track_count = sum(1 for x in all_base_attainments if x >= 100)
        at_risk_count = sum(1 for x in all_base_attainments if x < 70)
    else:
        team_avg = 0.0
        on_track_count = 0
        at_risk_count = 0

    return {
        "quarter": f"{q_start.year}-Q{(q_start.month - 1) // 3 + 1}",
        "quarter_window": {"start": str(q_start), "end": str(q_end)},
        "days_into_quarter": days_into_quarter,
        "days_in_quarter": days_in_quarter,
        "team_summary": {
            "avg_base_attainment_pct": team_avg,
            "on_track_count": on_track_count,
            "at_risk_count": at_risk_count,
            "total_reps": len(enriched_reps),
        },
        "reps": enriched_reps,
        "model_info": result.get("model_info", "heuristic"),
        "warnings": result.get("warnings", []),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ── C6: Territory forecast ────────────────────────────────────────────────

@router.get("/forecast/territory")
async def territory_forecast(
    horizon: int = 6,
    db: AsyncSession = Depends(get_db),
):
    """
    Hierarchical territory revenue forecast with MinT reconciliation.
    Groups revenue by rep (as proxy for territory) and forecasts.
    """
    from backend.ml.territory_forecaster import forecast_territories

    rows = (await db.execute(
        select(Revenue.rep_id, Revenue.period, func.sum(Revenue.amount).label("total"))
        .group_by(Revenue.rep_id, Revenue.period)
        .order_by(Revenue.rep_id, Revenue.period)
    )).all()

    if not rows:
        return {"error": "No revenue data.", "forecasts": {}, "warnings": []}

    # Build territory history {rep_id: [monthly values sorted by period]}
    from collections import defaultdict
    territory_periods: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        territory_periods[str(r.rep_id)][r.period] = float(r.total or 0.0)

    # Get all periods sorted
    all_periods = sorted({p for pd_ in territory_periods.values() for p in pd_})
    territory_history: dict[str, list[float]] = {}
    for terr_id, period_vals in territory_periods.items():
        territory_history[terr_id] = [period_vals.get(p, 0.0) for p in all_periods]

    result = forecast_territories(
        territory_history=territory_history,
        horizon=horizon,
        history_periods=all_periods,
    )
    result["generated_at"] = datetime.now(timezone.utc).isoformat()
    return result

