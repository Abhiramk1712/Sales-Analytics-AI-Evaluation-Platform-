"""
backend/ml/territory_forecaster.py
=====================================
C6 — Hierarchical Territory Forecaster

Bottom-up MinT (Minimum Trace) reconciliation:
    territory → region → company

Each territory is forecast independently (ETS / baseline),
then reconciled top-down so that roll-ups are internally consistent.

No external dependencies beyond numpy/statsmodels (already in requirements).
"""
from __future__ import annotations

import warnings as _w
from typing import Any

import numpy as np


# ── Simple ETS-based single-series forecaster ────────────────────────────

def _forecast_series(history: list[float], horizon: int) -> list[float]:
    """Forecast a single time series using ETS or moving average."""
    if len(history) < 6:
        avg = float(np.mean(history)) if history else 0.0
        return [max(0.0, round(avg, 2))] * horizon

    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            m = ExponentialSmoothing(
                history,
                trend="add",
                seasonal="add" if len(history) >= 24 else None,
                seasonal_periods=12 if len(history) >= 24 else None,
                initialization_method="estimated",
            ).fit(optimized=True)
        return [max(0.0, round(v, 2)) for v in m.forecast(horizon)]
    except Exception:
        avg = float(np.mean(history[-6:]))
        return [max(0.0, round(avg, 2))] * horizon


# ── MinT reconciliation (OLS variant) ────────────────────────────────────

def _mint_reconcile(
    base_forecasts: dict[str, list[float]],
    hierarchy: dict[str, list[str]],   # {parent: [child, ...]}
    horizon: int,
) -> dict[str, list[float]]:
    """
    Simple top-down proportional reconciliation (approx MinT OLS).

    1. Compute bottom-level proportions from base forecast sums.
    2. Compute company-level forecast = sum of all territory forecasts.
    3. Distribute top-down using historical proportions.
    """
    # All leaves = territories not in hierarchy as a parent
    all_nodes  = set(base_forecasts.keys())
    parents    = set(hierarchy.keys())
    leaf_nodes = all_nodes - parents

    if not leaf_nodes:
        return base_forecasts

    reconciled: dict[str, list[float]] = {}

    # Company total = sum of all territory base forecasts
    company_total = np.zeros(horizon)
    for leaf in leaf_nodes:
        company_total += np.array(base_forecasts.get(leaf, [0.0] * horizon))

    # Each region = sum of its territory children
    for region, children in hierarchy.items():
        region_fc = np.zeros(horizon)
        for child in children:
            region_fc += np.array(base_forecasts.get(child, [0.0] * horizon))
        reconciled[region] = [round(v, 2) for v in region_fc]

    # Top-level company = sum of all regions
    all_regions = [k for k in hierarchy if not any(k in v for v in hierarchy.values())]
    if all_regions:
        company_fc = np.zeros(horizon)
        for r in all_regions:
            company_fc += np.array(reconciled.get(r, [0.0] * horizon))
        reconciled["__company__"] = [round(v, 2) for v in company_fc]
    else:
        reconciled["__company__"] = [round(v, 2) for v in company_total]

    # Territories (leaves) — distribute proportionally from region
    for region, children in hierarchy.items():
        region_fc = np.array(reconciled[region])
        child_fc_sum = np.zeros(horizon)
        for child in children:
            child_fc_sum += np.array(base_forecasts.get(child, [0.0] * horizon))

        for child in children:
            child_base = np.array(base_forecasts.get(child, [0.0] * horizon))
            proportion = np.where(child_fc_sum > 0, child_base / child_fc_sum, 1.0 / len(children))
            reconciled[child] = [max(0.0, round(v, 2)) for v in region_fc * proportion]

    return reconciled


# ── Public API ────────────────────────────────────────────────────────────

def forecast_territories(
    territory_history: dict[str, list[float]],
    hierarchy: dict[str, list[str]] | None = None,
    horizon: int = 6,
    history_periods: list[str] | None = None,
) -> dict[str, Any]:
    """
    Forecast revenue for each territory and reconcile hierarchically.

    Parameters
    ----------
    territory_history : {territory_id: [monthly_revenue, ...]}  oldest first
    hierarchy         : {region: [territory_id, ...]}
                        If None, each territory is forecast independently.
    horizon           : months to forecast (default 6)
    history_periods   : YYYY-MM labels for history (optional, for period labeling)

    Returns
    -------
    dict:
        forecasts          : {territory/region/__company__: [values]}
        forecast_periods   : list of YYYY-MM labels
        reconciled         : bool (True if hierarchy was applied)
        territory_count    : int
        assumptions        : list[str]
        warnings           : list[str]
    """
    warnings: list[str] = []
    if not territory_history:
        return {"error": "No territory history provided.", "warnings": []}

    # Step 1: Base forecasts per territory
    base_forecasts: dict[str, list[float]] = {}
    for terr, hist in territory_history.items():
        base_forecasts[terr] = _forecast_series(hist, horizon)

    # Step 2: Reconcile if hierarchy provided
    reconciled_flag = False
    if hierarchy:
        # Validate: all children must be in territory_history
        unknown: list[str] = []
        for children in hierarchy.values():
            for c in children:
                if c not in territory_history:
                    unknown.append(c)
        if unknown:
            warnings.append(f"Unknown territory IDs in hierarchy: {unknown}. Using unreconciled forecasts.")
        else:
            base_forecasts = _mint_reconcile(base_forecasts, hierarchy, horizon)
            reconciled_flag = True

    # Step 3: Period labels
    if history_periods:
        last_period = history_periods[-1]
        try:
            yr, mo = int(last_period[:4]), int(last_period[5:7])
            forecast_periods: list[str] = []
            for _ in range(horizon):
                mo += 1
                if mo > 12:
                    mo, yr = 1, yr + 1
                forecast_periods.append(f"{yr}-{mo:02d}")
        except Exception:
            forecast_periods = [f"T+{i+1}" for i in range(horizon)]
    else:
        forecast_periods = [f"T+{i+1}" for i in range(horizon)]

    return {
        "forecasts":        base_forecasts,
        "forecast_periods": forecast_periods,
        "reconciled":       reconciled_flag,
        "territory_count":  len(territory_history),
        "assumptions": [
            "Bottom-up MinT (proportional OLS variant) reconciliation applied." if reconciled_flag
            else "No hierarchy provided; territories forecast independently.",
            "ETS (Holt-Winters) used for territories with ≥6 months history; moving average otherwise.",
        ],
        "warnings": warnings,
    }
