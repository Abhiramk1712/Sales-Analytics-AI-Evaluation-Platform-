"""
tests/test_compute_credit_payouts.py
=====================================
GRAIN: compute_credit_payouts is the async DB-orchestrating entrypoint —
plan/cascade resolution, the true-credit path vs. the rep-level and
revenue-aggregate fallbacks, accelerators/spiffs/clawbacks, and pro-rata
allocation back across a rep's credits. Its pure helpers (allocate_pro_rata,
_apply_commission_rules, apply_accelerators/spiffs/clawbacks) already have
direct unit tests in test_payout_engine.py and test_payout_attainment_grain.py;
this file is about the orchestration around them, which had 0% coverage —
confirmed via `pytest --cov=backend.payout.credit_payout_engine`, 0/226 lines
of compute_credit_payouts itself ever executed by any test in the suite.

Every scenario here asserts the SAME invariant this module's own comments
describe as the reason it exists: attainment, and everything computed from it,
is a period-cumulative fact — computed once from every credit's amount
summed, then allocated back proportionally — never evaluated per credit row.
That's the CORR-1 regression (see CLAUDE.md), reproduced here at the
orchestration grain rather than the helper grain.

Real DB, real tenant scope (backend/tenancy.py), rows cleaned up per test.
"""
from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import delete

from sqlalchemy import select

from backend.database import get_session_factory
from backend.models import (
    Deal, Manager, PayoutRecord, Plan, PlanAssignment, PlanCascadeRule, Position,
    Quota, Rep, Revenue, Rule, SalesCredit, SalesUnit, UserProfile,
)
from backend.payout.credit_payout_engine import compute_credit_payouts, persist_payout_records
from backend.payout.engine import DEFAULT_PAYOUT_CONFIG, ClawbackRule, CommissionTier, PayoutConfig, SpiffRule
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-credit-payout-{uuid.uuid4().hex[:8]}"
PERIOD = "2026-03"

# Deletion order respects FK dependency: children before the parents they
# reference (SalesCredit -> SalesUnit/UserProfile, PlanCascadeRule/
# PlanAssignment/Rule -> Plan, Manager -> UserProfile, Quota/Revenue/Deal -> Rep,
# PayoutRecord -> UserProfile/Plan).
CLEANUP_MODELS = [
    PayoutRecord, SalesCredit, SalesUnit, PlanCascadeRule, PlanAssignment, Rule, Plan,
    Manager, Quota, Revenue, Deal, UserProfile, Position, Rep,
]


@pytest.fixture(autouse=True)
async def fresh_engine(db_schema):
    """Same reasoning as test_tenancy_enforcement.py's fixture of the same name:
    the async engine caches connections against the event loop that created
    them, and pytest-asyncio gives each test a new loop."""
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

async def _make_rep_and_user(db, *, name: str, email: str, rank: int = 5) -> tuple[Rep, UserProfile]:
    """A Rep and a UserProfile sharing an email — the only link between them
    (see _get_user_id_for_rep: matched by email, no FK)."""
    position = Position(name=f"{name}'s position", rank=rank)
    db.add(position)
    await db.flush()

    rep = Rep(name=name, email=email)
    user = UserProfile(name=name, email=email, position_id=position.id)
    db.add(rep)
    db.add(user)
    await db.flush()
    return rep, user


async def _make_plan_with_rule(
    db, *, name: str, rate: float, threshold_min: float = 0.0, threshold_max: float | None = None,
    metric_name: str = "attainment_pct",
) -> tuple[Plan, Rule]:
    plan = Plan(name=name, scope="individual")
    db.add(plan)
    await db.flush()
    rule = Rule(
        plan_id=plan.id, name=f"{name} rule", metric_name=metric_name,
        threshold_min=threshold_min, threshold_max=threshold_max, rate=rate,
    )
    db.add(rule)
    await db.flush()
    return plan, rule


