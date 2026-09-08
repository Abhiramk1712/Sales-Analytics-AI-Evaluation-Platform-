"""
tests/test_model_store_tenant_scoping.py
==========================================
ModelStore (backend/ml/model_store.py) is a JSON file on disk backing GET
/ml/model-runs' fallback (used whenever the DB-backed ModelRunRecord table
has nothing for the current tenant) and the only source GET /ml/drift's
baseline comes from. It carried no company_id at all: every run ever
appended by any company went into one flat, unscoped list, so every tenant
reading it saw the exact same global run history rubber-stamped with its
own company_id.

Confirmed live: with ModelRunRecord (the DB table) completely empty --
true for every company in this environment -- GET /ml/model-runs for both
techo-solutions and insurex returned the identical 163-run history, and
GET /ml/drift for both returned the identical baseline_trained_at /
baseline_training_rows, despite the two companies having entirely
different datasets.

These tests use a throwaway ModelStore pointed at a tmp_path file, never
the real backend/ml/saved/model_runs.json.
"""
from __future__ import annotations

import uuid

import pytest

from backend.ml.model_store import ModelStore


def test_load_runs_filters_by_company(tmp_path):
    store = ModelStore(path=str(tmp_path / "model_runs.json"))
    store.append_run({"model_name": "revenue_forecast", "trained_at": "2026-01-01T00:00:00"}, company_id="company-a")
    store.append_run({"model_name": "revenue_forecast", "trained_at": "2026-02-01T00:00:00"}, company_id="company-b")

    a_runs = store.load_runs(company_id="company-a")
    b_runs = store.load_runs(company_id="company-b")

    assert len(a_runs) == 1
    assert len(b_runs) == 1
    assert a_runs[0]["trained_at"] == "2026-01-01T00:00:00"
    assert b_runs[0]["trained_at"] == "2026-02-01T00:00:00"


def test_load_runs_with_no_company_filter_returns_everything(tmp_path):
    """append_run's own internal re-read (to append and rewrite the whole
    file) must see every run regardless of company, or each new append
    would silently drop every other company's history from the file."""
    store = ModelStore(path=str(tmp_path / "model_runs.json"))
    store.append_run({"model_name": "m1"}, company_id="company-a")
    store.append_run({"model_name": "m2"}, company_id="company-b")

    assert len(store.load_runs()) == 2


def test_run_with_no_company_id_is_not_returned_to_any_company(tmp_path):
    """A run with no company_id (legacy data from before this scoping
    existed, or a caller that didn't pass one) must not be treated as
    belonging to whichever tenant happens to ask -- that is exactly the
    cross-tenant leak this store exists to avoid."""
    store = ModelStore(path=str(tmp_path / "model_runs.json"))
    # Simulate a legacy run with no company_id, written directly (not via
    # append_run, which always stamps one -- even as None).
    import json
    store.path.write_text(json.dumps([{"model_name": "revenue_forecast", "trained_at": "2020-01-01"}]))

    assert store.load_runs(company_id="company-a") == []
    assert store.load_runs(company_id="company-b") == []
    # Unfiltered, the orphaned run is still visible (needed internally).
    assert len(store.load_runs()) == 1


@pytest.mark.asyncio
async def test_model_runs_endpoint_fallback_does_not_cross_companies(monkeypatch, db_schema):
    """GET /ml/model-runs' JSON fallback (hit here since ModelRunRecord has
    no rows for either company) must not show company A's run history to
    company B."""
    import backend.database as database
    database._engine = None
    database._async_session_factory = None

    from backend.database import get_session_factory
    from backend.routers import forecasting
    from backend.tenancy import tenant_scope

    company_a = f"test-model-runs-a-{uuid.uuid4().hex[:8]}"
    company_b = f"test-model-runs-b-{uuid.uuid4().hex[:8]}"

    test_store = ModelStore(path=str(__import__("pathlib").Path(__file__).parent / f"_tmp_model_runs_{uuid.uuid4().hex[:8]}.json"))
    monkeypatch.setattr(forecasting, "store", test_store)
    try:
        test_store.append_run({"model_name": "revenue_forecast", "trained_at": "2026-01-01T00:00:00", "metrics": {}}, company_id=company_a)

        factory = get_session_factory()
        async with factory() as db, tenant_scope(company_a):
            result_a = await forecasting.model_runs(db=db, company_id=company_a)
        async with factory() as db, tenant_scope(company_b):
            result_b = await forecasting.model_runs(db=db, company_id=company_b)

        assert len(result_a["model_runs"]) == 1
        assert result_b["model_runs"] == []
    finally:
        test_store.path.unlink(missing_ok=True)
        database._engine = None
        database._async_session_factory = None


