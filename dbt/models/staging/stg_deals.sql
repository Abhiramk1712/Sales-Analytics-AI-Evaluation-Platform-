select *
from {{ source('app', 'deals') }}
