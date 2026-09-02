"""
tests/test_anomaly_detector.py
===============================
backend/ml/anomaly_detector.py had 0% coverage and, unlike the other three
previously-untested ml/*.py modules fixed alongside this file, it also has no
caller anywhere in the backend — its own docstring says it "feeds
explain_metric_change() with significance scores," but
backend/statistics/sales_drivers.py's real explain_metric_change() doesn't call
it. That's a real integration gap, flagged separately (not something to paper
over with a call added quietly from a test file). What's tested here is that
detect_anomalies() itself does what it claims, so whoever wires it in has a
contract to build against.
"""
from __future__ import annotations

from backend.ml.anomaly_detector import detect_anomalies


def test_empty_records_returns_no_anomalies():
    result = detect_anomalies([])
    assert result["anomalies"] == []
    assert result["anomaly_count"] == 0
    assert result["warnings"]


def test_no_numeric_feature_keys_is_reported_not_crashed():
    result = detect_anomalies([{"period": "2026-01", "label_only": "x"}])
    assert result["anomaly_count"] == 0
    assert any("numeric" in w.lower() for w in result["warnings"])


def test_a_clear_outlier_is_flagged_anomalous():
    # Six steady periods, then one that's wildly higher — a textbook z-score hit.
    records = [{"period": f"2026-{i:02d}", "revenue": 100_000} for i in range(1, 7)]
    records.append({"period": "2026-07", "revenue": 900_000})

    result = detect_anomalies(records, feature_keys=["revenue"])

    anomalous_periods = {a["label"] for a in result["anomalies"] if a["is_anomaly"]}
    assert "2026-07" in anomalous_periods
    assert result["anomaly_count"] >= 1


def test_flat_series_has_no_anomalies():
    records = [{"period": f"2026-{i:02d}", "revenue": 100_000} for i in range(1, 9)]
    result = detect_anomalies(records, feature_keys=["revenue"])
    assert result["anomaly_count"] == 0
    assert all(not a["is_anomaly"] for a in result["anomalies"])


def test_default_feature_keys_pick_up_all_numeric_columns_except_the_label():
    records = [
        {"period": "2026-01", "revenue": 100_000, "deal_count": 5},
        {"period": "2026-02", "revenue": 110_000, "deal_count": 6},
        {"period": "2026-03", "revenue": 105_000, "deal_count": 5},
        {"period": "2026-04", "revenue": 108_000, "deal_count": 7},
    ]
    result = detect_anomalies(records)
    assert set(result["feature_keys"]) == {"revenue", "deal_count"}


def test_baseline_is_the_column_median():
    records = [{"period": str(i), "x": v} for i, v in enumerate([10.0, 20.0, 30.0, 40.0, 50.0])]
    result = detect_anomalies(records, feature_keys=["x"])
    assert result["baseline"]["x"] == 30.0


def test_top_drivers_names_the_feature_that_deviates_most():
    records = [
        {"period": "2026-01", "revenue": 100_000, "deal_count": 5},
        {"period": "2026-02", "revenue": 101_000, "deal_count": 5},
        {"period": "2026-03", "revenue": 99_000, "deal_count": 5},
        {"period": "2026-04", "revenue": 100_500, "deal_count": 50},  # deal_count spikes, revenue doesn't
    ]
    result = detect_anomalies(records, feature_keys=["revenue", "deal_count"])
    spiked = next(a for a in result["anomalies"] if a["label"] == "2026-04")
    assert spiked["top_drivers"][0]["feature"] == "deal_count"


def test_method_and_shape_are_stable_for_downstream_callers():
    records = [{"period": str(i), "x": float(i)} for i in range(5)]
    result = detect_anomalies(records, feature_keys=["x"])
    assert result["method"] == "zscore+isolation_forest"
    for key in ("anomalies", "anomaly_count", "feature_keys", "baseline", "warnings"):
        assert key in result
    for entry in result["anomalies"]:
        for key in ("label", "if_score", "zscore_flags", "top_drivers", "is_anomaly"):
            assert key in entry
