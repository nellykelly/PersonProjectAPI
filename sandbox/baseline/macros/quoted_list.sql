{#
    Turns a var into a SQL list of quoted strings.
    Accepts a list (["KY", "TN"]) or a comma-separated string ("KY,TN" or "KY").
    quoted_list(["KY", "TN"])  ->  'KY', 'TN'
#}
{% macro quoted_list(value) -%}
    {%- if value is string -%}
        {%- set items = value.split(',') -%}
    {%- else -%}
        {%- set items = value -%}
    {%- endif -%}
    {%- for item in items -%}
        '{{ item | string | trim }}'{% if not loop.last %}, {% endif %}
    {%- endfor -%}
{%- endmacro %}
