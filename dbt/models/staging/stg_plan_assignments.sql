select *
from {{ source('app', 'plan_assignments') }}
