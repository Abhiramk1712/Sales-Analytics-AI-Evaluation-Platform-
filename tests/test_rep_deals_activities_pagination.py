"""
tests/test_rep_deals_activities_pagination.py
===============================================
GET /analytics/reps/{rep_id}/activities and GET /analytics/reps/{rep_id}/deals
had no tests at all. Both returned "count": len(rows) — this response's own
size, capped at `limit` by construction — rather than the rep's true total.
The one frontend caller (RepScorecardPage.jsx) re-paginated that already-
truncated array client-side, which looked like real pagination (it reaches
a final page) while silently hiding everything past the first `limit`.

Confirmed live: a rep with 121 real activities only ever had the first 50
fetched; client-side pagination over those 50 showed 5 complete pages with
no indication 71 more existed.

Fixed by returning the true total (a separate, unlimited COUNT query) and
accepting `offset` so a caller can actually reach the rest.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import Rep, Deal, Activity
from backend.routers.analytics import rep_activities, rep_deals
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-rep-pagination-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(Activity).where(Activity.company_id == COMPANY))
        await db.execute(delete(Deal).where(Deal.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_activities_count_is_the_true_total_not_the_page_size(cleanup):
    """75 real activities, limit=50 (the frontend's old fixed page size) —
    count must say 75, not 50."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Pagination Rep", email="paginationrep@example.com")
        db.add(rep)
        await db.flush()
        deal = Deal(rep_id=rep.id, name="Anchor Deal", stage="Prospecting", amount=1000, created_at=datetime(2025, 1, 1))
        db.add(deal)
        await db.flush()
        for i in range(75):
            db.add(Activity(
                rep_id=rep.id, deal_id=deal.id, type="call", outcome="connected",
                activity_date=datetime(2025, 1, 1 + (i % 27)),
            ))
        await db.commit()

        result = await rep_activities(rep_id=str(rep.id), limit=50, offset=0, db=db)

    assert result["count"] == 75
    assert len(result["activities"]) == 50


@pytest.mark.asyncio
async def test_activities_offset_reaches_rows_past_the_first_page(cleanup):
    """Page 2 (offset=50) of those same 75 must return the remaining 25,
    with no overlap against page 1 — proving offset genuinely pages through
    the data rather than the endpoint always returning the same top slice."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Pagination Rep 2", email="paginationrep2@example.com")
        db.add(rep)
        await db.flush()
        deal = Deal(rep_id=rep.id, name="Anchor Deal", stage="Prospecting", amount=1000, created_at=datetime(2025, 1, 1))
        db.add(deal)
        await db.flush()
        for i in range(75):
            db.add(Activity(
                rep_id=rep.id, deal_id=deal.id, type="email", outcome="sent",
                activity_date=datetime(2025, 1, 1 + (i % 27)),
            ))
        await db.commit()

        page1 = await rep_activities(rep_id=str(rep.id), limit=50, offset=0, db=db)
        page2 = await rep_activities(rep_id=str(rep.id), limit=50, offset=50, db=db)

    assert len(page1["activities"]) == 50
    assert len(page2["activities"]) == 25
    ids_page1 = {a["id"] for a in page1["activities"]}
    ids_page2 = {a["id"] for a in page2["activities"]}
    assert ids_page1.isdisjoint(ids_page2)


@pytest.mark.asyncio
async def test_deals_count_is_the_true_total_not_the_page_size(cleanup):
    """Same fix, same shape, on the sibling deals endpoint."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Pagination Rep 3", email="paginationrep3@example.com")
        db.add(rep)
        await db.flush()
        for i in range(60):
            db.add(Deal(
                rep_id=rep.id, name=f"Deal {i}", stage="Prospecting", amount=1000 + i,
                created_at=datetime(2025, 1, 1 + (i % 27)),
            ))
        await db.commit()

        result = await rep_deals(rep_id=str(rep.id), stage=None, limit=50, offset=0, db=db)

    assert result["count"] == 60
    assert len(result["deals"]) == 50
