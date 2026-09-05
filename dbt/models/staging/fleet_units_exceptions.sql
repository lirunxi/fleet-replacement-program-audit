select
    trim(r.unit_no) as unit_no,
    r._source_row_number,
    r._duplicate_type,
    'CONFLICTING_DUPLICATE' as exception_reason,
    r._raw_row_hash,
    to_json(r) as raw_record_json
from {{ source('raw', 'fleet_units') }} r
where r._duplicate_type = 'conflicting_duplicate'

union all

select
    s.unit_no,
    s._source_row_number,
    s._duplicate_type,
    concat_ws(
        '|',
        case when invalid_age then 'INVALID_AGE' end,
        case when invalid_expected_life then 'INVALID_EXPECTED_LIFE' end,
        case when invalid_manufacture_year then 'INVALID_MANUFACTURE_YEAR' end,
        case when invalid_unit_type then 'INVALID_UNIT_TYPE' end,
        case when invalid_fuel then 'INVALID_FUEL' end,
        case when contaminated_high_priority then 'CONTAMINATED_HIGH_PRIORITY' end,
        case when contaminated_maintenance_location then 'CONTAMINATED_MAINTENANCE_LOCATION' end
    ) as exception_reason,
    s._raw_row_hash,
    to_json(r) as raw_record_json
from {{ ref('stg_fleet_units') }} s
left join {{ source('raw', 'fleet_units') }} r
    on s._source_row_number = r._source_row_number
    and s._raw_row_hash = r._raw_row_hash
where s.invalid_age
    or s.invalid_expected_life
    or s.invalid_manufacture_year
    or s.invalid_unit_type
    or s.invalid_fuel
    or s.contaminated_high_priority
    or s.contaminated_maintenance_location
