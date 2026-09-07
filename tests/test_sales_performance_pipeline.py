"""
tests/test_sales_performance_pipeline.py
==========================================
backend/agent/workflows/sales_performance_pipeline.py had no test file at
all, and had never actually completed a single run:

1. run_sales_performance_pipeline() read `timezone.utc` at both the start
   and end of every call, but only imported `datetime, date` from the
   datetime module -- every invocation raised NameError before a single
   step ran. Reachable directly from the AI Agent chat tab: any message
   matching the "sales_performance_workflow" intent (e.g. "run a full
   sales performance analysis", "revops report", "full pipeline") hit
   this, and POST /agent/workflows/sales-performance always 500'd too.

2. _step_cluster_reps() built rep dicts with keys total_revenue/quota/
   attainment/deals_won/deals_lost/win_rate, but RepClusteringModel.fit()
   (backend/ml/rep_clustering.py) requires exactly its FEATURES columns:
   attainment_pct, win_rate, avg_deal_size, pipeline_coverage,
   avg_sales_cycle, activity_rate. Four of six were simply never present
   (and "attainment" != "attainment_pct"), so the step always failed with
   KeyError('avg_sales_cycle') inside fit() -- masked until bug #1 above
   was fixed, since the pipeline never got this far before.

3. _step_cluster_reps(), like get_payout_summary/get_quota_risk_summary/
   get_rep_ramp_status (see test_payout_tools_what_if.py,
   test_revops_tools.py), pulled every Rep row with no filter for whether
   that person is actually a quota-carrying seller -- an Executive with
   their own Revenue/Quota/Deal rows would be clustered as a "rep" too.

4. _step_grade_enterprise() called EnterpriseGrader() with zero arguments,
   but EnterpriseGrader.__init__ requires a repo_root positional argument
   (see GET /grading/enterprise-readiness, backend/routers/grading.py) --
   every invocation raised TypeError.

Confirmed live against techo-solutions before this fix: POST
/agent/workflows/sales-performance returned {"detail": "Internal server
error"} (bug #1); after fixing that alone, step_results showed
cluster_reps: {"status": "failed", "error": "'avg_sales_cycle'"} (bug #2)
and grade_enterprise: {"status": "failed", "error": "EnterpriseGrader.
__init__() missing 1 required positional argument: 'repo_root'"} (bug #4),
with the CRO (Caitlin Brown) present in the clustered rows despite her
19.8% personal attainment being nothing like a real IC's performance
(bug #3). After all four fixes: all 10 steps report "ok".
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.agent.workflows.sales_performance_pipeline import (
    _step_cluster_reps,
    _step_grade_enterprise,
    run_sales_performance_pipeline,
)
from backend.database import get_session_factory
from backend.models import Activity, Deal, Position, Quota, Rep, Revenue, UserProfile
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-sales-perf-pipeline-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

CLEANUP_MODELS = [Activity, Deal, Quota, Revenue, UserProfile, Position, Rep]


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


async def _make_full_rep(db, *, name: str, email: str, closed_amount: float, quota: float) -> Rep:
    """A rep with enough closed/open deals, activities, revenue and quota
    to populate every FEATURES column _step_cluster_reps computes."""
    rep = await _make_rep(db, name=name, email=email)
    await db.flush()

    won_deal = Deal(
        rep_id=rep.id, name=f"{name} won deal", stage="Closed Won", amount=closed_amount,
        created_at=datetime(2026, 1, 1), actual_close_date=date(2026, 2, 1),
    )
    open_deal = Deal(
        rep_id=rep.id, name=f"{name} open deal", stage="Proposal", amount=closed_amount * 0.5,
        created_at=datetime(2026, 2, 15), close_probability=50,
    )
    db.add_all([won_deal, open_deal])
    await db.flush()

    db.add(Activity(deal_id=won_deal.id, rep_id=rep.id, type="call", activity_date=datetime(2026, 1, 10)))
    db.add(Activity(deal_id=open_deal.id, rep_id=rep.id, type="email", activity_date=datetime(2026, 2, 20)))
    db.add(Revenue(rep_id=rep.id, period=PERIOD, amount=closed_amount))
    db.add(Quota(rep_id=rep.id, period=PERIOD, amount=quota))
    await db.commit()
    return rep


# ── run_sales_performance_pipeline (orchestrator-level timezone bug) ──────

@pytest.mark.asyncio
async def test_pipeline_does_not_crash_on_timezone_name(cleanup):
    """The orchestrator's own two `timezone.utc` reads (start and end of
    every call) are exercised even with every step skipped -- isolating
    them from the (slower, separately-tested) individual step bodies."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        all_steps = [
            "fetch_metrics", "fetch_forecast", "evaluate_forecasting", "score_deals",
            "cluster_reps", "check_data_quality", "compute_payouts", "generate_report",
            "grade_enterprise",
        ]
        result = await run_sales_performance_pipeline(db, options={"skip_steps": all_steps})

    assert result["pipeline_version"] == "1.0"
    # resolve_period always runs unconditionally (line 414, outside maybe());
    # every other step above was skipped.
    assert result["summary"]["steps_completed"] == 1
    assert result["summary"]["elapsed_ms"] >= 0
    # A valid ISO-8601 timestamp -- raises if generated_at were ever a
    # placeholder / malformed string instead of started_at.isoformat().
    datetime.fromisoformat(result["generated_at"])


# ── _step_cluster_reps ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cluster_reps_step_succeeds_with_real_rep_data(cleanup):
    """Regression for KeyError('avg_sales_cycle'): the step must actually
    reach a clustering result, not fail, once given real reps."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        for i in range(4):
            await _make_full_rep(
                db, name=f"Rep {i}", email=f"cluster-rep-{i}@example.com",
                closed_amount=100_000 + i * 10_000, quota=120_000,
            )

        result = await _step_cluster_reps(db)

    assert result["status"] == "ok", result
    assert len(result["data"]["clusters"]) == 4
    for cluster in result["data"]["clusters"]:
        assert cluster["persona"]
        assert "avg_sales_cycle" in cluster["features"]


@pytest.mark.asyncio
async def test_cluster_reps_excludes_executive(cleanup):
    """An Executive with their own Revenue/Quota/Deal rows must not be
    clustered as a rep -- matching get_payout_summary, get_quota_risk_
    summary, and get_rep_ramp_status."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        for i in range(4):
            await _make_full_rep(
                db, name=f"IC Rep {i}", email=f"ic-cluster-{i}@example.com",
                closed_amount=100_000 + i * 10_000, quota=120_000,
            )
        exec_rep = await _make_full_rep(
            db, name="Exec Rep", email="exec-cluster@example.com",
            closed_amount=20_000, quota=1_000_000,
        )
        exec_position = Position(name="Chief Revenue Officer", level="Executive", rank=1)
        db.add(exec_position)
        await db.flush()
        db.add(UserProfile(name="Exec Rep", email="exec-cluster@example.com", position_id=exec_position.id))
        await db.commit()

        result = await _step_cluster_reps(db)

    assert result["status"] == "ok", result
    names = [c["rep_name"] for c in result["data"]["clusters"]]
    assert "Exec Rep" not in names
    assert len(names) == 4


# ── _step_grade_enterprise ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grade_enterprise_step_succeeds():
    """Regression for TypeError: EnterpriseGrader.__init__() missing 1
    required positional argument: 'repo_root'."""
    result = await _step_grade_enterprise(db=None)

    assert result["status"] == "ok", result
    assert 0 <= result["data"]["overall_score"] <= 100
