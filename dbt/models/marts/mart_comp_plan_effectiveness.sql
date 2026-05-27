with payout_totals as (
    select
        plan_id,
        period,
        sum(payout_amount) as total_payout_amount,
        count(*) as payout_count
    from {{ ref('stg_payouts') }}
    group by 1, 2
)
select
    payout_totals.plan_id,
    plans.name as plan_name,
    payout_totals.period,
    payout_totals.total_payout_amount,
    payout_totals.payout_count,
    coverage.rule_count,
    coverage.has_tier_rules,
    coverage.has_bonus_component
from payout_totals
left join {{ ref('stg_plans') }} plans
    on payout_totals.plan_id = plans.id
left join {{ ref('int_plan_rule_coverage') }} coverage
    on payout_totals.plan_id = coverage.plan_id
