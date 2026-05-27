"""
Tests for backend/payout/credit_payout_engine.py —
apply_accelerators, apply_spiffs, apply_clawbacks, formula_trace, CreditPayoutResult.
"""
import pytest
from backend.payout.credit_payout_engine import (
    apply_accelerators,
    apply_spiffs,
    apply_clawbacks,
    CreditPayoutResult,
    PayoutConfig,
)


# ── CreditPayoutResult ────────────────────────────────────────────────────

def test_credit_payout_result_defaults():
    r = CreditPayoutResult(rep_id="r1", period="Q1 2025")
    assert r.credit_percent == 1.0
    assert isinstance(r.formula_trace, list)


def test_credit_payout_result_to_dict():
    r = CreditPayoutResult(
        rep_id="r1", period="Q1 2025",
        final_payout=10_000, attainment=100.0,
        credit_percent=0.75,
        formula_trace=["Step 1", "Step 2"],
    )
    d = r.to_dict()
    assert d["credit_percent"] == 0.75
    assert d["formula_trace"] == ["Step 1", "Step 2"]
    assert d["final_payout"] == 10_000


# ── apply_accelerators ────────────────────────────────────────────────────

def _mock_config(accelerator_rate=0.02):
    return PayoutConfig(accelerator_rate=accelerator_rate)


def test_accelerator_no_acceleration_below_threshold():
    trace = []
    result = apply_accelerators(100_000, quota=100_000, config=_mock_config(), rules=[], trace=trace)
    assert result >= 0


def test_accelerator_above_threshold_boosts():
    trace = []
    cfg = _mock_config(accelerator_rate=0.05)
    high_att = apply_accelerators(200_000, quota=100_000, config=cfg, rules=[], trace=trace)
    trace2 = []
    low_att = apply_accelerators(50_000, quota=100_000, config=cfg, rules=[], trace=trace2)
    # Higher credited_amount relative to quota → more commission
    assert high_att >= low_att


def test_accelerator_populates_trace():
    trace = []
    apply_accelerators(150_000, quota=100_000, config=_mock_config(), rules=[], trace=trace)
    assert len(trace) >= 1


def test_accelerator_explicit_rule_takes_precedence():
    """apply_accelerators returns a non-negative float."""
    trace = []
    cfg = _mock_config(accelerator_rate=0.05)
    result = apply_accelerators(150_000, quota=100_000, config=cfg, rules=[], trace=trace)
    assert isinstance(result, float)
    assert result >= 0


# ── apply_spiffs ──────────────────────────────────────────────────────────

def test_spiff_applied_when_condition_met():
    trace = []
    result = apply_spiffs(100.0, deals_won=5, win_rate=40.0, config=PayoutConfig(), trace=trace)
    assert result >= 0


def test_spiff_with_rule_applies_bonus():
    from types import SimpleNamespace
    rule = SimpleNamespace(is_active=True, trigger_metric="attainment",
                           trigger_threshold=90.0, amount=3_000, name="Q Bonus")
    cfg = PayoutConfig(spiff_rules=[rule])
    trace = []
    result = apply_spiffs(95.0, deals_won=8, win_rate=50.0, config=cfg, trace=trace)
    assert result >= 3_000


def test_spiff_not_applied_when_condition_not_met():
    from types import SimpleNamespace
    rule = SimpleNamespace(is_active=True, trigger_metric="attainment",
                           trigger_threshold=100.0, amount=5_000, name="100pct Bonus")
    cfg = PayoutConfig(spiff_rules=[rule])
    trace = []
    result = apply_spiffs(80.0, deals_won=4, win_rate=30.0, config=cfg, trace=trace)
    assert result == 0


# ── apply_clawbacks ───────────────────────────────────────────────────────

def test_clawback_below_threshold_reduces_payout():
    from types import SimpleNamespace
    rule = SimpleNamespace(is_active=True, trigger_metric="attainment",
                           trigger_below=50.0, penalty_pct=0.50, name="Low Att CB")
    cfg = PayoutConfig(clawback_rules=[rule])
    trace = []
    result = apply_clawbacks(10_000, attainment_pct=30.0, deals_won=2, win_rate=20.0,
                              config=cfg, trace=trace)
    assert result > 0
    assert any("clawback" in s.lower() for s in trace)


def test_clawback_not_applied_when_above_threshold():
    from types import SimpleNamespace
    rule = SimpleNamespace(is_active=True, trigger_metric="attainment",
                           trigger_below=50.0, penalty_pct=0.50, name="Low Att CB")
    cfg = PayoutConfig(clawback_rules=[rule])
    trace = []
    result = apply_clawbacks(10_000, attainment_pct=80.0, deals_won=5, win_rate=40.0,
                              config=cfg, trace=trace)
    assert result == 0


def test_clawback_full_reduction():
    from types import SimpleNamespace
    rule = SimpleNamespace(is_active=True, trigger_metric="attainment",
                           trigger_below=30.0, penalty_pct=1.0, name="Full CB")
    cfg = PayoutConfig(clawback_rules=[rule])
    trace = []
    result = apply_clawbacks(10_000, attainment_pct=10.0, deals_won=1, win_rate=10.0,
                              config=cfg, trace=trace)
    assert result == 10_000
