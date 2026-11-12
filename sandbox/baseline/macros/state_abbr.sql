{# Numeric (FIPS) state code -> two-letter code. Used by the Pinnacle base queries. #}
{% macro state_abbr(column) -%}
    case {{ column }}
        when 21 then 'KY'
        when 29 then 'MO'
        when 47 then 'TN'
    end
{%- endmacro %}
