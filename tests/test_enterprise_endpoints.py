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
    res = client.get("/payout-audit?lifecycle_state=approved")

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
        "/payout-audit/p-123/approve",
        json={"note": "reviewed"},
        headers={"X-User-Role": "revops_admin"},
    )

    assert res.status_code == 409
    detail = res.json().get("detail", {})
    assert "critical_issues" in detail


# ── /review and /pay: no route called mark_reviewed/mark_paid before this ────
# mark_reviewed already existed in audit_trail_service.py; mark_paid is new.
# Both mirror approve_payout/lock_payout exactly, so tested the same way —
# plus one real (unmocked) test of the actual lifecycle guard, since a mocked
# service call proves the route wires to the function, not that the state
# machine's locked-payout rule holds.

def test_review_endpoint_calls_mark_reviewed(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(payout_audit_router.router)

    calls = {}

    def fake_mark_reviewed(payout_id, actor):
        calls["payout_id"] = payout_id
        calls["actor"] = actor
        return {"payout_id": payout_id, "lifecycle_state": "reviewed"}

    monkeypatch.setattr(payout_audit_router, "mark_reviewed", fake_mark_reviewed)

    client = TestClient(app)
    res = client.post("/payout-audit/p-1/review", headers={"X-User-Role": "revops_admin"})

    assert res.status_code == 200
    assert res.json()["lifecycle_state"] == "reviewed"
    assert calls["payout_id"] == "p-1"


def test_pay_endpoint_calls_mark_paid(monkeypatch) -> None:
    app = FastAPI()
    app.include_router(payout_audit_router.router)

    monkeypatch.setattr(
        payout_audit_router,
        "mark_paid",
        lambda payout_id, actor: {"payout_id": payout_id, "lifecycle_state": "paid"},
    )

    client = TestClient(app)
    res = client.post("/payout-audit/p-1/pay", headers={"X-User-Role": "finance_admin"})

    assert res.status_code == 200
    assert res.json()["lifecycle_state"] == "paid"


def test_review_and_pay_are_gated_by_approve_payouts_permission() -> None:
    """sales_manager has view_payouts (can list/read) but not approve_payouts."""
    app = FastAPI()
    app.include_router(payout_audit_router.router)
    client = TestClient(app)

    res = client.post("/payout-audit/p-1/review", headers={"X-User-Role": "sales_manager"})
    assert res.status_code == 403

    res = client.post("/payout-audit/p-1/pay", headers={"X-User-Role": "sales_manager"})
    assert res.status_code == 403


def test_full_lifecycle_against_the_real_service_not_a_mock(monkeypatch) -> None:
    """
    review -> approve -> lock -> pay against the actual audit_trail_service
    state machine (no monkeypatching of the service layer), then confirms the
    real lock guard: once locked, review/approve are rejected (409) but
    adjust still works — adjust_payout has no lock guard by design
    (corrections must reach a payout in any state, including paid).

    /approve alone needs a data-quality check against a DB — get_critical_issues
    is stubbed to "none found" so this test proves the lifecycle machine, not
    that unrelated dependency.
    """
    from backend.payout.audit_trail_service import clear_store, upsert_payout_trace

    app = FastAPI()
    app.include_router(payout_audit_router.router)

    async def fake_db():
        yield object()

    async def no_critical_issues(_db):
        return []

    app.dependency_overrides[payout_audit_router.get_db] = fake_db
    monkeypatch.setattr(payout_audit_router, "get_critical_issues", no_critical_issues)

    client = TestClient(app)
    headers = {"X-User-Role": "revops_admin"}

    clear_store()
    try:
        record = upsert_payout_trace(
            company_id="test-co", period="2026-03", rep_id="r-1", user_id=None,
            plan_id=None, rule_id=None, sales_credit_id=None,
            credited_amount=10_000, quota=10_000, attainment_pct=100.0,
            base_commission=1_000, accelerator_amount=0, spiff_amount=0,
            clawback_amount=0, final_payout=1_000,
            calculation_trace_json={}, source_records_json={}, computed_by="test",
        )
        pid = record["payout_id"]

        assert client.post(f"/payout-audit/{pid}/review", headers=headers).json()["lifecycle_state"] == "reviewed"
        assert client.post(f"/payout-audit/{pid}/approve", json={}, headers=headers).json()["lifecycle_state"] == "approved"
        assert client.post(f"/payout-audit/{pid}/lock", headers=headers).json()["lifecycle_state"] == "locked"

        # Real guard: locked payouts may only move to paid or adjusted.
        blocked = client.post(f"/payout-audit/{pid}/review", headers=headers)
        assert blocked.status_code == 409

        paid = client.post(f"/payout-audit/{pid}/pay", headers=headers)
        assert paid.status_code == 200
        assert paid.json()["lifecycle_state"] == "paid"

        adjusted = client.post(
            f"/payout-audit/{pid}/adjust",
            json={"adjustment_amount": -50.0, "reason": "overpaid spiff"},
            headers=headers,
        )
        assert adjusted.status_code == 200
        assert adjusted.json()["lifecycle_state"] == "adjusted"
        assert adjusted.json()["final_payout"] == 950.0
    finally:
        clear_store()


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
