-- grain: one row per order (order_id).
-- created_at is a UTC timestamp stored as text. The order date is the calendar day in Riyadh (UTC+3).
select
    order_id,
    store_id,
    status,
    cast(created_at as timestamp)                                                        as created_at_utc,
    cast(cast(created_at as timestamp) + interval '{{ var("riyadh_utc_offset_hours") }} hours' as date) as order_date
from {{ source('raw', 'orders') }}
