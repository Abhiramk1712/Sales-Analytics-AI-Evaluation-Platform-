"""
tests/test_agent_self_service_tools.py
========================================
backend/agent/tools/self_service_tools.py is new: the AI Agent tab was
previously reachable only by executive/revops_admin (run_agent_workflow),
and had zero role awareness anywhere in its pipeline even for those two --
identical evidence for the same question asked as either role. sales_rep
and sales_manager now reach POST /agent/chat (view_own_metrics, already
held by both), but ToolExecutor._execute_self_service routes them through
these identity-scoped tools instead of the full company-wide tool surface
-- the point being that a sales_rep's or sales_manager's evidence pool
never contains anyone else's data in the first place, so correctness
doesn't depend on the LLM declining an out-of-scope request.

Real DB, real tenant scope, rows cleaned up per test -- same pattern as
test_payout_tools_what_if.py and test_revops_tools.py.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.agent.tools.self_service_tools import (
    get_my_payout_summary,
    get_my_performance_summary,
    get_team_payout_summary,
    get_team_performance_summary,
)
from backend.database import get_session_factory
from backend.models import Plan, PlanAssignment, Quota, Rep, Revenue, Rule, UserProfile
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-self-service-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

CLEANUP_MODELS = [PlanAssignment, Rule, Plan, Quota, Revenue, UserProfile, Rep]


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


async def _make_revenue(db, *, rep: Rep, amount: float, period: str = PERIOD) -> Revenue:
    r = Revenue(rep_id=rep.id, period=period, amount=amount)
    db.add(r)
    await db.flush()
    return r


async def _make_quota(db, *, rep: Rep, amount: float, period: str = PERIOD) -> Quota:
    q = Quota(rep_id=rep.id, period=period, amount=amount)
    db.add(q)
    await db.flush()
    return q


# ── get_my_performance_summary ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_my_performance_summary_returns_only_this_rep(cleanup):
    """Two reps in the company; asking for one by rep_id must never leak
    the other's numbers into the result."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        me = await _make_rep(db, name="Me Rep", email="me@example.com")
        other = await _make_rep(db, name="Other Rep", email="other@example.com")
        await _make_revenue(db, rep=me, amount=80_000)
        await _make_quota(db, rep=me, amount=100_000)
        await _make_revenue(db, rep=other, amount=999_000)
        await _make_quota(db, rep=other, amount=100_000)
        await db.commit()

        result = await get_my_performance_summary(db, str(me.id))

    # status may be "warning" (e.g. no Closed Won/Lost deals to compute a
    # win rate from) even though the data itself is correct and complete --
    # this test is about scoping, not about win-rate data completeness.
    assert result["data"]["name"] == "Me Rep"
    assert result["data"]["revenue"] == 80_000.0
    assert result["data"]["attainment_pct"] == 80.0


@pytest.mark.asyncio
async def test_get_my_performance_summary_unknown_rep_id_is_a_warning_not_a_crash(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        result = await get_my_performance_summary(db, str(uuid.uuid4()))

    assert result["status"] == "warning"
    assert result["data"] is None


# ── get_my_payout_summary ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_my_payout_summary_uses_own_real_plan_not_default_spiffs(cleanup):
    """Same real-plan-resolution fix as get_payout_summary
    (tests/test_payout_tools_what_if.py), applied here to the single-rep
    self-service tool: a rep assigned a real plan must get that plan's
    rate, not the default engine's synthetic SPIFFs."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Planned Self Rep", email="planned-self@example.com")
        await _make_revenue(db, rep=rep, amount=150_000)
        await _make_quota(db, rep=rep, amount=100_000)

        plan = Plan(name="Flat 10% Plan", scope="individual")
        db.add(plan)
        await db.flush()
        db.add(Rule(plan_id=plan.id, name="Flat tier", threshold_min=0, threshold_max=999, rate=0.10, bonus_amount=0))
        await db.flush()

        user = UserProfile(name="Planned Self Rep", email="planned-self@example.com")
        db.add(user)
        await db.flush()
        db.add(PlanAssignment(user_id=user.id, plan_id=plan.id))
        await db.commit()

        result = await get_my_payout_summary(db, str(rep.id))

    data = result["data"]
    assert data["payout"] == 16_000.0, "10% of $150K plus the 2% default accelerator on the $50K overage"
    assert not any("SPIFF" in r for r in data["rules_applied"])


@pytest.mark.asyncio
async def test_get_my_payout_summary_falls_back_when_unassigned(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Unassigned Self Rep", email="unassigned-self@example.com")
        await _make_revenue(db, rep=rep, amount=150_000)
        await _make_quota(db, rep=rep, amount=100_000)
        await db.commit()

        result = await get_my_payout_summary(db, str(rep.id))

    assert any("SPIFF" in r for r in result["data"]["rules_applied"]), "unassigned reps keep the default-engine fallback"


# ── get_team_performance_summary / get_team_payout_summary ────────────────

@pytest.mark.asyncio
async def test_get_team_performance_summary_scoped_to_exactly_the_given_rep_ids(cleanup):
    """A third rep exists in the company but is NOT in the team roster
    passed in -- their revenue must not be counted."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        team_a = await _make_rep(db, name="Team A", email="team-a@example.com")
        team_b = await _make_rep(db, name="Team B", email="team-b@example.com")
        outsider = await _make_rep(db, name="Outsider", email="outsider@example.com")
        await _make_revenue(db, rep=team_a, amount=50_000)
        await _make_quota(db, rep=team_a, amount=100_000)
        await _make_revenue(db, rep=team_b, amount=50_000)
        await _make_quota(db, rep=team_b, amount=100_000)
        await _make_revenue(db, rep=outsider, amount=1_000_000)
        await _make_quota(db, rep=outsider, amount=100_000)
        await db.commit()

        result = await get_team_performance_summary(db, [str(team_a.id), str(team_b.id)])

    data = result["data"]
    assert data["team_size"] == 2
    assert data["total_revenue"] == 100_000.0, "the outsider's $1M must not be counted"
    assert data["team_attainment_pct"] == 50.0
    names = {r["name"] for r in data["reps"]}
    assert names == {"Team A", "Team B"}


@pytest.mark.asyncio
async def test_get_team_performance_summary_empty_scope_is_a_warning_not_a_crash(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        result = await get_team_performance_summary(db, [])

    assert result["status"] == "warning"
    assert result["data"] is None


@pytest.mark.asyncio
async def test_get_team_payout_summary_aggregates_each_members_own_plan(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Payout Team A", email="payout-team-a@example.com")
        rep_b = await _make_rep(db, name="Payout Team B", email="payout-team-b@example.com")
        await _make_revenue(db, rep=rep_a, amount=100_000)
        await _make_quota(db, rep=rep_a, amount=100_000)
        await _make_revenue(db, rep=rep_b, amount=50_000)
        await _make_quota(db, rep=rep_b, amount=100_000)
        await db.commit()

        result = await get_team_payout_summary(db, [str(rep_a.id), str(rep_b.id)])

    data = result["data"]
    assert data["team_size"] == 2
    names = {r["name"] for r in data["rows"]}
    assert names == {"Payout Team A", "Payout Team B"}
    # Sorted by payout descending -- Rep A (100% attainment) out-earns Rep B (50%).
    assert data["rows"][0]["name"] == "Payout Team A"
