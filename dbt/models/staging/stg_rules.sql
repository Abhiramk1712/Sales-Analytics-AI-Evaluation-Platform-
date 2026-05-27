select *
from {{ source('app', 'rules') }}
