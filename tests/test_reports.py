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
