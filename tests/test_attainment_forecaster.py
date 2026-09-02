"""
tests/test_attainment_forecaster.py
====================================
backend/ml/attainment_forecaster.py had 0% coverage. It has two real code paths —
a GBR quantile model when there's enough training history, and a heuristic
fallback (pipeline_coverage × win_rate) when there isn't — reachable from
POST /ml/forecast/rep-attainment via backend/routers/forecasting.py. Both are
exercised directly here, no DB required.
"""
from __future__ import annotations

from backend.ml.attainment_forecaster import AttainmentForecaster, forecast_rep_attainment


# ── Heuristic fallback path (< 10 historical records) ────────────────────────

def test_no_history_uses_heuristic_and_says_so():
    result = forecast_rep_attainment(
        rep_signals=[{"rep_id": "r1", "qtd_attainment": 0.3, "pipeline_coverage": 0.5, "win_rate_ytd": 0.4}],
    )
    assert result["model_info"] == "heuristic"
    assert result["warnings"]
    assert "heuristic" in result["warnings"][0].lower()


def test_heuristic_forecast_matches_documented_formula():
    # attain = min(qtd + pipeline_coverage * win_rate, 5.0)
    sig = {"rep_id": "r1", "qtd_attainment": 0.30, "pipeline_coverage": 0.50, "win_rate_ytd": 0.40}
    result = forecast_rep_attainment(rep_signals=[sig])
    forecast = result["forecasts"][0]

    expected_base = round(min(0.30 + 0.50 * 0.40, 5.0), 4)
    assert forecast["base"] == expected_base
    assert forecast["commit"] == round(expected_base * 0.75, 4)
    assert forecast["upside"] == round(expected_base * 1.25, 4)


def test_heuristic_caps_attainment_at_500_percent():
    sig = {"rep_id": "r1", "qtd_attainment": 4.0, "pipeline_coverage": 10.0, "win_rate_ytd": 0.9}
    result = forecast_rep_attainment(rep_signals=[sig])
    assert result["forecasts"][0]["base"] == 5.0


def test_low_attainment_is_risk_flagged():
    sig = {"rep_id": "r1", "qtd_attainment": 0.1, "pipeline_coverage": 0.1, "win_rate_ytd": 0.1}
    result = forecast_rep_attainment(rep_signals=[sig])
    assert result["forecasts"][0]["base"] < 0.5
    assert result["forecasts"][0]["risk_flag"] is True


def test_fewer_than_ten_historical_records_still_falls_back_to_heuristic():
    history = [{"actual_attainment": 0.8} for _ in range(9)]  # one short of the threshold
    result = forecast_rep_attainment(
        rep_signals=[{"rep_id": "r1"}],
        historical_records=history,
    )
    assert result["model_info"] == "heuristic"


def test_multiple_reps_each_get_their_own_forecast():
    sigs = [
        {"rep_id": "r1", "qtd_attainment": 0.9, "pipeline_coverage": 0.2, "win_rate_ytd": 0.3},
        {"rep_id": "r2", "qtd_attainment": 0.1, "pipeline_coverage": 0.1, "win_rate_ytd": 0.1},
    ]
    result = forecast_rep_attainment(rep_signals=sigs)
    assert result["n_reps"] == 2
    ids = [f["rep_id"] for f in result["forecasts"]]
    assert ids == ["r1", "r2"]
    # r1 has much better signals than r2 — the heuristic should rank them accordingly.
    by_id = {f["rep_id"]: f for f in result["forecasts"]}
    assert by_id["r1"]["base"] > by_id["r2"]["base"]


# ── Fitted GBR path (>= 10 historical records) ────────────────────────────────

def _synthetic_history(n=30):
    """Training records where attainment scales roughly with pipeline coverage."""
    records = []
    for i in range(n):
        coverage = 0.5 + (i % 10) * 0.1
        records.append({
            "pipeline_coverage": coverage,
            "win_rate_ytd": 0.3,
            "activities_mtd": 20,
            "deals_created_mtd": 3,
            "avg_deal_size": 10_000,
            "days_into_quarter": 45,
            "quota": 100_000,
            "attainment_prior_quarter": coverage * 0.8,
            "ramp_factor": 1.0,
            "actual_attainment": coverage * 0.9,
        })
    return records


def test_enough_history_fits_a_real_model_not_the_heuristic():
    result = forecast_rep_attainment(
        rep_signals=[{"rep_id": "r1", "pipeline_coverage": 0.8, "win_rate_ytd": 0.3, "quota": 100_000}],
        historical_records=_synthetic_history(),
    )
    assert result["model_info"] != "heuristic"
    assert "GBR" in result["model_info"]
    assert result["warnings"] == []


def test_fitted_model_predictions_are_never_negative():
    forecaster = AttainmentForecaster().fit(_synthetic_history())
    preds = forecaster.predict([{"rep_id": "r1", "pipeline_coverage": 0.0, "win_rate_ytd": 0.0}])
    assert preds[0]["commit"] >= 0.0
    assert preds[0]["base"] >= 0.0
    assert preds[0]["upside"] >= 0.0


def test_fitted_model_quantiles_are_returned_per_rep_with_risk_flag():
    forecaster = AttainmentForecaster().fit(_synthetic_history())
    preds = forecaster.predict([
        {"rep_id": "r1", "pipeline_coverage": 0.8, "win_rate_ytd": 0.3, "quota": 100_000},
    ])
    assert preds[0]["rep_id"] == "r1"
    assert set(preds[0].keys()) >= {"commit", "base", "upside", "risk_flag"}


def test_unfitted_forecaster_used_directly_falls_back_to_heuristic_predict():
    """Calling predict() without fit() first shouldn't raise — same fallback as forecast_rep_attainment."""
    forecaster = AttainmentForecaster()
    preds = forecaster.predict([{"rep_id": "r1", "qtd_attainment": 0.5, "pipeline_coverage": 0.5, "win_rate_ytd": 0.3}])
    assert preds[0]["rep_id"] == "r1"
    assert "warning" in preds[0]
