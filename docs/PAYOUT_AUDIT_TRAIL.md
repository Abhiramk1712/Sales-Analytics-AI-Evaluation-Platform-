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

- GET /payout-audit
- GET /payout-audit/{id}
- GET /payout-audit/{id}/trace
- POST /payout-audit/{id}/review
- POST /payout-audit/{id}/approve
- POST /payout-audit/{id}/lock
- POST /payout-audit/{id}/pay
- POST /payout-audit/{id}/adjust

`/review` and `/pay` reuse `mark_reviewed`/`mark_paid` in
`audit_trail_service.py`, which existed (and, for `mark_reviewed`, already
did) before either had a route calling them — a payout could reach `locked`
through the API and then have no path to `paid` at all. `/adjust` has no
lifecycle guard (unlike the others, which block once `is_locked`) —
corrections are deliberately reachable from any state, including `paid`.

Not to be confused with `/payout` (singular) — a different router
(`backend/routers/payout.py`) covering calculation, statements, and config.

## Blocking Rules

- Approval is blocked when critical data quality issues exist.
- Locked payouts are immutable; adjustments must use the adjust endpoint.

## Current Limitation

The payout lifecycle store is in-memory scaffold mode for local/demo simplicity.
A production deployment should persist records in an immutable database table and include approval signatures.
