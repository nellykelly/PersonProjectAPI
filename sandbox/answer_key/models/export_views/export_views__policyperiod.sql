{{ config(alias='policyperiod') }}

-- What gets exported to S3 for the target system's policy period table.

select pp.* exclude (record_hash)
from {{ ref('export_policyperiod') }} as pp
where {{ views_filter('pp', 'policyperiod') }}
