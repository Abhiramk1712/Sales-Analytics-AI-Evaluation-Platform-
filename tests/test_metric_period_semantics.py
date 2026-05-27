from __future__ import annotations

import pytest

import backend.services.quota_attainment_service as quota_svc
from backend.services.quota_attainment_service import period_to_months, get_quota_for_period


def test_period_to_months_quarter_expansion_is_three_months():
    assert period_to_months("2025-Q2") == ["2025-04", "2025-05", "2025-06"]


def test_period_to_months_annual_expansion_is_twelve_months():
    months = period_to_months("2025")
    assert len(months) == 12
    assert months[0] == "2025-01"
    assert months[-1] == "2025-12"


def test_period_to_months_ytd_expansion_starts_from_january():
    months = period_to_months("YTD")
    assert months[0].endswith("-01")
    assert len(months) >= 1


@pytest.mark.asyncio
async def test_monthly_quota_can_fallback_from_annual(monkeypatch):
    async def fake_sum(_db, periods, rep_id=None):
        if periods == ["2025-08"]:
            return 0.0
        if periods == ["2025-Q3"]:
            return 0.0
        if periods == ["2025"]:
            return 1200.0
        return 0.0

    monkeypatch.setattr(quota_svc, "_sum_quota", fake_sum)

    quota, source, warnings = await get_quota_for_period(object(), "2025-08", rep_id="rep-1")
    assert quota == 100.0
    assert source == "allocated_from_annual"
    assert warnings
