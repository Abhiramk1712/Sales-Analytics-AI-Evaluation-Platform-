"""Canonical list of report types supported by the report generator and API."""

REPORT_TYPES: list[str] = [
    "executive_weekly",
    "manager_monthly",
    "rep_performance",
    "pipeline_health",
    "quota_attainment",
    "arr_bridge",
    "payout_statement",
    "forecast_summary",
    "executive_sales_summary",
    "payout_audit_report",
    "forecast_confidence_report",
    "data_quality_report",
    "model_monitoring_report",
    "revops_risk_report",
]

REPORT_TYPE_LABELS: dict[str, str] = {
    "executive_weekly":   "Executive Weekly",
    "manager_monthly":    "Manager Monthly",
    "rep_performance":    "Rep Performance",
    "pipeline_health":    "Pipeline Health",
    "quota_attainment":   "Quota Attainment",
    "arr_bridge":         "ARR Bridge",
    "payout_statement":   "Payout Statement",
    "forecast_summary":   "Forecast Summary",
    "executive_sales_summary": "Executive Sales Summary",
    "payout_audit_report": "Payout Audit Report",
    "forecast_confidence_report": "Forecast Confidence Report",
    "data_quality_report": "Data Quality Report",
    "model_monitoring_report": "Model Monitoring Report",
    "revops_risk_report": "RevOps Risk Report",
}
