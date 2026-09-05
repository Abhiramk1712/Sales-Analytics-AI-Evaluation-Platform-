"""
tests/test_payout_approvals_db_fallback.py
============================================
GET /payout-audit's "empty in-memory store" fallback built display-only row
dicts from real PayoutRecord rows (payout_id = str(pr.id)), but never
registered them in the in-memory audit_trail_service store. Every lifecycle
action (review/approve/lock/pay/adjust) looks the payout_id up in that same
store and raises KeyError -> 404 if it's missing -- so clicking "Mark
reviewed" or "Approve" on any row that came from this fallback path 404'd,
always, for every payout nothing had explicitly run /payout/calculate or
/payout/team-summary against first.

A second, compounding bug: the fallback only ran `if not rows` -- once a
single real in-memory record existed for a company (from any one period),
every *other* period's PayoutRecord stopped being surfaced at all, since
`rows` was no longer empty. Confirmed live: a fresh server showed all 144
of techo-solutions' PayoutRecord rows (all fallback, all unactionable);
after visiting the Payouts tab for one quarter (which computes and
registers just that quarter's 11-12 records), the list collapsed to only
those 11-12 rows and every other quarter's payout history vanished from
the Payout Approvals tab entirely.

This test is DB-backed (a real PayoutRecord row, not audit_trail_service's
in-memory store pre-seeded by hand) so it exercises exactly the code path
that broke: list the payout, then act on the exact payout_id the list
response gave back.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Account, Deal, PayoutRecord, Plan, Quota, Rep, Revenue, UserProfile
from backend.routers.payout import team_payout_summary
from backend.routers.payout_audit import (
    approve_payout_record,
    list_payout_records,
    mark_payout_paid,
    review_payout_record,
    ApprovePayoutRequest,
)
from backend.auth.models import UserContext
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-payout-approvals-{uuid.uuid4().hex[:8]}"


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
    # Also clear the in-memory audit trail so a leftover seeded record from
    # this test's company id can't leak into a later test.
    from backend.payout.audit_trail_service import clear_store
    clear_store()

    factory = get_session_factory()
    async with factory() as db, unscoped():
        await db.execute(delete(PayoutRecord).where(PayoutRecord.company_id == COMPANY))
        await db.execute(delete(Plan).where(Plan.company_id == COMPANY))
        await db.execute(delete(UserProfile).where(UserProfile.company_id == COMPANY))
        await db.execute(delete(Deal).where(Deal.company_id == COMPANY))
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Quota).where(Quota.company_id == COMPANY))
        await db.execute(delete(Account).where(Account.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


def _finance_admin_ctx() -> UserContext:
    return UserContext(
        user_id="test-finance-admin",
        role="finance_admin",
        team_id=None,
        territory_id=None,
        company_id=None,
        permissions={"view_payouts", "approve_payouts"},
        auth_source="demo",
        is_demo=True,
    )


@pytest.mark.asyncio
async def test_db_fallback_payout_row_is_actually_actionable(cleanup):
    """List a payout that only exists as a real PayoutRecord (nothing has
    ever computed it via /payout/calculate or /payout/team-summary), then
    act on the exact payout_id the list handed back. Must not 404."""
    factory = get_session_factory()
    email = "fallback-actionable-rep@example.com"

    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Fallback Rep", email=email)
        user = UserProfile(name="Fallback Rep", email=email)
        db.add_all([rep, user])
        await db.flush()

        payout = PayoutRecord(
            user_id=user.id, plan_id=None, period="2026-Q3",
            payout_amount=4321.55, fallback_used=False, confidence=1.0,
        )
        db.add(payout)
        # approve_payout_record's own critical-data-quality gate checks for
        # non-empty, non-orphaned deals/revenue tables company-wide --
        # unrelated to this test's own bug, satisfied minimally so the
        # approve call under test isn't blocked by a different,
        # correctly-firing guard.
        account = Account(name="Quality Gate Account")
        db.add(account)
        await db.flush()
        db.add(Deal(rep_id=rep.id, account_id=account.id, name="Quality Gate Deal", stage="Closed Won", amount=100))
        db.add(Revenue(rep_id=rep.id, period="2026-08", amount=100))
        await db.commit()
        payout_id = str(payout.id)

        listing = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        row = next(r for r in listing["rows"] if r["payout_id"] == payout_id)
        assert row["lifecycle_state"] == "draft"
        assert row["final_payout"] == pytest.approx(4321.55)

        # This is the exact call the "Mark reviewed" button makes with the
        # exact payout_id the list just returned -- must resolve, not 404.
        reviewed = await review_payout_record(payout_id, ctx=_finance_admin_ctx())
        assert reviewed["lifecycle_state"] == "reviewed"

        approved = await approve_payout_record(
            payout_id, ApprovePayoutRequest(note="looks right"), db=db, ctx=_finance_admin_ctx()
        )
        assert approved["lifecycle_state"] == "approved"
        assert approved["approval_status"] == "approved"


@pytest.mark.asyncio
async def test_other_periods_stay_listed_after_one_period_is_seeded(cleanup):
    """Two PayoutRecord rows in different periods. Acting on (seeding) one
    must not make the other disappear from the list -- the old code's
    `if not rows` fallback guard meant exactly one real in-memory record for
    the company hid every other period's payout history."""
    factory = get_session_factory()
    email = "two-periods-rep@example.com"

    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Two Periods Rep", email=email)
        user = UserProfile(name="Two Periods Rep", email=email)
        db.add_all([rep, user])
        await db.flush()

        q3 = PayoutRecord(user_id=user.id, plan_id=None, period="2026-Q3", payout_amount=1000, fallback_used=False, confidence=1.0)
        q2 = PayoutRecord(user_id=user.id, plan_id=None, period="2026-Q2", payout_amount=2000, fallback_used=False, confidence=1.0)
        db.add_all([q3, q2])
        await db.commit()

        # First list call seeds both into the in-memory store.
        first = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        assert {r["period"] for r in first["rows"]} == {"2026-Q3", "2026-Q2"}

        # Act on just the Q3 record.
        q3_id = next(r["payout_id"] for r in first["rows"] if r["period"] == "2026-Q3")
        await review_payout_record(q3_id, ctx=_finance_admin_ctx())

        # Q2's record must still be listed -- not silently dropped.
        second = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        assert {r["period"] for r in second["rows"]} == {"2026-Q3", "2026-Q2"}
        q2_row = next(r for r in second["rows"] if r["period"] == "2026-Q2")
        assert q2_row["lifecycle_state"] == "draft"
        q3_row = next(r for r in second["rows"] if r["period"] == "2026-Q3")
        assert q3_row["lifecycle_state"] == "reviewed"


