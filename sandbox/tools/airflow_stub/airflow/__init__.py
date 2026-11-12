"""Tiny stand-in for Airflow so the DAG file imports locally. Not the real thing."""


class _Task:
    def __init__(self, task_id=None, **kwargs):
        self.task_id = task_id
        self.kwargs = kwargs
        self.downstream = []

    def __rshift__(self, other):
        self.downstream.append(other)
        return other

    def __lshift__(self, other):
        other.downstream.append(self)
        return other


class DAG:
    def __init__(self, dag_id=None, **kwargs):
        self.dag_id = dag_id
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False
