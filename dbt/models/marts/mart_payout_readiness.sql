select
    payouts.id as payout_id,
    payouts.user_id,
    payouts.plan_id,
    payouts.period,
    payouts.payout_amount,
    payouts.commission_rate,
    signals.signal_type,
    case
        when signals.signal_type = 'ok' then 'ready'
        else 'review'
    end as readiness_status
from {{ ref('stg_payouts') }} payouts
left join {{ ref('int_payout_quality_signals') }} signals
    on payouts.id = signals.payout_id
