-- Flatten: ACCOUNT domain -> target account table.
-- record_id / payload_id are built from source + account number, so the same account
-- gets the same key whichever domain (account or policy) it arrives from.

select
    md5(ab.source_system || ':' || ab.account_number)   as record_id,
    ab.source_system || ':' || ab.account_number        as payload_id,
    ab.account_state                                    as record_state,
    ab.source_system,
    ab.account_number,
    ab.account_name,
    ab.address_line1,
    ab.account_state
from {{ ref('stg_account_base') }} as ab
