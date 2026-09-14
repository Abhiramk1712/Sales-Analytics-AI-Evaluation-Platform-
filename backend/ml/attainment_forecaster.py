"""
backend/ml/attainment_forecaster.py
=====================================
C5 — Rep Attainment Forecaster

Gradient Boosting Regressor with quantile outputs.
Predicts end-of-quarter quota attainment for each rep
from mid-quarter signals (pipeline, activities, deals created, etc.).

Output: point + p20 (commit) / p80 (upside) per rep.
"""
from __future__ import annotations

from typing import Any

import numpy as np

# Heuristic-fallback-only: how much pipeline coverage the un-fitted predict()
# path will credit toward attainment before treating the rest as redundancy
# rather than additional expected revenue. Matches this project's own
# "strong coverage" band (backend/statistics/pipeline_health.py), computed
# against the same grain -- open pipeline over one quarter's own quota.
PIPELINE_COVERAGE_CEILING = 3.0


# ── Feature extraction ────────────────────────────────────────────────────

def _extract_features(rep_signal: dict[str, Any]) -> list[float]:
    """
    Build a numeric feature vector from a rep's mid-quarter signals.

    Expected keys (all optional, default 0):
        pipeline_coverage, win_rate_ytd, activities_mtd, deals_created_mtd,
        avg_deal_size, days_into_quarter, quota, attainment_prior_quarter,
        ramp_factor
    """
    pipeline_coverage          = float(rep_signal.get("pipeline_coverage")          or 0.0)
    win_rate_ytd               = float(rep_signal.get("win_rate_ytd")               or 0.0)
    activities_mtd             = float(rep_signal.get("activities_mtd")             or 0.0)
    deals_created_mtd          = float(rep_signal.get("deals_created_mtd")          or 0.0)
    avg_deal_size              = float(rep_signal.get("avg_deal_size")              or 0.0)
    days_into_quarter          = float(rep_signal.get("days_into_quarter")          or 45.0)
    quota                      = float(rep_signal.get("quota")                      or 1.0)
    attainment_prior_quarter   = float(rep_signal.get("attainment_prior_quarter")   or 0.0)
    ramp_factor                = float(rep_signal.get("ramp_factor")               or 1.0)

    # Derived features
    days_remaining   = max(0.0, 91.0 - days_into_quarter)
    coverage_per_day = pipeline_coverage / max(days_remaining, 1.0)
    expected_won_pipeline = pipeline_coverage * win_rate_ytd

    return [
        pipeline_coverage,
        win_rate_ytd,
        activities_mtd,
        deals_created_mtd,
        avg_deal_size / max(quota, 1.0),   # normalised deal size
        days_into_quarter / 91.0,          # quarter progress (0–1)
        attainment_prior_quarter,
        ramp_factor,
        coverage_per_day,
        expected_won_pipeline / max(quota, 1.0),
    ]


# ── Training ──────────────────────────────────────────────────────────────

