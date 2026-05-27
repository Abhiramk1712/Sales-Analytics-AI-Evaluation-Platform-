"""
tests/test_agent_new_intents.py
================================
Tests for the new agent intents: payout_request, pipeline_request,
ingestion_request, model_training_request, data_quality_request.
"""
import pytest
from backend.agent.planner import IntentPlanner
from backend.agent.state import AgentState, INTENTS


class TestNewIntentClassification:
    def setup_method(self):
        self.planner = IntentPlanner()

    def _classify(self, msg: str) -> str:
        return self.planner.classify(msg)

    # ── Payout ────────────────────────────────────────────────────────────

    def test_payout_keywords(self):
        assert self._classify("What is the payout for this quarter?") == "payout_request"
        assert self._classify("Show me commission calculations") == "payout_request"
        assert self._classify("Calculate compensation for all reps") == "payout_request"

    def test_payout_incentive(self):
        assert self._classify("What are the sales incentives?") == "payout_request"

    # ── Ingestion ────────────────────────────────────────────────────────

    def test_ingest_keywords(self):
        assert self._classify("Ingest new sales data from /data/company") == "ingestion_request"
        assert self._classify("Load CSV files into the database") == "ingestion_request"
        assert self._classify("Upload our data from this directory") == "ingestion_request"
        assert self._classify("Import data from the source folder") == "ingestion_request"

    # ── Pipeline ─────────────────────────────────────────────────────────

    def test_pipeline_keywords(self):
        assert self._classify("Run the end-to-end pipeline") == "sales_performance_workflow"
        assert self._classify("Automate pipeline processing") == "sales_performance_workflow"
        assert self._classify("Process data through the full pipeline") == "sales_performance_workflow"

    # ── Model training ────────────────────────────────────────────────────

    def test_model_training_keywords(self):
        assert self._classify("Retrain the forecast model") == "model_training_request"
        assert self._classify("Update the ML model with new data") == "model_training_request"
        assert self._classify("Run model training now") == "model_training_request"

    # ── Data quality ─────────────────────────────────────────────────────

    def test_data_quality_keywords(self):
        assert self._classify("Check data quality status") == "data_quality_request"
        assert self._classify("Run data validation on current dataset") == "data_quality_request"
        assert self._classify("Show me the quality check results") == "data_quality_request"

    # ── New intents registered in INTENTS dict ────────────────────────────

    def test_new_intents_in_registry(self):
        for intent in [
            "payout_request",
            "pipeline_request",
            "sales_performance_workflow",
            "ingestion_request",
            "model_training_request",
            "data_quality_request",
            "business_diagnostic_question",
        ]:
            assert intent in INTENTS

    # ── Existing intents not disrupted ───────────────────────────────────

    def test_existing_forecast_intent(self):
        assert self._classify("What is the revenue forecast?") == "forecast_question"

    def test_existing_report_intent(self):
        assert self._classify("Generate an executive summary") == "report_request"

    def test_business_summary_intent(self):
        assert self._classify("Can you give me the business summary of the company?") == "report_request"
        assert self._classify("Give me a company summary") == "report_request"
        assert self._classify("Need a sales overview for leadership") == "report_request"

    def test_existing_metric_intent(self):
        assert self._classify("What is our current quota attainment?") == "metric_question"

    def test_business_diagnostic_intent(self):
        assert self._classify("How is the business doing overall and where are we at risk?") == "business_diagnostic_question"


class TestAgentStateNewFields:
    def test_pipeline_fields_defaults(self):
        state = AgentState(user_message="test")
        assert state.pipeline_status is None
        assert state.pipeline_stages == []
        assert state.requires_confirmation is False
        assert state.confirmation_prompt is None
        assert state.source_files == []
        assert state.ingestion_result is None
        assert state.payout_data is None

    def test_pipeline_fields_settable(self):
        state = AgentState(user_message="run pipeline")
        state.pipeline_status = "planned"
        state.requires_confirmation = True
        state.confirmation_prompt = "Please confirm source directory."
        state.pipeline_stages = [{"step": "ingest", "status": "pending"}]
        assert state.pipeline_status == "planned"
        assert state.requires_confirmation
        assert state.confirmation_prompt == "Please confirm source directory."
        assert len(state.pipeline_stages) == 1
