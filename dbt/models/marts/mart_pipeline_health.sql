select
    stage,
    count(*) as deal_count,
    sum(amount) as pipeline_amount,
    sum(amount * coalesce(close_probability, 0) / 100.0) as weighted_pipeline_amount
from {{ ref('stg_deals') }}
group by 1
