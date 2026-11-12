{#
    WHERE clause shared by every EXPORT_VIEWS model. Each filter switches on only
    when its var is set, so running with no vars exposes the full population.

    alias: the table alias the view uses for its EXPORT table.
#}
{% macro views_filter(alias) -%}
    1 = 1
    {%- set states = var('record_state', none) %}
    {%- if states %}
    and {{ alias }}.record_state in ({{ quoted_list(states) }})
    {%- endif %}
    {%- set changed_since = var('changed_since', none) %}
    {%- if changed_since %}
    and {{ alias }}.updated_ts >= cast('{{ changed_since }}' as timestamp)
    {%- endif %}
{%- endmacro %}
