{#
  Use the model's +schema verbatim as the schema name (e.g. "staging",
  "marts") instead of dbt's default "<profile_schema>_<custom>" prefixing.
  Keeps the warehouse layout identical on DuckDB and Snowflake and easy to
  read. A single-developer project has no need for the per-user prefix the
  default guards against.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
