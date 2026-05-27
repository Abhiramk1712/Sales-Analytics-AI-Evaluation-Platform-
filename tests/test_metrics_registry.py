import pytest

from backend.metrics import get_global_registry


REQUIRED_METRICS = {
    "total_revenue",
    "total_quota",
    "quota_attainment",
    "win_rate",
    "pipeline_coverage",
    "average_deal_size",
    "open_pipeline",
    "forecasted_revenue",
    "rep_risk_score",
    "sales_cycle_length",
}


def test_required_metrics_exist():
    registry = get_global_registry()
    names = {metric.name for metric in registry.list_all()}
    assert REQUIRED_METRICS.issubset(names)


def test_metric_fields_are_governed():
    registry = get_global_registry()
    for name in REQUIRED_METRICS:
        metric = registry.get_required(name)
        assert metric.formula
        assert metric.description
        assert metric.caveats is not None


def test_unknown_metric_fails_safely():
    registry = get_global_registry()
    with pytest.raises(ValueError):
        registry.get_required("not_a_metric")


def test_total_revenue_definition_matches_revenue_table_behavior():
    registry = get_global_registry()
    metric = registry.get_required("total_revenue")
    assert "SUM(revenue.amount)" in metric.formula
    assert "recognized revenue" in metric.description.lower()


def test_pipeline_coverage_definition_mentions_quota_grain_caveat():
    registry = get_global_registry()
    metric = registry.get_required("pipeline_coverage")
    caveats = " ".join(metric.caveats or []).lower()
    assert "quota grain" in caveats
