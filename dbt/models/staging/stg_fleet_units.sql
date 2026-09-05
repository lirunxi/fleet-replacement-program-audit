with ranked as (
    select
        *,
        row_number() over (
            partition by trim(unit_no)
            order by _source_row_number
        ) as analytical_row_number
    from {{ source('raw', 'fleet_units') }}
    where _duplicate_type <> 'conflicting_duplicate'
),
deduplicated as (
    select * exclude (analytical_row_number)
    from ranked
    where analytical_row_number = 1
),
normalized as (
    select
        trim(unit_no) as unit_no,
        upper(nullif(trim(division), '')) as division_key,
        nullif(trim(division), '') as division_raw,
        nullif(trim(make), '') as make,
        nullif(trim(model), '') as model,
        nullif(trim(category), '') as category,
        nullif(trim(category_desc), '') as category_description,
        nullif(trim(category_class), '') as category_class,
        nullif(trim(category_group), '') as category_group,
        nullif(trim(category_group_desc), '') as category_group_description,
        upper(trim(unit_type)) as unit_type_key,
        nullif(trim(unit_type), '') as unit_type_raw,
        upper(trim(fuel_product)) as fuel_key,
        nullif(trim(fuel_product), '') as fuel_raw,
        upper(trim(current_status_description)) as status_key,
        nullif(trim(current_status_description), '') as status_raw,
        nullif(trim(maintenenace_location_name), '') as maintenance_location_raw,
        upper(trim(maintenenace_location_name)) as maintenance_location_key,
        case when upper(trim(high_priority)) = 'Y' then true else false end as high_priority_flag,
        nullif(trim(high_priority), '') as high_priority_raw,
        try_cast(trim(year) as integer) as manufacture_year_candidate,
        try_cast(trim(age) as double) as age_candidate,
        try_cast(trim(expected_life_yr) as double) as expected_life_candidate,
        nullif(trim(in_service_date), '') as in_service_date_raw,
        nullif(trim(owning_cost_center), '') as owning_cost_center,
        nullif(trim(using_cost_center), '') as using_cost_center,
        nullif(trim(park_location), '') as park_location,
        nullif(trim(park_location_name), '') as park_location_name,
        nullif(trim(billing_code), '') as billing_code,
        nullif(trim(maintenance_classification_code), '') as maintenance_classification_code,
        nullif(trim(tech_spec), '') as tech_spec,
        nullif(trim(tech_spec_desc), '') as tech_spec_description,
        _source_file,
        _source_row_number,
        _source_sha256,
        _ingested_at,
        _raw_row_hash,
        _duplicate_count,
        _duplicate_type
    from deduplicated
)
select
    n.* exclude (
        unit_type_key,
        fuel_key,
        status_key,
        division_key,
        manufacture_year_candidate,
        age_candidate,
        expected_life_candidate,
        maintenance_location_key
    ),
    n.division_key,
    n.status_key,
    s.status_label as current_status,
    coalesce(s.is_operational = 1, false) as is_operational,
    coalesce(s.is_replacement_program = 1, false) as in_replacement_program,
    case
        when n.unit_type_key in ('LIGHT DUTY', 'MEDIUM DUTY', 'HEAVY DUTY', 'OFF-ROAD', 'OTHER')
            then n.unit_type_key
    end as unit_type,
    case
        when n.fuel_key in (
            'DIESEL', 'DIESEL / DYED DIESEL', 'DYED DIESEL', 'ELECTRIC',
            'NATURAL GAS', 'PRODUCT N/A', 'PROPANE', 'SOLAR POWER',
            'UNLEADED', 'UNLEADED / DIESEL', 'UNLEADED / DYED DIESEL'
        ) then n.fuel_key
    end as fuel,
    case
        when n.maintenance_location_key in (
            'ACTIVE UNIT', 'REDEPLOYED UNIT', 'REPLACEMENT PROGRAM',
            'UNIT AT AUCTION TO BE SOLD', 'UNIT IS TOTALED',
            'UNIT RETURN FOR DISP/SALE/REM', 'UNIT SOLD',
            'LIGHT DUTY', 'MEDIUM DUTY', 'HEAVY DUTY', 'OFF-ROAD', 'OTHER',
            'DIESEL', 'ELECTRIC', 'NATURAL GAS', 'PROPANE', 'UNLEADED'
        ) then null
        else n.maintenance_location_raw
    end as maintenance_location,
    case
        when n.manufacture_year_candidate between 1900 and 2027
            then n.manufacture_year_candidate
    end as manufacture_year,
    case when n.age_candidate between 0 and 80 then n.age_candidate end as age,
    case
        when n.expected_life_candidate between 1 and 50 then n.expected_life_candidate
    end as expected_life_years,
    n.manufacture_year_candidate is null
        or n.manufacture_year_candidate not between 1900 and 2027 as invalid_manufacture_year,
    n.age_candidate is null or n.age_candidate not between 0 and 80 as invalid_age,
    n.expected_life_candidate is null
        or n.expected_life_candidate not between 1 and 50 as invalid_expected_life,
    n.unit_type_key not in (
        'LIGHT DUTY', 'MEDIUM DUTY', 'HEAVY DUTY', 'OFF-ROAD', 'OTHER'
    ) as invalid_unit_type,
    n.fuel_key not in (
        'DIESEL', 'DIESEL / DYED DIESEL', 'DYED DIESEL', 'ELECTRIC',
        'NATURAL GAS', 'PRODUCT N/A', 'PROPANE', 'SOLAR POWER',
        'UNLEADED', 'UNLEADED / DIESEL', 'UNLEADED / DYED DIESEL'
    ) as invalid_fuel,
    n.status_key not in (
        'ACTIVE UNIT', 'REDEPLOYED UNIT', 'REPLACEMENT PROGRAM',
        'UNIT AT AUCTION TO BE SOLD', 'UNIT IS TOTALED',
        'UNIT RETURN FOR DISP/SALE/REM', 'UNIT SOLD'
    ) as invalid_status,
    n.high_priority_raw is not null
        and upper(trim(n.high_priority_raw)) <> 'Y' as contaminated_high_priority,
    n.maintenance_location_raw is not null and n.maintenance_location_key in (
        'ACTIVE UNIT', 'REDEPLOYED UNIT', 'REPLACEMENT PROGRAM',
        'UNIT AT AUCTION TO BE SOLD', 'UNIT IS TOTALED',
        'UNIT RETURN FOR DISP/SALE/REM', 'UNIT SOLD',
        'LIGHT DUTY', 'MEDIUM DUTY', 'HEAVY DUTY', 'OFF-ROAD', 'OTHER',
        'DIESEL', 'ELECTRIC', 'NATURAL GAS', 'PROPANE', 'UNLEADED'
    ) as contaminated_maintenance_location,
    case
        when n.age_candidate between 0 and 80
            and n.manufacture_year_candidate between 1900 and 2027
            and abs(n.age_candidate - (2027 - n.manufacture_year_candidate)) > 2
        then true else false
    end as age_year_warning
from normalized n
left join {{ ref('status_mapping') }} s
    on n.status_key = s.status_key
