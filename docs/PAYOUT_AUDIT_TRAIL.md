# Payout Audit Trail

## Objective

Provide finance-grade payout explainability and lifecycle controls without breaking the current demo workflows.

## Trace Fields

Each payout trace record stores:

- payout_id, company_id, rep_id/user_id, period
- plan_id, rule_id, sales_credit_id
- credited_amount, quota, attainment_pct
- base_commission, accelerator_amount, spiff_amount, clawback_amount, final_payout
- calculation_trace_json, source_records_json
- computed_at, computed_by
- approval_status, approved_by, approved_at
- locked_at, version, is_locked, correction_ref
- lifecycle_state

## Lifecycle States

- draft
- reviewed
- approved
- locked
- paid
- adjusted

## API Endpoints

- GET /payouts
- GET /payouts/{id}
- GET /payouts/{id}/trace
- POST /payouts/{id}/approve
- POST /payouts/{id}/lock
- POST /payouts/{id}/adjust

## Blocking Rules

- Approval is blocked when critical data quality issues exist.
- Locked payouts are immutable; adjustments must use the adjust endpoint.

## Current Limitation

The payout lifecycle store is in-memory scaffold mode for local/demo simplicity.
A production deployment should persist records in an immutable database table and include approval signatures.
