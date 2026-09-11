-- H1: fct_security_price inner-joins stg_prices to dim_security. If a
-- ticker's security_info pull ever fails (while its price pull succeeds),
-- that inner join silently drops every price row for it -- no error, the
-- ticker just vanishes from the fact. This test makes that loud instead:
-- any ticker with price rows must have a dim_security row.
select distinct p.ticker
from {{ ref('stg_prices') }} p
left join {{ ref('dim_security') }} s on s.ticker = p.ticker
where s.ticker is null
