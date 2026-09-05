select
    trim(r.unit_no) as unit_no,
    r._source_row_number,
    r._duplicate_type,
    'CONFLICTING_DUPLICATE' as exception_reason,
    r._raw_row_hash,
    to_json(r) as raw_record_json
from {{ source('raw', 'fleet_usage') }} r
where r._duplicate_type = 'conflicting_duplicate'

union all

select
    s.unit_no,
    s._source_row_number,
    s._duplicate_type,
    concat_ws(
        '|',
        case when invalid_availability then 'INVALID_AVAILABILITY_DATA' end,
        case when suspected_source_displacement then 'SUSPECTED_SOURCE_DISPLACEMENT' end
    ) as exception_reason,
    s._raw_row_hash,
    to_json(r) as raw_record_json
from {{ ref('stg_fleet_usage') }} s
left join {{ source('raw', 'fleet_usage') }} r
    on s._source_row_number = r._source_row_number
    and s._raw_row_hash = r._raw_row_hash
where s.invalid_availability or s.suspected_source_displacement
