{{ config(alias='vehicle') }}

-- What gets exported to S3 for the target system's vehicle table.

select veh.* exclude (record_hash)
from {{ ref('export_vehicle') }} as veh
where {{ views_filter('veh') }}
