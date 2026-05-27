# Metrics Dictionary

This dictionary defines representative metrics surfaced by analytics endpoints and dbt marts.

## Revenue and Pipeline Metrics

- `revenue_amount`
  - Definition: Recognized revenue amount for a record.
  - Grain: rep-period or aggregated period.

- `pipeline_amount`
  - Definition: Sum of open deal amounts.
  - Grain: stage/team/global depending on query.

- `weighted_pipeline_amount`
  - Definition: Sum of `deal_amount * close_probability`.
  - Grain: usually stage or team aggregate.

## Quota and Attainment Metrics

- `quota_amount`
  - Definition: Target quota assigned to a rep for a period.
  - Grain: rep-period.

- `attainment_pct`
  - Definition: `revenue_amount / quota_amount` where quota exists.
  - Grain: rep-quarter in dbt intermediate model.

- `period_status`
  - Definition: Attainment classification bucket.
  - Values: `above_plan`, `on_track`, `at_risk`, `no_quota`.

## Payout Metrics

- `payout_amount`
  - Definition: Final payout value for user-plan-period.
  - Grain: payout record.

- `commission_rate`
  - Definition: Commission rate applied in payout calculation.
  - Grain: payout record.

- `readiness_status`
  - Definition: Whether payout is operationally ready.
  - Values: `ready`, `review`.

- `anomaly_type`
  - Definition: Payout quality signal category.
  - Values: `fallback_used`, `low_confidence`, `negative_or_zero_payout`.

## Forecasting Baseline Metrics

- `forecast_baseline_amount`
  - Definition: Rolling 3-period average baseline estimate.
  - Grain: rep-month.

- `baseline_error_amount`
  - Definition: `actual_revenue_amount - forecast_baseline_amount`.
  - Grain: rep-month.

- `baseline_error_pct`
  - Definition: `baseline_error_amount / forecast_baseline_amount` where denominator exists.
  - Grain: rep-month.
