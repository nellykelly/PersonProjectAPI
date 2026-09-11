{#
  Captures a new version of a security's metadata row whenever any tracked
  attribute changes, so history is preserved even though dim_security only
  exposes the current state. An SCD-2 dim can be built straight off this.
#}
{% snapshot security_info_snapshot %}
{{
    config(
        target_schema='snapshots',
        unique_key='ticker',
        strategy='check',
        check_cols=[
            'short_name', 'long_name', 'exchange_code', 'currency',
            'sector', 'industry', 'country'
        ],
        invalidate_hard_deletes=True
    )
}}
select
    ticker,
    short_name,
    long_name,
    quote_type,
    exchange_code,
    exchange_name,
    currency,
    financial_currency,
    sector,
    industry,
    country,
    timezone_name,
    ingested_at
from {{ ref('stg_security_info') }}
{% endsnapshot %}
