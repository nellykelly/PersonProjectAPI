-- Ironway (legacy) policy base query.
-- One row per policy x vehicle unit, with the owning account's details.
-- Legacy quirks: dates are YYYYMMDD text (Snowflake: to_date(col, 'YYYYMMDD')), term is a '01'/'02' suffix.
-- Active-policy rule: latest term suffix of each policy, kept only if it is in force ('IF').

with latest_terms as (
    select
        pol.policy_no,
        pol.term_sfx,
        pol.acct_no,
        pol.state,
        pol.lob,
        pol.eff_yyyymmdd,
        pol.exp_yyyymmdd,
        pol.status
    from {{ source('raw', 'iw_policy') }} as pol
    qualify row_number() over (partition by pol.policy_no order by pol.term_sfx desc) = 1
),

in_force_policies as (
    select lt.*
    from latest_terms as lt
    where lt.status = 'IF'
),

lob_map as (
    select lm.legacy_lob, lm.gw_lob
    from {{ ref('lob_map') }} as lm
    where lm.source_system = 'IRONWAY'
)

select
    'IRONWAY'                                             as source_system,
    ifp.policy_no                                         as policy_number,
    trim(ifp.acct_no)                                     as account_number,
    trim(acc.insd_name)                                   as account_name,
    trim(acc.street)                                      as address_line1,
    acc.state                                             as account_state,
    lm.gw_lob                                             as lob,
    ifp.state                                             as policy_state,
    cast(strptime(ifp.eff_yyyymmdd, '%Y%m%d') as date)    as effective_date,
    cast(strptime(ifp.exp_yyyymmdd, '%Y%m%d') as date)    as expiration_date,
    'InForce'                                             as policy_status,
    veh.unit_no                                           as vehicle_seq,
    veh.vin                                               as vin,
    veh.yr                                                as model_year,
    veh.make                                              as make
from in_force_policies as ifp
inner join {{ source('raw', 'iw_account') }} as acc
    on trim(acc.acct_no) = trim(ifp.acct_no)
inner join lob_map as lm
    on lm.legacy_lob = ifp.lob
left join {{ source('raw', 'iw_vehicle') }} as veh
    on veh.policy_no = ifp.policy_no
