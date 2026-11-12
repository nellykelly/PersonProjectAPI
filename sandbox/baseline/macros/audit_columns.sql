{#
    Body of every EXPORT model. Incremental on record_id:
      - first build: every record is an INSERT
      - later runs: only new records (INSERT) and records whose record_hash changed (UPDATE)
        are written, stamped with updated_ts. That's how delta sync finds what
        changed in legacy since the last run.
#}
{% macro audit_columns(source_relation) -%}
with src as (
    select * from {{ source_relation }}
)
{% if is_incremental() %}
, existing as (
    select cur.record_id, cur.record_hash, cur.created_ts
    from {{ this }} as cur
)

select
    src.*,
    coalesce(ex.created_ts, cast(now() as timestamp))   as created_ts,
    cast(now() as timestamp)                                as updated_ts,
    case when ex.record_id is null then 'INSERT' else 'UPDATE' end as record_action
from src
left join existing as ex
    on ex.record_id = src.record_id
where ex.record_id is null
   or ex.record_hash <> src.record_hash
{% else %}
select
    src.*,
    cast(now() as timestamp)   as created_ts,
    cast(now() as timestamp)   as updated_ts,
    'INSERT'                   as record_action
from src
{% endif %}
{%- endmacro %}
