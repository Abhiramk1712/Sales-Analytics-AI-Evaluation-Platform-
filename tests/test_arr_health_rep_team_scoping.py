"""
tests/test_arr_health_rep_team_scoping.py
============================================
ARR Health (NRR, GRR, ARR growth, sales cycle, activity ratio, weighted
pipeline coverage, quota attainment distribution, ARR waterfall) was always
company-wide, even for sales_manager -- no way to see "my team's" ARR
health. GET /analytics/revops-kpis and GET /forecast/arr-waterfall now
accept optional rep_id/team_id, resolved the same way as the Forecast/
Pipeline Health scoping (see test_forecasting_rep_team_scoping.py):
rep_id -> one rep, team_id -> that team's roster, neither -> unscoped
(executive/revops_admin keep seeing company-wide combined data, unchanged).

The ARR waterfall grain question (money path, backend/metrics/
calculators.py): each arr_waterfall row already carries its OWN rep-level
running balance (arr_start(period) == arr_end(prior period) per rep,
confirmed populated for every row in seed data) -- summing a FIXED subset
of reps across periods preserves that continuity at the scoped level, since
the same subset filters every period consistently. A scoped query that
finds no rows for a period must NOT fall back to the company-wide derived
path (that would leak unscoped numbers into a rep/team-only view) -- tests
below assert this directly.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.metrics import calculators
from backend.models import ArrWaterfallEntry, Deal, Quota, Revenue, Team, Rep
from backend.routers import analytics as analytics_router
from backend.routers import forecasting as forecasting_router
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-arr-scope-{uuid.uuid4().hex[:8]}"
P1 = "2026-01"
P2 = "2026-02"

CLEANUP_MODELS = [ArrWaterfallEntry, Deal, Quota, Revenue, Rep, Team]


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
        for model in CLEANUP_MODELS:
            await db.execute(delete(model).where(model.company_id == COMPANY))
        await db.commit()


async def _make_team(db, *, name: str) -> Team:
    team = Team(name=name)
    db.add(team)
    await db.flush()
    return team


async def _make_rep(db, *, name: str, email: str, team: Team | None = None) -> Rep:
    rep = Rep(name=name, email=email, team_id=team.id if team else None)
    db.add(rep)
    await db.flush()
    return rep


async def _make_revenue(db, *, rep: Rep, amount: float, period: str, revenue_type: str = "new_biz") -> Revenue:
    r = Revenue(rep_id=rep.id, period=period, amount=amount, revenue_type=revenue_type)
    db.add(r)
    await db.flush()
    return r


async def _make_quota(db, *, rep: Rep, amount: float, period: str) -> Quota:
    q = Quota(rep_id=rep.id, period=period, amount=amount)
    db.add(q)
    await db.flush()
    return q


async def _make_arr_entry(db, *, rep: Rep, period: str, arr_start: float, arr_end: float, new_logo: float = 0.0) -> ArrWaterfallEntry:
    e = ArrWaterfallEntry(
        rep_id=rep.id, period=period,
        arr_start=arr_start, arr_end=arr_end,
        mrr_new=new_logo / 12, mrr_expansion=0, mrr_contraction=0, mrr_churn=0, mrr_renewal=0,
        mrr_net=(arr_end - arr_start) / 12,
    )
    db.add(e)
    await db.flush()
    return e


# ── NRR / activity ratio / quota attainment (Revenue/Deal/Rep-query scoping) ─

@pytest.mark.asyncio
async def test_get_nrr_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b@x.com")
        # Rep A: healthy retention. Rep B: churning heavily -- if B leaked
        # into A's scoped NRR, A's number would drop sharply.
        await _make_revenue(db, rep=rep_a, amount=10000.0, period=P1, revenue_type="renewal")
        await _make_revenue(db, rep=rep_a, amount=2000.0, period=P1, revenue_type="expansion")
        await _make_revenue(db, rep=rep_b, amount=10000.0, period=P1, revenue_type="renewal")
        await _make_revenue(db, rep=rep_b, amount=9000.0, period=P1, revenue_type="churn")
        await db.commit()

        scoped = await calculators.get_nrr(db, {"rep_ids": [rep_a.id]})
        unscoped_result = await calculators.get_nrr(db, {})

        assert scoped["nrr_pct"] == pytest.approx(120.0)
        assert unscoped_result["nrr_pct"] < scoped["nrr_pct"]


@pytest.mark.asyncio
async def test_get_quota_attainment_distribution_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team = await _make_team(db, name="Central")
        rep_a = await _make_rep(db, name="Rep A", email="a2@x.com", team=team)
        rep_b = await _make_rep(db, name="Rep B", email="b2@x.com")
        # Rep A: 130% attainment (above_120 tier). Rep B: 20% attainment
        # (below_50 tier) -- if B leaked into A's scoped distribution, the
        # below_50 bucket would show a count instead of staying empty.
        await _make_revenue(db, rep=rep_a, amount=130.0, period=P1)
        await _make_quota(db, rep=rep_a, amount=100.0, period=P1)
        await _make_revenue(db, rep=rep_b, amount=20.0, period=P1)
        await _make_quota(db, rep=rep_b, amount=100.0, period=P1)
        await db.commit()

        scoped = await calculators.get_quota_attainment_distribution(db, {"rep_ids": [rep_a.id]})
        unscoped_result = await calculators.get_quota_attainment_distribution(db, {})

        assert scoped["data"]["counts"] == {"below_50": 0, "50_to_75": 0, "75_to_100": 0, "100_to_120": 0, "above_120": 1}
        assert unscoped_result["data"]["counts"]["below_50"] == 1


# ── ARR waterfall (money-path grain: per-rep running balance, additive) ─────

@pytest.mark.asyncio
async def test_calc_arr_waterfall_sums_only_scoped_reps(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team = await _make_team(db, name="Central")
        rep_a = await _make_rep(db, name="Rep A", email="a3@x.com", team=team)
        rep_b = await _make_rep(db, name="Rep B", email="b3@x.com")
        await _make_arr_entry(db, rep=rep_a, period=P1, arr_start=100000.0, arr_end=110000.0, new_logo=120000.0)
        await _make_arr_entry(db, rep=rep_b, period=P1, arr_start=900000.0, arr_end=950000.0, new_logo=600000.0)
        await db.commit()

        scoped = await calculators.calc_arr_waterfall(db, P1, rep_ids=[rep_a.id])
        unscoped_result = await calculators.calc_arr_waterfall(db, P1)

        assert scoped["arr_start"] == 100000.0
        assert scoped["arr_end"] == 110000.0
        assert unscoped_result["arr_start"] == 1000000.0  # both reps summed


@pytest.mark.asyncio
async def test_calc_arr_waterfall_series_preserves_continuity_for_scoped_subset(cleanup):
    """arr_start(period) must equal arr_end(prior period) for the SAME scoped
    subset across the whole series -- not just period by period."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a4@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b4@x.com")
        await _make_arr_entry(db, rep=rep_a, period=P1, arr_start=50000.0, arr_end=55000.0)
        await _make_arr_entry(db, rep=rep_a, period=P2, arr_start=55000.0, arr_end=62000.0)
        await _make_arr_entry(db, rep=rep_b, period=P1, arr_start=500000.0, arr_end=480000.0)
        await _make_arr_entry(db, rep=rep_b, period=P2, arr_start=480000.0, arr_end=470000.0)
        await db.commit()

        series = await calculators.calc_arr_waterfall_series(db, months=2, rep_ids=[rep_a.id])
        by_period = {s["period"]: s for s in series}

        assert by_period[P1]["arr_start"] == 50000.0
        assert by_period[P1]["arr_end"] == 55000.0
        assert by_period[P2]["arr_start"] == 55000.0
        assert by_period[P2]["arr_end"] == 62000.0
        # Rep B's much larger numbers never leaked in.
        assert by_period[P2]["arr_end"] < 100000.0


