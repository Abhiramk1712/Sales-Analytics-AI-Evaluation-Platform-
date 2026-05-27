"""Tests for deal slip model (backend/ml/deal_slip.py)."""
from __future__ import annotations

import pandas as pd
import pytest

from backend.ml.deal_slip import DealSlipModel, SLIP_THRESHOLD


def _make_deals(n=20):
    import random
    random.seed(42)
    rows = []
    for i in range(n):
        rows.append({
            "deal_id": f"D{i}",
            "rep_id": "R1",
            "stage": random.choice(["Qualification", "Proposal", "Negotiation"]),
            "amount": random.uniform(10000, 200000),
            "created_at": pd.Timestamp("2024-01-01") + pd.Timedelta(days=random.randint(0, 60)),
            "expected_close_date": pd.Timestamp("2024-03-01") + pd.Timedelta(days=random.randint(-20, 30)),
            "actual_close_date": None,
            "close_probability": random.uniform(0.1, 0.9),
        })
    return pd.DataFrame(rows)


def _make_activities(deals_df):
    rows = []
    idx = 0
    for did in deals_df["deal_id"]:
        for j in range(3):
            rows.append({
                "id": f"A{idx}",
                "deal_id": did,
                "activity_date": pd.Timestamp("2024-02-01") + pd.Timedelta(days=j * 7),
                "activity_type": "call",
            })
            idx += 1
    return pd.DataFrame(rows)


def test_deal_slip_model_fit_predict():
    deals = _make_deals(60)
    activities = _make_activities(deals)
    model = DealSlipModel()
    model.fit(deals, activities)
    results = model.predict(deals, activities)
    assert len(results) > 0


def test_results_sorted_descending():
    deals = _make_deals(60)
    activities = _make_activities(deals)
    model = DealSlipModel()
    model.fit(deals, activities)
    results = model.predict(deals, activities)
    scores = [r.slip_risk_score for r in results]
    assert scores == sorted(scores, reverse=True), "Results should be sorted by slip_score descending"


def test_slip_score_range():
    deals = _make_deals(60)
    activities = _make_activities(deals)
    model = DealSlipModel()
    model.fit(deals, activities)
    results = model.predict(deals, activities)
    for r in results:
        assert 0.0 <= r.slip_risk_score <= 1.0, f"slip_score out of range: {r.slip_risk_score}"


def test_result_has_deal_id():
    deals = _make_deals(60)
    activities = _make_activities(deals)
    model = DealSlipModel()
    model.fit(deals, activities)
    results = model.predict(deals, activities)
    for r in results:
        assert r.deal_id is not None


def test_empty_activities():
    deals = _make_deals(60)
    activities = pd.DataFrame(columns=["id", "deal_id", "activity_date", "activity_type"])
    model = DealSlipModel()
    model.fit(deals, activities)
    results = model.predict(deals, activities)
    assert len(results) == len(deals)


def test_slip_threshold_constant():
    assert 0.0 < SLIP_THRESHOLD < 1.0
