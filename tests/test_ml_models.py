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
