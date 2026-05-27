# Data Lifecycle

## End-to-End Flow

1. Ingest source data through ingestion loaders.
2. Run validation checks and generate quality warnings.
3. Compute governed metrics through shared metric calculators/service.
4. Feed analytics, reports, and agent tools from the same metric layer.
5. Feed ML workflows and monitoring with tracked metadata.

## Layers

- Ingestion: `backend/ingestion/`
- Validation: `backend/validation/`
- Metrics governance: `backend/metrics/`
- Statistics/intelligence: `backend/statistics/`
- Reports: `backend/reports/`
- Agent tools: `backend/agent/tools/`
- ML: `backend/ml/`

## Quality Signals

Current quality signals are surfaced as warnings rather than hard failures where possible:
- missing or empty data
- filters not applicable to current grain
- no matching rows for selected criteria

Data quality is also exposed via API endpoints:
- `GET /data-quality/summary`
- `GET /data-quality/checks`

Checks include empty-table, duplicate, orphan, null-FK, negative revenue, invalid date, and missing quota diagnostics.

Warnings are propagated into:
- analytics responses
- report output metadata
- agent response warnings

## Consistency Goal

Dashboard, reports, and AI answers should reflect the same governed metrics and evidence paths, reducing mismatch between surfaces.
