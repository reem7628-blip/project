-- grain: one row per rejected supplier row (source_file, file_row).
-- BRIEF rule 3, in this order: unknown SKU first, then a cost that isn't a positive number.
select
    s.source_file,
    s.file_row,
    s.sku,
    s.valid_from,
    s.raw_cost,
    case
        when p.sku is null then 'unknown_sku'
        else 'invalid_cost'
    end as reason
from {{ ref('stg_supplier_prices') }} as s
left join (select distinct sku from {{ ref('stg_products') }}) as p on p.sku = s.sku
where p.sku is null
   or s.unit_cost_sar is null
   or not isfinite(s.unit_cost_sar)
   or s.unit_cost_sar <= 0
