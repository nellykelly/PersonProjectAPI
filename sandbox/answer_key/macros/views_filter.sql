{#
    WHERE clause shared by every EXPORT_VIEWS model. Each filter switches on only
    when its var is set, so running with no vars exposes the full population.

    alias:  the table alias the view uses for its EXPORT table.
    entity: which target table the view is (policyperiod, vehicle, account), because
            only POLICYPERIOD carries policy_number; the others are reached through it.
#}
{% macro views_filter(alias, entity) -%}
    {#- ref outside the if, so dbt always records the dependency -#}
    {%- set policyperiod = ref('export_policyperiod') -%}
    1 = 1
    {%- set states = var('record_state', none) %}
    {%- if states %}
    and {{ alias }}.record_state in ({{ quoted_list(states) }})
    {%- endif %}
    {%- set changed_since = var('changed_since', none) %}
    {%- if changed_since %}
    and {{ alias }}.updated_ts >= cast('{{ changed_since }}' as timestamp)
    {%- endif %}
    {%- set policy_numbers = var('policy_numbers', none) %}
    {%- if policy_numbers %}
    and {{ policy_number_filter(alias, entity, policyperiod, policy_numbers) }}
    {%- endif %}
{%- endmacro %}


{#
    Targeted testing (ATL-101): keep only the selected policies and the records that
    migrate with them.
      policyperiod -> its own policy_number
      vehicle      -> same payload (policy number + expiration date) as a selected policy
      account      -> the account that owns a selected policy. Match on source_system too:
                      account numbers are only unique within a source (GM 50012 vs Ironway 50012).
#}
{% macro policy_number_filter(alias, entity, policyperiod, policy_numbers) -%}
    {%- if entity == 'policyperiod' -%}
        {{ alias }}.policy_number in ({{ quoted_list(policy_numbers) }})
    {%- elif entity == 'vehicle' -%}
        exists (
            select 1
            from {{ policyperiod }} as sel
            where sel.policy_number in ({{ quoted_list(policy_numbers) }})
              and sel.payload_id = {{ alias }}.payload_id
        )
    {%- elif entity == 'account' -%}
        exists (
            select 1
            from {{ policyperiod }} as sel
            where sel.policy_number in ({{ quoted_list(policy_numbers) }})
              and sel.source_system = {{ alias }}.source_system
              and sel.account_number = {{ alias }}.account_number
        )
    {%- else -%}
        {{ exceptions.raise_compiler_error("policy_number_filter: unknown entity '" ~ entity ~ "'") }}
    {%- endif -%}
{%- endmacro %}
