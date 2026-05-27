"""
Tests for backend/metrics/calculators.py — get_pipeline_hygiene().
Uses AsyncMock-based DB sessions with pre-built ORM objects.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, timedelta


# ── Helpers ───────────────────────────────────────────────────────────────

def _mock_deal(
    id="d1",
    stage="Qualification",
    amount=10_000,
    close_probability=30,
    expected_close_date=None,
):
    from types import SimpleNamespace
    return SimpleNamespace(
        id=id,
        stage=stage,
        amount=amount,
        close_probability=close_probability,
        expected_close_date=expected_close_date,
    )


def _open_deals_mock(*deals):
    """Return an AsyncMock db.execute that yields the given deals via scalars().all()."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(deals)
    db = AsyncMock()
    db.execute.return_value = result
    return db


# ── Basic return shape ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_returns_expected_keys():
    from backend.metrics.calculators import get_pipeline_hygiene
    db = _open_deals_mock()
    result = await get_pipeline_hygiene(db)
    assert "total_open_deals" in result
    assert "missing_close_date_count" in result
    assert "overdue_count" in result
    assert "high_prob_early_stage_count" in result
    assert "warnings" in result
    assert "sources" in result


@pytest.mark.asyncio
async def test_hygiene_empty_pipeline():
    from backend.metrics.calculators import get_pipeline_hygiene
    db = _open_deals_mock()
    result = await get_pipeline_hygiene(db)
    assert result["total_open_deals"] == 0
    assert result["missing_close_date_count"] == 0
    assert result["overdue_count"] == 0


# ── Missing close date detection ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_detects_missing_close_date():
    from backend.metrics.calculators import get_pipeline_hygiene
    deal_missing = _mock_deal(id="dm1", expected_close_date=None)
    deal_ok = _mock_deal(id="dok", expected_close_date=date.today() + timedelta(days=10))
    db = _open_deals_mock(deal_missing, deal_ok)
    result = await get_pipeline_hygiene(db)
    assert result["missing_close_date_count"] >= 1
    assert "dm1" in result.get("missing_close_date_ids", [])


# ── Overdue detection ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_detects_overdue_deal():
    from backend.metrics.calculators import get_pipeline_hygiene
    past_date = date.today() - timedelta(days=5)
    overdue_deal = _mock_deal(id="od1", expected_close_date=past_date)
    db = _open_deals_mock(overdue_deal)
    result = await get_pipeline_hygiene(db)
    assert result["overdue_count"] >= 1
    assert "od1" in result.get("overdue_ids", [])


@pytest.mark.asyncio
async def test_hygiene_future_close_date_not_overdue():
    from backend.metrics.calculators import get_pipeline_hygiene
    future_deal = _mock_deal(id="fd1", expected_close_date=date.today() + timedelta(days=30))
    db = _open_deals_mock(future_deal)
    result = await get_pipeline_hygiene(db)
    assert result["overdue_count"] == 0


# ── Stale threshold ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_stale_threshold_respected():
    from backend.metrics.calculators import get_pipeline_hygiene
    db = _open_deals_mock()
    result30 = await get_pipeline_hygiene(db, stale_days=30)
    result60 = await get_pipeline_hygiene(db, stale_days=60)
    assert result30["stale_threshold_days"] == 30
    assert result60["stale_threshold_days"] == 60


# ── High-prob early stage ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_high_prob_early_stage():
    from backend.metrics.calculators import get_pipeline_hygiene
    # High probability (>=70) in an early stage should be flagged
    suspect = _mock_deal(id="hp1", stage="Prospecting", close_probability=90,
                         expected_close_date=date.today() + timedelta(days=20))
    db = _open_deals_mock(suspect)
    result = await get_pipeline_hygiene(db)
    assert result["high_prob_early_stage_count"] >= 1


@pytest.mark.asyncio
async def test_hygiene_low_prob_early_stage_not_flagged():
    from backend.metrics.calculators import get_pipeline_hygiene
    normal = _mock_deal(id="lp1", stage="Prospecting", close_probability=20,
                        expected_close_date=date.today() + timedelta(days=20))
    db = _open_deals_mock(normal)
    result = await get_pipeline_hygiene(db)
    assert result["high_prob_early_stage_count"] == 0


# ── Multiple issues on same deal ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_hygiene_multiple_issues():
    from backend.metrics.calculators import get_pipeline_hygiene
    d1 = _mock_deal(id="m1", expected_close_date=date.today() - timedelta(days=2))  # overdue
    d2 = _mock_deal(id="m2", expected_close_date=None)  # missing date
    d3 = _mock_deal(id="m3", stage="Qualification", close_probability=95,
                    expected_close_date=date.today() + timedelta(days=5))  # high-prob early
    db = _open_deals_mock(d1, d2, d3)
    result = await get_pipeline_hygiene(db)
    assert result["total_open_deals"] == 3
    assert result["overdue_count"] >= 1
    assert result["missing_close_date_count"] >= 1
    assert result["high_prob_early_stage_count"] >= 1
    # warnings is populated by filter issues, not hygiene counts — just check structure
    assert isinstance(result["warnings"], list)