@pytest.mark.asyncio
async def test_pay_action_on_a_never_computed_payout_does_not_404(cleanup):
    """mark_payout_paid on a payout that only ever existed as a DB row."""
    factory = get_session_factory()
    email = "pay-action-rep@example.com"

    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Pay Action Rep", email=email)
        user = UserProfile(name="Pay Action Rep", email=email)
        db.add_all([rep, user])
        await db.flush()

        payout = PayoutRecord(user_id=user.id, plan_id=None, period="2026-Q1", payout_amount=555, fallback_used=True, confidence=0.5)
        db.add(payout)
        await db.commit()
        payout_id = str(payout.id)

        await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        paid = await mark_payout_paid(payout_id, ctx=_finance_admin_ctx())
        assert paid["lifecycle_state"] == "paid"


def test_clear_store_scoped_to_one_company_leaves_others_intact():
    """clear_store() used to take no argument and wipe every company's audit
    trail unconditionally on every single company load -- loading company A
    silently erased every approval/lock/correction ever recorded for B, C,
    ... too, even though records are already keyed by company_id. Two
    companies resident at once is a supported configuration
    (test_tenancy_enforcement.py); this is the same guarantee applied to the
    audit trail."""
    from backend.payout.audit_trail_service import (
        clear_store,
        list_payouts,
        seed_from_db_record,
    )

    company_a = f"test-clear-store-a-{uuid.uuid4().hex[:8]}"
    company_b = f"test-clear-store-b-{uuid.uuid4().hex[:8]}"
    try:
        seed_from_db_record(
            payout_id=f"{company_a}-payout", company_id=company_a, period="2026-Q1",
            user_id=None, rep_id=None, rep_name="A Rep", plan_id=None,
            credited_amount=100, final_payout=100, confidence=1.0, fallback_used=False,
        )
        seed_from_db_record(
            payout_id=f"{company_b}-payout", company_id=company_b, period="2026-Q1",
            user_id=None, rep_id=None, rep_name="B Rep", plan_id=None,
            credited_amount=200, final_payout=200, confidence=1.0, fallback_used=False,
        )

        clear_store(company_id=company_a)

        assert list_payouts(company_id=company_a) == []
        assert len(list_payouts(company_id=company_b)) == 1
    finally:
        clear_store(company_id=company_a)
        clear_store(company_id=company_b)