async def _assign_plan_directly(db, user: UserProfile, plan: Plan) -> None:
    db.add(PlanAssignment(user_id=user.id, plan_id=plan.id))
    await db.flush()


async def _make_credit(db, *, user: UserProfile, amount: float, booked: date, credit_type: str = "primary") -> SalesCredit:
    unit = SalesUnit(booked_date=booked, amount=amount)
    db.add(unit)
    await db.flush()
    credit = SalesCredit(
        sales_unit_id=unit.id, user_id=user.id, credit_type=credit_type,
        credit_percent=1.0, credit_amount=amount,
    )
    db.add(credit)
    await db.flush()
    return credit


async def _make_quota(db, *, rep: Rep, amount: float, period: str = PERIOD) -> Quota:
    q = Quota(rep_id=rep.id, period=period, amount=amount)
    db.add(q)
    await db.flush()
    return q


async def _make_deal(db, *, rep: Rep, stage: str, closed: date) -> Deal:
    d = Deal(rep_id=rep.id, name=f"{stage} deal", stage=stage, amount=0,
              actual_close_date=closed)
    db.add(d)
    await db.flush()
    return d


# ── Scenario A: no resolvable plan -> rep-level fallback ─────────────────

@pytest.mark.asyncio
async def test_rep_with_no_plan_falls_back_to_rep_level_estimate(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="No Plan Rep", email="noplan@example.com")
        await _make_quota(db, rep=rep, amount=100_000)
        db.add(Revenue(rep_id=rep.id, period=PERIOD, amount=90_000))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 1
    result = results[0]
    assert result.fallback_mode == "rep_level_estimate"
    assert result.credited_amount == 90_000
    assert result.quota == 100_000
    assert any("FALLBACK" in w for w in result.warnings)


# ── Scenario B: plan exists, no SalesCredit rows -> revenue-aggregate fallback ──

@pytest.mark.asyncio
async def test_plan_but_no_credit_rows_falls_back_to_revenue_aggregate(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Revenue Only Rep", email="revonly@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 5%", rate=0.05, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=100_000)
        db.add(Revenue(rep_id=rep.id, period=PERIOD, amount=80_000))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 1
    result = results[0]
    assert result.fallback_mode == "no_credit_rows"
    assert result.credited_amount == 80_000
    # Real plan rule was used (not the deterministic PayoutEngine fallback):
    # 80,000 * 5% = 4,000 exactly.
    assert result.base_commission == 4_000.0
    assert result.confidence == "high"


# ── Scenario C: true credit-level path, multiple credits, period-cumulative attainment ──

@pytest.mark.asyncio
async def test_multiple_credits_are_evaluated_at_cumulative_attainment_not_per_credit(cleanup):
    """
    The CORR-1 regression at the orchestration grain: a rule that only fires
    above 100% attainment must fire when credits summed together cross that
    threshold, even though no single credit does on its own.
    """
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Multi Credit Rep", email="multicredit@example.com")
        plan = Plan(name="Tiered plan", scope="individual")
        db.add(plan)
        await db.flush()
        # Only fires at or above 100% cumulative attainment.
        db.add(Rule(plan_id=plan.id, name="over-quota tier", metric_name="attainment_pct",
                     threshold_min=100.0, threshold_max=999.0, rate=0.10))
        await db.flush()
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=100_000)

        # Four credits of $30k each = $120k total = 120% of a $100k quota.
        # No single credit ($30k = 30%) would ever cross the 100% threshold alone.
        for _ in range(4):
            await _make_credit(db, user=user, amount=30_000, booked=date(2026, 3, 15))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 4
    assert all(r.fallback_mode == "none" for r in results)
    assert all(r.attainment == 120.0 for r in results)  # every row sees the SAME period attainment
    # 120,000 * 10% = 12,000 total base commission, allocated pro-rata across 4
    # equal credits -> 3,000 each.
    assert sum(r.base_commission for r in results) == pytest.approx(12_000.0, abs=0.01)
    for r in results:
        assert r.base_commission == pytest.approx(3_000.0, abs=0.01)
    # Sanity: the fallback tier (0-80%, rate would need testing separately) never
    # applied here — confirms the rule that fired is the 100%+ tier, not a
    # per-credit evaluation where each $30k credit alone (30% attainment) would
    # have matched no rule at all and paid $0.
    assert sum(r.base_commission for r in results) > 0


