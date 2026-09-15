"""
tests/test_forecast_lab_all_types_scoping.py
================================================
_load_history_for_forecast_type (backend/routers/forecasting.py, used by
GET /ml/forecast/lab) only scoped forecast_type="revenue" to a rep/team --
every other type (ARR, pipeline/commit/best_case, booking, payout,
quota_attainment) ran company-wide even when rep_id/team_id was given,
surfacing a warning instead. Each type's own history query now accepts the
same rep_ids filter, at the same grain it already computed at.

payout is the interesting case: PayoutRecord has no rep_id -- it's keyed by
user_id (UserProfile), bridged to Rep via email (same join
backend/agent/tools/payout_tools.py's get_payout_summary already uses).
_resolve_scope_user_ids does that bridging; a rep with no matching
UserProfile contributes nothing rather than raising.

The "no series -> fall back to company-wide revenue" tail must also respect
scope -- a scoped query that finds nothing for its own type must not leak
unscoped revenue history into what's supposed to be a rep/team-only view.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import ArrWaterfallEntry, Booking, Deal, PayoutRecord, Quota, Revenue, Rep, UserProfile
from backend.routers import forecasting as forecasting_router
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-lab-scope-{uuid.uuid4().hex[:8]}"

CLEANUP_MODELS = [PayoutRecord, UserProfile, Booking, ArrWaterfallEntry, Deal, Quota, Revenue, Rep]


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


async def _make_rep(db, *, name: str, email: str) -> Rep:
    rep = Rep(name=name, email=email)
    db.add(rep)
    await db.flush()
    return rep


@pytest.mark.asyncio
async def test_arr_type_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b@x.com")
        db.add(ArrWaterfallEntry(rep_id=rep_a.id, period="2026-01", arr_end=50000.0))
        db.add(ArrWaterfallEntry(rep_id=rep_b.id, period="2026-01", arr_end=900000.0))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "ARR", rep_ids=[rep_a.id]
        )

        assert periods == ["2026-01"]
        assert values == [50000.0]
        assert not any("does not support rep/team scoping" in w for w in warnings)


@pytest.mark.asyncio
async def test_pipeline_type_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a2@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b2@x.com")
        close_date = date.today() + timedelta(days=20)
        db.add(Deal(rep_id=rep_a.id, name="A deal", stage="Negotiation", amount=10000.0,
                     close_probability=50, expected_close_date=close_date,
                     created_at=datetime.utcnow()))
        db.add(Deal(rep_id=rep_b.id, name="B deal", stage="Negotiation", amount=500000.0,
                     close_probability=50, expected_close_date=close_date,
                     created_at=datetime.utcnow()))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "pipeline", rep_ids=[rep_a.id]
        )

        assert sum(values) == 10000.0


@pytest.mark.asyncio
async def test_booking_type_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a3@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b3@x.com")
        db.add(Booking(rep_id=rep_a.id, booking_date=date(2026, 1, 15), amount=20000.0))
        db.add(Booking(rep_id=rep_b.id, booking_date=date(2026, 1, 15), amount=700000.0))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "booking", rep_ids=[rep_a.id]
        )

        assert source == "bookings"
        assert sum(values) == 20000.0


@pytest.mark.asyncio
async def test_booking_type_closed_won_fallback_scopes_to_rep_ids(cleanup):
    """When the bookings table has no rows, the Closed-Won-deal proxy must
    stay scoped too -- it must not fall back to every rep's closed deals."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a4@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b4@x.com")
        db.add(Deal(rep_id=rep_a.id, name="A won", stage="Closed Won", amount=15000.0,
                     actual_close_date=date(2026, 1, 20), created_at=datetime.utcnow()))
        db.add(Deal(rep_id=rep_b.id, name="B won", stage="Closed Won", amount=800000.0,
                     actual_close_date=date(2026, 1, 20), created_at=datetime.utcnow()))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "booking", rep_ids=[rep_a.id]
        )

        assert source == "deals.closed_won"
        assert sum(values) == 15000.0


@pytest.mark.asyncio
async def test_quota_attainment_type_scopes_to_rep_ids(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a5@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b5@x.com")
        db.add(Quota(rep_id=rep_a.id, period="2026-01", amount=100.0))
        db.add(Quota(rep_id=rep_b.id, period="2026-01", amount=100.0))
        db.add(Revenue(rep_id=rep_a.id, period="2026-01", amount=130.0))
        db.add(Revenue(rep_id=rep_b.id, period="2026-01", amount=20.0))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "quota_attainment", rep_ids=[rep_a.id]
        )

        # Rep A alone: 130/100 = 130% attainment. If Rep B leaked in, the
        # combined revenue/quota would pull this toward 75%.
        assert periods == ["2026-01"]
        assert values == [130.0]


@pytest.mark.asyncio
async def test_payout_type_bridges_rep_ids_to_user_ids_via_email(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a6@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b6@x.com")
        user_a = UserProfile(name="Rep A", email="A6@X.COM")  # case-insensitive match
        user_b = UserProfile(name="Rep B", email="b6@x.com")
        db.add_all([user_a, user_b])
        await db.flush()
        db.add(PayoutRecord(user_id=user_a.id, period="2026-01", payout_amount=5000.0))
        db.add(PayoutRecord(user_id=user_b.id, period="2026-01", payout_amount=90000.0))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "payout", rep_ids=[rep_a.id]
        )

        assert source == "payouts"
        assert sum(values) == 5000.0


@pytest.mark.asyncio
async def test_unscoped_call_is_unchanged(cleanup):
    """rep_ids=None (executive/revops_admin) must still aggregate everyone,
    exactly as before this change."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="a7@x.com")
        rep_b = await _make_rep(db, name="Rep B", email="b7@x.com")
        db.add(ArrWaterfallEntry(rep_id=rep_a.id, period="2026-01", arr_end=50000.0))
        db.add(ArrWaterfallEntry(rep_id=rep_b.id, period="2026-01", arr_end=900000.0))
        await db.commit()

        values, periods, source, warnings = await forecasting_router._load_history_for_forecast_type(
            db, "ARR", rep_ids=None
        )

        assert values == [950000.0]
