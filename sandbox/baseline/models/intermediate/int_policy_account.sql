-- INT: add the record hash (all columns) used by the incremental / delta sync logic.

select
    src.*,
    {{ record_hash(ref('policy_account')) }} as record_hash
from {{ ref('policy_account') }} as src
