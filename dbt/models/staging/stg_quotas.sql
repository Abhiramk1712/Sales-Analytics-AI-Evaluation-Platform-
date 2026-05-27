select *
from {{ source('app', 'quotas') }}