@pytest.mark.asyncio
async def test_team_summary_after_list_does_not_duplicate_the_payout(cleanup):
    """seed_from_db_record() (from list_payout_records) keys a payout by its
    PayoutRecord's own DB id; upsert_payout_trace() (from
    /payout/team-summary) used to always key by a uuid5 hash of
    (company, rep, period) -- a different id space entirely. So a rep+period
    with both a persisted PayoutRecord and a live team-summary run produced
    TWO independently-approvable rows with two different amounts for what
    is conceptually the same payout. Confirmed live in techo-solutions'
    2026-Q3 data.

    List first (seeding the DB-id-keyed record), then run team-summary --
    it must reuse that same id and prefer the real PayoutRecord's amount,
    not add a second row."""
    factory = get_session_factory()
    email = "list-then-summary-rep@example.com"

    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="List Then Summary Rep", email=email)
        user = UserProfile(name="List Then Summary Rep", email=email)
        db.add_all([rep, user])
        await db.flush()

        # Revenue/quota that would make compute_payout() land on a very
        # different number than the persisted PayoutRecord below, so a
        # passing test can't be a coincidence of the two numbers matching.
        db.add(Revenue(rep_id=rep.id, period="2026-08", amount=10_000))
        db.add(Quota(rep_id=rep.id, period="2026-08", amount=8_000))
        real_payout = PayoutRecord(
            user_id=user.id, plan_id=None, period="2026-Q3",
            payout_amount=99_999.99, commission_rate=0.5, fallback_used=False, confidence=1.0,
        )
        db.add(real_payout)
        await db.commit()
        real_payout_id = str(real_payout.id)

        listing = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        matching = [r for r in listing["rows"] if r["period"] == "2026-Q3"]
        assert len(matching) == 1
        assert matching[0]["payout_id"] == real_payout_id

        await team_payout_summary(period="2026-Q3", db=db, company_id=COMPANY, ctx=_finance_admin_ctx())

        after = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        matching_after = [r for r in after["rows"] if r["period"] == "2026-Q3"]
        assert len(matching_after) == 1, (
            f"expected exactly one payout row for this rep+period, got {len(matching_after)}: {matching_after}"
        )
        assert matching_after[0]["payout_id"] == real_payout_id
        assert matching_after[0]["final_payout"] == pytest.approx(99_999.99)


@pytest.mark.asyncio
async def test_team_summary_before_list_does_not_duplicate_the_payout(cleanup):
    """Same guarantee, reverse order -- team-summary runs first (as it does
    live: visiting the Payouts tab before Payout Approvals), then the list
    is fetched. Must still resolve to one row, anchored to the real
    PayoutRecord's id, not the hash team-summary would otherwise use."""
    factory = get_session_factory()
    email = "summary-then-list-rep@example.com"

    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Summary Then List Rep", email=email)
        user = UserProfile(name="Summary Then List Rep", email=email)
        db.add_all([rep, user])
        await db.flush()

        db.add(Revenue(rep_id=rep.id, period="2026-08", amount=10_000))
        db.add(Quota(rep_id=rep.id, period="2026-08", amount=8_000))
        real_payout = PayoutRecord(
            user_id=user.id, plan_id=None, period="2026-Q3",
            payout_amount=77_777.77, commission_rate=0.4, fallback_used=False, confidence=1.0,
        )
        db.add(real_payout)
        await db.commit()
        real_payout_id = str(real_payout.id)

        await team_payout_summary(period="2026-Q3", db=db, company_id=COMPANY, ctx=_finance_admin_ctx())

        listing = await list_payout_records(lifecycle_state=None, company_id=COMPANY, db=db)
        matching = [r for r in listing["rows"] if r["period"] == "2026-Q3"]
        assert len(matching) == 1, (
            f"expected exactly one payout row for this rep+period, got {len(matching)}: {matching}"
        )
        assert matching[0]["payout_id"] == real_payout_id
        assert matching[0]["final_payout"] == pytest.approx(77_777.77)
