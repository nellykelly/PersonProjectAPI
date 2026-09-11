{#
  ============================================================================
  NOT A FORECAST. NOT FINANCIAL ADVICE.
  ============================================================================
  Three deliberately different, real forecasting methods from the
  literature -- not "three tunings of one model." Picking a method changes
  which assumption you're making about the future, which is the honest
  point of offering more than one:

  - `linear_trend`   -- momentum: the recent log-price trend continues.
                        Ordinary least squares on ln(close) vs. a day index.
  - `random_walk_drift` -- the textbook no-skill baseline (Hyndman &
                        Athanasopoulos, "Forecasting: Principles and
                        Practice", the "drift method"): tomorrow's best
                        guess is today's price plus the *average* historical
                        daily return. Every real forecast evaluation
                        benchmarks against this.
  - `mean_reversion` -- the opposite assumption: price reverts toward its
                        own trailing average at an estimated rate (a
                        discretized Ornstein-Uhlenbeck / AR(1) process --
                        standard quant-finance mean-reversion estimation:
                        regress each day's deviation from the mean against
                        the *previous* day's deviation; the slope is the
                        reversion speed phi, clipped to [0, 0.999) so the
                        projection can't explode or oscillate).

  None of these has any demonstrated ability to predict real future
  prices -- stock prices are close to a random walk. `fit_quality` (an R^2,
  null for the drift method, which has no analogous fit statistic) is
  carried specifically so a bad fit is visible, not hidden.

  Grain: one security x one *future* trading day x one `method` x one
  `lookback_days` -- the lookback window used to estimate that method's
  parameters is a genuine additional axis of variation (a 30-day trend and
  a 180-day trend are different claims), not a display setting, so it's
  part of the grain rather than a UI-only filter on a single precomputed
  answer. Every combination in `projection_lookback_grid` (var, default
  [30, 60, 90, 180]) is precomputed for every method, so the dashboard's
  method/timeframe pickers are just filters over rows that already exist
  -- no live computation happens outside dbt.

  Full-refresh every dbt run: this table always reflects today's
  projections, computed fresh -- it does not keep yesterday's projections
  around (no snapshot; over-engineering a feature this explicitly
  uncertain).
  ============================================================================
#}

{% set lookback_grid = var('projection_lookback_grid', [30, 60, 90, 180]) %}
{% set horizon = var('projection_horizon_days', 90) %}
{% set z = 1.96 %}  {# ~95% band under a normal-returns assumption #}

with price_history as (
    select security_key, ticker, trade_date, close, ln(close) as ln_close
    from {{ ref('fct_security_price') }}
    where close is not null
),

ranked_from_end as (
    select
        *,
        row_number() over (partition by security_key order by trade_date desc) as rn_from_end
    from price_history
),

-- day-over-day log return, needed by the drift method. Its own step: a
-- window function can't nest inside another window call.
with_log_return as (
    select
        *,
        ln_close - lag(ln_close) over (partition by security_key order by trade_date) as log_return
    from ranked_from_end
),

last_actual as (
    select security_key, ticker, max(trade_date) as last_trade_date
    from price_history
    group by security_key, ticker
),

anchor as (
    -- last actual ln(close) per security -- every method's projection is
    -- anchored here, whichever way it then extrapolates.
    select security_key, ln_close as last_ln_close
    from ranked_from_end
    where rn_from_end = 1
),

risk as (
    -- 60-day annualized volatility -> daily sigma, drives the confidence
    -- band's width identically for all three methods (reused from
    -- fct_security_risk_daily rather than recomputed, so they can't
    -- disagree; keeping the band treatment uniform is what makes
    -- comparing methods meaningful -- only the central-tendency
    -- assumption should differ between them, not the uncertainty math).
    select security_key, trade_date, volatility_60d
    from {{ ref('fct_security_risk_daily') }}
),

-- Future trading days are the same regardless of method or lookback --
-- computed once, joined into every method x lookback combination below.
future_days as (
    select
        la.security_key,
        la.ticker,
        r.volatility_60d / sqrt(252) as daily_vol,
        d.date_key,
        d.date_day as projection_date,
        row_number() over (partition by la.security_key order by d.date_day) as trading_day_offset
    from last_actual la
    left join risk r
        on r.security_key = la.security_key
       and r.trade_date = la.last_trade_date
    join {{ ref('dim_date') }} d
        on d.date_day > la.last_trade_date
       and d.is_weekday
    qualify trading_day_offset <= {{ horizon }}
),

{% set point_ctes = [] %}
{% for L in lookback_grid %}
{% do point_ctes.extend(["point_linear_" ~ L, "point_drift_" ~ L, "point_meanrev_" ~ L]) %}

fit_window_{{ L }} as (
    select * from with_log_return where rn_from_end <= {{ L }}
),

-- --- linear_trend: OLS of ln(close) on a day index -----------------------
linear_indexed_{{ L }} as (
    select
        *,
        row_number() over (partition by security_key order by trade_date) - 1 as day_index
    from fit_window_{{ L }}
),

linear_fit_{{ L }} as (
    select
        security_key,
        regr_slope(ln_close, day_index)     as slope,
        regr_intercept(ln_close, day_index) as intercept,
        regr_r2(ln_close, day_index)        as r_squared,
        max(day_index)                      as last_day_index
    from linear_indexed_{{ L }}
    group by security_key
),

point_linear_{{ L }} as (
    select
        f.security_key, f.ticker, f.date_key, f.projection_date, f.trading_day_offset, f.daily_vol,
        cast('linear_trend' as varchar) as method,
        {{ L }} as lookback_days,
        exp(lf.intercept + lf.slope * (lf.last_day_index + f.trading_day_offset)) as projected_close,
        lf.r_squared as fit_quality
    from future_days f
    join linear_fit_{{ L }} lf using (security_key)
),

-- --- random_walk_drift: last price x exp(mean daily log return x t) -----
drift_fit_{{ L }} as (
    select security_key, avg(log_return) as drift
    from fit_window_{{ L }}
    where log_return is not null
    group by security_key
),

point_drift_{{ L }} as (
    select
        f.security_key, f.ticker, f.date_key, f.projection_date, f.trading_day_offset, f.daily_vol,
        cast('random_walk_drift' as varchar) as method,
        {{ L }} as lookback_days,
        exp(a.last_ln_close + df.drift * f.trading_day_offset) as projected_close,
        cast(null as double) as fit_quality  -- an average has no regression fit statistic to report
    from future_days f
    join drift_fit_{{ L }} df using (security_key)
    join anchor a using (security_key)
),

-- --- mean_reversion: AR(1) on the deviation from the trailing mean ------
meanrev_long_run_{{ L }} as (
    select security_key, avg(ln_close) as long_run_mean
    from fit_window_{{ L }}
    group by security_key
),

meanrev_deviation_{{ L }} as (
    select
        w.security_key,
        w.trade_date,
        w.ln_close - lr.long_run_mean as deviation
    from fit_window_{{ L }} w
    join meanrev_long_run_{{ L }} lr using (security_key)
),

meanrev_lagged_{{ L }} as (
    select
        *,
        lag(deviation) over (partition by security_key order by trade_date) as prior_deviation
    from meanrev_deviation_{{ L }}
),

meanrev_fit_{{ L }} as (
    select
        security_key,
        -- clipped to [0, 0.999): a negative or >=1 raw slope means the
        -- series isn't behaving as mean-reverting over this window at
        -- all (oscillating or explosive) -- clip rather than let phi^h
        -- blow up or flip sign every step.
        least(greatest(regr_slope(deviation, prior_deviation), 0), 0.999) as phi,
        regr_r2(deviation, prior_deviation) as r_squared
    from meanrev_lagged_{{ L }}
    where prior_deviation is not null
    group by security_key
),

point_meanrev_{{ L }} as (
    select
        f.security_key, f.ticker, f.date_key, f.projection_date, f.trading_day_offset, f.daily_vol,
        cast('mean_reversion' as varchar) as method,
        {{ L }} as lookback_days,
        exp(
            lr.long_run_mean
            + power(mf.phi, f.trading_day_offset) * (a.last_ln_close - lr.long_run_mean)
        ) as projected_close,
        mf.r_squared as fit_quality
    from future_days f
    join meanrev_fit_{{ L }} mf using (security_key)
    join meanrev_long_run_{{ L }} lr using (security_key)
    join anchor a using (security_key)
),

{% endfor %}

all_points as (
    {% for cte in point_ctes %}
    select * from {{ cte }}
    {% if not loop.last %}union all{% endif %}
    {% endfor %}
),

final as (
    select
        {{ dbt_utils.generate_surrogate_key(['security_key', 'projection_date', 'method', 'lookback_days']) }}
            as projection_key,
        security_key,
        ticker,
        date_key,
        projection_date,
        method,
        lookback_days,
        trading_day_offset,
        round(projected_close, 4)                                                          as projected_close,
        round(projected_close * exp(-{{ z }} * coalesce(daily_vol, 0) * sqrt(trading_day_offset)), 4)
            as lower_bound_95,
        round(projected_close * exp({{ z }} * coalesce(daily_vol, 0) * sqrt(trading_day_offset)), 4)
            as upper_bound_95,
        round(fit_quality, 4)                                                               as fit_quality
    from all_points
)

select * from final
