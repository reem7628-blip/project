-- grain: one row per SKU per cost version (sku, valid_from). SCD2: valid_from inclusive, valid_to exclusive.
--
-- 1. accepted: supplier rows that are NOT quarantined (known SKU, positive cost).
-- 2. latest_file: same SKU + valid_from in several files -> the file whose name sorts last wins (rule 5),
--    so prices_2026-W39_fix.csv replaces prices_2026-W39.csv without any special-casing of names.
-- 3. changes: keep a row only when the cost differs from the SKU's previous quoted cost (rule 6).
--    A product missing from a weekly file has no row, so it simply keeps its previous version (rule 4).
-- 4. valid_to is the next version's valid_from; empty for the newest, which is_current.
with accepted as (
    select s.sku, s.valid_from, round(s.unit_cost_sar, 2) as unit_cost_sar, s.source_file, s.file_row
    from {{ ref('stg_supplier_prices') }} as s
    where s.valid_from is not null
      and not exists (
          select 1
          from {{ ref('price_quarantine') }} as q
          where q.source_file = s.source_file and q.file_row = s.file_row
      )
),
latest_file as (
    select sku, valid_from, unit_cost_sar
    from accepted
    qualify row_number() over (partition by sku, valid_from order by source_file desc, file_row desc) = 1
),
with_previous as (
    select *, lag(unit_cost_sar) over (partition by sku order by valid_from) as previous_cost
    from latest_file
),
changes as (
    select sku, valid_from, unit_cost_sar
    from with_previous
    where previous_cost is null or unit_cost_sar <> previous_cost
)
select
    sku,
    unit_cost_sar,
    valid_from,
    lead(valid_from) over (partition by sku order by valid_from) as valid_to,
    lead(valid_from) over (partition by sku order by valid_from) is null as is_current
from changes
