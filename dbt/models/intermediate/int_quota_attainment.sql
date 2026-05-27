with revenue_quarter as (
    select
        rep_id,
        concat(
            split_part(period, '-', 1),
            '-Q',
            (((split_part(period, '-', 2)::int - 1) / 3) + 1)::int
        ) as quarter_period,
        sum(amount) as revenue_amount
    from {{ ref('stg_revenue') }}
    group by 1, 2
),
quotas as (
    select
        rep_id,
        period as quarter_period,
        amount as quota_amount
    from {{ ref('stg_quotas') }}
)
select
    coalesce(revenue_quarter.rep_id, quotas.rep_id) as rep_id,
    coalesce(revenue_quarter.quarter_period, quotas.quarter_period) as quarter_period,
    coalesce(revenue_quarter.revenue_amount, 0) as revenue_amount,
    coalesce(quotas.quota_amount, 0) as quota_amount,
    case
        when coalesce(quotas.quota_amount, 0) = 0 then null
        else coalesce(revenue_quarter.revenue_amount, 0) / quotas.quota_amount
    end as attainment_pct,
    case
        when coalesce(quotas.quota_amount, 0) = 0 then 'no_quota'
        when coalesce(revenue_quarter.revenue_amount, 0) >= quotas.quota_amount then 'above_plan'
        when coalesce(revenue_quarter.revenue_amount, 0) >= quotas.quota_amount * 0.8 then 'on_track'
        else 'at_risk'
    end as period_status
from revenue_quarter
full outer join quotas
    on revenue_quarter.rep_id = quotas.rep_id
    and revenue_quarter.quarter_period = quotas.quarter_period
