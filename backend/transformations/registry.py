"""
backend/transformations/registry.py
==================================
Manifest-aware transformation registry.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable


def _norm_stage(value: Any) -> str:
    if value is None:
        return "Qualification"
    raw = str(value).strip()
    if not raw:
        return "Qualification"
    table = {
        "prospecting": "Prospecting",
        "qualification": "Qualification",
        "proposal": "Proposal",
        "negotiation": "Negotiation",
        "closed won": "Closed Won",
        "won": "Closed Won",
        "closed_won": "Closed Won",
        "closedwon": "Closed Won",
        "closed lost": "Closed Lost",
        "lost": "Closed Lost",
        "closed_lost": "Closed Lost",
        "closedlost": "Closed Lost",
    }
    return table.get(raw.lower(), raw)


def _norm_period(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if len(raw) == 7 and raw[4] == "-":
        return raw
    if "Q" in raw.upper():
        v = raw.upper().replace(" ", "").replace("/", "-")
        if v.startswith("Q") and "-" in v:
            q, y = v.split("-", 1)
            return f"{y}-{q}"
        if "-Q" in v:
            return v
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m")
        except ValueError:
            pass
    return raw


def _date_parse(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return raw[:10]


def _datetime_parse(value: Any) -> str:
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    if "T" in raw:
        return raw
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            pass
    return raw


def _email_lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _strip(value: Any) -> str:
    return str(value or "").strip()


TRANSFORMS: dict[str, Callable[[Any], Any]] = {
    "stage_normalize": _norm_stage,
    "period_normalize": _norm_period,
    "date_parse": _date_parse,
    "datetime_parse": _datetime_parse,
    "email_lower": _email_lower,
    "strip": _strip,
}


def apply_transform(transform_name: str | None, value: Any) -> Any:
    if not transform_name:
        return value
    fn = TRANSFORMS.get(transform_name)
    if not fn:
        return value
    return fn(value)
