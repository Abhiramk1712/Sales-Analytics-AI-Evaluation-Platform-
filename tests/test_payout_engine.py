"""
tests/test_payout_engine.py
============================
Tests for the hybrid payout engine (rule-based + fallback paths).
"""
import pytest
from backend.payout.engine import PayoutEngine, PayoutConfig, CommissionTier, compute_payout, DEFAULT_PAYOUT_CONFIG


class TestPayoutEngine:
    def setup_method(self):
        self.engine = PayoutEngine()

    # ── Rule-based path ───────────────────────────────────────────────────

    def test_tier_0_to_80_pct(self):
        # 50% attainment → tier 0 (3%)
        result = self.engine.compute(50_000, 100_000, 2, 3)
        assert not result["fallback_used"]
        assert result["commission_rate"] == 0.03
        assert result["attainment_pct"] == 50.0
        assert result["payout"] == pytest.approx(50_000 * 0.03, rel=1e-4)

    def test_tier_80_to_100_pct(self):
        result = self.engine.compute(90_000, 100_000, 2, 3)
        assert result["commission_rate"] == 0.05

    def test_tier_100_to_120_pct(self):
        result = self.engine.compute(110_000, 100_000, 3, 1)
        assert result["commission_rate"] == 0.08

    def test_tier_above_120_pct(self):
        result = self.engine.compute(130_000, 100_000, 4, 1)
        assert result["commission_rate"] == 0.10

    def test_accelerator_applied_above_quota(self):
        result = self.engine.compute(120_000, 100_000, 3, 1)
        # Accelerator = (120k - 100k) * 2% = 400
        assert result["accelerator"] == pytest.approx(400.0, rel=1e-4)

    def test_bonus_qualified(self):
        # ≥100% attainment, win_rate ≥55%, deals_won ≥3
        result = self.engine.compute(100_000, 100_000, 4, 2)
        assert result["bonus"] == 2000.0

    def test_bonus_not_qualified_low_win_rate(self):
        # 40% win rate — no bonus
        result = self.engine.compute(100_000, 100_000, 2, 3)
        assert result["bonus"] == 0.0

    def test_bonus_not_qualified_few_deals(self):
        # deals_won < 3 — no bonus
        result = self.engine.compute(100_000, 100_000, 2, 0)
        assert result["bonus"] == 0.0

    def test_rules_applied_list_populated(self):
        result = self.engine.compute(100_000, 100_000, 4, 2)
        assert isinstance(result["rules_applied"], list)
        assert any("attainment" in r for r in result["rules_applied"])

    def test_confidence_is_1_for_rule_path(self):
        result = self.engine.compute(100_000, 100_000, 3, 2)
        assert result["confidence"] == 1.0
        assert not result["fallback_used"]

    # ── Fallback path ─────────────────────────────────────────────────────

    def test_fallback_when_no_quota(self):
        result = self.engine.compute(50_000, 0, 2, 1)
        assert result["fallback_used"]
        assert result["confidence"] < 1.0
        # Flat 5% fallback
        assert result["payout"] == pytest.approx(50_000 * 0.05, rel=1e-4)

    def test_fallback_when_no_revenue_no_quota(self):
        result = self.engine.compute(0, 0, 0, 0)
        assert result["fallback_used"]

    def test_fallback_confidence_below_rule_based(self):
        rule = self.engine.compute(80_000, 100_000, 2, 2)
        fallback = self.engine.compute(80_000, 0, 2, 2)
        assert fallback["confidence"] < rule["confidence"]

    # ── Custom config ─────────────────────────────────────────────────────

    def test_custom_config(self):
        config = PayoutConfig(
            tiers=[CommissionTier(0, 100, 0.07), CommissionTier(100, 999, 0.15)],
            accelerator_rate=0.0,
            team_bonus=0.0,
            team_bonus_threshold_pct=100.0,
            team_bonus_min_win_rate_pct=0.0,
            team_bonus_min_deals=0,
        )
        engine = PayoutEngine(config)
        result = engine.compute(80_000, 100_000, 2, 2)
        assert result["commission_rate"] == 0.07

    # ── Module-level function ─────────────────────────────────────────────

    def test_compute_payout_function(self):
        result = compute_payout(100_000, 100_000, 3, 2)
        assert result["payout"] > 0
        assert "rules_applied" in result

    def test_compute_payout_with_custom_config(self):
        config = PayoutConfig(
            tiers=[CommissionTier(0, 999, 0.10)],
            accelerator_rate=0.0,
            team_bonus=0.0,
            team_bonus_threshold_pct=999.0,
            team_bonus_min_win_rate_pct=999.0,
            team_bonus_min_deals=999,
        )
        result = compute_payout(50_000, 100_000, 1, 1, config=config)
        assert result["commission_rate"] == 0.10
