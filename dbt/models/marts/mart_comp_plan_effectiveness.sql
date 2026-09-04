with payout_totals as (
    select
        plan_id,
        period,
        sum(payout_amount) as total_payout_amount,
        count(*) as payout_count
    from {{ ref('stg_payouts') }}
    -- payouts.plan_id is nullable — the credit payout engine falls back to
    -- rep-level revenue aggregation (no plan resolved) when SalesCredit rows
    -- don't exist (see backend/payout/credit_payout_engine.py). Those
    -- payouts didn't come from any comp plan being effective or ineffective,
    -- so they don't belong in a per-plan effectiveness report. They're
    -- already tracked on their own terms in int_payout_quality_signals
    -- (signal_type = 'fallback_used').
    where plan_id is not null
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
