select
    id as payout_id,
    user_id,
    plan_id,
    period,
    payout_amount,
    commission_rate,
    fallback_used,
    confidence,
    case
        when coalesce(fallback_used, false) then 'fallback_used'
        when coalesce(confidence, 1.0) < 0.8 then 'low_confidence'
        when coalesce(payout_amount, 0) <= 0 then 'negative_or_zero_payout'
        else 'ok'
    end as signal_type
from {{ ref('stg_payouts') }}
