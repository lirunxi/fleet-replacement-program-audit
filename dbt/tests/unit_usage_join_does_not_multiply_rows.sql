select 1
where
    (select count(*) from {{ ref('vehicle_metrics') }})
    <> (select count(*) from {{ ref('vehicle') }})
