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


class _TerritoryDB:
    def __init__(self, territory_id: uuid.UUID):
        self.territory_id = territory_id

    async def get(self, model, key):
        if model is plans_router.Territory and key == self.territory_id:
            return SimpleNamespace(id=key, name="NA Enterprise", region="North America")
        return None

    async def execute(self, stmt):
        sql = str(stmt)
        if "FROM user_territory_assignments" in sql:
            return _ScalarResult(rows=[SimpleNamespace(user_id=uuid.uuid4())])
        if "sum(revenue.amount" in sql.lower():
            return _ScalarResult(value=250000.0)
        if "count(deals.id" in sql.lower():
            return _ScalarResult(value=7)
        return _ScalarResult(rows=[])


def test_territory_performance_uses_valid_deal_and_revenue_fields(monkeypatch):
    territory_id = uuid.uuid4()
    app = FastAPI()
    app.include_router(plans_router.territory_router)

    fake_db = _TerritoryDB(territory_id)

    async def _override_db():
        yield fake_db

    async def fake_rep_ids(_db, _user_ids):
        return [uuid.uuid4(), uuid.uuid4()]

    monkeypatch.setattr(plans_router, "get_rep_ids_for_user_ids", fake_rep_ids)
    app.dependency_overrides[plans_router.get_db] = _override_db

    client = TestClient(app)
    res = client.get(f"/territories/{territory_id}/performance?period=2025-Q2")

    assert res.status_code == 200
    payload = res.json()
    assert payload["territory_name"] == "NA Enterprise"
    assert payload["assigned_reps"] == 2
    assert payload["total_revenue"] == 250000.0
    assert payload["closed_won_deals"] == 7
