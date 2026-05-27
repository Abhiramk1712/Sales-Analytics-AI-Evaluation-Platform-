"""
backend/payout/credit_payout_engine.py
=======================================
Credit-level payout computation engine.

TRUE CREDIT-LEVEL FLOW
----------------------
SalesUnit / Deal / Booking
  → SalesCredit (what the rep gets credited for)
    → UserProfile / Rep
      → PlanAssignment (which comp plan applies)
        → Rule (rates, thresholds, accelerators)
          → PayoutRecord (computed output, persisted)

FALLBACK
--------
If SalesCredit rows are unavailable, falls back to rep-level
revenue/quota estimates from the existing PayoutEngine.
All fallback results are tagged: fallback_mode = "rep_level_estimate".

PAYOUT OUTPUT FIELDS
--------------------
  rep_id, period, sales_credit_id, source_deal_id,
  credited_amount, quota, attainment, base_commission,
  accelerator_amount, spiff_amount, bonus_amount,
  clawback_amount, final_payout, rules_applied,
  confidence, warnings, fallback_mode
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models import (
    Rep, Revenue, Quota, Plan, Rule, PlanAssignment,
    PayoutRecord as PayoutRecordModel,
    UserProfile, Manager, Position, PlanCascadeRule,
    SalesCredit, SalesUnit,
)
from backend.payout.engine import PayoutConfig, PayoutEngine, DEFAULT_PAYOUT_CONFIG


# ── Output dataclass ──────────────────────────────────────────────────────

@dataclass
class CreditPayoutResult:
    rep_id:             str
    period:             str
    sales_credit_id:    Optional[str]    = None
    source_deal_id:     Optional[str]    = None
    credited_amount:    float            = 0.0
    credit_percent:     float            = 1.0
    quota:              float            = 0.0
    attainment:         float            = 0.0
    base_commission:    float            = 0.0
    accelerator_amount: float            = 0.0
    spiff_amount:       float            = 0.0
    bonus_amount:       float            = 0.0
    clawback_amount:    float            = 0.0
    final_payout:       float            = 0.0
    rules_applied:      list[str]        = field(default_factory=list)
    formula_trace:      list[str]        = field(default_factory=list)
    confidence:         str              = "medium"
    fallback_mode:      str              = "none"
    warnings:           list[str]        = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rep_id":             self.rep_id,
            "period":             self.period,
            "sales_credit_id":    self.sales_credit_id,
            "source_deal_id":     self.source_deal_id,
            "credited_amount":    round(self.credited_amount, 2),
            "credit_percent":     round(self.credit_percent, 4),
            "quota":              round(self.quota, 2),
            "attainment":         round(self.attainment, 2),
            "base_commission":    round(self.base_commission, 2),
            "accelerator_amount": round(self.accelerator_amount, 2),
            "spiff_amount":       round(self.spiff_amount, 2),
            "bonus_amount":       round(self.bonus_amount, 2),
            "clawback_amount":    round(self.clawback_amount, 2),
            "final_payout":       round(self.final_payout, 2),
            "rules_applied":      self.rules_applied,
            "formula_trace":      self.formula_trace,
            "confidence":         self.confidence,
            "fallback_mode":      self.fallback_mode,
            "warnings":           self.warnings,
        }


# ── Plan/Rule resolution helpers ─────────────────────────────────────────

async def _get_user_id_for_rep(db: AsyncSession, rep: Rep) -> Optional[uuid.UUID]:
    """Resolve the UserProfile.id for a Rep by matching on email."""
    user = (
        await db.execute(
            select(UserProfile).where(UserProfile.email == rep.email)
        )
    ).scalars().first()
    return user.id if user else None


async def _get_manager_chain(
    db: AsyncSession,
    user_id: uuid.UUID,
    max_depth: int = 6,
) -> list[uuid.UUID]:
    """
    Walk the Manager table upward from user_id and return the ordered list of
    ancestor user_ids (closest manager first, up to the root/CEO).
    Stops at max_depth to prevent infinite loops on circular data.
    """
    chain: list[uuid.UUID] = []
    current = user_id
    for _ in range(max_depth):
        row = (
            await db.execute(
                select(Manager).where(Manager.user_id == current)
            )
        ).scalars().first()
        if row is None or row.manager_user_id is None:
            break
        chain.append(row.manager_user_id)
        current = row.manager_user_id
    return chain


async def _resolve_cascade_rules(
    db: AsyncSession,
    user_id: uuid.UUID,
    period: str,
) -> list[tuple[Plan, list[Rule], int]]:
    """
    Walk the manager chain upward and collect all PlanCascadeRules that cover
    the given user based on their position rank and the rule's min/max rank.

    Returns a list of (Plan, [Rule], priority) sorted by priority ASC,
    so callers apply global executive rules first and individual overrides last.

    Only rules whose effective window covers today (or is open-ended) are returned.
    """
    from datetime import date as date_type
    today = date_type.today()

    # Determine this user's position rank
    user_row = (await db.execute(select(UserProfile).where(UserProfile.id == user_id))).scalars().first()
    if user_row is None:
        return []
    user_rank: int = 99
    if user_row.position_id:
        pos = (await db.execute(select(Position).where(Position.id == user_row.position_id))).scalars().first()
        if pos is not None:
            user_rank = pos.rank

    # Collect all ancestor user_ids (self + manager chain)
    ancestor_ids = await _get_manager_chain(db, user_id)
    # Include user themselves so direct-assign plans via cascade are also found
    candidate_owners = [user_id] + ancestor_ids

    collected: list[tuple[Plan, list[Rule], int]] = []
    for owner_id in candidate_owners:
        cascade_rows = (
            await db.execute(
                select(PlanCascadeRule)
                .where(PlanCascadeRule.owner_user_id == owner_id)
                .where(PlanCascadeRule.min_rank <= user_rank)
                .where(PlanCascadeRule.max_rank >= user_rank)
                .order_by(PlanCascadeRule.priority)
            )
        ).scalars().all()

        for cr in cascade_rows:
            # Check effective window
            if cr.effective_start_date and cr.effective_start_date > today:
                continue
            if cr.effective_end_date and cr.effective_end_date < today:
                continue
            # Skip if scope is direct_reports and owner is not the immediate manager
            if cr.cascade_scope == "direct_reports" and owner_id != (ancestor_ids[0] if ancestor_ids else None):
                continue

            plan = (await db.execute(select(Plan).where(Plan.id == cr.plan_id))).scalars().first()
            if plan is None:
                continue
            rules = (await db.execute(select(Rule).where(Rule.plan_id == plan.id))).scalars().all()
            collected.append((plan, list(rules), cr.priority))

    # Sort by priority so global/executive rules are applied first
    collected.sort(key=lambda t: t[2])
    return collected


async def _get_plan_for_rep(
    db: AsyncSession,
    rep: Rep,
    period: str,
) -> Optional[tuple[Plan, list[Rule]]]:
    """Return the active Plan and its Rules for a rep+period, or None.

    Resolution order:
    1. Direct PlanAssignment (individual scope) — highest specificity.
    2. Cascaded rules from manager chain via PlanCascadeRule — global/dept/team.
    Returns the first (highest-priority) match.
    """
    # Resolve the UserProfile.id that corresponds to this rep (matched by email)
    user_id = await _get_user_id_for_rep(db, rep)
    if user_id is None:
        return None

    # 1. Direct individual PlanAssignment
    pa = (
        await db.execute(
            select(PlanAssignment)
            .where(PlanAssignment.user_id == user_id)
        )
    ).scalars().first()
    if pa is not None:
        plan = (await db.execute(select(Plan).where(Plan.id == pa.plan_id))).scalars().first()
        if plan is not None:
            rules = (await db.execute(select(Rule).where(Rule.plan_id == plan.id))).scalars().all()
            return plan, list(rules)

    # 2. Cascade rules from manager chain
    cascaded = await _resolve_cascade_rules(db, user_id, period)
    if cascaded:
        # First entry is the highest-priority (lowest priority number) cascaded plan
        plan, rules, _priority = cascaded[0]
        return plan, rules

    return None


def _apply_commission_rules(
    credited_amount: float,
    quota: float,
    rules: list[Rule],
) -> dict[str, Any]:
    """
    Apply plan rules to a credited amount.

    Returns dict with: base_commission, accelerator_amount, bonus_amount,
    rules_applied, rate_applied.
    """
    attainment = (credited_amount / quota * 100) if quota > 0 else 0.0
    base = 0.0
    accelerator = 0.0
    bonus = 0.0
    rules_applied: list[str] = []

    for rule in rules:
        rate = float(rule.rate or 0)
        min_t = float(rule.threshold_min or 0)
        max_t = float(rule.threshold_max or 9999)
        bonus_a = float(rule.bonus_amount or 0)

        if rule.metric_name == "attainment_pct":
            if min_t <= attainment < max_t:
                commission = credited_amount * rate
                base += commission
                rules_applied.append(f"rule '{rule.name}': attainment={attainment:.1f}%, rate={rate:.0%}, commission=${commission:,.2f}")
        elif rule.metric_name == "accelerator":
            if attainment > min_t:
                overage = max(0.0, credited_amount - quota)
                acc = overage * rate
                accelerator += acc
                rules_applied.append(f"accelerator '{rule.name}': ${acc:,.2f}")
        elif rule.metric_name == "bonus" and bonus_a > 0:
            if attainment >= min_t:
                bonus += bonus_a
                rules_applied.append(f"bonus '{rule.name}': ${bonus_a:,.2f}")

    return {
        "base_commission":    round(base, 2),
        "accelerator_amount": round(accelerator, 2),
        "bonus_amount":       round(bonus, 2),
        "rate_applied":       base / credited_amount if credited_amount > 0 else 0.0,
        "rules_applied":      rules_applied,
    }


# ── Fallback: rep-level estimate ─────────────────────────────────────────

async def _rep_level_fallback(
    db: AsyncSession,
    rep: Rep,
    period: str,
    config: PayoutConfig,
) -> CreditPayoutResult:
    """
    Fall back to the existing rep-level PayoutEngine when credit rows are absent.
    Clearly labels result as fallback_mode = "rep_level_estimate".
    """
    from backend.models import Deal
    warnings = [
        "[FALLBACK] SalesCredit rows not found for this rep/period. "
        "Using rep-level revenue/quota aggregate estimate. "
        "Credit-level payout requires SalesCredit data to be ingested."
    ]

    # Rev for period — revenue is stored monthly ("YYYY-MM"), so expand quarterly
    # periods to the three constituent months before summing.
    if "-Q" in str(period):
        try:
            _yr, _qn = str(period).split("-Q")
            _months = [f"{_yr}-{(int(_qn) - 1) * 3 + m:02d}" for m in range(1, 4)]
        except (ValueError, AttributeError):
            _months = []
        rev = (
            await db.execute(
                select(func.sum(Revenue.amount))
                .where(Revenue.rep_id == rep.id)
                .where(Revenue.period.in_(_months))
            )
        ).scalar() or 0.0
    else:
        rev = (
            await db.execute(
                select(func.sum(Revenue.amount))
                .where(Revenue.rep_id == rep.id)
                .where(Revenue.period == period)
            )
        ).scalar() or 0.0

    # Quota for period
    quota = (
        await db.execute(
            select(func.sum(Quota.amount))
            .where(Quota.rep_id == rep.id)
            .where(Quota.period == period)
        )
    ).scalar() or 0.0

    # Deals won / lost
    won = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep.id)
            .where(Deal.stage == "Closed Won")
        )
    ).scalar() or 0
    lost = (
        await db.execute(
            select(func.count(Deal.id))
            .where(Deal.rep_id == rep.id)
            .where(Deal.stage == "Closed Lost")
        )
    ).scalar() or 0

    engine = PayoutEngine(config=config)
    result = engine.compute(float(rev), float(quota), int(won), int(lost))

    return CreditPayoutResult(
        rep_id=str(rep.id),
        period=period,
        credited_amount=float(rev),
        quota=float(quota),
        attainment=result.get("attainment_pct", 0.0),
        base_commission=result.get("base_commission", 0.0),
        accelerator_amount=result.get("accelerator", 0.0),
        spiff_amount=result.get("spiff_total", 0.0),
        bonus_amount=result.get("bonus", 0.0),
        clawback_amount=result.get("clawback_total", 0.0),
        final_payout=result.get("payout", 0.0),
        rules_applied=result.get("rules_applied", []),
        confidence="medium",
        fallback_mode="rep_level_estimate",
        warnings=warnings,
    )


# ── Credit-level input helpers ───────────────────────────────────────────

async def get_credit_level_payout_inputs(
    db: AsyncSession,
    rep: Rep,
    period: str,
) -> list[dict[str, Any]]:
    """
    Fetch SalesCredit rows for the given rep's UserProfile in the given period.
    Each row includes the linked SalesUnit's booked_date and deal FK.
    Returns [] when no credits exist (caller falls back to rep-level).
    """
    user_id = await _get_user_id_for_rep(db, rep)
    if user_id is None:
        return []

    rows = (
        await db.execute(
            select(SalesCredit, SalesUnit)
            .join(SalesUnit, SalesCredit.sales_unit_id == SalesUnit.id)
            .where(SalesCredit.user_id == user_id)
        )
    ).all()

    # Determine which months belong to the requested period.
    # period may be "YYYY-QX" (quarterly) or "YYYY-MM" (monthly).
    if "-Q" in str(period):
        try:
            _yr, _qn = str(period).split("-Q")
            _start_month = (int(_qn) - 1) * 3 + 1
            _valid_months = {f"{_yr}-{m:02d}" for m in range(_start_month, _start_month + 3)}
        except (ValueError, AttributeError):
            _valid_months = set()
    else:
        _valid_months = {str(period)[:7]}

    # Filter to credits whose SalesUnit booked_date falls in the period.
    result: list[dict[str, Any]] = []
    for credit, su in rows:
        booked = su.booked_date
        if booked is None:
            continue
        credit_month = booked.strftime("%Y-%m") if hasattr(booked, "strftime") else str(booked)[:7]
        if credit_month not in _valid_months:
            continue
        result.append({
            "credit_id":       str(credit.id),
            "sales_unit_id":   str(su.id),
            "deal_id":         str(su.opportunity_id) if su.opportunity_id else None,
            "credit_type":     credit.credit_type,
            "credit_percent":  float(credit.credit_percent or 1.0),
            "credit_amount":   float(credit.credit_amount or 0.0),
            "booked_date":     booked.isoformat() if hasattr(booked, "isoformat") else str(booked),
        })
    return result


async def resolve_user_for_credit(
    db: AsyncSession,
    credit_user_id: str,
) -> Optional[UserProfile]:
    """Return UserProfile for a credit's user_id string."""
    try:
        uid = uuid.UUID(credit_user_id)
    except ValueError:
        return None
    return (await db.execute(select(UserProfile).where(UserProfile.id == uid))).scalars().first()


