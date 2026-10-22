-- grain: one row per iso_week x city x category that has at least one counted order line.
--
-- A line counts when: order delivered, quantity > 0, Riyadh order date >= margin_start_date (rules 7-8).
-- Point-in-time costing (rule 9): the line joins the cost version with valid_from <= order_date < valid_to.
-- Versions of one SKU never overlap, so the join cannot duplicate a line (tested in tests/).
-- Lines with no valid version are "uncosted": they count in lines and revenue_sar, not in the cost columns.
with lines as (
    select
        o.order_date,
        st.city,
        p.category,
        p.sku,
        i.quantity,
        i.line_revenue_sar
    from {{ ref('stg_order_items') }} as i
    join {{ ref('stg_orders') }} as o on o.order_id = i.order_id
    left join {{ ref('stg_stores') }} as st on st.store_id = o.store_id
    left join {{ ref('stg_products') }} as p on p.product_id = i.product_id
    where o.status = 'delivered'
      and i.quantity > 0
      and o.order_date >= cast('{{ var("margin_start_date") }}' as date)
),
costed as (
    select
        l.*,
        h.unit_cost_sar,
        h.unit_cost_sar is not null          as is_costed,
        l.quantity * h.unit_cost_sar         as line_cost_sar
    from lines as l
    left join {{ ref('product_cost_history') }} as h
        on h.sku = l.sku
       and l.order_date >= h.valid_from
       and (h.valid_to is null or l.order_date < h.valid_to)
),
agg as (
    select
        week(order_date)                                                          as iso_week,
        city,
        category,
        count(*)                                                                  as lines,
        round(sum(line_revenue_sar), 2)                                           as revenue_sar,
        round(coalesce(sum(line_revenue_sar) filter (where is_costed), 0), 2)     as costed_revenue_sar,
        round(coalesce(sum(line_cost_sar), 0), 2)                                 as cost_sar,
        count(*) filter (where not is_costed)                                     as uncosted_lines
    from costed
    group by 1, 2, 3
)
select
    cast(iso_week as integer)                          as iso_week,
    city,
    category,
    cast(lines as integer)                             as lines,
    revenue_sar,
    costed_revenue_sar,
    cost_sar,
    round(costed_revenue_sar - cost_sar, 2)            as margin_sar,
    cast(uncosted_lines as integer)                    as uncosted_lines
from agg
