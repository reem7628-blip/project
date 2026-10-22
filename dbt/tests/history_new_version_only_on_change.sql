-- A new version starts only when the cost changes, and every cost is a positive number.
select sku, valid_from
from (
    select sku, valid_from, unit_cost_sar,
           lag(unit_cost_sar) over (partition by sku order by valid_from) as previous_cost
    from {{ ref('product_cost_history') }}
)
where unit_cost_sar = previous_cost or unit_cost_sar <= 0 or not isfinite(unit_cost_sar)
