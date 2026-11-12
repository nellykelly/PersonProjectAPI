{{ config(materialized='incremental', unique_key='record_id', incremental_strategy='delete+insert', alias='account') }}

{{ audit_columns(ref('pre_export_account')) }}
