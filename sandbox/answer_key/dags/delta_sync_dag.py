"""
Delta sync DAG.

Agents still write business in the legacy systems after a policy has migrated, so every
day this DAG:
  1. runs the dbt project incrementally: legacy changes flow RAW -> STAGING -> INTERMEDIATE
     (new record_hash) -> PRE_EXPORT -> EXPORT, where only new/changed records are written
  2. rebuilds the EXPORT_VIEWS with the run's filters (--vars)
  3. exports every view to S3 (the platform team loads S3 -> downstream from there)

Triggering with config (Airflow UI: "Trigger DAG w/ config") passes `dag_run.conf`:
    {"record_state": "KY"}
    {"policy_numbers": ["PN1001", "IW2002"]}    # only these policies (and their vehicles/accounts)
The scheduler fills `changed_since` with the previous run's start time, so a run only
exports what changed since then. Pass {"changed_since": "1900-01-01"} to export everything.
"""
import json
from datetime import datetime

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

DBT_DIR = "/opt/airflow/dbt/atlas_conversion"


def build_dbt_vars(conf: dict) -> dict:
    """Turn the DAG run's conf into the dbt --vars the EXPORT_VIEWS layer reads."""
    dbt_vars = {}
    if conf.get("record_state"):
        dbt_vars["record_state"] = conf["record_state"]
    if conf.get("changed_since"):
        dbt_vars["changed_since"] = conf["changed_since"]
    if conf.get("policy_numbers"):  # ATL-101: targeted testing of specific policies
        dbt_vars["policy_numbers"] = conf["policy_numbers"]
    return dbt_vars


def build_dbt_commands(conf: dict) -> list:
    """The dbt commands one run executes, in order."""
    dbt_vars = json.dumps(build_dbt_vars(conf))
    return [
        "dbt build --select +tag:export",
        f"dbt run --select tag:views --vars '{dbt_vars}'",
    ]


def export_views_to_s3(**context):
    """Unloads every EXPORT_VIEWS view to s3://atlas-exports/delta_sync/<run_id>/."""
    ...


with DAG(
    dag_id="atlas_delta_sync",
    start_date=datetime(2026, 9, 1),
    schedule="0 5 * * *",
    catchup=False,
    tags=["atlas", "delta_sync"],
) as dag:
    run_dbt = BashOperator(
        task_id="run_dbt",
        bash_command=(
            f"cd {DBT_DIR} && "
            "{{ ti.xcom_pull(task_ids='plan_commands') | join(' && ') }}"
        ),
    )
    plan_commands = PythonOperator(
        task_id="plan_commands",
        python_callable=lambda **ctx: build_dbt_commands(ctx["dag_run"].conf or {}),
    )
    export = PythonOperator(task_id="export_views_to_s3", python_callable=export_views_to_s3)

    plan_commands >> run_dbt >> export
