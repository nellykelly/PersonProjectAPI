-- H1, corporate-action side: same silent-drop risk as
-- assert_no_orphaned_price_tickers.sql, but for fct_corporate_action's
-- inner join to dim_security via the dividends/splits staging models.
with actions as (
    select ticker from {{ ref('stg_dividends') }}
    union
    select ticker from {{ ref('stg_splits') }}
)

select distinct actions.ticker
from actions
left join {{ ref('dim_security') }} s on s.ticker = actions.ticker
where s.ticker is null
