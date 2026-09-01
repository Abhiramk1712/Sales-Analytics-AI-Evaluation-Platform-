"""
tests/test_format_utils.py
===========================
Verify frontend formatting utilities don't produce NaN/undefined.
These are unit tests for the equivalent logic that runs in the browser.
"""


def safe_number(value, fallback=0):
    """Python equivalent of frontend safeNumber."""
    try:
        n = float(value)
        if n != n:  # NaN check
            return fallback
        if abs(n) == float("inf"):
            return fallback
        return n
    except (TypeError, ValueError):
        return fallback


def fmt(n):
    """Python equivalent of frontend fmt."""
    v = safe_number(n)
    if v >= 1e6:
        return f"${v / 1e6:.1f}M"
    if v >= 1e3:
        return f"${v / 1e3:.0f}K"
    return f"${v:,.0f}"


def pct(n):
    """Python equivalent of frontend pct."""
    v = safe_number(n)
    return f"{v:.1f}%"


def test_safe_number_none():
    assert safe_number(None) == 0


def test_safe_number_nan():
    assert safe_number(float("nan")) == 0


def test_safe_number_string():
    assert safe_number("abc") == 0


def test_safe_number_valid():
    assert safe_number(42.5) == 42.5


def test_safe_number_string_number():
    assert safe_number("123.45") == 123.45


def test_safe_number_empty_string():
    assert safe_number("") == 0


def test_safe_number_inf():
    assert safe_number(float("inf")) == 0


def test_fmt_nan():
    result = fmt(float("nan"))
    assert "NaN" not in result


def test_fmt_none():
    result = fmt(None)
    assert "NaN" not in result
    assert "undefined" not in result


def test_fmt_large():
    assert fmt(2_500_000) == "$2.5M"


def test_fmt_medium():
    assert fmt(50_000) == "$50K"


def test_pct_nan():
    result = pct(float("nan"))
    assert "NaN" not in result


def test_pct_none():
    result = pct(None)
    assert "NaN" not in result


def test_pct_normal():
    assert pct(85.7) == "85.7%"
