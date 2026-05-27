import pytest

import backend.agent.executor as executor_module
from backend.agent.executor import ToolExecutor
from backend.agent.executor import (
    _extract_max_attainment_pct_from_message,
    _extract_max_pipeline_coverage_from_message,
    _extract_min_attainment_pct_from_message,
    _extract_min_pipeline_coverage_from_message,
    _extract_top_n_from_message,
)
from backend.agent.planner import IntentPlanner
from backend.agent.state import AgentState
from backend.agent.verifier import EvidenceVerifier


class DummyResult:
    def __init__(self, scalar_value=0, rows=None):
        self._scalar = scalar_value
        self._rows = rows or []

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    def scalars(self):
        return self

    def first(self):
        return None


class DummyDB:
    async def execute(self, _query):
        return DummyResult(0, [])


class TestIntentPlanner:
    def test_classifies_metric_question(self):
        planner = IntentPlanner()
        assert planner.classify("what is our quota attainment this month?") == "metric_question"

    def test_classifies_definition_question(self):
        planner = IntentPlanner()
        assert planner.classify("what is quota attainment?") == "definition_question"

    def test_classifies_rep_quota_whatif(self):
        planner = IntentPlanner()
        msg = "If Alex Johnson rep hits quota target, what bonus would they get and what steps should they take?"
        assert planner.classify(msg) == "rep_quota_whatif"

    def test_classifies_rep_quota_driver_whatif(self):
        planner = IntentPlanner()
        msg = (
            "What if James Tucker improves his close rate by 10% and shortens average sales cycle by 15 days, "
            "in FY2026 Plan 1 how does that change quota attainment, bonus payout, and deal-slippage risk?"
        )
        assert planner.classify(msg) == "rep_quota_whatif"

    def test_classifies_rep_quota_pipeline_whatif(self):
        planner = IntentPlanner()
        msg = "If James Tucker pipeline increases by 20%, how would payout and attainment change?"
        assert planner.classify(msg) == "rep_quota_whatif"

    def test_classifies_rep_quota_scenario_keyword_whatif(self):
        planner = IntentPlanner()
        msg = "Run a scenario sensitivity on James Tucker deal size up 15% and show slippage impact"
        assert planner.classify(msg) == "rep_quota_whatif"

    def test_classifies_deal_velocity_trends(self):
        planner = IntentPlanner()
        assert planner.classify("Show me deal velocity trends") == "deal_velocity_trends"

    def test_classifies_plan_performance_question(self):
        planner = IntentPlanner()
        assert planner.classify("performance of FY2026 PLAN 1") == "plan_performance_question"

    def test_classifies_plan_rules_listing_question(self):
        planner = IntentPlanner()
        assert planner.classify("list down the plans and rules") == "plan_performance_question"

    def test_classifies_team_coverage_without_pipeline_phrase(self):
        planner = IntentPlanner()
        msg = "Show top 5 teams by coverage with attainment below 75%"
        assert planner.classify(msg) == "pipeline_coverage_check"

    def test_classifies_pipeline_rescue_whatif(self):
        planner = IntentPlanner()
        msg = (
            "What if we raise weighted pipeline coverage from 0.68x to 1.0x "
            "by rescuing the top 15 at-risk deals?"
        )
        assert planner.classify(msg) == "pipeline_rescue_whatif"


