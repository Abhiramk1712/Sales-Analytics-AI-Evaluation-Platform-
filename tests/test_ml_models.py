"""
tests/test_ml_models.py
=======================
Unit tests for all three ML models.
Run: pytest tests/ -v
"""
import pytest
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Revenue Forecasting ───────────────────────────────────────────────────
class TestRevenueForecast:
    def _make_series(self, n=24) -> dict:
        """Synthetic monthly revenue with trend + seasonality."""
        base = 100_000
        months = pd.date_range("2023-01", periods=n, freq="MS")
        vals = [base + i * 1500 + np.random.normal(0, 5000) + 10000 * np.sin(i * np.pi / 6)
                for i in range(n)]
        return {m.strftime("%Y-%m"): round(v) for m, v in zip(months, vals)}

    def test_forecast_returns_correct_horizon(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(), horizon=6)
        assert len(result["forecast_periods"])  == 6
        assert len(result["forecast_values"])   == 6
        assert len(result["lower_ci"])          == 6
        assert len(result["upper_ci"])          == 6

    def test_confidence_intervals_ordered(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(), horizon=6)
        for lo, fc, hi in zip(result["lower_ci"], result["forecast_values"], result["upper_ci"]):
            assert lo <= fc <= hi, f"CI order violated: {lo} <= {fc} <= {hi}"

    def test_forecast_values_positive(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(), horizon=6)
        assert all(v > 0 for v in result["forecast_values"])

    def test_ensemble_weights_sum_to_one(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(), horizon=6)
        total = sum(result["ensemble_weights"].values())
        assert abs(total - 1.0) < 1e-6, f"Weights sum to {total}, expected 1.0"

    def test_metrics_present(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(), horizon=6)
        assert "MAE"  in result["model_metrics"]
        assert "RMSE" in result["model_metrics"]
        assert "MAPE" in result["model_metrics"]

    def test_short_history_uses_baseline_fallback(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast({"2024-01": 100_000, "2024-02": 110_000}, horizon=3)
        assert result["metadata"]["forecast_mode"] == "baseline"
        assert result["warnings"]

    def test_period_labels_are_future(self):
        from backend.ml.forecasting import run_revenue_forecast
        series = self._make_series(24)
        last_period = sorted(series.keys())[-1]
        result = run_revenue_forecast(series, horizon=3)
        assert result["forecast_periods"][0] > last_period

    # ── Coverage pass ahead of the forecasting-stack consolidation ────────
    #
    # run_revenue_forecast has 3 real branches by history length (<6 baseline,
    # 6-23 trend, >=24 full ensemble) — only the baseline one had a dedicated
    # test. The shape-contract test below exists specifically because of what
    # investigating the consolidation found: GET /payout/forecast read
    # forecast_result.forecast — a real attribute, but on RevenueForecastModel
    # .predict()'s *internal* ForecastResult (defined in this same module),
    # not on what run_revenue_forecast actually returns to callers, which is
    # a dict keyed "forecast_values". Two same-shaped-sounding but different
    # objects in one call chain is exactly how that confusion happened; this
    # pins the one callers actually see.

    def test_full_ensemble_output_has_exactly_the_documented_keys(self):
        from backend.ml.forecasting import run_revenue_forecast
        result = run_revenue_forecast(self._make_series(30), horizon=6)
        assert set(result.keys()) == {
            "historical", "forecast_periods", "forecast_values", "lower_ci",
            "upper_ci", "commit_lane", "best_case_lane", "ensemble_weights",
            "model_metrics", "model_info", "model_used", "metadata", "warnings",
        }
        assert result["metadata"]["forecast_mode"] == "model"
        # The exact mistake found in GET /payout/forecast: this dict has no
        # top-level "forecast" key, only "forecast_values".
        assert "forecast" not in result

    def test_trend_branch_used_for_6_to_23_months(self):
        """The middle branch (linear trend extrapolation) had no dedicated
        test at all — every existing test used either <6 or >=24 months."""
        from backend.ml.forecasting import run_revenue_forecast
        series = self._make_series(12)
        result = run_revenue_forecast(series, horizon=3)
        assert result["metadata"]["forecast_mode"] == "trend"
        assert result["model_used"] == "linear_trend"
        assert "slope_per_month" in result["model_metrics"]

    def test_short_history_shape_matches_full_ensemble_shape(self):
        """The baseline (<6mo) and full-ensemble (>=24mo) branches are
        independently-written code paths returning what's supposed to be the
        same dict shape — a caller shouldn't have to special-case which one
        it got. Confirms they actually match key-for-key."""
        from backend.ml.forecasting import run_revenue_forecast
        short_result = run_revenue_forecast({"2024-01": 100_000, "2024-02": 110_000}, horizon=3)
        full_result = run_revenue_forecast(self._make_series(30), horizon=3)
        assert set(short_result.keys()) == set(full_result.keys())

    def test_gap_in_monthly_history_is_forward_filled_with_a_warning(self):
        """A missing month in real revenue data is a realistic case (a
        company that didn't log anything one month) — confirms it's handled
        by forward-fill rather than silently skewing the series or crashing.

        The gap-detection code only runs in the >= 24-month (full ensemble)
        branch — dropping a month from a 24-month series first drops
        history_len to 23, which reroutes to the untested-for-this trend
        branch instead. Starting from 25 months keeps history_len at 24 with
        a real gap in the calendar range."""
        from backend.ml.forecasting import run_revenue_forecast
        series = self._make_series(25)
        keys = sorted(series.keys())
        gapped = {k: v for k, v in series.items() if k != keys[12]}  # drop one middle month
        assert len(gapped) == 24
        result = run_revenue_forecast(gapped, horizon=3)
        assert any("DATA GAP" in w for w in result["warnings"])
        assert len(result["forecast_values"]) == 3

    def test_zero_history_raises_a_clear_error(self):
        from backend.ml.forecasting import run_revenue_forecast
        with pytest.raises(ValueError, match="No historical revenue data"):
            run_revenue_forecast({}, horizon=3)

    def test_sarimax_fit_failure_falls_back_to_ridge_only_ensemble(self, monkeypatch):
        """RevenueForecastModel.fit() catches a SARIMAX fit exception and
        proceeds Ridge-only (sarimax_rmse = inf, w_sarimax excluded from the
        ensemble) rather than the whole forecast failing. Proven, not just
        read: force the fit to raise and confirm the ensemble still produces
        a real result with SARIMAX weighted out."""
        import statsmodels.tsa.statespace.sarimax as sarimax_module
        from backend.ml.forecasting import run_revenue_forecast

        class _BoomSARIMAX:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("synthetic SARIMAX fit failure for this test")

        # Forecasting-stack consolidation Phase 2: run_revenue_forecast's
        # >=24mo branch now delegates to forecasting_engine.RevenueForecastModel
        # (moved from this module in Phase 1), which does SARIMAX's import
        # locally inside fit()'s try block — so the patch target is
        # statsmodels' own class, not either forecasting module's namespace.
        monkeypatch.setattr(sarimax_module, "SARIMAX", _BoomSARIMAX)
        result = run_revenue_forecast(self._make_series(30), horizon=4)

        assert result["ensemble_weights"]["sarimax"] == 0.0
        assert "SARIMAX" not in result["model_info"]
        assert len(result["forecast_values"]) == 4
        assert abs(sum(result["ensemble_weights"].values()) - 1.0) < 1e-6


# ── Deal Scoring ──────────────────────────────────────────────────────────
class TestDealScoring:
    def _make_deals(self, n_closed=40, n_open=20) -> list[dict]:
        deals = []
        stages_closed = ["Closed Won", "Closed Lost"]
        stages_open   = ["Prospecting", "Qualification", "Proposal", "Negotiation"]
        for i in range(n_closed):
            stage = stages_closed[i % 2]
            deals.append({
                "id": str(i), "stage": stage,
                "amount": 50_000 + i * 3000,
                "close_probability": 100 if stage == "Closed Won" else 0,
                "industry": ["SaaS", "Healthcare", "Finance"][i % 3],
                "product":  ["Enterprise", "Pro", "Starter"][i % 3],
                "created_at": _utc_now_naive() - timedelta(days=60 + i),
                "activity_count": 3 + (i % 6),
                "days_since_last_activity": 5 + (i % 20),
            })
        for i in range(n_open):
            deals.append({
                "id": str(n_closed + i), "stage": stages_open[i % 4],
                "amount": 30_000 + i * 5000,
                "close_probability": 30 + i * 2,
                "industry": ["SaaS", "Retail"][i % 2],
                "product":  ["Enterprise", "Starter"][i % 2],
                "created_at": _utc_now_naive() - timedelta(days=20 + i),
                "activity_count": 2 + (i % 5),
                "days_since_last_activity": 3 + (i % 15),
            })
        return deals

    def test_scores_open_deals_only(self):
        from backend.ml.deal_scoring import run_deal_scoring
        result = run_deal_scoring(self._make_deals())
        stages = {d["deal_id"] for d in result["scored_deals"]}
        assert len(result["scored_deals"]) == 20

    def test_probability_bounds(self):
        from backend.ml.deal_scoring import run_deal_scoring
        result = run_deal_scoring(self._make_deals())
        for deal in result["scored_deals"]:
            assert 0.0 <= deal["win_probability"] <= 1.0

    def test_risk_levels_valid(self):
        from backend.ml.deal_scoring import run_deal_scoring
        result = run_deal_scoring(self._make_deals())
        valid = {"high", "medium", "low"}
        for deal in result["scored_deals"]:
            assert deal["risk_level"] in valid

    def test_roc_auc_reasonable(self):
        from backend.ml.deal_scoring import run_deal_scoring
        result = run_deal_scoring(self._make_deals(60, 20))
        assert result["cv_roc_auc"] >= 0.5, "ROC-AUC below random baseline"

    def test_explanation_present(self):
        from backend.ml.deal_scoring import run_deal_scoring
        result = run_deal_scoring(self._make_deals())
        for deal in result["scored_deals"]:
            assert isinstance(deal["explanation"], str) and len(deal["explanation"]) > 0


# ── Rep Clustering ────────────────────────────────────────────────────────
class TestRepClustering:
    def _make_reps(self, n=15) -> list[dict]:
        np.random.seed(42)
        reps = []
        for i in range(n):
            reps.append({
                "rep_id": str(i), "name": f"Rep {i}",
                "attainment_pct":      float(np.random.uniform(40, 140)),
                "win_rate":            float(np.random.uniform(20, 80)),
                "avg_deal_size":       float(np.random.uniform(20_000, 200_000)),
                "pipeline_coverage":   float(np.random.uniform(0.5, 4.0)),
                "avg_sales_cycle":     float(np.random.uniform(20, 90)),
                "activity_rate":       float(np.random.randint(10, 100)),
            })
        return reps

    def test_all_reps_assigned(self):
        from backend.ml.rep_clustering import run_rep_clustering
        reps = self._make_reps(15)
        result = run_rep_clustering(reps)
        assert len(result["clusters"]) == 15

    def test_optimal_k_in_range(self):
        from backend.ml.rep_clustering import run_rep_clustering
        result = run_rep_clustering(self._make_reps(15))
        assert 3 <= result["diagnostics"]["optimal_k"] <= 6

    def test_pca_coords_2d(self):
        from backend.ml.rep_clustering import run_rep_clustering
        result = run_rep_clustering(self._make_reps(15))
        for r in result["clusters"]:
            assert "pca_x" in r and "pca_y" in r

    def test_persona_labels_non_empty(self):
        from backend.ml.rep_clustering import run_rep_clustering
        result = run_rep_clustering(self._make_reps(15))
        for r in result["clusters"]:
            assert len(r["persona"]) > 0

    def test_silhouette_scores_all_k(self):
        from backend.ml.rep_clustering import run_rep_clustering
        result = run_rep_clustering(self._make_reps(15))
        scores = result["diagnostics"]["silhouette_scores"]
        for k in range(3, 7):
            assert str(k) in scores or k in scores
