"""
Local stand-in for Snowflake + dbt + Airflow + S3 + git, used by the practice web app.

Snowflake = a DuckDB file (warehouse/atlas_dev.duckdb) behind a Snowflake-flavoured layer
dbt       = real dbt-core + dbt-duckdb, run against repo/
Airflow   = repo/dags/delta_sync_dag.py imported with a stub, its commands run for real
S3        = the s3/ folder

Every visitor gets their own "box" (repo/, warehouse/, s3/ under one session directory).
The server picks the box for each request with use_box(); everything below reads the
current box through B(), so two browsers never touch each other's files.
"""
import contextvars
import csv
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import duckdb
import yaml

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
BASELINE = ROOT / "baseline"
ANSWER_KEY = ROOT / "answer_key"
DATA = Path(os.environ.get("SANDBOX_DATA") or ROOT / "data")
AIRFLOW_STUB = TOOLS / "airflow_stub"
PROFILES_DIR = TOOLS / "profiles"   # fixed, outside the editable repo
DAG_RUNNER = TOOLS / "dag_runner.py"
DBT_EXE = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
SKIP_DIRS = {"target", "logs", "dbt_packages", "__pycache__"}
ANSI = re.compile(r"\x1b\[[0-9;]*m")
DB = "ATLAS_DEV"

# At most this many dbt processes at once across every visitor (each is ~150 MB).
DBT_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SANDBOX_DBT_CONCURRENCY", "2")))
SQL_TIMEOUT = float(os.environ.get("SANDBOX_SQL_TIMEOUT", "30"))
DBT_TIMEOUT = int(os.environ.get("SANDBOX_DBT_TIMEOUT", "300"))

from raw_data import RAW_DATA_SQL  # noqa: E402


# ================================================================ per-visitor boxes

@dataclass(frozen=True)
class Box:
    root: Path

    @property
    def repo(self):
        return self.root / "repo"

    @property
    def warehouse_dir(self):
        return self.root / "warehouse"

    @property
    def warehouse(self):
        return self.warehouse_dir / "atlas_dev.duckdb"

    @property
    def check_warehouse(self):
        return self.warehouse_dir / "atlas_check.duckdb"

    @property
    def state_file(self):
        return self.warehouse_dir / "state.json"

    @property
    def s3(self):
        return self.root / "s3"


_box = contextvars.ContextVar("box")


def B() -> Box:
    return _box.get()


def use_box(box: Box):
    return _box.set(box)


TEMPLATE = Box(DATA / "_template")


def build_template():
    """Build one ready-made box (dbt build ~20 s) that new sessions copy instead of rebuilding."""
    fingerprint = template_fingerprint()
    ready = TEMPLATE.root / ".ready"
    if TEMPLATE.warehouse.exists() and ready.exists() and ready.read_text() == fingerprint:
        return
    if TEMPLATE.root.exists():
        shutil.rmtree(TEMPLATE.root)
    TEMPLATE.root.mkdir(parents=True)
    token = use_box(TEMPLATE)
    try:
        result = reset()
    finally:
        _box.reset(token)
    if not result["ok"]:
        raise RuntimeError("Template build failed:\n" + result["log"])
    ready.write_text(fingerprint)


