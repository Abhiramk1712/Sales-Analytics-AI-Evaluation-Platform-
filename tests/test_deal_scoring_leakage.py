from backend.ml.deal_scoring import get_allowed_deal_features


def test_allowed_deal_features_exclude_leakage_columns():
    allowed = get_allowed_deal_features()
    assert "close_probability" not in allowed
    assert "actual_close_date" not in allowed
    assert "closed_lost_reason" not in allowed


def test_allowed_deal_features_include_pre_outcome_signals():
    allowed = get_allowed_deal_features()
    assert "amount" in allowed
    assert "stage" in allowed
    assert "activity_count" in allowed
