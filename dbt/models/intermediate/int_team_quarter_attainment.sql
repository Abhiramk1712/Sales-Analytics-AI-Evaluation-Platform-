-- Fix grain: aggregate to rep-quarter FIRST, then sum to team-quarter.
-- This prevents triple-counting quarterly quota when joined to monthly revenue.
with rep_quarter as (
    select
        rep_id,
        team_id,
        team_name,
        quarter_period,
        sum(revenue_amount) as quarter_revenue,
        max(quarterly_quota_amount) as quarter_quota
    from {{ ref('int_rep_month_performance') }}
    group by 1, 2, 3, 4
)

select
    team_id,
    coalesce(team_name, 'Unknown Team') as team_name,
    quarter_period,
    sum(quarter_revenue) as team_revenue_amount,
    sum(quarter_quota) as team_quarterly_quota_amount,
    case
        when sum(quarter_quota) = 0 then null
        else sum(quarter_revenue) / sum(quarter_quota)
    end as team_attainment_pct
from rep_quarter
group by 1, 2, 3
