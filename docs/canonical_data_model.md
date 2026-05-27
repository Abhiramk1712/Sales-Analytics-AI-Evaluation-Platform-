# Canonical Data Model

## Entity Hierarchy

```
UserProfile (rep)
  ↳ PlanAssignment (which comp plan)
    ↳ Plan
      ↳ Rule (commission tiers, accelerators, bonuses)

Account (customer company)
  ↳ Deal / Opportunity (sales opportunity)
    ↳ SalesCredit (what the rep is credited for)
      ↳ PayoutRecord (computed payout)
    ↳ Booking (signed contract)
      ↳ Revenue (revenue row, monthly recognition)
        ↳ ChurnEvent (for MRR churn tracking)

Lead → Opportunity → Deal → Booking → Revenue
```

## Source-of-Truth Mapping

| Concept | Canonical Model | Notes |
|---------|----------------|-------|
| Sales representative | UserProfile / Rep | `users` table |
| Sales opportunity | Deal | `deals` table |
| Committed booking | Booking | `bookings` table |
| Revenue entry | Revenue | `revenue` table |
| Credit for commission | SalesCredit | `sales_credits` table |
| Commission output | PayoutRecord | `payouts` table |
| Customer | Account | `accounts` table |

## Revenue Model Fields

The `Revenue` model supports both simple revenue rows and full SaaS MRR/ARR tracking:

| Field | Type | Description |
|-------|------|-------------|
| id | UUID | Primary key |
| rep_id | UUID | Foreign key → users.id |
| period | str | "YYYY-MM" |
| amount | Decimal | Revenue amount |
| account_id | UUID (nullable) | → accounts.id |
| deal_id | UUID (nullable) | → deals.id |
| revenue_type | str (nullable) | new_biz / renewal / expansion / contraction / churn |
| contract_term_months | int (nullable) | |
| recognition_start_date | date (nullable) | ASC 606 start |
| product_sku | str (nullable) | |
| is_recurring | bool (nullable) | SaaS recurring flag |

When `revenue_type` is populated for ≥50% of rows, NRR/GRR calculations use actual typed breakdowns.
Otherwise they fall back to approximations labeled `[FALLBACK]`.

## Field Mapping Conventions

Canonical mappings are defined in `backend/transformations/canonical_mapping.py`.
Use `map_fields(source_dict, field_map)` to remap keys when transforming between representations.

### Booking → Revenue
```python
BOOKING_TO_REVENUE = {
    "booking_amount": "amount",
    "booking_period": "period",
    "booking_account_id": "account_id",
    "booking_deal_id": "deal_id",
    "contract_type": "revenue_type",
    "term_months": "contract_term_months",
    "start_date": "recognition_start_date",
    "sku": "product_sku",
    "is_subscription": "is_recurring",
}
```

### SalesCredit → Payout Input
```python
SALES_CREDIT_TO_PAYOUT_INPUT = {
    "credit_amount": "credited_amount",
    "credit_period": "period",
    "owner_rep_id": "rep_id",
    "source_deal_id": "deal_id",
    "credit_type": "credit_type",
    "split_percentage": "split_pct",
}
```

## Notes

- All numeric facts in agent responses must come from the database, not from this document.
- This document is for developer reference and agent methodology explanations only.
