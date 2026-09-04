"""
tests/test_payout_statements_quarterly_grain.py
=================================================
GET /payout/statements/{rep_id} had no test at all. It evaluated each
month's tier/bonus against that single month's isolated attainment —
but Rule.bonus_amount and the quota/tier structure are quarterly-grain
by design (Quota rows are quarterly; the real payout engine,
credit_payout_engine.py, evaluates cumulatively per quarter). A rep whose
cumulative quarter falls short of the bonus threshold could still get a
bonus shown for the one strong month that, in isolation, cleared it —
exactly the "evaluated at the wrong grain" failure CLAUDE.md documents as
this project's costliest historical incident (misallocated commission by
judging attainment one credit/period at a time instead of cumulatively).

Confirmed live before this fix: Chelsea Butler (techo-solutions), Q2 2026 —
98.45% cumulative quarterly attainment, correctly no bonus in the real
payout record — but June 2026 alone (104.05% in isolation) showed a full
$2,000 bonus in this endpoint.

Fixed by deriving each month's commission/accelerator/bonus as that
month's revenue-weighted share of its quarter's real PayoutRecord (the
actual source of truth), falling back to a quarterly-aggregated
compute_payout() call only when no real record exists yet for that
quarter — never evaluating a single month against the tier table directly.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Rep, Revenue, Quota, Plan, Rule, PlanAssignment, UserProfile, PayoutRecord
from backend.routers.payout import rep_payout_statements
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-payout-stmt-grain-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(PayoutRecord).where(PayoutRecord.company_id == COMPANY))
        await db.execute(delete(PlanAssignment).where(PlanAssignment.company_id == COMPANY))
        await db.execute(delete(Rule).where(Rule.company_id == COMPANY))
        await db.execute(delete(Plan).where(Plan.company_id == COMPANY))
        await db.execute(delete(UserProfile).where(UserProfile.company_id == COMPANY))
        await db.execute(delete(Quota).where(Quota.company_id == COMPANY))
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


async def _seed_rep_with_plan(db, email: str):
    """One rep, one quarterly plan (3%/5%/10%+$3000-bonus tiers), and a
    quarter shaped so cumulative attainment (70%) stays well below the
    bonus threshold even though one month alone (150%) clears it."""
    rep = Rep(name="Grain Test Rep", email=email)
    db.add(rep)
    await db.flush()

    user = UserProfile(name="Grain Test Rep", email=email)
    db.add(user)
    await db.flush()

    plan = Plan(name="Grain Test Plan", scope="individual")
    db.add(plan)
    await db.flush()

    db.add(Rule(plan_id=plan.id, name="Tier 1", metric_name="attainment_pct", threshold_min=0, threshold_max=79.99, rate=0.03, bonus_amount=0))
    db.add(Rule(plan_id=plan.id, name="Tier 2", metric_name="attainment_pct", threshold_min=80, threshold_max=99.99, rate=0.05, bonus_amount=0))
    db.add(Rule(plan_id=plan.id, name="Tier 3", metric_name="attainment_pct", threshold_min=100, threshold_max=999, rate=0.10, bonus_amount=3000))

    db.add(PlanAssignment(user_id=user.id, plan_id=plan.id, effective_start_date=date(2025, 1, 1)))

    # Q1 2025: quota $30,000 (=$10,000/month). Month 1 alone is 150% of its
    # monthly slice; months 2-3 are 30% each. Quarter total: $21,000 / $30,000
    # = 70% — below every bonus tier, even though month 1 in isolation clears it.
    db.add(Quota(rep_id=rep.id, period="2025-Q1", amount=30_000))
    db.add(Revenue(rep_id=rep.id, period="2025-01", amount=15_000))
    db.add(Revenue(rep_id=rep.id, period="2025-02", amount=3_000))
    db.add(Revenue(rep_id=rep.id, period="2025-03", amount=3_000))

    await db.commit()
    return rep, user, plan


@pytest.mark.asyncio
async def test_statement_does_not_pay_a_bonus_a_quarter_never_earned(cleanup):
    """The core regression: a real PayoutRecord says Q1 2025 earned tier 1
    (3%, no bonus) on its $21,000 cumulative revenue. The statement for
    January — the one month that, alone, would have cleared the bonus
    tier — must not show a bonus, and every month's commission_rate must
    match the quarter's real rate, not a per-month re-evaluation."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user, plan = await _seed_rep_with_plan(db, "granttest1@example.com")

        real_payout_amount = round(21_000 * 0.03, 2)  # $630.00 — tier 1, no bonus
        db.add(PayoutRecord(
            user_id=user.id, plan_id=plan.id, period="2025-Q1",
            payout_amount=real_payout_amount, commission_rate=0.03, fallback_used=False,
        ))
        await db.commit()

        result = await rep_payout_statements(rep_id=rep.id, periods=3, db=db)

    statements = {s["period"]: s for s in result["statements"]}
    assert set(statements.keys()) == {"2025-01", "2025-02", "2025-03"}

    jan = statements["2025-01"]
    assert jan["attainment_pct"] == 150.0  # isolated monthly figure is still shown, informationally
    assert jan["bonus"] == 0.0             # but it must not have triggered a bonus the quarter didn't earn
    assert jan["commission_rate"] == 0.03
    assert "quarterly" in jan["tier_applied"]

    for period in ("2025-01", "2025-02", "2025-03"):
        s = statements[period]
        assert s["commission_rate"] == 0.03
        assert abs((s["commission"] + s["accelerator"] + s["bonus"]) - s["total_payout"]) < 0.01

    total_shown = sum(s["total_payout"] for s in statements.values())
    assert abs(total_shown - real_payout_amount) < 0.01


@pytest.mark.asyncio
async def test_statement_falls_back_to_quarterly_aggregate_without_a_real_record(cleanup):
    """No PayoutRecord exists yet for the quarter (e.g. it hasn't been run) —
    must still evaluate at quarterly-aggregated grain, not per-month."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user, plan = await _seed_rep_with_plan(db, "granttest2@example.com")
        # No PayoutRecord inserted this time.

        result = await rep_payout_statements(rep_id=rep.id, periods=3, db=db)

    statements = {s["period"]: s for s in result["statements"]}
    jan = statements["2025-01"]
    # Quarter-cumulative attainment is 70% -> tier 1 (3%), regardless of
    # January's own 150% -- still no bonus, and the same rate on every month.
    assert jan["bonus"] == 0.0
    assert jan["commission_rate"] == 0.03
    for period in ("2025-01", "2025-02", "2025-03"):
        assert statements[period]["commission_rate"] == 0.03