@pytest.mark.asyncio
async def test_credit_shares_are_proportional_to_each_credits_own_amount(cleanup):
    """Unequal credits split the period total in proportion to what each contributed,
    not evenly — allocate_pro_rata's contract, exercised through the real orchestration."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Uneven Credit Rep", email="uneven@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=100_000)

        await _make_credit(db, user=user, amount=75_000, booked=date(2026, 3, 5))
        await _make_credit(db, user=user, amount=25_000, booked=date(2026, 3, 20))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 2
    by_amount = {r.credited_amount: r for r in results}
    # Total base commission: 100,000 * 10% = 10,000, split 75/25.
    assert by_amount[75_000.0].base_commission == pytest.approx(7_500.0, abs=0.01)
    assert by_amount[25_000.0].base_commission == pytest.approx(2_500.0, abs=0.01)
    # Reconciles exactly to the cent — this is allocate_pro_rata's whole point.
    assert sum(r.base_commission for r in results) == 10_000.0


@pytest.mark.asyncio
async def test_credit_booked_outside_the_requested_period_is_excluded(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Wrong Month Rep", email="wrongmonth@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 5%", rate=0.05, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=50_000)

        await _make_credit(db, user=user, amount=10_000, booked=date(2026, 2, 15))  # February, not March
        # No revenue row either -> falls all the way to rep-level fallback if
        # no credits match, since get_credit_level_payout_inputs returns [].
        db.add(Revenue(rep_id=rep.id, period=PERIOD, amount=0))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 1
    # The Feb credit doesn't count toward March -> falls through to the
    # revenue-aggregate branch (a plan was resolved, just no matching credits).
    assert results[0].fallback_mode == "no_credit_rows"
    assert results[0].credited_amount == 0.0


# ── Scenario D: plan resolved via manager-chain cascade, not direct assignment ──

@pytest.mark.asyncio
async def test_plan_resolves_via_cascade_rule_through_manager_chain(cleanup):
    """No PlanAssignment for the rep at all — the plan must come from a
    PlanCascadeRule owned by their manager, walked via the Manager table."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        manager_rep, manager_user = await _make_rep_and_user(
            db, name="Manager", email="manager@example.com", rank=4,
        )
        ic_rep, ic_user = await _make_rep_and_user(
            db, name="IC Report", email="ic@example.com", rank=5,
        )
        db.add(Manager(user_id=ic_user.id, manager_user_id=manager_user.id))
        await db.flush()

        plan = Plan(name="Cascaded team plan", scope="team", owner_user_id=manager_user.id)
        db.add(plan)
        await db.flush()
        db.add(Rule(plan_id=plan.id, name="cascade rule", metric_name="attainment_pct",
                     threshold_min=0.0, threshold_max=999.0, rate=0.07))
        db.add(PlanCascadeRule(
            plan_id=plan.id, owner_user_id=manager_user.id, cascade_scope="all_reports",
            min_rank=4, max_rank=5, priority=10,
        ))
        await db.flush()

        await _make_quota(db, rep=ic_rep, amount=40_000)
        await _make_credit(db, user=ic_user, amount=40_000, booked=date(2026, 3, 10))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(ic_rep.id))

    assert len(results) == 1
    result = results[0]
    # It found a plan (not the rep-level fallback) with no direct assignment —
    # only the cascade path could have supplied it.
    assert result.fallback_mode == "none"
    assert any("cascade rule" in r for r in result.rules_applied)
    assert result.base_commission == pytest.approx(2_800.0, abs=0.01)  # 40,000 * 7%


