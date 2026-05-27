with scored as (
    select
        rep_id,
        period,
        amount as actual_revenue_amount,
        avg(amount) over (
            partition by rep_id
            order by period
            rows between 2 preceding and current row
        ) as forecast_baseline_amount
    from {{ ref('stg_revenue') }}
)
select
    rep_id,
    period as month_period,
    actual_revenue_amount,
    forecast_baseline_amount,
    actual_revenue_amount - forecast_baseline_amount as baseline_error_amount
from scored
