{{ config(materialized='incremental', unique_key='record_id', incremental_strategy='delete+insert', alias='vehicle') }}

{{ audit_columns(ref('pre_export_vehicle')) }}