@pytest.mark.asyncio
async def test_cascade_rule_outside_rank_window_does_not_apply(cleanup):
    """A cascade rule scoped to ranks 1-2 (executives) must not reach a rank-5 IC."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        exec_rep, exec_user = await _make_rep_and_user(db, name="Exec", email="exec@example.com", rank=1)
        ic_rep, ic_user = await _make_rep_and_user(db, name="Far IC", email="farIC@example.com", rank=5)
        db.add(Manager(user_id=ic_user.id, manager_user_id=exec_user.id))
        await db.flush()

        plan = Plan(name="Exec-only plan", scope="global", owner_user_id=exec_user.id)
        db.add(plan)
        await db.flush()
        db.add(Rule(plan_id=plan.id, name="exec rule", metric_name="attainment_pct",
                     threshold_min=0.0, threshold_max=999.0, rate=0.20))
        db.add(PlanCascadeRule(
            plan_id=plan.id, owner_user_id=exec_user.id, cascade_scope="all_reports",
            min_rank=1, max_rank=2, priority=1,  # excludes rank 5
        ))
        await db.flush()

        await _make_quota(db, rep=ic_rep, amount=10_000)
        db.add(Revenue(rep_id=ic_rep.id, period=PERIOD, amount=5_000))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(ic_rep.id))

    assert len(results) == 1
    # No plan resolved for the IC (the only cascade rule excludes their rank) ->
    # rep-level fallback, not the exec's 20% rule.
    assert results[0].fallback_mode == "rep_level_estimate"


# ── Scenario: accelerator on revenue above quota ──────────────────────────

@pytest.mark.asyncio
async def test_accelerator_applies_to_credited_amount_above_quota(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Overachiever", email="over@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 5%", rate=0.05, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=50_000)
        await _make_credit(db, user=user, amount=70_000, booked=date(2026, 3, 1))
        await db.commit()

        config = PayoutConfig(tiers=[CommissionTier(0, 999, 0.05)], accelerator_rate=0.15)
        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id), config=config)

    assert len(results) == 1
    # 20,000 overage * 15% accelerator = 3,000.
    assert results[0].accelerator_amount == pytest.approx(3_000.0, abs=0.01)


# ── Scenario: multi-rep aggregation in one call ───────────────────────────

@pytest.mark.asyncio
async def test_multiple_reps_in_one_period_are_each_computed_independently(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a, user_a = await _make_rep_and_user(db, name="Rep A", email="repa@example.com")
        rep_b, user_b = await _make_rep_and_user(db, name="Rep B", email="repb@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user_a, plan)
        await _assign_plan_directly(db, user_b, plan)
        await _make_quota(db, rep=rep_a, amount=10_000)
        await _make_quota(db, rep=rep_b, amount=10_000)
        await _make_credit(db, user=user_a, amount=10_000, booked=date(2026, 3, 1))
        await _make_credit(db, user=user_b, amount=50_000, booked=date(2026, 3, 1))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD)

    by_rep = {r.rep_id: r for r in results}
    assert by_rep[str(rep_a.id)].base_commission == pytest.approx(1_000.0, abs=0.01)
    assert by_rep[str(rep_b.id)].base_commission == pytest.approx(5_000.0, abs=0.01)
    # Rep B's much larger credit doesn't leak into Rep A's row.
    assert by_rep[str(rep_a.id)].credited_amount == 10_000.0


@pytest.mark.asyncio
async def test_rep_id_filter_excludes_every_other_rep(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep_a, user_a = await _make_rep_and_user(db, name="Only Me", email="onlyme@example.com")
        rep_b, user_b = await _make_rep_and_user(db, name="Not Me", email="notme@example.com")
        db.add(Revenue(rep_id=rep_a.id, period=PERIOD, amount=1_000))
        db.add(Revenue(rep_id=rep_b.id, period=PERIOD, amount=2_000))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep_a.id))

    assert len(results) == 1
    assert results[0].rep_id == str(rep_a.id)


# ── Scenario: plan resolved but has zero rules -> PayoutEngine fallback ──────

@pytest.mark.asyncio
async def test_plan_with_no_rules_falls_back_to_payout_engine_compute(cleanup):
    """A Plan with a PlanAssignment but no Rule rows takes the `else` branch —
    PayoutEngine.compute() rather than _apply_commission_rules — in both the
    credit-level and revenue-fallback paths. Real, distinct code, untested
    before this file: no fixture anywhere built a rule-less plan."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="No Rules Rep", email="norules@example.com")
        plan = Plan(name="Empty plan", scope="individual")  # no Rule rows at all
        db.add(plan)
        await db.flush()
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=50_000)
        await _make_credit(db, user=user, amount=45_000, booked=date(2026, 3, 1))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))

    assert len(results) == 1
    result = results[0]
    assert result.fallback_mode == "none"          # a plan WAS found — just with no rules
    assert result.confidence == "medium"            # confidence drops without real rules
    # PayoutEngine.compute() ran DEFAULT_PAYOUT_CONFIG's tiers: 90% attainment
    # lands in the 80-100% band at 5%.
    assert result.base_commission == pytest.approx(45_000 * 0.05, abs=0.01)


