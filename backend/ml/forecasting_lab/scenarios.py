"""
backend/ml/forecasting_lab/scenarios.py
=======================================
Scenario utilities for optimistic/base/conservative forecasting.

Scenario bands are data-driven: they are computed from the company's actual
detrended residual volatility rather than a fixed ±8% multiplier.  This
ensures that high-volatility companies (e.g. fast-ramp InsureX at ±10% σ)
get appropriately wide bands while stable businesses get narrower ones.
"""
from __future__ import annotations

import numpy as np


# Fixed fallback multipliers used when no history is available.
_DEFAULT_SCENARIO_MULTIPLIERS = {
    "conservative": 0.92,
    "base": 1.00,
    "optimistic": 1.08,
}

# Per-target sigma multiplier for scenario width.
# RevOps planning convention: use 1.5-sigma for a ~86% coverage interval.
# Pipeline/payout are choppier so use slightly wider bands.
_SIGMA_MULTIPLIER: dict[str, float] = {
    "revenue": 1.5,
    "ARR": 1.3,
    "pipeline": 1.8,
    "commit": 1.6,
    "best_case": 1.8,
    "booking": 1.6,
    "payout": 1.7,
    "quota_attainment": 1.4,
}

# Hard bounds to prevent implausibly narrow or wide scenarios.
_BAND_MIN = 0.05   # never narrower than ±5%
_BAND_MAX = 0.35   # never wider than ±35%


def compute_scenario_band(
    history_values: list[float],
    target: str = "revenue",
) -> float:
    """
    Compute the scenario half-band from detrended residual volatility.

    Returns a fraction (e.g. 0.15 → ±15%) to use as the conservative/optimistic
    offset from the base forecast.
    """
    arr = np.asarray(history_values, dtype=float)
    n = len(arr)

    if n < 6:
        return _DEFAULT_SCENARIO_MULTIPLIERS["optimistic"] - 1.0

    # Use the most recent 24 months for volatility (recent regime matters more).
    window = arr[max(0, n - 24):]

    # Detrend to isolate residual noise from the structural trend.
    x = np.arange(len(window), dtype=float)
    coeffs = np.polyfit(x, window, 1)
    residuals = window - np.polyval(coeffs, x)
    sigma = float(np.std(residuals))

    # Normalise by the recent average level.
    mean_recent = float(np.mean(np.abs(window[-6:]))) if n >= 6 else float(np.mean(np.abs(window)))
    if mean_recent < 1e-6:
        return _DEFAULT_SCENARIO_MULTIPLIERS["optimistic"] - 1.0

    sigma_pct = sigma / mean_recent
    multiplier = _SIGMA_MULTIPLIER.get(target, 1.5)
    band = float(np.clip(sigma_pct * multiplier, _BAND_MIN, _BAND_MAX))
    return round(band, 4)


def scenario_multipliers(
    history_values: list[float] | None = None,
    target: str = "revenue",
) -> dict[str, float]:
    """
    Return scenario multipliers calibrated to this company's volatility.
    Falls back to fixed defaults when no history is provided.
    """
    if not history_values:
        return dict(_DEFAULT_SCENARIO_MULTIPLIERS)

    band = compute_scenario_band(history_values, target=target)
    return {
        "conservative": round(1.0 - band, 4),
        "base": 1.0,
        "optimistic": round(1.0 + band, 4),
    }


def apply_scenario(
    values: list[float],
    scenario: str = "base",
    target: str = "revenue",
    history_values: list[float] | None = None,
) -> list[float]:
    """Apply a data-driven scenario multiplier."""
    mults = scenario_multipliers(history_values, target=target)
    scenario_key = scenario if scenario in mults else "base"
    mult = mults[scenario_key]

    adjusted = [float(v) * mult for v in values]

    if target == "quota_attainment":
        adjusted = [min(max(v, 0.0), 300.0) for v in adjusted]
    else:
        adjusted = [max(v, 0.0) for v in adjusted]

    return [round(float(v), 2) for v in adjusted]


def scenario_matrix(
    values: list[float],
    target: str = "revenue",
    history_values: list[float] | None = None,
) -> dict[str, list[float]]:
    mults = scenario_multipliers(history_values, target=target)
    return {
        "conservative": apply_scenario(values, "conservative", target, history_values),
        "base": apply_scenario(values, "base", target, history_values),
        "optimistic": apply_scenario(values, "optimistic", target, history_values),
        "_band_pct": round((mults["optimistic"] - 1.0) * 100, 1),  # surfaced in API response
    }

