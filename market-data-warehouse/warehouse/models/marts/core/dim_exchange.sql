{#
  Conformed exchange dimension. Every exchange_code seen in the security
  metadata, enriched from the seed; anything not in the seed maps to a
  single UNKNOWN member so facts never lose a row on the join.
#}

with seed as (
    select * from {{ ref('seed_exchange') }}
),

seen as (
    select distinct exchange_code
    from {{ ref('stg_security_info') }}
    where exchange_code is not null
),

mapped as (
    select
        seen.exchange_code,
        coalesce(seed.mic, 'UNKNOWN')                     as mic,
        coalesce(seed.exchange_name, 'Unmapped exchange') as exchange_name,
        seed.country,
        seed.timezone_name
    from seen
    left join seed
        on upper(seen.exchange_code) = upper(seed.exchange_code)
),

with_sentinel as (
    select exchange_code, mic, exchange_name, country, timezone_name from mapped
    union
    select 'UNKNOWN', 'UNKNOWN', 'Unmapped exchange',
           cast(null as varchar), cast(null as varchar)
)

select
    {{ dbt_utils.generate_surrogate_key(['exchange_code']) }} as exchange_key,
    exchange_code,
    mic,
    exchange_name,
    country,
    timezone_name
from with_sentinel
