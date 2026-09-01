"""
backend/payout/engine.py
========================
Hybrid commission payout engine.

Primary path:  Configurable rule-based tiered commissions (rate, accelerator,
               team bonus, cap) — used when required fields are available.
Fallback path: Attainment-based estimate — used when quota or revenue is missing.

All outputs include `confidence`, `fallback_used`, and `rules_applied` so the
dashboard and reports can surface transparency labels.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommissionTier:
    """A single attainment tier band."""
    min_attainment_pct: float   # inclusive lower bound (e.g. 0)
    max_attainment_pct: float   # exclusive upper bound (use 999 for "uncapped")
    rate: float                 # commission rate as decimal (0.08 = 8 %)


@dataclass
class SpiffRule:
    """A SPIFF (Sales Performance Incentive Fund) or one-time incentive rule."""
    name: str
    amount: float                   # flat bonus amount
    trigger_metric: str = "win_rate"  # win_rate | attainment | deals_won
    trigger_threshold: float = 0.0    # minimum value to qualify
    is_active: bool = True


@dataclass
class ClawbackRule:
    """A clawback rule that reduces payout under poor-performance conditions."""
    name: str
    penalty_pct: float          # percentage to deduct from total payout (0.0–1.0)
    trigger_metric: str = "win_rate"  # win_rate | attainment | deals_won
    trigger_below: float = 0.0        # triggers when metric falls BELOW this threshold
    is_active: bool = True


@dataclass
class PayoutConfig:
    """
    Configurable commission plan.

    tiers          — ordered list of CommissionTier objects (low → high)
    accelerator_rate — additional rate applied to revenue above quota
    team_bonus     — flat bonus for attainment ≥ team_bonus_threshold_pct
                     AND minimum win_rate ≥ team_bonus_min_win_rate_pct
                     AND minimum deals_won ≥ team_bonus_min_deals
    cap_multiplier — maximum payout as multiple of quota (None = uncapped)
    spiff_rules    — list of SpiffRule objects applied after base commission
    clawback_rules — list of ClawbackRule objects that reduce final payout
    """
    tiers: list[CommissionTier] = field(default_factory=list)
    accelerator_rate: float = 0.02
    team_bonus: float = 2000.0
    team_bonus_threshold_pct: float = 100.0
    team_bonus_min_win_rate_pct: float = 55.0
    team_bonus_min_deals: int = 3
    cap_multiplier: float | None = None
    spiff_rules: list[SpiffRule] = field(default_factory=list)
    clawback_rules: list[ClawbackRule] = field(default_factory=list)


DEFAULT_PAYOUT_CONFIG = PayoutConfig(
    tiers=[
        CommissionTier(0, 80, 0.03),
        CommissionTier(80, 100, 0.05),
        CommissionTier(100, 120, 0.08),
        CommissionTier(120, 999, 0.10),
    ],
    accelerator_rate=0.02,
    team_bonus=2000.0,
    team_bonus_threshold_pct=100.0,
    team_bonus_min_win_rate_pct=55.0,
    team_bonus_min_deals=3,
    cap_multiplier=None,
    # Both surfaces returned an empty list, and neither engine branch ever ran
    # against data, despite SPIFs and clawbacks being named capabilities. These
    # are deliberately modest and threshold-gated so they exercise the code
    # paths without dominating a payout: the SPIF is a flat quarterly incentive
    # for genuine overachievement, the clawback a small deduction for a win rate
    # far below plan.
    spiff_rules=[
        SpiffRule(
            name="Quarterly overachiever",
            amount=1500.0,
            trigger_metric="attainment",
            trigger_threshold=120.0,
        ),
        SpiffRule(
            name="High win rate",
            amount=750.0,
            trigger_metric="win_rate",
            trigger_threshold=75.0,
        ),
    ],
    clawback_rules=[
        ClawbackRule(
            name="Win rate below plan",
            penalty_pct=0.05,
            trigger_metric="win_rate",
            trigger_below=40.0,
        ),
    ],
)


class PayoutEngine:
    """Hybrid commission payout engine."""

    def __init__(self, config: PayoutConfig | None = None) -> None:
        self.config = config or DEFAULT_PAYOUT_CONFIG

    # ── Primary: rule-based ───────────────────────────────────────────────
    def _rule_based(
        self,
        rep_revenue: float,
        rep_quota: float,
        deals_won: int,
        deals_lost: int,
    ) -> dict[str, Any]:
        attainment_pct = (100.0 * rep_revenue / rep_quota) if rep_quota > 0 else 0.0
        total_closed = deals_won + deals_lost
        win_rate = (100.0 * deals_won / total_closed) if total_closed > 0 else 0.0

        # Tiered base commission
        commission_rate = self.config.tiers[0].rate
        for tier in self.config.tiers:
            if tier.min_attainment_pct <= attainment_pct < tier.max_attainment_pct:
                commission_rate = tier.rate
                break

        base_commission = rep_revenue * commission_rate
        accelerator = max(0.0, rep_revenue - rep_quota) * self.config.accelerator_rate

        qualifies_bonus = (
            attainment_pct >= self.config.team_bonus_threshold_pct
            and win_rate >= self.config.team_bonus_min_win_rate_pct
            and deals_won >= self.config.team_bonus_min_deals
        )
        bonus = self.config.team_bonus if qualifies_bonus else 0.0

        payout = base_commission + accelerator + bonus

        if self.config.cap_multiplier is not None and rep_quota > 0:
            cap = rep_quota * self.config.cap_multiplier
            payout = min(payout, cap)

        rules: list[str] = [
            f"attainment={attainment_pct:.1f}%",
            f"tier_rate={commission_rate:.0%}",
            f"base_commission=${base_commission:,.2f}",
            f"accelerator=${accelerator:,.2f}",
        ]
        if qualifies_bonus:
            rules.append(f"team_bonus=${bonus:,.2f} (qualified)")
        if self.config.cap_multiplier is not None:
            rules.append(f"cap={self.config.cap_multiplier}x quota")

        # ── SPIFFs ────────────────────────────────────────────────────────
        spiff_total = 0.0
        metrics_map = {
            "win_rate": win_rate,
            "attainment": attainment_pct,
            "deals_won": float(deals_won),
        }
        for spiff in (self.config.spiff_rules or []):
            if not spiff.is_active:
                continue
            metric_val = metrics_map.get(spiff.trigger_metric, 0.0)
            if metric_val >= spiff.trigger_threshold:
                spiff_total += spiff.amount
                rules.append(f"SPIFF '{spiff.name}': ${spiff.amount:,.2f} (qualified)")

        payout += spiff_total

        # ── Clawbacks ─────────────────────────────────────────────────────
        clawback_total = 0.0
        for cb in (self.config.clawback_rules or []):
            if not cb.is_active:
                continue
            metric_val = metrics_map.get(cb.trigger_metric, 0.0)
            if metric_val < cb.trigger_below:
                deduction = payout * cb.penalty_pct
                clawback_total += deduction
                rules.append(f"Clawback '{cb.name}': -${deduction:,.2f} ({cb.penalty_pct:.0%} penalty)")

        payout = max(0.0, payout - clawback_total)

        return {
            "payout": round(payout, 2),
            "base_commission": round(base_commission, 2),
            "accelerator": round(accelerator, 2),
            "bonus": round(bonus, 2),
            "spiff_total": round(spiff_total, 2),
            "clawback_total": round(clawback_total, 2),
            "commission_rate": commission_rate,
            "attainment_pct": round(attainment_pct, 2),
            "win_rate": round(win_rate, 2),
            "rules_applied": rules,
            "confidence": 1.0,
            "fallback_used": False,
        }
    def _attainment_fallback(
        self,
        rep_revenue: float,
        rep_quota: float,
        deals_won: int,
        deals_lost: int,
        missing_fields: list[str],
    ) -> dict[str, Any]:
        """Simple attainment-based estimate when required fields are absent."""
        attainment_pct = (100.0 * rep_revenue / rep_quota) if rep_quota > 0 else 0.0
        # Simplified 5 % flat commission fallback
        payout = rep_revenue * 0.05
        total_closed = deals_won + deals_lost
        win_rate = (100.0 * deals_won / total_closed) if total_closed > 0 else 0.0

        confidence = 0.40 if not rep_quota else 0.60
        rules = [
            "fallback: flat 5% commission (missing fields prevent full rule evaluation)",
            f"missing: {', '.join(missing_fields)}",
            f"attainment={attainment_pct:.1f}%",
        ]

        return {
            "payout": round(payout, 2),
            "base_commission": round(payout, 2),
            "accelerator": 0.0,
            "bonus": 0.0,
            "commission_rate": 0.05,
            "attainment_pct": round(attainment_pct, 2),
            "win_rate": round(win_rate, 2),
            "rules_applied": rules,
            "confidence": confidence,
            "fallback_used": True,
        }

    # ── Public entry point ────────────────────────────────────────────────
    def compute(
        self,
        rep_revenue: float,
        rep_quota: float,
        deals_won: int,
        deals_lost: int,
    ) -> dict[str, Any]:
        """Compute payout, auto-routing between rule-based and fallback paths."""
        missing: list[str] = []
        if rep_revenue == 0 and rep_quota == 0:
            missing.append("revenue")
            missing.append("quota")
        elif rep_quota == 0:
            missing.append("quota")

        if missing:
            return self._attainment_fallback(rep_revenue, rep_quota, deals_won, deals_lost, missing)

        return self._rule_based(rep_revenue, rep_quota, deals_won, deals_lost)


def build_payout_config_from_rules(rules: list) -> "PayoutConfig":
    """Build a PayoutConfig from a list of Rule ORM objects (or dicts with the same fields).

    Reads threshold_min, threshold_max, rate, accelerator_rate, bonus_amount from each rule.
    Falls back to DEFAULT_PAYOUT_CONFIG when the rules list is empty.
    """
    if not rules:
        return DEFAULT_PAYOUT_CONFIG

    tiers: list[CommissionTier] = []
    team_bonus = 0.0
    team_bonus_threshold_pct = 100.0
    accelerator_rate = 0.02  # sensible default

    for rule in sorted(rules, key=lambda r: float(getattr(r, "threshold_min", 0) or 0)):
        t_min = float(getattr(rule, "threshold_min", 0) or 0)
        t_max = float(getattr(rule, "threshold_max", 999) or 999)
        rate = float(getattr(rule, "rate", 0.03) or 0.03)
        bonus = float(getattr(rule, "bonus_amount", 0) or 0)
        accel = float(getattr(rule, "accelerator_rate", 0) or 0)

        tiers.append(CommissionTier(t_min, t_max, rate))

        if bonus > team_bonus:
            team_bonus = bonus
            team_bonus_threshold_pct = t_min  # bonus kicks in at this attainment

        if accel > accelerator_rate:
            accelerator_rate = accel

    return PayoutConfig(
        tiers=tiers,
        accelerator_rate=accelerator_rate,
        team_bonus=team_bonus,
        team_bonus_threshold_pct=team_bonus_threshold_pct,
        team_bonus_min_win_rate_pct=0.0,  # not encoded in Rule schema; use permissive default
        team_bonus_min_deals=0,
        cap_multiplier=None,
    )


# Module-level convenience singleton
_default_engine = PayoutEngine()


def compute_payout(
    rep_revenue: float,
    rep_quota: float,
    deals_won: int,
    deals_lost: int,
    config: PayoutConfig | None = None,
) -> dict[str, Any]:
    """Compute payout using the default (or provided) config."""
    engine = PayoutEngine(config) if config else _default_engine
    return engine.compute(rep_revenue, rep_quota, deals_won, deals_lost)
