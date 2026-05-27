from __future__ import annotations

import uuid
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import plans as plans_router


class _ScalarResult:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None


class _PlansDB:
    def __init__(self, plan_id: uuid.UUID):
        self.plan_id = plan_id

    async def get(self, model, key):
        if model is plans_router.Plan and key == self.plan_id:
            return SimpleNamespace(
                id=key,
                name="FY2026 Global Plan",
                scope="global",
                effective_start_date=None,
                effective_end_date=None,
                owner_user_id=None,
            )
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM rules" in sql:
            return _ScalarResult(
                rows=[
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        plan_id=self.plan_id,
                        name="AE Attainment Tier",
                        metric_name="attainment_pct",
                        threshold_min=80,
                        threshold_max=100,
                        rate=0.05,
                        bonus_amount=1000,
                    )
                ]
            )
        if "FROM plan_assignments" in sql:
            return _ScalarResult(
                rows=[
                    SimpleNamespace(
                        id=uuid.uuid4(),
                        user_id=uuid.uuid4(),
                        effective_start_date=None,
                        effective_end_date=None,
                    )
                ]
            )
        if "FROM revenue" in sql:
            return _ScalarResult(value=150000.0)
        return _ScalarResult(rows=[])


def test_list_all_rules_uses_actual_rule_fields():
    plan_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(plans_router.router)

    fake_db = _PlansDB(plan_id)

    async def _override_db():
        yield fake_db

    app.dependency_overrides[plans_router.get_db] = _override_db
    client = TestClient(app)

    res = client.get("/plans/rules/all")
    assert res.status_code == 200
    payload = res.json()
    assert payload["rules"]

    rule = payload["rules"][0]
    assert "name" in rule
    assert "metric_name" in rule
    assert "threshold_min" in rule
    assert "threshold_max" in rule
    assert "bonus_amount" in rule

    # Legacy/non-existent ORM fields must not leak.
    assert "rule_type" not in rule
    assert "metric_type" not in rule
    assert "threshold_low" not in rule
    assert "threshold_high" not in rule
    assert "cap_amount" not in rule
    assert "accelerator_rate" not in rule


def test_plan_performance_uses_rep_mapping_and_quota_service(monkeypatch):
    plan_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(plans_router.router)

    fake_db = _PlansDB(plan_id)

    async def _override_db():
        yield fake_db

    app.dependency_overrides[plans_router.get_db] = _override_db

    async def fake_rep_ids(_db, _user_ids):
        return [uuid.uuid4(), uuid.uuid4()]

    async def fake_quota(_db, _period, rep_id=None, user_id=None):
        return 100000.0, "direct", []

    monkeypatch.setattr(plans_router, "get_rep_ids_for_user_ids", fake_rep_ids)
    monkeypatch.setattr(plans_router, "get_quota_for_period", fake_quota)

    client = TestClient(app)
    res = client.get(f"/plans/{plan_id}/performance?period=2025-Q2")

    assert res.status_code == 200
    payload = res.json()
    assert payload["plan_name"] == "FY2026 Global Plan"
    assert payload["total_revenue"] == 150000.0
    assert payload["total_quota"] == 200000.0
    assert payload["attainment_pct"] == 75.0
