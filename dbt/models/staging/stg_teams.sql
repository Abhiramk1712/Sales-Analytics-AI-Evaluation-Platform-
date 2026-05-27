select *
from {{ source('app', 'teams') }}
