select unit_no
from {{ ref('vehicle_review_features') }}
where valid_lifecycle
    and (
        (
            age = expected_life_years
            and lifecycle_band <> 'At or beyond expected life'
        )
        or (
            age < expected_life_years
            and expected_life_years - age = 2
            and lifecycle_band <> 'Due within two years'
        )
        or (
            expected_life_years - age > 2
            and lifecycle_band <> 'More than two years remaining'
        )
    )
