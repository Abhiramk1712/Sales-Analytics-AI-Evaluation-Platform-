"""
tests/test_arr_waterfall_continuity.py
=======================================
GET /ml/forecast/arr-waterfall (build_arr_waterfall() in backend/ml/forecasting.py)
computed arr_start[period] = total_revenue[period] * 12 independently for every
period, then added new_logo + expansion + contraction + churn *again* on top --
but those components are already subsets of that same period's total_revenue.
Two consequences, both confirmed live against techo-solutions:

  1. No bridge continuity: arr_start[i+1] never equals arr_end[i], because
     arr_start[i+1] is derived fresh from that period's own revenue rather than
     carried forward from arr_end[i].
  2. Every component already counted inside arr_start[i] gets counted again in
     arr_end[i], so a single month showed $2.8M "net new ARR" against a $4.7M
     ARR base -- 60%+ month-over-month growth, every month.

Meanwhile the canonical `arr_waterfall` DB table (populated by the data
generator, already exposed via the otherwise-unused GET /analytics/arr-waterfall
through backend.metrics.calculators.calc_arr_waterfall_series) has correct,
continuous numbers: arr_end[i] == arr_start[i+1] exactly.

This test seeds both a Revenue fixture (so the old Revenue-reconstruction code
path has something to chew on) and an ArrWaterfallEntry fixture with known,
correct continuity, then asserts the endpoint's output matches the
ArrWaterfallEntry source of truth -- not the double-counted Revenue
reconstruction.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from backend.database import get_session_factory
from backend.models import ArrWaterfallEntry, Revenue
from backend.routers.forecasting import arr_waterfall
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped

COMPANY = f"test-arr-waterfall-{uuid.uuid4().hex[:8]}"


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
        await db.execute(delete(ArrWaterfallEntry).where(ArrWaterfallEntry.company_id == COMPANY))
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_waterfall_carries_arr_end_forward_as_next_periods_arr_start(cleanup):
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        # Revenue rows: present so the old Revenue-reconstruction path has data
        # to work with (and doesn't just 404). Deliberately a different order
        # of magnitude than the ArrWaterfallEntry fixture below, so the two
        # code paths cannot coincidentally agree.
        db.add_all([
            Revenue(period="2025-01", amount=10_000, revenue_type="new_logo"),
            Revenue(period="2025-01", amount=5_000, revenue_type="expansion"),
            Revenue(period="2025-01", amount=50_000, revenue_type="renewal"),
            Revenue(period="2025-01", amount=-1_000, revenue_type="churn"),
            Revenue(period="2025-02", amount=11_000, revenue_type="new_logo"),
            Revenue(period="2025-02", amount=5_500, revenue_type="expansion"),
            Revenue(period="2025-02", amount=52_000, revenue_type="renewal"),
            Revenue(period="2025-02", amount=-1_200, revenue_type="churn"),
        ])

        # ArrWaterfallEntry rows: the canonical source of truth, with known,
        # correct continuity (period 2's arr_start == period 1's arr_end).
        db.add_all([
            ArrWaterfallEntry(
                period="2025-01", arr_start=1_000_000, arr_end=1_013_200,
                mrr_new=800, mrr_expansion=400, mrr_contraction=0, mrr_churn=-100,
                mrr_renewal=4_000, mrr_net=1_100,
            ),
            ArrWaterfallEntry(
                period="2025-02", arr_start=1_013_200, arr_end=1_027_360,
                mrr_new=850, mrr_expansion=420, mrr_contraction=0, mrr_churn=-90,
                mrr_renewal=4_100, mrr_net=1_180,
            ),
        ])
        await db.commit()

        result = await arr_waterfall(db=db)

    wf = result["waterfall"]
    assert wf["periods"] == ["2025-01", "2025-02"]

    # Bridge continuity: this period's arr_end must equal the next period's
    # arr_start. The old Revenue-reconstruction code recomputed arr_start from
    # scratch each period and could not satisfy this.
    assert wf["arr_end"][0] == wf["arr_start"][1]

    # Values must come from the ArrWaterfallEntry source of truth, not a
    # doubled-up reconstruction from Revenue.
    assert wf["arr_start"][0] == 1_000_000
    assert wf["arr_end"][0] == 1_013_200
    assert wf["arr_start"][1] == 1_013_200
    assert wf["arr_end"][1] == 1_027_360

    # Sanity bound: no single month should show >15% ARR growth in this
    # fixture. The double-counting bug inflated net_new_arr to ~22% of
    # arr_start in an equivalent scenario.
    assert abs(wf["net_new_arr"][0]) < 0.15 * wf["arr_start"][0]
