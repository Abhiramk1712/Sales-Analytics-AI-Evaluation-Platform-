from __future__ import annotations

import calendar
from datetime import date, datetime
import re
from dataclasses import dataclass
from typing import Optional

# Quarter → (start_month, end_month)
_QUARTER_MONTHS: dict[int, tuple[int, int]] = {
    1: (1, 3),
    2: (4, 6),
    3: (7, 9),
    4: (10, 12),
}


@dataclass
class PeriodRange:
    start_date: str
    end_date: str


def _natural_to_canonical(period: str) -> Optional[str]:
    """Convert natural language period strings to canonical YYYY-MM / YYYY-QN / YYYY."""
    today = date.today()
    lower = period.strip().lower()

    if lower in ("this month",):
        return today.strftime("%Y-%m")
    if lower in ("last month",):
        first = today.replace(day=1)
        prev = first.replace(month=first.month - 1) if first.month > 1 else first.replace(year=first.year - 1, month=12)
        return prev.strftime("%Y-%m")
    if lower in ("this quarter",):
        q = (today.month - 1) // 3 + 1
        return f"{today.year}-Q{q}"
    if lower in ("last quarter",):
        q = (today.month - 1) // 3 + 1
        pq, py = (q - 1, today.year) if q > 1 else (4, today.year - 1)
        return f"{py}-Q{pq}"
    if lower in ("ytd", "year to date", "this year"):
        return str(today.year)
    if lower in ("last year",):
        return str(today.year - 1)

    # "Q2 2024" or "2024 Q2"
    m = re.match(r"^Q([1-4])\s+(\d{4})$", period.strip(), re.IGNORECASE)
    if m:
        return f"{m.group(2)}-Q{m.group(1)}"
    m = re.match(r"^(\d{4})\s+Q([1-4])$", period.strip(), re.IGNORECASE)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    # "2024/01"
    m = re.match(r"^(\d{4})/(\d{2})$", period.strip())
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    return None


def parse_period_to_range(period: Optional[str]) -> Optional[PeriodRange]:
    """
    Parse a period string into an inclusive (start_date, end_date) range.

    Supported formats:
      YYYY-MM          → first day .. last day of that month
      YYYY             → 01-01 .. 12-31 of that year
      YYYY-Q1..Q4      → quarter start .. quarter end
      "Q2 2024"        → same as YYYY-Q2
      "this month"     → current month
      "last month"     → previous month
      "this quarter"   → current quarter
      "last quarter"   → previous quarter
      "YTD"            → current year
      "last year"      → previous year

    Raises ValueError for unrecognised formats.
    Returns None when period is None or empty.
    """
    if not period:
        return None

    # "all time" → no date filter
    if period.strip().lower() in ("all time", "all", "alltime"):
        return None

    # Try natural language conversion first
    canonical = _natural_to_canonical(period)
    if canonical:
        period = canonical

    # YYYY-MM
    if re.match(r"^\d{4}-\d{2}$", period):
        year, month = int(period[:4]), int(period[5:7])
        if not (1 <= month <= 12):
            raise ValueError(f"Invalid month in period '{period}'. Month must be 01–12.")
        last_day = calendar.monthrange(year, month)[1]
        return PeriodRange(start_date=f"{period}-01", end_date=f"{period}-{last_day:02d}")

    # YYYY-Q1 .. YYYY-Q4
    q_match = re.match(r"^(\d{4})-Q([1-4])$", period)
    if q_match:
        year, q = int(q_match.group(1)), int(q_match.group(2))
        start_month, end_month = _QUARTER_MONTHS[q]
        last_day = calendar.monthrange(year, end_month)[1]
        return PeriodRange(
            start_date=f"{year:04d}-{start_month:02d}-01",
            end_date=f"{year:04d}-{end_month:02d}-{last_day:02d}",
        )

    # YYYY
    if re.match(r"^\d{4}$", period):
        return PeriodRange(start_date=f"{period}-01-01", end_date=f"{period}-12-31")

    raise ValueError(
        f"Invalid period format '{period}'. "
        "Use YYYY-MM, YYYY-Q1..Q4, YYYY, or natural language like 'this month'."
    )


def previous_period(period: str) -> str:
    """
    Return the previous period string in the same format.

    YYYY-MM  → one month earlier
    YYYY-Q1  → previous quarter (wraps year)
    YYYY     → previous year
    """
    if re.match(r"^\d{4}-\d{2}$", period):
        current = datetime.strptime(period + "-01", "%Y-%m-%d")
        year = current.year if current.month > 1 else current.year - 1
        month = current.month - 1 if current.month > 1 else 12
        return f"{year:04d}-{month:02d}"

    q_match = re.match(r"^(\d{4})-Q([1-4])$", period)
    if q_match:
        year, q = int(q_match.group(1)), int(q_match.group(2))
        if q == 1:
            return f"{year - 1:04d}-Q4"
        return f"{year:04d}-Q{q - 1}"

    if re.match(r"^\d{4}$", period):
        return f"{int(period) - 1:04d}"

    raise ValueError(
        f"Invalid period format '{period}'. "
        "Use YYYY-MM for monthly, YYYY-Q1..Q4 for quarterly, or YYYY for yearly."
    )


def period_to_filter_dict(period: Optional[str]) -> dict[str, str]:
    if not period:
        return {}
    resolved = parse_period_to_range(period)
    if not resolved:
        return {}
    return {"start_date": resolved.start_date, "end_date": resolved.end_date}


def period_label(period: str) -> str:
    """Human-readable label for a period string."""
    q_match = re.match(r"^(\d{4})-Q([1-4])$", period)
    if q_match:
        year, q = q_match.group(1), q_match.group(2)
        month_names = {1: "Jan–Mar", 2: "Apr–Jun", 3: "Jul–Sep", 4: "Oct–Dec"}
        return f"Q{q} {year} ({month_names[int(q)]})"
    if re.match(r"^\d{4}-\d{2}$", period):
        dt = datetime.strptime(period + "-01", "%Y-%m-%d")
        return dt.strftime("%B %Y")
    if re.match(r"^\d{4}$", period):
        return f"FY {period}"
    return period