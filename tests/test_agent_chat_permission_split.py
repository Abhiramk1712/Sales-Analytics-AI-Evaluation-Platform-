"""
tests/test_agent_chat_permission_split.py
===========================================
The AI Agent tab is listed as visible to sales_manager and sales_rep in
frontend/src/App.jsx's ROLE_TAB_ACCESS, but every route on
backend/routers/agent.py's router was gated behind a single blanket
require_permission("run_agent_workflow") -- a permission only executive
and revops_admin hold. Confirmed live: a sales_rep/sales_manager session
got 403 on every chat request, and the frontend silently rendered "No
response" with no error banner (frontend/src/pages/AgentPage.jsx's
fallback-fetch bug, fixed separately).

Since a router-level dependency runs unconditionally for every route on
that router with no per-route override, admitting sales_rep/sales_manager
to POST /agent/chat and /agent/chat/stream without also loosening
GET /agent/ml-evidence and POST /agent/workflows/sales-performance (both
revops/exec-appropriate, both with zero frontend callers) needed a second
router sharing the /agent prefix -- same pattern already used for
GET /payout/statements/{rep_id} (tests/test_payout_statements_permission.py).

view_own_permission is view_own_metrics -- already held by both
sales_rep and sales_manager for exactly this "see my own numbers" case.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import agent as agent_router


class _FakeScalarResult:
    def scalars(self):
        return self

    def first(self):
        return None

    def all(self):
        return []

    def scalar(self):
        return 0


class _FakeDB:
    async def execute(self, *_args, **_kwargs):
        return _FakeScalarResult()


def _build_app():
    app = FastAPI()
    app.include_router(agent_router.router)
    app.include_router(agent_router.chat_router)

    async def fake_db():
        yield _FakeDB()

    app.dependency_overrides[agent_router.get_db] = fake_db
    return app


def test_sales_rep_can_reach_chat_but_not_admin_only_agent_routes():
    client = TestClient(_build_app())
    headers = {"X-User-Role": "sales_rep", "X-Company-Id": "techo-solutions"}

    chat_res = client.post("/agent/chat", headers=headers, json={"message": "how am I doing?"})
    assert chat_res.status_code != 403, chat_res.json()

    ml_evidence_res = client.get("/agent/ml-evidence", headers=headers)
    assert ml_evidence_res.status_code == 403

    workflow_res = client.post("/agent/workflows/sales-performance", headers=headers, json={})
    assert workflow_res.status_code == 403


def test_sales_manager_can_reach_chat_but_not_admin_only_agent_routes():
    client = TestClient(_build_app())
    headers = {"X-User-Role": "sales_manager", "X-Company-Id": "techo-solutions"}

    chat_res = client.post("/agent/chat", headers=headers, json={"message": "how is my team doing?"})
    assert chat_res.status_code != 403, chat_res.json()

    ml_evidence_res = client.get("/agent/ml-evidence", headers=headers)
    assert ml_evidence_res.status_code == 403


def test_executive_and_revops_admin_still_reach_every_agent_route():
    client = TestClient(_build_app())
    for role in ("executive", "revops_admin"):
        headers = {"X-User-Role": role, "X-Company-Id": "techo-solutions"}
        chat_res = client.post("/agent/chat", headers=headers, json={"message": "how is revenue trending?"})
        assert chat_res.status_code != 403, f"{role}: {chat_res.status_code} {chat_res.json()}"

        ml_evidence_res = client.get("/agent/ml-evidence", headers=headers)
        assert ml_evidence_res.status_code != 403, f"{role}: {ml_evidence_res.status_code}"
