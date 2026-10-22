-- Quarantined rows are ignored everywhere else: an unknown SKU must never get a cost version.
select h.sku
from {{ ref('product_cost_history') }} as h
where h.sku not in (select sku from {{ ref('stg_products') }})
