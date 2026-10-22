-- grain: one row per supplier file row (source_file, file_row). Nothing is dropped here: bad rows are
-- classified downstream (quality.price_quarantine), so the counts always reconcile with the raw files.
--
-- Cost parsing (BRIEF rule 1):
--   * the column is unit_cost until week 38 and cost_sar from week 39: coalesce picks whichever the row has
--   * Arabic-Indic digits (٠-٩), Persian digits (۰-۹) and the Arabic decimal separator ٫ become 0-9 and '.'
--   * the Arabic thousands separator ٬ is removed; a decimal comma (9,49) becomes a point
--   * anything that still isn't a number gives NULL (try_cast), which later becomes `invalid_cost`
with src as (
    select
        source_file,
        file_row,
        supplier_id,
        trim(sku)                                                            as sku,
        try_cast(trim(valid_from) as date)                                   as valid_from,
        coalesce(nullif(trim(unit_cost), ''), nullif(trim(cost_sar), ''))    as raw_cost
    from {{ source('raw', 'supplier_prices') }}
),
normalized as (
    select
        *,
        replace(
            regexp_replace(
                translate(raw_cost, '٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬', '01234567890123456789.'),
                '[\s\x{00A0}]', '', 'g'),
            ',', '.')                                                        as cost_text
    from src
)
select
    source_file,
    file_row,
    supplier_id,
    sku,
    valid_from,
    raw_cost,
    try_cast(cost_text as double)                                            as unit_cost_sar
from normalized
