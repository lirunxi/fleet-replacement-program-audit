select unit_no, availability_proxy
from {{ ref('vehicle_metrics') }}
where availability_proxy is not null
  and (availability_proxy < 0 or availability_proxy > 1)