# ── Scenario: quarterly period expands to its three constituent months ───────

@pytest.mark.asyncio
async def test_quarterly_period_sums_revenue_across_its_three_months(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Quarterly Rep", email="quarterly@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=30_000, period="2026-Q1")
        db.add(Revenue(rep_id=rep.id, period="2026-01", amount=10_000))
        db.add(Revenue(rep_id=rep.id, period="2026-02", amount=10_000))
        db.add(Revenue(rep_id=rep.id, period="2026-03", amount=10_000))
        db.add(Revenue(rep_id=rep.id, period="2026-04", amount=999_999))  # outside Q1, must be excluded
        await db.commit()

        results = await compute_credit_payouts(db, "2026-Q1", rep_id=str(rep.id))

    assert len(results) == 1
    # 10k+10k+10k, not the April row leaking in.
    assert results[0].credited_amount == 30_000.0
    assert results[0].fallback_mode == "no_credit_rows"


@pytest.mark.asyncio
async def test_quarterly_period_sums_credits_across_its_three_months(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Quarterly Credit Rep", email="qcredit@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=30_000, period="2026-Q1")
        await _make_credit(db, user=user, amount=10_000, booked=date(2026, 1, 15))
        await _make_credit(db, user=user, amount=10_000, booked=date(2026, 2, 15))
        await _make_credit(db, user=user, amount=10_000, booked=date(2026, 3, 15))
        await _make_credit(db, user=user, amount=999_999, booked=date(2026, 4, 1))  # Q2, excluded
        await db.commit()

        results = await compute_credit_payouts(db, "2026-Q1", rep_id=str(rep.id))

    assert len(results) == 3  # the Q2 credit is not among them
    assert sum(r.credited_amount for r in results) == 30_000.0
    assert all(r.attainment == 100.0 for r in results)


# ── persist_payout_records ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_payout_records_writes_a_real_payout_row(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Persist Rep", email="persist@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 8%", rate=0.08, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=20_000)
        db.add(Revenue(rep_id=rep.id, period=PERIOD, amount=20_000))
        await db.commit()

        computed = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id))
        count = await persist_payout_records(db, computed)
        await db.commit()

        assert count == 1
        rows = (await db.execute(select(PayoutRecord).where(PayoutRecord.user_id == user.id))).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.period == PERIOD
    assert float(row.payout_amount) == pytest.approx(computed[0].final_payout, abs=0.01)
    assert row.fallback_used is True  # this rep went through the no_credit_rows fallback
    assert row.source_system == "credit_payout_engine"


