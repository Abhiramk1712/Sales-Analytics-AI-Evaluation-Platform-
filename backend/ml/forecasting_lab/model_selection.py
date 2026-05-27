"""
backend/ml/forecasting_lab/model_selection.py
=============================================
Ranking and model selection logic for forecasting lab candidates.

Model weights and eligibility thresholds are adaptive: they are derived from
the statistical profile of the company's data (growth rate, seasonality, trend
stability, volatility) so that the best-fit model changes with the data rather
than being hard-coded.
"""
from __future__ import annotations

from typing import Any

import numpy as np


# ── Data profiling ────────────────────────────────────────────────────────────

def compute_data_profile(values: list[float], periods: list[str]) -> dict[str, Any]:
    """
    Compute statistical characteristics of the time series to drive adaptive
    model selection weights.

    Returns
    -------
    n_months        : number of observations
    growth_rate     : annualised CAGR (e.g. 0.25 = 25 % p.a.)
    volatility      : coefficient of variation of detrended residuals
    seasonality_score : fraction of total variance explained by month-of-year means [0, 1]
    trend_r2        : R² of a simple linear trend fit [0, 1]
    regime          : "high_growth" | "stable" | "declining"
    """
    arr = np.asarray(values, dtype=float)
    n = len(arr)

    # ── Growth rate: annualised CAGR from early → late quintile ─────────────
    growth_rate = 0.0
    if n >= 12:
        seg = max(3, n // 5)
        early_mean = float(np.mean(arr[:seg]))
        late_mean = float(np.mean(arr[-seg:]))
        if early_mean > 1e-6:
            years = max((n - seg) / 12.0, 0.5)
            growth_rate = float((late_mean / early_mean) ** (1.0 / years) - 1.0)
    growth_rate = float(np.clip(growth_rate, -1.0, 10.0))

    # ── Trend stability: R² of linear fit ───────────────────────────────────
    trend_r2 = 0.0
    if n >= 6:
        x = np.arange(n, dtype=float)
        coeffs = np.polyfit(x, arr, 1)
        trend_line = np.polyval(coeffs, x)
        ss_res = float(np.sum((arr - trend_line) ** 2))
        ss_tot = float(np.sum((arr - float(np.mean(arr))) ** 2))
        trend_r2 = float(np.clip(1.0 - ss_res / (ss_tot + 1e-9), 0.0, 1.0))

    # ── Volatility: CV of detrended residuals ────────────────────────────────
    volatility = 0.0
    if n >= 6:
        x = np.arange(n, dtype=float)
        coeffs = np.polyfit(x, arr, 1)
        residuals = arr - np.polyval(coeffs, x)
        mean_val = float(np.mean(np.abs(arr)))
        if mean_val > 1e-6:
            volatility = float(np.std(residuals) / mean_val)
    volatility = float(np.clip(volatility, 0.0, 2.0))

    # ── Seasonality: fraction of variance from month-of-year means ───────────
    seasonality_score = 0.0
    if n >= 24 and len(periods) >= n:
        monthly_buckets: dict[int, list[float]] = {m: [] for m in range(1, 13)}
        for i, p in enumerate(periods[:n]):
            try:
                month = int(str(p)[5:7])
                val = float(arr[i])
                if np.isfinite(val) and val >= 0.0:
                    monthly_buckets[month].append(val)
            except Exception:
                continue
        month_means = [float(np.mean(v)) for v in monthly_buckets.values() if len(v) >= 2]
        if len(month_means) >= 6:
            total_var = float(np.var(arr))
            if total_var > 1e-9:
                seasonal_var = float(np.var(month_means))
                seasonality_score = float(np.clip(seasonal_var / total_var, 0.0, 1.0))

    # ── Regime label ────────────────────────────────────────────────────────
    if growth_rate >= 0.15:
        regime = "high_growth"
    elif growth_rate <= -0.05:
        regime = "declining"
    else:
        regime = "stable"

    return {
        "n_months": n,
        "growth_rate": round(growth_rate, 4),
        "volatility": round(volatility, 4),
        "seasonality_score": round(seasonality_score, 4),
        "trend_r2": round(trend_r2, 4),
        "regime": regime,
    }


# ── Adaptive scoring ──────────────────────────────────────────────────────────

def _selection_score(metrics: dict[str, Any], data_profile: dict[str, Any] | None = None) -> float:
    """
    Compute a composite selection score (lower = better).

    Weights adapt to the company's data profile:
    - High-growth series: penalise directional failure more heavily
    - High-volatility series: down-weight MAPE (noisy), up-weight bias
    - Seasonal series: ease sMAPE weight (seasonal models trade sMAPE for shape fit)
    """
    mape = float(metrics.get("mape", 1e6))
    smape = float(metrics.get("smape", 1e6))
    bias_pct = abs(float(metrics.get("bias_pct", 0.0)))
    directional_accuracy = float(metrics.get("directional_accuracy", 0.0))

    profile = data_profile or {}
    growth_rate = float(profile.get("growth_rate", 0.0))
    volatility = float(profile.get("volatility", 0.0))
    seasonality_score = float(profile.get("seasonality_score", 0.0))

    # ── Directional-accuracy penalty ─────────────────────────────────────────
    # High-growth companies: missing the direction is catastrophic for quota
    # planning — scale the DA multiplier with growth.  Range: [2.5, 5.5].
    growth_factor = float(np.clip(abs(growth_rate) / 0.40, 0.0, 1.0))
    da_penalty_strength = 2.5 + growth_factor * 3.0      # [2.5, 5.5]
    da_penalty = max(0.0, (50.0 - directional_accuracy) / 50.0)
    da_multiplier = 1.0 + da_penalty * da_penalty_strength

    # ── MAPE weight: reduce when series is noisy ─────────────────────────────
    # High residual volatility inflates MAPE; don't let it dominate selection.
    vol_factor = float(np.clip(volatility / 0.30, 0.0, 1.0))
    w_mape = 1.0 - vol_factor * 0.30          # [1.0, 0.7]

    # ── sMAPE weight: ease for seasonal series ────────────────────────────────
    seas_factor = float(np.clip(seasonality_score / 0.50, 0.0, 1.0))
    w_smape = 0.30 - seas_factor * 0.10       # [0.30, 0.20]

    # ── Bias weight: up-weight for high-growth/declining (systematic error hurts) ─
    growth_bias_factor = float(np.clip(abs(growth_rate) / 0.30, 0.0, 1.0))
    w_bias = 0.20 + growth_bias_factor * 0.15  # [0.20, 0.35]

    return round(mape * w_mape * da_multiplier + w_smape * smape + w_bias * bias_pct, 6)


def _da_tiebreak_bonus(directional_accuracy: float) -> float:
    """
    Return a small score reduction (bonus) for models with high DA.

    Applied only when DA > 65%: reduces score by up to 1.5 points so that
    when two models are within ~3% of each other's composite score, the
    more directionally accurate one wins. Does not override large MAPE gaps.
    """
    if directional_accuracy <= 65.0:
        return 0.0
    excess = (directional_accuracy - 65.0) / 35.0   # [0, 1] for DA in [65, 100]
    return float(np.clip(excess * 1.5, 0.0, 1.5))


def _model_suitability_bonus(model_name: str, data_profile: dict[str, Any]) -> float:
    """
    Return a score multiplier for how well the model fits the data profile.
    Values < 1.0 improve (lower) the score; > 1.0 penalise.
    """
    n = int(data_profile.get("n_months", 0))
    growth_rate = float(data_profile.get("growth_rate", 0.0))
    seasonality = float(data_profile.get("seasonality_score", 0.0))
    trend_r2 = float(data_profile.get("trend_r2", 0.0))
    volatility = float(data_profile.get("volatility", 0.0))

    adj = 1.0

    # ── Seasonal models shine when seasonality is strong ─────────────────────
    if model_name in ("ets", "sarimax", "seasonal_naive") and seasonality > 0.20:
        bonus = min(0.18, seasonality * 0.35)
        adj -= bonus

    # ── Trend/regression models shine when the series is linearly predictable ─
    if model_name == "ridge" and trend_r2 > 0.60 and abs(growth_rate) > 0.05:
        adj -= min(0.12, trend_r2 * 0.12)

    # ── SARIMAX / ridge need sufficient history ───────────────────────────────
    if model_name == "sarimax" and n < 36:
        adj += 0.25
    if model_name == "ridge" and n < 24:
        adj += 0.15

    # ── Simple models should yield to more capable ones on long, stable series ─
    if model_name in ("naive", "moving_average") and n >= 36 and trend_r2 > 0.70:
        adj += 0.08

    # ── Volatile data: simpler models overfit less ───────────────────────────
    if model_name in ("sarimax", "ets") and volatility > 0.25:
        adj += 0.10

    return float(np.clip(adj, 0.50, 1.60))


# ── Leaderboard ───────────────────────────────────────────────────────────────

def build_leaderboard(
    model_results: dict[str, dict[str, Any]],
    data_profile: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Build sorted leaderboard from model_results, applying data-profile-adaptive
    scoring so the best model for *this company's data* rises to the top.
    """
    profile = data_profile or {}
    rows: list[dict[str, Any]] = []

    for model_name, payload in model_results.items():
        metrics = payload.get("backtest", {})
        score = None
        if metrics.get("status") == "ok":
            base = _selection_score(metrics, profile)
            adj = _model_suitability_bonus(model_name, profile)
            da = float(metrics.get("directional_accuracy", 0.0))
            # Subtract DA bonus so directionally accurate models rank higher
            # when composite scores are similar.
            da_bonus = _da_tiebreak_bonus(da)
            score = round(max(0.0, base * adj - da_bonus), 6)

        rows.append(
            {
                "model": model_name,
                "selection_score": score,
                "backtest": metrics,
                "strategy_used": payload.get("forecast", {}).get("strategy_used", model_name),
                "warnings": list(payload.get("forecast", {}).get("warnings", [])) + list(metrics.get("warnings", [])),
            }
        )

    rows.sort(key=lambda r: (r["selection_score"] is None, r["selection_score"] if r["selection_score"] is not None else 1e12))
    for idx, row in enumerate(rows, start=1):
        row["rank"] = idx
    return rows


# ── Model selection ───────────────────────────────────────────────────────────

def choose_best_model(
    leaderboard: list[dict[str, Any]],
    data_profile: dict[str, Any] | None = None,
) -> str:
    """
    Pick the winning model, applying a data-driven minimum directional accuracy
    threshold that scales with growth rate.

    High-growth companies require stricter trend compliance; stable/flat data
    can tolerate a lower threshold because direction matters less.
    """
    if not leaderboard:
        return "naive"

    profile = data_profile or {}
    growth_rate = abs(float(profile.get("growth_rate", 0.0)))

    # Adaptive DA floor: scales from 20 % (flat/declining) to 50 % (rapid growth)
    if growth_rate >= 0.30:
        eligible_da_threshold = 50.0
    elif growth_rate >= 0.15:
        eligible_da_threshold = 40.0
    elif growth_rate >= 0.05:
        eligible_da_threshold = 30.0
    else:
        eligible_da_threshold = 20.0

    eligible = [
        row for row in leaderboard
        if row.get("selection_score") is not None
        and float(row.get("backtest", {}).get("directional_accuracy", 0.0)) >= eligible_da_threshold
    ]

    if eligible:
        # DA tiebreaker: if runner-up is within 5% of the top score AND has
        # higher DA, prefer it.  This catches cases where the scoring bonus
        # alone didn't fully resolve a close race.
        best_score = float(eligible[0]["selection_score"])
        tiebreak_pool = [
            row for row in eligible
            if float(row["selection_score"]) <= best_score * 1.05
        ]
        if len(tiebreak_pool) > 1:
            tiebreak_pool.sort(
                key=lambda r: float(r.get("backtest", {}).get("directional_accuracy", 0.0)),
                reverse=True,
            )
        return str(tiebreak_pool[0].get("model", "naive"))

    # If nothing clears the adaptive threshold, lower to an absolute minimum.
    fallback = [
        row for row in leaderboard
        if row.get("selection_score") is not None
        and float(row.get("backtest", {}).get("directional_accuracy", 0.0)) >= 20.0
    ]
    if fallback:
        return str(fallback[0].get("model", "naive"))

    # Final safety fallback: lowest-score candidate regardless of DA.
    for row in leaderboard:
        if row.get("selection_score") is not None:
            return str(row.get("model", "naive"))
    return str(leaderboard[0].get("model", "naive"))

