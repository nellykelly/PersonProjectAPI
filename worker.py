"""RQ worker entrypoint for Pipeline World (see app/services/queue.py).

Runs as its own process -- a dedicated `worker` service in
docker-compose -- consuming the same Redis queue the web process
enqueues jobs onto (app.services.pipeline.run_pipeline).

Uses RQ's `SimpleWorker` (no forking) rather than the default `Worker`,
for the same reason queue.py's local-dev-without-Docker fallback has
to: RQ's default Worker forks a child process per job (`os.fork()`),
which doesn't exist on Windows at all. Using SimpleWorker everywhere
means this exact code path is what's already exercised by the
in-process fallback during local development, not a second, untested
one that only runs in Docker.
"""
import os

from rq import SimpleWorker

from app import create_app
from app.services.queue import QUEUE_NAME, get_redis_connection

app = create_app(os.environ.get("FLASK_ENV", "production"))

if __name__ == "__main__":
    with app.app_context():
        worker = SimpleWorker([QUEUE_NAME], connection=get_redis_connection())
        worker.work()
