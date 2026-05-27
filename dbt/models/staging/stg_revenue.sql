select *
from {{ source('app', 'revenue') }}
