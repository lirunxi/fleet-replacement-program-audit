with population as (
    select * from {{ ref('vehicle_review_features') }}
)
select
    'Fleet vehicles' as metric,
    count(*)::bigint as numerator,
    count(*)::bigint as eligible_denominator,
    1.0::double as percentage,
    0::bigint as excluded_records,
    1.0::double as coverage_pct
from population
union all

select
    'Operational vehicles',
    count(*) filter (where is_operational),
    count(*),
    count(*) filter (where is_operational)::double / nullif(count(*), 0),
    0,
    1.0
from population

union all

select
    'In replacement program',
    count(*) filter (where in_replacement_program),
    count(*) filter (where is_operational),
    count(*) filter (where in_replacement_program)::double
        / nullif(count(*) filter (where is_operational), 0),
    count(*) filter (where not is_operational),
    count(*) filter (where is_operational)::double / nullif(count(*), 0)
from population

union all

select
    'Operational lifecycle urgent',
    count(*) filter (where is_operational and lifecycle_urgent),
    count(*) filter (where is_operational and valid_lifecycle),
    count(*) filter (where is_operational and lifecycle_urgent)::double
        / nullif(count(*) filter (where is_operational and valid_lifecycle), 0),
    count(*) filter (where is_operational and not valid_lifecycle),
    count(*) filter (where is_operational and valid_lifecycle)::double
        / nullif(count(*) filter (where is_operational), 0)
from population

union all

select
    'Lifecycle urgent outside program',
    count(*) filter (
        where is_operational and lifecycle_urgent and not in_replacement_program
    ),
    count(*) filter (where is_operational and valid_lifecycle),
    count(*) filter (
        where is_operational and lifecycle_urgent and not in_replacement_program
    )::double / nullif(count(*) filter (where is_operational and valid_lifecycle), 0),
    count(*) filter (where is_operational and not valid_lifecycle),
    count(*) filter (where is_operational and valid_lifecycle)::double
        / nullif(count(*) filter (where is_operational), 0)
from population
