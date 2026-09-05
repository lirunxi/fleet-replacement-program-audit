select unit_no
from {{ ref('vehicle_review_features') }}
where availability_proxy is not null
    and (
        (availability_proxy < 0.80 and not low_availability)
        or (availability_proxy >= 0.80 and low_availability)
    )
