with conflicting_ids as (
    select distinct trim(unit_no) as unit_no
    from {{ source('raw', 'fleet_units') }}
    where _duplicate_type = 'conflicting_duplicate'

    union

    select distinct trim(unit_no) as unit_no
    from {{ source('raw', 'fleet_usage') }}
    where _duplicate_type = 'conflicting_duplicate'
)
select features.unit_no
from {{ ref('vehicle_review_features') }} features
inner join conflicting_ids using (unit_no)
