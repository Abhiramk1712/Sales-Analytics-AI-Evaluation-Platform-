"""
backend/agent/state.py
======================
Agent conversation state and types
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List


@dataclass
class AgentState:
    """Tracks the state of an agent conversation."""
    
    user_message: str
    intent: Optional[str] = None
    tools_called: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)
    evidence_results: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    answer: Optional[str] = None
    # Pipeline workflow fields
    pipeline_status: Optional[str] = None  # planned | running | success | partial_failure
    pipeline_stages: List[Dict[str, Any]] = field(default_factory=list)
    requires_confirmation: bool = False
    confirmation_prompt: Optional[str] = None
    source_files: List[str] = field(default_factory=list)
    ingestion_result: Optional[Dict[str, Any]] = None
    payout_data: Optional[Dict[str, Any]] = None


# Intent classifications
INTENTS = {
    "metric_question": "User asking about a specific metric (quota_attainment, win_rate, etc.)",
    "rep_performance": "User asking about a specific rep's performance",
    "forecast_question": "User asking about revenue forecasts",
    "anomaly_question": "User asking about outliers or unusual patterns",
    "report_request": "User requesting a report",
    "definition_question": "User asking for definition or explanation",
    "general_sales_question": "General sales/business question",
    "ingestion_request": "User requesting data ingestion, file upload, or data loading",
    "payout_request": "User asking about commissions, payouts, or compensation",
    "pipeline_request": "User requesting end-to-end automated data pipeline execution",
    "sales_performance_workflow": "User requesting full 10-step sales performance analysis pipeline",
    "model_training_request": "User requesting ML model retraining or forecasting updates",
    "data_quality_request": "User asking about data quality or validation status",
    "rep_quota_whatif": "User asking rep-specific quota attainment path, bonus what-if, or coaching actions",
    "pipeline_rescue_whatif": "User asking coverage lift / revenue impact from rescuing at-risk deals and priority actions",
    "deal_velocity_trends": "User asking for deal velocity trend analysis over time",
    "plan_performance_question": "User asking about performance of a specific compensation plan",
    "business_diagnostic_question": "User asking broad business performance, health, or risk diagnostics",
    "unknown": "Unclear request; requires clarification or insufficient data response",
}
