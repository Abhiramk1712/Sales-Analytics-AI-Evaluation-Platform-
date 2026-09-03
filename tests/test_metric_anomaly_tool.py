"""
tests/test_metric_anomaly_tool.py
==================================
backend/ml/anomaly_detector.py::detect_anomalies() had no caller anywhere in
the backend — its own docstring claimed to feed explain_metric_change(), but
that function only ever compares two point-in-time snapshots (current vs.
previous), never a history to detect anomalies against, and never called it
regardless. get_metric_anomaly_summary() (backend/agent/tools/ml_tools.py) is
the real integration: it builds the monthly revenue history that already
exists for forecasting, and wires detect_anomalies() into the agent's
anomaly_question intent — which previously gathered generic KPI/deal-risk
evidence for a question like "any revenue spikes this year?", nothing about
anomalies at all.

Most tests here fake the DB session (a grouped select().all() query, easy to
stand in for without a real Postgres) — the executor-wiring test at the
bottom uses a real one via db_schema, since that's what's actually new.
"""
from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from backend.agent.executor import ToolExecutor
from backend.agent.state import AgentState
from backend.agent.tools.ml_tools import get_metric_anomaly_summary
from backend.database import get_session_factory
from backend.models import Revenue, Rep
from backend.tenancy import tenant_scope
from backend.tenant_guard import unscoped


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _FakeResult(self._rows)


def _rev_row(period: str, total: float):
    return SimpleNamespace(period=period, total=total)


@pytest.mark.asyncio
async def test_too_few_periods_returns_a_warning_not_a_crash():
    db = _FakeDB([_rev_row("2026-01", 100_000), _rev_row("2026-02", 105_000)])
    result = await get_metric_anomaly_summary(db)
    assert result["tool_name"] == "get_metric_anomaly_summary"
    assert result["status"] == "warning"
    assert result["data"]["anomaly_count"] == 0
    assert any("at least 4" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_flat_revenue_history_reports_no_anomalies():
    rows = [_rev_row(f"2026-{m:02d}", 100_000) for m in range(1, 9)]
    db = _FakeDB(rows)
    result = await get_metric_anomaly_summary(db)
    assert result["status"] == "success"
    assert result["data"]["anomaly_count"] == 0
    assert result["data"]["periods_analyzed"] == 8


@pytest.mark.asyncio
async def test_a_revenue_spike_is_flagged_with_a_warning():
    rows = [_rev_row(f"2026-{m:02d}", 100_000) for m in range(1, 7)]
    rows.append(_rev_row("2026-07", 900_000))  # obvious spike
    db = _FakeDB(rows)
    result = await get_metric_anomaly_summary(db)

    assert result["status"] == "warning"
    assert result["data"]["anomaly_count"] >= 1
    flagged_periods = {a["label"] for a in result["data"]["anomalies"]}
    assert "2026-07" in flagged_periods
    assert any("flagged as anomalous" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_months_parameter_limits_history_to_the_trailing_window():
    rows = [_rev_row(f"2026-{m:02d}", 100_000) for m in range(1, 13)]
    db = _FakeDB(rows)
    result = await get_metric_anomaly_summary(db, months=4)
    assert result["data"]["periods_analyzed"] == 4


# ── Executor wiring: the anomaly_question intent now actually detects anomalies ──

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


COMPANY = f"test-anomaly-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def cleanup():
    yield
    factory = get_session_factory()
    async with factory() as db, unscoped():
        from sqlalchemy import delete
        await db.execute(delete(Revenue).where(Revenue.company_id == COMPANY))
        await db.execute(delete(Rep).where(Rep.company_id == COMPANY))
        await db.commit()


@pytest.mark.asyncio
async def test_anomaly_question_intent_now_calls_the_metric_anomaly_tool(cleanup):
    """This is the actual regression this file exists to prevent: before this
    change, routing a message to anomaly_question never touched anomaly
    detection at all."""
    factory = get_session_factory()
    async with factory() as db, tenant_scope(COMPANY):
        rep = Rep(name="Anomaly Rep", email="anomaly-test@example.com")
        db.add(rep)
        await db.flush()
        for i, m in enumerate(range(1, 9)):
            db.add(Revenue(rep_id=rep.id, period=f"2026-{m:02d}", amount=100_000))
        await db.commit()

        state = AgentState(user_message="were there any unusual revenue spikes?", intent="anomaly_question")
        result_state = await ToolExecutor().execute_for_intent(state, db)

    assert "get_metric_anomaly_summary" in result_state.tools_called
    anomaly_evidence = next(r for r in result_state.evidence_results if r["tool_name"] == "get_metric_anomaly_summary")
    assert anomaly_evidence["data"]["periods_analyzed"] == 8
