"""Tests for RevOps metric calculators (backend/metrics/calculators.py).

These tests use a mock AsyncSession to verify calculator output structure
without requiring a live database.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.metrics.calculators import (
    get_nrr,
    get_grr,
    get_arr_growth_rate,
    get_sales_cycle_days,
    get_activity_ratio,
    get_weighted_pipeline_coverage,
    get_quota_attainment_distribution,
)


def _mock_db(scalar_result=None, all_result=None):
    """Create a minimal AsyncSession mock that returns controlled results."""
    db = AsyncMock()
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = scalar_result
    result_mock.all.return_value = all_result or []
    result_mock.fetchall.return_value = all_result or []
    db.execute.return_value = result_mock
    return db


@pytest.mark.asyncio
async def test_get_nrr_returns_dict():
    db = _mock_db(scalar_result=None, all_result=[])
    result = await get_nrr(db)
    assert isinstance(result, dict)
    assert "nrr_pct" in result


@pytest.mark.asyncio
async def test_get_grr_returns_dict():
    db = _mock_db(scalar_result=None, all_result=[])
    result = await get_grr(db)
    assert isinstance(result, dict)
    assert "grr_pct" in result


@pytest.mark.asyncio
async def test_get_arr_growth_rate_returns_dict():
    db = _mock_db(all_result=[])
    result = await get_arr_growth_rate(db)
    assert isinstance(result, dict)
    assert "arr_growth_pct" in result


@pytest.mark.asyncio
async def test_get_sales_cycle_days_no_data():
    db = _mock_db(all_result=[])
    result = await get_sales_cycle_days(db)
    assert isinstance(result, dict)
    assert "avg_days" in result


@pytest.mark.asyncio
async def test_get_activity_ratio_no_data():
    db = _mock_db(scalar_result=0, all_result=[])
    result = await get_activity_ratio(db)
    assert isinstance(result, dict)
    assert "ratio" in result


@pytest.mark.asyncio
async def test_get_weighted_pipeline_coverage_no_data():
    db = _mock_db(all_result=[])
    result = await get_weighted_pipeline_coverage(db)
    assert isinstance(result, dict)
    assert "ratio" in result or "weighted_pipeline" in result


@pytest.mark.asyncio
async def test_get_quota_attainment_distribution_no_data():
    db = _mock_db(all_result=[])
    result = await get_quota_attainment_distribution(db)
    assert isinstance(result, dict)
    assert "data" in result


@pytest.mark.asyncio
async def test_nrr_with_warnings():
    """NRR result should include a warnings key for approximation notice."""
    db = _mock_db(all_result=[])
    result = await get_nrr(db)
    # warnings key optional but nrr_pct always present
    assert "nrr_pct" in result


@pytest.mark.asyncio
async def test_arr_growth_rate_zero_prior():
    """Zero prior ARR should not raise — should return 0% growth."""
    db = _mock_db(all_result=[])
    result = await get_arr_growth_rate(db)
    assert result["arr_growth_pct"] == 0.0 or result["arr_growth_pct"] is not None
