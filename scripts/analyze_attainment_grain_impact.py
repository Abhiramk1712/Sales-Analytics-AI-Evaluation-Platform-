#!/usr/bin/env python3
"""
scripts/analyze_attainment_grain_impact.py
==========================================
Quantify what the attainment-grain defect cost, using a company's own data.

The defect: commission tiers were evaluated against each SalesCredit in
isolation rather than against the rep's cumulative attainment for the period.
A rep whose individual credits all land in a low tier is paid at that tier
even when their period total clears a much higher one.

This reads a company folder directly — no database, no API — and computes each
rep's period payout twice: once per credit (the defect) and once cumulatively
(the fix). The difference is real money for real rows, which is what makes it
worth putting in a report rather than describing in the abstract.

    python -m scripts.analyze_attainment_grain_impact --company techo-solutions
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


# ── Loading ──────────────────────────────────────────────────────────────────

def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def period_of(booked_date: str) -> str | None:
    """Map a booked date to the quarter key quotas are stored under."""
    date = (booked_date or "").strip()[:10]
    if len(date) < 7:
        return None
    year, month = date[:4], int(date[5:7])
    return f"{year}-Q{(month - 1) // 3 + 1}"


# ── Commission rules ─────────────────────────────────────────────────────────

def commission(credited: float, quota: float, rules: list[dict[str, str]]) -> float:
    """
    Apply attainment tiers to a credited amount, mirroring the engine's
    corrected convention: bands are inclusive at the lower bound and exclusive
    at the upper, and a missing upper bound is open-ended.
    """
    attainment = (credited / quota * 100) if quota > 0 else 0.0
    total = 0.0

    for rule in rules:
        if rule.get("metric_name") != "attainment_pct":
            continue
        low = to_float(rule.get("threshold_min"), 0.0)
        raw_high = str(rule.get("threshold_max") or "").strip()
        high = to_float(raw_high, math.inf) if raw_high else math.inf
        if low <= attainment < high:
            total += credited * to_float(rule.get("rate"))
            total += to_float(rule.get("bonus_amount"))
    return total


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyse(company_dir: Path, period: str | None) -> dict[str, Any]:
    users = {u["id"]: u for u in read_csv(company_dir / "users.csv")}
    reps_by_email = {r["email"]: r for r in read_csv(company_dir / "reps.csv")}
    units = {u["id"]: u for u in read_csv(company_dir / "sales_units.csv")}
    credits = read_csv(company_dir / "sales_credits.csv")
    quotas = read_csv(company_dir / "quotas.csv")
    assignments = read_csv(company_dir / "plan_assignments.csv")
    rules_all = read_csv(company_dir / "rules.csv")

    rules_by_plan: dict[str, list[dict[str, str]]] = defaultdict(list)
    for rule in rules_all:
        rules_by_plan[rule["plan_id"]].append(rule)

    plan_of_user = {a["user_id"]: a["plan_id"] for a in assignments}

    quota_of: dict[tuple[str, str], float] = {}
    for q in quotas:
        quota_of[(q["rep_id"], q["period"])] = (
            quota_of.get((q["rep_id"], q["period"]), 0.0) + to_float(q["amount"])
        )

    # Group credits by (user, period).
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for credit in credits:
        unit = units.get(credit["sales_unit_id"])
        if not unit:
            continue
        credit_period = period_of(unit.get("booked_date", ""))
        if not credit_period or (period and credit_period != period):
            continue
        grouped[(credit["user_id"], credit_period)].append((
            to_float(credit["credit_amount"]),
            to_float(credit.get("credit_percent"), 1.0),
        ))

    rows: list[dict[str, Any]] = []
    for (user_id, credit_period), amounts in sorted(grouped.items()):
        user = users.get(user_id)
        if not user:
            continue
        rep = reps_by_email.get(user.get("email", ""))
        if not rep:
            continue

        quota = quota_of.get((rep["id"], credit_period), 0.0)
        if quota <= 0:
            continue

        plan_id = plan_of_user.get(user_id)
        rules = rules_by_plan.get(plan_id, [])
        if not rules:
            continue

        total_credited = sum(a for a, _ in amounts)

        # The defect, exactly as the engine had it: each credit judged alone
        # against the full period quota scaled by that credit's *deal-level*
        # split percentage — not by its share of the rep's period total. Two
        # things go wrong at once. Tiers are reached against one deal instead
        # of the period, and any flat bonus attached to a tier is paid once per
        # credit rather than once per period.
        per_credit_total = 0.0
        for amount, credit_pct in amounts:
            effective_quota = quota * credit_pct if quota > 0 else 0.0
            per_credit_total += commission(amount, effective_quota, rules)

        cumulative_total = commission(total_credited, quota, rules)

        rows.append({
            "rep": rep["name"],
            "period": credit_period,
            "credits": len(amounts),
            "attainment_spread": round(
                max((a / (quota * p) * 100) if quota * p > 0 else 0 for a, p in amounts)
                - min((a / (quota * p) * 100) if quota * p > 0 else 0 for a, p in amounts), 1),
            "credited": round(total_credited, 2),
            "quota": round(quota, 2),
            "attainment_pct": round(total_credited / quota * 100, 1),
            "paid_per_credit": round(per_credit_total, 2),
            "paid_cumulative": round(cumulative_total, 2),
            "delta": round(cumulative_total - per_credit_total, 2),
        })

    underpaid = [r for r in rows if r["delta"] > 0.01]
    overpaid = [r for r in rows if r["delta"] < -0.01]
    # Rows the defect paid nothing at all: per-credit attainment overshot the
    # top tier's upper bound, so no band matched and the commission was zero.
    zero_paid = [r for r in rows if r["paid_per_credit"] == 0 and r["paid_cumulative"] > 0]

    return {
        "company": company_dir.name,
        "period": period or "all",
        "rep_periods_examined": len(rows),
        "rep_periods_affected": len(underpaid) + len(overpaid),
        "underpaid_count": len(underpaid),
        "overpaid_count": len(overpaid),
        "total_paid_per_credit": round(sum(r["paid_per_credit"] for r in rows), 2),
        "total_paid_cumulative": round(sum(r["paid_cumulative"] for r in rows), 2),
        "total_delta": round(sum(r["delta"] for r in rows), 2),
        # Net is misleading on its own: overpayments cancel underpayments while
        # both are wrong. Gross is the size of the error actually made.
        "gross_error": round(sum(abs(r["delta"]) for r in rows), 2),
        "zero_paid_count": len(zero_paid),
        "zero_paid_value": round(sum(r["paid_cumulative"] for r in zero_paid), 2),
        "largest_underpayments": sorted(underpaid, key=lambda r: -r["delta"])[:10],
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company", default="techo-solutions")
    parser.add_argument("--base-dir", default="companies")
    parser.add_argument("--period", default=None, help="e.g. 2026-Q2; omit for all periods")
    parser.add_argument("--json", action="store_true", help="emit the full result as JSON")
    args = parser.parse_args()

    company_dir = Path(args.base_dir) / args.company
    if not company_dir.is_dir():
        raise SystemExit(f"Company folder not found: {company_dir}")

    result = analyse(company_dir, args.period)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"\nAttainment-grain impact — {result['company']} ({result['period']})")
    print("=" * 72)
    print(f"  rep-periods examined     {result['rep_periods_examined']}")
    print(f"  rep-periods affected     {result['rep_periods_affected']}")
    print(f"    underpaid              {result['underpaid_count']}")
    print(f"    overpaid               {result['overpaid_count']}")
    print(f"  commission, per-credit   ${result['total_paid_per_credit']:,.2f}")
    print(f"  commission, cumulative   ${result['total_paid_cumulative']:,.2f}")
    print(f"  net difference           ${result['total_delta']:,.2f}")
    print(f"  gross error              ${result['gross_error']:,.2f}")
    print(f"  paid $0 despite earning  {result['zero_paid_count']} rep-periods"
          f"  (${result['zero_paid_value']:,.2f} owed)")

    if result["largest_underpayments"]:
        print("\n  Largest underpayments")
        print(f"  {'rep':<22}{'period':<10}{'cr':>4}{'attain':>9}{'was':>13}{'should be':>13}{'delta':>12}")
        for row in result["largest_underpayments"]:
            print(
                f"  {row['rep']:<22}{row['period']:<10}{row['credits']:>4}"
                f"{row['attainment_pct']:>8.1f}%"
                f"{row['paid_per_credit']:>13,.2f}{row['paid_cumulative']:>13,.2f}"
                f"{row['delta']:>12,.2f}"
            )
    print()


if __name__ == "__main__":
    main()
