from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routers import forecasting as forecasting_router


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _fake_db():
    yield object()


def _build_dataset(n_closed=36, n_open=12):
    deals = []
    for i in range(n_closed):
        stage = "Closed Won" if i % 2 == 0 else "Closed Lost"
        deals.append(
            {
                "id": f"c{i}",
                "stage": stage,
                "amount": 40000 + i * 1000,
                "close_probability": 80 if stage == "Closed Won" else 20,
                "industry": "SaaS",
                "product": "Enterprise" if i % 3 else "Pro",
                "created_at": _utc_now_naive() - timedelta(days=120 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=14),
                "activity_count": 3 + (i % 4),
                "days_since_last_activity": 3 + (i % 6),
            }
        )

    for i in range(n_open):
        deals.append(
            {
                "id": f"o{i}",
                "stage": "Proposal",
                "amount": 30000 + i * 1500,
                "close_probability": 45,
                "industry": "Healthcare",
                "product": "Starter",
                "created_at": _utc_now_naive() - timedelta(days=30 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=30),
                "activity_count": 1 + (i % 4),
                "days_since_last_activity": 2 + (i % 8),
            }
        )

    activities = [
        {
            "deal_id": d["id"],
            "activity_date": _utc_now_naive() - timedelta(days=2),
            "notes": "Customer approved next step but flagged security concern.",
        }
        for d in deals
    ]
    return deals, activities


def _make_app(monkeypatch):
    app = FastAPI()
    app.include_router(forecasting_router.router)
    app.dependency_overrides[forecasting_router.get_db] = _fake_db

    deals, activities = _build_dataset()

    async def fake_collect(db):
        return deals, activities, len(activities)

    monkeypatch.setattr(forecasting_router, "_collect_deal_scoring_inputs", fake_collect)
    return app


def test_evaluate_deal_scoring_endpoint(monkeypatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    res = client.get("/ml/evaluate/deal-scoring")
    assert res.status_code == 200
    payload = res.json()
    assert "metrics" in payload
    assert "confusion_matrix" in payload
    assert "class_distribution" in payload


def test_explain_endpoints(monkeypatch):
    app = _make_app(monkeypatch)
    client = TestClient(app)

    global_res = client.get("/ml/explain/global-importance?top_n=5")
    assert global_res.status_code == 200
    global_payload = global_res.json()
    assert "top_features" in global_payload

    deal_res = client.get("/ml/explain/deal/o1")
    assert deal_res.status_code == 200
    deal_payload = deal_res.json()
    assert "win_probability" in deal_payload
    assert "top_factors" in deal_payload
