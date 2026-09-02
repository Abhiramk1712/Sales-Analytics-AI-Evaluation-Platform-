"""
backend/payout/money.py
=======================
Exact arithmetic for amounts that somebody gets paid.

The schema was always right — every monetary column is `Numeric(14,2)`. The
engine was not: values were coerced with `float()` at each read boundary and the
whole calculation ran in binary floating point, where 0.1 + 0.2 is not 0.3.

That did not visibly break anything, because `allocate_pro_rata` assigns the
rounding residual to the largest share and the period total therefore reconciles.
But exactness resting on one function compensating at the end is fragile: any new
calculation that does not route through it reintroduces drift, and a $0.01
tolerance in the reconciliation would absorb it silently.

Rates and thresholds are converted too, not just amounts. A rate is one operand
of a multiplication whose result is money; leaving it as a float puts the error
back.

`D()` converts through `str()` deliberately. `Decimal(0.1)` is
0.1000000000000000055511151231257827 — the float's real value — whereas
`Decimal("0.1")` is exactly one tenth. Going via the repr is what makes a value
read from a float mean what it appears to mean.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: Cent precision, the grain every payable amount is stored and paid at.
CENTS = Decimal("0.01")

ZERO = Decimal("0")


def D(value: Any, default: str = "0") -> Decimal:
    """
    Convert anything numeric to Decimal, exactly as it reads.

    None and unparseable values fall back to `default` rather than raising:
    plan rules legitimately leave thresholds and bonuses unset, and a missing
    rate means "no rate", not "crash the payout run".
    """
    if isinstance(value, Decimal):
        return value
    if value is None:
        return Decimal(default)
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def money(value: Any) -> Decimal:
    """Round to cents, half away from zero — the convention payroll uses."""
    return D(value).quantize(CENTS, rounding=ROUND_HALF_UP)


def to_float(value: Any) -> float:
    """
    Back to float at the response boundary.

    Callers, response models and the ORM all still speak float. The conversion
    happens once, on the way out, after the arithmetic is finished — not before
    it starts, which is what the old code did.
    """
    return float(money(value))
