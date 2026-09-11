{#
  Grain: one security x one trading day -- identical to fct_security_price.
  A separate fact table at the same grain (a "fact table family" sharing
  conformed dimensions and keys) rather than new columns bolted onto
  fct_security_price: these are derived technical signals with a much
  higher rate of change (new indicators, tweaked windows) than raw OHLCV,
  and keeping them apart means iterating here never touches the stable,
  tested price fact.

  Only indicators computable with plain SQL window functions are here.
  True EMA/MACD need a recursive definition (each day's EMA depends on
  the previous day's EMA) that plain OVER() windows can't express -- see
  the note above `sma_20` below. Faking that with a simple moving average
  and calling it MACD would be actively misleading, so it's left out
  rather than approximated.
#}

with prices as (
    select security_key, date_key, ticker, trade_date, close
    from {{ ref('fct_security_price') }}
),

-- Window functions can't nest (`avg(lag(x) over (...)) over (...)` is
-- invalid SQL), so the day-over-day gain/loss RSI needs feed off of a
-- plain lag() computed in its own step first.
with_prior_close as (
    select
        *,
        lag(close) over (partition by security_key order by trade_date) as prior_close
    from prices
),

gains_losses as (
    select
        *,
        greatest(close - prior_close, 0) as gain,
        greatest(prior_close - close, 0) as loss
    from with_prior_close
),

windows as (
    select
        security_key,
        date_key,
        ticker,
        trade_date,
        close,

        -- Simple moving averages. Plain rolling AVG() -- no recursion needed.
        avg(close) over (
            partition by security_key order by trade_date
            rows between 19 preceding and current row
        )                                                          as sma_20,
        avg(close) over (
            partition by security_key order by trade_date
            rows between 49 preceding and current row
        )                                                          as sma_50,
        avg(close) over (
            partition by security_key order by trade_date
            rows between 199 preceding and current row
        )                                                          as sma_200,

        -- Bollinger Bands(20, 2): SMA-20 +/- 2 standard deviations.
        stddev_samp(close) over (
            partition by security_key order by trade_date
            rows between 19 preceding and current row
        )                                                          as _stddev_20,

        -- 52-week (rolling 252-trading-day) range.
        max(close) over (
            partition by security_key order by trade_date
            rows between 251 preceding and current row
        )                                                          as high_52w,
        min(close) over (
            partition by security_key order by trade_date
            rows between 251 preceding and current row
        )                                                          as low_52w,

        -- Simplified RSI(14): plain rolling averages of gains/losses, not
        -- Wilder's exponential smoothing (the "textbook" RSI) -- that,
        -- like EMA/MACD above, needs a recursive definition. This is a
        -- documented, common approximation, not the canonical formula.
        avg(gain) over (
            partition by security_key order by trade_date
            rows between 13 preceding and current row
        )                                                          as _avg_gain_14,
        avg(loss) over (
            partition by security_key order by trade_date
            rows between 13 preceding and current row
        )                                                          as _avg_loss_14
    from gains_losses
),

final as (
    select
        security_key,
        date_key,
        ticker,
        trade_date,
        round(sma_20, 4)                                            as sma_20,
        round(sma_50, 4)                                            as sma_50,
        round(sma_200, 4)                                           as sma_200,
        round(sma_20 + 2 * _stddev_20, 4)                           as bollinger_upper_20,
        round(sma_20 - 2 * _stddev_20, 4)                           as bollinger_lower_20,
        round(high_52w, 4)                                          as high_52w,
        round(low_52w, 4)                                           as low_52w,
        round((close / nullif(high_52w, 0) - 1) * 100, 2)           as pct_off_52w_high,
        case
            when _avg_loss_14 = 0 then 100
            else round(100 - (100 / (1 + (_avg_gain_14 / nullif(_avg_loss_14, 0)))), 2)
        end                                                         as rsi_14_simplified
    from windows
)

select * from final
