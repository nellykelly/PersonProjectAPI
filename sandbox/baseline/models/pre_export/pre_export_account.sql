{{ config(alias='account') }}

-- PRE_EXPORT: an account exists once in the target system, so union the account table from every
-- domain (account, policy; quote later) and keep one row per record_id (account domain wins).

with all_domains as (
    select acc.*, 1 as domain_priority from {{ ref('int_account_account') }} as acc
    union all
    select pac.*, 2 as domain_priority from {{ ref('int_policy_account') }} as pac
)

select ad.* exclude (domain_priority)
from all_domains as ad
qualify row_number() over (partition by ad.record_id order by ad.domain_priority) = 1
