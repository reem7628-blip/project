-- margin = costed revenue - cost; costed revenue never exceeds revenue; uncosted lines never exceed lines.
select *
from {{ ref('weekly_margin') }}
where abs(costed_revenue_sar - cost_sar - margin_sar) > 0.011
   or costed_revenue_sar > revenue_sar + 0.011
   or uncosted_lines > lines
   or uncosted_lines < 0
