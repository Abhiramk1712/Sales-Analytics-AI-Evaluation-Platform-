"""
tests/test_forecasting_rep_team_scoping.py
============================================
Revenue Intelligence (Forecast, Pipeline Health) was always company-wide,
even for sales_rep/sales_manager -- there was no way to see "my" or "my
team's" predictions. /forecast/revenue, /forecast/lab (forecast_type
"revenue"), and /score/deal-slip now accept optional rep_id/team_id query
params, resolved via _resolve_scope_rep_ids (rep_id -> one rep, team_id ->
that team's roster, neither -> unscoped/company-wide, unchanged from before).

/score/deal-slip is deliberately NOT scoped for the model.fit() call --
fitting on a single rep's often-tiny deal count produces an uncalibrated
model (the same lesson from the AI agent's deal-slip bug hunt this session:
a uniform synthesized label across a small sample yields a
non-discriminating classifier). The model always fits on the full
company's deals and only the returned/scored subset is scoped.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Deal, Revenue, Team, Rep
from backend.routers import forecasting as forecasting_router
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-forecast-scope-{uuid.uuid4().hex[:8]}"
P1 = "2026-01"
P2 = "2026-02"

CLEANUP_MODELS = [Deal, Revenue, Rep, Team]


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


async def _make_revenue(db, *, rep: Rep, amount: float, period: str) -> Revenue:
    r = Revenue(rep_id=rep.id, period=period, amount=amount)
    db.add(r)
    await db.flush()
    return r


async def _make_deal(db, *, rep: Rep, days_overdue: int) -> Deal:
    now = datetime.utcnow()
    d = Deal(
        rep_id=rep.id,
        name="Overdue deal",
        stage="Negotiation",
        amount=50000.0,
        close_probability=40,
        created_at=now - timedelta(days=90 + days_overdue),
        expected_close_date=(now - timedelta(days=days_overdue)).date(),
    )
    db.add(d)
    await db.flush()
    return d


@pytest.mark.asyncio
async def test_revenue_forecast_scopes_to_single_rep(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team = await _make_team(db, name="Central")
        rep_a = await _make_rep(db, name="Rep A", email="a@x.com", team=team)
        rep_b = await _make_rep(db, name="Rep B", email="b@x.com", team=team)
        await _make_revenue(db, rep=rep_a, amount=1000.0, period=P1)
        await _make_revenue(db, rep=rep_a, amount=1100.0, period=P2)
        await _make_revenue(db, rep=rep_b, amount=9000.0, period=P1)
        await _make_revenue(db, rep=rep_b, amount=9500.0, period=P2)
        await db.commit()

        result = await forecasting_router.revenue_forecast(rep_id=str(rep_a.id), db=db)

        assert result["scope"] == {"type": "rep", "rep_id": str(rep_a.id), "team_id": None, "rep_count": 1}
        assert result["historical"][P1] == 1000.0
        assert result["historical"][P2] == 1100.0
        assert 9000.0 not in result["historical"].values()


@pytest.mark.asyncio
async def test_revenue_forecast_scopes_to_team_roster(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team_a = await _make_team(db, name="Central")
        team_b = await _make_team(db, name="West")
        rep_a1 = await _make_rep(db, name="Rep A1", email="a1@x.com", team=team_a)
        rep_a2 = await _make_rep(db, name="Rep A2", email="a2@x.com", team=team_a)
        rep_b1 = await _make_rep(db, name="Rep B1", email="b1@x.com", team=team_b)
        await _make_revenue(db, rep=rep_a1, amount=1000.0, period=P1)
        await _make_revenue(db, rep=rep_a2, amount=500.0, period=P1)
        await _make_revenue(db, rep=rep_b1, amount=9000.0, period=P1)
        await db.commit()

        result = await forecasting_router.revenue_forecast(team_id=str(team_a.id), db=db)

        assert result["scope"] == {"type": "team", "rep_id": None, "team_id": str(team_a.id), "rep_count": 2}
        assert result["historical"][P1] == 1500.0


@pytest.mark.asyncio
async def test_revenue_forecast_unscoped_still_aggregates_everyone(cleanup):
    """Executive/revops_admin behavior must be unchanged: no rep_id/team_id
    means every rep's revenue is included, same as before this feature."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a2@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b2@x.com")
        await _make_revenue(db, rep=rep_a, amount=1000.0, period=P1)
        await _make_revenue(db, rep=rep_b, amount=9000.0, period=P1)
        await db.commit()

        result = await forecasting_router.revenue_forecast(db=db)

        assert result["scope"] == {"type": "company", "rep_id": None, "team_id": None, "rep_count": None}
        assert result["historical"][P1] == 10000.0


@pytest.mark.asyncio
async def test_revenue_forecast_team_with_no_reps_404s(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        empty_team = await _make_team(db, name="Empty")
        await db.commit()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await forecasting_router.revenue_forecast(team_id=str(empty_team.id), db=db)
        assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_deal_slip_scopes_returned_deals_but_fits_on_full_company(cleanup, monkeypatch):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team = await _make_team(db, name="Central")
        rep_a = await _make_rep(db, name="Rep A", email="a3@x.com", team=team)
        rep_b = await _make_rep(db, name="Rep B", email="b3@x.com", team=team)
        await _make_deal(db, rep=rep_a, days_overdue=30)
        await _make_deal(db, rep=rep_b, days_overdue=45)
        await _make_deal(db, rep=rep_b, days_overdue=60)
        await db.commit()

        fit_calls = []
        predict_calls = []

        class _FakeModel:
            def fit(self, deals_df, activities_df):
                fit_calls.append(len(deals_df))
                return self

            def predict(self, deals_df, activities_df):
                predict_calls.append(len(deals_df))
                return []

        monkeypatch.setattr(forecasting_router, "DealSlipModel", _FakeModel)

        result = await forecasting_router.deal_slip_risk(rep_id=str(rep_a.id), db=db)

        # fit() saw all 3 deals (company-wide calibration); predict() saw
        # only rep_a's 1 deal (the scoped subset actually returned).
        assert fit_calls == [3]
        assert predict_calls == [1]
        assert result["scope"] == {"type": "rep", "rep_id": str(rep_a.id), "team_id": None, "rep_count": 1}
