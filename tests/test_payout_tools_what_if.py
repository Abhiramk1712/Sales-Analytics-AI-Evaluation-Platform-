"""
tests/test_payout_tools_what_if.py
====================================
backend/agent/tools/payout_tools.py had 23% coverage. The extraction helpers
(regex parsing of free-text questions) were already unit-tested in
tests/test_agent_tools.py; what wasn't touched by anything is
_find_rep_from_message, get_payout_summary, and the entire 500+ line
get_rep_quota_bonus_what_if — the agent's what-if scenario projector, the
thing a question like "what bonus does Alex earn at 110% quota?" or "what if
we cut sales cycle by 2 weeks?" actually runs. Real DB, real tenant scope,
rows cleaned up per test — same pattern as test_compute_credit_payouts.py.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.agent.tools.payout_tools import (
    _find_rep_from_message,
    get_payout_summary,
    get_rep_quota_bonus_what_if,
)
from backend.database import get_session_factory
from backend.models import Deal, Quota, Rep, Revenue
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-whatif-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

CLEANUP_MODELS = [Deal, Quota, Revenue, Rep]


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


# ── Fixture builders ──────────────────────────────────────────────────────

async def _make_rep(db, *, name: str, email: str) -> Rep:
    rep = Rep(name=name, email=email)
    db.add(rep)
    await db.flush()
    return rep


async def _make_deal(
    db, *, rep: Rep, stage: str, amount: float,
    created_at: datetime | None = None,
    expected_close_date: date | None = None,
    actual_close_date: date | None = None,
    close_probability: int | None = None,
) -> Deal:
    d = Deal(
        rep_id=rep.id, name=f"{stage} deal", stage=stage, amount=amount,
        created_at=created_at, expected_close_date=expected_close_date,
        actual_close_date=actual_close_date, close_probability=close_probability,
    )
    db.add(d)
    await db.flush()
    return d


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


# ── _find_rep_from_message ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_rep_exact_name_substring_match(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="Alex Johnson", email="alex@example.com")
        await _make_rep(db, name="Priya Shah", email="priya@example.com")
        await db.commit()

        rep, candidates, requested = await _find_rep_from_message(db, "If Alex Johnson hits 110% quota, what's the bonus?")

    assert rep is not None
    assert rep.name == "Alex Johnson"
    assert candidates == []


@pytest.mark.asyncio
async def test_find_rep_fuzzy_match_on_misspelled_name(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="Alexandra Johnston", email="alexj@example.com")
        await db.commit()

        # Close but not exact — should still resolve via fuzzy matching.
        rep, candidates, requested = await _find_rep_from_message(db, "What bonus for Alexandra Johnson at 100% quota?")

    assert rep is not None
    assert rep.name == "Alexandra Johnston"


@pytest.mark.asyncio
async def test_find_rep_no_match_returns_suggestions(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="Completely Different", email="cd@example.com")
        await db.commit()

        rep, candidates, requested = await _find_rep_from_message(db, "What bonus for Zzyzx Nowhere at 100% quota?")

    assert rep is None
    assert requested == "zzyzx nowhere"
    assert len(candidates) >= 1


@pytest.mark.asyncio
async def test_find_rep_no_reps_in_company_returns_empty(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, candidates, requested = await _find_rep_from_message(db, "What bonus for Anyone at 100% quota?")

    assert rep is None
    assert candidates == []


# ── get_payout_summary ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_payout_summary_aggregates_across_reps(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Rep A", email="repa@example.com")
        rep_b = await _make_rep(db, name="Rep B", email="repb@example.com")
        await _make_revenue(db, rep=rep_a, amount=100_000)
        await _make_quota(db, rep=rep_a, amount=100_000)
        await _make_revenue(db, rep=rep_b, amount=50_000)
        await _make_quota(db, rep=rep_b, amount=100_000)
        await db.commit()

        result = await get_payout_summary(db)

    assert result["status"] == "success"
    assert result["data"]["summary"]["rep_count"] == 2
    assert result["data"]["summary"]["total_revenue"] == 150_000.0
    assert result["data"]["summary"]["total_quota"] == 200_000.0
    # Sorted by payout descending — Rep A (100% attainment) should out-earn Rep B (50%).
    assert result["data"]["rows"][0]["name"] == "Rep A"


@pytest.mark.asyncio
async def test_payout_summary_period_prefix_filters_correctly(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Quarterly Rep", email="q@example.com")
        await _make_revenue(db, rep=rep, amount=10_000, period="2026-01")
        await _make_revenue(db, rep=rep, amount=20_000, period="2026-02")
        await _make_revenue(db, rep=rep, amount=999_999, period="2025-12")  # outside prefix
        await db.commit()

        result = await get_payout_summary(db, period_prefix="2026")

    assert result["data"]["summary"]["total_revenue"] == 30_000.0


@pytest.mark.asyncio
async def test_payout_summary_flags_fallback_reps(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="No Quota Rep", email="noquota@example.com")
        # No revenue, no quota rows at all -> compute_payout's fallback path.
        await db.commit()

        result = await get_payout_summary(db)

    assert result["data"]["summary"]["fallback_count"] >= 1
    assert any("fallback" in w for w in result["warnings"])


# ── get_rep_quota_bonus_what_if: no rep identified ────────────────────────

@pytest.mark.asyncio
async def test_no_rep_no_drivers_returns_generic_guidance(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="Someone Else", email="se@example.com")
        await db.commit()

        result = await get_rep_quota_bonus_what_if(db, "what would it take to hit quota?")

    assert result["status"] == "warning"
    assert result["data"]["team_scenario"] is None
    assert any("Rep name not detected" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_no_rep_but_drivers_present_computes_team_scenario(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a = await _make_rep(db, name="Team Rep A", email="ta@example.com")
        rep_b = await _make_rep(db, name="Team Rep B", email="tb@example.com")
        for rep in (rep_a, rep_b):
            await _make_revenue(db, rep=rep, amount=50_000)
            await _make_quota(db, rep=rep, amount=100_000)
            await _make_deal(db, rep=rep, stage="Closed Won", amount=50_000)
        await db.commit()

        # No rep name, but a real driver ("pipeline up 20%") -> team-level scenario.
        result = await get_rep_quota_bonus_what_if(db, "what if pipeline grows by 20% across the team?")

    scenario = result["data"]["team_scenario"]
    assert scenario is not None
    assert scenario["baseline"]["revenue"] == 100_000.0  # rep_a + rep_b
    assert scenario["baseline"]["quota"] == 200_000.0
    assert scenario["projected"]["open_pipeline"] >= scenario["baseline"]["open_pipeline"]


# ── get_rep_quota_bonus_what_if: rep identified ───────────────────────────

@pytest.mark.asyncio
async def test_rep_already_at_target_gets_no_gap_message(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Overachiever Rep", email="over@example.com")
        await _make_revenue(db, rep=rep, amount=150_000)
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_deal(db, rep=rep, stage="Closed Won", amount=150_000)
        await db.commit()

        result = await get_rep_quota_bonus_what_if(db, "If Overachiever Rep hits 100% quota, what's the bonus?")

    data = result["data"]
    assert data["matched_rep"]["name"] == "Overachiever Rep"
    assert data["quota_target_scenario"]["gap_to_target"] == 0.0
    assert any("already at or above" in a for a in data["action_plan"])


@pytest.mark.asyncio
async def test_rep_below_target_computes_real_gap_and_deals_needed(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Below Target Rep", email="below@example.com")
        await _make_revenue(db, rep=rep, amount=40_000)
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_deal(db, rep=rep, stage="Closed Won", amount=20_000)
        await _make_deal(db, rep=rep, stage="Closed Won", amount=20_000)
        # Open pipeline: two $50k deals, one overdue.
        await _make_deal(
            db, rep=rep, stage="Proposal", amount=50_000,
            created_at=datetime.utcnow() - timedelta(days=30),
            expected_close_date=date.today() - timedelta(days=5),
            close_probability=40,
        )
        await _make_deal(
            db, rep=rep, stage="Negotiation", amount=50_000,
            created_at=datetime.utcnow() - timedelta(days=10),
            expected_close_date=date.today() + timedelta(days=20),
            close_probability=60,
        )
        await db.commit()

        result = await get_rep_quota_bonus_what_if(db, "If Below Target Rep hits 100% quota, what bonus do they earn?")

    data = result["data"]
    assert data["current_state"]["revenue"] == 40_000.0
    assert data["current_state"]["open_pipeline"] == 100_000.0
    assert data["current_state"]["overdue_open_deals"] == 1
    assert data["current_state"]["slip_risk_pct"] == 50.0  # 1 of 2 open deals overdue
    # target 100% of $100k quota, at $40k revenue -> $60k gap.
    assert data["quota_target_scenario"]["target_revenue"] == 100_000.0
    assert data["quota_target_scenario"]["gap_to_target"] == 60_000.0


@pytest.mark.asyncio
async def test_close_rate_driver_scenario_projects_higher_win_rate(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Driver Scenario Rep", email="driver@example.com")
        await _make_revenue(db, rep=rep, amount=50_000)
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_deal(db, rep=rep, stage="Closed Won", amount=50_000)
        await _make_deal(db, rep=rep, stage="Closed Lost", amount=30_000)
        await _make_deal(
            db, rep=rep, stage="Proposal", amount=80_000,
            created_at=datetime.utcnow() - timedelta(days=20),
            expected_close_date=date.today() + timedelta(days=30),
            close_probability=50,
        )
        await db.commit()

        result = await get_rep_quota_bonus_what_if(
            db, "If Driver Scenario Rep improves close rate by 20%, what's the projected payout?",
        )

    data = result["data"]
    driver = data["driver_scenario"]
    # Win rate should have moved up from baseline, not stayed flat.
    assert driver["projected"]["win_rate_pct"] > driver["baseline"]["win_rate_pct"]
    assert any("close-rate lift" in a for a in data["action_plan"])
    # Payout is a real compute_payout() call, not hand-waved — projected should
    # be >= baseline given win rate only ever improves in this scenario.
    assert driver["projected"]["payout"] >= driver["baseline"]["payout"]


@pytest.mark.asyncio
async def test_win_rate_inferred_from_open_deal_probabilities_when_no_closed_history(cleanup):
    """No Closed Won/Lost deals at all -> win_rate falls back to averaging
    open-deal close_probability, with a warning saying so."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Sparse History Rep", email="sparse@example.com")
        await _make_quota(db, rep=rep, amount=50_000)
        await _make_deal(
            db, rep=rep, stage="Proposal", amount=40_000,
            created_at=datetime.utcnow() - timedelta(days=15),
            expected_close_date=date.today() + timedelta(days=15),
            close_probability=60,
        )
        await db.commit()

        result = await get_rep_quota_bonus_what_if(db, "If Sparse History Rep hits quota, what's the bonus?")

    assert any("Win rate inferred" in w for w in result["warnings"])
    assert result["data"]["current_state"]["win_rate"] == 60.0


@pytest.mark.asyncio
async def test_driver_projection_capped_at_200_percent_quota(cleanup):
    """An absurdly large pipeline lift should be capped, not extrapolated
    without bound — and the cap must produce the documented warning."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Capped Rep", email="capped@example.com")
        await _make_revenue(db, rep=rep, amount=90_000)
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_deal(db, rep=rep, stage="Closed Won", amount=90_000)
        await _make_deal(
            db, rep=rep, stage="Proposal", amount=500_000,
            created_at=datetime.utcnow() - timedelta(days=5),
            expected_close_date=date.today() + timedelta(days=25),
            close_probability=90,
        )
        await db.commit()

        result = await get_rep_quota_bonus_what_if(
            db, "If Capped Rep improves win rate to 100% and pipeline grows by 500%, what's the payout?",
        )

    data = result["data"]
    # 200% of $100k quota = $200k ceiling on the driver projection.
    assert data["driver_scenario"]["projected"]["projected_revenue"] <= 200_000.0
    assert any("capped at 200% quota" in w for w in result["warnings"])