def apply_accelerators(
    credited_amount: float,
    quota: float,
    config: PayoutConfig,
    rules: list[Rule],
    trace: list[str],
) -> float:
    """Return accelerator commission on revenue above quota."""
    if quota <= 0 or credited_amount <= quota:
        return 0.0
    overage = credited_amount - quota
    # Check for explicit accelerator rules first
    for rule in rules:
        if rule.metric_name == "accelerator":
            rate = float(rule.rate or 0)
            acc = overage * rate
            trace.append(f"accelerator rule '{rule.name}': ${acc:,.2f} ({rate:.0%} × ${overage:,.2f} overage)")
            return round(acc, 2)
    # Fall back to config-level accelerator rate
    acc = overage * config.accelerator_rate
    trace.append(f"accelerator (config): ${acc:,.2f} ({config.accelerator_rate:.0%} × ${overage:,.2f} overage)")
    return round(acc, 2)


def apply_spiffs(
    attainment_pct: float,
    deals_won: int,
    win_rate: float,
    config: PayoutConfig,
    trace: list[str],
) -> float:
    """Evaluate SpiffRule conditions; return total SPIFF bonus."""
    total = 0.0
    for spiff in (config.spiff_rules or []):
        if not spiff.is_active:
            continue
        metric_val = {
            "attainment": attainment_pct,
            "deals_won":  float(deals_won),
            "win_rate":   win_rate * 100,
        }.get(spiff.trigger_metric, 0.0)
        if metric_val >= spiff.trigger_threshold:
            total += spiff.amount
            trace.append(f"SPIFF '{spiff.name}': ${spiff.amount:,.2f}")
    return round(total, 2)


