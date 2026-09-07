"""
tests/test_quota_fairness_excludes_executives.py
==================================================
GET /payout/quota-fairness had no test coverage at all, and computed its
Gini coefficient / mean / std / outliers / rep_count from every rep_id with
a Quota row, regardless of whether that rep is actually a quota-carrying
seller -- unlike GET /analytics/reps/performance and GET /payout/team-summary,
which already exclude Executive/Leadership positions via _selling_rep_ids().
An Executive can still have Quota rows (this platform's data generator
assigns every position a quota, including the CRO), so leaving them in
skews the fairness statistics with a target that was never meant to be
compared against individual reps' territories.

Confirmed live: techo-solutions' CRO carries a $130K quarterly quota --
roughly 2.4x a typical IC's -- and GET /payout/quota-fairness reported
rep_count=12 while every sibling view (Reps tab, rep profile rank) treats
the team as 11 reps.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Position, Quota, Rep, UserProfile
from backend.routers.payout import quota_fairness
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-quota-fairness-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(Quota).where(Quota.company_id == COMPANY))
        await db.execute(delete(UserProfile).where(UserProfile.company_id == COMPANY))
        await db.execute(delete(Position).where(Position.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_executive_quota_excluded_from_fairness_population(cleanup):
    """Two IC reps with equal, modest quotas plus one Executive with a much
    larger quota. If the Executive counts, rep_count is 3 and mean_quota is
    pulled well above what either real IC actually carries."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        ic_a = Rep(name="IC Rep A", email="ic-a@example.com")
        ic_b = Rep(name="IC Rep B", email="ic-b@example.com")
        exec_rep = Rep(name="Exec Rep", email="exec-rep@example.com")
        db.add_all([ic_a, ic_b, exec_rep])
        await db.flush()

        ic_position = Position(name="Account Executive", level="Individual Contributor", rank=5)
        exec_position = Position(name="Chief Revenue Officer", level="Executive", rank=1)
        db.add_all([ic_position, exec_position])
        await db.flush()

        db.add_all([
            UserProfile(name="IC Rep A", email="ic-a@example.com", position_id=ic_position.id),
            UserProfile(name="IC Rep B", email="ic-b@example.com", position_id=ic_position.id),
            UserProfile(name="Exec Rep", email="exec-rep@example.com", position_id=exec_position.id),
        ])

        db.add(Quota(rep_id=ic_a.id, period="2025-Q1", amount=50_000))
        db.add(Quota(rep_id=ic_b.id, period="2025-Q1", amount=50_000))
        db.add(Quota(rep_id=exec_rep.id, period="2025-Q1", amount=500_000))
        await db.commit()

        result = await quota_fairness(period="2025-Q1", db=db)

    assert result["rep_count"] == 2, "Executive must not be counted among quota-carrying reps"
    assert result["mean_quota"] == 50_000.0, "Executive's quota must not skew the mean"
    assert result["std_quota"] == 0.0, "Two equal IC quotas should show zero deviation once the outlier Executive is excluded"
