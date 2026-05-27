"""Gold marts and feature-store derivations for sales analytics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _quarter_label(period: str) -> str:
    year = int(period[:4])
    month = int(period[5:7])
    return f"{year}-Q{(month - 1) // 3 + 1}"


def build_gold_marts(silver: dict[str, pd.DataFrame]) -> dict[str, dict[str, Any]]:
    marts: dict[str, dict[str, Any]] = {}

    revenue = silver.get("revenue", pd.DataFrame())
    reps = silver.get("reps", pd.DataFrame())
    quotas = silver.get("quotas", pd.DataFrame())
    deals = silver.get("deals", pd.DataFrame())
    sales_units = silver.get("sales_units", pd.DataFrame())
    sales_credits = silver.get("sales_credits", pd.DataFrame())
    payouts = silver.get("payouts", pd.DataFrame())
    plans = silver.get("plans", pd.DataFrame())
    assignments = silver.get("plan_assignments", pd.DataFrame())
    uta = silver.get("user_territory_assignments", pd.DataFrame())
    territories = silver.get("territories", pd.DataFrame())

    if not revenue.empty:
        rev = revenue.copy()
        rev["amount"] = pd.to_numeric(rev["amount"], errors="coerce").fillna(0.0)
        rep_month = rev.groupby(["rep_id", "period"], as_index=False)["amount"].sum().rename(columns={"amount": "revenue"})

        quota_m = pd.DataFrame(columns=["rep_id", "period", "quota"])
        if not quotas.empty:
            q = quotas.copy()
            q["amount"] = pd.to_numeric(q["amount"], errors="coerce").fillna(0.0)
            monthly = q[q["period"].astype(str).str.match(r"^\d{4}-\d{2}$")][["rep_id", "period", "amount"]].rename(columns={"amount": "quota"})
            quarterly = q[q["period"].astype(str).str.match(r"^\d{4}-Q[1-4]$")][["rep_id", "period", "amount"]]
            q_rows = []
            for row in quarterly.itertuples(index=False):
                year, qn = str(row.period).split("-Q")
                start = (int(qn) - 1) * 3 + 1
                for m in range(start, start + 3):
                    q_rows.append({"rep_id": row.rep_id, "period": f"{year}-{m:02d}", "quota": float(row.amount) / 3.0})
            quarter_alloc = pd.DataFrame(q_rows)
            quota_m = pd.concat([monthly, quarter_alloc], ignore_index=True) if not quarter_alloc.empty else monthly

        mart_rep_month = rep_month.merge(quota_m, on=["rep_id", "period"], how="left")
        mart_rep_month["quota"] = pd.to_numeric(mart_rep_month["quota"], errors="coerce").fillna(0.0)
        mart_rep_month["attainment_pct"] = mart_rep_month.apply(
            lambda r: (r["revenue"] / r["quota"] * 100.0) if r["quota"] > 0 else 0.0,
            axis=1,
        )
        marts["mart_rep_month_performance"] = {
            "data": mart_rep_month,
            "metadata": {
                "row_count": int(len(mart_rep_month)),
                "generated_at": _now_iso(),
                "period": "mixed",
                "source_tables": ["revenue", "quotas"],
                "warnings": ["Monthly quota allocated from quarterly quotas when direct monthly quota missing"],
                "data_quality_score": 90.0,
            },
        }

        if not reps.empty:
            team_period = mart_rep_month.merge(reps[["id", "team_id"]], left_on="rep_id", right_on="id", how="left")
            team_period["quarter"] = team_period["period"].map(_quarter_label)
            agg = team_period.groupby(["team_id", "quarter"], as_index=False)[["revenue", "quota"]].sum()
            agg["attainment_pct"] = agg.apply(lambda r: (r["revenue"] / r["quota"] * 100.0) if r["quota"] > 0 else 0.0, axis=1)
            marts["mart_team_period_performance"] = {
                "data": agg,
                "metadata": {
                    "row_count": int(len(agg)),
                    "generated_at": _now_iso(),
                    "period": "quarterly",
                    "source_tables": ["revenue", "quotas", "reps"],
                    "warnings": [],
                    "data_quality_score": 90.0,
                },
            }

    if not deals.empty:
        d = deals.copy()
        open_pipeline = d[~d["stage"].astype(str).isin(["Closed Won", "Closed Lost"])].copy()
        if "expected_close_date" in open_pipeline.columns:
            open_pipeline["period"] = open_pipeline["expected_close_date"].astype(str).str[:7]
        open_pipeline["amount"] = pd.to_numeric(open_pipeline.get("amount", 0), errors="coerce").fillna(0.0)
        snap = open_pipeline.groupby(["period", "stage"], as_index=False)["amount"].sum().rename(columns={"amount": "open_pipeline"})
        marts["mart_pipeline_snapshot"] = {
            "data": snap,
            "metadata": {
                "row_count": int(len(snap)),
                "generated_at": _now_iso(),
                "period": "monthly",
                "source_tables": ["deals"],
                "warnings": [],
                "data_quality_score": 92.0,
            },
        }

    if not sales_units.empty and not sales_credits.empty:
        su = sales_units.copy()
        sc = sales_credits.copy()
        su["amount"] = pd.to_numeric(su.get("amount", 0), errors="coerce").fillna(0.0)
        sc["credit_percent"] = pd.to_numeric(sc.get("credit_percent", 0), errors="coerce").fillna(0.0)
        sc["credit_amount"] = pd.to_numeric(sc.get("credit_amount", 0), errors="coerce").fillna(0.0)
        credit_detail = sc.merge(su[["id", "opportunity_id", "account_id", "booked_date", "amount"]], left_on="sales_unit_id", right_on="id", how="left", suffixes=("", "_sales_unit"))
        marts["mart_sales_credit_detail"] = {
            "data": credit_detail,
            "metadata": {
                "row_count": int(len(credit_detail)),
                "generated_at": _now_iso(),
                "period": "mixed",
                "source_tables": ["sales_units", "sales_credits"],
                "warnings": [],
                "data_quality_score": 93.0,
            },
        }

    if not payouts.empty:
        pdtl = payouts.copy()
        pdtl["payout_amount"] = pd.to_numeric(pdtl.get("payout_amount", 0), errors="coerce").fillna(0.0)
        marts["mart_payout_detail"] = {
            "data": pdtl,
            "metadata": {
                "row_count": int(len(pdtl)),
                "generated_at": _now_iso(),
                "period": "mixed",
                "source_tables": ["payouts"],
                "warnings": [],
                "data_quality_score": 92.0,
            },
        }

    if not revenue.empty:
        r = revenue.copy()
        r["amount"] = pd.to_numeric(r.get("amount", 0), errors="coerce").fillna(0.0)
        monthly = r.groupby("period", as_index=False)["amount"].sum().rename(columns={"amount": "revenue"})
        monthly = monthly.sort_values("period")
        monthly["lag_1"] = monthly["revenue"].shift(1)
        monthly["lag_3"] = monthly["revenue"].shift(3)
        monthly["rolling_mean_3"] = monthly["revenue"].rolling(3).mean()
        monthly["rolling_mean_6"] = monthly["revenue"].rolling(6).mean()
        monthly = monthly.fillna(0.0)
        marts["mart_forecast_features"] = {
            "data": monthly,
            "metadata": {
                "row_count": int(len(monthly)),
                "generated_at": _now_iso(),
                "period": "monthly",
                "source_tables": ["revenue"],
                "warnings": [],
                "data_quality_score": 95.0,
            },
        }

    # Plan and territory performance marts
    if not plans.empty and not assignments.empty and not revenue.empty:
        pa = assignments.copy()
        rr = revenue.copy()
        rr["amount"] = pd.to_numeric(rr.get("amount", 0), errors="coerce").fillna(0.0)
        plan_perf = pa.merge(rr, left_on="user_id", right_on="rep_id", how="left")
        grouped = plan_perf.groupby(["plan_id", "period"], as_index=False)["amount"].sum().rename(columns={"amount": "revenue"})
        marts["mart_plan_period_performance"] = {
            "data": grouped,
            "metadata": {
                "row_count": int(len(grouped)),
                "generated_at": _now_iso(),
                "period": "monthly",
                "source_tables": ["plan_assignments", "revenue"],
                "warnings": ["Plan-performance mapping uses user_id↔rep_id alignment from source data"],
                "data_quality_score": 80.0,
            },
        }

    if not uta.empty and not territories.empty and not revenue.empty:
        rr = revenue.copy()
        rr["amount"] = pd.to_numeric(rr.get("amount", 0), errors="coerce").fillna(0.0)
        terr = uta.merge(territories[["id", "name"]], left_on="territory_id", right_on="id", how="left")
        terr_perf = terr.merge(rr, left_on="user_id", right_on="rep_id", how="left")
        grouped = terr_perf.groupby(["territory_id", "name", "period"], as_index=False)["amount"].sum().rename(columns={"amount": "revenue"})
        marts["mart_territory_period_performance"] = {
            "data": grouped,
            "metadata": {
                "row_count": int(len(grouped)),
                "generated_at": _now_iso(),
                "period": "monthly",
                "source_tables": ["user_territory_assignments", "territories", "revenue"],
                "warnings": ["Territory mapping depends on user_id/rep_id alignment in source data"],
                "data_quality_score": 80.0,
            },
        }

    return marts
