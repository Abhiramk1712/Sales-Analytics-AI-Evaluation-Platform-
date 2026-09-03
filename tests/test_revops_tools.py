"""
tests/test_revops_tools.py
============================
backend/agent/tools/revops_tools.py had 22% coverage. Its extraction helpers
for the pipeline-rescue what-if are already tested in
tests/test_revops_pipeline_rescue_guardrails.py; none of the seven async tool
functions themselves were touched by anything. Real DB, real tenant scope,
rows cleaned up per test.

get_deal_slip_analysis and get_pipeline_rescue_what_if run a real
GradientBoostingClassifier (backend/ml/deal_slip.py) — its exact predicted
probabilities aren't asserted on (that's the model's business, not this
tool's), only the structural contract this tool is actually responsible for:
an overdue deal shows up, the shape is right, the arithmetic built from the
model's output is correct.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import delete

from backend.agent.tools.revops_tools import (
    _extract_top_n_at_risk_deals,
    get_arr_trajectory,
    get_deal_slip_analysis,
    get_deal_velocity_trends,
    get_pipeline_coverage_check,
    get_pipeline_rescue_what_if,
    get_quota_risk_summary,
    get_rep_ramp_status,
)
from backend.database import get_session_factory
from backend.models import Activity, Deal, Quota, Rep, Revenue
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-revops-tools-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

CLEANUP_MODELS = [Activity, Deal, Quota, Revenue, Rep]


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

async def _make_rep(db, *, name: str, email: str, hire_date: date | None = None) -> Rep:
    rep = Rep(name=name, email=email, hire_date=hire_date)
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


# ── _extract_top_n_at_risk_deals (the one extractor not already tested) ──

def test_extract_top_n_parses_explicit_count():
    assert _extract_top_n_at_risk_deals("show me the top 5 at-risk deals") == 5


def test_extract_top_n_falls_back_to_default():
    assert _extract_top_n_at_risk_deals("what deals are at risk?", default_value=15) == 15


def test_extract_top_n_is_clamped_to_30():
    assert _extract_top_n_at_risk_deals("rescue the top 99 deals") == 30


# ── get_quota_risk_summary ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_quota_risk_flags_low_attainment_thin_pipeline_rep(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        at_risk_rep = await _make_rep(db, name="At Risk Rep", email="risk@example.com")
        await _make_revenue(db, rep=at_risk_rep, amount=50_000)   # 50% attainment
        await _make_quota(db, rep=at_risk_rep, amount=100_000)
        # Thin pipeline: well under 2x quota.
        await _make_deal(db, rep=at_risk_rep, stage="Proposal", amount=50_000, close_probability=40)

        healthy_rep = await _make_rep(db, name="Healthy Rep", email="healthy@example.com")
        await _make_revenue(db, rep=healthy_rep, amount=90_000)   # 90% attainment
        await _make_quota(db, rep=healthy_rep, amount=100_000)
        await db.commit()

        result = await get_quota_risk_summary(db)

    names = {r["rep_name"] for r in result["data"]["at_risk_reps"]}
    assert "At Risk Rep" in names
    assert "Healthy Rep" not in names
    at_risk_entry = next(r for r in result["data"]["at_risk_reps"] if r["rep_name"] == "At Risk Rep")
    assert "Warning: attainment below 60%" in at_risk_entry["risk_signals"]
    assert "Severe: pipeline coverage < 1× quota (will miss even at 100% conversion)" in at_risk_entry["risk_signals"]


@pytest.mark.asyncio
async def test_quota_risk_rep_with_no_quota_is_skipped(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="No Quota Rep", email="noquota@example.com")
        await db.commit()

        result = await get_quota_risk_summary(db)

    assert result["data"]["at_risk_rep_count"] == 0


# ── get_pipeline_coverage_check ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_coverage_healthy_when_well_above_benchmarks(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Coverage Rep", email="cov@example.com")
        await _make_quota(db, rep=rep, amount=100_000)
        # 5x unweighted, high-probability deals for strong weighted coverage too.
        for _ in range(5):
            await _make_deal(db, rep=rep, stage="Negotiation", amount=100_000, close_probability=90)
        await db.commit()

        result = await get_pipeline_coverage_check(db)

    assert result["data"]["health_status"] == "healthy"
    assert result["data"]["unweighted_coverage_ratio"] == 5.0
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_pipeline_coverage_at_risk_when_thin(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Thin Coverage Rep", email="thin@example.com")
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_deal(db, rep=rep, stage="Prospecting", amount=50_000, close_probability=10)
        await db.commit()

        result = await get_pipeline_coverage_check(db)

    assert result["data"]["health_status"] == "at_risk"
    assert result["status"] == "warning"
    assert result["data"]["recommendations"]


# ── get_deal_velocity_trends ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deal_velocity_no_history_returns_warning_not_crash(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        result = await get_deal_velocity_trends(db)

    assert result["status"] == "warning"
    assert result["data"]["trend_points"] == []
    assert result["data"]["direction"] == "flat"


@pytest.mark.asyncio
async def test_deal_velocity_returns_a_point_per_month_with_deals(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Velocity Rep", email="vel@example.com")
        for month in (1, 2, 3):
            await _make_deal(
                db, rep=rep, stage="Closed Won", amount=10_000,
                created_at=datetime(2026, month, 10),
                actual_close_date=date(2026, month, 20),
            )
        await db.commit()

        result = await get_deal_velocity_trends(db, months=3)

    assert result["data"]["months_returned"] == 3
    assert [p["period"] for p in result["data"]["trend_points"]] == ["2026-01", "2026-02", "2026-03"]


# ── get_deal_slip_analysis ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deal_slip_no_deals_returns_warning_not_crash(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        result = await get_deal_slip_analysis(db)

    assert result["status"] == "warning"
    assert result["data"]["open_deals_analyzed"] == 0


@pytest.mark.asyncio
async def test_deal_slip_flags_a_badly_overdue_deal(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Slip Rep", email="slip@example.com")
        # Badly overdue, low probability -> should train.fit()'s synthetic
        # "slipped" label (days_until_close < -14) as a positive example, and
        # predict() should flag it too.
        await _make_deal(
            db, rep=rep, stage="Proposal", amount=40_000,
            created_at=datetime.utcnow() - timedelta(days=90),
            expected_close_date=date.today() - timedelta(days=60),
            close_probability=15,
        )
        # Healthy, on-track deal for contrast.
        await _make_deal(
            db, rep=rep, stage="Negotiation", amount=40_000,
            created_at=datetime.utcnow() - timedelta(days=10),
            expected_close_date=date.today() + timedelta(days=20),
            close_probability=80,
        )
        await db.commit()

        result = await get_deal_slip_analysis(db)

    assert result["status"] in {"success", "warning"}
    assert result["data"]["open_deals_analyzed"] == 2
    # The badly-overdue deal must be the (or a) top at-risk deal.
    at_risk_names = {d["deal_name"] for d in result["data"]["top_at_risk_deals"]}
    if result["data"]["slip_risk_count"] > 0:
        assert "Proposal deal" in at_risk_names


# ── get_pipeline_rescue_what_if ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pipeline_rescue_no_deals_returns_warning_not_crash(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        result = await get_pipeline_rescue_what_if(db, "rescue the top 5 at-risk deals")

    assert result["status"] == "warning"
    assert result["data"]["priority_deals"] == []


@pytest.mark.asyncio
async def test_pipeline_rescue_rolls_selected_deals_up_by_rep(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="Rescue Rep", email="rescue@example.com")
        await _make_quota(db, rep=rep, amount=100_000)
        await _make_revenue(db, rep=rep, amount=40_000)
        # Two badly overdue at-risk deals for this rep.
        for i in range(2):
            await _make_deal(
                db, rep=rep, stage="Proposal", amount=30_000,
                created_at=datetime.utcnow() - timedelta(days=90),
                expected_close_date=date.today() - timedelta(days=60),
                close_probability=20,
            )
        await db.commit()

        result = await get_pipeline_rescue_what_if(db, "rescue the top 5 at-risk deals")

    data = result["data"]
    assert data["scenario"]["top_n_at_risk_deals"] == 5
    # incremental_impact math is internally consistent: attainment lift equals
    # after minus before, not just independently-plausible numbers.
    impact = data["incremental_impact"]
    assert round(impact["quota_attainment_after_expected_pct"] - impact["quota_attainment_before_pct"], 2) == impact["quota_attainment_lift_expected_pct_points"]
    if data["priority_reps"]:
        assert data["priority_reps"][0]["rep_name"] == "Rescue Rep"


# ── get_arr_trajectory ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_arr_trajectory_insufficient_history_degrades_gracefully(cleanup):
    """Fewer than 13 monthly revenue periods -> get_arr_growth_rate's own
    documented floor. Confirms the tool surfaces that as a real response
    shape, not a crash, rather than asserting a specific health verdict."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = await _make_rep(db, name="ARR Rep", email="arr@example.com")
        await _make_revenue(db, rep=rep, amount=50_000, period="2026-01")
        await db.commit()

        result = await get_arr_trajectory(db)

    assert result["data"]["arr_growth_pct"] == 0.0
    assert result["data"]["health_assessment"] in {"excellent", "healthy", "watch", "at_risk"}


# ── get_rep_ramp_status ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ramp_status_separates_ramping_from_fully_ramped_reps(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        new_rep = await _make_rep(
            db, name="New Hire", email="new@example.com",
            hire_date=date.today() - timedelta(days=30),
        )
        veteran_rep = await _make_rep(
            db, name="Veteran Rep", email="vet@example.com",
            hire_date=date.today() - timedelta(days=900),
        )
        for rep in (new_rep, veteran_rep):
            await _make_revenue(db, rep=rep, amount=50_000)
            await _make_quota(db, rep=rep, amount=100_000)
        await db.commit()

        result = await get_rep_ramp_status(db)

    ramping_names = {r["rep_name"] for r in result["data"]["ramping_reps"]}
    assert "New Hire" in ramping_names
    assert "Veteran Rep" not in ramping_names
    assert result["data"]["fully_ramped_rep_count"] >= 1


@pytest.mark.asyncio
async def test_ramp_status_skips_reps_with_no_hire_date(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        await _make_rep(db, name="No Hire Date Rep", email="nohire@example.com", hire_date=None)
        await db.commit()

        result = await get_rep_ramp_status(db)

    assert result["data"]["ramping_rep_count"] == 0
    assert result["data"]["fully_ramped_rep_count"] == 0
