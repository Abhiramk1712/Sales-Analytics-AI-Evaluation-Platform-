select
    quota.rep_id,
    reps.name as rep_name,
    reps.team_id,
    teams.name as team_name,
    quota.quarter_period,
    quota.revenue_amount,
    quota.quota_amount,
    quota.attainment_pct,
    quota.period_status
from {{ ref('int_quota_attainment') }} quota
left join {{ ref('stg_reps') }} reps
    on quota.rep_id = reps.id
left join {{ ref('stg_teams') }} teams
    on reps.team_id = teams.id
