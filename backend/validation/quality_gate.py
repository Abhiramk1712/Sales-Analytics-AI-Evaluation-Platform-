"""Quality gate helpers used by write-sensitive workflows."""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.routers.data_quality import _build_checks

_CRITICAL_CHECKS = {
    "empty_table_reps",
    "empty_table_deals",
    "empty_table_revenue",
    "orphaned_revenue_records",
    "orphaned_deals",
    "negative_revenue",
    "missing_quota",
}


def _derive_severity(check: dict[str, Any]) -> str:
    if check.get("severity"):
        return str(check["severity"]).lower()

    status = str(check.get("status", "")).upper()
    if status == "FAIL":
        return "error"
    if status == "WARN":
        return "warning"
    return "info"


async def get_critical_issues(db: AsyncSession) -> list[dict[str, Any]]:
    checks = await _build_checks(db)
    critical: list[dict[str, Any]] = []

    for check in checks:
        severity = _derive_severity(check)
        if severity == "critical":
            critical.append(check)
            continue

        if check.get("name") in _CRITICAL_CHECKS and str(check.get("status", "")).upper() == "FAIL":
            critical.append({**check, "severity": "critical"})

    return critical
