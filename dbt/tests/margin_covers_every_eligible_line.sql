-- Point-in-time join must neither lose nor duplicate lines: weekly_margin covers exactly the eligible
-- delivered lines (same line count and same revenue as computed straight from staging).
with expected as (
    select count(*) as lines, round(sum(i.line_revenue_sar), 2) as revenue_sar
    from {{ ref('stg_order_items') }} as i
    join {{ ref('stg_orders') }} as o on o.order_id = i.order_id
    where o.status = 'delivered' and i.quantity > 0
      and o.order_date >= cast('{{ var("margin_start_date") }}' as date)
),
actual as (
    select sum(lines) as lines, round(sum(revenue_sar), 2) as revenue_sar
    from {{ ref('weekly_margin') }}
)
select *
from expected, actual
where expected.lines <> actual.lines or abs(expected.revenue_sar - actual.revenue_sar) > 0.05
