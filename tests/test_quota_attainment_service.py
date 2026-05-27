"""
tests/test_quota_attainment_service.py
=======================================
Tests for QuotaAttainmentService helpers (pure/logic layer only — no DB needed).
"""
from __future__ import annotations

import pytest
import backend.services.quota_attainment_service as quota_svc

from backend.services.quota_attainment_service import (
    calculate_attainment,
    get_quota_for_period,
    normalize_period,
    period_grain,
    period_to_months,
    quota_period_matches,
)


class TestCalculateAttainment:
    def test_normal(self):
        assert calculate_attainment(75_000, 100_000) == 75.0

    def test_over_quota(self):
        assert calculate_attainment(130_000, 100_000) == 130.0

    def test_zero_quota(self):
        assert calculate_attainment(50_000, 0) == 0.0

    def test_zero_revenue(self):
        assert calculate_attainment(0, 100_000) == 0.0

    def test_exact_quota(self):
        assert calculate_attainment(100_000, 100_000) == 100.0


class TestPeriodGrainService:
    @pytest.mark.parametrize("period,expected", [
        ("2024-01", "monthly"),
        ("2024-12", "monthly"),
        ("2024-Q1", "quarterly"),
        ("2024-Q4", "quarterly"),
        ("2024", "annual"),
        ("2023", "annual"),
    ])
    def test_grain(self, period, expected):
        assert period_grain(period) == expected


class TestPeriodToMonthsService:
    def test_q1_expansion(self):
        assert period_to_months("2024-Q1") == ["2024-01", "2024-02", "2024-03"]

    def test_q4_expansion(self):
        assert period_to_months("2024-Q4") == ["2024-10", "2024-11", "2024-12"]

    def test_monthly_single(self):
        assert period_to_months("2024-06") == ["2024-06"]

    def test_annual_count(self):
        assert len(period_to_months("2024")) == 12


class TestQuotaPeriodMatchesService:
    @pytest.mark.parametrize("quota_period,target,expected", [
        ("2024-Q2", "2024-04", True),
        ("2024-Q2", "2024-05", True),
        ("2024-Q2", "2024-06", True),
        ("2024-Q2", "2024-03", False),
        ("2024-Q2", "2024-07", False),
        ("2024", "2024-Q2", True),
        ("2024", "2024-06", True),
        ("2024", "2025-01", False),
        ("2024-06", "2024-Q2", True),
        ("2024-03", "2024-Q2", False),
    ])
    def test_matching(self, quota_period, target, expected):
        assert quota_period_matches(quota_period, target) == expected


class TestQuotaFallbackSemantics:
    @pytest.mark.asyncio
    async def test_monthly_allocates_from_quarterly_when_month_missing(self, monkeypatch):
        async def fake_sum(_db, periods, rep_id=None):
            if periods == ["2025-04"]:
                return 0.0
            if periods == ["2025-Q2"]:
                return 300.0
            return 0.0

        monkeypatch.setattr(quota_svc, "_sum_quota", fake_sum)

        quota, source, warnings = await get_quota_for_period(object(), "2025-04", rep_id="rep-1")
        assert quota == 100.0
        assert source == "allocated_from_quarterly"
        assert any("allocated" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_quarterly_rolls_up_from_monthly_when_quarter_missing(self, monkeypatch):
        async def fake_sum(_db, periods, rep_id=None):
            if periods == ["2025-Q2"]:
                return 0.0
            if periods == ["2025-04", "2025-05", "2025-06"]:
                return 450.0
            return 0.0

        monkeypatch.setattr(quota_svc, "_sum_quota", fake_sum)

        quota, source, warnings = await get_quota_for_period(object(), "2025-Q2", rep_id="rep-1")
        assert quota == 450.0
        assert source == "rolled_up_from_monthly"
        assert any("rolled up" in w.lower() for w in warnings)

    @pytest.mark.asyncio
    async def test_annual_rolls_up_from_quarterly_when_annual_missing(self, monkeypatch):
        async def fake_sum(_db, periods, rep_id=None):
            if periods == ["2025"]:
                return 0.0
            if periods == ["2025-Q1", "2025-Q2", "2025-Q3", "2025-Q4"]:
                return 1200.0
            return 0.0

        monkeypatch.setattr(quota_svc, "_sum_quota", fake_sum)

        quota, source, _warnings = await get_quota_for_period(object(), "2025", rep_id="rep-1")
        assert quota == 1200.0
        assert source == "rolled_up_from_quarterly"