def template_fingerprint():
    """Changes whenever the baseline project or the raw data changes (i.e. after a deploy)."""
    import hashlib
    digest = hashlib.sha256()
    for path in sorted([*BASELINE.rglob("*"), TOOLS / "raw_data.py", PROFILES_DIR / "profiles.yml"]):
        if path.is_file():
            digest.update(path.relative_to(ROOT).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def clone_template(box: Box):
    """A fresh box = a copy of the template (seconds, not a 20 s dbt build)."""
    tmp = box.root.with_name(box.root.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(TEMPLATE.root, tmp, ignore=shutil.ignore_patterns("target", "logs", ".ready"))
    tmp.rename(box.root)


# ================================================================ state

def load_state():
    if B().state_file.exists():
        return json.loads(B().state_file.read_text(encoding="utf-8"))
    return {}


def save_state(state):
    B().warehouse_dir.mkdir(exist_ok=True)
    B().state_file.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def default_state():
    return {
        "context": {"role": "DEVELOPER", "warehouse": "DEV_WH", "database": DB, "schema": "EXPORT"},
        "query_history": [],
        "git": {"branch": "main", "commits": [], "pr": None},
        "dag_runs": [],
        "last_dag_run": None,
        "jira_status": "To Do",
    }


# ================================================================ Snowflake layer

SHIMS = [
    "create or replace temp macro iff(c, a, b) as case when c then a else b end",
    "create or replace temp macro nvl(a, b) as coalesce(a, b)",
    "create or replace temp macro nvl2(a, b, c) as case when a is not null then b else c end",
    "create or replace temp macro to_varchar(a) as cast(a as varchar)",
    "create or replace temp macro to_char(a) as cast(a as varchar)",
    "create or replace temp macro zeroifnull(a) as coalesce(a, 0)",
    "create or replace temp macro array_contains(v, arr) as list_contains(arr, v)",
    "create or replace temp macro current_warehouse() as 'DEV_WH'",
    "create or replace temp macro current_role() as 'DEVELOPER'",
    "create or replace temp macro sysdate() as cast(now() as timestamp)",
    "create or replace temp macro __dateadd(u, n, d) as case lower(u) "
    " when 'day' then d + n * interval 1 day when 'days' then d + n * interval 1 day"
    " when 'week' then d + n * interval 7 day when 'month' then d + n * interval 1 month"
    " when 'year' then d + n * interval 1 year when 'hour' then d + n * interval 1 hour"
    " when 'minute' then d + n * interval 1 minute when 'second' then d + n * interval 1 second end",
]

SF_FORMAT = [("YYYY", "%Y"), ("YY", "%y"), ("MM", "%m"), ("DD", "%d"), ("HH24", "%H"),
             ("HH", "%I"), ("MI", "%M"), ("SS", "%S"), ("ATL", "%b")]


def sf_to_strftime(fmt):
    for sf, py in SF_FORMAT:
        fmt = fmt.replace(sf, py)
    return fmt


def rewrite_snowflake(sql):
    """Translate the Snowflake-only syntax people type most into DuckDB."""
    def to_date(m):
        return f"cast(strptime({m.group(2)}, '{sf_to_strftime(m.group(3))}') as {'date' if m.group(1).lower() == 'to_date' else 'timestamp'})"
    sql = re.sub(r"\b(to_date|to_timestamp(?:_ntz)?)\s*\(\s*([^,()]+?)\s*,\s*'([^']*)'\s*\)", to_date, sql, flags=re.I)
    sql = re.sub(r"\bdateadd\s*\(\s*'?(\w+)'?\s*,", r"__dateadd('\1',", sql, flags=re.I)
    sql = re.sub(r"\bdatediff\s*\(\s*'?(\w+)'?\s*,", r"date_diff('\1',", sql, flags=re.I)
    sql = re.sub(r"\bcurrent_timestamp\s*\(\s*\)", "cast(now() as timestamp)", sql, flags=re.I)
    sql = re.sub(r"\bvarchar\s*\(\s*\d+\s*\)", "varchar", sql, flags=re.I)
    sql = re.sub(r"\bnumber\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)", r"decimal(\1,\2)", sql, flags=re.I)
    return sql


def sf_type(duck_type):
    t = str(duck_type).upper()
    if t in ("VARCHAR", "STRING", "TEXT") or t.startswith("VARCHAR"):
        return "VARCHAR(16777216)"
    if t in ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "INT32", "INT64", "UBIGINT", "UINTEGER"):
        return "NUMBER(38,0)"
    if t.startswith("DECIMAL"):
        return t.replace("DECIMAL", "NUMBER")
    if t in ("DOUBLE", "FLOAT", "REAL"):
        return "FLOAT"
    if t == "DATE":
        return "DATE"
    if t.startswith("TIMESTAMP WITH TIME ZONE"):
        return "TIMESTAMP_TZ(9)"
    if t.startswith("TIMESTAMP"):
        return "TIMESTAMP_NTZ(9)"
    if t == "BOOLEAN":
        return "BOOLEAN"
    return t


def jsonable(v):
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return str(v)


def sf_error(err):
    """Rephrase DuckDB errors the way Snowflake words them."""
    msg = str(err)
    m = re.search(r'Table with name (\S+) does not exist', msg)
    if m:
        return f"SQL compilation error:\nObject '{m.group(1).upper()}' does not exist or not authorized."
    m = re.search(r'Referenced column "?([^"\s]+)"? not found', msg)
    if m:
        return f"SQL compilation error: error line 1 at position 0\ninvalid identifier '{m.group(1).upper()}'"
    m = re.search(r'Schema with name (\S+) does not exist', msg)
    if m:
        return f"SQL compilation error:\nSchema '{DB}.{m.group(1).upper()}' does not exist or not authorized."
    m = re.search(r'syntax error at or near "([^"]*)"', msg)
    if m:
        return f"SQL compilation error:\nsyntax error line 1 at position 0 unexpected '{m.group(1)}'."
    if "must appear in the GROUP BY" in msg:
        return "SQL compilation error:\n" + msg.split("\n")[0].replace("Binder Error: ", "") + "\n(is not a valid group by expression)"
    return "SQL compilation error:\n" + msg.split("\n")[0].split(": ", 1)[-1]


def connect(path=None, read_only=False, schema=None):
    """A visitor-facing connection: no file or network access, settings locked.

    Anything that runs SQL a visitor wrote (worksheets, previews, reading their views)
    goes through here, so read_csv('/etc/...'), COPY ... TO, ATTACH and INSTALL all fail.
    """
    con = duckdb.connect(str(path or B().warehouse), read_only=read_only, config={
        "enable_external_access": False,
        "memory_limit": os.environ.get("SANDBOX_DUCKDB_MEMORY", "512MB"),
        "threads": 2,
    })
    for shim in SHIMS:
        con.execute(shim)
    if schema:
        con.execute(f"set search_path = 'atlas_dev.{schema.lower()},atlas_dev.main'")
    con.execute("set lock_configuration = true")
    return con


def execute_with_timeout(con, sql, timeout=None):
    """Run one statement, interrupting it if it runs past the timeout."""
    timer = threading.Timer(timeout or SQL_TIMEOUT, con.interrupt)
    timer.start()
    try:
        return con.execute(sql)
    finally:
        timer.cancel()


def query_id():
    u = uuid.uuid4().hex
    return f"01b{u[:5]}-0000-{u[5:9]}-0000-{u[9:21]}"


def objects_in(con, schema=None):
    where = "table_catalog = 'atlas_dev' and table_schema not in ('main', 'information_schema')"
    if schema:
        where += f" and lower(table_schema) = '{schema.lower()}'"
    return con.execute(f"""
        select table_schema, table_name, table_type from information_schema.tables
        where {where} order by 1, 2""").fetchall()


def show_command(con, stmt, ctx):
    """SHOW TABLES / VIEWS / SCHEMAS / DATABASES and DESC TABLE, Snowflake-shaped."""
    s = stmt.strip().rstrip(";")
    m = re.match(r"(?is)^show\s+(terse\s+)?(tables|views|objects)(?:\s+like\s+'([^']*)')?(?:\s+in\s+(?:schema\s+)?([\w.]+))?$", s)
    if m:
        kind_wanted = m.group(2).lower()
        like = (m.group(3) or "%").lower().replace("%", ".*").replace("_", ".")
        schema = (m.group(4) or ctx["schema"]).split(".")[-1]
        cols = ["created_on", "name", "database_name", "schema_name", "kind", "comment", "rows", "owner"]
        rows = []
        for sch, name, typ in objects_in(con, schema):
            kind = "VIEW" if typ == "VIEW" else "TABLE"
            if kind_wanted == "tables" and kind != "TABLE" or kind_wanted == "views" and kind != "VIEW":
                continue
            if not re.fullmatch(like, name.lower()):
                continue
            count = con.execute(f'select count(*) from "{sch}"."{name}"').fetchone()[0] if kind == "TABLE" else None
            rows.append(["2026-11-09 06:00:00.000 -0800", name.upper(), DB, sch.upper(), kind, "", count, "DEVELOPER"])
        return cols, rows, f"{len(rows)} objects"
    if re.match(r"(?is)^show\s+schemas", s):
        names = sorted({r[0] for r in objects_in(con)})
        return ["created_on", "name", "database_name", "owner"], [["2026-11-09 06:00:00.000 -0800", n.upper(), DB, "DEVELOPER"] for n in names], None
    if re.match(r"(?is)^show\s+databases", s):
        return ["created_on", "name", "owner"], [["2026-09-01 06:00:00.000 -0700", DB, "SYSADMIN"]], None
    if re.match(r"(?is)^show\s+warehouses", s):
        return ["name", "state", "type", "size"], [["DEV_WH", "STARTED", "STANDARD", "X-Small"]], None
    m = re.match(r"(?is)^desc(?:ribe)?\s+(?:table|view)\s+([\w.\"]+)$", s)
    if m:
        name = m.group(1).replace('"', "")
        parts = name.split(".")
        rel = ".".join(parts[-2:]) if len(parts) >= 2 else f"{ctx['schema']}.{parts[-1]}"
        try:
            info = con.execute(f"describe {rel}").fetchall()
        except Exception as err:
            raise RuntimeError(sf_error(err))
        cols = ["name", "type", "kind", "null?", "default", "primary key", "unique key"]
        return cols, [[c[0].upper(), sf_type(c[1]), "COLUMN", "Y", None, "N", "N"] for c in info], None
    return None


def run_worksheet(sql):
    state = load_state() or default_state()
    ctx = state["context"]
    con = connect(schema=ctx["schema"])
    results = []
    for part in split_sql(sql):
        started = time.time()
        qid = query_id()
        entry = {"query_id": qid, "sql": part.strip()}
        try:
            use = re.match(r"(?is)^\s*use\s+(role|warehouse|database|schema)?\s*([\w.\"]+)\s*;?\s*$", part)
            shown = show_command(con, part, ctx) if re.match(r"(?is)^\s*(show|desc)", part) else None
            if use:
                what = (use.group(1) or "database").lower()
                value = use.group(2).replace('"', "").upper()
                if what == "schema" and "." in value:
                    ctx["database"], ctx["schema"] = value.split(".")[-2:]
                elif what == "database" and "." in value:
                    ctx["database"], ctx["schema"] = value.split(".")[-2:]
                else:
                    ctx[what] = value
                if what in ("schema", "database"):
                    con.close()
                    con = connect(schema=ctx["schema"])
                    con.execute(f"select 1 from information_schema.schemata where lower(schema_name) = '{ctx['schema'].lower()}'")
                entry.update(columns=[{"name": "status", "type": "VARCHAR"}],
                             rows=[["Statement executed successfully."]], rowcount=1)
            elif shown:
                cols, rows, _ = shown
                entry.update(columns=[{"name": c, "type": "VARCHAR"} for c in cols],
                             rows=[[jsonable(v) for v in r] for r in rows], rowcount=len(rows))
            else:
                rel = execute_with_timeout(con, rewrite_snowflake(part))
                if rel.description:
                    cols = [{"name": d[0].upper(), "type": sf_type(d[1])} for d in rel.description]
                    rows = [[jsonable(v) for v in r] for r in rel.fetchmany(10000)]
                    entry.update(columns=cols, rows=rows, rowcount=len(rows))
                else:
                    entry.update(columns=[{"name": "status", "type": "VARCHAR"}],
                                 rows=[["Statement executed successfully."]], rowcount=1)
        except Exception as err:
            entry["error"] = str(err) if str(err).startswith("SQL compilation") else sf_error(err)
        entry["elapsed"] = round(time.time() - started, 3)
        results.append(entry)
        state["query_history"].insert(0, {
            "query_id": qid, "sql": part.strip()[:2000], "status": "Failed" if "error" in entry else "Succeeded",
            "rows": entry.get("rowcount", 0), "elapsed": entry["elapsed"],
            "started": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "warehouse": ctx["warehouse"],
        })
        if "error" in entry:
            break
    state["query_history"] = state["query_history"][:200]
    state["context"] = ctx
    save_state(state)
    con.close()
    return {"results": results, "context": ctx}


def split_sql(sql):
    """Split on semicolons outside quotes and comments, keeping each statement's text."""
    parts, buf, quote, i = [], [], None, 0
    while i < len(sql):
        c = sql[i]
        if quote:
            buf.append(c)
            if c == quote:
                quote = None
        elif c in ("'", '"'):
            quote = c
            buf.append(c)
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            end = len(sql) if end == -1 else end
            buf.append(sql[i:end])
            i = end
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i)
            end = len(sql) if end == -1 else end + 2
            buf.append(sql[i:end])
            i = end
            continue
        elif c == ";":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if strip_comments(p).strip()]


