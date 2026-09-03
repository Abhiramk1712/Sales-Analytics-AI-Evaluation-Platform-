"""
tests/test_payout_forecast_endpoint.py
========================================
GET /payout/forecast had no test at all. It accesses
run_revenue_forecast(...).forecast — but run_revenue_forecast
(backend/ml/forecasting.py) returns a plain dict keyed "forecast_values", not
an object with a .forecast attribute. That AttributeError was silently caught
by a broad `except Exception:` one line below, so the endpoint always fell
through to the seasonal-heuristic fallback, for every rep, regardless of how
much real history they had — confirmed live against techo-solutions (12 reps,
8-24 months of history each) before this fix: every single one came back
"medium" confidence, never "high", which only the fallback path can produce
once a rep has >= 12 months of history.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Rep, Revenue
from backend.routers.payout import payout_forecast
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-payout-forecast-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True)
async def fresh_engine(db_schema):
    import backend.database as database

    database._engine = None
    database._async_session_factory = None
    yield
    engine = database._engine
    if engine is not None:
        await engine.dispose()
    database._engine = None
    database._async_session_factory = None


@pytest.fixture
async def cleanup():
    yield
    factory = get_session_factory()
    async with factory() as db, unscoped():
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_forecast_reaches_high_confidence_with_a_full_year_of_history(cleanup):
    """The real regression test: with >= 12 months of revenue history, the
    endpoint must actually run the ML ensemble path (forecast_confidence
    "high"), not silently degrade to the heuristic fallback ("medium")."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Twelve Month Rep", email="twelvemonth@example.com")
        db.add(rep)
        await db.flush()
        for month in range(1, 19):  # 18 months, comfortably >= 12
            period = f"2025-{month:02d}" if month <= 12 else f"2026-{month - 12:02d}"
            db.add(Revenue(rep_id=rep.id, period=period, amount=100_000 + month * 1_500))
        await db.commit()

        result = await payout_forecast(periods=2, rep_id=None, db=db)

    assert len(result["reps"]) == 1
    rep_forecast = result["reps"][0]
    assert rep_forecast["history_months"] == 18
    assert rep_forecast["forecast_confidence"] == "high"


@pytest.mark.asyncio
async def test_forecast_falls_back_gracefully_with_sparse_history(cleanup):
    """Fewer than 3 months: the documented carry-forward-average path, not a crash."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Sparse Rep", email="sparse@example.com")
        db.add(rep)
        await db.flush()
        db.add(Revenue(rep_id=rep.id, period="2026-01", amount=50_000))
        await db.commit()

        result = await payout_forecast(periods=2, rep_id=None, db=db)

    assert len(result["reps"]) == 1
    assert result["reps"][0]["forecast_confidence"] == "low"
    assert len(result["reps"][0]["quarters"]) == 2
