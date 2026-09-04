"""
tests/test_territory_performance_period_scope.py
==================================================
GET /territories/{territory_id}/performance had a test (test_territory_performance.py)
but it's mock-based: a fake DB that returns the same hardcoded value for any
query matching "count(deals.id" regardless of which specific query it is or
what filters are applied. That structurally cannot catch a per-rep-vs-
aggregate discrepancy, which is exactly what shipped: the territory-level
deal_stmt/lost_stmt/open_stmt queries correctly applied `date_filter` for a
given period, but the per-rep won_by_rep/lost_by_rep/open_by_rep breakdown
queries a few lines below them did not — they ran unfiltered regardless of
`period`.

Confirmed live: EMEA territory, period=2026-Q3 — the endpoint's own
top-level total said "deals_won": 2, while its "reps" breakdown for the same
response claimed Chelsea Butler alone had 15 wins and Kyle Melton 8 (each
rep's real *all-time* total) — an aggregate contradicted by its own
breakdown in the same payload.

This test is real, DB-backed (not the existing file's SQL-string-sniffing
mock) specifically so it can catch that class of bug: it asserts the
per-rep numbers actually sum to the territory-level aggregate for a given
period, which is impossible to satisfy by accident if either side is
silently unscoped.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import (
    Rep, Deal, Revenue, Quota, Territory, UserProfile, UserTerritoryAssignment,
)
from backend.routers.plans import get_territory_performance
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-territory-period-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(UserTerritoryAssignment).where(UserTerritoryAssignment.company_id == COMPANY))
        await db.execute(delete(Territory).where(Territory.company_id == COMPANY))
        await db.execute(delete(UserProfile).where(UserProfile.company_id == COMPANY))
        await db.execute(delete(Quota).where(Quota.company_id == COMPANY))
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Deal).where(Deal.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_rep_breakdown_sums_to_territory_total_for_a_scoped_period(cleanup):
    """A rep with deals split across two quarters — Q1 heavy, Q2 empty of
    wins. A Q2-scoped call's per-rep "deals_won" must be 0, not Q1's real
    count, and must sum exactly to the territory-level total for Q2."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        email = "territoryperiodrep@example.com"
        rep = Rep(name="Territory Period Rep", email=email, region="EMEA")
        db.add(rep)
        user = UserProfile(name="Territory Period Rep", email=email)
        db.add(user)
        territory = Territory(name="Test EMEA", territory_code=f"T-TEST-{uuid.uuid4().hex[:6]}", region="EMEA")
        db.add(territory)
        await db.flush()

        db.add(UserTerritoryAssignment(user_id=user.id, territory_id=territory.id, is_primary=True))

        # Q1 2025: 3 deals closed won, real revenue.
        for i in range(3):
            db.add(Deal(
                rep_id=rep.id, name=f"Q1 Deal {i}", stage="Closed Won", amount=10_000,
                actual_close_date=date(2025, 2, 10), created_at=datetime(2025, 1, 5),
            ))
        db.add(Revenue(rep_id=rep.id, period="2025-02", amount=30_000))

        # Q2 2025: no closed deals at all, but the rep is still assigned to
        # the territory and has (zero) revenue that quarter.
        db.add(Revenue(rep_id=rep.id, period="2025-04", amount=0))

        await db.commit()

        q2_result = await get_territory_performance(territory_id=str(territory.id), period="2025-Q2", db=db)
        q1_result = await get_territory_performance(territory_id=str(territory.id), period="2025-Q1", db=db)

    assert q2_result["deals_won"] == 0
    assert q2_result["reps"][0]["deals_won"] == 0
    assert sum(r["deals_won"] for r in q2_result["reps"]) == q2_result["deals_won"]

    assert q1_result["deals_won"] == 3
    assert q1_result["reps"][0]["deals_won"] == 3
    assert sum(r["deals_won"] for r in q1_result["reps"]) == q1_result["deals_won"]