def apply_clawbacks(
    payout_before_clawback: float,
    attainment_pct: float,
    deals_won: int,
    win_rate: float,
    config: PayoutConfig,
    trace: list[str],
) -> float:
    """Evaluate ClawbackRule conditions; return total clawback amount."""
    total = 0.0
    for cb in (config.clawback_rules or []):
        if not cb.is_active:
            continue
        metric_val = {
            "attainment": attainment_pct,
            "deals_won":  float(deals_won),
            "win_rate":   win_rate * 100,
        }.get(cb.trigger_metric, 0.0)
        if metric_val < cb.trigger_below:
            penalty = payout_before_clawback * cb.penalty_pct
            total += penalty
            trace.append(f"clawback '{cb.name}': -${penalty:,.2f} (metric={metric_val:.1f} < {cb.trigger_below})")
    return round(total, 2)


# ── Main compute function ─────────────────────────────────────────────────

async def compute_credit_payouts(
    db: AsyncSession,
    period: str,
    rep_id: Optional[str] = None,
    config: Optional[PayoutConfig] = None,
) -> list[CreditPayoutResult]:
    """
    Compute payouts at credit level (or rep-level fallback) for a period.

    Parameters
    ----------
    db     : async DB session
    period : "YYYY-MM" — must be a monthly period
    rep_id : optional — filter to single rep
    config : optional PayoutConfig override; defaults to DEFAULT_PAYOUT_CONFIG

    Returns
    -------
    List of CreditPayoutResult — one per rep (fallback mode) or per credit row.
    """
    cfg = config or DEFAULT_PAYOUT_CONFIG

    # Gather reps
    reps_q = select(Rep)
    if rep_id:
        try:
            reps_q = reps_q.where(Rep.id == uuid.UUID(rep_id))
        except ValueError:
            pass
    reps = (await db.execute(reps_q)).scalars().all()

    results: list[CreditPayoutResult] = []
    for rep in reps:
        # ── 1. Resolve plan/rules ────────────────────────────────────────
        plan_rules = await _get_plan_for_rep(db, rep, period)
        if plan_rules is None:
            results.append(await _rep_level_fallback(db, rep, period, cfg))
            continue
        plan, rules = plan_rules

        # ── 2. Try true SalesCredit path ────────────────────────────────
        credit_inputs = await get_credit_level_payout_inputs(db, rep, period)
        if credit_inputs:
            for ci in credit_inputs:
                trace: list[str] = [
                    f"SalesCredit: credit_type={ci['credit_type']}, "
                    f"credit_percent={ci['credit_percent']:.0%}, "
                    f"credited_amount=${ci['credit_amount']:,.2f}",
                ]
                credited = ci["credit_amount"]
                credit_pct = ci["credit_percent"]
                quota_val = float(
                    (await db.execute(
                        select(func.sum(Quota.amount))
                        .where(Quota.rep_id == rep.id)
                        .where(Quota.period == period)
                    )).scalar() or 0
                )
                # Scale quota by credit_percent so attainment is credit-proportional
                effective_quota = quota_val * credit_pct if quota_val > 0 else 0.0
                attainment = (credited / effective_quota * 100) if effective_quota > 0 else 0.0
                trace.append(f"attainment: {attainment:.2f}% (credited ${credited:,.2f} / quota ${effective_quota:,.2f})")

                if rules:
                    comp = _apply_commission_rules(credited, effective_quota, rules)
                    trace.extend(comp["rules_applied"])
                else:
                    from backend.models import Deal
                    won = int((await db.execute(
                        select(func.count(Deal.id)).where(Deal.rep_id == rep.id).where(Deal.stage == "Closed Won")
                    )).scalar() or 0)
                    lost = int((await db.execute(
                        select(func.count(Deal.id)).where(Deal.rep_id == rep.id).where(Deal.stage == "Closed Lost")
                    )).scalar() or 0)
                    eng_result = PayoutEngine(config=cfg).compute(credited, effective_quota, won, lost)
                    comp = {
                        "base_commission":    eng_result.get("base_commission", 0.0),
                        "accelerator_amount": eng_result.get("accelerator", 0.0),
                        "bonus_amount":       eng_result.get("bonus", 0.0),
                        "rules_applied":      eng_result.get("rules_applied", []),
                    }
                    trace.extend(comp["rules_applied"])

                acc = apply_accelerators(credited, effective_quota, cfg, rules, trace)
                spiff = apply_spiffs(attainment, 0, 0.0, cfg, trace)
                clawback = apply_clawbacks(
                    comp["base_commission"] + acc + spiff, attainment, 0, 0.0, cfg, trace
                )
                final_payout = round(
                    comp["base_commission"] + acc + spiff + comp.get("bonus_amount", 0.0) - clawback, 2
                )
                trace.append(
                    f"final_payout: ${final_payout:,.2f} = base ${comp['base_commission']:,.2f}"
                    f" + accel ${acc:,.2f} + spiff ${spiff:,.2f}"
                    f" + bonus ${comp.get('bonus_amount', 0.0):,.2f} - clawback ${clawback:,.2f}"
                )
                results.append(CreditPayoutResult(
                    rep_id=str(rep.id),
                    period=period,
                    sales_credit_id=ci["credit_id"],
                    source_deal_id=ci["deal_id"],
                    credited_amount=credited,
                    credit_percent=credit_pct,
                    quota=effective_quota,
                    attainment=round(attainment, 2),
                    base_commission=comp["base_commission"],
                    accelerator_amount=acc,
                    spiff_amount=spiff,
                    bonus_amount=comp.get("bonus_amount", 0.0),
                    clawback_amount=clawback,
                    final_payout=final_payout,
                    rules_applied=comp.get("rules_applied", []),
                    formula_trace=trace,
                    confidence="high" if rules else "medium",
                    fallback_mode="none",
                ))
            continue

        # ── 3. No SalesCredit rows → aggregate revenue fallback ──────────
        # Revenue is stored monthly; expand quarterly periods to month list.
        if "-Q" in str(period):
            try:
                _yr, _qn = str(period).split("-Q")
                _months = [f"{_yr}-{(int(_qn) - 1) * 3 + m:02d}" for m in range(1, 4)]
            except (ValueError, AttributeError):
                _months = []
            credited = float(
                (await db.execute(
                    select(func.sum(Revenue.amount))
                    .where(Revenue.rep_id == rep.id)
                    .where(Revenue.period.in_(_months))
                )).scalar() or 0
            )
        else:
            credited = float(
                (await db.execute(
                    select(func.sum(Revenue.amount))
                    .where(Revenue.rep_id == rep.id)
                    .where(Revenue.period == period)
                )).scalar() or 0
            )
        quota_val = float(
            (await db.execute(
                select(func.sum(Quota.amount))
                .where(Quota.rep_id == rep.id)
                .where(Quota.period == period)
            )).scalar() or 0
        )
        attainment = (credited / quota_val * 100) if quota_val > 0 else 0.0
        trace = [
            f"[revenue-aggregate fallback] credited=${credited:,.2f}, quota=${quota_val:,.2f}, attainment={attainment:.2f}%"
        ]
        if rules:
            comp = _apply_commission_rules(credited, quota_val, rules)
            trace.extend(comp["rules_applied"])
        else:
            from backend.models import Deal
            won = int((await db.execute(
                select(func.count(Deal.id)).where(Deal.rep_id == rep.id).where(Deal.stage == "Closed Won")
            )).scalar() or 0)
            lost = int((await db.execute(
                select(func.count(Deal.id)).where(Deal.rep_id == rep.id).where(Deal.stage == "Closed Lost")
            )).scalar() or 0)
            eng_result = PayoutEngine(config=cfg).compute(credited, quota_val, won, lost)
            comp = {
                "base_commission":    eng_result.get("base_commission", 0.0),
                "accelerator_amount": eng_result.get("accelerator", 0.0),
                "bonus_amount":       eng_result.get("bonus", 0.0),
                "rules_applied":      eng_result.get("rules_applied", []),
            }
            trace.extend(comp["rules_applied"])

        acc = apply_accelerators(credited, quota_val, cfg, rules, trace)
        spiff = apply_spiffs(attainment, 0, 0.0, cfg, trace)
        clawback = apply_clawbacks(comp["base_commission"] + acc + spiff, attainment, 0, 0.0, cfg, trace)
        final_payout = round(comp["base_commission"] + acc + spiff + comp.get("bonus_amount", 0.0) - clawback, 2)
        trace.append(
            f"final_payout: ${final_payout:,.2f} = base ${comp['base_commission']:,.2f}"
            f" + accel ${acc:,.2f} + spiff ${spiff:,.2f}"
            f" + bonus ${comp.get('bonus_amount', 0.0):,.2f} - clawback ${clawback:,.2f}"
        )
        results.append(CreditPayoutResult(
            rep_id=str(rep.id),
            period=period,
            credited_amount=credited,
            credit_percent=1.0,
            quota=quota_val,
            attainment=round(attainment, 2),
            base_commission=comp["base_commission"],
            accelerator_amount=acc,
            spiff_amount=spiff,
            bonus_amount=comp.get("bonus_amount", 0.0),
            clawback_amount=clawback,
            final_payout=final_payout,
            rules_applied=comp.get("rules_applied", []),
            formula_trace=trace,
            confidence="high" if rules else "medium",
            fallback_mode="no_credit_rows",
            warnings=["No SalesCredit rows found; using revenue aggregate."],
        ))

    return results