@pytest.mark.asyncio
async def test_model_runs_endpoint_merges_db_and_fallback_history(monkeypatch, db_schema):
    """_save_run() (backend/routers/forecasting.py) writes every run to BOTH
    ModelRunRecord (DB) and the JSON fallback file, unconditionally -- so
    the two are not alternatives, they're the same history recorded twice.
    The endpoint's old "if rows: DB only, else: fallback only" logic meant
    a company's *entire* prior run history (JSON-only, from before that
    company had its first DB-backed row) vanished from GET /ml/model-runs
    the instant one real DB row appeared for it.

    Confirmed live: techo-solutions showed 11 runs (DB empty, all
    JSON-fallback); one /ml/cluster/reps call inserted a single DB row, and
    the count dropped to 1 -- the other 10 were still in the JSON file,
    just no longer shown. This seeds one run only in the JSON file (as if
    it predated any DB-backed run for this company) and one run present in
    BOTH (as _save_run actually produces), then asserts both distinct runs
    show up, not just the DB one, and the duplicate isn't double-counted."""
    import backend.database as database
    database._engine = None
    database._async_session_factory = None

    from backend.database import get_session_factory
    from backend.models import ModelRunRecord
    from backend.routers import forecasting
    from backend.tenancy import tenant_scope
    from backend.tenant_guard import unscoped
    from datetime import datetime
    from sqlalchemy import delete

    company = f"test-model-runs-merge-{uuid.uuid4().hex[:8]}"
    # Nonzero microseconds deliberately -- datetime.isoformat() omits a
    # trailing ".000000", so a round-trip through the DB dedup path would
    # falsely "clean up" a zero-microsecond value regardless of whether the
    # dedup logic itself is correct. This keeps the test's own expected
    # strings unambiguous.
    older_trained_at = "2026-01-01T00:00:00.111111"
    newer_trained_at = "2026-02-01T00:00:00.222222"

    test_store = ModelStore(path=str(__import__("pathlib").Path(__file__).parent / f"_tmp_model_runs_merge_{uuid.uuid4().hex[:8]}.json"))
    monkeypatch.setattr(forecasting, "store", test_store)
    try:
        # JSON-only: a run from "before" this company had any DB row.
        test_store.append_run(
            {"model_name": "rep_clustering", "trained_at": older_trained_at, "metrics": {}},
            company_id=company,
        )
        # In both DB and JSON: exactly what _save_run() itself produces.
        test_store.append_run(
            {"model_name": "revenue_forecast", "trained_at": newer_trained_at, "metrics": {"mape": 0.05}},
            company_id=company,
        )

        factory = get_session_factory()
        async with factory() as db, tenant_scope(company):
            db.add(ModelRunRecord(
                model_name="revenue_forecast",
                model_version="v1",
                trained_at=datetime.fromisoformat(newer_trained_at),
                training_rows=10,
                metrics={"mape": 0.05},
            ))
            await db.commit()

            result = await forecasting.model_runs(db=db, company_id=company)

        names_and_times = {(r["model_name"], r["trained_at"]) for r in result["model_runs"]}
        assert names_and_times == {
            ("rep_clustering", older_trained_at),
            ("revenue_forecast", newer_trained_at),
        }
        assert len(result["model_runs"]) == 2, (
            f"expected the JSON-only and DB+JSON runs exactly once each, got: {result['model_runs']}"
        )
    finally:
        test_store.path.unlink(missing_ok=True)
        async with factory() as db, unscoped():
            await db.execute(delete(ModelRunRecord).where(ModelRunRecord.company_id == company))
            await db.commit()
        database._engine = None
        database._async_session_factory = None


@pytest.mark.asyncio
async def test_drift_baseline_does_not_cross_companies(monkeypatch, db_schema):
    """GET /ml/drift's baseline lookup must not fall back to a different
    company's training run just because it's the most recent one globally."""
    import backend.database as database
    database._engine = None
    database._async_session_factory = None

    from backend.database import get_session_factory
    from backend.routers import forecasting
    from backend.tenancy import tenant_scope
    from backend.models import Revenue
    from datetime import date

    company_a = f"test-drift-a-{uuid.uuid4().hex[:8]}"
    company_b = f"test-drift-b-{uuid.uuid4().hex[:8]}"

    test_store = ModelStore(path=str(__import__("pathlib").Path(__file__).parent / f"_tmp_drift_{uuid.uuid4().hex[:8]}.json"))
    monkeypatch.setattr(forecasting, "store", test_store)
    try:
        # Only company A has ever trained this model.
        test_store.append_run(
            {"model_name": "revenue_forecast", "trained_at": "2026-01-01T00:00:00", "training_rows": 12, "metrics": {"mape": 0.1}},
            company_id=company_a,
        )

        factory = get_session_factory()
        # Company B has revenue (so the backtest itself can run) but no
        # baseline run of its own -- it must 404, not silently borrow A's.
        async with factory() as db, tenant_scope(company_b):
            from backend.models import Rep
            rep = Rep(name="Drift Test Rep", email="drift-test@example.com")
            db.add(rep)
            await db.flush()
            for i in range(6):
                db.add(Revenue(rep_id=rep.id, period=f"2026-{i+1:02d}", amount=1000 + i * 100))
            await db.commit()

            with pytest.raises(Exception) as exc_info:
                await forecasting.model_drift_report(db=db, company_id=company_b)
            assert "404" in str(exc_info.value) or "No stored training run" in str(exc_info.value)

        # Company A does have one -- must resolve using its own baseline.
        async with factory() as db, tenant_scope(company_a):
            rep = Rep(name="Drift Test Rep A", email="drift-test-a@example.com")
            db.add(rep)
            await db.flush()
            for i in range(6):
                db.add(Revenue(rep_id=rep.id, period=f"2026-{i+1:02d}", amount=1000 + i * 100))
            await db.commit()

            result_a = await forecasting.model_drift_report(db=db, company_id=company_a)
            # Present in every non-404 response shape this endpoint returns
            # (whether or not the backtest itself has enough data for a full
            # comparison) -- the point is it resolves to company A's own
            # baseline rather than 404ing like company B did.
            assert result_a["baseline_trained_at"] == "2026-01-01T00:00:00"
    finally:
        test_store.path.unlink(missing_ok=True)
        database._engine = None
        database._async_session_factory = None
