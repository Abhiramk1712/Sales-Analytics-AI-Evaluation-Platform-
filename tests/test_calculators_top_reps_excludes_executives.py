"""
tests/test_calculators_top_reps_excludes_executives.py
========================================================
backend/metrics/calculators.py's get_top_reps() (and get_underperforming_reps(),
which is built on top of it) queried every Rep row with no filter for whether
that person is actually a quota-carrying seller -- unlike
/analytics/reps/performance, /payout/team-summary, and /payout/quota-fairness,
which already exclude Executive/Leadership positions via _selling_rep_ids().

These two functions are reachable ONLY from the AI Agent
(backend/agent/tools/analytics_tools.py -> backend/agent/executor.py's
rep_performance/business_diagnostic_question/general_sales_question/
report_request intents) -- the dashboard's equivalent endpoint already
applies the filter correctly, so this bug was agent-exclusive.

Confirmed live: "which reps are underperforming?" -- one of the four
suggestion chips shown in the agent's own empty-state UI -- returned
techo-solutions' own CRO at 19.79% attainment as the single worst
"underperforming rep" in the company, ahead of the one real underperforming
manager.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.metrics.calculators import get_top_reps, get_underperforming_reps
from backend.models import Position, Quota, Rep, Revenue, UserProfile
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-top-reps-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

CLEANUP_MODELS = [Quota, Revenue, UserProfile, Position, Rep]


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


@pytest.mark.asyncio
async def test_get_top_reps_excludes_executive(cleanup):
    """An Executive with a low attainment_pct must not appear in the ranked
    rep list at all -- not just be ranked correctly."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        ic_rep = Rep(name="IC Rep", email="ic-topreps@example.com")
        exec_rep = Rep(name="Exec Rep", email="exec-topreps@example.com")
        db.add_all([ic_rep, exec_rep])
        await db.flush()

        db.add_all([
            Revenue(rep_id=ic_rep.id, period=PERIOD, amount=90_000),
            Quota(rep_id=ic_rep.id, period=PERIOD, amount=100_000),
            # Executive: real revenue/quota data, deliberately a much lower
            # attainment than the IC -- if included at all, this is the
            # scenario that put the CRO at the top of "underperforming reps".
            Revenue(rep_id=exec_rep.id, period=PERIOD, amount=100_000),
            Quota(rep_id=exec_rep.id, period=PERIOD, amount=500_000),
        ])

        ic_position = Position(name="Account Executive", level="Individual Contributor", rank=5)
        exec_position = Position(name="Chief Revenue Officer", level="Executive", rank=1)
        db.add_all([ic_position, exec_position])
        await db.flush()
        db.add_all([
            UserProfile(name="IC Rep", email="ic-topreps@example.com", position_id=ic_position.id),
            UserProfile(name="Exec Rep", email="exec-topreps@example.com", position_id=exec_position.id),
        ])
        await db.commit()

        top = await get_top_reps(db, limit=10)
        under = await get_underperforming_reps(db, threshold_pct=95)

    top_names = [r["name"] for r in top["data"]]
    under_names = [r["name"] for r in under["data"]]
    assert top_names == ["IC Rep"], "the Executive must not appear in the ranked rep list at all"
    assert under_names == ["IC Rep"], "the Executive must not appear in the underperforming list at all"
