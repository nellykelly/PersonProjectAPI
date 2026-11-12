-- Flatten: POLICY domain -> target policy period table (one row per policy term).
-- Payload = policy number + expiration date: everything for this term migrates together.

select
    md5(pb.source_system || ':' || pb.policy_number || ':' || strftime(pb.expiration_date, '%Y%m%d')) as record_id,
    pb.policy_number || '|' || strftime(pb.expiration_date, '%Y%m%d')                                 as payload_id,
    pb.policy_state                                                                                   as record_state,
    pb.source_system,
    pb.policy_number,
    pb.account_number,
    pb.lob,
    pb.effective_date,
    pb.expiration_date,
    pb.policy_status
from {{ ref('stg_policy_base') }} as pb
qualify row_number() over (
    partition by pb.source_system, pb.policy_number
    order by pb.vehicle_seq
) = 1
