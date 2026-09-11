with source as (
    select * from {{ source('raw', 'dividends') }}
),

typed as (
    select
        upper(trim(ticker))            as ticker,
        cast(ex_date as date)          as ex_date,
        cast(amount as double)         as amount,
        source,
        cast(ingested_at as timestamp) as ingested_at
    from source
)

select *
from typed
where amount > 0
qualify row_number() over (
    partition by ticker, ex_date order by ingested_at desc
) = 1