@pytest.mark.asyncio
async def test_persist_payout_records_skips_a_result_with_no_resolvable_user(cleanup):
    """persist_payout_records resolves Rep -> email -> UserProfile itself; a
    CreditPayoutResult naming a rep_id with no matching row anywhere is
    skipped rather than raising, per its own docstring."""
    from backend.payout.credit_payout_engine import CreditPayoutResult

    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        fake_result = CreditPayoutResult(rep_id=str(uuid.uuid4()), period=PERIOD, final_payout=500.0)
        count = await persist_payout_records(db, [fake_result])
        await db.commit()

    assert count == 0


# ── win_rate/deals_won now flow from real deal data, not hardcoded 0/0.0 ────
#
# Found while writing the tests above: apply_spiffs/apply_clawbacks were being
# called with deals_won and win_rate hardcoded to 0 and 0.0 in both the
# credit-level and revenue-fallback branches, rather than the rep's real
# counts (_count_closed_deals already existed and was already called
# elsewhere in this same function — just not threaded into these two calls).
# Verified empirically before touching anything: against DEFAULT_PAYOUT_CONFIG,
# apply_clawbacks(10000.0, attainment_pct=150.0, deals_won=0, win_rate=0.0, ...)
# returned a $500 clawback — DEFAULT_PAYOUT_CONFIG's "Win rate below plan" rule
# (trigger_below=40.0) always fired, because 0.0*100 is always < 40.0,
# regardless of the rep's actual win rate. The "High win rate" SPIFF
# (trigger_metric="win_rate", threshold 75.0) could symmetrically never fire.
# Fixed in the same change as these tests — see .claude/plan-state for this
# branch for the stated grain.

@pytest.mark.asyncio
async def test_high_win_rate_rep_no_longer_gets_the_low_win_rate_clawback(cleanup):
    """Before the fix, this rep — 100% win rate, 150% attainment — still lost
    5% of their payout to a clawback meant for reps below a 40% win rate."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Great Closer", email="great@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=10_000)
        await _make_credit(db, user=user, amount=15_000, booked=date(2026, 3, 1))  # 150% attainment
        for _ in range(5):
            await _make_deal(db, rep=rep, stage="Closed Won", closed=date(2026, 3, 10))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id), config=DEFAULT_PAYOUT_CONFIG)

    assert len(results) == 1
    result = results[0]
    # No "Win rate below plan" clawback line in the trace, and clawback_amount is 0.
    assert result.clawback_amount == 0.0
    assert not any("Win rate below plan" in line for line in result.formula_trace)
    # The "High win rate" SPIFF (100% win rate >= 75% threshold) now fires.
    assert result.spiff_amount == pytest.approx(750.0 + 1500.0, abs=0.01)  # win-rate spiff + attainment spiff
    assert any("High win rate" in line for line in result.formula_trace)


@pytest.mark.asyncio
async def test_genuinely_low_win_rate_rep_still_gets_clawed_back(cleanup):
    """The other direction of the same fix: a rep who actually has a low win
    rate must still trigger the clawback — this isn't about disabling it."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep, user = await _make_rep_and_user(db, name="Struggling Rep", email="struggling@example.com")
        plan, rule = await _make_plan_with_rule(db, name="Flat 10%", rate=0.10, threshold_max=999)
        await _assign_plan_directly(db, user, plan)
        await _make_quota(db, rep=rep, amount=10_000)
        await _make_credit(db, user=user, amount=10_000, booked=date(2026, 3, 1))
        await _make_deal(db, rep=rep, stage="Closed Won", closed=date(2026, 3, 5))
        for _ in range(9):
            await _make_deal(db, rep=rep, stage="Closed Lost", closed=date(2026, 3, 10))
        await db.commit()

        results = await compute_credit_payouts(db, PERIOD, rep_id=str(rep.id), config=DEFAULT_PAYOUT_CONFIG)

    assert len(results) == 1
    result = results[0]
    # 10% win rate is well below the 40% clawback threshold -> still penalized.
    assert result.clawback_amount > 0.0
    assert any("Win rate below plan" in line for line in result.formula_trace)
