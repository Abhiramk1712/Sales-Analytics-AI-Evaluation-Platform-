# Enterprise Readiness

## Scope

This project remains a demo/academic platform, but now includes enterprise-grade foundations:

- repository hygiene and clean packaging checks
- reproducible setup paths (Makefile; Docker Compose exists but isn't the verified path)
- RBAC enforced per-route (`require_permission`/`require_role`, 401/403) and query-scoped
  tenancy enforced at the session level
- payout lifecycle traceability endpoints
- model governance metadata and model cards
- CI gates for hygiene/tests/frontend build

## Readiness Matrix

| Area | Current State | Notes |
|---|---|---|
| Packaging hygiene | Implemented | Scripted checks and packaging exclusions in place |
| Local reproducibility | Implemented | make setup/backend/frontend/test/package/clean |
| AuthN/AuthZ | Implemented | Real JWT signature verification (HS256, PyJWT) in production mode; no OIDC provider, refresh, or revocation flow yet |
| Tenant isolation | Implemented | `company_id` on 40/41 tables, enforced automatically at the SQLAlchemy session level — not per-call-site filtering |
| Payout auditability | Scaffold implemented | In-memory lifecycle store; persistent DB workflow pending |
| ML governance | Partially implemented | Model cards + monitoring summary + metadata available |
| Agent safety | Improved | Sensitive action guardrail + evidence/assumption contract |
| CI readiness | Implemented | GitHub Actions workflow added |

## Next Steps to Reach Production

1. Move from a static JWT secret to a real OIDC provider, with token refresh and
   revocation.
2. Add row-level hierarchical RBAC scoping (a manager restricted to their own subtree) —
   today permissions are role-level, not per-row.
3. Move payout audit lifecycle from in-memory store to durable DB tables.
4. Add approval workflow integration (identity, signatures, immutable audit logs).
5. Add SLOs/alerts for model drift and data quality critical checks.
