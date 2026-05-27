from pathlib import Path

from backend.etl.pipeline import run_etl_pipeline


def test_run_etl_pipeline_produces_medallion_layers():
    source = Path("companies/techo-solutions")
    result = run_etl_pipeline(source)

    assert result.bronze
    assert result.silver
    assert result.gold
    assert result.quality
    assert "mart_rep_month_performance" in result.gold
    assert "mart_forecast_features" in result.gold
