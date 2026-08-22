"""Redis connection + RQ queue for Pipeline World's async character-join
processing -- the actual "async processing story" from the spec: a join
request enqueues a job and returns immediately, while a worker runs the
real validate/test/build/deploy pipeline in the background (see
app/services/pipeline.py).

Three environments, three behaviors:
  - **Docker / real Redis** (`REDIS_URL` set): jobs go on a real queue,
    consumed by the separate `worker.py` process (its own container in
    docker-compose).
  - **Local dev without Docker** (`REDIS_URL` unset): falls back to
    `fakeredis` (an in-memory Redis-compatible server) so there's still
    something to demo without needing a real Redis install. To keep that
    genuinely asynchronous instead of silently degrading to synchronous
    inline execution, a worker runs in a background thread of the same
    Flask process against that same fake connection.
  - **Tests** (`TESTING` config): synchronous (`is_async=False` -- RQ
    executes the job immediately inside `.enqueue()`, no worker/thread
    needed at all), for deterministic, fast tests.

One real, Windows-specific wrinkle the in-process fallback ran into during
development: RQ's default `Worker` forks a child process per job
(`os.fork()`), which doesn't exist on Windows at all, and separately
tries to install SIGINT/SIGTERM handlers, which only works on a
process's *main* thread. `SimpleWorker` (no forking -- required on
Windows regardless of the threading question) plus skipping signal
installation (needed because this specific worker runs in a background
thread) resolves both.
"""
from __future__ import annotations

import threading

from flask import Flask, current_app
from rq import Queue, SimpleWorker

QUEUE_NAME = "pipeline_world"


class _ThreadSafeSimpleWorker(SimpleWorker):
    def _install_signal_handlers(self):
        pass  # signal.signal() only works on a process's main thread


def init_app(app: Flask) -> None:
    redis_url = app.config.get("REDIS_URL")
    if redis_url:
        import redis

        connection = redis.Redis.from_url(redis_url)
    else:
        try:
            import fakeredis
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "REDIS_URL is unset and 'fakeredis' isn't installed. fakeredis is a real "
                "runtime dependency (see requirements.txt) for exactly this fallback -- "
                "either `pip install fakeredis` or set REDIS_URL to a real Redis instance."
            ) from exc

        connection = fakeredis.FakeStrictRedis()

    is_async = not app.config.get("TESTING", False)

    app.extensions["pipeline_redis"] = connection
    app.extensions["pipeline_queue"] = Queue(QUEUE_NAME, connection=connection, is_async=is_async)

    if is_async and not redis_url:
        _start_inprocess_worker(app, connection)


def _start_inprocess_worker(app: Flask, connection) -> None:
    def _run():
        with app.app_context():
            worker = _ThreadSafeSimpleWorker([QUEUE_NAME], connection=connection)
            worker.work(burst=False, with_scheduler=False)

    thread = threading.Thread(target=_run, daemon=True, name="pipeline-inprocess-worker")
    thread.start()


def get_redis_connection():
    return current_app.extensions["pipeline_redis"]


def get_queue() -> Queue:
    return current_app.extensions["pipeline_queue"]


def enqueue_character_join(character_id: int):
    from app.services.pipeline import run_pipeline

    return get_queue().enqueue(run_pipeline, character_id)
