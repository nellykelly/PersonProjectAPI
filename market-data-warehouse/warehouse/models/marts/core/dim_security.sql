{#
  One row per instrument, current attributes (SCD type 1). History is
  captured by snapshots/security_info_snapshot.sql, so an SCD-2 variant
  can be layered on later without changing the ingestion.
#}

with info as (
    select * from {{ ref('stg_security_info') }}
),

exch as (
    select * from {{ ref('dim_exchange') }}
),

final as (
    select
        {{ dbt_utils.generate_surrogate_key(['info.ticker']) }}        as security_key,
        info.ticker,
        coalesce(info.long_name, info.short_name, info.ticker)         as security_name,
        info.short_name,
        info.quote_type,
        info.exchange_code,
        coalesce(exch.exchange_key,
                 {{ dbt_utils.generate_surrogate_key(["'UNKNOWN'"]) }}) as exchange_key,
        info.currency,
        info.sector,
        info.industry,
        info.country,
        info.ingested_at                                              as source_loaded_at
    from info
    left join exch
        on upper(info.exchange_code) = upper(exch.exchange_code)
)

select * from final
