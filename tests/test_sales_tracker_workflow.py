"""
tests/test_sales_tracker_workflow.py
======================================
Tests for the SalesTracker workflow (logic/structure layer — no live DB needed).
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
