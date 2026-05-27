select
    plans.id as plan_id,
    plans.name as plan_name,
    count(rules.id) as rule_count,
    max(case when lower(coalesce(rules.name, '')) like '%tier%' then true else false end) as has_tier_rules,
    max(case when coalesce(rules.bonus_amount, 0) > 0 then true else false end) as has_bonus_component
from {{ ref('stg_plans') }} plans
left join {{ ref('stg_rules') }} rules
    on plans.id = rules.plan_id
group by 1, 2
