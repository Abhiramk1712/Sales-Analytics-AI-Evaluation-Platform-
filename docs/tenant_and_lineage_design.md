# Tenant & Lineage Design — Sales Analytics AI

## Overview

This document describes the multi-tenancy model and data lineage architecture for the
Sales Analytics AI platform. It previously described the multi-tenant model as a *future*
target while the current implementation was a single resident company swapped on demand.
That migration (tracked internally as ARCH-1) is done — the sections below describe what
actually runs, corrected against the code rather than carried forward from the design
doc. See CLAUDE.md's tenancy section for the short version and the incident it replaced.

---

## Tenancy Model

### Query-scoped, enforced at the session

`company_id` lives on 40 of the 41 domain tables. `backend/tenancy.py` carries the
current tenant in a `ContextVar`, bound per request by `tenant_binding_middleware` in
`backend/main.py`. `backend/tenant_guard.py` listens for SQLAlchemy's `do_orm_execute`
and `before_flush` events and, from those two hooks alone:

- adds `WHERE company_id = :tenant` to every ORM select, automatically — no router or
  service function adds this filter itself;
- stamps `company_id` on every insert;
- raises (`TenantStampError`) rather than silently writing a row no tenant can see, if a
  write happens with no tenant bound.

This means two companies can be resident and queried **concurrently** — nothing is
loaded, swapped, or dropped to switch which company a request sees.
`tests/test_tenancy_enforcement.py` holds two companies resident in the same test run to
prove it, and this was also verified live: concurrent requests against two different
companies returned each company's own numbers, not a shared or overwritten dataset.

For deliberate cross-tenant work (an admin report spanning companies, a migration
script) there's `tenant_guard.unscoped()` — an explicit opt-out, so a bypass is always a
visible decision at the call site rather than an accidental missing filter.

#### What this replaced

Tenancy used to be a whole-database swap: loading one company dropped every table via
`Base.metadata.drop_all` and rebuilt the schema for just that company, so the server held
exactly one tenant's data at a time and naming a different company in a request rebuilt
the database mid-request to serve it — unauthenticated, on a plain `GET`. `drop_all`
appears nowhere in the codebase now.

#### Schema history

The three migrations that got here, in order:

```text
20260901_0001_add_company_id_tenant_scope.py — add company_id (nullable) to every table
20260901_0002_backfill_company_id.py         — backfill company_id on pre-existing rows
20260901_0003_per_company_natural_keys.py    — make natural-key uniqueness per-company
                                                (two tenants both number positions
                                                POS-001 up — those constraints were
                                                global and collided the moment a second
                                                tenant could be resident)
```

### Tenant resolution (as implemented)

`backend/tenancy.py` and `backend/auth/tenant.py` resolve the tenant per request; see
[RBAC_AND_TENANCY.md](RBAC_AND_TENANCY.md) for the exact header/claim precedence order.

---

## Data Lineage Model

### Source tracking fields

All ingested records carry source lineage metadata. These fields exist on `Revenue`, `Deal`,
and `Activity` models (see `backend/models.py`):

| Field | Type | Description |
|---|---|---|
| `source_system` | VARCHAR(64) | Origin CRM or system name (e.g., `salesforce`, `hubspot`, `csv_upload`) |
| `source_file` | VARCHAR(256) | Original filename if ingested via file upload |
| `ingested_at` | TIMESTAMP | When the record entered the platform |
| `source_record_id` | VARCHAR(128) | Native ID from source system (for deduplication) |

### Canonical mapping
`backend/transformations/canonical_mapping.py` declares `SOURCE_OF_TRUTH` which maps
each field name to its authoritative source system. This prevents metric drift when
multiple systems report the same field with different values.

### Ingestion pipeline lineage
Every ingestion run is recorded in `IngestionRun` (see `backend/ingestion/ingestion_run.py`):
- `run_id`: UUID for the ingestion run
- `source_file`: path or name of the input file
- `company_id`: target tenant
- `records_inserted`, `records_updated`, `records_skipped`: row-level counts
- `warnings`: JSON list of data quality warnings encountered during load
- `completed_at`: timestamp of successful completion

### Workflow lineage
The workflow store (`backend/workflows/store.py`) attaches lineage metadata to each
agentic pipeline run:
```json
{
  "workflow_id": "wf_abc123",
  "pipeline": "sales_performance",
  "company_id": "acme-corp",
  "triggered_at": "2025-01-15T10:30:00Z",
  "steps_completed": ["kpis", "forecast", "deal_risk", "clustering"],
  "warnings": [],
  "status": "completed"
}
```

---

## Audit & Immutability

### Payout audit trail
See [PAYOUT_AUDIT_TRAIL.md](PAYOUT_AUDIT_TRAIL.md) for the trace field list and lifecycle
states — kept in one place rather than duplicated here, since a duplicate is exactly what
went stale before this pass (this section previously named fields — `cascade_rule_id`,
`payout_amount` on a `CreditPayoutResult` — that don't match either the in-memory trace
record or the persisted `PayoutRecord` table). The short version: payouts aren't updated
in-place; a correction creates a new row with `correction_ref` pointing at the original.

### Deal snapshots
`backend/features/deal_snapshots.py` records a snapshot of each deal's fields at the
time of ML training to prevent data leakage. Snapshots include:
- `snapshot_date`: when the snapshot was captured
- `stage`: deal stage at that moment
- `deal_id`, `rep_id`: identifiers
- the feature columns themselves (`amount`, `days_in_pipeline`, `activity_count`, …) and,
  for closed deals, `final_outcome`/`is_terminal` as training labels

---

## Data Retention Policy (Recommended)

| Data type | Retention | Reason |
|---|---|---|
| Raw ingestion files | 90 days | Debugging and re-ingestion |
| Revenue records | Indefinite | Required for ARR/NRR accuracy |
| Payout records | 7 years | Finance compliance |
| ML model artifacts | Last 3 versions | Model rollback capability |
| Agent chat logs | 30 days | PII minimization |
| Workflow run logs | 90 days | Operational debugging |

---

## Known Limitations (Current Implementation)

1. **No row-level hierarchical scoping** — tenant isolation (which company) is enforced;
   within a tenant, a role either can or can't see a resource (`view_payouts`), but
   there's no query-level filter restricting a manager to their own reports' rows. See
   [rbac_design.md](rbac_design.md)'s Enforcement section.
2. **Single Postgres schema per deployment** — every tenant's rows live in the same
   tables, distinguished by `company_id`, not by separate schemas or databases. That's
   the intended model here (RLS-by-application-layer, not RLS-by-Postgres), not a gap.
3. **In-memory workflow store** — `backend/workflows/store.py` persists to a JSON file
   but does not use the database; not suitable for multi-process deployments.
4. **Lineage fields partially populated** — `source_system` and `source_file` are set
   during CSV ingestion but not always during synthetic data generation.
