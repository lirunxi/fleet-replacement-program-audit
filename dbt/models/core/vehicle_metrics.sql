select
    v.*,
    u.last_fuel_date_raw,
    u.last_workorder_open_date_raw,
    u.life_km,
    u.life_hours,
    u.ytd_km,
    u.ytd_hours,
    u.expected_usage_km,
    u.expected_usage_hours,
    u.veu_value,
    u.available_hours,
    u.downtime_hours,
    coalesce(u.invalid_availability, true) as invalid_availability,
    coalesce(u.suspected_source_displacement, false) as suspected_source_displacement,
    coalesce(u.availability_confidence, 'Low') as availability_confidence,
    case
        when v.age is not null and v.expected_life_years is not null
        then v.age / v.expected_life_years
    end as life_consumed_pct,
    case
        when v.age is not null and v.expected_life_years is not null
        then v.expected_life_years - v.age
    end as remaining_life_years,
    case
        when v.age is not null and v.expected_life_years is not null
        then greatest(v.age - v.expected_life_years, 0)
    end as years_overdue,
    case
        when u.available_hours > 0 and u.downtime_hours between 0 and u.available_hours
        then 1 - (u.downtime_hours / u.available_hours)
    end as availability_proxy
from {{ ref('vehicle') }} v
left join {{ ref('usage_snapshot') }} u using (unit_no)
