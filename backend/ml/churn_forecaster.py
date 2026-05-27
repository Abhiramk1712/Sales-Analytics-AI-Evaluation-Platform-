"""
backend/ml/churn_forecaster.py
================================
C3 — Churn & Retention Forecaster

Kaplan-Meier survival curves + Weibull AFT for time-to-churn.
Inputs: account-level tenure/churn observations.
Output: cohort-level retention curves + 12-month churn risk per account.

Dependencies: lifelines (install: pip install lifelines)
"""
from __future__ import annotations

import warnings as _w
from typing import Any

import numpy as np

# ── Safe import ──────────────────────────────────────────────────────────
try:
    from lifelines import KaplanMeierFitter, WeibullAFTFitter
    import pandas as pd
    _LIFELINES_AVAILABLE = True
except ImportError:
    _LIFELINES_AVAILABLE = False


# ── Fallback (no lifelines) ──────────────────────────────────────────────

def _simple_retention(durations: list[float], event_observed: list[bool]) -> dict[str, Any]:
    """Simple empirical retention when lifelines is unavailable."""
    paired = sorted(zip(durations, event_observed))
    n = len(paired)
    churned = sum(1 for _, e in paired if e)
    churn_rate = churned / n if n else 0.0
    retention_at_12 = max(0.0, 1.0 - churn_rate)
    return {
        "median_tenure_months":    float(np.median(durations)) if durations else None,
        "overall_churn_rate":      round(churn_rate, 4),
        "retention_at_12m":        round(retention_at_12, 4),
        "survival_curve":          {},
        "method":                  "empirical_fallback",
        "warnings":                ["lifelines not installed; using simple empirical retention estimate."],
    }


# ── Kaplan-Meier + Weibull AFT ────────────────────────────────────────────

