{#
  Grain: one security x one trading day. Foreign keys to dim_security,
  dim_date, dim_exchange. Derived measures (returns, prior close, gap,
  dollar volume) are computed here so BI tools only ever join and filter.
#}

with prices as (
    select * from {{ ref('stg_prices') }}
),

securities as (
    select security_key, exchange_key, ticker
    from {{ ref('dim_security') }}
),

joined as (
    select
        prices.*,
        securities.security_key,
        securities.exchange_key
    from prices
    inner join securities using (ticker)
),

windowed as (
    select
        *,
        lag(close)     over (partition by security_key order by trade_date) as prior_close,
        lag(adj_close) over (partition by security_key order by trade_date) as prior_adj_close
    from joined
),

final as (
    select
        {{ dbt_utils.generate_surrogate_key(['ticker', 'trade_date']) }} as price_key,
        security_key,
        cast(
            extract(year  from trade_date) * 10000
          + extract(month from trade_date) * 100
          + extract(day   from trade_date)
            as integer
        )                                                               as date_key,
        exchange_key,
        ticker,
        trade_date,
        open,
        high,
        low,
        close,
        adj_close,
        volume,
        close * volume                                                  as dollar_volume,
        prior_close,
        close - prior_close                                             as change_abs,
        (adj_close / nullif(prior_adj_close, 0)) - 1                     as daily_return,
        (open / nullif(prior_close, 0)) - 1                             as gap_pct,
        dividend                                                        as dividend_on_day,
        split_ratio                                                     as split_ratio_on_day
    from windowed
)

select * from final
