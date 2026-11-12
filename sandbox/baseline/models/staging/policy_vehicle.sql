-- Flatten: POLICY domain -> target vehicle table (one row per vehicle on the policy term).
-- Like the target table, it has no policy number: it links to its policy through payload_id.

select
    md5(pb.policy_number || '|' || strftime(pb.expiration_date, '%Y%m%d') || ':' || cast(pb.vehicle_seq as varchar)) as record_id,
    pb.policy_number || '|' || strftime(pb.expiration_date, '%Y%m%d')                                               as payload_id,
    pb.policy_state                                                                                                 as record_state,
    pb.source_system,
    pb.vehicle_seq,
    pb.vin,
    pb.model_year,
    pb.make
from {{ ref('stg_policy_base') }} as pb
where pb.vehicle_seq is not null
