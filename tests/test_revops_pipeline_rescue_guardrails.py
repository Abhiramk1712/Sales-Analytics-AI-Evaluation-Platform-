from backend.agent.tools.revops_tools import (
    _extract_requested_weighted_coverage_baseline,
    _extract_target_weighted_coverage_details,
)


def test_extract_requested_weighted_coverage_baseline_from_prompt_range():
    msg = "What if we raise weighted pipeline coverage from 0.68x to 1.00x this quarter?"
    assert _extract_requested_weighted_coverage_baseline(msg) == 0.68


def test_extract_requested_weighted_coverage_baseline_from_current_is_phrase():
    msg = "What if current weighted pipeline coverage is 0.60x and we target 1.20x this quarter?"
    assert _extract_requested_weighted_coverage_baseline(msg) == 0.60


def test_extract_target_weighted_coverage_details_marks_explicit_target():
    msg = "Raise weighted pipeline coverage to 1.00x by rescuing top 15 at-risk deals"
    value, explicit = _extract_target_weighted_coverage_details(msg, default_value=1.0)
    assert value == 1.0
    assert explicit is True


def test_extract_target_weighted_coverage_details_for_target_shorthand_phrase():
    msg = "What if current weighted pipeline coverage is 0.60x and we target 1.20x this quarter?"
    value, explicit = _extract_target_weighted_coverage_details(msg, default_value=1.0)
    assert value == 1.2
    assert explicit is True


def test_extract_target_weighted_coverage_details_uses_default_when_missing():
    msg = "Rescue the top 15 at-risk deals and quantify impact"
    value, explicit = _extract_target_weighted_coverage_details(msg, default_value=1.0)
    assert value == 1.0
    assert explicit is False
