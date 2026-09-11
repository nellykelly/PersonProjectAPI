-- Example analytics over the star schema. Every question is answered with
-- joins on surrogate keys and filters on dimension attributes -- no
-- window functions or CASE gymnastics in the query itself, because the
-- facts already carry the derived measures.
--
-- Run against DuckDB:
--   duckdb warehouse/market.duckdb ".read analytics_queries.sql"

-- 1. AAPL dividend-adjusted return by calendar year
--    (compounding the daily adjusted-close returns already on the fact).
select
    d.year_number,
    round((exp(sum(ln(1 + p.daily_return))) - 1) * 100, 2) as annual_return_pct
from marts.fct_security_price p
join marts.dim_security s on s.security_key = p.security_key
join marts.dim_date     d on d.date_key    = p.date_key
where s.ticker = 'AAPL'
  and p.daily_return is not null
group by d.year_number
order by d.year_number;


-- 2. 20-day average dollar volume by security, this month.
select
    s.ticker,
    s.security_name,
    round(avg(p.dollar_volume), 0) as avg_dollar_volume_mtd
from marts.fct_security_price p
join marts.dim_security s on s.security_key = p.security_key
join marts.dim_date     d on d.date_key    = p.date_key
where d.year_number  = extract(year  from current_date)
  and d.month_number = extract(month from current_date)
group by s.ticker, s.security_name
order by avg_dollar_volume_mtd desc;


-- 3. Total cash dividends paid per security per year.
select
    s.ticker,
    d.year_number,
    round(sum(ca.dividend_amount), 4) as dividends_per_share
from marts.fct_corporate_action ca
join marts.dim_security s on s.security_key = ca.security_key
join marts.dim_date     d on d.date_key    = ca.date_key
where ca.action_type = 'dividend'
group by s.ticker, d.year_number
order by s.ticker, d.year_number;


-- 4. Every stock split on record, newest first, with the exchange.
select
    s.ticker,
    ca.action_date,
    ca.split_ratio,
    x.exchange_name
from marts.fct_corporate_action ca
join marts.dim_security s on s.security_key = ca.security_key
join marts.dim_exchange x on x.exchange_key = ca.exchange_key
where ca.action_type = 'split'
order by ca.action_date desc;


-- 5. Biggest single-day moves (by absolute return) on a weekday, by name.
select
    s.ticker,
    p.trade_date,
    d.day_name,
    round(p.daily_return * 100, 2) as pct_move
from marts.fct_security_price p
join marts.dim_security s on s.security_key = p.security_key
join marts.dim_date     d on d.date_key    = p.date_key
where d.is_weekday
  and p.daily_return is not null
order by abs(p.daily_return) desc
limit 20;
