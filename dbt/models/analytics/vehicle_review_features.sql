select
    *,
    age is not null and expected_life_years is not null as valid_lifecycle,
    availability_proxy is not null as valid_availability,
    case
        when age is null or expected_life_years is null then 'Unknown lifecycle'
        when age >= expected_life_years then 'At or beyond expected life'
        when expected_life_years - age > 0
            and expected_life_years - age <= {{ var('planning_window_years', 2) }}
            then 'Due within two years'
        else 'More than two years remaining'
    end as lifecycle_band,
    coalesce(availability_proxy < {{ var('availability_threshold', 0.80) }}, false)
        as low_availability,
    coalesce(
        age >= expected_life_years
        or (
            expected_life_years - age > 0
            and expected_life_years - age <= {{ var('planning_window_years', 2) }}
        ),
        false
    ) as lifecycle_urgent,
    concat_ws(
        '|',
        case when age is null or expected_life_years is null
            then 'INVALID_LIFECYCLE_DATA' end,
        case when availability_proxy is null
            then 'INVALID_AVAILABILITY_DATA' end,
        case when suspected_source_displacement
            then 'SUSPECTED_SOURCE_DISPLACEMENT' end,
        case when invalid_unit_type then 'INVALID_UNIT_TYPE' end,
        case when invalid_fuel then 'INVALID_FUEL' end,
        case when contaminated_high_priority then 'CONTAMINATED_HIGH_PRIORITY' end,
        case when contaminated_maintenance_location
            then 'CONTAMINATED_MAINTENANCE_LOCATION' end
    ) as data_quality_reasons
from {{ ref('vehicle_metrics') }}
