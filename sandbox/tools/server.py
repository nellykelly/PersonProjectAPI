"""HTTP server for the ATL-101 practice sandbox.

Two ways to run it:
  * locally, on its own:  python tools/server.py   -> http://127.0.0.1:8765, one "local" sandbox
  * behind the personal site (docker compose service `sandbox`): HOST=0.0.0.0, never published to
    the internet. The site's /practice blueprint checks the password, then proxies here with an
    X-Sandbox-Session header naming the visitor's own sandbox.

Every session gets its own directory under $SANDBOX_DATA/sessions/<id>/ (a copy of a prebuilt
template), its own lock, and its own rate-limit bucket. Idle sessions are deleted after
SANDBOX_SESSION_TTL_HOURS; past SANDBOX_MAX_SESSIONS the least-recently-used idle one goes.
"""
import json
import os
import re
import shutil
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402

WEB = engine.ROOT / "web"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8765))
HOST = os.environ.get("HOST", "127.0.0.1")  # set HOST=0.0.0.0 inside a container
SESSIONS = engine.DATA / "sessions"
SESSION_TTL = float(os.environ.get("SANDBOX_SESSION_TTL_HOURS", "72")) * 3600
MAX_SESSIONS = int(os.environ.get("SANDBOX_MAX_SESSIONS", "10"))
MAX_BODY = 512 * 1024
RATE_LIMIT = int(os.environ.get("SANDBOX_RATE_PER_MINUTE", "240"))
SESSION_ID = re.compile(r"^(local|[a-f0-9]{32})$")
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml"}

REGISTRY_LOCK = threading.Lock()
SESSION_LOCKS = {}      # sid -> Lock (one thing at a time per sandbox)
LAST_USED = {}          # sid -> epoch seconds
HITS = {}               # sid -> deque of request times (rate limit)
GRADER_SLOTS = threading.BoundedSemaphore(int(os.environ.get("SANDBOX_GRADER_CONCURRENCY", "1")))


class Busy(Exception):
    pass


def get_routes(q):
    return {
        "/api/state": lambda: {**(engine.load_state() or engine.default_state()), "ready": engine.B().repo.exists()},
        "/api/tree": engine.object_tree,
        "/api/files": lambda: {"files": engine.repo_files()},
        "/api/file": lambda: {"path": q["path"], "text": engine.read_file(q["path"])},
        "/api/git": engine.git_status,
        "/api/dag/source": lambda: {"text": engine.dag_source()},
        "/api/s3": engine.s3_list,
        "/api/s3/object": lambda: {"text": engine.s3_object(q["key"])},
        "/api/answer": engine.answer_key,
    }


def post_routes(body):
    return {
        "/api/reset": engine.reset,
        "/api/sql": lambda: engine.run_worksheet(str(body.get("sql", ""))),
        "/api/file": lambda: (engine.write_file(body["path"], str(body["text"])), {"ok": True})[1],
        "/api/file/delete": lambda: (engine.delete_file(body["path"]), {"ok": True})[1],
        "/api/dbt": lambda: engine.dbt_command(str(body.get("command", ""))),
        "/api/compile": lambda: engine.compile_model(body["path"]),
        "/api/preview": lambda: engine.preview_model(body["path"]),
        "/api/git/branch": lambda: engine.create_branch(str(body.get("name", ""))),
        "/api/git/commit": lambda: engine.commit(str(body.get("message", ""))),
        "/api/git/pr": lambda: engine.open_pr(str(body.get("title", "")), str(body.get("body", ""))),
        "/api/git/pr/update": lambda: engine.update_pr({k: v for k, v in dict(body.get("fields", {})).items()
                                                        if k in ("title", "body")}),
        "/api/git/merge": engine.merge_pr,
        "/api/ci": run_ci,
        "/api/check": run_check,
        "/api/dag/trigger": lambda: engine.trigger_dag(dict(body.get("conf", {}))),
        "/api/jira/status": lambda: engine.set_jira_status(str(body.get("status", "To Do"))[:40]),
    }


def run_check():
    if not GRADER_SLOTS.acquire(timeout=120):
        raise Busy("The grader is busy with another run. Try again in a minute.")
    try:
        return engine.check()
    finally:
        GRADER_SLOTS.release()


def run_ci():
    """The PR's required check: the acceptance tests against what's committed on the branch."""
    state = engine.load_state() or engine.default_state()
    if not state["git"]["pr"]:
        return {"ok": False, "error": "No pull request open."}
    result = run_check()
    sha = state["git"]["commits"][-1]["sha"] if state["git"]["commits"] else None
    engine.update_pr({"checks": {"passed": bool(result.get("passed")), "sha": sha, "result": result}})
    return result


# ================================================================ sessions

def session_lock(sid):
    with REGISTRY_LOCK:
        return SESSION_LOCKS.setdefault(sid, threading.Lock())


