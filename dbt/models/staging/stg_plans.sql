select *
from {{ source('app', 'plans') }}
