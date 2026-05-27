"""
backend/ml/forecasting_lab/service.py
=====================================
Main orchestration service for unified forecasting lab endpoints.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .backtesting import holdout_backtest
from .datasets import normalize_history
from .explainability import build_business_explanation
from .model_selection import build_leaderboard, choose_best_model, compute_data_profile
from .models import candidate_models, run_model_forecast
from .scenarios import apply_scenario, scenario_matrix


def _build_ensemble(
    model_results: dict[str, dict],
    leaderboard: list[dict],
    history_values: list[float],
    history_periods: list[str],
    target: str,
    horizon: int,
) -> tuple[dict, dict] | None:
    """
    Build a DA-weighted ensemble of the top-2 eligible (DA≥50%) non-ensemble models.

    Returns (forecast_payload, backtest_payload) or None if fewer than 2 eligible models.
    """
    import numpy as _np

    eligible = [
        row for row in leaderboard
        if row.get("model") != "ensemble"
        and row.get("selection_score") is not None
        and float(row.get("backtest", {}).get("directional_accuracy", 0.0)) >= 50.0
    ]
    if len(eligible) < 2:
        return None

    top2 = eligible[:2]
    weights = []
    for row in top2:
        da = float(row.get("backtest", {}).get("directional_accuracy", 50.0))
        weights.append(max(da, 1.0))
    w_sum = sum(weights)
    weights = [w / w_sum for w in weights]

    from .datasets import future_periods_from_history
    periods_out = future_periods_from_history(history_periods, horizon)

    # Weighted average of forecasts
    combined_values: list[float] = []
    m1_vals = model_results[top2[0]["model"]]["forecast"].get("values", [])
    m2_vals = model_results[top2[1]["model"]]["forecast"].get("values", [])
    n_out = min(len(m1_vals), len(m2_vals), horizon)
    for i in range(n_out):
        combined_values.append(round(weights[0] * m1_vals[i] + weights[1] * m2_vals[i], 2))

    lo = [round(max(0.0, v * 0.90), 2) for v in combined_values]
    hi = [round(v * 1.10, 2) for v in combined_values]

    forecast_payload = {
        "model": "ensemble",
        "strategy_used": f"ensemble({top2[0]['model']}+{top2[1]['model']})",
        "periods": periods_out[:n_out],
        "values": combined_values,
        "lower_bound": lo,
        "upper_bound": hi,
        "assumptions": [
            f"DA-weighted ensemble of {top2[0]['model']} (w={weights[0]:.2f}) "
            f"and {top2[1]['model']} (w={weights[1]:.2f}).",
            "Weights proportional to backtest directional accuracy.",
            "Uncorrelated errors between models reduce directional mistakes.",
        ],
        "warnings": [],
    }

    # Backtest the ensemble using the same walk-forward CV
    def _ensemble_fn(train_values: list[float], train_periods: list[str], bt_horizon: int) -> list[float]:
        r1 = run_model_forecast(top2[0]["model"], train_values, train_periods, target, bt_horizon)
        r2 = run_model_forecast(top2[1]["model"], train_values, train_periods, target, bt_horizon)
        v1 = r1.get("values", [0.0] * bt_horizon)
        v2 = r2.get("values", [0.0] * bt_horizon)
        n_bt = min(len(v1), len(v2), bt_horizon)
        return [round(weights[0] * v1[i] + weights[1] * v2[i], 2) for i in range(n_bt)]

    backtest_payload = holdout_backtest(
        history_values=history_values,
        history_periods=history_periods,
        forecast_fn=_ensemble_fn,
    )

    return forecast_payload, backtest_payload


def compare_models_for_target(
    history_values: list[float],
    history_periods: list[str],
    target: str,
    horizon: int = 6,
    include_lstm: bool = True,
) -> dict[str, Any]:
    """Run candidate models, evaluate holdout metrics, and build leaderboard."""
    values, periods = normalize_history(history_values, history_periods)
    if not values:
        return {
            "status": "no_data",
            "warnings": ["No historical values available for model comparison."],
            "target": target,
            "leaderboard": [],
        }

    # Compute once; drives all adaptive scoring downstream.
    data_profile = compute_data_profile(values, periods)

    model_results: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []

    # Growth deceleration signal: if recent 6-month growth rate is less than
    # 70% of the prior 6-month rate, naive drift may over-project. Warn RevOps.
    if len(values) >= 18:
        import numpy as _np
        seg = 6
        recent_growth = (float(_np.mean(values[-seg:])) / max(float(_np.mean(values[-seg*2:-seg])), 1e-9)) - 1.0
        prior_growth = (float(_np.mean(values[-seg*2:-seg])) / max(float(_np.mean(values[-seg*3:-seg*2])), 1e-9)) - 1.0
        if prior_growth > 0.02 and recent_growth < prior_growth * 0.70:
            decel_pct = round((1.0 - recent_growth / max(prior_growth, 1e-9)) * 100, 1)
            warnings.append(
                f"Growth deceleration detected: recent 6-month growth ({recent_growth:.1%}) is "
                f"{decel_pct}% below prior period ({prior_growth:.1%}). "
                "Base forecast may over-project — consider the conservative scenario for quota and comp planning."
            )

    for model_name in candidate_models(include_lstm=include_lstm):
        forecast_payload = run_model_forecast(
            model_name=model_name,
            history_values=values,
            history_periods=periods,
            target=target,
            horizon=horizon,
            include_lstm=include_lstm,
        )

        def _forecast_fn(train_values: list[float], train_periods: list[str], backtest_horizon: int) -> list[float]:
            result = run_model_forecast(
                model_name=model_name,
                history_values=train_values,
                history_periods=train_periods,
                target=target,
                horizon=backtest_horizon,
                include_lstm=include_lstm,
            )
            return result.get("values", [])

        backtest_payload = holdout_backtest(
            history_values=values,
            history_periods=periods,
            forecast_fn=_forecast_fn,
        )

        # If LSTM path already computed a backtest, use it when valid.
        lstm_backtest = forecast_payload.get("lstm_backtest")
        if isinstance(lstm_backtest, dict) and lstm_backtest.get("status") == "ok":
            backtest_payload = dict(lstm_backtest)

        model_results[model_name] = {
            "forecast": forecast_payload,
            "backtest": backtest_payload,
        }
        warnings.extend(list(forecast_payload.get("warnings", [])))

    leaderboard = build_leaderboard(model_results, data_profile=data_profile)

    # ── Build ensemble from top-2 DA-eligible models ───────────────────────
    ensemble_result = _build_ensemble(
        model_results=model_results,
        leaderboard=leaderboard,
        history_values=values,
        history_periods=periods,
        target=target,
        horizon=horizon,
    )
    if ensemble_result is not None:
        ens_forecast, ens_backtest = ensemble_result
        model_results["ensemble"] = {"forecast": ens_forecast, "backtest": ens_backtest}
        # Rebuild leaderboard with ensemble included
        leaderboard = build_leaderboard(model_results, data_profile=data_profile)

    best_model = choose_best_model(leaderboard, data_profile=data_profile)
    selected = model_results.get(best_model, model_results.get("naive", {}))
    selected_forecast = selected.get("forecast", {})
    selected_backtest = selected.get("backtest", {})

    values_base = selected_forecast.get("values", [])
    scenarios = scenario_matrix(values_base, target=target, history_values=values)

    explanation = build_business_explanation(
        target=target,
        selected_model=best_model,
        backtest_metrics=selected_backtest,
        history_months=len(values),
    )

    return {
        "status": "ok",
        "target": target,
        "history_months": len(values),
        "horizon_months": horizon,
        "selected_model": best_model,
        "selected_strategy": selected_forecast.get("strategy_used", best_model),
        "periods": selected_forecast.get("periods", []),
        "values": values_base,
        "lower_bound": selected_forecast.get("lower_bound", []),
        "upper_bound": selected_forecast.get("upper_bound", []),
        "backtest": selected_backtest,
        "leaderboard": leaderboard,
        "scenario_matrix": scenarios,
        "assumptions": list(selected_forecast.get("assumptions", [])),
        "business_explanation": explanation,
        "data_profile": data_profile,
        "warnings": sorted(set(warnings + list(selected_backtest.get("warnings", [])))),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def run_forecast_for_target(
    history_values: list[float],
    history_periods: list[str],
    target: str,
    horizon: int = 6,
    scenario: str = "base",
    include_lstm: bool = True,
) -> dict[str, Any]:
    """Run end-to-end forecast for target and apply requested scenario."""
    comparison = compare_models_for_target(
        history_values=history_values,
        history_periods=history_periods,
        target=target,
        horizon=horizon,
        include_lstm=include_lstm,
    )
    if comparison.get("status") != "ok":
        return comparison

    scenario_key = scenario if scenario in {"base", "optimistic", "conservative"} else "base"

    scenario_values = comparison.get("scenario_matrix", {}).get(scenario_key)
    if not scenario_values:
        scenario_values = apply_scenario(comparison.get("values", []), scenario=scenario_key, target=target)

    # Keep interval width proportional for scenario-adjusted output.
    lower = apply_scenario(comparison.get("lower_bound", []), scenario=scenario_key, target=target)
    upper = apply_scenario(comparison.get("upper_bound", []), scenario=scenario_key, target=target)

    payload = dict(comparison)
    payload.update(
        {
            "scenario": scenario_key,
            "values": scenario_values,
            "lower_bound": lower,
            "upper_bound": upper,
        }
    )
    return payload
