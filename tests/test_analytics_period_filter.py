import pytest

from backend.utils.date_ranges import parse_period_to_range, previous_period, period_to_filter_dict, period_label


def test_parse_month_period_range():
    r = parse_period_to_range("2025-04")
    assert r.start_date == "2025-04-01"
    assert r.end_date == "2025-04-30"


def test_parse_month_31_days():
    r = parse_period_to_range("2025-01")
    assert r.end_date == "2025-01-31"


def test_parse_month_28_days_leap():
    r = parse_period_to_range("2024-02")
    assert r.end_date == "2024-02-29"  # 2024 is a leap year


def test_parse_month_28_days_non_leap():
    r = parse_period_to_range("2025-02")
    assert r.end_date == "2025-02-28"


def test_parse_year_period_range():
    r = parse_period_to_range("2025")
    assert r.start_date == "2025-01-01"
    assert r.end_date == "2025-12-31"


# ── Quarter tests ─────────────────────────────────────────────────────────

def test_parse_q1():
    r = parse_period_to_range("2025-Q1")
    assert r.start_date == "2025-01-01"
    assert r.end_date == "2025-03-31"


def test_parse_q2():
    r = parse_period_to_range("2025-Q2")
    assert r.start_date == "2025-04-01"
    assert r.end_date == "2025-06-30"


def test_parse_q3():
    r = parse_period_to_range("2025-Q3")
    assert r.start_date == "2025-07-01"
    assert r.end_date == "2025-09-30"


def test_parse_q4():
    r = parse_period_to_range("2025-Q4")
    assert r.start_date == "2025-10-01"
    assert r.end_date == "2025-12-31"


# ── previous_period with quarters ────────────────────────────────────────

def test_previous_period_for_month_and_year():
    assert previous_period("2025-01") == "2024-12"
    assert previous_period("2025") == "2024"


def test_previous_period_q1_wraps_year():
    assert previous_period("2025-Q1") == "2024-Q4"


def test_previous_period_q2():
    assert previous_period("2025-Q2") == "2025-Q1"


def test_previous_period_q4():
    assert previous_period("2025-Q4") == "2025-Q3"


# ── Invalid formats ───────────────────────────────────────────────────────

def test_slash_format_is_valid():
    """2025/04 is now accepted as YYYY/MM → same as 2025-04."""
    pr = parse_period_to_range("2025/04")
    assert pr is not None
    assert pr.start_date == "2025-04-01"


def test_invalid_quarter_raises():
    with pytest.raises(ValueError):
        parse_period_to_range("2025-Q5")


def test_invalid_month_raises():
    with pytest.raises(ValueError):
        parse_period_to_range("2025-13")


# ── Helpers ───────────────────────────────────────────────────────────────

def test_period_to_filter_dict_empty_safe():
    assert period_to_filter_dict(None) == {}


def test_period_to_filter_dict_quarter():
    d = period_to_filter_dict("2025-Q2")
    assert d["start_date"] == "2025-04-01"
    assert d["end_date"] == "2025-06-30"


def test_period_label_quarter():
    assert "Q2" in period_label("2025-Q2")
    assert "2025" in period_label("2025-Q2")


def test_period_label_month():
    lbl = period_label("2025-03")
    assert "March" in lbl or "2025" in lbl


def test_period_label_year():
    assert "2025" in period_label("2025")
