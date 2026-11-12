{{ config(alias='policyperiod') }}

-- PRE_EXPORT: only the policy domain has policy periods today (quotes will be unioned here later).

select pp.*
from {{ ref('int_policy_policyperiod') }} as pp