def rate_limited(sid):
    now = time.time()
    with REGISTRY_LOCK:
        hits = HITS.setdefault(sid, deque())
        while hits and hits[0] < now - 60:
            hits.popleft()
        if len(hits) >= RATE_LIMIT:
            return True
        hits.append(now)
        return False


def touch(sid):
    LAST_USED[sid] = time.time()
    marker = SESSIONS / sid / ".last_used"
    if marker.parent.exists():
        marker.touch()


def evict(sid):
    """Delete a session's sandbox, but only if nobody is using it right now."""
    lock = session_lock(sid)
    if not lock.acquire(blocking=False):
        return False
    try:
        shutil.rmtree(SESSIONS / sid, ignore_errors=True)
        with REGISTRY_LOCK:
            LAST_USED.pop(sid, None)
            HITS.pop(sid, None)
        return True
    finally:
        lock.release()


def ensure_box(sid):
    """The visitor's sandbox, cloned from the template the first time they show up."""
    box = engine.Box(SESSIONS / sid)
    if box.root.exists():
        return box
    live = sorted((s for s in LAST_USED if s != sid and (SESSIONS / s).exists()), key=LAST_USED.get)
    while len(live) >= MAX_SESSIONS:
        if not evict(live.pop(0)):
            raise Busy("The sandbox is at capacity right now. Try again in a few minutes.")
    SESSIONS.mkdir(parents=True, exist_ok=True)
    engine.clone_template(box)
    return box


def reaper():
    while True:
        time.sleep(600)
        cutoff = time.time() - SESSION_TTL
        for sid, used in list(LAST_USED.items()):
            if used < cutoff:
                evict(sid)


def load_existing_sessions():
    """After a restart, pick up the sandboxes still on disk (they live on a volume)."""
    if not SESSIONS.exists():
        return
    for path in SESSIONS.iterdir():
        if path.name.endswith(".tmp"):
            shutil.rmtree(path, ignore_errors=True)
        elif SESSION_ID.match(path.name):
            marker = path / ".last_used"
            LAST_USED[path.name] = marker.stat().st_mtime if marker.exists() else path.stat().st_mtime


# ================================================================ HTTP

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # keep-alive; every response carries Content-Length
    server_version = "sandbox"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send_bytes(self, raw, status=200, ctype="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, default=str).encode("utf-8"), status)

    def session_id(self):
        sid = self.headers.get("X-Sandbox-Session") or "local"
        return sid if SESSION_ID.match(sid) else None

    def handle_api(self, make_route):
        sid = self.session_id()
        if sid is None:
            return self.send_json({"ok": False, "error": "bad session"}, 400)
        if rate_limited(sid):
            return self.send_json({"ok": False, "error": "Too many requests: slow down a little."}, 429)
        lock = session_lock(sid)
        if not lock.acquire(timeout=float(os.environ.get("SANDBOX_LOCK_WAIT", "600"))):
            return self.send_json({"ok": False, "error": "Your sandbox is still busy with the last command."}, 503)
        try:
            box = ensure_box(sid)
            token = engine.use_box(box)
            try:
                route = make_route()
                if route is None:
                    return self.send_json({"error": "not found"}, 404)
                touch(sid)
                self.send_json(route())
            finally:
                engine._box.reset(token)
        except Busy as err:
            self.send_json({"ok": False, "error": str(err)}, 503)
        except (ValueError, KeyError, FileNotFoundError, engine.DagError) as err:
            self.send_json({"ok": False, "error": f"{type(err).__name__}: {err}"}, 400)
        except Exception as err:
            traceback.print_exc()
            self.send_json({"ok": False, "error": f"{type(err).__name__}: {err}"}, 500)
        finally:
            lock.release()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/healthz":
            return self.send_json({"ok": True, "template": (engine.TEMPLATE.root / ".ready").exists()})
        if url.path.startswith("/api/"):
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            return self.handle_api(lambda: get_routes(q).get(url.path))
        rel = "index.html" if url.path in ("/", "") else url.path.lstrip("/")
        path = (WEB / rel).resolve()
        if WEB.resolve() not in path.parents or not path.is_file():
            return self.send_bytes(b"not found", 404, "text/plain; charset=utf-8")
        self.send_bytes(path.read_bytes(), 200, TYPES.get(path.suffix, "application/octet-stream") + "; charset=utf-8")

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self.close_connection = True
            return self.send_json({"ok": False, "error": "Request too large."}, 413)
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self.send_json({"ok": False, "error": "Body must be a JSON object."}, 400)
        return self.handle_api(lambda: post_routes(body).get(urlparse(self.path).path))


if __name__ == "__main__":
    print("Preparing the sandbox template (first run builds it with dbt, ~20 s)...", flush=True)
    engine.build_template()
    load_existing_sessions()
    threading.Thread(target=reaper, daemon=True).start()
    print(f"Atlas practice app on http://{HOST}:{PORT}", flush=True)
    ThreadingHTTPServer.request_queue_size = 128  # the default backlog of 5 resets parallel script loads on Windows
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
