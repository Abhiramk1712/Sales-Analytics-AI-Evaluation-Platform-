# dbt Lineage (Minimum Layer)

This project includes a lightweight dbt layer under `dbt/` for portfolio-grade analytics modeling.

## Source Layer

Source namespace: `app`

Main sources:

- teams, reps, accounts, deals
- revenue, quotas
- plans, rules, plan_assignments
- sales_units, sales_credits, payouts
- bookings, churn_events

## Staging Layer

Models: `stg_*`

Purpose:

- Provide direct, clean references to source tables.
- Establish testable model contracts (`not_null`, `unique`, `relationships`, `accepted_values`).

## Intermediate Layer

Models: `int_*`

Key transformations:

- `int_rep_month_performance`
- `int_team_quarter_attainment`
- `int_quota_attainment`
- `int_plan_rule_coverage`
- `int_payout_quality_signals`
- `int_revenue_forecast_baseline`

## Mart Layer

Models: `mart_*`

Consumer-ready outputs:

- `mart_exec_kpis`
- `mart_rep_performance`
- `mart_payout_readiness`
- `mart_forecast_vs_actual`
- `mart_comp_plan_effectiveness`
- `mart_pipeline_health`
- `mart_payout_anomalies`

## Quick Commands

From project root:

```bash
cd dbt
# copy profiles.example.yml into your active dbt profiles.yml location
# then run:
dbt debug
dbt run
dbt test
```

Note: This dbt layer is additive. It does not replace existing Python ETL/runtime logic.
