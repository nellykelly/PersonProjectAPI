{{ config(alias='account') }}

-- What gets exported to S3 for the target system's account table.
-- Column names must match the target table exactly (record_hash is internal, so it's dropped).

select acc.* exclude (record_hash)
from {{ ref('export_account') }} as acc
where {{ views_filter('acc') }}
