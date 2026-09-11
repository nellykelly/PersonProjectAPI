with source as (
    select * from {{ source('raw', 'price_history') }}
),

typed as (
    select
        upper(trim(ticker))            as ticker,
        cast(trade_date as date)       as trade_date,
        cast(open as double)           as open,
        cast(high as double)           as high,
        cast(low as double)            as low,
        cast(close as double)          as close,
        cast(adj_close as double)      as adj_close,
        cast(volume as bigint)         as volume,
        cast(dividend as double)       as dividend,
        cast(split_ratio as double)    as split_ratio,
        source,
        cast(ingested_at as timestamp) as ingested_at
    from source
)

select *
from typed
-- drop the in-progress partial bar (today's row before the close prints)
where close is not null
-- latest load wins for a (ticker, day)
qualify row_number() over (
    partition by ticker, trade_date order by ingested_at desc
) = 1
