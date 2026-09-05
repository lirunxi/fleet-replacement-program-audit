select unit_no, division_key
from {{ ref('vehicle') }}
where division_key is not null
    and division_key <> upper(trim(division_key))
