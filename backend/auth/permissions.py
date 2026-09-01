"""Role-permission matrix for enterprise RBAC foundation."""
from __future__ import annotations

# Required permission examples
PERM_VIEW_DASHBOARD = "view_dashboard"
PERM_VIEW_FORECASTS = "view_forecasts"
PERM_VIEW_PAYOUTS = "view_payouts"
PERM_APPROVE_PAYOUTS = "approve_payouts"
PERM_MANAGE_PLANS = "manage_plans"
PERM_MANAGE_RULES = "manage_rules"
PERM_VIEW_MODEL_MONITORING = "view_model_monitoring"
PERM_RUN_MODEL_TRAINING = "run_model_training"
PERM_VIEW_DATA_QUALITY = "view_data_quality"
PERM_MANAGE_TENANT_DATA = "manage_tenant_data"
PERM_VIEW_AUDIT_LOGS = "view_audit_logs"

# Compatibility permissions already used in the codebase.
PERM_VIEW_COMPANY_METRICS = "view_company_metrics"
PERM_VIEW_ALL_REPS = "view_all_reps"
PERM_VIEW_ALL_PAYOUTS = "view_all_payouts"
PERM_VIEW_PLANS = "view_plans"
PERM_EDIT_PLANS = "edit_plans"
PERM_RUN_INGESTION = "run_ingestion"
PERM_GENERATE_REPORTS = "generate_reports"
PERM_RUN_AGENT_WORKFLOW = "run_agent_workflow"
PERM_ADMIN = "admin"
PERM_VIEW_OWN_PAYOUT = "view_own_payout"
PERM_VIEW_OWN_METRICS = "view_own_metrics"

# Choosing which company you are looking at. Deliberately *not* run_ingestion:
# under a query-scoped tenancy model this is a read-scope change, not an
# operator action. It only rebuilds the database today because tenancy is
# implemented as a whole-database swap — that is the bug, not a reason to make
# viewing privileged.
PERM_SWITCH_COMPANY = "switch_company"

ALL_PERMISSIONS: set[str] = {
    PERM_VIEW_DASHBOARD,
    PERM_VIEW_FORECASTS,
    PERM_VIEW_PAYOUTS,
    PERM_APPROVE_PAYOUTS,
    PERM_MANAGE_PLANS,
    PERM_MANAGE_RULES,
    PERM_VIEW_MODEL_MONITORING,
    PERM_RUN_MODEL_TRAINING,
    PERM_VIEW_DATA_QUALITY,
    PERM_MANAGE_TENANT_DATA,
    PERM_VIEW_AUDIT_LOGS,
    PERM_VIEW_COMPANY_METRICS,
    PERM_VIEW_ALL_REPS,
    PERM_VIEW_ALL_PAYOUTS,
    PERM_VIEW_PLANS,
    PERM_EDIT_PLANS,
    PERM_RUN_INGESTION,
    PERM_GENERATE_REPORTS,
    PERM_RUN_AGENT_WORKFLOW,
    PERM_ADMIN,
    PERM_VIEW_OWN_PAYOUT,
    PERM_VIEW_OWN_METRICS,
    PERM_SWITCH_COMPANY,
}

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "executive": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_FORECASTS,
        PERM_VIEW_PAYOUTS,
        PERM_VIEW_MODEL_MONITORING,
        PERM_VIEW_DATA_QUALITY,
        PERM_VIEW_AUDIT_LOGS,
        PERM_GENERATE_REPORTS,
        PERM_RUN_AGENT_WORKFLOW,
        PERM_VIEW_COMPANY_METRICS,
        PERM_VIEW_ALL_REPS,
        PERM_VIEW_ALL_PAYOUTS,
        PERM_VIEW_PLANS,
        PERM_VIEW_OWN_PAYOUT,
        PERM_VIEW_OWN_METRICS,
    },
    "revops_admin": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_FORECASTS,
        PERM_VIEW_PAYOUTS,
        PERM_APPROVE_PAYOUTS,
        PERM_MANAGE_PLANS,
        PERM_MANAGE_RULES,
        PERM_VIEW_MODEL_MONITORING,
        PERM_RUN_MODEL_TRAINING,
        PERM_VIEW_DATA_QUALITY,
        PERM_MANAGE_TENANT_DATA,
        PERM_VIEW_AUDIT_LOGS,
        PERM_RUN_INGESTION,
        PERM_GENERATE_REPORTS,
        PERM_RUN_AGENT_WORKFLOW,
        PERM_VIEW_COMPANY_METRICS,
        PERM_VIEW_ALL_REPS,
        PERM_VIEW_ALL_PAYOUTS,
        PERM_VIEW_PLANS,
        PERM_EDIT_PLANS,
        PERM_VIEW_OWN_PAYOUT,
        PERM_VIEW_OWN_METRICS,
        PERM_ADMIN,
    },
    "finance_admin": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_PAYOUTS,
        PERM_APPROVE_PAYOUTS,
        PERM_VIEW_AUDIT_LOGS,
        PERM_GENERATE_REPORTS,
        PERM_VIEW_ALL_PAYOUTS,
        PERM_VIEW_OWN_PAYOUT,
    },
    "sales_manager": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_FORECASTS,
        PERM_VIEW_PAYOUTS,
        PERM_VIEW_AUDIT_LOGS,
        PERM_GENERATE_REPORTS,
        PERM_VIEW_OWN_PAYOUT,
        PERM_VIEW_OWN_METRICS,
    },
    "sales_rep": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_FORECASTS,
        PERM_VIEW_OWN_PAYOUT,
        PERM_VIEW_OWN_METRICS,
    },
    "data_scientist": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_FORECASTS,
        PERM_VIEW_MODEL_MONITORING,
        PERM_RUN_MODEL_TRAINING,
        PERM_VIEW_DATA_QUALITY,
        PERM_VIEW_AUDIT_LOGS,
        PERM_GENERATE_REPORTS,
    },
    "auditor": {
        PERM_VIEW_DASHBOARD,
        PERM_SWITCH_COMPANY,
        PERM_VIEW_PAYOUTS,
        PERM_VIEW_MODEL_MONITORING,
        PERM_VIEW_DATA_QUALITY,
        PERM_VIEW_AUDIT_LOGS,
        PERM_GENERATE_REPORTS,
    },
}
