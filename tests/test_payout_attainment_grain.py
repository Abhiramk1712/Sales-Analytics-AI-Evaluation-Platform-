"""
tests/test_payout_attainment_grain.py
=====================================
Attainment is a period-cumulative measure, and commission tiers must be
evaluated against it — not against each sales credit in isolation.

The bug these cover: attainment was computed per SalesCredit, so a rep closing
eight $25k deals against a $200k quota registered ~12.5% attainment eight
separate times and never crossed a tier threshold. Tiered rates, accelerators
and attainment bonuses could not fire for any rep with more than one credit.
"""
from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from backend.payout.credit_payout_engine import (
    _apply_commission_rules,
    allocate_pro_rata,
)


def _rule(metric_name: str, *, name: str, rate=None, tmin=None, tmax=None, bonus=None):
    """Minimal stand-in for a Rule row — the engine only reads these fields."""
    return SimpleNamespace(
        metric_name=metric_name,
        name=name,
        rate=rate,
        threshold_min=tmin,
        threshold_max=tmax,
        bonus_amount=bonus,
    )


# ── Tiered rates reached cumulatively ────────────────────────────────────────

TIERS = [
    _rule("attainment_pct", name="tier-1", rate=0.05, tmin=0, tmax=100),
    _rule("attainment_pct", name="tier-2", rate=0.08, tmin=100, tmax=150),
    _rule("attainment_pct", name="tier-3", rate=0.12, tmin=150, tmax=None),
]


def test_single_credit_below_first_tier_pays_base_rate():
    comp = _apply_commission_rules(25_000.0, 200_000.0, TIERS)
    assert comp["base_commission"] == pytest.approx(25_000 * 0.05)


def test_eight_credits_summed_cross_into_the_second_tier():
    """
    Eight $25k credits against a $200k quota is 100% attainment, which lands in
    tier 2. Evaluated per credit each one is 12.5% and stays in tier 1 — the
    regression this guards.
    """
    total = 8 * 25_000.0
    comp = _apply_commission_rules(total, 200_000.0, TIERS)

    assert comp["base_commission"] == pytest.approx(total * 0.08)

    per_credit = _apply_commission_rules(25_000.0, 200_000.0, TIERS)
    assert per_credit["base_commission"] * 8 < comp["base_commission"], (
        "per-credit evaluation must pay strictly less than cumulative "
        "evaluation here; if it does not, the tiers are not being reached"
    )


def test_overachievement_reaches_the_top_tier():
    comp = _apply_commission_rules(320_000.0, 200_000.0, TIERS)
    assert comp["base_commission"] == pytest.approx(320_000 * 0.12)


# ── One boundary convention, applied to every rule type ──────────────────────


def test_threshold_boundary_is_inclusive_at_the_lower_bound():
    """A rep landing exactly on 100% is paid at tier 2, not tier 1."""
    comp = _apply_commission_rules(200_000.0, 200_000.0, TIERS)
    assert comp["base_commission"] == pytest.approx(200_000 * 0.08)


def test_bands_do_not_overlap_at_the_boundary():
    """Exactly one attainment tier may match, so no double payment."""
    comp = _apply_commission_rules(200_000.0, 200_000.0, TIERS)
    assert len([r for r in comp["rules_applied"] if r.startswith("rule ")]) == 1


def test_accelerator_and_bonus_share_the_boundary_convention():
    """
    Previously accelerator used `> min` and bonus used `>= min`, so a rep exactly
    on the threshold got one but not the other.
    """
    rules = [
        _rule("accelerator", name="acc", rate=0.03, tmin=100),
        _rule("bonus", name="bonus", tmin=100, bonus=5_000),
    ]
    comp = _apply_commission_rules(200_000.0, 200_000.0, rules)

    assert comp["bonus_amount"] == pytest.approx(5_000)
    # At exactly quota there is no overage, so the accelerator is $0 — but it
    # must have been *evaluated*, which the trace records.
    assert any("accelerator" in r for r in comp["rules_applied"])


def test_accelerator_pays_on_overage_above_quota():
    rules = [_rule("accelerator", name="acc", rate=0.03, tmin=100)]
    comp = _apply_commission_rules(250_000.0, 200_000.0, rules)
    assert comp["accelerator_amount"] == pytest.approx(50_000 * 0.03)


# ── Falsy-zero handling ──────────────────────────────────────────────────────


def test_zero_upper_threshold_is_honoured_not_treated_as_open_ended():
    """
    `float(rule.threshold_max or 9999)` turned a legitimate 0 into 9999, making
    a closed band open-ended. A band of [0, 0) matches nothing.
    """
    rules = [_rule("attainment_pct", name="degenerate", rate=0.05, tmin=0, tmax=0)]
    comp = _apply_commission_rules(100_000.0, 200_000.0, rules)
    assert comp["base_commission"] == 0.0


