"""
period_filters.py — Semantic SQLAlchemy filter builders per table/date-field context.

Each builder returns a list of SQLAlchemy WHERE clauses appropriate for the
date semantics of the target table:

  - Revenue        → Revenue.period  (YYYY-MM string, lexicographic compare)
  - Quota          → Quota.period    (YYYY-MM or YYYY-Qn, use period_bounds)
  - Closed deals   → Deal.actual_close_date  (DATE column)
  - Pipeline deals → Deal.expected_close_date (DATE column)
  - Bookings       → Booking.booking_date (DATE column)
  - ARR waterfall  → ArrWaterfallEntry.period (YYYY-MM string)
"""
from __future__ import annotations

from datetime import datetime, date
from typing import Any, Optional

from sqlalchemy import cast, Date, and_
from sqlalchemy.orm import InstrumentedAttribute

from backend.models import (
    ArrWaterfallEntry,
    Booking,
    Deal,
    Quota,
    Revenue,
)
from backend.utils.date_ranges import parse_period_to_range


def _date_from_str(s: str) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _period_bounds_from_filters(filters: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    """Return (start_period_str, end_period_str) as YYYY-MM from a filters dict."""
    start = filters.get("start_date")
    end = filters.get("end_date")
    return (str(start)[:7] if start else None, str(end)[:7] if end else None)


def _date_column_clauses(col: InstrumentedAttribute, filters: dict[str, Any]) -> list:
    """Generic DATE column bounds (casts if needed)."""
    clauses = []
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start:
        clauses.append(col >= _date_from_str(start))
    if end:
        clauses.append(col <= _date_from_str(end))
    return clauses


# ── Public builders ────────────────────────────────────────────────────────


def build_revenue_period_filter(filters: dict[str, Any]) -> list:
    """
    Filter Revenue rows by Revenue.period (YYYY-MM lexicographic).
    start_date / end_date are truncated to YYYY-MM for comparison.
    """
    clauses = []
    start_p, end_p = _period_bounds_from_filters(filters)
    if start_p:
        clauses.append(Revenue.period >= start_p)
    if end_p:
        clauses.append(Revenue.period <= end_p)
    return clauses


def build_quota_period_filter(filters: dict[str, Any]) -> list:
    """Filter Quota rows by Quota.period (YYYY-MM or YYYY-Qn, lexicographic)."""
    clauses = []
    start_p, end_p = _period_bounds_from_filters(filters)
    if start_p:
        clauses.append(Quota.period >= start_p)
    if end_p:
        clauses.append(Quota.period <= end_p)
    return clauses


def build_closed_deal_period_filter(filters: dict[str, Any]) -> list:
    """
    Filter closed deals by Deal.actual_close_date (DATE).
    This is the semantically correct field for win-rate and closed-revenue queries.
    """
    return _date_column_clauses(Deal.actual_close_date, filters)


def build_pipeline_period_filter(filters: dict[str, Any]) -> list:
    """
    Filter open pipeline deals by Deal.expected_close_date (DATE).
    Use when asking 'what deals are expected to close in period X'.
    """
    return _date_column_clauses(Deal.expected_close_date, filters)


def build_deal_created_period_filter(filters: dict[str, Any]) -> list:
    """Filter deals by when they were created (Deal.created_at cast to DATE)."""
    clauses = []
    start = filters.get("start_date")
    end = filters.get("end_date")
    if start:
        clauses.append(cast(Deal.created_at, Date) >= _date_from_str(start))
    if end:
        clauses.append(cast(Deal.created_at, Date) <= _date_from_str(end))
    return clauses


def build_booking_period_filter(filters: dict[str, Any]) -> list:
    """Filter Booking rows by booking_date (DATE)."""
    return _date_column_clauses(Booking.booking_date, filters)


def build_arr_period_filter(filters: dict[str, Any]) -> list:
    """Filter ArrWaterfallEntry rows by period (YYYY-MM lexicographic)."""
    clauses = []
    start_p, end_p = _period_bounds_from_filters(filters)
    if start_p:
        clauses.append(ArrWaterfallEntry.period >= start_p)
    if end_p:
        clauses.append(ArrWaterfallEntry.period <= end_p)
    return clauses


def period_filter_for(table: str, filters: dict[str, Any]) -> list:
    """
    Dispatch helper — returns the right period filter clauses by table name.

    Supported table names: 'revenue', 'quota', 'deals_closed', 'deals_pipeline',
                            'deals_created', 'bookings', 'arr_waterfall'.
    """
    dispatch = {
        "revenue":        build_revenue_period_filter,
        "quota":          build_quota_period_filter,
        "deals_closed":   build_closed_deal_period_filter,
        "deals_pipeline": build_pipeline_period_filter,
        "deals_created":  build_deal_created_period_filter,
        "bookings":       build_booking_period_filter,
        "arr_waterfall":  build_arr_period_filter,
    }
    fn = dispatch.get(table)
    if fn is None:
        raise ValueError(f"Unknown table context '{table}'. Use one of: {list(dispatch)}")
    return fn(filters)
