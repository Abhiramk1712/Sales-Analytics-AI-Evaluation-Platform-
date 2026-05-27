from __future__ import annotations

import pytest

import backend.services.sales_performance_service as sps
from backend.services.quota_attainment_service import AttainmentResult
from backend.services.sales_performance_service import SalesPerformanceService


@pytest.mark.asyncio
async def test_sales_performance_summary_uses_canonical_attainment_for_period(monkeypatch):
    async def _v(value, warnings=None, fallback=False):
        return {"value": value, "warnings": warnings or [], "fallback_mode": fallback}

    async def fake_total_revenue(_db, _filters=None):
        return await _v(300000.0)

    async def fake_open_pipeline(_db, _filters=None):
        return await _v(900000.0)

    async def fake_win_rate(_db, _filters=None):
        return {"value": 60.0, "warnings": [], "won": 12, "lost": 8}

    async def fake_avg_deal(_db, _filters=None):
        return await _v(50000.0)

    async def fake_cov(_db, _filters=None):
        return await _v(3.0)

    async def fake_nrr(_db, _filters=None):
        return await _v(108.0)

    async def fake_grr(_db, _filters=None):
        return await _v(96.0)

    async def fake_company_att(_db, period):
        return AttainmentResult(
            period=period,
            period_grain="quarterly",
            revenue=300000.0,
            quota=250000.0,
            attainment_pct=120.0,
            quota_source="direct",
            fallback_mode=False,
            warnings=[],
            calculation_trace={"test": True},
        )

    monkeypatch.setattr(sps.calc, "get_total_revenue", fake_total_revenue)
    monkeypatch.setattr(sps.calc, "get_open_pipeline", fake_open_pipeline)
    monkeypatch.setattr(sps.calc, "get_win_rate", fake_win_rate)
    monkeypatch.setattr(sps.calc, "get_average_deal_size", fake_avg_deal)
    monkeypatch.setattr(sps.calc, "get_pipeline_coverage", fake_cov)
    monkeypatch.setattr(sps.calc, "get_nrr", fake_nrr)
    monkeypatch.setattr(sps.calc, "get_grr", fake_grr)
    monkeypatch.setattr(sps, "get_company_attainment", fake_company_att)

    svc = SalesPerformanceService(db=object())
    out = await svc.get_summary(period="2025-Q2")

    assert out["metrics"]["total_revenue"]["value"] == 300000.0
    assert out["metrics"]["quota"]["value"] == 250000.0
    assert out["metrics"]["attainment_pct"]["value"] == 120.0
    assert out["metrics"]["pipeline_coverage"]["value"] == 3.0
