"""
backend/ml/forecasting_lab/datasets.py
======================================
Forecasting-lab dataset metadata and time-series helpers.
"""
from __future__ import annotations

from datetime import datetime, timezone


TARGET_REGISTRY: dict[str, dict[str, str]] = {
    "revenue": {
        "label": "Revenue",
        "description": "Monthly recognized revenue",
        "default_source": "revenue",
    },
    "ARR": {
        "label": "ARR",
        "description": "Annual recurring revenue ending balance",
        "default_source": "arr_waterfall.arr_end",
    },
    "pipeline": {
        "label": "Open Pipeline",
        "description": "Open pipeline value by expected close month",
        "default_source": "deals.open_pipeline",
    },
    "commit": {
        "label": "Commit Forecast",
        "description": "Probability-weighted commit projection",
        "default_source": "deals.open_pipeline",
    },
    "best_case": {
        "label": "Best Case Forecast",
        "description": "Upside-weighted best-case projection",
        "default_source": "deals.open_pipeline",
    },
    "booking": {
        "label": "Bookings",
        "description": "Monthly booking totals",
        "default_source": "bookings",
    },
    "payout": {
        "label": "Payout",
        "description": "Commission payout totals allocated to month",
        "default_source": "payouts",
    },
    "quota_attainment": {
        "label": "Quota Attainment",
        "description": "Revenue as percent of quota",
        "default_source": "revenue+quota",
    },
}


def list_supported_targets() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key, meta in TARGET_REGISTRY.items():
        rows.append(
            {
                "target": key,
                "label": meta["label"],
                "description": meta["description"],
                "default_source": meta["default_source"],
            }
        )
    return rows


def normalize_history(values: list[float], periods: list[str]) -> tuple[list[float], list[str]]:
    """Coerce values/periods into aligned monthly arrays."""
    n = min(len(values), len(periods))
    if n == 0:
        return [], []

    pairs = []
    for idx in range(n):
        period = str(periods[idx])
        if len(period) != 7 or period[4] != "-":
            continue
        try:
            val = float(values[idx])
        except Exception:
            continue
        pairs.append((period, val))

    pairs.sort(key=lambda t: t[0])
    normalized_periods = [p for p, _ in pairs]
    normalized_values = [round(float(v), 4) for _, v in pairs]
    return normalized_values, normalized_periods


def future_periods_from_history(history_periods: list[str], horizon: int) -> list[str]:
    """Generate future YYYY-MM labels based on the last observed period."""
    if horizon <= 0:
        return []

    if not history_periods:
        now = datetime.now(timezone.utc)
        return [f"{now.year:04d}-{((now.month + i - 1) % 12) + 1:02d}" for i in range(1, horizon + 1)]

    try:
        last = history_periods[-1]
        dt = datetime.strptime(last + "-01", "%Y-%m-%d")
        result = []
        year = dt.year
        month = dt.month
        for _ in range(horizon):
            month += 1
            if month > 12:
                month = 1
                year += 1
            result.append(f"{year:04d}-{month:02d}")
        return result
    except Exception:
        return [f"T+{i}" for i in range(1, horizon + 1)]
