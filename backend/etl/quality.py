"""Data quality checks for ETL contracts and business sanity rules."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backend.etl.contracts import DATA_CONTRACTS


@dataclass
class QualityIssue:
    check: str
    severity: str
    message: str


def _is_valid_period(value: Any) -> bool:
    s = str(value or "")
    return bool(
        s and (
            len(s) == 7 and s[4] == "-" or
            (len(s) == 7 and s[4:6] == "-Q") or
            (len(s) == 4 and s.isdigit())
        )
    )


def evaluate_table_quality(entity: str, df: pd.DataFrame) -> tuple[list[QualityIssue], float]:
    issues: list[QualityIssue] = []
    contract = DATA_CONTRACTS.get(entity)
    if contract:
        missing_cols = [c for c in contract.required_columns if c not in df.columns]
        if missing_cols:
            issues.append(QualityIssue("missing_columns", "critical", f"Missing required columns: {missing_cols}"))

    if "id" in df.columns:
        if df["id"].isna().any():
            issues.append(QualityIssue("missing_ids", "critical", "Rows with null IDs detected"))
        dup_count = int(df["id"].duplicated().sum())
        if dup_count:
            issues.append(QualityIssue("duplicate_ids", "major", f"Duplicate IDs detected: {dup_count}"))

    if entity == "revenue" and "amount" in df.columns:
        neg = int((pd.to_numeric(df["amount"], errors="coerce").fillna(0) < 0).sum())
        if neg:
            issues.append(QualityIssue("negative_revenue", "major", f"Negative revenue rows: {neg}"))

    if entity in {"revenue", "quotas", "bookings", "churn_events"} and "period" in df.columns:
        invalid = int((~df["period"].map(_is_valid_period)).sum())
        if invalid:
            issues.append(QualityIssue("invalid_period", "major", f"Invalid period format rows: {invalid}"))

    if entity == "deals":
        if "close_probability" in df.columns:
            cp = pd.to_numeric(df["close_probability"], errors="coerce")
            bad = int(((cp < 0) | (cp > 100)).fillna(False).sum())
            if bad:
                issues.append(QualityIssue("invalid_close_probability", "major", f"Invalid close_probability rows: {bad}"))
        if "expected_close_date" in df.columns:
            missing = int(df["expected_close_date"].isna().sum())
            if missing:
                issues.append(QualityIssue("missing_expected_close_date", "minor", f"Missing expected_close_date rows: {missing}"))

    penalty = 0.0
    for issue in issues:
        penalty += {"critical": 35, "major": 15, "minor": 5}.get(issue.severity, 5)
    score = max(0.0, 100.0 - penalty)
    return issues, score
