"""
backend/validation/revops_rules.py
====================================
RevOps business-rule validation layer.

Rules fall into two severity classes:
  - HARD_FAIL : blocks ingestion if violated
  - WARN      : recorded in audit log but ingestion continues

Five hard-fail rules (as designed in the RevOps plan):
  1. quota_vs_revenue     – quota must not exceed 20× annual revenue
  2. stage_progression    – closed deals must have an actual_close_date
  3. date_order           – actual_close_date must be >= created_at
  4. negative_arr         – open deal amounts must not be < 0
  5. activity_coverage    – at least 50% of open deals must have ≥ 1 activity

Additional WARN rules:
  - rep_without_quota     – every active rep should have quota records
  - empty_revenue_type    – revenue rows should declare a revenue_type
  - ramp_mismatch         – rep_ramp.csv ramp_factor should equal RAMP_SCHEDULE value
    - manager_span          – managers should not exceed practical direct-report span
    - role_mix              – generated org should include leadership + IC role coverage
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import csv
from datetime import date
from collections import Counter


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────

@dataclass
class RuleViolation:
    rule: str
    severity: str          # "HARD_FAIL" | "WARN"
    message: str
    row_count: int = 0


@dataclass
class RevOpsValidationResult:
    passed: bool
    violations: list[RuleViolation] = field(default_factory=list)
    warnings: list[RuleViolation] = field(default_factory=list)

    def summary(self) -> dict:
        return {
            "passed": self.passed,
            "hard_fail_count": len(self.violations),
            "warn_count": len(self.warnings),
            "violations": [
                {"rule": v.rule, "severity": v.severity, "message": v.message, "row_count": v.row_count}
                for v in self.violations
            ],
            "warnings": [
                {"rule": v.rule, "severity": v.severity, "message": v.message, "row_count": v.row_count}
                for v in self.warnings
            ],
        }


# ─────────────────────────────────────────────────────────────
# CSV helpers
# ─────────────────────────────────────────────────────────────

def _load(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ─────────────────────────────────────────────────────────────
# Rule implementations
# ─────────────────────────────────────────────────────────────

def _rule_quota_vs_revenue(
    quota_rows: list[dict],
    revenue_rows: list[dict],
    max_ratio: float = 20.0,
) -> RuleViolation | None:
    """Quota total must not exceed configured max ratio vs annual revenue total."""
    total_quota = sum(float(r.get("amount", 0) or 0) for r in quota_rows)
    total_revenue = sum(float(r.get("amount", 0) or 0) for r in revenue_rows)
    if total_revenue <= 0:
        return None  # can't validate without revenue
    ratio = total_quota / total_revenue
    if ratio > max_ratio:
        return RuleViolation(
            rule="quota_vs_revenue",
            severity="HARD_FAIL",
            message=(
                f"Total quota (${total_quota:,.0f}) is {ratio:.1f}× total revenue "
                f"(${total_revenue:,.0f}). Maximum allowed ratio is {max_ratio:g}×. "
                "This indicates quota was not set using a revenue-proportional formula."
            ),
            row_count=len(quota_rows),
        )
    return None


def _rule_stage_progression(deal_rows: list[dict]) -> RuleViolation | None:
    """Closed deals must have an actual_close_date."""
    CLOSED = {"Closed Won", "Closed Lost"}
    bad = [
        r for r in deal_rows
        if r.get("stage") in CLOSED and not r.get("actual_close_date", "").strip()
    ]
    if bad:
        return RuleViolation(
            rule="stage_progression",
            severity="HARD_FAIL",
            message=f"{len(bad)} closed deals are missing actual_close_date.",
            row_count=len(bad),
        )
    return None


def _rule_date_order(deal_rows: list[dict]) -> RuleViolation | None:
    """actual_close_date must be >= created_at (date part)."""
    bad = 0
    for r in deal_rows:
        close_raw = r.get("actual_close_date", "")
        created_raw = r.get("created_at", "")
        if not close_raw or not created_raw:
            continue
        try:
            close_d = date.fromisoformat(close_raw[:10])
            created_d = date.fromisoformat(created_raw[:10])
            if close_d < created_d:
                bad += 1
        except ValueError:
            bad += 1
    if bad:
        return RuleViolation(
            rule="date_order",
            severity="HARD_FAIL",
            message=f"{bad} deals have actual_close_date earlier than created_at.",
            row_count=bad,
        )
    return None


def _rule_negative_arr(deal_rows: list[dict]) -> RuleViolation | None:
    """Open deal amounts must not be negative."""
    OPEN_STAGES = {"Prospecting", "Qualification", "Proposal", "Negotiation"}
    bad = [
        r for r in deal_rows
        if r.get("stage") in OPEN_STAGES and float(r.get("amount", 0) or 0) < 0
    ]
    if bad:
        return RuleViolation(
            rule="negative_arr",
            severity="HARD_FAIL",
            message=f"{len(bad)} open deals have negative amount values.",
            row_count=len(bad),
        )
    return None


def _rule_activity_coverage(
    deal_rows: list[dict],
    activity_rows: list[dict],
    min_coverage_pct: float = 50.0,
) -> RuleViolation | None:
    """At least configured percentage of open deals must have ≥ 1 activity."""
    OPEN_STAGES = {"Prospecting", "Qualification", "Proposal", "Negotiation"}
    open_deal_ids = {r["id"] for r in deal_rows if r.get("stage") in OPEN_STAGES and r.get("id")}
    if not open_deal_ids:
        return None
    deal_ids_with_activity = {r.get("deal_id", "") for r in activity_rows}
    covered = len(open_deal_ids & deal_ids_with_activity)
    coverage_pct = covered / len(open_deal_ids) * 100
    if coverage_pct < min_coverage_pct:
        return RuleViolation(
            rule="activity_coverage",
            severity="HARD_FAIL",
            message=(
                f"Only {coverage_pct:.1f}% of open deals have at least one activity "
                f"(minimum {min_coverage_pct:g}% required). {len(open_deal_ids) - covered} open deals have zero activities."
            ),
            row_count=len(open_deal_ids) - covered,
        )
    return None


# ─────────────── WARN rules ───────────────────────────────────

def _warn_rep_without_quota(
    rep_rows: list[dict],
    quota_rows: list[dict],
) -> RuleViolation | None:
    rep_ids = {r.get("id", "") for r in rep_rows}
    quota_rep_ids = {r.get("rep_id", "") for r in quota_rows}
    missing = rep_ids - quota_rep_ids
    if missing:
        return RuleViolation(
            rule="rep_without_quota",
            severity="WARN",
            message=f"{len(missing)} reps have no quota records.",
            row_count=len(missing),
        )
    return None


def _warn_empty_revenue_type(revenue_rows: list[dict]) -> RuleViolation | None:
    bad = [r for r in revenue_rows if not r.get("revenue_type", "").strip()]
    if bad:
        return RuleViolation(
            rule="empty_revenue_type",
            severity="WARN",
            message=(
                f"{len(bad)} revenue rows are missing revenue_type "
                "(new_logo/expansion/contraction/churn/renewal)."
            ),
            row_count=len(bad),
        )
    return None


def _warn_ramp_mismatch(rep_ramp_rows: list[dict]) -> RuleViolation | None:
    """Ramp factor should be ≤ 1.0 and non-negative."""
    bad = [
        r for r in rep_ramp_rows
        if not (0.0 <= float(r.get("ramp_factor", 1.0)) <= 1.0)
    ]
    if bad:
        return RuleViolation(
            rule="ramp_mismatch",
            severity="WARN",
            message=f"{len(bad)} rep_ramp rows have ramp_factor outside [0, 1].",
            row_count=len(bad),
        )
    return None


def _warn_manager_span(rep_hierarchy_rows: list[dict], max_span: int = 8) -> RuleViolation | None:
    """Warn when a manager has too many direct reports."""
    if not rep_hierarchy_rows:
        return None
    reports_by_manager = Counter(
        r.get("manager_rep_id", "")
        for r in rep_hierarchy_rows
        if r.get("manager_rep_id", "").strip()
    )
    if not reports_by_manager:
        return None
    max_found = max(reports_by_manager.values())
    if max_found > max_span:
        return RuleViolation(
            rule="manager_span",
            severity="WARN",
            message=f"Detected manager span of {max_found} direct reports (recommended max {max_span}).",
            row_count=sum(1 for v in reports_by_manager.values() if v > max_span),
        )
    return None


def _warn_role_mix(rep_hierarchy_rows: list[dict]) -> RuleViolation | None:
    """Warn when hierarchy lacks leadership or IC role diversity."""
    if not rep_hierarchy_rows:
        return None
    roles = {r.get("role", "").strip() for r in rep_hierarchy_rows if r.get("role", "").strip()}
    leadership_keywords = ("Chief", "SVP", "VP", "Director", "Manager")
    has_leadership = any(any(k in role for k in leadership_keywords) for role in roles)
    has_ic = any(role in {"Account Executive", "Senior Account Executive", "Sales Development Representative", "Overlay Specialist"} for role in roles)
    if not has_leadership or not has_ic:
        return RuleViolation(
            rule="role_mix",
            severity="WARN",
            message="rep_hierarchy role mix is incomplete; expected both leadership and IC role families.",
            row_count=len(rep_hierarchy_rows),
        )
    return None


# ─────────────────────────────────────────────────────────────
# Main validator class
# ─────────────────────────────────────────────────────────────

class RevOpsBusinessRuleValidator:
    """Run RevOps hard-fail and warn rules against a company CSV directory."""

    def __init__(
        self,
        max_quota_to_revenue_ratio: float = 20.0,
        min_open_deal_activity_coverage_pct: float = 50.0,
        manager_span_warn_threshold: int = 8,
    ) -> None:
        self.max_quota_to_revenue_ratio = max(0.1, float(max_quota_to_revenue_ratio))
        self.min_open_deal_activity_coverage_pct = min(
            100.0,
            max(0.0, float(min_open_deal_activity_coverage_pct)),
        )
        self.manager_span_warn_threshold = max(1, int(manager_span_warn_threshold))

    def validate(self, company_dir: Path) -> RevOpsValidationResult:
        deal_rows = _load(company_dir / "deals.csv")
        quota_rows = _load(company_dir / "quotas.csv")
        revenue_rows = _load(company_dir / "revenue.csv")
        activity_rows = _load(company_dir / "activities.csv")
        rep_rows = _load(company_dir / "reps.csv")
        rep_ramp_rows = _load(company_dir / "rep_ramp.csv")
        rep_hierarchy_rows = _load(company_dir / "rep_hierarchy.csv")

        violations: list[RuleViolation] = []
        warnings: list[RuleViolation] = []

        # Hard-fail rules
        for check_result in [
            _rule_quota_vs_revenue(
                quota_rows,
                revenue_rows,
                max_ratio=self.max_quota_to_revenue_ratio,
            ),
            _rule_stage_progression(deal_rows),
            _rule_date_order(deal_rows),
            _rule_negative_arr(deal_rows),
            _rule_activity_coverage(
                deal_rows,
                activity_rows,
                min_coverage_pct=self.min_open_deal_activity_coverage_pct,
            ),
        ]:
            if check_result is not None:
                violations.append(check_result)

        # Warn rules
        for check_result in [
            _warn_rep_without_quota(rep_rows, quota_rows),
            _warn_empty_revenue_type(revenue_rows),
            _warn_ramp_mismatch(rep_ramp_rows),
            _warn_manager_span(
                rep_hierarchy_rows,
                max_span=self.manager_span_warn_threshold,
            ),
            _warn_role_mix(rep_hierarchy_rows),
        ]:
            if check_result is not None:
                warnings.append(check_result)

        return RevOpsValidationResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
        )