@pytest.mark.asyncio
async def test_calc_arr_waterfall_scoped_empty_does_not_leak_company_wide_fallback(cleanup):
    """A scoped rep/team with zero arr_waterfall rows for a period must not
    silently fall back to the unscoped derive-from-bookings path -- that
    would show company-wide numbers inside what's supposed to be a
    rep/team-only view."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a5@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b5@x.com")
        # Only rep_b has an arr_waterfall row for P1 -- rep_a has none.
        await _make_arr_entry(db, rep=rep_b, period=P1, arr_start=900000.0, arr_end=950000.0)
        await db.commit()

        scoped = await calculators.calc_arr_waterfall(db, P1, rep_ids=[rep_a.id])

        assert scoped["data_source"] == "no_data_for_scope"
        assert scoped["arr_start"] == 0.0
        assert scoped["arr_end"] == 0.0


# ── Router-level: /analytics/revops-kpis and /forecast/arr-waterfall ────────

@pytest.mark.asyncio
async def test_revops_kpis_endpoint_scopes_to_team_and_stays_unscoped_by_default(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team = await _make_team(db, name="Central")
        rep_a = await _make_rep(db, name="Rep A", email="a6@x.com", team=team)
        rep_b = await _make_rep(db, name="Rep B", email="b6@x.com")
        await _make_revenue(db, rep=rep_a, amount=10000.0, period=P1, revenue_type="renewal")
        await _make_revenue(db, rep=rep_b, amount=500000.0, period=P1, revenue_type="renewal")
        await db.commit()

        scoped = await analytics_router.get_revops_kpis(period=None, team_id=str(team.id), db=db)
        unscoped_result = await analytics_router.get_revops_kpis(period=None, db=db)

        assert scoped["scope"] == {"type": "team", "rep_id": None, "team_id": str(team.id), "rep_count": 1}
        assert scoped["nrr_components"]["mrr_start"] == 10000.0
        # Executive/revops_admin (no rep_id/team_id) still see everyone combined.
        assert unscoped_result["scope"] == {"type": "company", "rep_id": None, "team_id": None, "rep_count": None}
        assert unscoped_result["nrr_components"]["mrr_start"] == 510000.0


@pytest.mark.asyncio
async def test_revops_kpis_team_with_no_reps_404s(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        empty_team = await _make_team(db, name="Empty")
        await db.commit()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await analytics_router.get_revops_kpis(period=None, team_id=str(empty_team.id), db=db)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_forecast_arr_waterfall_endpoint_scopes_to_rep(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a7@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b7@x.com")
        await _make_arr_entry(db, rep=rep_a, period=P1, arr_start=20000.0, arr_end=25000.0)
        await _make_arr_entry(db, rep=rep_b, period=P1, arr_start=800000.0, arr_end=820000.0)
        await db.commit()

        result = await forecasting_router.arr_waterfall(rep_id=str(rep_a.id), db=db)

        assert result["scope"]["type"] == "rep"
        assert result["waterfall"]["arr_start"] == [20000.0]
        assert result["waterfall"]["arr_end"] == [25000.0]
