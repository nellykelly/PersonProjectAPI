-- Singular test: on any bar, the day's high must be >= the day's low.
-- Any returned row is a failure.
select
    ticker,
    trade_date,
    high,
    low
from {{ ref('fct_security_price') }}
where high is not null
  and low is not null
  and high < low
