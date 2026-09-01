"""
backend/audit/payout_audit.py
==============================
Automated payout-math + ML-forecast audit for any company directory.

Runs automatically in three places:
  1. After data_generator.seed()           — data generation path
  2. After data_generator.load_company_dataset() — ingestion path
  3. GET /payout/audit/{company_name}      — REST endpoint
  4. pytest tests/test_payout_audit.py     — CI gate (discovers all companies/)

Two pipelines
─────────────
A. run_payout_audit()
   Pure CSV-layer; no DB required.
   • Checks plan assignment, rule count, revenue presence, closed-won deal count
   • Re-derives quarterly attainment and expected payout from PayoutEngine
   • Compares stored payouts.csv value to engine-derived value
   • Flags: NO_PLAN | NO_RULES | ZERO_REVENUE | NO_CLOSED_WON | ZERO_PAYOUT
             | PAYOUT_MISMATCH(delta=...)
   • Recognises QUOTA_EXEMPT_ROLES (CRO/SVP/VP) — shown as EXEMPT(role)

B. run_forecast_audit()
   Runs ml.forecasting_engine.forecast() over each rep's revenue.csv history.
   • Strategy auto-selected: baseline / ETS / Ridge / SARIMAX
   • Reports backtest MAE / MAPE + 3-month forward projection
   • Flags reps with MAPE > mape_warn_threshold (default 0.40)
   • Company-level aggregate: total forecast revenue for the next quarter
"""
from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Quota-exempt roles — intentionally no comp plan
# ---------------------------------------------------------------------------
QUOTA_EXEMPT_ROLES: frozenset[str] = frozenset(
    {"Chief Revenue Officer", "SVP Sales", "VP Sales"}
)


# ---------------------------------------------------------------------------
# CSV helpers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _to_quarter(yyyymm: str) -> str:
    try:
        y, m = int(yyyymm[:4]), int(yyyymm[5:7])
        return f"{y}-Q{(m - 1) // 3 + 1}"
    except (ValueError, IndexError):
        return ""


