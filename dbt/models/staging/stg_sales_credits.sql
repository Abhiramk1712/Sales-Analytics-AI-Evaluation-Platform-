select *
from {{ source('app', 'sales_credits') }}
