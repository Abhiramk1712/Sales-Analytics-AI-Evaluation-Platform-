select *
from {{ source('app', 'sales_units') }}
