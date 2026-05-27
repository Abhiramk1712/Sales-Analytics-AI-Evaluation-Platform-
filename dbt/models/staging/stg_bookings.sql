select *
from {{ source('app', 'bookings') }}
