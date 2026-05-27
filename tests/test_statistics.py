"""
tests/test_statistics.py
=======================
Tests for statistics module
"""
import pytest
import numpy as np
from backend.statistics.descriptive import summarize_distribution, percentile_rank, month_over_month_change
from backend.statistics.anomaly_detection import zscore_outliers, iqr_outliers, detect_metric_spikes
from backend.statistics.driver_analysis import contribution_analysis, compare_periods
from backend.statistics.funnel_analysis import stage_conversion_rates, stage_dropoff
import pandas as pd


class TestDescriptive:
    def test_summarize_distribution(self):
        values = [1, 2, 3, 4, 5]
        summary = summarize_distribution(values)
        assert summary["count"] == 5
        assert summary["mean"] == 3.0
        assert summary["median"] == 3.0
        assert summary["min"] == 1.0
        assert summary["max"] == 5.0

    def test_percentile_rank(self):
        values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        rank = percentile_rank(5, values)
        assert 40 <= rank <= 60  # 5 is around median

    def test_month_over_month_change(self):
        result = month_over_month_change(120000, 100000)
        assert result["absolute_change"] == 20000
        assert abs(result["percent_change"] - 20.0) < 0.1


class TestAnomalyDetection:
    def test_zscore_outliers(self):
        values = [1, 2, 3, 4, 5, 100]  # 100 is outlier
        outliers = zscore_outliers(values, threshold=2.0)
        assert len(outliers) > 0

    def test_iqr_outliers(self):
        values = [1, 2, 3, 4, 5, 100]
        outliers = iqr_outliers(values)
        assert len(outliers) > 0

    def test_detect_metric_spikes(self):
        series = [100, 102, 101, 160]
        spikes = detect_metric_spikes(series, threshold_pct=30)
        assert spikes == [3]


class TestDriverAnalysis:
    def test_contribution_analysis(self):
        df = pd.DataFrame({
            "region": ["US-West", "US-East", "US-Central"],
            "revenue": [1500000, 1800000, 1200000]
        })
        result = contribution_analysis(df, "region", "revenue")
        assert len(result) == 3
        assert "contribution_pct" in result.columns
        assert result["contribution_pct"].sum() > 99  # ~100%

    def test_compare_periods(self):
        df = pd.DataFrame({
            "period": ["2025-01", "2025-01", "2025-02", "2025-02"],
            "amount": [100000, 150000, 120000, 160000]
        })
        result = compare_periods(df, "period", "amount", "2025-02", "2025-01")
        assert result["current_value"] == 280000
        assert result["previous_value"] == 250000


class TestFunnelAnalysis:
    def test_stage_conversion_rates(self):
        df = pd.DataFrame({
            "stage": ["Prospecting", "Prospecting", "Qualification", "Proposal"],
            "id": [1, 2, 3, 4]
        })
        stage_order = ["Prospecting", "Qualification", "Proposal"]
        result = stage_conversion_rates(df, "stage", "id", stage_order)
        assert "conversion_from_first" in result.columns
        assert len(result) == 3
