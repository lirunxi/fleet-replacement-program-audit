with grouped as (
    select
        lifecycle_band,
        in_replacement_program,
        count(*)::bigint as vehicle_count,
        count(*) filter (where availability_confidence = 'High')::bigint
            as high_confidence_availability_count,
        avg(availability_proxy) filter (where availability_confidence = 'High')
            as mean_availability_proxy,
        median(availability_proxy) filter (where availability_confidence = 'High')
            as median_availability_proxy
    from {{ ref('vehicle_review_features') }}
    where is_operational
    group by 1, 2
)

select
    lifecycle_band,
    in_replacement_program,
    vehicle_count,
    sum(vehicle_count) over ()::bigint as eligible_denominator,
    vehicle_count::double / nullif(sum(vehicle_count) over (), 0) as fleet_percentage,
    0::bigint as excluded_records,
    1.0::double as coverage_pct,
    high_confidence_availability_count,
    vehicle_count - high_confidence_availability_count as availability_excluded_records,
    high_confidence_availability_count::double / nullif(vehicle_count, 0)
        as availability_coverage_pct,
    mean_availability_proxy,
    median_availability_proxy
from grouped
