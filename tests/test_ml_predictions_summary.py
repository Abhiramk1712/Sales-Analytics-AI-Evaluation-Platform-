from datetime import datetime, timedelta, timezone

from backend.routers.forecasting import build_prediction_summary


class _Pred:
    def __init__(self, model_name, predicted_at, prediction, model_version="v1", confidence=None):
        self.model_name = model_name
        self.predicted_at = predicted_at
        self.prediction = prediction
        self.model_version = model_version
        self.confidence = confidence


def test_build_prediction_summary_counts_and_latest_forecast():
    now = datetime.now(timezone.utc)
    rows = [
        _Pred("deal_scoring", now - timedelta(minutes=5), {"risk_level": "high"}),
        _Pred("revenue_forecast", now - timedelta(minutes=2), {"forecast_values": [10], "warnings": ["low confidence"]}, confidence=0.3),
        _Pred("revenue_forecast", now - timedelta(minutes=10), {"forecast_values": [9]}),
    ]

    summary = build_prediction_summary(rows)

    assert summary["prediction_count"] == 3
    assert summary["counts_by_model"]["revenue_forecast"] == 2
    assert summary["counts_by_model"]["deal_scoring"] == 1
    assert summary["latest_forecast"] is not None
    assert summary["latest_forecast"]["prediction"]["forecast_values"] == [10]
    assert summary["warning_counts_by_model"]["revenue_forecast"] == 1
