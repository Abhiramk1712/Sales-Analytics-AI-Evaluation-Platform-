"""
tests/test_pipeline_forecaster.py
==================================
backend/ml/pipeline_forecaster.py had 0% coverage — nothing proved
forecast_pipeline() ran, let alone that its stage probabilities, time decay, or
week-bucketing did what the docstring claims. It's a pure function (no DB, no
mocking needed), reachable from POST /ml/forecast/pipeline via
backend/routers/forecasting.py.
"""
from __future__ import annotations

from datetime import date, timedelta

from backend.ml.pipeline_forecaster import forecast_pipeline


def test_empty_pipeline_returns_zero_forecast():
    result = forecast_pipeline([])
    assert result["total_expected"] == 0
    assert result["deal_contributions"] == []
    assert len(result["weekly_forecast"]) == 13  # 90 // 7 + 1


def test_expected_value_is_amount_times_probability_for_a_deal_closing_today():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Commit", "amount": 100_000, "expected_close": today}]
    result = forecast_pipeline(deals, today=today)

    contribution = result["deal_contributions"][0]
    # Closing exactly today: decay == 1.0 (not overdue), so EV == amount * stage_prob.
    assert contribution["decay"] == 1.0
    assert contribution["expected_value"] == 100_000 * 0.85
    assert result["total_expected"] == 100_000 * 0.85


def test_overdue_deal_decays_toward_zero():
    today = date(2026, 6, 1)
    on_time = {"id": "on_time", "stage": "Commit", "amount": 100_000, "expected_close": today}
    overdue = {"id": "overdue", "stage": "Commit", "amount": 100_000, "expected_close": today - timedelta(days=90)}
    result = forecast_pipeline([on_time, overdue], today=today)

    by_id = {c["id"]: c for c in result["deal_contributions"]}
    assert by_id["overdue"]["decay"] < by_id["on_time"]["decay"]
    assert by_id["overdue"]["expected_value"] < by_id["on_time"]["expected_value"]
    # 90 days is two 45-day half-lives: decay should be close to 0.25, not near-zero or 1.
    assert 0.2 < by_id["overdue"]["decay"] < 0.3


def test_unknown_stage_falls_back_to_default_probability():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Some Custom CRM Stage", "amount": 10_000, "expected_close": today}]
    result = forecast_pipeline(deals, today=today)
    assert result["deal_contributions"][0]["probability"] == 0.15  # DEFAULT_PROB


def test_known_stage_uses_its_own_probability_not_the_default():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Negotiation", "amount": 10_000, "expected_close": today}]
    result = forecast_pipeline(deals, today=today)
    assert result["deal_contributions"][0]["probability"] == 0.70


def test_invalid_expected_close_string_produces_a_warning_and_excludes_from_buckets():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Proposal", "amount": 5_000, "expected_close": "not-a-date"}]
    result = forecast_pipeline(deals, today=today)

    assert any("d1" in w and "invalid expected_close" in w for w in result["warnings"])
    assert result["deal_contributions"][0]["expected_close"] is None
    # No week bucket touched — total weekly_forecast sums to 0 even though the
    # deal itself still has a nonzero expected_value (decay=0.5 for unknown date).
    assert sum(result["weekly_forecast"].values()) == 0


def test_deal_past_the_horizon_is_excluded_from_weekly_buckets():
    today = date(2026, 6, 1)
    deals = [{"id": "far", "stage": "Commit", "amount": 50_000, "expected_close": today + timedelta(days=200)}]
    result = forecast_pipeline(deals, today=today, horizon_days=90)
    assert sum(result["weekly_forecast"].values()) == 0
    # It's still tracked in deal_contributions even though it misses the horizon.
    assert result["deal_contributions"][0]["id"] == "far"


def test_expected_close_accepts_iso_date_string_not_just_date_objects():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Commit", "amount": 1_000, "expected_close": "2026-06-01"}]
    result = forecast_pipeline(deals, today=today)
    assert result["deal_contributions"][0]["expected_close"] == "2026-06-01"
    assert not result["warnings"]


def test_stage_summary_aggregates_count_and_pipeline_per_stage():
    today = date(2026, 6, 1)
    deals = [
        {"id": "d1", "stage": "Proposal", "amount": 10_000, "expected_close": today},
        {"id": "d2", "stage": "Proposal", "amount": 20_000, "expected_close": today},
        {"id": "d3", "stage": "Commit", "amount": 5_000, "expected_close": today},
    ]
    result = forecast_pipeline(deals, today=today)
    assert result["stage_summary"]["Proposal"]["count"] == 2
    assert result["stage_summary"]["Proposal"]["total_pipeline"] == 30_000
    assert result["stage_summary"]["Commit"]["count"] == 1


def test_historical_win_rates_override_default_stage_probabilities():
    today = date(2026, 6, 1)
    deals = [{"id": "d1", "stage": "Commit", "amount": 10_000, "expected_close": today}]
    result = forecast_pipeline(deals, today=today, historical_win_rates={"Commit": 0.99})
    assert result["deal_contributions"][0]["probability"] == 0.99
