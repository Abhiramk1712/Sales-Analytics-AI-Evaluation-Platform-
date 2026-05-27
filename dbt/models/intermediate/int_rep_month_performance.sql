with revenue as (
    select
        rep_id,
        period,
        amount as revenue_amount,
        concat(
            split_part(period, '-', 1),
            '-Q',
            (((split_part(period, '-', 2)::int - 1) / 3) + 1)::int
        ) as quarter_period
    from {{ ref('stg_revenue') }}
),
quotas as (
    select
        rep_id,
        period as quarter_period,
        amount as quota_amount
    from {{ ref('stg_quotas') }}
)
select
    revenue.rep_id,
    reps.name as rep_name,
    reps.team_id,
    teams.name as team_name,
    revenue.period as month_period,
    revenue.quarter_period,
    revenue.revenue_amount,
    coalesce(quotas.quota_amount, 0) as quarterly_quota_amount
from revenue
left join {{ ref('stg_reps') }} reps
    on revenue.rep_id = reps.id
left join {{ ref('stg_teams') }} teams
    on reps.team_id = teams.id
left join quotas
    on revenue.rep_id = quotas.rep_id
    and revenue.quarter_period = quotas.quarter_period
