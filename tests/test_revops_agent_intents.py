"""Tests for 5 new RevOps intents in agent planner (backend/agent/planner.py)."""
from __future__ import annotations

import pytest

from backend.agent.planner import IntentPlanner


@pytest.fixture
def planner():
    return IntentPlanner()


@pytest.mark.parametrize("message,expected_intent", [
    ("Which reps are behind on quota and at quota risk?", "quota_risk"),
    ("Show me the at-risk reps", "quota_risk"),
    ("What is our pipeline coverage today?", "pipeline_coverage_check"),
    ("Do we have 3x coverage this quarter?", "pipeline_coverage_check"),
    (
        "What if we raise weighted pipeline coverage from 0.68x to 1.0x by rescuing the top 15 at-risk deals?",
        "pipeline_rescue_whatif",
    ),
    ("Are there any deal slip risks this quarter?", "deal_slip_analysis"),
    ("Which deals are overdue or slipping?", "deal_slip_analysis"),
    ("What is our ARR trajectory for this year?", "arr_trajectory"),
    ("Show me the NRR and GRR breakdown", "arr_trajectory"),
    ("What is the ARR bridge this quarter?", "arr_trajectory"),
    ("Show ramp status for new reps", "rep_ramp_status"),
    ("Which reps are still ramping?", "rep_ramp_status"),
])
def test_revops_intent_classification(planner, message, expected_intent):
    intent = planner.classify(message)
    assert intent == expected_intent, f"For '{message}' expected '{expected_intent}', got '{intent}'"


def test_revenue_waterfall_maps_to_arr_trajectory(planner):
    assert planner.classify("Show me the revenue waterfall") == "arr_trajectory"


def test_generic_metric_fallback(planner):
    """A general revenue question should still produce a valid metric intent."""
    intent = planner.classify("What is our total revenue this quarter?")
    assert intent is not None
    assert isinstance(intent, str)
