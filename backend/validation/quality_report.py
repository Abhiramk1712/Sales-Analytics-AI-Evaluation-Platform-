"""
backend/validation/quality_report.py
====================================
Data quality report and check results
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class CheckResult:
    """Result of a single data quality check."""
    
    check_name: str
    status: str  # 'PASS', 'WARN', 'FAIL'
    message: str
    affected_rows: int = 0
    details: Optional[dict] = None


@dataclass
class DataQualityReport:
    """
    Comprehensive data quality report.
    """
    
    status: str  # 'PASS', 'WARN', 'FAIL'
    checked_at: datetime = field(default_factory=datetime.utcnow)
    error_count: int = 0
    warning_count: int = 0
    check_results: list[CheckResult] = field(default_factory=list)
    row_count: int = 0
    column_count: int = 0
    
    def add_check(self, result: CheckResult) -> None:
        """Add a check result."""
        self.check_results.append(result)
        if result.status == "FAIL":
            self.error_count += 1
        elif result.status == "WARN":
            self.warning_count += 1
    
    def update_status(self) -> None:
        """Update overall status based on check results."""
        if self.error_count > 0:
            self.status = "FAIL"
        elif self.warning_count > 0:
            self.status = "WARN"
        else:
            self.status = "PASS"
    
    def summary(self) -> dict:
        """Return a summary dict."""
        return {
            "status": self.status,
            "checked_at": self.checked_at.isoformat(),
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "row_count": self.row_count,
            "column_count": self.column_count,
            "check_count": len(self.check_results),
            "checks": [
                {
                    "name": c.check_name,
                    "status": c.status,
                    "message": c.message,
                    "affected_rows": c.affected_rows,
                }
                for c in self.check_results
            ],
        }
