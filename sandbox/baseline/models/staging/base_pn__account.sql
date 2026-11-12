-- Pinnacle account base query: one row per household.

select
    'PINNACLE'                          as source_system,
    cast(fam.household_nbr as varchar)      as account_number,
    fam.first_nm || ' ' || fam.last_nm   as account_name,
    fam.addr_line_1                      as address_line1,
    {{ state_abbr('fam.st_cd') }}        as account_state
from {{ source('raw', 'pn_household') }} as fam