class AttainmentForecaster:
    """
    GBR-based quantile attainment forecaster.

    Parameters
    ----------
    quantiles : tuple of floats (commit, base, upside)
    """

    def __init__(self, quantiles: tuple[float, float, float] = (0.20, 0.50, 0.80)):
        self.quantiles = quantiles
        self._fitted = False

    def fit(self, training_records: list[dict[str, Any]]) -> "AttainmentForecaster":
        """
        Parameters
        ----------
        training_records : list of dicts, each with the signal keys listed in
                           _extract_features() plus ``actual_attainment`` (float, 0–2+).
        """
        try:
            from sklearn.ensemble import GradientBoostingRegressor
        except ImportError as exc:
            raise RuntimeError("scikit-learn is required for AttainmentForecaster") from exc

        if len(training_records) < 10:
            self._fitted = False
            self._warning = f"Only {len(training_records)} training records; model not fit."
            return self

        X = np.array([_extract_features(r) for r in training_records])
        y = np.array([float(r.get("actual_attainment", 0.0)) for r in training_records])

        self._models: dict[float, Any] = {}
        for q in self.quantiles:
            m = GradientBoostingRegressor(
                loss="quantile",
                alpha=q,
                n_estimators=200,
                max_depth=3,
                learning_rate=0.05,
                subsample=0.8,
                random_state=42,
            )
            m.fit(X, y)
            self._models[q] = m

        self._fitted = True
        self._warning = None
        return self

    def predict(self, rep_signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Parameters
        ----------
        rep_signals : list of dicts with signal keys + ``rep_id``

        Returns
        -------
        list of dicts: {rep_id, commit, base, upside, risk_flag}
        """
        if not self._fitted:
            warning_msg = getattr(self, "_warning", "Model not fitted.")
            # Heuristic: rev already booked + expected from Q-dated pipeline.
            #
            # The pipeline term is deliberately NOT `pc * wr` uncapped and
            # undiscounted by time. Confirmed live: a rep at 60.5% actual QTD
            # attainment with 12.7x pipeline coverage and a 100%-from-one-deal
            # win rate forecast a flat base=500% -- the same cap a rep who had
            # *already closed* 500% of quota would hit, indistinguishable from
            # someone genuinely on pace. Two real effects were missing:
            #   - pipeline_coverage beyond what's needed to comfortably hit
            #     quota doesn't linearly add more expected revenue -- past
            #     PIPELINE_COVERAGE_CEILING (this project's own "strong
            #     coverage" band, backend/statistics/pipeline_health.py) the
            #     excess is redundancy/optionality, not more attainment;
            #   - pipeline still open late in the quarter is increasingly
            #     likely to slip past it, so its contribution is discounted
            #     by the fraction of the quarter still remaining.
            # days_in_quarter isn't in the signal dict the router builds
            # (only days_into_quarter is); 91.0 mirrors _extract_features()'s
            # own hardcoded quarter-length assumption above, for consistency
            # within this file rather than introducing a second constant.
            results = []
            for sig in rep_signals:
                qtd  = float(sig.get("qtd_attainment") or 0.0)          # fraction already booked
                pc   = float(sig.get("pipeline_coverage") or 0.0)       # Q-dated pipe / quota
                wr   = float(sig.get("win_rate_ytd") or 0.25)
                days_into_quarter = float(sig.get("days_into_quarter") or 45.0)
                days_in_quarter = 91.0
                time_remaining_fraction = max(0.0, min(1.0, (days_in_quarter - days_into_quarter) / days_in_quarter))
                pipe_contribution = min(pc, PIPELINE_COVERAGE_CEILING) * wr * time_remaining_fraction
                attain = round(min(qtd + pipe_contribution, 5.0), 4)    # cap at 500%
                results.append({
                    "rep_id":   sig.get("rep_id", "?"),
                    "commit":   round(min(attain * 0.75, 5.0), 4),
                    "base":     attain,
                    "upside":   round(min(attain * 1.25, 5.0), 4),
                    "risk_flag": attain < 0.5,
                    "warning":  warning_msg,
                })
            return results

        X = np.array([_extract_features(sig) for sig in rep_signals])
        q_low, q_mid, q_high = self.quantiles
        preds_low  = self._models[q_low].predict(X)
        preds_mid  = self._models[q_mid].predict(X)
        preds_high = self._models[q_high].predict(X)

        results = []
        for i, sig in enumerate(rep_signals):
            commit = round(float(max(0.0, preds_low[i])),  4)
            base   = round(float(max(0.0, preds_mid[i])),  4)
            upside = round(float(max(0.0, preds_high[i])), 4)
            results.append({
                "rep_id":    sig.get("rep_id", str(i)),
                "commit":    commit,
                "base":      base,
                "upside":    upside,
                "risk_flag": base < 0.75,   # flagged if base attainment < 75%
            })
        return results


# ── Convenience function ─────────────────────────────────────────────────

def forecast_rep_attainment(
    rep_signals: list[dict[str, Any]],
    historical_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Forecast end-of-quarter attainment for a list of reps.

    Parameters
    ----------
    rep_signals        : current quarter mid-quarter signals per rep
    historical_records : past quarter records for training (optional)

    Returns
    -------
    dict: {forecasts, model_info, warnings}
    """
    forecaster = AttainmentForecaster()
    warnings: list[str] = []

    if historical_records and len(historical_records) >= 10:
        forecaster.fit(historical_records)
    else:
        forecaster._fitted = False
        forecaster._warning = (
            "No sufficient historical data; using heuristic pipeline×win_rate attainment."
        )
        warnings.append(forecaster._warning)

    forecasts = forecaster.predict(rep_signals)

    return {
        "forecasts":    forecasts,
        "model_info":  "GBR quantile attainment forecast (q20/q50/q80)" if forecaster._fitted else "heuristic",
        "n_reps":       len(rep_signals),
        "warnings":     warnings,
    }
