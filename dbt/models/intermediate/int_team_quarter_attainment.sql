select
    team_id,
    coalesce(team_name, 'Unknown Team') as team_name,
    quarter_period,
    sum(revenue_amount) as team_revenue_amount,
    sum(quarterly_quota_amount) as team_quarterly_quota_amount,
    case
        when sum(quarterly_quota_amount) = 0 then null
        else sum(revenue_amount) / sum(quarterly_quota_amount)
    end as team_attainment_pct
from {{ ref('int_rep_month_performance') }}
group by 1, 2, 3
