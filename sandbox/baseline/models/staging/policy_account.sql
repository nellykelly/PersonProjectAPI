-- Flatten: POLICY domain -> target account table (the accounts that own the policies).
-- The policy base has one row per vehicle, so keep one row per account with QUALIFY (no DISTINCT).

select
    md5(pb.source_system || ':' || pb.account_number)   as record_id,
    pb.source_system || ':' || pb.account_number        as payload_id,
    pb.account_state                                    as record_state,
    pb.source_system,
    pb.account_number,
    pb.account_name,
    pb.address_line1,
    pb.account_state
from {{ ref('stg_policy_base') }} as pb
qualify row_number() over (
    partition by pb.source_system, pb.account_number
    order by pb.policy_number
) = 1
