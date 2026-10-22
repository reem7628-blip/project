-- Every SKU has exactly one current version, and valid_to is empty exactly for it.
select sku
from {{ ref('product_cost_history') }}
group by sku
having count(*) filter (where is_current) <> 1
    or count(*) filter (where valid_to is null) <> 1
union all
select sku from {{ ref('product_cost_history') }} where is_current <> (valid_to is null)
