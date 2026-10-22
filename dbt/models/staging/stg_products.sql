-- grain: one row per product (product_id). The SKU is trimmed: two products arrive with trailing spaces.
select
    product_id,
    trim(sku)  as sku,
    category,
    unit_price
from {{ source('raw', 'products') }}