def test_missing_upper_threshold_is_open_ended():
    rules = [_rule("attainment_pct", name="top", rate=0.05, tmin=0, tmax=None)]
    comp = _apply_commission_rules(100_000.0, 200_000.0, rules)
    assert comp["base_commission"] == pytest.approx(5_000)


def test_zero_rate_is_honoured():
    rules = [_rule("attainment_pct", name="unpaid", rate=0, tmin=0, tmax=None)]
    comp = _apply_commission_rules(100_000.0, 200_000.0, rules)
    assert comp["base_commission"] == 0.0


# ── Pro-rata allocation reconciles exactly ───────────────────────────────────


def test_allocation_sums_to_the_total():
    parts = allocate_pro_rata(1_000.00, [1.0, 1.0, 1.0])
    assert sum(parts) == pytest.approx(1_000.00)


def test_allocation_absorbs_the_rounding_residual():
    """Three-way split of a cent-awkward total must still reconcile exactly."""
    parts = allocate_pro_rata(100.01, [1.0, 1.0, 1.0])
    assert round(sum(parts), 2) == 100.01
    assert len(parts) == 3


def test_allocation_is_proportional_to_weight():
    parts = allocate_pro_rata(1_000.0, [75_000.0, 25_000.0])
    assert parts[0] == pytest.approx(750.0)
    assert parts[1] == pytest.approx(250.0)


def test_allocation_handles_zero_total_weight():
    parts = allocate_pro_rata(500.0, [0.0, 0.0])
    assert round(sum(parts), 2) == 500.0


def test_allocation_of_empty_weights_is_empty():
    assert allocate_pro_rata(500.0, []) == []


@pytest.mark.parametrize(
    "total,weights",
    [
        (10_000.00, [3_333.0, 3_333.0, 3_334.0]),
        (0.01, [1.0, 1.0, 1.0]),
        (99_999.99, [1.0, 2.0, 3.0, 5.0, 8.0]),
        (1_234.56, [0.1, 0.2, 0.7]),
    ],
)
def test_allocation_always_reconciles(total, weights):
    parts = allocate_pro_rata(total, weights)
    assert round(sum(parts), 2) == round(total, 2)


def test_no_infinite_sentinel_leaks_into_a_payout():
    """math.inf is the open upper bound; it must never reach a paid amount."""
    rules = [_rule("attainment_pct", name="top", rate=0.05, tmin=0, tmax=None)]
    comp = _apply_commission_rules(100_000.0, 200_000.0, rules)
    assert math.isfinite(comp["base_commission"])
    assert math.isfinite(comp["accelerator_amount"])
    assert math.isfinite(comp["bonus_amount"])


# ── #19: the arithmetic is exact, not rounded-at-the-end ─────────────────────


def test_money_conversion_goes_through_str_not_the_float_value():
    """
    Decimal(0.1) is 0.1000000000000000055511151231257827 — the float's real
    value. Decimal("0.1") is exactly one tenth. Converting via the repr is what
    makes a value read from a float mean what it appears to mean.
    """
    from decimal import Decimal

    from backend.payout.money import D

    assert D(0.1) == Decimal("0.1")
    assert D(0.1) + D(0.2) == D(0.3)
    assert D(None) == Decimal("0")
    assert D("not a number") == Decimal("0")


def test_allocation_is_exact_where_float_arithmetic_drifts():
    """
    A three-way split of a value floats cannot represent. Under float each share
    carries error and the parts do not sum to the total without the residual
    correction; under Decimal the arithmetic itself is exact.
    """
    from decimal import Decimal

    from backend.payout.credit_payout_engine import allocate_pro_rata

    parts = allocate_pro_rata(0.30, [1.0, 1.0, 1.0])
    assert sum(Decimal(str(p)) for p in parts) == Decimal("0.30")


@pytest.mark.parametrize(
    "total,weights",
    [
        (1_000_000.01, [1.0] * 7),
        (0.07, [1.0, 1.0, 1.0]),
        (33.33, [2.0, 3.0, 5.0, 7.0]),
        (12_345.67, [1.1, 2.2, 3.3]),
    ],
)
def test_allocation_reconciles_exactly_in_decimal(total, weights):
    from decimal import Decimal

    from backend.payout.credit_payout_engine import allocate_pro_rata

    parts = allocate_pro_rata(total, weights)
    assert sum(Decimal(str(p)) for p in parts) == Decimal(str(total)).quantize(Decimal("0.01"))


def test_commission_rate_multiplication_is_exact():
    """
    A rate is one operand of a multiplication whose result is money. Leaving it
    a float puts the error back even when the amount is Decimal.
    """
    from decimal import Decimal

    rules = [_rule("attainment_pct", name="tier", rate=0.07, tmin=0, tmax=None)]
    comp = _apply_commission_rules(1_000_000.10, 500_000.0, rules)

    # 7% of 1,000,000.10 is exactly 70,000.007, which rounds to 70,000.01.
    assert Decimal(str(comp["base_commission"])) == Decimal("70000.01")
