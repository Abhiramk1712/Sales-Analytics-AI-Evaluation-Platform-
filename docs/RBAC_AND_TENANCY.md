# RBAC and Tenancy

## RBAC Roles

Implemented roles:

- executive
- revops_admin
- finance_admin
- sales_manager
- sales_rep
- data_scientist
- auditor

## Key Permissions

- view_dashboard
- view_forecasts
- view_payouts
- approve_payouts
- manage_plans
- manage_rules
- view_model_monitoring
- run_model_training
- view_data_quality
- manage_tenant_data
- view_audit_logs

Helpers:

- require_permission("...")
- require_role("...", "...")

Demo mode:

- Enabled by DEMO_MODE=true
- Uses DEMO_DEFAULT_ROLE fallback

Production mode scaffold:

- Requires Authorization header
- Supports placeholder bearer format for transition tests
- Rejects missing/invalid role context with 401/403

## Tenant Context Resolution Order

1. X-Company-ID header
2. company_id query param
3. company query param (legacy)
4. authenticated user context company_id
5. active in-process company context
6. DEMO_DEFAULT_COMPANY (demo only)

Tenant helpers:

- get_current_company_id()
- apply_company_scope(query, model, company_id)

Note: apply_company_scope no-ops for models without company_id, which matters for the
one table (of 41) that still lacks it. The primary enforcement path is session-level now
(backend/tenant_guard.py — every ORM select gets `WHERE company_id = ...` automatically);
apply_company_scope/get_current_company_id are the earlier per-call-site helpers, kept
where a route narrows scope explicitly. See CLAUDE.md's tenancy section for the current
model.
