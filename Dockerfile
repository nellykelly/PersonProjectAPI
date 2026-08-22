FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --shell /bin/bash appuser
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY wsgi.py ./

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
CMD ["gunicorn", "-w", "2", "--worker-class", "gthread", "--threads", "4", "-b", "0.0.0.0:8000", "wsgi:app"]
