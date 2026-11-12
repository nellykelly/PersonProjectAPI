"""Run one function from a visitor-edited DAG file, safely enough for a shared server.

Called by engine.call_dag() as its own process:  python -I dag_runner.py  < JSON on stdin.
Input:  {"source": "...", "func": "build_dbt_commands", "conf": {...}, "stub": "<airflow stub dir>"}
Output: one JSON line on stdout: {"ok": true, "result": ...} or {"ok": false, "error": "..."}

Layers, outermost first:
  1. A separate process with a 10 s wall-clock timeout (engine side), CPU and memory
     rlimits (here, on Linux), and an empty environment.
  2. An AST allow-list: no dunder or frame/code attributes, no str.format, no imports
     outside json / datetime / re / typing / airflow, no dangerous builtin names.
  3. exec() with a small hand-picked builtins dict and stand-in modules that expose only
     the few functions a DAG needs (json.codecs.open and similar never become reachable).
The DAG's job is string/dict plumbing (conf -> dbt --vars -> commands), and that is all
this allows.
"""
import ast
import json
import sys

ALLOWED_IMPORTS = {"json", "datetime", "re", "typing", "airflow", "airflow.operators.bash",
                   "airflow.operators.python", "__future__"}
BANNED_NAMES = {"eval", "exec", "compile", "open", "getattr", "setattr", "delattr", "globals", "locals",
                "vars", "dir", "__import__", "breakpoint", "input", "help", "memoryview", "type", "object",
                "super", "classmethod", "staticmethod", "property", "exit", "quit", "id", "hash"}
BANNED_ATTR_PREFIXES = ("_", "gi_", "cr_", "ag_", "f_", "tb_", "co_", "func_", "im_")
BANNED_ATTRS = {"format", "format_map", "mro", "with_traceback"}


class Rejected(Exception):
    pass


def check(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name not in ALLOWED_IMPORTS:
                    raise Rejected(f"import {alias.name} isn't allowed in the sandbox DAG (json, datetime, re, typing, airflow only)")
        elif isinstance(node, ast.ImportFrom):
            if node.level or node.module not in ALLOWED_IMPORTS:
                raise Rejected(f"from {node.module} import ... isn't allowed in the sandbox DAG")
            for alias in node.names:
                if alias.name == "*" or alias.name.startswith("_"):
                    raise Rejected("that import form isn't allowed in the sandbox DAG")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith(BANNED_ATTR_PREFIXES) or node.attr in BANNED_ATTRS:
                hint = " (use an f-string instead)" if node.attr in ("format", "format_map") else ""
                raise Rejected(f"attribute .{node.attr} isn't allowed in the sandbox DAG{hint}")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES or node.id.startswith("__"):
                raise Rejected(f"{node.id} isn't allowed in the sandbox DAG")
        elif isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef, ast.Await, ast.Global, ast.Nonlocal)):
            raise Rejected(f"{type(node).__name__} isn't allowed in the sandbox DAG")
        elif isinstance(node, ast.arg) and node.arg.startswith("__"):
            raise Rejected("dunder argument names aren't allowed")
        elif isinstance(node, ast.keyword) and node.arg and node.arg.startswith("__"):
            raise Rejected("dunder keyword names aren't allowed")


class Namespace:
    """A stand-in module exposing only the attributes listed."""
    def __init__(self, **attrs):
        for k, v in attrs.items():
            object.__setattr__(self, k, v)

    def __setattr__(self, *_):
        raise AttributeError("read-only module")


def make_modules(stub):
    import datetime as _dt
    import re as _re
    import typing as _typing

    sys.path.insert(0, stub)
    import airflow as _airflow  # the tiny stub, not real Airflow
    from airflow.operators import bash as _bash, python as _python

    return {
        "json": Namespace(dumps=json.dumps, loads=json.loads, JSONDecodeError=json.JSONDecodeError),
        "datetime": Namespace(datetime=_dt.datetime, date=_dt.date, timedelta=_dt.timedelta, timezone=_dt.timezone),
        "re": Namespace(sub=_re.sub, split=_re.split, match=_re.match, fullmatch=_re.fullmatch, search=_re.search,
                        findall=_re.findall, compile=_re.compile, escape=_re.escape, I=_re.I, IGNORECASE=_re.I),
        "typing": Namespace(**{n: getattr(_typing, n) for n in ("Any", "Dict", "List", "Optional", "Union", "Tuple")}),
        "airflow": Namespace(DAG=_airflow.DAG, operators=Namespace(bash=Namespace(BashOperator=_bash.BashOperator),
                                                                   python=Namespace(PythonOperator=_python.PythonOperator))),
        "airflow.operators.bash": Namespace(BashOperator=_bash.BashOperator),
        "airflow.operators.python": Namespace(PythonOperator=_python.PythonOperator),
        "__future__": Namespace(annotations=None),
    }


def make_builtins(modules):
    import builtins as b

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ImportError(f"import {name} isn't allowed in the sandbox DAG")
        if fromlist:
            return modules[name]
        return modules[name.split(".")[0]]

    def safe_print(*args, **kwargs):
        print(*args, file=sys.stderr)

    names = ["abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "frozenset", "int",
             "isinstance", "len", "list", "map", "max", "min", "range", "repr", "reversed", "round",
             "set", "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
             "Exception", "ValueError", "TypeError", "KeyError", "IndexError", "AttributeError", "RuntimeError"]
    safe = {n: getattr(b, n) for n in names}
    safe.update({"__import__": safe_import, "print": safe_print, "__build_class__": None})
    return safe


def limit_resources():
    try:
        import resource
    except ImportError:  # Windows local dev: the engine's timeout still applies
        return
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))  # may not create files


def main():
    req = json.loads(sys.stdin.read())
    if req["func"] not in ("build_dbt_commands", "build_dbt_vars"):
        return {"ok": False, "error": "unknown function"}
    modules = make_modules(req["stub"])  # import everything we need before the limits go on
    limit_resources()
    try:
        tree = ast.parse(req["source"], filename="delta_sync_dag.py")
    except SyntaxError as err:
        return {"ok": False, "error": f"SyntaxError: {err.msg} (line {err.lineno})"}
    try:
        check(tree)
    except Rejected as err:
        return {"ok": False, "error": f"SandboxError: {err}"}
    env = {"__builtins__": make_builtins(modules), "__name__": "delta_sync_dag"}
    try:
        exec(compile(tree, "delta_sync_dag.py", "exec"), env)
        func = env.get(req["func"])
        if not callable(func):
            return {"ok": False, "error": f"AttributeError: the DAG file has no function {req['func']}"}
        result = func(dict(req["conf"]))
        json.dumps(result)
    except Exception as err:
        return {"ok": False, "error": f"{type(err).__name__}: {err}"}
    return {"ok": True, "result": result}


if __name__ == "__main__":
    out = main()
    sys.stdout.write("\n" + json.dumps(out) + "\n")
