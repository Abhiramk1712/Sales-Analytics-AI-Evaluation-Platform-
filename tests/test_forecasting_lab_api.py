from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import forecasting as forecasting_router


async def _fake_db():
    yield object()


def test_forecasting_lab_single_scenario(monkeypatch):
    app = FastAPI()
    app.include_router(forecasting_router.router)

    async def fake_history_loader(db, forecast_type):
        return [100000.0, 110000.0, 120000.0], ["2025-01", "2025-02", "2025-03"], "synthetic", []

    async def fake_persist(**kwargs):
        return True

    monkeypatch.setattr(forecasting_router, "_load_history_for_forecast_type", fake_history_loader)
    monkeypatch.setattr(forecasting_router, "_persist_prediction", fake_persist)
    app.dependency_overrides[forecasting_router.get_db] = _fake_db

    client = TestClient(app)
    res = client.get(
        "/ml/forecast/lab",
        params={
            "forecast_type": "revenue",
            "scenario": "base",
            "horizon": 3,
            "confidence_interval": 0.8,
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["forecast_type"] == "revenue"
    assert payload["scenario"] == "base"
    assert payload["horizon_months"] == 3
    assert len(payload["periods"]) == 3
    assert len(payload["values"]) == 3
    assert "scenario_matrix" not in payload


def test_forecasting_lab_multi_scenario(monkeypatch):
    app = FastAPI()
    app.include_router(forecasting_router.router)

    async def fake_history_loader(db, forecast_type):
        return [200000.0, 210000.0, 220000.0, 230000.0], ["2025-01", "2025-02", "2025-03", "2025-04"], "synthetic", []

    async def fake_persist(**kwargs):
        return True

    monkeypatch.setattr(forecasting_router, "_load_history_for_forecast_type", fake_history_loader)
    monkeypatch.setattr(forecasting_router, "_persist_prediction", fake_persist)
    app.dependency_overrides[forecasting_router.get_db] = _fake_db

    client = TestClient(app)
    res = client.get(
        "/ml/forecast/lab",
        params={
            "forecast_type": "pipeline",
            "scenario": "optimistic",
            "horizon": 4,
            "confidence_interval": 0.9,
            "include_multi_scenario": "true",
        },
    )

    assert res.status_code == 200
    payload = res.json()
    assert payload["forecast_type"] == "pipeline"
    assert payload["scenario"] == "optimistic"
    assert "scenario_matrix" in payload
    assert "base" in payload["scenario_matrix"]
    assert "optimistic" in payload["scenario_matrix"]
    assert "conservative" in payload["scenario_matrix"]


def test_forecasting_lab_rejects_invalid_confidence_interval():
    app = FastAPI()
    app.include_router(forecasting_router.router)
    app.dependency_overrides[forecasting_router.get_db] = _fake_db

    client = TestClient(app)
    res = client.get(
        "/ml/forecast/lab",
        params={
            "forecast_type": "revenue",
            "scenario": "base",
            "horizon": 3,
            "confidence_interval": 0.85,
        },
    )

    assert res.status_code == 400
    assert "confidence_interval" in res.json().get("detail", "")
