select
    payout_id,
    user_id,
    plan_id,
    period,
    payout_amount,
    commission_rate,
    signal_type as anomaly_type
from {{ ref('int_payout_quality_signals') }}
where signal_type <> 'ok'
