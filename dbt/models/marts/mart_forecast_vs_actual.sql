select
    baseline.rep_id,
    reps.name as rep_name,
    baseline.month_period,
    baseline.actual_revenue_amount,
    baseline.forecast_baseline_amount,
    baseline.baseline_error_amount,
    case
        when baseline.forecast_baseline_amount = 0 then null
        else baseline.baseline_error_amount / baseline.forecast_baseline_amount
    end as baseline_error_pct
from {{ ref('int_revenue_forecast_baseline') }} baseline
left join {{ ref('stg_reps') }} reps
    on baseline.rep_id = reps.id
