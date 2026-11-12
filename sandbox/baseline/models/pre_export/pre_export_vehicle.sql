{{ config(alias='vehicle') }}

-- PRE_EXPORT: only the policy domain has vehicles today (quotes will be unioned here later).

select veh.*
from {{ ref('int_policy_vehicle') }} as veh
