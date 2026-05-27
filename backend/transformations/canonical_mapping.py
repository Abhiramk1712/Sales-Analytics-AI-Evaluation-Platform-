"""
backend/transformations/canonical_mapping.py
=============================================
Canonical entity mapping for the sales analytics platform.

This module documents the authoritative source-of-truth for each entity
and provides mapping constants/helpers for use across the platform.

KEY PRINCIPLE
-------------
  Do NOT re-derive the same entity from multiple sources without going through
  this mapping layer first. When in doubt, check which model is the
  "source of truth" in the table below before writing a query.

ENTITY MAP
----------

| Canonical Concept        | Source-of-truth Model | Dashboard Projection   | Notes
|--------------------------|----------------------|------------------------|-------------------------------
| Person / Sales rep       | UserProfile          | Rep                    | Rep is a simplified projection
| Organizational hierarchy | Manager              | Team                   | Team.manager via Manager link
| Pipeline object          | Deal / Opportunity   | Deal                   | Opportunity mirrors Deal for enterprise import
| Closed commercial event  | Booking              | Deal (stage=Closed Won)| Booking has ARR/MRR/term fields
| Recognized period rev.   | Revenue              | Revenue                | Can derive from MonthlyFinance
| Finance period roll-up   | MonthlyFinance       | Revenue (aggregated)   | Higher fidelity when available
| Compensation crediting   | SalesCredit          | Deal (awarded credit)  | Required for credit-level payout
| Computed payout output   | PayoutRecord         | n/a (report only)      | Derived by payout engine
| Quota target             | Quota                | Quota                  | Rep × period × amount
| Comp plan definition     | Plan / Rule          | n/a (engine only)      | Drives payout engine logic
| Sales territory          | Territory            | Territory              | Assigned via UserTerritoryAssignment
| Brand / product line     | Brand                | Deal.product (partial) | Many-to-many with accounts
| Lead / top-funnel        | n/a                  | Deal (early stage)     | Prospecting stage deals
| Engagement signal        | Activity             | Activity               | Phone, email, meeting events
| ARR period movement      | ArrWaterfallEntry    | n/a (analytics only)   | Derived from Bookings+ChurnEvents
| Customer churn signal    | ChurnEvent           | n/a (analytics only)   | Feeds ARR waterfall + NRR/GRR
"""
from __future__ import annotations

from typing import Any


# ── Authoritative source-of-truth lookup ────────────────────────────────

SOURCE_OF_TRUTH: dict[str, str] = {
    "sales_rep":          "UserProfile",
    "person":             "UserProfile",
    "hierarchy":          "Manager",
    "team":               "Team",
    "pipeline":           "Deal",
    "opportunity":        "Deal",       # Opportunity is an alias/import projection
    "booking":            "Booking",
    "revenue":            "Revenue",    # prefer MonthlyFinance when available
    "mrr":                "MonthlyFinance",
    "arr":                "Booking + ChurnEvent",
    "sales_credit":       "SalesCredit",
    "payout":             "PayoutRecord",
    "quota":              "Quota",
    "plan":               "Plan",
    "rule":               "Rule",
    "territory":          "Territory",
    "brand":              "Brand",
    "activity":           "Activity",
    "arr_waterfall":      "ArrWaterfallEntry",
    "churn":              "ChurnEvent",
}


# ── Field-level mappings (UserProfile → Rep) ─────────────────────────────

USER_PROFILE_TO_REP: dict[str, str] = {
    "id":           "id",            # Rep.id may or may not match UserProfile.id
    "external_id":  "external_id",   # link key when syncing
    "name":         "name",
    "email":        "email",
    "region":       "region",
    "hire_date":    "hire_date",
    "team_id":      "team_id",
}

DEAL_TO_OPPORTUNITY: dict[str, str] = {
    "id":                  "id",
    "name":                "name",
    "amount":              "amount",
    "stage":               "stage",
    "close_probability":   "close_probability",
    "expected_close_date": "close_date",
    "actual_close_date":   "closed_at",
    "account_id":          "account_id",
    "rep_id":              "owner_id",
}

BOOKING_TO_REVENUE: dict[str, str] = {
    "rep_id":                 "rep_id",
    "booking_date":           "period",         # period derived from booking_date
    "amount":                 "amount",
    "arr":                    "amount",          # use arr if revenue_type is ARR
    "mrr":                    "amount",          # use mrr if revenue_type is MRR
    "product_sku":            "product_sku",
    "contract_term_months":   "contract_term_months",
    "revenue_type":           "revenue_type",
    "recognition_start_date": "recognition_start_date",
}

SALES_CREDIT_TO_PAYOUT_INPUT: dict[str, str] = {
    "id":               "sales_credit_id",
    "user_id":          "rep_id",
    "credited_amount":  "credited_amount",
    "deal_id":          "source_deal_id",
    "sales_unit_id":    "source_sales_unit_id",
    "period":           "period",
    "credit_type":      "credit_type",
    "split_pct":        "split_pct",
}


# ── Convenience helpers ───────────────────────────────────────────────────

def map_fields(source: dict[str, Any], field_map: dict[str, str]) -> dict[str, Any]:
    """
    Remap a dict's keys using a field_map.

    Only keys present in field_map are remapped; unmapped keys are dropped.
    Source values of None are preserved so callers can detect missing data.

    Example:
        map_fields({"amount": 1000, "booking_date": "2025-01"}, BOOKING_TO_REVENUE)
        → {"amount": 1000, "period": "2025-01"}
    """
    result: dict[str, Any] = {}
    for src_key, dst_key in field_map.items():
        if src_key in source:
            result[dst_key] = source[src_key]
    return result


def get_source_of_truth(concept: str) -> str:
    """
    Return the authoritative source model for a given business concept.

    >>> get_source_of_truth("booking")
    'Booking'
    >>> get_source_of_truth("unknown")
    'Unknown'
    """
    return SOURCE_OF_TRUTH.get(concept.lower().replace(" ", "_"), "Unknown")
