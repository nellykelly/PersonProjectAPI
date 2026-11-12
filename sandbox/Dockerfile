# ATL-101 practice sandbox: real dbt-core + dbt-duckdb behind a stdlib HTTP server.
# Runs as the `sandbox` service in the site's docker-compose.yml, on an internal-only
# network (no internet, no published port). The site's /practice blueprint is the only
# way in: it checks the password and proxies requests here.
FROM python:3.12-slim

RUN useradd --create-home --shell /usr/sbin/nologin sandbox
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY baseline ./baseline
COPY answer_key ./answer_key
COPY tools ./tools
COPY web ./web

# /data holds the prebuilt template and every visitor's sandbox (a named volume in compose).
RUN mkdir -p /data && chown sandbox:sandbox /data
USER sandbox

ENV HOST=0.0.0.0 \
    PORT=8765 \
    SANDBOX_DATA=/data \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if b'\"template\": true' in urllib.request.urlopen('http://127.0.0.1:8765/healthz', timeout=4).read() else 1)"

CMD ["python", "tools/server.py"]
