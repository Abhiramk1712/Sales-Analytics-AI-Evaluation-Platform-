from datetime import datetime, timedelta, timezone

from backend.ml.deal_scoring import prepare_training_frame, run_deal_scoring
from backend.ml.explainability import explain_deal_prediction, global_feature_importance


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_dataset(n_closed=30, n_open=8):
    deals = []
    for i in range(n_closed):
        stage = "Closed Won" if i % 2 == 0 else "Closed Lost"
        deals.append(
            {
                "id": f"c{i}",
                "stage": stage,
                "amount": 40000 + i * 1000,
                "close_probability": 75 if stage == "Closed Won" else 25,
                "industry": "SaaS",
                "product": "Enterprise" if i % 3 else "Pro",
                "created_at": _utc_now_naive() - timedelta(days=100 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=10),
                "activity_count": 3 + (i % 6),
                "days_since_last_activity": 4 + (i % 8),
            }
        )

    for i in range(n_open):
        deals.append(
            {
                "id": f"o{i}",
                "stage": "Proposal",
                "amount": 30000 + i * 2000,
                "close_probability": 45,
                "industry": "Healthcare",
                "product": "Starter",
                "created_at": _utc_now_naive() - timedelta(days=20 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=25),
                "activity_count": 2 + (i % 3),
                "days_since_last_activity": 2 + (i % 5),
            }
        )

    activities = [
        {
            "deal_id": d["id"],
            "activity_date": _utc_now_naive() - timedelta(days=2),
            "notes": "Approved next step but budget concern remains.",
        }
        for d in deals
    ]
    return deals, activities


def test_global_feature_importance_returns_features():
    deals, activities = _make_dataset()
    train_result = run_deal_scoring(deals, activities=activities, return_model=True)
    model = train_result["_model"]

    frame, _, _ = prepare_training_frame(deals, activities=activities)
    labelled = frame[frame["target"].notna()].copy()

    result = global_feature_importance(model, labelled)
    assert "method" in result
    assert len(result.get("features", [])) > 0


def test_explain_deal_prediction_returns_probability():
    deals, activities = _make_dataset()
    train_result = run_deal_scoring(deals, activities=activities, return_model=True)
    model = train_result["_model"]

    frame, _, _ = prepare_training_frame(deals, activities=activities)
    open_row = frame[frame["target"].isna()].iloc[[0]]

    result = explain_deal_prediction(model, open_row)
    assert 0.0 <= result["win_probability"] <= 1.0
    assert isinstance(result.get("top_factors", []), list)
