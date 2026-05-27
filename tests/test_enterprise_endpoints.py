from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import agent as agent_router
from backend.routers import forecasting as forecasting_router
from backend.routers import payout_audit as payout_audit_router


class _ScalarResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _query):
        return _ScalarResult(self._rows)


def test_model_cards_endpoint_has_expected_catalog() -> None:
    app = FastAPI()
    app.include_router(forecasting_router.router)

    client = TestClient(app)
    res = client.get("/ml/model-cards")

    assert res.status_code == 200
    body = res.json()
    assert "generated_at" in body
    card_names = {c["model_name"] for c in body.get("model_cards", [])}
    assert {"revenue_forecast", "deal_scoring", "rep_clustering", "deal_slip"}.issubset(card_names)


def test_model_card_endpoint_returns_404_for_unknown_name() -> None:
    app = FastAPI()
    app.include_router(forecasting_router.router)

    client = TestClient(app)
    res = client.get("/ml/model-cards/does_not_exist")

    assert res.status_code == 404


def test_model_monitoring_summary_shape(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(forecasting_router.router)

    rows = [
        SimpleNamespace(
            model_name="revenue_forecast",
            model_version="v1",
            trained_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            metrics={"mape": 0.12},
        ),
        SimpleNamespace(
            model_name="deal_scoring",
            model_version="v2",
            trained_at=datetime(2026, 5, 2, tzinfo=timezone.utc),
            metrics={"cv_roc_auc": 0.78},
        ),
    ]

    async def fake_db():
        yield _FakeDB(rows)

    app.dependency_overrides[forecasting_router.get_db] = fake_db
    app.dependency_overrides[forecasting_router.get_current_company_id] = lambda: "techo-solutions"

    client = TestClient(app)
    res = client.get("/ml/model-monitoring/summary")

    assert res.status_code == 200
    body = res.json()
    assert body["company_id"] == "techo-solutions"
    assert isinstance(body.get("models"), list)
    assert len(body["models"]) == 2
    assert all("recommended_action" in row for row in body["models"])


def test_payouts_list_filters_by_lifecycle_state(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(payout_audit_router.router)

    monkeypatch.setattr(
        payout_audit_router,
        "list_payouts",
        lambda company_id=None: [
            {"payout_id": "p-1", "company_id": company_id, "lifecycle_state": "approved"},
            {"payout_id": "p-2", "company_id": company_id, "lifecycle_state": "draft"},
        ],
    )

    app.dependency_overrides[payout_audit_router.get_current_company_id] = lambda: "techo-solutions"

    client = TestClient(app)
    res = client.get("/payouts?lifecycle_state=approved")

    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["rows"][0]["payout_id"] == "p-1"


def test_payout_approval_blocked_when_critical_issues_exist(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(payout_audit_router.router)

    async def fake_db():
        yield object()

    async def fake_critical_issues(_db):
        return [{"name": "orphaned_revenue_records", "severity": "critical"}]

    monkeypatch.setattr(payout_audit_router, "get_critical_issues", fake_critical_issues)
    app.dependency_overrides[payout_audit_router.get_db] = fake_db

    client = TestClient(app)
    res = client.post(
        "/payouts/p-123/approve",
        json={"note": "reviewed"},
        headers={"X-User-Role": "revops_admin"},
    )

    assert res.status_code == 409
    detail = res.json().get("detail", {})
    assert "critical_issues" in detail


def test_agent_sensitive_action_guardrail_contract() -> None:
    app = FastAPI()
    app.include_router(agent_router.router)

    async def fake_db():
        yield object()

    app.dependency_overrides[agent_router.get_db] = fake_db

    client = TestClient(app)
    res = client.post(
        "/agent/chat",
        json={"message": "Please approve payout for rep R-42", "history": []},
    )

    assert res.status_code == 200
    body = res.json()
    assert body["requires_confirmation"] is True
    assert isinstance(body.get("assumptions"), list)
    assert isinstance(body.get("recommended_next_action"), str)
    assert body["recommended_next_action"]
    assert body["answer"] == body["reply"]
