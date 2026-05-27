from pathlib import Path

from backend.etl.pipeline import run_etl_pipeline


def test_feature_store_tables_are_generated_from_gold_marts():
    result = run_etl_pipeline(Path("companies/techo-solutions"))

    assert "forecast_revenue_monthly" in result.feature_store
    assert "rep_performance_monthly" in result.feature_store
    assert len(result.feature_store["forecast_revenue_monthly"]) > 0
    assert len(result.feature_store["rep_performance_monthly"]) > 0
