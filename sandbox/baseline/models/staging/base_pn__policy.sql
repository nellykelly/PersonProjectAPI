-- Pinnacle Pinnacle auto policy base query.
-- One row per policy x vehicle, with the owning account's details (several entities in one row).
-- Active-policy rule: take the latest term of each policy, keep it only if that term is active ('A').

with latest_terms as (
    select
        pol.pol_nbr,
        pol.term_nbr,
        pol.household_nbr,
        pol.st_cd,
        pol.pol_eff_dt,
        pol.pol_exp_dt,
        pol.pol_sts_cd
    from {{ source('raw', 'pn_policy') }} as pol
    qualify row_number() over (partition by pol.pol_nbr order by pol.term_nbr desc) = 1
),

active_policies as (
    select lt.*
    from latest_terms as lt
    where lt.pol_sts_cd = 'A'
),

lob_map as (
    select lm.legacy_lob, lm.gw_lob
    from {{ ref('lob_map') }} as lm
    where lm.source_system = 'PINNACLE'
)

select
    'PINNACLE'                          as source_system,
    ap.pol_nbr                           as policy_number,
    cast(ap.household_nbr as varchar)       as account_number,
    fam.first_nm || ' ' || fam.last_nm   as account_name,
    fam.addr_line_1                      as address_line1,
    {{ state_abbr('fam.st_cd') }}        as account_state,
    lm.gw_lob                            as lob,
    {{ state_abbr('ap.st_cd') }}         as policy_state,
    ap.pol_eff_dt                        as effective_date,
    ap.pol_exp_dt                        as expiration_date,
    'InForce'                            as policy_status,
    veh.veh_seq                          as vehicle_seq,
    veh.vin                              as vin,
    veh.mdl_yr                           as model_year,
    veh.make_nm                          as make
from active_policies as ap
inner join {{ source('raw', 'pn_household') }} as fam
    on fam.household_nbr = ap.household_nbr
inner join lob_map as lm
    on lm.legacy_lob = 'PAUTO'
left join {{ source('raw', 'pn_vehicle') }} as veh
    on veh.pol_nbr = ap.pol_nbr
