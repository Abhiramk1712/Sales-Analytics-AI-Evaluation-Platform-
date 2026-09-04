select
    plans.id as plan_id,
    plans.name as plan_name,
    count(rules.id) as rule_count,
    -- max(boolean) has no implementation in Postgres (works on warehouses
    -- like Snowflake/BigQuery, which is presumably why this went unnoticed
    -- until dbt was actually run against this project's own database).
    -- bool_or() is the correct Postgres aggregate for "true if any row is
    -- true", which is exactly what this was trying to express.
    bool_or(lower(coalesce(rules.name, '')) like '%tier%') as has_tier_rules,
    bool_or(coalesce(rules.bonus_amount, 0) > 0) as has_bonus_component
from {{ ref('stg_plans') }} plans
left join {{ ref('stg_rules') }} rules
    on plans.id = rules.plan_id
group by 1, 2