class TestExecutorAndVerifier:
    def test_extract_thresholds_from_message(self):
        msg = "Which teams have pipeline coverage above 6x and quota attainment below 70%?"
        assert _extract_min_pipeline_coverage_from_message(msg) == 6.0
        assert _extract_max_attainment_pct_from_message(msg) == 70.0

    def test_extract_coverage_range_from_message(self):
        msg = "Find teams with coverage between 5x and 9x"
        assert _extract_min_pipeline_coverage_from_message(msg) == 5.0
        assert _extract_max_pipeline_coverage_from_message(msg) == 9.0

    def test_extract_attainment_or_bounds_from_message(self):
        msg = "Which teams have attainment under 65% or over 120%?"
        assert _extract_max_attainment_pct_from_message(msg) == 65.0
        assert _extract_min_attainment_pct_from_message(msg) == 120.0

    def test_extract_top_n_from_message(self):
        assert _extract_top_n_from_message("show top 5 teams by coverage") == 5

    def test_extract_coverage_does_not_capture_attainment_percent(self):
        msg = "Show top 5 teams by coverage with attainment below 75%"
        assert _extract_min_pipeline_coverage_from_message(msg) is None
        assert _extract_max_pipeline_coverage_from_message(msg) is None

    @pytest.mark.asyncio
    async def test_executor_calls_tools(self):
        executor = ToolExecutor()
        state = AgentState(user_message="which reps are behind quota?", intent="rep_performance")
        updated = await executor.execute_for_intent(state, db_session=DummyDB())
        assert updated.tools_called
        assert updated.evidence_results

    @pytest.mark.asyncio
    async def test_executor_plan_listing_calls_catalog_tool_only(self, monkeypatch):
        calls = {"catalog": 0, "performance": 0}

        async def fake_catalog(db, max_plans=25, max_rules=80):
            calls["catalog"] += 1
            return {
                "tool_name": "get_plans_rules_catalog",
                "status": "success",
                "data": {
                    "plan_count": 2,
                    "rule_count": 8,
                    "plans": [],
                    "rules": [],
                },
                "warnings": [],
                "sources": ["plans", "rules"],
            }

        async def fake_perf(db, message):
            calls["performance"] += 1
            return {
                "tool_name": "get_plan_performance_summary",
                "status": "warning",
                "data": {"matched_plan": None, "performance": None, "candidate_plans": ["FY2026 Plan 1"]},
                "warnings": ["Could not match the request to a known plan name."],
                "sources": ["plans"],
            }

        monkeypatch.setattr(executor_module, "get_plans_rules_catalog", fake_catalog)
        monkeypatch.setattr(executor_module, "get_plan_performance_summary", fake_perf)

        executor = ToolExecutor()
        state = AgentState(user_message="list down the plans and rules", intent="plan_performance_question")
        updated = await executor.execute_for_intent(state, db_session=DummyDB())

        assert "get_plans_rules_catalog" in updated.tools_called
        assert calls["catalog"] == 1
        assert calls["performance"] == 0

    @pytest.mark.asyncio
    async def test_executor_pipeline_coverage_team_question_calls_team_tool(self):
        executor = ToolExecutor()
        state = AgentState(
            user_message="Which teams have high pipeline coverage but low quota attainment?",
            intent="pipeline_coverage_check",
        )
        updated = await executor.execute_for_intent(state, db_session=DummyDB())
        assert "get_team_pipeline_coverage_attainment" in updated.tools_called

    @pytest.mark.asyncio
    async def test_executor_pipeline_coverage_team_question_passes_custom_thresholds(self, monkeypatch):
        captured: dict[str, float | str] = {}

        async def fake_team_tool(
            db,
            min_pipeline_coverage=4.0,
            max_attainment_pct=80.0,
            max_pipeline_coverage=None,
            min_attainment_pct=None,
            attainment_logic="and",
            sort_by="match_priority",
            limit=10,
        ):
            captured["min_pipeline_coverage"] = float(min_pipeline_coverage) if min_pipeline_coverage is not None else -1.0
            captured["max_attainment_pct"] = float(max_attainment_pct) if max_attainment_pct is not None else -1.0
            captured["max_pipeline_coverage"] = float(max_pipeline_coverage) if max_pipeline_coverage is not None else -1.0
            captured["min_attainment_pct"] = float(min_attainment_pct) if min_attainment_pct is not None else -1.0
            captured["attainment_logic"] = str(attainment_logic)
            captured["sort_by"] = str(sort_by)
            captured["limit"] = float(limit)
            return {
                "tool_name": "get_team_pipeline_coverage_attainment",
                "status": "success",
                "data": {
                    "criteria": {
                        "min_pipeline_coverage": min_pipeline_coverage,
                        "max_pipeline_coverage": max_pipeline_coverage,
                        "max_attainment_pct": max_attainment_pct,
                        "min_attainment_pct": min_attainment_pct,
                        "attainment_logic": attainment_logic,
                        "sort_by": sort_by,
                        "limit": limit,
                    },
                    "matches": [],
                },
                "warnings": [],
                "sources": ["teams"],
            }

        monkeypatch.setattr(executor_module, "get_team_pipeline_coverage_attainment", fake_team_tool)

        executor = ToolExecutor()
        state = AgentState(
            user_message="Which teams have pipeline coverage above 6x and quota attainment below 70%?",
            intent="pipeline_coverage_check",
        )
        updated = await executor.execute_for_intent(state, db_session=DummyDB())

        assert "get_team_pipeline_coverage_attainment" in updated.tools_called
        assert captured["min_pipeline_coverage"] == 6.0
        assert captured["max_attainment_pct"] == 70.0

    @pytest.mark.asyncio
    async def test_executor_pipeline_coverage_top_n_and_or_attainment(self, monkeypatch):
        captured: dict[str, float | str] = {}

        async def fake_team_tool(
            db,
            min_pipeline_coverage=4.0,
            max_attainment_pct=80.0,
            max_pipeline_coverage=None,
            min_attainment_pct=None,
            attainment_logic="and",
            sort_by="match_priority",
            limit=10,
        ):
            captured["min_pipeline_coverage"] = float(min_pipeline_coverage) if min_pipeline_coverage is not None else -1.0
            captured["max_pipeline_coverage"] = float(max_pipeline_coverage) if max_pipeline_coverage is not None else -1.0
            captured["max_attainment_pct"] = float(max_attainment_pct) if max_attainment_pct is not None else -1.0
            captured["min_attainment_pct"] = float(min_attainment_pct) if min_attainment_pct is not None else -1.0
            captured["attainment_logic"] = str(attainment_logic)
            captured["sort_by"] = str(sort_by)
            captured["limit"] = float(limit)
            return {
                "tool_name": "get_team_pipeline_coverage_attainment",
                "status": "success",
                "data": {"criteria": {}, "matches": []},
                "warnings": [],
                "sources": ["teams"],
            }

        monkeypatch.setattr(executor_module, "get_team_pipeline_coverage_attainment", fake_team_tool)

        executor = ToolExecutor()
        state = AgentState(
            user_message="Show top 5 teams with coverage between 5x and 9x and attainment under 65% or over 120%",
            intent="pipeline_coverage_check",
        )
        updated = await executor.execute_for_intent(state, db_session=DummyDB())

        assert "get_team_pipeline_coverage_attainment" in updated.tools_called
        assert captured["min_pipeline_coverage"] == 5.0
        assert captured["max_pipeline_coverage"] == 9.0
        assert captured["max_attainment_pct"] == 65.0
        assert captured["min_attainment_pct"] == 120.0
        assert captured["attainment_logic"] == "or"
        assert captured["sort_by"] == "coverage_desc"
        assert captured["limit"] == 5.0

    @pytest.mark.asyncio
    async def test_executor_pipeline_rescue_whatif_calls_rescue_tool(self, monkeypatch):
        captured: dict[str, str] = {}

        async def fake_rescue_tool(db, message):
            captured["message"] = str(message)
            return {
                "tool_name": "get_pipeline_rescue_what_if",
                "status": "success",
                "data": {
                    "scenario": {
                        "current_weighted_coverage": 0.68,
                        "target_weighted_coverage": 1.0,
                        "weighted_coverage_after_rescue": 0.78,
                    },
                    "incremental_impact": {
                        "expected_incremental_closed_revenue": 975000,
                    },
                    "priority_deals": [],
                    "priority_reps": [],
                },
                "warnings": [],
                "sources": ["deals"],
            }

        monkeypatch.setattr(executor_module, "get_pipeline_rescue_what_if", fake_rescue_tool)

        executor = ToolExecutor()
        state = AgentState(
            user_message=(
                "What if we raise weighted pipeline coverage from 0.68x to 1.0x "
                "by rescuing the top 15 at-risk deals?"
            ),
            intent="pipeline_rescue_whatif",
        )
        updated = await executor.execute_for_intent(state, db_session=DummyDB())

        assert "get_pipeline_rescue_what_if" in updated.tools_called
        assert "weighted pipeline coverage" in captured["message"].lower()

    def test_verifier_catches_missing_evidence(self):
        verifier = EvidenceVerifier()
        state = AgentState(user_message="hello", intent="unknown")
        ok, warnings = verifier.verify_state(state)
        assert ok is False
        assert warnings
