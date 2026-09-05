with ranked as (
    select
        *,
        row_number() over (
            partition by trim(unit_no)
            order by _source_row_number
        ) as analytical_row_number
    from {{ source('raw', 'fleet_usage') }}
    where _duplicate_type <> 'conflicting_duplicate'
),
deduplicated as (
    select * exclude (analytical_row_number)
    from ranked
    where analytical_row_number = 1
),
typed as (
    select
        trim(unit_no) as unit_no,
        nullif(trim(last_fuel_date), '') as last_fuel_date_raw,
        nullif(trim(last_workorder_open_date), '') as last_workorder_open_date_raw,
        try_cast(replace(trim(m5_life_km_usage_km), ',', '') as double) as life_km,
        try_cast(replace(trim(m5_life_hrs_usage_hrs), ',', '') as double) as life_hours,
        try_cast(replace(trim(m5_ytd_km_usage_km), ',', '') as double) as ytd_km,
        try_cast(replace(trim(m5_ytd_hrs_usage_hrs), ',', '') as double) as ytd_hours,
        try_cast(replace(trim(available_hours_hrs), ',', '') as double) as available_hours_candidate,
        try_cast(replace(trim(downtime_hrs), ',', '') as double) as downtime_hours_candidate,
        try_cast(replace(trim(expect_usage_km), ',', '') as double) as expected_usage_km,
        try_cast(replace(trim(expect_usage_hrs), ',', '') as double) as expected_usage_hours,
        try_cast(replace(trim(veu), ',', '') as double) as veu_value,
        regexp_matches(coalesce(m5_life_km_usage_km, ''), '[A-Za-z]')
            or regexp_matches(coalesce(m5_life_hrs_usage_hrs, ''), '[A-Za-z]')
            or regexp_matches(coalesce(m5_ytd_km_usage_km, ''), '[A-Za-z]')
            or regexp_matches(coalesce(m5_ytd_hrs_usage_hrs, ''), '[A-Za-z]')
            or regexp_matches(coalesce(available_hours_hrs, ''), '[A-Za-z]')
            or regexp_matches(coalesce(downtime_hrs, ''), '[A-Za-z]')
            or regexp_matches(coalesce(expect_usage_km, ''), '[A-Za-z]')
            or regexp_matches(coalesce(expect_usage_hrs, ''), '[A-Za-z]')
            or regexp_matches(coalesce(veu, ''), '[A-Za-z]')
            as suspected_source_displacement,
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
    * exclude (available_hours_candidate, downtime_hours_candidate),
    case
        when available_hours_candidate > 0
            and downtime_hours_candidate between 0 and available_hours_candidate
        then available_hours_candidate
    end as available_hours,
    case
        when available_hours_candidate > 0
            and downtime_hours_candidate between 0 and available_hours_candidate
        then downtime_hours_candidate
    end as downtime_hours,
    available_hours_candidate is null
        or downtime_hours_candidate is null
        or available_hours_candidate <= 0
        or downtime_hours_candidate < 0
        or downtime_hours_candidate > available_hours_candidate
        as invalid_availability,
    case
        when available_hours_candidate is null
            or downtime_hours_candidate is null
            or available_hours_candidate <= 0
            or downtime_hours_candidate < 0
            or downtime_hours_candidate > available_hours_candidate
        then 'Low'
        when suspected_source_displacement then 'Medium'
        else 'High'
    end as availability_confidence
from typed
