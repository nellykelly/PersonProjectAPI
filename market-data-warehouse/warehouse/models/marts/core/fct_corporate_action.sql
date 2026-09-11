{#
  Grain: one security x one corporate action (dividend or split) x
  effective date. Dividends and splits share a table so "all cash and
  structural events for a name" is one filter, not a union in every query.
#}

with dividends as (
    select
        ticker,
        ex_date                  as action_date,
        cast('dividend' as varchar) as action_type,
        amount                   as dividend_amount,
        cast(null as double)     as split_ratio
    from {{ ref('stg_dividends') }}
),

splits as (
    select
        ticker,
        effective_date           as action_date,
        cast('split' as varchar) as action_type,
        cast(null as double)     as dividend_amount,
        ratio                    as split_ratio
    from {{ ref('stg_splits') }}
),

unioned as (
    select * from dividends
    union all
    select * from splits
),

securities as (
    select security_key, exchange_key, ticker
    from {{ ref('dim_security') }}
),

final as (
    select
        {{ dbt_utils.generate_surrogate_key(['unioned.ticker', 'unioned.action_date', 'unioned.action_type']) }} as corporate_action_key,
        securities.security_key,
        cast(
            extract(year  from unioned.action_date) * 10000
          + extract(month from unioned.action_date) * 100
          + extract(day   from unioned.action_date)
            as integer
        )                                                    as date_key,
        securities.exchange_key,
        unioned.ticker,
        unioned.action_date,
        unioned.action_type,
        unioned.dividend_amount,
        unioned.split_ratio
    from unioned
    inner join securities using (ticker)
)

select * from final