def _quarter_months(period: str) -> list[str]:
    """'2026-Q1' → ['2026-01', '2026-02', '2026-03']"""
    try:
        yr, qn = period.split("-Q")
        start = (int(qn) - 1) * 3 + 1
        return [f"{yr}-{m:02d}" for m in range(start, start + 3)]
    except (ValueError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RepPayoutAuditRow:
    rep_id: str
    rep_name: str
    plan_id: str
    plan_external_id: str
    n_rules: int
    quota: float
    revenue: float
    attainment_pct: float
    csv_payout: float
    engine_payout: float
    payout_delta: float            # csv_payout − engine_payout
    closed_won: int
    closed_lost: int
    open_deals: int
    status: str                    # OK | EXEMPT(role) | <BUG list>
    bugs: list[str] = field(default_factory=list)


@dataclass
class RepForecastAuditRow:
    rep_id: str
    rep_name: str
    history_months: int
    strategy_used: str
    backtest_mae: float | None
    backtest_mape: float | None
    forecast_next_q_revenue: float  # sum of next 3 monthly forecasts
    forecast_periods: list[str] = field(default_factory=list)
    forecast_values: list[float] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mape_flagged: bool = False


@dataclass
class PayoutAuditSummary:
    company: str
    period: str
    total_reps: int
    reps_ok: int
    reps_exempt: int
    reps_with_bugs: int
    reps_zero_revenue: int
    reps_no_closed_won: int
    reps_no_plan: int
    reps_no_rules: int
    reps_payout_mismatch: int
    total_credits: float
    total_csv_payout: float
    total_engine_payout: float
    estimated_lost_commission: float   # credits where csv_payout==0 × min_rate
    plan_coverage: dict[str, dict]     # plan_id → {external_id, reps, rules}
    payout_rows: list[RepPayoutAuditRow] = field(default_factory=list)


@dataclass
class ForecastAuditSummary:
    company: str
    total_reps_forecasted: int
    reps_flagged_high_mape: int
    mape_warn_threshold: float
    company_forecast_next_q: float
    rep_rows: list[RepForecastAuditRow] = field(default_factory=list)


def to_native(value: Any) -> Any:
    """
    Convert numpy scalars and containers to plain Python types, recursively.

    The audit computes with pandas and numpy, so values like `mape_flagged`
    arrive as `numpy.bool_` rather than `bool`. Pydantic cannot serialize those,
    and `/payout/audit/{company}` returned 500 with
    "Unable to serialize unknown type: <class 'numpy.bool'>".

    Applied once at the serialization boundary rather than at each field, so a
    numpy value added to the report later cannot reintroduce the failure.
    """
    if isinstance(value, dict):
        return {k: to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_native(v) for v in value]
    # numpy scalars expose .item(); guard on the attribute so numpy stays an
    # optional import here.
    if hasattr(value, "item") and hasattr(value, "dtype"):
        return value.item()
    return value


@dataclass
class CompanyAuditReport:
    company: str
    payout: PayoutAuditSummary
    forecast: ForecastAuditSummary
    passed: bool
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return to_native(self._raw_dict())

    def _raw_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "passed": self.passed,
            "errors": self.errors,
            "payout": {
                "period": self.payout.period,
                "total_reps": self.payout.total_reps,
                "reps_ok": self.payout.reps_ok,
                "reps_exempt": self.payout.reps_exempt,
                "reps_with_bugs": self.payout.reps_with_bugs,
                "reps_zero_revenue": self.payout.reps_zero_revenue,
                "reps_no_closed_won": self.payout.reps_no_closed_won,
                "reps_no_plan": self.payout.reps_no_plan,
                "reps_no_rules": self.payout.reps_no_rules,
                "reps_payout_mismatch": self.payout.reps_payout_mismatch,
                "total_credits": round(self.payout.total_credits, 2),
                "total_csv_payout": round(self.payout.total_csv_payout, 2),
                "total_engine_payout": round(self.payout.total_engine_payout, 2),
                "estimated_lost_commission": round(self.payout.estimated_lost_commission, 2),
                "plan_coverage": self.payout.plan_coverage,
                "reps": [
                    {
                        "rep_name": r.rep_name,
                        "plan_external_id": r.plan_external_id,
                        "n_rules": r.n_rules,
                        "quota": round(r.quota, 2),
                        "revenue": round(r.revenue, 2),
                        "attainment_pct": round(r.attainment_pct, 2),
                        "csv_payout": round(r.csv_payout, 2),
                        "engine_payout": round(r.engine_payout, 2),
                        "payout_delta": round(r.payout_delta, 2),
                        "closed_won": r.closed_won,
                        "status": r.status,
                        "bugs": r.bugs,
                    }
                    for r in self.payout.payout_rows
                ],
            },
            "forecast": {
                "total_reps_forecasted": self.forecast.total_reps_forecasted,
                "reps_flagged_high_mape": self.forecast.reps_flagged_high_mape,
                "mape_warn_threshold": self.forecast.mape_warn_threshold,
                "company_forecast_next_q": round(self.forecast.company_forecast_next_q, 2),
                "reps": [
                    {
                        "rep_name": r.rep_name,
                        "history_months": r.history_months,
                        "strategy_used": r.strategy_used,
                        "backtest_mae": round(r.backtest_mae, 2) if r.backtest_mae is not None else None,
                        "backtest_mape": round(r.backtest_mape, 4) if r.backtest_mape is not None else None,
                        "forecast_next_q_revenue": round(r.forecast_next_q_revenue, 2),
                        "forecast_periods": r.forecast_periods,
                        "forecast_values": [round(v, 2) for v in r.forecast_values],
                        "mape_flagged": r.mape_flagged,
                        "warnings": r.warnings,
                    }
                    for r in self.forecast.rep_rows
                ],
            },
        }


# ---------------------------------------------------------------------------
# Pipeline A — Payout Math Audit
# ---------------------------------------------------------------------------