async def persist_payout_records(
    db: AsyncSession,
    payouts: list[CreditPayoutResult],
) -> int:
    """
    Persist CreditPayoutResult rows into the PayoutRecord table.
    Maps to existing schema columns; stores extended fields in source_system tag.
    Returns count of persisted records.
    """
    count = 0
    for p in payouts:
        # Resolve user_id from rep email (PayoutRecord FK targets users.id, not reps.id)
        rep_uuid = uuid.UUID(p.rep_id) if p.rep_id else None
        resolved_user_id: Optional[uuid.UUID] = None
        if rep_uuid:
            rep_row = (await db.execute(select(Rep).where(Rep.id == rep_uuid))).scalars().first()
            if rep_row:
                user_row = (await db.execute(
                    select(UserProfile).where(UserProfile.email == rep_row.email)
                )).scalars().first()
                if user_row:
                    resolved_user_id = user_row.id
        if resolved_user_id is None:
            # Cannot persist without a valid user FK — skip with warning
            continue
        record = PayoutRecordModel(
            user_id=resolved_user_id,
            period=p.period,
            payout_amount=p.final_payout,
            commission_rate=(p.base_commission / p.credited_amount) if p.credited_amount > 0 else None,
            fallback_used=(p.fallback_mode != "none"),
            confidence=min(1.0, max(0.0, {"high": 0.9, "medium": 0.7, "low": 0.4}.get(p.confidence, 0.7))),
            source_system="credit_payout_engine",
        )
        db.add(record)
        count += 1
    await db.flush()
    return count
