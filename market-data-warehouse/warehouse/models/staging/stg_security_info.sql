with source as (
    select * from {{ source('raw', 'security_info') }}
),

cleaned as (
    select
        upper(trim(cast(ticker as varchar)))                 as ticker,
        nullif(trim(cast(short_name as varchar)), '')        as short_name,
        nullif(trim(cast(long_name as varchar)), '')         as long_name,
        nullif(trim(cast(quote_type as varchar)), '')        as quote_type,
        nullif(trim(cast(exchange_code as varchar)), '')     as exchange_code,
        nullif(trim(cast(exchange_name as varchar)), '')     as exchange_name,
        nullif(trim(cast(currency as varchar)), '')          as currency,
        nullif(trim(cast(financial_currency as varchar)), '') as financial_currency,
        nullif(trim(cast(sector as varchar)), '')            as sector,
        nullif(trim(cast(industry as varchar)), '')          as industry,
        nullif(trim(cast(country as varchar)), '')           as country,
        nullif(trim(cast(timezone_name as varchar)), '')     as timezone_name,
        source,
        cast(ingested_at as timestamp)                       as ingested_at
    from source
)

select *
from cleaned
qualify row_number() over (
    partition by ticker order by ingested_at desc
) = 1