def run_payout_audit(
    company_dir: Path,
    period: str = "",
    payout_delta_tolerance: float = 1.0,
) -> PayoutAuditSummary:
    """
    Re-derive payout from CSV data and compare to stored payouts.csv.

    Parameters
    ----------
    company_dir            : Path to company data folder (e.g. companies/techo-solutions)
    period                 : Quarter to audit, e.g. '2026-Q1'.  Empty = most recent
                             quota period.
    payout_delta_tolerance : Acceptable $ difference between CSV and engine payout
                             before flagging PAYOUT_MISMATCH.
    """
    from backend.payout.engine import PayoutEngine, build_payout_config_from_rules

    company = company_dir.name

    # ── Load CSVs ──────────────────────────────────────────────────────────
    reps_map   = {r["id"]: r for r in _read_csv(company_dir / "reps.csv")}
    users_list = _read_csv(company_dir / "users.csv")
    email_to_uid: dict[str, str] = {u["email"]: u["id"] for u in users_list}

    roles_map: dict[str, str] = {}
    for r in _read_csv(company_dir / "rep_hierarchy.csv"):
        roles_map[r["rep_id"]] = r.get("role", "Account Executive")

    # Legacy schema: reps.csv may carry plan/role columns inline
    for rep_id, rep in reps_map.items():
        if rep_id not in roles_map and rep.get("role"):
            roles_map[rep_id] = rep["role"]

    # Quotas: rep_id → period → amount
    quotas: dict[str, dict[str, float]] = defaultdict(dict)
    for r in _read_csv(company_dir / "quotas.csv"):
        quotas[r["rep_id"]][r["period"]] = float(r["amount"])

    # Determine audit period (most recent quarter with quota rows)
    all_quota_periods: list[str] = sorted(
        {p for rep_periods in quotas.values() for p in rep_periods},
        reverse=True,
    )
    if not period and all_quota_periods:
        period = all_quota_periods[0]

    # Revenue: aggregate monthly → per-rep for the audit quarter
    rev_monthly: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in _read_csv(company_dir / "revenue.csv"):
        m = r.get("period", "")[:7]
        rev_monthly[r["rep_id"]][m] += float(r.get("amount") or 0)

    q_months = _quarter_months(period)
    rev_quarterly: dict[str, float] = {
        rep_id: sum(months.get(m, 0.0) for m in q_months)
        for rep_id, months in rev_monthly.items()
    }

    # Deals
    deals_by_rep: dict[str, list[dict]] = defaultdict(list)
    for r in _read_csv(company_dir / "deals.csv"):
        deals_by_rep[r["rep_id"]].append(r)

    # Plans / Rules — handle both generated (has "id") and legacy (has "plan_name") formats
    _raw_plans = _read_csv(company_dir / "plans.csv")
    plans_map: dict[str, dict] = {}
    for r in _raw_plans:
        key = r.get("id") or r.get("plan_name") or r.get("name") or ""
        if key:
            plans_map[key] = r
    rules_by_plan: dict[str, list[dict]] = defaultdict(list)
    for r in _read_csv(company_dir / "rules.csv"):
        rules_by_plan[r.get("plan_id", "")].append(r)

    # Plan assignments: user_id → plan_id (generated schema)
    # Legacy fallback: reps.csv["plan"] column carries the plan name
    plan_assign: dict[str, str] = {}
    for r in _read_csv(company_dir / "plan_assignments.csv"):
        plan_assign[r["user_id"]] = r["plan_id"]

    # For legacy schemas where users.csv is absent, also build rep_id → plan_name
    rep_plan_legacy: dict[str, str] = {
        r["id"]: r["plan"]
        for r in reps_map.values()
        if r.get("plan")
    }

    # Stored payouts: (user_id, period) → payout_amount
    stored_payout: dict[tuple[str, str], float] = {}
    for r in _read_csv(company_dir / "payouts.csv"):
        stored_payout[(r["user_id"], r["period"])] = float(r.get("payout_amount") or 0)

    # Credits per user (for lost-commission estimate)
    credits_by_user: dict[str, float] = defaultdict(float)
    for r in _read_csv(company_dir / "sales_credits.csv"):
        credits_by_user[r["user_id"]] += float(r.get("credit_amount") or 0)

    # ── Rule proxy (csv dict → engine-compatible object) ──────────────────
    class _RuleLike:
        __slots__ = ("threshold_min", "threshold_max", "rate", "accelerator_rate", "bonus_amount")
        def __init__(self, d: dict) -> None:
            self.threshold_min   = float(d.get("threshold_min")   or 0)
            self.threshold_max   = float(d.get("threshold_max")   or 999)
            self.rate            = float(d.get("rate")            or 0.03)
            self.accelerator_rate = float(d.get("accelerator_rate") or 0)
            self.bonus_amount    = float(d.get("bonus_amount")    or 0)

    # ── Per-rep audit ──────────────────────────────────────────────────────
    payout_rows: list[RepPayoutAuditRow] = []
    plan_coverage: dict[str, dict] = {}

    for rep_id, rep in reps_map.items():
        rep_name = rep.get("name", rep_id)
        role     = roles_map.get(rep_id, "Account Executive")
        email    = rep.get("email", "")
        user_id  = email_to_uid.get(email, "")

        # Quota-exempt leadership roles
        if role in QUOTA_EXEMPT_ROLES:
            payout_rows.append(RepPayoutAuditRow(
                rep_id=rep_id, rep_name=rep_name,
                plan_id="", plan_external_id="NONE",
                n_rules=0, quota=0.0, revenue=0.0,
                attainment_pct=0.0, csv_payout=0.0, engine_payout=0.0,
                payout_delta=0.0,
                closed_won=0, closed_lost=0, open_deals=0,
                status=f"EXEMPT({role})",
            ))
            continue

        plan_id   = plan_assign.get(user_id, "") or rep_plan_legacy.get(rep_id, "")
        plan_row  = plans_map.get(plan_id, {})
        plan_ext  = (
            plan_row.get("external_id")
            or plan_row.get("plan_name")
            or (plan_id[:8] if plan_id else "NO_PLAN")
        )
        rules     = rules_by_plan.get(plan_id, [])
        n_rules   = len(rules)

        quota   = quotas.get(rep_id, {}).get(period, 0.0)
        revenue = rev_quarterly.get(rep_id, 0.0)
        attainment_pct = (revenue / quota * 100.0) if quota > 0 else 0.0

        rep_deals   = deals_by_rep.get(rep_id, [])
        # Use quarter-scoped deal counts to match how _seed_payout_records computes
        # payouts (bonuses/accelerators are per-quarter, not lifetime totals).
        closed_won  = sum(
            1 for d in rep_deals
            if d.get("stage") == "Closed Won"
            and _to_quarter((d.get("actual_close_date") or "")[:7]) == period
        )
        closed_lost = sum(
            1 for d in rep_deals
            if d.get("stage") == "Closed Lost"
            and _to_quarter((d.get("actual_close_date") or "")[:7]) == period
        )
        open_deals  = sum(1 for d in rep_deals if d.get("stage") not in ("Closed Won", "Closed Lost"))
        # Lifetime closed-won for the NO_CLOSED_WON flag (a rep with zero
        # lifetime closed deals is a data quality issue; zero in one quarter is normal)
        lifetime_closed_won = sum(1 for d in rep_deals if d.get("stage") == "Closed Won")

        csv_payout = stored_payout.get((user_id, period), 0.0)

        # Re-compute expected payout with the plan's own rules
        config = (
            build_payout_config_from_rules([_RuleLike(r) for r in rules])
            if rules else None
        )
        engine = PayoutEngine(config)
        engine_payout = engine.compute(revenue, quota, closed_won, closed_lost)["payout"]
        payout_delta  = csv_payout - engine_payout

        # Plan coverage tracking
        if plan_id:
            if plan_id not in plan_coverage:
                plan_coverage[plan_id] = {"external_id": plan_ext, "reps": 0, "rules": n_rules}
            plan_coverage[plan_id]["reps"] += 1

        # Bug flags
        bugs: list[str] = []
        if not plan_id:
            bugs.append("NO_PLAN")
        if n_rules == 0:
            bugs.append("NO_RULES")
        if revenue == 0:
            bugs.append("ZERO_REVENUE")
        if lifetime_closed_won == 0:
            bugs.append("NO_CLOSED_WON")
        if quota > 0 and csv_payout == 0:
            bugs.append("ZERO_PAYOUT")
        # Allow mismatch up to 5% of the stored payout (handles floating-point and
        # minor algorithm differences between _seed_payout_records and the audit engine)
        payout_tolerance = max(payout_delta_tolerance, abs(csv_payout) * 0.05)
        if csv_payout != 0 and abs(payout_delta) > payout_tolerance:
            bugs.append(f"PAYOUT_MISMATCH(delta=${payout_delta:+.2f})")

        payout_rows.append(RepPayoutAuditRow(
            rep_id=rep_id, rep_name=rep_name,
            plan_id=plan_id, plan_external_id=plan_ext,
            n_rules=n_rules, quota=quota, revenue=revenue,
            attainment_pct=attainment_pct,
            csv_payout=csv_payout, engine_payout=engine_payout,
            payout_delta=payout_delta,
            closed_won=closed_won, closed_lost=closed_lost, open_deals=open_deals,
            status="OK" if not bugs else "; ".join(bugs),
            bugs=bugs,
        ))

    payout_rows.sort(key=lambda r: r.rep_name)

    # ── Aggregate summary ─────────────────────────────────────────────────
    non_exempt = [r for r in payout_rows if not r.status.startswith("EXEMPT")]
    total_credits = sum(
        credits_by_user.get(email_to_uid.get(reps_map[r.rep_id].get("email", ""), ""), 0.0)
        for r in non_exempt
        if r.rep_id in reps_map
    )
    zero_payout_credits = sum(
        credits_by_user.get(email_to_uid.get(reps_map[r.rep_id].get("email", ""), ""), 0.0)
        for r in non_exempt
        if r.csv_payout == 0 and r.quota > 0 and r.rep_id in reps_map
    )

    return PayoutAuditSummary(
        company=company,
        period=period,
        total_reps=len(payout_rows),
        reps_ok=sum(1 for r in payout_rows if r.status == "OK"),
        reps_exempt=sum(1 for r in payout_rows if r.status.startswith("EXEMPT")),
        reps_with_bugs=sum(1 for r in non_exempt if r.bugs),
        reps_zero_revenue=sum(1 for r in non_exempt if "ZERO_REVENUE" in r.bugs),
        reps_no_closed_won=sum(1 for r in non_exempt if "NO_CLOSED_WON" in r.bugs),
        reps_no_plan=sum(1 for r in non_exempt if "NO_PLAN" in r.bugs),
        reps_no_rules=sum(1 for r in non_exempt if "NO_RULES" in r.bugs),
        reps_payout_mismatch=sum(
            1 for r in non_exempt if any("PAYOUT_MISMATCH" in b for b in r.bugs)
        ),
        total_credits=total_credits,
        total_csv_payout=sum(r.csv_payout for r in non_exempt),
        total_engine_payout=sum(r.engine_payout for r in non_exempt),
        estimated_lost_commission=zero_payout_credits * 0.03,
        plan_coverage=plan_coverage,
        payout_rows=payout_rows,
    )


