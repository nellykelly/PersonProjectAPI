{#
  Grain: one security x one trading day -- same grain as
  fct_security_price and fct_security_technical_daily (a third member of
  the same fact table family). Risk-adjusted-return metrics serve a
  different question (how much could this lose, not where is it trending)
  than the technical fact, so it's kept separate even at an identical
  grain -- see fct_security_technical_daily's docstring for the same
  reasoning.

  Two deliberate simplifications, documented rather than hidden:
  - `risk_free_rate` (dbt var, default 0.0) has no real data source behind
    it -- there's no T-bill feed in this project. Sharpe/Sortino below are
    directionally useful, not precise.
  - `max_drawdown_252d` is the worst drawdown-from-all-time-high observed
    in the trailing year, not a peak that resets at the window's start.
    That's the standard, common definition (what most dashboards show as
    "current drawdown"), but worth being explicit about.
#}

{% set rf = var('risk_free_rate', 0.0) %}
{% set benchmark_ticker = var('benchmark_ticker', 'SPY') %}

with prices as (
    select security_key, date_key, ticker, trade_date, close, daily_return
    from {{ ref('fct_security_price') }}
),

benchmark as (
    select trade_date, daily_return as bench_return
    from prices
    where ticker = '{{ benchmark_ticker }}'
),

with_benchmark as (
    select p.*, b.bench_return
    from prices p
    -- left join: a security's own risk metrics (volatility, drawdown,
    -- Sharpe) don't depend on the benchmark, so a gap in benchmark
    -- history must not drop those rows -- only beta_* is null for them.
    left join benchmark b using (trade_date)
),

-- All-time running peak, used by max_drawdown_252d below. Computed in
-- its own step because a later window (the trailing min()) can't nest
-- directly on top of another window expression.
with_running_peak as (
    select
        *,
        max(close) over (
            partition by security_key order by trade_date
            rows between unbounded preceding and current row
        ) as running_peak_alltime
    from with_benchmark
),

with_drawdown as (
    select
        *,
        close / nullif(running_peak_alltime, 0) - 1 as drawdown_from_ath
    from with_running_peak
),

windows as (
    select
        security_key,
        date_key,
        ticker,
        trade_date,

        -- Annualized volatility of daily returns (stdev * sqrt(252)).
        stddev_samp(daily_return) over (
            partition by security_key order by trade_date
            rows between 19 preceding and current row
        ) * sqrt(252)                                               as volatility_20d,
        stddev_samp(daily_return) over (
            partition by security_key order by trade_date
            rows between 59 preceding and current row
        ) * sqrt(252)                                               as volatility_60d,
        stddev_samp(daily_return) over (
            partition by security_key order by trade_date
            rows between 251 preceding and current row
        ) * sqrt(252)                                               as volatility_252d,

        -- Worst all-time-high drawdown observed in the trailing year.
        min(drawdown_from_ath) over (
            partition by security_key order by trade_date
            rows between 251 preceding and current row
        )                                                           as max_drawdown_252d,

        -- Sharpe: annualized excess return over annualized volatility.
        (
            avg(daily_return) over (
                partition by security_key order by trade_date
                rows between 251 preceding and current row
            ) * 252 - {{ rf }}
        ) / nullif(
            stddev_samp(daily_return) over (
                partition by security_key order by trade_date
                rows between 251 preceding and current row
            ) * sqrt(252), 0
        )                                                           as sharpe_ratio_252d,

        -- Sortino: same, but the denominator only penalizes downside
        -- moves (semi-deviation of returns below 0), not all volatility.
        (
            avg(daily_return) over (
                partition by security_key order by trade_date
                rows between 251 preceding and current row
            ) * 252 - {{ rf }}
        ) / nullif(
            sqrt(
                avg(power(least(daily_return, 0), 2)) over (
                    partition by security_key order by trade_date
                    rows between 251 preceding and current row
                )
            ) * sqrt(252), 0
        )                                                           as sortino_ratio_252d,

        -- Beta vs the benchmark: covariance / variance of returns,
        -- equivalent to a linear regression slope.
        regr_slope(daily_return, bench_return) over (
            partition by security_key order by trade_date
            rows between 59 preceding and current row
        )                                                           as beta_60d,
        regr_slope(daily_return, bench_return) over (
            partition by security_key order by trade_date
            rows between 251 preceding and current row
        )                                                           as beta_252d
    from with_drawdown
),

final as (
    select
        security_key,
        date_key,
        ticker,
        trade_date,
        round(volatility_20d, 4)     as volatility_20d,
        round(volatility_60d, 4)     as volatility_60d,
        round(volatility_252d, 4)    as volatility_252d,
        round(max_drawdown_252d, 4)  as max_drawdown_252d,
        round(sharpe_ratio_252d, 3)  as sharpe_ratio_252d,
        round(sortino_ratio_252d, 3) as sortino_ratio_252d,
        round(beta_60d, 3)           as beta_60d,
        round(beta_252d, 3)          as beta_252d
    from windows
)

select * from final
