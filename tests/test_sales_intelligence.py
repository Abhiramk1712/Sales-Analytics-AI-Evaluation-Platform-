import pandas as pd

from backend.statistics.sales_drivers import (
    payout_or_revenue_change_explanation,
    quota_attainment_driver_analysis,
    revenue_driver_analysis,
)
from backend.statistics.pipeline_health import (
    pipeline_coverage_status,
    stale_pipeline_detection,
    stage_slippage_summary,
)
from backend.statistics.rep_risk import (
    calculate_rep_risk_score,
    classify_rep_risk,
    explain_rep_risk,
)
from backend.statistics.forecast_quality import mae, rmse, mape, bias


def test_sales_driver_functions():
    df = pd.DataFrame({"region": ["A", "B"], "revenue": [100, 200], "period": ["p1", "p1"]})
    out = revenue_driver_analysis(df, "period", ["region"], "revenue")
    assert out["drivers"]

    qdf = pd.DataFrame({"rep_name": ["r1"], "revenue": [100], "quota": [200]})
    qout = quota_attainment_driver_analysis(qdf)
    assert qout["drivers"][0]["attainment_pct"] == 50.0

    eout = payout_or_revenue_change_explanation(pd.DataFrame({"entity": ["x"], "change": [10]}))
    assert eout["explanations"]


def test_pipeline_and_risk_functions():
    status = pipeline_coverage_status(1000, 1000)
    assert "coverage" in status

    deals = pd.DataFrame({"id": [1], "updated_at": ["2020-01-01"], "stage": ["Proposal"]})
    stale = stale_pipeline_detection(deals, days_threshold=1)
    assert stale["stale_deals"]

    summary = stage_slippage_summary(deals)
    assert summary["summary"]

    score = calculate_rep_risk_score({"attainment_pct": 50, "win_rate": 20, "pipeline_coverage": 1})
    assert classify_rep_risk(score) in {"low", "medium", "high"}
    explained = explain_rep_risk({"attainment_pct": 50, "win_rate": 20, "pipeline_coverage": 1})
    assert "reasons" in explained


def test_forecast_quality_metrics():
    actual = [100, 200, 300]
    pred = [110, 190, 280]
    assert mae(actual, pred) > 0
    assert rmse(actual, pred) > 0
    assert mape(actual, pred) > 0
    assert isinstance(bias(actual, pred), float)
