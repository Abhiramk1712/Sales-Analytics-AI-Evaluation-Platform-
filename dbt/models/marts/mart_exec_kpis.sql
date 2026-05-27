with quota_attainment as (
    select
        quarter_period,
        sum(revenue_amount) as total_revenue_amount,
        sum(quota_amount) as total_quota_amount
    from {{ ref('int_quota_attainment') }}
    group by 1
),
payouts as (
    select
        period as quarter_period,
        sum(payout_amount) as total_payout_amount
    from {{ ref('stg_payouts') }}
    group by 1
)
select
    quota_attainment.quarter_period,
    quota_attainment.total_revenue_amount,
    quota_attainment.total_quota_amount,
    case
        when quota_attainment.total_quota_amount = 0 then null
        else quota_attainment.total_revenue_amount / quota_attainment.total_quota_amount
    end as quota_attainment_pct,
    coalesce(payouts.total_payout_amount, 0) as total_payout_amount
from quota_attainment
left join payouts
    on quota_attainment.quarter_period = payouts.quarter_period
