with division_label_counts as (
    select
        division_key,
        division_raw,
        count(*) as label_count
    from {{ ref('stg_fleet_units') }}
    where division_key is not null and division_raw is not null
    group by division_key, division_raw
),
division_labels as (
    select division_key, division_raw as division_display
    from division_label_counts
    qualify row_number() over (
        partition by division_key
        order by label_count desc, division_raw
    ) = 1
)
select
    v.unit_no,
    v.division_key,
    coalesce(d.division_display, v.division_raw) as division,
    v.division_raw,
    make,
    model,
    category,
    category_description,
    category_class,
    category_group,
    category_group_description,
    unit_type,
    unit_type_raw,
    fuel,
    fuel_raw,
    status_key,
    status_raw,
    current_status,
    is_operational,
    in_replacement_program,
    high_priority_flag,
    high_priority_raw,
    manufacture_year,
    age,
    expected_life_years,
    in_service_date_raw,
    owning_cost_center,
    using_cost_center,
    maintenance_location,
    maintenance_location_raw,
    park_location,
    park_location_name,
    billing_code,
    maintenance_classification_code,
    tech_spec,
    tech_spec_description,
    invalid_manufacture_year,
    invalid_age,
    invalid_expected_life,
    invalid_unit_type,
    invalid_fuel,
    invalid_status,
    contaminated_high_priority,
    contaminated_maintenance_location,
    age_year_warning,
    _source_row_number,
    _raw_row_hash
from {{ ref('stg_fleet_units') }} v
left join division_labels d using (division_key)
where not exists (
    select 1
    from {{ source('raw', 'fleet_usage') }} u
    where trim(u.unit_no) = v.unit_no
        and u._duplicate_type = 'conflicting_duplicate'
)
