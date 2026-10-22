-- SCD2 integrity: one row per (sku, valid_from); each version ends exactly where the next one starts
-- (valid_to exclusive), so versions never overlap and never leave gaps.
select sku, valid_from
from {{ ref('product_cost_history') }}
group by sku, valid_from
having count(*) > 1
union all
select sku, valid_from
from (
    select sku, valid_from, valid_to,
           lead(valid_from) over (partition by sku order by valid_from) as next_from
    from {{ ref('product_cost_history') }}
)
where next_from is not null and valid_to is distinct from next_from
