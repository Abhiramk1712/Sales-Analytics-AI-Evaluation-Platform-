"""
tests/test_period_normalization.py
===================================
Tests for period normalisation, quota-period matching, and month expansion.
"""
from __future__ import annotations

import pytest
from datetime import date

from backend.services.quota_attainment_service import (
    normalize_period,
    period_grain,
    period_to_months,
    quota_period_matches,
)
from backend.utils.date_ranges import parse_period_to_range


class TestNormalizePeriod:
    def test_monthly_passthrough(self):
        assert normalize_period("2024-03") == "2024-03"

    def test_quarterly_passthrough(self):
        assert normalize_period("2024-Q2") == "2024-Q2"

    def test_annual_passthrough(self):
        assert normalize_period("2024") == "2024"

    def test_q2_space_year(self):
        assert normalize_period("Q2 2024") == "2024-Q2"

    def test_year_space_q2(self):
        assert normalize_period("2024 Q2") == "2024-Q2"

    def test_year_slash_month(self):
        assert normalize_period("2024/03") == "2024-03"

    def test_this_month_returns_yyyy_mm(self):
        result = normalize_period("this month")
        assert result is not None
        assert len(result) == 7  # YYYY-MM

    def test_last_month(self):
        result = normalize_period("last month")
        assert result is not None
        today = date.today()
        # last month must be ≤ current month
        assert result <= today.strftime("%Y-%m")

    def test_this_quarter(self):
        result = normalize_period("this quarter")
        assert result is not None
        assert "Q" in result

    def test_last_quarter(self):
        result = normalize_period("last quarter")
        assert result is not None
        assert "Q" in result

    def test_ytd(self):
        result = normalize_period("YTD")
        today = date.today()
        assert result == str(today.year)

    def test_none_returns_none(self):
        assert normalize_period(None) is None

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            normalize_period("not-a-period")


class TestPeriodGrain:
    def test_monthly(self):
        assert period_grain("2024-01") == "monthly"

    def test_quarterly(self):
        assert period_grain("2024-Q3") == "quarterly"

    def test_annual(self):
        assert period_grain("2024") == "annual"

    def test_natural_language(self):
        assert period_grain("this month") == "monthly"
        assert period_grain("this quarter") == "quarterly"
        assert period_grain("ytd") == "annual"


class TestPeriodToMonths:
    def test_monthly(self):
        assert period_to_months("2024-03") == ["2024-03"]

    def test_q1(self):
        assert period_to_months("2024-Q1") == ["2024-01", "2024-02", "2024-03"]

    def test_q2(self):
        assert period_to_months("2024-Q2") == ["2024-04", "2024-05", "2024-06"]

    def test_q3(self):
        assert period_to_months("2024-Q3") == ["2024-07", "2024-08", "2024-09"]

    def test_q4(self):
        assert period_to_months("2024-Q4") == ["2024-10", "2024-11", "2024-12"]

    def test_annual(self):
        months = period_to_months("2024")
        assert len(months) == 12
        assert months[0] == "2024-01"
        assert months[-1] == "2024-12"

    def test_natural_language_monthly(self):
        months = period_to_months("this month")
        assert len(months) == 1


class TestQuotaPeriodMatches:
    def test_exact_match(self):
        assert quota_period_matches("2024-Q2", "2024-Q2") is True

    def test_monthly_in_quarter(self):
        assert quota_period_matches("2024-Q2", "2024-05") is True

    def test_monthly_outside_quarter(self):
        assert quota_period_matches("2024-Q2", "2024-01") is False

    def test_monthly_quota_in_quarterly_target(self):
        assert quota_period_matches("2024-05", "2024-Q2") is True

    def test_annual_covers_quarter(self):
        assert quota_period_matches("2024", "2024-Q2") is True

    def test_annual_covers_month(self):
        assert quota_period_matches("2024", "2024-07") is True


class TestParsePeriodToRangeExtended:
    def test_natural_this_month(self):
        pr = parse_period_to_range("this month")
        assert pr is not None
        assert len(pr.start_date) == 10

    def test_natural_last_year(self):
        pr = parse_period_to_range("last year")
        today = date.today()
        assert pr is not None
        assert pr.start_date.startswith(str(today.year - 1))

    def test_q2_space(self):
        pr = parse_period_to_range("Q2 2024")
        assert pr is not None
        assert pr.start_date == "2024-04-01"
        assert pr.end_date == "2024-06-30"
