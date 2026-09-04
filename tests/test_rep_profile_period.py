"""
tests/test_rep_profile_period.py
=================================
GET /analytics/reps/{rep_id}/profile had no test at all, and no `period`
query parameter — every field in its "performance" summary (revenue, quota,
attainment_pct, win_rate, deals_won/lost, open_pipeline, rank, commission_tier)
was computed as an unscoped all-time total, unlike its sibling endpoint
GET /analytics/reps/performance, which has supported `period` since it was
written. Confirmed live: the header's period selector correctly filtered the
"Top Performers" table and the Executive Overview KPIs to a single quarter,
while the rep detail panel for the exact same rep, on the exact same screen,
kept showing all-time numbers regardless — two different attainment
percentages for one person, visible at once, because two code paths agreed
to disagree.

Fixed by reusing calculators.get_rep_performance() (already period-aware,
already used by /reps/performance) instead of the endpoint's own hand-rolled
unscoped queries, and by scoping the revenue-based rank comparison to the
same period window.

Also covers a second, unrelated bug found while fixing the first: the
ramp_factor/ramp_status lookup lived in a block *after* this function's only
`return` statement — dead code, unreachable, and it referenced a
`profile_resp` name the function never defined. Both fields always came back
None. No current frontend caller reads them from this endpoint (checked), so
nothing was visibly broken by it, but it was live RepRamp data this endpoint
silently never surfaced.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Rep, Revenue, Quota, Deal, RepRamp
from backend.routers.analytics import rep_profile
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-rep-profile-period-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(RepRamp).where(RepRamp.company_id == COMPANY))
        await db.execute(delete(Deal).where(Deal.company_id == COMPANY))
        await db.execute(delete(Quota).where(Quota.company_id == COMPANY))
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


async def _seed_two_quarter_rep(db):
    """One rep with deliberately different attainment in Q1 vs Q2 2025:
    Q1 revenue is well below quota (60%), Q2 is well above (150%) — chosen
    far apart so a period-scoping bug (mixing the two, or ignoring period
    entirely) is impossible to miss in the resulting attainment_pct."""
    rep = Rep(name="Period Test Rep", email="periodtest@example.com")
    db.add(rep)
    await db.flush()

    # Q1 2025: quota $30,000, revenue $18,000 -> 60% attainment
    db.add(Quota(rep_id=rep.id, period="2025-Q1", amount=30_000))
    db.add(Revenue(rep_id=rep.id, period="2025-01", amount=6_000))
    db.add(Revenue(rep_id=rep.id, period="2025-02", amount=6_000))
    db.add(Revenue(rep_id=rep.id, period="2025-03", amount=6_000))

    # Q2 2025: quota $20,000, revenue $30,000 -> 150% attainment
    db.add(Quota(rep_id=rep.id, period="2025-Q2", amount=20_000))
    db.add(Revenue(rep_id=rep.id, period="2025-04", amount=10_000))
    db.add(Revenue(rep_id=rep.id, period="2025-05", amount=10_000))
    db.add(Revenue(rep_id=rep.id, period="2025-06", amount=10_000))

    # One closed-won deal per quarter, so win_rate/deals_won can be checked too.
    db.add(Deal(
        rep_id=rep.id, name="Q1 Deal", stage="Closed Won", amount=6_000,
        actual_close_date=date(2025, 2, 15),
        created_at=datetime(2025, 1, 10),
    ))
    db.add(Deal(
        rep_id=rep.id, name="Q2 Deal", stage="Closed Won", amount=10_000,
        actual_close_date=date(2025, 5, 15),
        created_at=datetime(2025, 4, 10),
    ))

    db.add(RepRamp(rep_id=rep.id, period="2025-06", months_since_hire=3, ramp_factor=0.75, is_ramping=True))

    await db.commit()
    return rep


@pytest.mark.asyncio
async def test_profile_all_time_matches_pre_fix_behavior(cleanup):
    """No period given -> combined totals across every period, same contract
    as before this endpoint accepted a period param at all."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _seed_two_quarter_rep(db)
        result = await rep_profile(rep_id=str(rep.id), period=None, db=db)

    perf = result["performance"]
    assert perf["revenue"] == 48_000.0  # 18,000 + 30,000
    assert perf["quota"] == 50_000.0    # 30,000 + 20,000
    assert perf["deals_won"] == 2


@pytest.mark.asyncio
async def test_profile_period_scopes_to_a_single_quarter(cleanup):
    """The actual regression: period='2025-Q1' must return ONLY Q1's
    revenue/quota/attainment, not the all-time total — and Q1 vs Q2 must
    disagree exactly the way the underlying data does (60% vs 150%)."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _seed_two_quarter_rep(db)

        q1 = await rep_profile(rep_id=str(rep.id), period="2025-Q1", db=db)
        q2 = await rep_profile(rep_id=str(rep.id), period="2025-Q2", db=db)

    assert q1["performance"]["revenue"] == 18_000.0
    assert q1["performance"]["quota"] == 30_000.0
    assert q1["performance"]["attainment_pct"] == 60.0
    assert q1["performance"]["deals_won"] == 1
    assert q1["commission_tier"] == "Below Threshold (3%)"

    assert q2["performance"]["revenue"] == 30_000.0
    assert q2["performance"]["quota"] == 20_000.0
    assert q2["performance"]["attainment_pct"] == 150.0
    assert q2["performance"]["deals_won"] == 1
    assert q2["commission_tier"] == "Accelerated (10%)"


@pytest.mark.asyncio
async def test_profile_rank_is_scoped_to_the_same_period_as_revenue(cleanup):
    """A second rep who out-earns the first only in Q1 must outrank them in
    a Q1-scoped call and not in a Q2-scoped one — proving rank isn't still
    silently comparing against unscoped, all-time revenue."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _seed_two_quarter_rep(db)

        other = Rep(name="Other Rep", email="other@example.com")
        db.add(other)
        await db.flush()
        db.add(Revenue(rep_id=other.id, period="2025-01", amount=100_000))  # dwarfs rep in Q1
        await db.commit()

        q1 = await rep_profile(rep_id=str(rep.id), period="2025-Q1", db=db)
        q2 = await rep_profile(rep_id=str(rep.id), period="2025-Q2", db=db)

    assert q1["rank"] == 2   # outranked in Q1 (other rep has $100k that quarter)
    assert q2["rank"] == 1   # other rep has no Q2 revenue at all


@pytest.mark.asyncio
async def test_profile_populates_ramp_data_not_always_none(cleanup):
    """ramp_factor/ramp_status used to be hardcoded None — the lookup that
    was supposed to populate them lived after the function's only return."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _seed_two_quarter_rep(db)
        result = await rep_profile(rep_id=str(rep.id), period=None, db=db)

    assert result["ramp_factor"] == 0.75
    assert result["ramp_status"] == "ramping"
