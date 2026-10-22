-- grain: one row per order line (order_id, line_no).
select
    order_id,
    line_no,
    product_id,
    quantity,
    unit_price,
    discount,
    round(quantity * unit_price - discount, 2) as line_revenue_sar   -- BRIEF rule 8
from {{ source('raw', 'order_items') }}
