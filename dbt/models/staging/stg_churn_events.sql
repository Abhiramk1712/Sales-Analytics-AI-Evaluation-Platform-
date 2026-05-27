select *
from {{ source('app', 'churn_events') }}
