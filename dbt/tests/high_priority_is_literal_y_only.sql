select unit_no
from {{ ref('stg_fleet_units') }}
where high_priority_flag
  and upper(trim(high_priority_raw)) <> 'Y'