def strip_comments(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    return re.sub(r"/\*.*?\*/", "", sql, flags=re.S)


def object_tree():
    con = connect(read_only=True)
    tree = {}
    for sch, name, typ in objects_in(con):
        cols = con.execute(f'describe "{sch}"."{name}"').fetchall()
        tree.setdefault(sch.upper(), []).append({
            "name": name.upper(), "kind": "VIEW" if typ == "VIEW" else "TABLE",
            "columns": [{"name": c[0].upper(), "type": sf_type(c[1])} for c in cols],
        })
    con.close()
    return {"database": DB, "schemas": tree}


# ================================================================ dbt

def clean_env(**extra):
    """A minimal environment for child processes: nothing from the server's own env
    (so {{ env_var(...) }} in a model can't read anything interesting)."""
    keep = ("PATH", "LANG", "LC_ALL", "PYTHONIOENCODING",
            # Windows local dev: without these, Windows creates a literal %SystemDrive% folder in cwd
            "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "LOCALAPPDATA", "APPDATA")
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["HOME"] = str(B().root)
    env.update(extra)
    return env


def run_dbt(args, warehouse=None, timeout=None):
    env = clean_env(
        ATLAS_WAREHOUSE=str(warehouse or B().warehouse),
        ATLAS_CHECK_WAREHOUSE=str(B().check_warehouse),
        ATLAS_SEEDS_DIR=str(B().repo / "seeds") + os.sep,
        ATLAS_TOOLS_DIR=str(TOOLS),
        DBT_PROFILES_DIR=str(PROFILES_DIR),
        DBT_USE_COLORS="false",
        DBT_SEND_ANONYMOUS_USAGE_STATS="false",
        DBT_PRINTER_WIDTH="100",
        DBT_LOG_PATH=str(B().repo / "logs"),
        DBT_TARGET_PATH=str(B().repo / "target"),
    )
    with DBT_SLOTS:
        try:
            proc = subprocess.run([str(DBT_EXE), *args], cwd=B().repo, env=env, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=timeout or DBT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return 1, f"ERROR: dbt timed out after {timeout or DBT_TIMEOUT} s"
    return proc.returncode, ANSI.sub("", proc.stdout + proc.stderr)


def parse_dbt_command(command):
    parts = shlex.split(command.strip())
    if not parts or parts[0] != "dbt":
        raise ValueError("Commands start with dbt, e.g.  dbt run --select tag:views")
    allowed = {"run", "build", "test", "compile", "seed", "ls", "list", "show", "parse"}
    if len(parts) < 2 or parts[1] not in allowed:
        raise ValueError(f"Unsupported dbt command. Try one of: {', '.join(sorted(allowed))}")
    prev = None
    for arg in parts[2:]:
        flag = arg.split("=", 1)[0]
        if flag in BLOCKED_FLAGS:
            raise ValueError(f"{flag} isn't available in the sandbox.")
        if ".." in arg and prev != "--vars" and not arg.startswith("--vars="):
            raise ValueError("'..' paths aren't allowed in the sandbox.")
        prev = arg
    return parts[1:]


BLOCKED_FLAGS = {"--profiles-dir", "--project-dir", "--target", "-t", "--profile", "--target-path",
                 "--log-path", "--state", "--defer-state", "--defer", "--favor-state", "--threads",
                 "--packages-install-path", "--log-level-file", "--log-format-file"}


NODE_LINE = re.compile(r"^\s*\d{2}:\d{2}:\d{2}\s+(\d+) of (\d+) (OK|ERROR|PASS|FAIL|WARN|SKIP)\s+(?:created |creating |running |)?(.*?)\s+\.{2,}.*?\[(.*?)\]")


def dbt_command(command):
    try:
        args = parse_dbt_command(command)
    except ValueError as err:
        return {"code": 1, "log": f"ERROR: {err}", "nodes": []}
    started = time.time()
    code, log = run_dbt(args)
    nodes = []
    for line in log.splitlines():
        m = NODE_LINE.match(line)
        if m:
            nodes.append({"status": m.group(3), "name": m.group(4).strip(), "detail": m.group(5)})
    return {"code": code, "log": log.strip(), "nodes": nodes, "elapsed": round(time.time() - started, 1)}


def model_name(path):
    p = Path(path)
    return p.stem if p.suffix == ".sql" and p.parts[0] == "models" else None


def compile_model(path):
    name = model_name(path)
    if not name:
        return {"ok": False, "error": "Open a model (.sql under models/) to compile it."}
    code, log = run_dbt(["compile", "--select", name, "--quiet"])
    compiled = B().repo / "target" / "compiled" / "atlas_conversion" / path
    if code != 0 or not compiled.exists():
        return {"ok": False, "error": log.strip()[-3000:]}
    return {"ok": True, "sql": compiled.read_text(encoding="utf-8")}


def preview_model(path):
    compiled = compile_model(path)
    if not compiled["ok"]:
        return compiled
    sql = compiled["sql"].strip().rstrip(";")
    con = connect(read_only=True)
    started = time.time()
    try:
        rel = execute_with_timeout(con, f"select * from ({sql}) as preview limit 500")
        cols = [{"name": d[0].upper(), "type": sf_type(d[1])} for d in rel.description]
        rows = [[jsonable(v) for v in r] for r in rel.fetchall()]
    except Exception as err:
        return {"ok": False, "error": sf_error(err)}
    finally:
        con.close()
    return {"ok": True, "columns": cols, "rows": rows, "elapsed": round(time.time() - started, 2), "sql": compiled["sql"]}


# ================================================================ repo / git

def repo_files(base=None):
    base = base or B().repo
    files = []
    for path in sorted(base.rglob("*")):
        rel = path.relative_to(base)
        if path.is_file() and not (set(rel.parts) & SKIP_DIRS) and not rel.name.startswith("."):
            files.append(rel.as_posix())
    return files


def safe_repo_path(rel):
    path = (B().repo / rel).resolve()
    if B().repo.resolve() not in path.parents:
        raise ValueError("path outside the repo")
    return path


def read_file(rel):
    return safe_repo_path(rel).read_text(encoding="utf-8")


MAX_FILE_BYTES = 200_000
MAX_FILES = 300
EDITABLE_SUFFIXES = {".sql", ".yml", ".yaml", ".csv", ".md", ".txt"}
DAG_FILE = "dags/delta_sync_dag.py"
PROJECT_PATH_KEYS = re.compile(r"-paths?$|^clean-targets$")


def check_editable(rel, text):
    """What a visitor may write: dbt source files plus the one DAG file. No Python
    models (dbt-duckdb would run them), no profile, no paths that leave the repo."""
    rel = Path(rel).as_posix()
    if rel != DAG_FILE and Path(rel).suffix.lower() not in EDITABLE_SUFFIXES:
        raise ValueError(f"Only {', '.join(sorted(EDITABLE_SUFFIXES))} files (and {DAG_FILE}) can be edited here.")
    parts = Path(rel).parts
    if set(parts) & SKIP_DIRS or rel == "profiles.yml" or any(p.startswith(".") for p in parts):
        raise ValueError(f"{rel} can't be edited in the sandbox.")
    if len(text.encode("utf-8")) > MAX_FILE_BYTES:
        raise ValueError(f"File too large (limit {MAX_FILE_BYTES // 1000} KB).")
    if rel == "dbt_project.yml":
        try:
            project = yaml.safe_load(text) or {}
        except yaml.YAMLError:
            return  # let dbt report the syntax error the way it normally would
        for key, value in (project.items() if isinstance(project, dict) else []):
            if PROJECT_PATH_KEYS.search(str(key)):
                values = value if isinstance(value, list) else [value]
                if any(not re.fullmatch(r"[A-Za-z0-9_-]+", str(v)) for v in values):
                    raise ValueError(f"{key} must be plain folder names inside the project.")


def write_file(rel, text):
    path = safe_repo_path(rel)
    check_editable(rel, text)
    if not path.exists() and len(repo_files()) >= MAX_FILES:
        raise ValueError("Too many files in the repo.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def delete_file(rel):
    safe_repo_path(rel).unlink()


def diff_between(old_base, new_base):
    files = []
    names = sorted(set(repo_files(old_base)) | set(repo_files(new_base)))
    for name in names:
        old = (old_base / name).read_text(encoding="utf-8").splitlines() if (old_base / name).exists() else []
        new = (new_base / name).read_text(encoding="utf-8").splitlines() if (new_base / name).exists() else []
        if old == new:
            continue
        diff = list(difflib.unified_diff(old, new, f"a/{name}", f"b/{name}", lineterm="", n=3))
        status = "added" if not old else ("removed" if not new else "modified")
        adds = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
        dels = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
        files.append({"path": name, "status": status, "additions": adds, "deletions": dels, "diff": "\n".join(diff)})
    return files


def git_status():
    state = load_state() or default_state()
    git = state["git"]
    committed_base = B().warehouse_dir / "committed"
    base = committed_base if committed_base.exists() else BASELINE
    return {
        "branch": git["branch"],
        "commits": git["commits"],
        "pr": git["pr"],
        "uncommitted": [{"path": f["path"], "status": f["status"]} for f in diff_between(base, B().repo)],
        "branch_changes": diff_between(BASELINE, committed_base) if committed_base.exists() else [],
    }


def create_branch(name):
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9._/-]+", name or ""):
        return {"ok": False, "error": "Branch names can use letters, numbers, . _ / and -"}
    if name in ("main", "master"):
        return {"ok": False, "error": "main is protected: create a feature branch."}
    state = load_state() or default_state()
    if state["git"]["branch"] != "main":
        return {"ok": False, "error": f"You're already on {state['git']['branch']}."}
    state["git"]["branch"] = name
    if state.get("jira_status") == "To Do":
        state["jira_status"] = "In Progress"
    save_state(state)
    return {"ok": True}


def commit(message):
    state = load_state() or default_state()
    if state["git"]["branch"] == "main":
        return {"ok": False, "error": "You can't commit to main. Create a branch first."}
    if not message.strip():
        return {"ok": False, "error": "Write a commit message."}
    committed = B().warehouse_dir / "committed"
    base = committed if committed.exists() else BASELINE
    changes = diff_between(base, B().repo)
    if not changes:
        return {"ok": False, "error": "Nothing to commit: no changes since your last commit."}
    if committed.exists():
        shutil.rmtree(committed)
    shutil.copytree(B().repo, committed, ignore=shutil.ignore_patterns(*SKIP_DIRS))
    sha = uuid.uuid4().hex[:7]
    state["git"]["commits"].append({"sha": sha, "message": message.strip(), "files": [c["path"] for c in changes],
                                    "at": datetime.now().strftime("%Y-%m-%d %H:%M")})
    save_state(state)
    return {"ok": True, "sha": sha}


def open_pr(title, body):
    state = load_state() or default_state()
    if not state["git"]["commits"]:
        return {"ok": False, "error": "Commit your work before opening a pull request."}
    state["git"]["pr"] = {"number": 1187, "title": title.strip() or state["git"]["commits"][-1]["message"],
                          "body": body, "state": "open", "checks": None,
                          "opened": datetime.now().strftime("%Y-%m-%d %H:%M")}
    state["jira_status"] = "In Review"
    save_state(state)
    return {"ok": True}


def update_pr(fields):
    state = load_state() or default_state()
    if not state["git"]["pr"]:
        return {"ok": False, "error": "No pull request open."}
    state["git"]["pr"].update(fields)
    save_state(state)
    return {"ok": True}


def merge_pr():
    state = load_state() or default_state()
    pr = state["git"]["pr"]
    if not pr:
        return {"ok": False, "error": "No pull request open."}
    checks = pr.get("checks") or {}
    if not checks.get("passed"):
        return {"ok": False, "error": "Required check atlas-ci has not passed."}
    if checks.get("sha") != (state["git"]["commits"][-1]["sha"] if state["git"]["commits"] else None):
        return {"ok": False, "error": "New commits since the last CI run: re-run checks first."}
    pr["state"] = "merged"
    state["jira_status"] = "Done"
    save_state(state)
    return {"ok": True}


def set_jira_status(status):
    state = load_state() or default_state()
    state["jira_status"] = status
    save_state(state)
    return {"ok": True}


# ================================================================ Airflow

class DagError(Exception):
    pass


def call_dag(func, conf, base=None):
    """Call build_dbt_vars / build_dbt_commands from the visitor's DAG file.

    The file is never imported into this process: tools/dag_runner.py checks it against an
    AST allow-list and runs it with restricted builtins in a separate, time- and
    memory-limited Python process, and only JSON comes back.
    """
    source = ((base or B().repo) / DAG_FILE).read_text(encoding="utf-8")
    payload = json.dumps({"source": source, "func": func, "conf": conf, "stub": str(AIRFLOW_STUB)})
    try:
        proc = subprocess.run([sys.executable, "-I", str(DAG_RUNNER)], input=payload, capture_output=True,
                              text=True, encoding="utf-8", timeout=10, cwd=str(TOOLS),
                              env=clean_env(PYTHONIOENCODING="utf-8"))
    except subprocess.TimeoutExpired:
        raise DagError("TimeoutError: the DAG file took longer than 10 s")
    lines = proc.stdout.strip().splitlines()
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        if proc.returncode < 0:  # killed by a signal: the CPU or memory rlimit
            raise DagError("ResourceError: the DAG file used too much CPU time or memory (infinite loop?)")
        raise DagError((proc.stderr.strip().splitlines() or ["DAG runner crashed"])[-1])
    if not out.get("ok"):
        raise DagError(out.get("error", "unknown error"))
    return out["result"]


class _DagModule:
    """What the grader used to get from importing the DAG: the two functions, safely."""
    def build_dbt_commands(self, conf):
        return call_dag("build_dbt_commands", conf)

    def build_dbt_vars(self, conf):
        return call_dag("build_dbt_vars", conf)


def load_dag_module():
    return _DagModule()


def trigger_dag(conf):
    state = load_state() or default_state()
    run_id = datetime.now().strftime("manual__%Y-%m-%dT%H:%M:%S")
    con = connect()
    started = con.execute("select cast(now() as timestamp)").fetchone()[0]
    con.close()
    conf = dict(conf)
    conf.setdefault("changed_since", state.get("last_dag_run") or "1900-01-01")
    tasks = []
    run = {"run_id": run_id, "conf": conf, "started": started.strftime("%Y-%m-%d %H:%M:%S"), "tasks": tasks, "state": "running"}

    def finish(run_state):
        run["state"] = run_state
        run["ended"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        state["dag_runs"].insert(0, run)
        state["dag_runs"] = state["dag_runs"][:30]
        if run_state == "success":
            state["last_dag_run"] = started.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
        return run

    # plan_commands
    log = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {{taskinstance.py}} INFO - Executing <Task(PythonOperator): plan_commands>",
           f"dag_run.conf = {json.dumps(conf)}"]
    try:
        commands = call_dag("build_dbt_commands", conf)
        if not isinstance(commands, list) or not all(isinstance(c, str) for c in commands):
            raise TypeError("build_dbt_commands must return a list of strings")
        log += ["Returned value was: " + json.dumps(commands, indent=2)]
        tasks.append({"task_id": "plan_commands", "state": "success", "log": "\n".join(log)})
    except Exception as err:
        log += [f"ERROR - {err}" if isinstance(err, DagError) else f"ERROR - {type(err).__name__}: {err}",
                "Task failed with exception"]
        tasks.append({"task_id": "plan_commands", "state": "failed", "log": "\n".join(log)})
        tasks += [{"task_id": t, "state": "upstream_failed", "log": ""} for t in ("run_dbt", "export_views_to_s3")]
        return finish("failed")

    # run_dbt
    log = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {{subprocess.py}} INFO - Running command: cd /opt/airflow/dbt/atlas_conversion && " + " && ".join(commands)]
    ok = True
    for command in commands:
        try:
            code, out = run_dbt(parse_dbt_command(command))
        except ValueError as err:
            code, out = 1, str(err)
        log += [f"$ {command}", out.strip(), ""]
        if code != 0:
            ok = False
            break
    tasks.append({"task_id": "run_dbt", "state": "success" if ok else "failed", "log": "\n".join(log)})
    if not ok:
        tasks.append({"task_id": "export_views_to_s3", "state": "upstream_failed", "log": ""})
        return finish("failed")

    # export_views_to_s3
    key = f"delta_sync/{run_id.replace(':', '')}"
    target = B().s3 / "atlas-exports" / key
    target.mkdir(parents=True, exist_ok=True)
    log = [f"[{datetime.now():%Y-%m-%d %H:%M:%S}] INFO - Unloading EXPORT_VIEWS to s3://atlas-exports/{key}/"]
    con = connect(read_only=True)
    for (view,) in con.execute("select table_name from information_schema.tables where table_schema = 'export_views' order by 1").fetchall():
        # Written from Python, not COPY ... TO: visitor connections have no file access.
        rel = execute_with_timeout(con, f'select * from export_views."{view}"')
        with open(target / (view.upper() + ".csv"), "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow([d[0] for d in rel.description])
            count = 0
            while batch := rel.fetchmany(5000):
                writer.writerows([[("" if v is None else v) for v in row] for row in batch])
                count += len(batch)
        log.append(f"INFO - COPY INTO @EXPORT_STAGE/{key}/{view.upper()}.csv FROM ATLAS_DEV.EXPORT_VIEWS.{view.upper()}  ->  {count} rows unloaded")
    con.close()
    tasks.append({"task_id": "export_views_to_s3", "state": "success", "log": "\n".join(log)})
    return finish("success")


def dag_source():
    return read_file("dags/delta_sync_dag.py")


# ================================================================ B().s3

def s3_list():
    root = B().s3 / "atlas-exports"
    items = []
    if root.exists():
        for path in sorted(root.rglob("*.csv"), reverse=True):
            items.append({"key": path.relative_to(root).as_posix(), "size": path.stat().st_size,
                          "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%B %d, %Y, %H:%M:%S")})
    return {"bucket": "atlas-exports", "objects": items}


def s3_object(key):
    root = (B().s3 / "atlas-exports").resolve()
    path = (root / key).resolve()
    if root not in path.parents:
        raise ValueError("bad key")
    return path.read_text(encoding="utf-8")


# ================================================================ reset

def reset():
    if B() != TEMPLATE and (TEMPLATE.root / ".ready").exists():
        # A visitor's reset: swap their box for a fresh copy of the template.
        if B().root.exists():
            shutil.rmtree(B().root)
        clone_template(B())
        return {"ok": True, "log": "Sandbox reset: repo, warehouse, S3 and git history are back to the starting point."}
    B().root.mkdir(parents=True, exist_ok=True)
    if B().repo.exists():
        shutil.rmtree(B().repo)
    shutil.copytree(BASELINE, B().repo)
    if B().warehouse_dir.exists():
        shutil.rmtree(B().warehouse_dir)
    B().warehouse_dir.mkdir()
    if B().s3.exists():
        shutil.rmtree(B().s3)
    B().s3.mkdir()
    con = duckdb.connect(str(B().warehouse))  # trusted, fixed SQL only
    con.execute(RAW_DATA_SQL)
    con.close()
    code, log = run_dbt(["build", "--full-refresh"])
    state = default_state()
    con = connect()
    state["last_dag_run"] = con.execute("select cast(now() as timestamp)").fetchone()[0].strftime("%Y-%m-%d %H:%M:%S")
    con.close()
    save_state(state)
    return {"ok": code == 0, "log": log.strip()[-4000:]}


def answer_key():
    """Diff of each file the answer key changes (the key holds only those files)."""
    files = []
    for name in repo_files(ANSWER_KEY):
        old = (BASELINE / name).read_text(encoding="utf-8").splitlines() if (BASELINE / name).exists() else []
        new = (ANSWER_KEY / name).read_text(encoding="utf-8").splitlines()
        diff = list(difflib.unified_diff(old, new, f"a/{name}", f"b/{name}", lineterm="", n=3))
        files.append({"path": name, "status": "added" if not old else "modified", "diff": "\n".join(diff)})
    return {"files": files}


from grader import check  # noqa: E402,F401
