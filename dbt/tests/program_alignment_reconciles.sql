select *
from {{ ref('program_alignment') }}
where
    vehicle_count < 0
    or eligible_denominator <= 0
    or excluded_records <> 0
    or coverage_pct <> 1.0
    or abs(fleet_percentage - vehicle_count::double / eligible_denominator) > 0.000000001
    or high_confidence_availability_count + availability_excluded_records <> vehicle_count
    or abs(
        availability_coverage_pct
        - high_confidence_availability_count::double / nullif(vehicle_count, 0)
    ) > 0.000000001
