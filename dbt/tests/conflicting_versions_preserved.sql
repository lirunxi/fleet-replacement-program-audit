with checks as (
    select
        'fleet_units' as source_name,
        (
            select count(*)
            from {{ source('raw', 'fleet_units') }}
            where _duplicate_type = 'conflicting_duplicate'
        ) as raw_conflicting_rows,
        (
            select count(*)
            from {{ ref('fleet_units_exceptions') }}
            where exception_reason = 'CONFLICTING_DUPLICATE'
        ) as preserved_conflicting_rows,
        (
            select count(*)
            from {{ ref('fleet_units_exceptions') }}
            where exception_reason = 'CONFLICTING_DUPLICATE'
                and raw_record_json is null
        ) as missing_raw_records

    union all

    select
        'fleet_usage' as source_name,
        (
            select count(*)
            from {{ source('raw', 'fleet_usage') }}
            where _duplicate_type = 'conflicting_duplicate'
        ) as raw_conflicting_rows,
        (
            select count(*)
            from {{ ref('fleet_usage_exceptions') }}
            where exception_reason = 'CONFLICTING_DUPLICATE'
        ) as preserved_conflicting_rows,
        (
            select count(*)
            from {{ ref('fleet_usage_exceptions') }}
            where exception_reason = 'CONFLICTING_DUPLICATE'
                and raw_record_json is null
        ) as missing_raw_records
)
select *
from checks
where raw_conflicting_rows <> preserved_conflicting_rows
    or missing_raw_records > 0