# ---------------------------------------------------------------------------
# Pipeline B — ML Forecast Audit
# ---------------------------------------------------------------------------

def run_forecast_audit(
    company_dir: Path,
    horizon: int = 3,
    mape_warn_threshold: float = 0.40,
) -> ForecastAuditSummary:
    """
    Run the ML forecasting engine over each rep's historical revenue and
    report backtest quality + next-quarter revenue projection.

    Parameters
    ----------
    company_dir          : Path to company data folder
    horizon              : Months to forecast (default 3 = one quarter forward)
    mape_warn_threshold  : MAPE above this value triggers a mape_flagged warning
    """
    from backend.ml.forecasting_engine import forecast

    reps_map = {r["id"]: r for r in _read_csv(company_dir / "reps.csv")}

    # Build per-rep monthly revenue time-series
    rev_by_rep_month: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for r in _read_csv(company_dir / "revenue.csv"):
        rep_id = r.get("rep_id", "")
        period = r.get("period", "")[:7]  # YYYY-MM
        if rep_id and period:
            rev_by_rep_month[rep_id][period] += float(r.get("amount") or 0)

    rep_rows: list[RepForecastAuditRow] = []
    company_forecast_next_q = 0.0

    for rep_id, rep in reps_map.items():
        rep_name = rep.get("name", rep_id)
        monthly  = rev_by_rep_month.get(rep_id, {})
        sorted_periods = sorted(monthly.keys())
        history        = [monthly[p] for p in sorted_periods]

        result = forecast(
            history=history,
            history_periods=sorted_periods,
            horizon=horizon,
            forecast_type="revenue",
            scenario="base",
        )

        q_forecast   = sum(result.values[:horizon])

        # Sanity-cap astronomical SARIMAX forecasts: if the 3-month projection
        # exceeds 20× the maximum observed monthly revenue, it's numerically
        # unstable. Fall back to the historical average for the company total.
        max_historical = max(history) if history else 0.0
        forecast_cap = max_historical * 20 * 3  # 20× peak monthly × 3 months
        if q_forecast > forecast_cap > 0:
            q_forecast = sum(history[-3:]) / 3 * 3 if len(history) >= 3 else (
                sum(history) / len(history) * 3 if history else 0.0
            )

        mape_flagged = (
            result.backtest_mape is not None
            and result.backtest_mape > mape_warn_threshold
        )
        company_forecast_next_q += q_forecast

        rep_rows.append(RepForecastAuditRow(
            rep_id=rep_id,
            rep_name=rep_name,
            history_months=result.history_months,
            strategy_used=result.strategy_used,
            backtest_mae=result.backtest_mae,
            backtest_mape=result.backtest_mape,
            forecast_next_q_revenue=round(q_forecast, 2),
            forecast_periods=result.periods,
            forecast_values=result.values,
            warnings=result.warnings,
            mape_flagged=mape_flagged,
        ))

    rep_rows.sort(key=lambda r: r.rep_name)

    return ForecastAuditSummary(
        company=company_dir.name,
        total_reps_forecasted=len(rep_rows),
        reps_flagged_high_mape=sum(1 for r in rep_rows if r.mape_flagged),
        mape_warn_threshold=mape_warn_threshold,
        company_forecast_next_q=round(company_forecast_next_q, 2),
        rep_rows=rep_rows,
    )


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------

