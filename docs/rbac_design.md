# RBAC Design — Sales Analytics AI

## Overview

This document describes the RBAC model as implemented. It previously described an
earlier design — a five-role hierarchy (`vp_sales`, `director`, `manager`, `rep`,
`revops`) with row-level org-subtree scoping — that was superseded before it was built;
none of those role names exist in the code. The roles, permissions, and enforcement
below are generated from `backend/auth/permissions.py` and `backend/auth/roles.py`
directly, not transcribed by hand, so this stays accurate as those files change.

For tenant scoping (as opposed to role permissions), see
[RBAC_AND_TENANCY.md](RBAC_AND_TENANCY.md) and CLAUDE.md's tenancy section.

---

## Roles

Seven roles, defined in `backend/auth/roles.py` (`ALL_ROLES`):

| Role | Display name |
|---|---|
| `executive` | Executive |
| `revops_admin` | RevOps Admin |
| `finance_admin` | Finance Admin |
| `sales_manager` | Sales Manager |
| `sales_rep` | Sales Rep |
| `data_scientist` | Data Scientist |
| `auditor` | Auditor |

In demo mode (`DEMO_MODE=true`) a role is asserted via the `X-User-Role` header — that's
the point of the persona switcher. In production mode it comes from a verified JWT's
claims and the header is ignored entirely; see `backend/auth/dependencies.py`'s module
docstring for why accepting the header in production would be a complete authorization
bypass.

---

## Permission Matrix

Generated from `ROLE_PERMISSIONS` in `backend/auth/permissions.py`:

| Permission | Executive | RevOps Admin | Finance Admin | Sales Manager | Sales Rep | Data Scientist | Auditor |
|---|---|---|---|---|---|---|---|
| `admin` |  | ✅ |  |  |  |  |  |
| `approve_payouts` |  | ✅ | ✅ |  |  |  |  |
| `edit_plans` |  | ✅ |  |  |  |  |  |
| `generate_reports` | ✅ | ✅ | ✅ | ✅ |  | ✅ | ✅ |
| `manage_plans` |  | ✅ |  |  |  |  |  |
| `manage_rules` |  | ✅ |  |  |  |  |  |
| `manage_tenant_data` |  | ✅ |  |  |  |  |  |
| `run_agent_workflow` | ✅ | ✅ |  |  |  |  |  |
| `run_ingestion` |  | ✅ |  |  |  |  |  |
| `run_model_training` |  | ✅ |  |  |  | ✅ |  |
| `switch_company` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `view_all_payouts` | ✅ | ✅ | ✅ |  |  |  |  |
| `view_all_reps` | ✅ | ✅ |  |  |  |  |  |
| `view_audit_logs` | ✅ | ✅ | ✅ | ✅ |  | ✅ | ✅ |
| `view_company_metrics` | ✅ | ✅ |  |  |  |  |  |
| `view_dashboard` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `view_data_quality` | ✅ | ✅ |  |  |  | ✅ | ✅ |
| `view_forecasts` | ✅ | ✅ |  | ✅ | ✅ | ✅ |  |
| `view_model_monitoring` | ✅ | ✅ |  |  |  | ✅ | ✅ |
| `view_own_metrics` | ✅ | ✅ |  | ✅ | ✅ |  |  |
| `view_own_payout` | ✅ | ✅ | ✅ | ✅ | ✅ |  |  |
| `view_payouts` | ✅ | ✅ | ✅ | ✅ |  |  | ✅ |
| `view_plans` | ✅ | ✅ |  |  |  |  |  |

`revops_admin` and `executive` are the two broad roles; every other role is scoped to
one functional area (finance to payouts, data_scientist to ML, auditor to read-only
oversight, sales_manager/sales_rep to their own numbers).

---

## Enforcement

Real, not aspirational — every one of the 12 backend routers gates at least one route
with `Depends(require_permission(...))` or `Depends(require_role(...))`
(`backend/auth/dependencies.py`):

- Missing/invalid bearer token in production mode → `401`.
- Valid identity, wrong role or missing permission → `403`.
- `has_permission(role, permission)` is a flat lookup against `ROLE_PERMISSIONS` — no
  caching, no implicit inheritance between roles.

**What this does not do:** row-level scoping. A `sales_manager` with `view_payouts`
can see the same payout rows an `executive` can within their tenant — there is no
query-level filter restricting them to their own reports' rows. Confirmed by grep: no
router filters a payout, deal, or analytics query by the caller's `manager_id` or
org-hierarchy subtree. The permission model answers "can this role see payouts at all,"
not "whose payouts." If per-manager row scoping is wanted, it needs a real filter added
at the query layer — it is not a config flag away.

---

## Position Rank → Role (Auto-Detection)

`Position.rank` (an org-hierarchy field, 1–5, used for org-chart display and leadership
detection — e.g. `data_quality.py` treats `rank <= 3` as leadership) is a different
concept from the RBAC role above, but `RANK_TO_ROLE` in `backend/auth/roles.py` maps one
onto the other for auto-detecting a plausible starting role from org position:

| rank | Position label | Auto-detected role |
|---|---|---|
| 1 | Executive | `executive` |
| 2 | VP | `executive` |
| 3 | Director | `sales_manager` |
| 4 | Manager | `sales_manager` |
| 5 | IC | `sales_rep` |

This is a convenience mapping (e.g. for seeding a sensible default persona), not a
runtime authorization check — the role actually enforced per-request is the one in the
JWT claim or, in demo mode, the `X-User-Role` header.
