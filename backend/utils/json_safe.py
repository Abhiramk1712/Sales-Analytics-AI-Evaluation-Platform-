"""
backend/utils/json_safe.py
==========================
Make values from numpy and pandas safe to serialize.

This exists because the same class of bug has surfaced twice in endpoints that
compute with numpy and return the result directly:

- `/payout/audit/{company}` 500'd on `numpy.bool_` — Pydantic refuses it.
- `/ml/forecast/churn-risk` 500'd on `inf` — a survival curve that never drops
  below 0.5 has an infinite median tenure, and JSON has no way to say `inf`.

Both are the same shape: a number that is perfectly reasonable inside numpy and
meaningless in JSON. Converting at the boundary, once, is more reliable than
finding each field — the second bug was in a field nobody had thought about.

`None` is used for non-finite floats rather than a sentinel like 0 or a string,
because "there is no median tenure" is genuinely absent, and a 0 would be read
as "churns immediately" — the opposite of what infinity means here.
"""
from __future__ import annotations

import math
from typing import Any


def json_safe(value: Any) -> Any:
    """
    Recursively convert `value` into something `json.dumps` accepts.

    - numpy scalars become their Python equivalents
    - `inf`, `-inf` and `nan` become None
    - dicts, lists and tuples are converted element-wise
    - everything else is returned unchanged
    """
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]

    # numpy scalars expose both .item() and .dtype; the guard keeps numpy an
    # optional import here rather than a hard dependency of this module.
    if hasattr(value, "item") and hasattr(value, "dtype"):
        value = value.item()

    if isinstance(value, float) and not math.isfinite(value):
        return None

    return value
