from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import data_quality as dq_router


def test_data_quality_summary_route(monkeypatch):
    app = FastAPI()
    app.include_router(dq_router.router)

    async def fake_build_checks(_db):
        return [
            {"name": "empty_table_reps", "status": "PASS", "message": "ok", "affected_rows": 0},
            {"name": "missing_quota", "status": "WARN", "message": "warn", "affected_rows": 2},
        ]

    async def fake_db():
        yield object()

    monkeypatch.setattr(dq_router, "_build_checks", fake_build_checks)
    app.dependency_overrides[dq_router.get_db] = fake_db

    client = TestClient(app)
    res = client.get("/data-quality/summary")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "WARN"
    assert "score" in body
    assert isinstance(body["checks"], list)


def test_data_quality_checks_route(monkeypatch):
    app = FastAPI()
    app.include_router(dq_router.router)

    async def fake_build_checks(_db):
        return [{"name": "negative_revenue", "status": "FAIL", "message": "bad", "affected_rows": 1}]

    async def fake_db():
        yield object()

    monkeypatch.setattr(dq_router, "_build_checks", fake_build_checks)
    app.dependency_overrides[dq_router.get_db] = fake_db

    client = TestClient(app)
    res = client.get("/data-quality/checks")
    assert res.status_code == 200
    body = res.json()
    assert "checks" in body
    assert body["checks"][0]["name"] == "negative_revenue"