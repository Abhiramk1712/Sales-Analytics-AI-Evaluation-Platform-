import pytest

from backend.reports.report_generator import ReportGenerator


class _Result:
    def __init__(self, scalar_value=0, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return None


class FakeDB:
    async def execute(self, _query):
        return _Result(0, [])


@pytest.mark.asyncio
async def test_generate_executive_report():
    report = await ReportGenerator.generate_report(FakeDB(), "executive_weekly", "2026-04", "CEO", {})
    assert "markdown" in report
    assert "metrics_used" in report
    assert "generated_at" in report
    assert "Sources:" in report["markdown"]
    assert "evidence_citations" in report


@pytest.mark.asyncio
async def test_generate_manager_report():
    report = await ReportGenerator.generate_report(FakeDB(), "manager_monthly", "2026-04", "Manager", {})
    assert report["report_type"] == "manager_monthly"
    assert isinstance(report["warnings"], list)


@pytest.mark.asyncio
async def test_generate_executive_sales_summary_report():
    """GET /reports/generate 500'd for this report type: metrics_service.get_kpis()
    returns a flat dict (kpis["total_revenue"] is a float), but this branch read it
    as kpis.get("total_revenue", {}).get("value", 0) -- a nested-dict shape that
    doesn't exist here, so `.get("value", 0)` raised AttributeError on the float.
    Confirmed live: POST /reports/generate with report_type=executive_sales_summary
    500'd for every company, always -- not data-dependent, so a FakeDB run alone
    reproduces it. Also caught a second bug in the same nested-shape family: the
    "quota_attainment" lookup used a key ("quota_attainment") that doesn't exist on
    kpis at all -- the real key is "attainment_pct".
    """
    report = await ReportGenerator.generate_report(FakeDB(), "executive_sales_summary", "2026-04", "CEO", {})
    assert report["report_type"] == "executive_sales_summary"
    assert "Executive Sales Performance Summary" in report["markdown"]
    assert "Quota Attainment" in report["markdown"]


@pytest.mark.asyncio
async def test_generate_revops_risk_report():
    """Same class of bug as executive_sales_summary, plus a second one in the same
    branch: pipeline_open/weighted_coverage_ratio were read from
    (pipeline.get("data") or {}).get("open_pipeline", 0) -- but
    calculators.get_open_pipeline()/get_weighted_pipeline_coverage() return flat
    dicts with a top-level "value"/"ratio" key, no "data" key at all, so this
    always silently fell back to 0 regardless of real pipeline value."""
    report = await ReportGenerator.generate_report(FakeDB(), "revops_risk_report", "2026-04", "RevOps", {})
    assert report["report_type"] == "revops_risk_report"
    assert "RevOps Risk Report" in report["markdown"]
    assert "Weighted Coverage Ratio" in report["markdown"]