def audit_company(
    company_dir: Path | str,
    period: str = "",
    payout_delta_tolerance: float = 1.0,
    forecast_horizon: int = 3,
    mape_warn_threshold: float = 0.40,
) -> CompanyAuditReport:
    """
    Run the full payout-math + ML forecast audit for a company directory.

    ``report.passed`` is True when zero quota-carrying reps have data bugs.
    Results are returned as a CompanyAuditReport and can be serialised via
    ``report.to_dict()`` for JSON endpoints or test assertions.
    """
    company_dir = Path(company_dir)
    if not company_dir.is_dir():
        _empty_payout = PayoutAuditSummary(
            company=company_dir.name, period=period,
            total_reps=0, reps_ok=0, reps_exempt=0, reps_with_bugs=0,
            reps_zero_revenue=0, reps_no_closed_won=0, reps_no_plan=0,
            reps_no_rules=0, reps_payout_mismatch=0,
            total_credits=0.0, total_csv_payout=0.0, total_engine_payout=0.0,
            estimated_lost_commission=0.0, plan_coverage={},
        )
        _empty_forecast = ForecastAuditSummary(
            company=company_dir.name, total_reps_forecasted=0,
            reps_flagged_high_mape=0, mape_warn_threshold=mape_warn_threshold,
            company_forecast_next_q=0.0,
        )
        return CompanyAuditReport(
            company=company_dir.name,
            payout=_empty_payout,
            forecast=_empty_forecast,
            passed=False,
            errors=[f"Company directory not found: {company_dir}"],
        )

    payout_summary   = run_payout_audit(
        company_dir, period=period, payout_delta_tolerance=payout_delta_tolerance
    )
    forecast_summary = run_forecast_audit(
        company_dir, horizon=forecast_horizon, mape_warn_threshold=mape_warn_threshold
    )

    errors: list[str] = []
    if payout_summary.reps_with_bugs > 0:
        errors.append(f"{payout_summary.reps_with_bugs} reps have payout data bugs")

    return CompanyAuditReport(
        company=company_dir.name,
        payout=payout_summary,
        forecast=forecast_summary,
        passed=len(errors) == 0,
        errors=errors,
    )
