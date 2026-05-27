# RBAC Design — Sales Analytics AI

## Overview

This document describes the intended Role-Based Access Control (RBAC) model for the
Sales Analytics AI platform. The current implementation uses a single-tenant model with
in-memory role concepts; this design targets a future multi-tenant production deployment.

---

## Roles

| Role | Label | rank (position) | Description |
|---|---|---|---|
| `executive` | Executive / CRO | 1 | Full read access to all companies, all reports, all agent queries, grading scorecard |
| `vp_sales` | VP of Sales | 2 | Read access to own team hierarchy; can trigger workflow pipeline |
| `director` | Director | 3 | Read access to managers and ICs under them; can view forecasts and payouts |
| `manager` | Sales Manager | 4 | Read access to their direct reports only; can view payout statements for their team |
| `rep` | Sales Rep / IC | 5 | Read-only access to own deal pipeline, own payout statement, own performance report |
| `finance_admin` | Finance / Admin | — | Full access to payout engine, payout statements, plan assignments, clawbacks |
| `revops` | RevOps | — | Full access to data quality, grading, ingestion, metrics registry |

---

## Access Matrix

| Resource | executive | vp_sales | director | manager | rep | finance_admin | revops |
|---|---|---|---|---|---|---|---|
| Dashboard metrics | ✅ all | ✅ team | ✅ team | ✅ team | ✅ own | ✅ | ✅ |
| Forecasting | ✅ | ✅ | ✅ | ✅ view | ❌ | ✅ view | ✅ |
| AI Agent chat | ✅ | ✅ | ✅ | ✅ | ✅ own data | ✅ limited | ✅ |
| Reports: executive_weekly | ✅ | ✅ | ✅ view | ❌ | ❌ | ✅ view | ✅ |
| Reports: payout_statement | ✅ | ✅ | ✅ own team | ✅ own team | ✅ own only | ✅ | ✅ |
| Reports: forecast_summary | ✅ | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ |
| Payout engine triggers | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ | ✅ |
| Plan assignments | ✅ | ✅ create | ✅ view | ✅ view | ✅ view own | ✅ | ✅ |
| Cascade rules | ✅ | ✅ create | ✅ view | ❌ | ❌ | ✅ | ✅ |
| Enterprise grader | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Data quality | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Ingestion | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Org hierarchy | ✅ | ✅ | ✅ | ✅ own subtree | ❌ | ❌ | ✅ |

---

## Data Scoping Rules

### Hierarchical data scoping
- Managers see data only for users where `Manager.manager_id` traces back to their own `user_id`.
- Directors see their full subtree (managers + their reps).
- Executives see all data for their company tenant.

### Plan cascade scope
- `cascade_scope = "all_reports"`: applies to all users in the reporter's hierarchy.
- `cascade_scope = "direct_reports"`: applies only to the user's immediate reports.
- `cascade_scope = "global"`: applies to everyone in the company (executive-level only).

### Payout access
- `finance_admin` and `executive` can trigger `compute_credit_payouts()` for any rep.
- A `manager` can view payout breakdowns for their direct reports only.
- A `rep` can view their own `payout_statement` report but cannot modify plan assignments.

---

## Implementation Notes

### Current state
- The backend does not yet enforce RBAC middleware; all endpoints are open within a
  single-tenant deployment.
- `Position.rank` (1–5) is available on all user records and can be used to derive
  role membership without a separate roles table.

### Recommended implementation path
1. Add `role` column to `UserProfile` (or derive from `position.rank`).
2. Implement a `RBACMiddleware` or FastAPI dependency (`Depends(require_role(...))`).
3. Add `company_id` (tenant scope) to all resource queries.
4. Gate `/grading/enterprise-readiness` to `revops` role only.
5. Gate payout engine triggers to `finance_admin` or `executive`.
6. Scope `/analytics/*` queries to the requesting user's hierarchy subtree.

---

## Position Rank Reference

| rank | label | Role equivalent |
|---|---|---|
| 1 | Executive | executive / CRO |
| 2 | VP | vp_sales |
| 3 | Director | director |
| 4 | Manager | manager |
| 5 | IC | rep |
| 99 | Unknown | rep (safe default) |
