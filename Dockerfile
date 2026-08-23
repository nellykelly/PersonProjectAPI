FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY wsgi.py worker.py ./

RUN mkdir -p /app/instance && chown -R appuser:appuser /app
USER appuser

ENV FLASK_ENV=production \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# --worker-class gthread --threads: both the Network Sniffer's live view
# (app/services/net_monitor.py) and the Trading Simulator's live
# watchlist (app/services/watchlist.py) hold an SSE connection open per
# viewer, which would otherwise tie up an entire sync worker for as long
# as that tab stays open and block every other request routed to it.
#
# -w 1, not 2: Flask-SocketIO's "threading" async_mode keeps each client's
# engine.io session in that one process's memory. With more than one
# gunicorn worker *process*, consecutive HTTP requests for the same
# Socket.IO session get round-robined across processes that don't share
# that memory -- the process that didn't create the session returns 400
# "unknown session", which silently breaks Pipeline World's live
# build-log/table updates (the pipeline itself still completes correctly,
# since that part only depends on Postgres/Redis, not the browser socket).
# All request-handling concurrency now comes from threads instead, so
# --threads is doubled to keep the same total request-handling capacity
# (8 slots) the sizing comment above already assumed.
CMD ["gunicorn", "-w", "1", "--worker-class", "gthread", "--threads", "8", "--access-logfile", "-", "--error-logfile", "-", "-b", "0.0.0.0:8000", "wsgi:app"]
