from datetime import datetime, timedelta, timezone

from backend.ml.deal_scoring import optimize_hyperparameters, prepare_training_frame, run_deal_scoring
from backend.ml.text_features import TEXT_FEATURE_COLUMNS


def _utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_deals(n_closed=30, n_open=10):
    deals = []
    closed_stages = ["Closed Won", "Closed Lost"]
    open_stages = ["Prospecting", "Qualification", "Proposal", "Negotiation"]

    for i in range(n_closed):
        stage = closed_stages[i % 2]
        deals.append(
            {
                "id": f"c-{i}",
                "stage": stage,
                "amount": 50000 + i * 1200,
                "close_probability": 80 if stage == "Closed Won" else 20,
                "industry": "SaaS" if i % 2 == 0 else "Healthcare",
                "product": "Enterprise" if i % 3 == 0 else "Starter",
                "created_at": _utc_now_naive() - timedelta(days=90 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=30),
                "activity_count": 3 + (i % 5),
                "days_since_last_activity": 2 + (i % 10),
            }
        )

    for i in range(n_open):
        deals.append(
            {
                "id": f"o-{i}",
                "stage": open_stages[i % len(open_stages)],
                "amount": 30000 + i * 2000,
                "close_probability": 40 + i,
                "industry": "SaaS",
                "product": "Enterprise",
                "created_at": _utc_now_naive() - timedelta(days=20 + i),
                "expected_close_date": _utc_now_naive() + timedelta(days=45),
                "activity_count": 1 + (i % 4),
                "days_since_last_activity": 3 + (i % 6),
            }
        )

    return deals


def _make_activities(deals):
    activities = []
    for d in deals:
        activities.append(
            {
                "deal_id": d["id"],
                "activity_date": _utc_now_naive() - timedelta(days=2),
                "notes": "Customer requested urgent follow up and approved next step.",
            }
        )
    return activities


def test_prepare_training_frame_contains_text_features():
    deals = _make_deals(n_closed=24, n_open=8)
    activities = _make_activities(deals)

    frame, warnings, leakage = prepare_training_frame(deals, activities=activities)
    assert not frame.empty
    assert isinstance(warnings, list)
    assert isinstance(leakage, list)
    for col in TEXT_FEATURE_COLUMNS:
        assert col in frame.columns


def test_run_deal_scoring_with_text_features():
    deals = _make_deals(n_closed=32, n_open=10)
    activities = _make_activities(deals)

    result = run_deal_scoring(deals, activities=activities, optimize=False)
    assert "cv_roc_auc" in result
    assert len(result["scored_deals"]) == 10


def test_optimize_hyperparameters_function_returns_status():
    deals = _make_deals(n_closed=12, n_open=4)
    activities = _make_activities(deals)

    result = optimize_hyperparameters(deals, activities=activities)
    assert result["status"] in {"ok", "insufficient_data", "no_data"}
