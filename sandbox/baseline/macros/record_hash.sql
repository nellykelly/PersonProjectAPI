{# Hash of every column of a relation (minus exclusions). INT uses it so EXPORT can spot changed records. #}
{% macro record_hash(relation, exclude=[]) -%}
    {%- if not execute -%}
        null
    {%- else -%}
        {%- set cols = adapter.get_columns_in_relation(relation) | rejectattr('name', 'in', exclude) | list -%}
        md5(concat_ws('||'
        {%- for col in cols %}, coalesce(cast({{ adapter.quote(col.name) }} as varchar), '~'){% endfor -%}
        ))
    {%- endif -%}
{%- endmacro %}
