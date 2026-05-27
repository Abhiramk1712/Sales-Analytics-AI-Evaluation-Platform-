# Tenant & Lineage Design — Sales Analytics AI

## Overview

This document describes the multi-tenancy model and data lineage architecture for the
Sales Analytics AI platform. The current implementation supports a single active company
at a time (selected via UI); this design targets a future multi-tenant deployment.

---

## Tenancy Model

### Current: Single-tenant per session
The `companies/` directory stores one CRM dataset per company folder. The UI's company
selector (`?company=<name>`) triggers a data reload; the backend serves the single
active loaded company.

### Target: Multi-tenant (company_id partitioned)
Each DB record will carry a `company_id` (UUID or slug) foreign key. All queries will
include a `WHERE company_id = :tenant_id` predicate applied automatically by middleware.

#### Schema additions (future migration)
```sql
-- Add tenant column to all resource tables
ALTER TABLE reps          ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE deals         ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE revenue       ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE activities    ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE positions     ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE plans         ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';
ALTER TABLE plan_cascade_rules ADD COLUMN company_id VARCHAR(64) NOT NULL DEFAULT 'default';

-- Tenant index for all key tables
CREATE INDEX idx_reps_company      ON reps(company_id);
CREATE INDEX idx_deals_company     ON deals(company_id);
CREATE INDEX idx_revenue_company   ON revenue(company_id);
CREATE INDEX idx_plans_company     ON plans(company_id);
```

### Tenant resolution
```python
# FastAPI dependency (future)
async def get_tenant(request: Request) -> str:
    company_id = request.headers.get("X-Company-ID") or request.query_params.get("company")
    if not company_id:
        raise HTTPException(status_code=400, detail="Missing X-Company-ID header")
    return company_id
```

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
The `CreditPayoutResult` model records:
- `deal_id`, `rep_id`, `plan_id`: entities involved
- `payout_amount`, `credit_percentage`: computed values
- `cascade_rule_id`: which cascade rule drove the allocation (nullable)
- `computed_at`: timestamp (immutable once recorded)

Payouts are never updated in-place; corrections create a new row with a `correction_ref`
pointing to the original payout row.

### Deal snapshots
`backend/features/deal_snapshots.py` records a snapshot of each deal's fields at the
time of ML training to prevent data leakage. Snapshots include:
- `snapshot_date`: when the snapshot was captured
- `stage_at_snapshot`: deal stage at that moment
- `features_hash`: hash of feature values for reproducibility

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

1. **No tenant isolation** — all data is shared in one DB schema; company is logically
   separated only by `company_id` values seeded during data generation.
2. **No auth middleware** — all API endpoints are open; RBAC is not enforced at runtime.
3. **In-memory workflow store** — `backend/workflows/store.py` persists to a JSON file
   but does not use the database; not suitable for multi-process deployments.
4. **Lineage fields partially populated** — `source_system` and `source_file` are set
   during CSV ingestion but not always during synthetic data generation.
