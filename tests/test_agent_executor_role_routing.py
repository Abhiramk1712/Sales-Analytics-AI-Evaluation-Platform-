"""
tests/test_agent_executor_role_routing.py
===========================================
ToolExecutor.execute_for_intent() had zero role awareness at all --
confirmed live, the same question asked as executive vs. revops_admin
produced byte-identical evidence. These tests cover the routing decision
itself (which path a role takes, and what happens with/without an
identity selection) using a lightweight fake DB -- the underlying tools'
own data correctness is covered separately in
tests/test_agent_self_service_tools.py.
"""
from __future__ import annotations

import pytest

from backend.agent.executor import ToolExecutor
from backend.agent.state import AgentState


class _FakeScalarResult:
    def scalars(self):
        return self

    def first(self):
        return None

    def all(self):
        return []

    def scalar(self):
        return 0


class _FakeDB:
    """No rep/user/plan ever matches -- these tests are about routing
    (which tools get called, with what scope), not data correctness."""

    async def execute(self, *_args, **_kwargs):
        return _FakeScalarResult()


@pytest.mark.asyncio
async def test_sales_rep_without_rep_id_gets_no_evidence_and_a_warning():
    state = AgentState(user_message="how am I doing?")
    state.role = "sales_rep"
    state.rep_id = None

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert result.evidence_results == []
    assert result.tools_called == []
    assert any("No rep selected" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_sales_rep_with_rep_id_calls_self_scoped_tools_regardless_of_intent():
    """Confirmed live: the planner's own catch-all classifies an ordinary
    "how am I doing?" as definition_question. Self/team evidence must be
    fetched regardless of which intent the planner landed on -- otherwise
    a rep's most natural question gets metric definitions and no numbers."""
    state = AgentState(user_message="how am I doing?")
    state.role = "sales_rep"
    state.rep_id = "11111111-1111-1111-1111-111111111111"
    state.intent = "definition_question"

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert "get_my_performance_summary" in result.tools_called
    assert "get_my_payout_summary" in result.tools_called
    # definitional tools are additive, not exclusive, when intent is
    # actually definition_question.
    assert "retrieve_knowledge_context" in result.tools_called


@pytest.mark.asyncio
async def test_sales_rep_non_definition_intent_does_not_fetch_definitional_tools():
    state = AgentState(user_message="how am I doing?")
    state.role = "sales_rep"
    state.rep_id = "11111111-1111-1111-1111-111111111111"
    state.intent = "rep_performance"

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert "get_my_performance_summary" in result.tools_called
    assert "get_my_payout_summary" in result.tools_called
    assert "retrieve_knowledge_context" not in result.tools_called
    assert "list_metrics" not in result.tools_called


@pytest.mark.asyncio
async def test_sales_manager_without_team_id_gets_no_evidence_and_a_warning():
    state = AgentState(user_message="how is my team doing?")
    state.role = "sales_manager"
    state.rep_ids_scope = None

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert result.evidence_results == []
    assert result.tools_called == []
    assert any("No team selected" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_sales_manager_with_team_calls_team_scoped_tools():
    state = AgentState(user_message="how is my team doing?")
    state.role = "sales_manager"
    state.rep_ids_scope = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert "get_team_performance_summary" in result.tools_called
    assert "get_team_payout_summary" in result.tools_called
    # A sales_manager's evidence must never include the single-rep tools --
    # those are the sales_rep path, scoped to one person, not a team.
    assert "get_my_performance_summary" not in result.tools_called
    assert "get_my_payout_summary" not in result.tools_called


@pytest.mark.asyncio
async def test_executive_role_is_unaffected_by_self_service_routing():
    """role defaults to "executive" on AgentState -- this is the
    regression guard that the router split / role field addition didn't
    change behavior for the two roles that already had full access."""
    state = AgentState(user_message="which reps are underperforming?")
    state.intent = "rep_performance"
    assert state.role == "executive"

    result = await ToolExecutor().execute_for_intent(state, db_session=_FakeDB())

    assert "get_top_reps" in result.tools_called
    assert "get_underperforming_reps" in result.tools_called
    assert "get_my_performance_summary" not in result.tools_called
