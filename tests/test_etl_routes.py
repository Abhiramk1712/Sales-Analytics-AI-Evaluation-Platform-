from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import etl as etl_router


def test_etl_run_route_success(monkeypatch):
    app = FastAPI()
    app.include_router(etl_router.router)

    fake_result = SimpleNamespace(
        generated_at="2026-01-01T00:00:00+00:00",
        bronze={"revenue": {"row_count": 10}},
        quality={"revenue": {"issues": [], "data_quality_score": 100.0, "row_count": 10}},
        gold={
            "mart_rep_month_performance": {
                "metadata": {"row_count": 8, "data_quality_score": 92.0, "warnings": []}
            }
        },
        feature_store={"forecast_revenue_monthly": object()},
    )

    monkeypatch.setattr(etl_router, "run_etl_pipeline", lambda _: fake_result)

    client = TestClient(app)
    res = client.post("/etl/run", params={"source_dir": "companies/techo-solutions"})

    assert res.status_code == 200
    payload = res.json()
    assert payload["bronze_tables"]["revenue"] == 10
    assert "mart_rep_month_performance" in payload["gold_marts"]
    assert "forecast_revenue_monthly" in payload["feature_store_tables"]


def test_etl_run_route_not_found(monkeypatch):
    app = FastAPI()
    app.include_router(etl_router.router)

    def _raise(_):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(etl_router, "run_etl_pipeline", _raise)

    client = TestClient(app)
    res = client.post("/etl/run", params={"source_dir": "missing"})

    assert res.status_code == 404
    assert "missing" in res.json()["detail"]
