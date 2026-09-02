"""
tests/test_sales_tracker_workflow.py
======================================
Tests for the SalesTracker workflow.

TestWorkflowStore/TestWorkflowStoreFields below cover backend/workflows/store.py, the
generic job-status store. They don't touch backend/workflows/sales_tracker_workflow.py
itself — the actual 7-step pipeline behind POST /workflows/sales-tracker/run — despite
the file name promising that. That module had 0% coverage; nothing proved
run_sales_tracker_workflow() ran, or that a step failing mid-pipeline was actually
contained the way its try/except per step implies. TestSalesTrackerWorkflow below
covers the orchestration itself: each step function is faked out (they're each tested
in their own module), so what's under test is this module's own logic — ordering, error
containment, audit trail construction, and which of steps_completed/steps_failed drives
complete_workflow vs fail_workflow.
"""
from __future__ import annotations

import pytest

from backend.workflows.store import (
    create_workflow,
    complete_workflow,
    fail_workflow,
    get_workflow,
    list_workflows,
)


class TestWorkflowStore:
    def test_create_and_get(self):
        wid = "wf_test_001"
        create_workflow(wid, pipeline="sales_tracker", period="2025-Q1")
        entry = get_workflow(wid)
        assert entry is not None
        assert entry["workflow_id"] == wid
        assert entry["status"] == "running"

    def test_complete_workflow(self):
        wid = "wf_test_002"
        create_workflow(wid, pipeline="test", period="2025-01")
        complete_workflow(wid, result={"revenue": 100000}, steps_completed=["kpis"])
        entry = get_workflow(wid)
        assert entry["status"] == "completed"
        assert entry["result"]["revenue"] == 100000

    def test_fail_workflow(self):
        wid = "wf_test_003"
        create_workflow(wid, pipeline="test", period="2025-01")
        fail_workflow(wid, errors=["DB connection failed"])
        entry = get_workflow(wid)
        assert entry["status"] == "failed"
        assert "DB connection failed" in entry.get("errors", [])

    def test_list_workflows(self):
        wid = "wf_test_list_001"
        create_workflow(wid, pipeline="test", period=None)
        workflows = list_workflows(limit=50)
        assert any(w["workflow_id"] == wid for w in workflows)

    def test_get_nonexistent_returns_none(self):
        entry = get_workflow("wf_does_not_exist_xyz")
        assert entry is None


class TestWorkflowStoreFields:
    def test_create_stores_pipeline_and_period(self):
        wid = "wf_test_fields_001"
        create_workflow(wid, pipeline="sales_performance", period="2024-Q4", company_id="acme")
        entry = get_workflow(wid)
        assert entry["pipeline"] == "sales_performance"
        assert entry["period"] == "2024-Q4"
        assert entry["company_id"] == "acme"

    def test_completed_workflow_has_timestamp(self):
        wid = "wf_test_ts_001"
        create_workflow(wid, pipeline="test", period=None)
        complete_workflow(wid, result={}, steps_completed=[])
        entry = get_workflow(wid)
        assert "completed_at" in entry
        assert entry["completed_at"] is not None


# ── run_sales_tracker_workflow orchestration ─────────────────────────────────

class _FakeSalesPerformanceService:
    """Stands in for SalesPerformanceService — that class has its own tests."""

    def __init__(self, db):
        self.db = db

    async def get_full_summary(self, period=None):
        return {"revenue": 500_000, "period": period, "warnings": []}


def _patch_all_steps_ok(monkeypatch):
    """Point every step the workflow imports at a fake that succeeds."""
    import backend.workflows.sales_tracker_workflow as swf
    import backend.routers.data_quality as dq_module
    import backend.agent.tools.ml_tools as ml_tools_module
    import backend.payout as payout_module
    import backend.reports.report_generator as report_gen_module

    monkeypatch.setattr(swf, "SalesPerformanceService", _FakeSalesPerformanceService)

    async def fake_build_checks(db):
        return [{"check_name": "no_orphan_deals", "status": "PASS"}]

    monkeypatch.setattr(dq_module, "_build_checks", fake_build_checks)

    async def fake_deal_risk(db):
        return {"data": {"high_risk_count": 2}, "warnings": []}

    async def fake_rep_clusters(db):
        return {"data": {"cluster_count": 3}, "warnings": []}

    async def fake_forecast(db):
        return {"data": {"next_month": 55_000}, "warnings": []}

    monkeypatch.setattr(ml_tools_module, "get_deal_risk_summary", fake_deal_risk)
    monkeypatch.setattr(ml_tools_module, "get_rep_clusters_summary", fake_rep_clusters)
    monkeypatch.setattr(ml_tools_module, "get_forecast_summary", fake_forecast)

    async def fake_compute_payout(db, period=None):
        return {"total_payout": 12_345, "period": period, "warnings": []}

    monkeypatch.setattr(payout_module, "compute_payout", fake_compute_payout)

    class _FakeReportGenerator:
        def __init__(self, db):
            self.db = db

        async def generate_report(self, report_type, period=""):
            return {"markdown": f"# {report_type} for {period}", "warnings": []}

    monkeypatch.setattr(report_gen_module, "ReportGenerator", _FakeReportGenerator)


