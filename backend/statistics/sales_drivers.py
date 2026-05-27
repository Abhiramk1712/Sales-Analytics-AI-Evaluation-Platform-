from __future__ import annotations

import pandas as pd


def revenue_driver_analysis(df: pd.DataFrame, period_col: str, group_cols: list[str], metric_col: str) -> dict:
    if df.empty:
        return {"drivers": [], "warnings": ["Empty dataframe"]}

    grouped = df.groupby(group_cols, dropna=False)[metric_col].sum().reset_index()
    total = grouped[metric_col].sum()
    grouped["contribution_pct"] = (grouped[metric_col] / total * 100).round(2) if total else 0
    grouped = grouped.sort_values(metric_col, ascending=False)
    return {"drivers": grouped.to_dict(orient="records"), "warnings": []}


def quota_attainment_driver_analysis(df: pd.DataFrame) -> dict:
    required = {"rep_name", "revenue", "quota"}
    if not required.issubset(set(df.columns)):
        return {"drivers": [], "warnings": ["Missing columns for quota attainment driver analysis"]}

    work = df.copy()
    work["attainment_pct"] = work.apply(lambda r: (r["revenue"] / r["quota"] * 100) if r["quota"] else 0.0, axis=1)
    work = work.sort_values("attainment_pct", ascending=False)
    return {"drivers": work[["rep_name", "attainment_pct"]].to_dict(orient="records"), "warnings": []}


def payout_or_revenue_change_explanation(df: pd.DataFrame) -> dict:
    if df.empty or "change" not in df.columns:
        return {"explanations": [], "warnings": ["Insufficient data for payout or revenue change explanation"]}

    explanations = []
    for _, row in df.iterrows():
        direction = "increased" if row["change"] >= 0 else "decreased"
        explanations.append({
            "entity": row.get("entity", "unknown"),
            "change": float(row["change"]),
            "explanation": f"{row.get('entity', 'Entity')} {direction} by {abs(float(row['change'])):.2f}",
        })
    return {"explanations": explanations, "warnings": []}


def explain_metric_change(current_metrics: dict, previous_metrics: dict) -> dict:
    if not current_metrics or not previous_metrics:
        return {"drivers": [], "warnings": ["Current or previous metrics missing for period-over-period analysis"]}

    tracked = [
        ("total_revenue", "Revenue"),
        ("attainment_pct", "Quota Attainment"),
        ("win_rate", "Win Rate"),
        ("pipeline_coverage", "Pipeline Coverage"),
    ]
    drivers = []
    for key, label in tracked:
        cur = float(current_metrics.get(key, 0.0) or 0.0)
        prev = float(previous_metrics.get(key, 0.0) or 0.0)
        delta = cur - prev
        pct = None if abs(prev) < 1e-9 else (delta / prev) * 100.0
        direction = "increased" if delta > 0 else ("decreased" if delta < 0 else "was flat")
        drivers.append(
            {
                "metric": key,
                "label": label,
                "current": round(cur, 4),
                "previous": round(prev, 4),
                "delta": round(delta, 4),
                "delta_pct": None if pct is None else round(pct, 2),
                "direction": direction,
            }
        )

    positive = sorted([d for d in drivers if d["delta"] > 0], key=lambda x: x["delta"], reverse=True)
    negative = sorted([d for d in drivers if d["delta"] < 0], key=lambda x: x["delta"]) 
    return {
        "drivers": drivers,
        "top_positive_drivers": positive[:3],
        "top_negative_drivers": negative[:3],
        "warnings": [],
    }
