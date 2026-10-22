-- weekly_margin has one row per iso_week x city x category.
select iso_week, city, category
from {{ ref('weekly_margin') }}
group by iso_week, city, category
having count(*) > 1
