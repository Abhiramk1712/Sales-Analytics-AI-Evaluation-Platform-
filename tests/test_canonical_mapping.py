"""
tests/test_canonical_mapping.py
================================
Tests for the canonical data model mapping module.
"""
from backend.transformations.canonical_mapping import (
    map_fields,
    get_source_of_truth,
    BOOKING_TO_REVENUE,
    SALES_CREDIT_TO_PAYOUT_INPUT,
    USER_PROFILE_TO_REP,
    DEAL_TO_OPPORTUNITY,
)


def test_get_source_of_truth_booking():
    assert get_source_of_truth("booking") == "Booking"


def test_get_source_of_truth_revenue():
    assert get_source_of_truth("revenue") == "Revenue"


def test_get_source_of_truth_sales_rep():
    assert get_source_of_truth("sales_rep") == "UserProfile"


def test_get_source_of_truth_unknown():
    result = get_source_of_truth("nonexistent_concept")
    assert result == "Unknown"


def test_map_fields_booking_to_revenue():
    source = {"rep_id": "abc", "booking_date": "2025-01", "amount": 5000.0, "product_sku": "ENT"}
    result = map_fields(source, BOOKING_TO_REVENUE)
    assert result["rep_id"] == "abc"
    assert result["period"] == "2025-01"
    assert result["amount"] == 5000.0
    assert result["product_sku"] == "ENT"


def test_map_fields_drops_unmapped_keys():
    source = {"rep_id": "abc", "unrelated_field": "x", "booking_date": "2025-03"}
    result = map_fields(source, BOOKING_TO_REVENUE)
    assert "unrelated_field" not in result
    assert "period" in result


def test_map_fields_preserves_none_values():
    source = {"rep_id": None, "booking_date": "2025-01"}
    result = map_fields(source, BOOKING_TO_REVENUE)
    assert result["rep_id"] is None


def test_map_fields_sales_credit_to_payout():
    source = {
        "id": "sc-1",
        "user_id": "rep-99",
        "credited_amount": 12000.0,
        "deal_id": "deal-42",
        "period": "2025-03",
        "credit_type": "direct",
        "split_pct": 100.0,
    }
    result = map_fields(source, SALES_CREDIT_TO_PAYOUT_INPUT)
    assert result["sales_credit_id"] == "sc-1"
    assert result["rep_id"] == "rep-99"
    assert result["credited_amount"] == 12000.0
    assert result["source_deal_id"] == "deal-42"
    assert result["period"] == "2025-03"


def test_map_fields_user_profile_to_rep():
    source = {"id": "u1", "name": "Alice", "email": "alice@co.com", "region": "West"}
    result = map_fields(source, USER_PROFILE_TO_REP)
    assert result["name"] == "Alice"
    assert result["region"] == "West"


def test_map_fields_deal_to_opportunity():
    source = {
        "id": "d1",
        "name": "Big Deal",
        "amount": 50000,
        "stage": "Proposal",
        "rep_id": "r1",
        "expected_close_date": "2025-06-30",
    }
    result = map_fields(source, DEAL_TO_OPPORTUNITY)
    assert result["close_date"] == "2025-06-30"
    assert result["owner_id"] == "r1"
    assert result["name"] == "Big Deal"
