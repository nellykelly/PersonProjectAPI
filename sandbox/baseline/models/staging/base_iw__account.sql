-- Ironway account base query: one row per legacy account.

select
    'IRONWAY'               as source_system,
    trim(acc.acct_no)       as account_number,
    trim(acc.insd_name)     as account_name,
    trim(acc.street)        as address_line1,
    acc.state               as account_state
from {{ source('raw', 'iw_account') }} as acc
