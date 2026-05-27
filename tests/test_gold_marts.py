from pathlib import Path

from backend.etl.pipeline import run_etl_pipeline


def test_gold_marts_include_required_metadata_fields():
    result = run_etl_pipeline(Path("companies/techo-solutions"))

    required_marts = {
        "mart_rep_month_performance",
        "mart_team_period_performance",
        "mart_plan_period_performance",
        "mart_territory_period_performance",
        "mart_pipeline_snapshot",
        "mart_sales_credit_detail",
        "mart_payout_detail",
        "mart_forecast_features",
    }
    assert required_marts.issubset(set(result.gold.keys()))

    for mart_name in required_marts:
        metadata = result.gold[mart_name]["metadata"]
        assert "row_count" in metadata
        assert "generated_at" in metadata
        assert "source_tables" in metadata
        assert "warnings" in metadata
        assert "data_quality_score" in metadata