def fit_survival_model(
    account_records: list[dict[str, Any]],
    max_horizon_months: int = 24,
) -> dict[str, Any]:
    """
    Fit Kaplan-Meier and Weibull AFT models on account churn data.

    Parameters
    ----------
    account_records : list of dicts, each with:
        - account_id : str
        - tenure_months : float  (observed tenure)
        - churned : bool  (True = event/churn observed, False = censored/active)
        - cohort : str (optional, e.g. "2022-Q1" for cohort stratification)
        - covariates : dict (optional, numeric features for Weibull AFT)
    max_horizon_months : survival curve evaluation horizon

    Returns
    -------
    dict:
        median_tenure_months : KM median
        survival_curve       : {month: survival_prob}
        retention_at_12m     : KM survival at 12 months
        weibull_lambda_      : Weibull scale parameter
        weibull_rho_         : Weibull shape parameter
        cohort_curves        : {cohort: {month: survival_prob}}
        account_risk         : list[{account_id, churn_prob_12m, risk_tier}]
        method               : "kaplan_meier_weibull"
        warnings             : list[str]
    """
    warnings: list[str] = []

    if not account_records:
        return {"error": "No account records provided.", "warnings": []}

    durations       = [float(r.get("tenure_months") or 1.0) for r in account_records]
    event_observed  = [bool(r.get("churned", False)) for r in account_records]

    if not _LIFELINES_AVAILABLE:
        return _simple_retention(durations, event_observed)

    import pandas as pd

    df = pd.DataFrame({
        "duration":  durations,
        "event":     event_observed,
        "cohort":    [r.get("cohort", "all") for r in account_records],
        "account_id": [r.get("account_id", str(i)) for r in account_records],
    })

    # ── Kaplan-Meier ──────────────────────────────────────────────────────
    kmf = KaplanMeierFitter()
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        kmf.fit(df["duration"], event_observed=df["event"])

    timeline = np.arange(0, max_horizon_months + 1, 1)
    survival_at = kmf.survival_function_at_times(timeline)
    survival_curve = {int(t): round(float(s), 4) for t, s in survival_at.items()}
    retention_12 = float(kmf.survival_function_at_times([12]).iloc[0])
    median_km = kmf.median_survival_time_

    # ── Cohort breakdown ──────────────────────────────────────────────────
    cohort_curves: dict[str, dict[int, float]] = {}
    for cohort, grp in df.groupby("cohort"):
        if len(grp) < 5:
            continue
        kmf_c = KaplanMeierFitter()
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            kmf_c.fit(grp["duration"], event_observed=grp["event"])
        cohort_curves[str(cohort)] = {
            int(t): round(float(s), 4)
            for t, s in kmf_c.survival_function_at_times(timeline).items()
        }

    # ── Weibull AFT (scalar features only) ───────────────────────────────
    weibull_lambda = None
    weibull_rho    = None
    account_risk: list[dict] = []

    # Build covariate matrix if available
    cov_keys: list[str] = []
    if account_records[0].get("covariates"):
        try:
            cov_df = pd.DataFrame([
                {**{"duration": float(r.get("tenure_months") or 1.0),
                    "event":    bool(r.get("churned", False))},
                 **{k: float(v) for k, v in (r.get("covariates") or {}).items() if v is not None}}
                for r in account_records
            ]).dropna()
            cov_keys = [c for c in cov_df.columns if c not in ("duration", "event")]
        except Exception as exc:
            warnings.append(f"Covariate parsing failed: {exc}")
            cov_df = df[["duration", "event"]]
    else:
        cov_df = df[["duration", "event"]]

    if len(cov_df) >= 20:
        try:
            waf = WeibullAFTFitter(penalizer=0.1)
            with _w.catch_warnings():
                _w.simplefilter("ignore")
                waf.fit(cov_df, duration_col="duration", event_col="event")
            weibull_lambda = round(float(waf.lambda_), 4)
            weibull_rho    = round(float(waf.rho_), 4)

            # Per-account churn probability at 12m
            for acc_idx, rec in enumerate(account_records):
                aid = rec.get("account_id", str(acc_idx))
                row = cov_df.iloc[[acc_idx]] if acc_idx < len(cov_df) else None
                if row is not None and not row.empty:
                    try:
                        sf = waf.predict_survival_function(row, times=[12])
                        s12 = float(sf.iloc[0, 0])
                        churn_p = round(1.0 - s12, 4)
                    except Exception:
                        churn_p = round(1.0 - retention_12, 4)
                else:
                    churn_p = round(1.0 - retention_12, 4)
                tier = "high" if churn_p >= 0.4 else ("medium" if churn_p >= 0.2 else "low")
                account_risk.append({
                    "account_id":    aid,
                    "churn_prob_12m": churn_p,
                    "risk_tier":     tier,
                })
        except Exception as exc:
            warnings.append(f"Weibull AFT fit failed: {exc}; falling back to KM-based risk.")
            for rec in account_records:
                aid = rec.get("account_id", "?")
                churn_p = round(1.0 - retention_12, 4)
                tier = "high" if churn_p >= 0.4 else ("medium" if churn_p >= 0.2 else "low")
                account_risk.append({"account_id": aid, "churn_prob_12m": churn_p, "risk_tier": tier})
    else:
        warnings.append(f"Only {len(cov_df)} records; Weibull AFT skipped. Using KM median churn rate.")
        for rec in account_records:
            churn_p = round(1.0 - retention_12, 4)
            tier = "high" if churn_p >= 0.4 else ("medium" if churn_p >= 0.2 else "low")
            account_risk.append({
                "account_id":    rec.get("account_id", "?"),
                "churn_prob_12m": churn_p,
                "risk_tier":     tier,
            })

    return {
        "median_tenure_months": float(median_km) if not np.isnan(float(median_km)) else None,
        "retention_at_12m":     round(retention_12, 4),
        "overall_churn_rate":   round(1.0 - retention_12, 4),
        "survival_curve":       survival_curve,
        "cohort_curves":        cohort_curves,
        "weibull_lambda_":      weibull_lambda,
        "weibull_rho_":         weibull_rho,
        "account_risk":         account_risk,
        "n_accounts":           len(account_records),
        "n_churned":            int(sum(event_observed)),
        "method":               "kaplan_meier_weibull",
        "warnings":             warnings,
    }
