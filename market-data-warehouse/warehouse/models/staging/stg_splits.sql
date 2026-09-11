with source as (
    select * from {{ source('raw', 'splits') }}
),

typed as (
    select
        upper(trim(ticker))            as ticker,
        cast(effective_date as date)   as effective_date,
        cast(ratio as double)          as ratio,
        source,
        cast(ingested_at as timestamp) as ingested_at
    from source
)

select *
from typed
where ratio > 0
qualify row_number() over (
    partition by ticker, effective_date order by ingested_at desc
) = 1
