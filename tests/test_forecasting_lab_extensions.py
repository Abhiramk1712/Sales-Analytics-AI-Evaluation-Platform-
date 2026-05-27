from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import forecasting as forecasting_router


async def _fake_db():
    yield object()


async def _fake_history_loader(db, forecast_type):
    return [100.0, 110.0, 120.0, 125.0], ["2025-01", "2025-02", "2025-03", "2025-04"], "synthetic", []


def _make_app(monkeypatch):
    app = FastAPI()
    app.include_router(forecasting_router.router)
    app.dependency_overrides[forecasting_router.get_db] = _fake_db
    monkeypatch.setattr(forecasting_router, "_load_history_for_forecast_type", _fake_history_loader)
    return app


def test_forecast_targets_endpoint(monkeypatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    res = client.get("/ml/forecast/targets")
    assert res.status_code == 200
    payload = res.json()
    assert "targets" in payload
    assert isinstance(payload["targets"], list)
    assert len(payload["targets"]) > 0
    assert "target" in payload["targets"][0]


def test_forecast_compare_models_endpoint(monkeypatch):
    app = _make_app(monkeypatch)

    def fake_compare(**kwargs):
        return {
            "status": "ok",
            "selected_model": "ridge",
            "selected_strategy": "ridge",
            "periods": ["2025-05", "2025-06"],
            "values": [130.0, 132.0],
            "leaderboard": [{"model": "ridge", "rank": 1, "backtest": {"status": "ok", "mape": 8.0}}],
            "backtest": {"status": "ok", "mape": 8.0},
            "warnings": [],
        }

    monkeypatch.setattr(forecasting_router, "compare_models_for_target", fake_compare)

    client = TestClient(app)
    res = client.post(
        "/ml/forecast/compare-models",
        json={"target": "revenue", "horizon": 2, "scenario": "base", "include_lstm": True},
    )
    assert res.status_code == 200
    payload = res.json()
    assert payload["selected_model"] == "ridge"
    assert len(payload["leaderboard"]) == 1


def test_forecast_run_and_scenario_endpoint(monkeypatch):
    app = _make_app(monkeypatch)

    def fake_run(**kwargs):
        return {
            "status": "ok",
            "target": "revenue",
            "scenario": kwargs.get("scenario", "base"),
            "selected_model": "sarimax",
            "periods": ["2025-05", "2025-06"],
            "values": [140.0, 145.0],
            "lower_bound": [130.0, 135.0],
            "upper_bound": [150.0, 155.0],
            "warnings": [],
        }

    monkeypatch.setattr(forecasting_router, "run_forecast_for_target", fake_run)

    client = TestClient(app)
    run_res = client.post(
        "/ml/forecast/run",
        json={"target": "revenue", "horizon": 2, "scenario": "optimistic", "include_lstm": True},
    )
    assert run_res.status_code == 200
    run_payload = run_res.json()
    assert run_payload["selected_model"] == "sarimax"
    assert run_payload["scenario"] == "optimistic"

    scenario_res = client.post(
        "/ml/forecast/scenario",
        json={"target": "revenue", "horizon": 2, "scenario": "conservative", "include_lstm": True},
    )
    assert scenario_res.status_code == 200
    assert scenario_res.json()["scenario"] == "conservative"


def test_forecast_lstm_endpoint(monkeypatch):
    app = _make_app(monkeypatch)

    def fake_lstm(history, horizon):
        return {
            "strategy_used": "lstm",
            "forecast_values": [200.0, 205.0][:horizon],
            "backtest": {"status": "ok", "mape": 9.2},
            "warnings": [],
            "history_months": len(history),
            "torch_available": True,
        }

    def fake_compare(**kwargs):
        return {
            "selected_model": "lstm",
            "leaderboard": [{"model": "lstm", "rank": 1, "backtest": {"status": "ok", "mape": 9.2}}],
        }

    monkeypatch.setattr(forecasting_router, "run_lstm_forecast", fake_lstm)
    monkeypatch.setattr(forecasting_router, "compare_models_for_target", fake_compare)

    client = TestClient(app)
    res = client.get("/ml/forecast/lstm?target=revenue&horizon=2")
    assert res.status_code == 200
    payload = res.json()
    assert payload["strategy_used"] == "lstm"
    assert payload["torch_available"] is True
    assert payload["compare_models"]["selected_model"] == "lstm"
