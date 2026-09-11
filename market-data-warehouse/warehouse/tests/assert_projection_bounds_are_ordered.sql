-- Sanity check on fct_security_price_projection: the confidence band must
-- bracket the point projection (lower <= projected <= upper). Any returned
-- row is a failure.
select
    ticker,
    projection_date,
    lower_bound_95,
    projected_close,
    upper_bound_95
from {{ ref('fct_security_price_projection') }}
where not (lower_bound_95 <= projected_close and projected_close <= upper_bound_95)
