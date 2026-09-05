with checks as (
    select 'Invalid age' as issue, invalid_age as failed
    from {{ ref('vehicle_review_features') }}
    union all
    select 'Invalid expected life', invalid_expected_life
    from {{ ref('vehicle_review_features') }}
    union all
    select 'Invalid availability', invalid_availability
    from {{ ref('vehicle_review_features') }}
    union all
    select 'Suspected usage-row displacement', suspected_source_displacement
    from {{ ref('vehicle_review_features') }}
    union all
    select 'Contaminated high-priority field', contaminated_high_priority
    from {{ ref('vehicle_review_features') }}
    union all
    select 'Contaminated maintenance location', contaminated_maintenance_location
    from {{ ref('vehicle_review_features') }}
)
select
    issue,
    count(*) filter (where failed)::bigint as failed_records,
    count(*)::bigint as total_records,
    count(*) filter (where not failed)::double / nullif(count(*), 0) as coverage_pct
from checks
group by issue