ALL_STEPS = [
    "sales_performance", "data_quality", "deal_risk",
    "rep_clusters", "forecast", "payouts", "report",
]


@pytest.mark.asyncio
class TestSalesTrackerWorkflow:
    async def test_happy_path_runs_all_seven_steps_in_order(self, monkeypatch):
        from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow

        _patch_all_steps_ok(monkeypatch)
        result = await run_sales_tracker_workflow(db=object(), period="2025-Q1", company_id="acme")

        assert result["steps_completed"] == ALL_STEPS
        assert result["steps_failed"] == []
        assert result["status"] == "completed"
        assert len(result["audit_trail"]) == 7
        assert all(entry["status"] == "ok" for entry in result["audit_trail"])
        assert result["step_results"]["payouts"]["total_payout"] == 12_345

    async def test_happy_path_stores_a_retrievable_completed_entry(self, monkeypatch):
        from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow

        _patch_all_steps_ok(monkeypatch)
        result = await run_sales_tracker_workflow(db=object(), period="2025-Q1")

        entry = get_workflow(result["workflow_id"])
        assert entry is not None
        assert entry["status"] == "completed"
        assert entry["steps_completed"] == ALL_STEPS

    async def test_one_failing_step_does_not_stop_the_rest(self, monkeypatch):
        """A step raising is caught per-step — later steps still run."""
        from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow
        import backend.agent.tools.ml_tools as ml_tools_module

        _patch_all_steps_ok(monkeypatch)

        async def broken_forecast(db):
            raise RuntimeError("model store unavailable")

        monkeypatch.setattr(ml_tools_module, "get_forecast_summary", broken_forecast)

        result = await run_sales_tracker_workflow(db=object(), period="2025-Q1")

        assert result["steps_failed"] == ["forecast"]
        # report runs after forecast in step order — proves the failure didn't abort the pipeline
        assert "report" in result["steps_completed"]
        assert any(
            e["step"] == "forecast" and e["status"] == "failed" and "model store unavailable" in e["error"]
            for e in result["audit_trail"]
        )
        assert any("model store unavailable" in w for w in result["warnings"])
        # one failure out of seven — not >= steps_completed, so this is "partial", not a hard failure
        assert result["status"] == "partial"

    async def test_majority_of_steps_failing_marks_the_stored_workflow_failed(self, monkeypatch):
        """
        steps_failed >= steps_completed routes to fail_workflow() instead of
        complete_workflow() — a real branch in the module, exercised here for the
        first time. Failing 4 of 7 (data_quality, deal_risk, rep_clusters, forecast)
        leaves 3 completed, 4 failed: 4 >= 3 triggers the fail_workflow path.
        """
        from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow
        import backend.routers.data_quality as dq_module
        import backend.agent.tools.ml_tools as ml_tools_module

        _patch_all_steps_ok(monkeypatch)

        async def broken(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(dq_module, "_build_checks", broken)
        monkeypatch.setattr(ml_tools_module, "get_deal_risk_summary", broken)
        monkeypatch.setattr(ml_tools_module, "get_rep_clusters_summary", broken)
        monkeypatch.setattr(ml_tools_module, "get_forecast_summary", broken)

        result = await run_sales_tracker_workflow(db=object(), period="2025-Q1")

        assert len(result["steps_failed"]) == 4
        assert len(result["steps_completed"]) == 3
        # the function's own summary field is correct...
        assert result["status"] == "partial"

        # ...but the store entry it hands off to is written by fail_workflow(), which
        # always sets status="failed" — the two disagree. That's real, current behavior,
        # not a test bug: complete_workflow() is only reached on the other branch, and
        # fail_workflow() always writes "failed" regardless of the caller's own status
        # string. Pinned here so a future change to either branch is a deliberate one.
        entry = get_workflow(result["workflow_id"])
        assert entry["status"] == "failed"
        assert entry["errors"] == result["warnings"]

    async def test_workflow_id_is_unique_per_run(self, monkeypatch):
        from backend.workflows.sales_tracker_workflow import run_sales_tracker_workflow

        _patch_all_steps_ok(monkeypatch)
        r1 = await run_sales_tracker_workflow(db=object())
        r2 = await run_sales_tracker_workflow(db=object())

        assert r1["workflow_id"] != r2["workflow_id"]
        assert r1["workflow_id"].startswith("wf_")
