import os

# The README documents `python wsgi.py` as the local run command on
# Windows (gunicorn doesn't run there), but that path never read .env --
# only the `flask` CLI auto-loads it. So the documented command silently
# ran as production and died on the SECRET_KEY guard, while
# `flask --app wsgi run` worked, which reads as the app being broken
# rather than the entrypoint skipping a file. Loading it here makes both
# commands behave identically. Real environment variables still win:
# load_dotenv() does not override anything already set, so Docker and the
# VPS (which inject real values and ship no .env) are unaffected.
try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - dotenv is in requirements
    pass
else:
    load_dotenv()

from app import create_app  # noqa: E402  (must follow load_dotenv)

app = create_app(os.environ.get("FLASK_ENV", "production"))

if __name__ == "__main__":
    # threaded=True matters here: the Network Sniffer's live view and the
    # Trading Simulator's live watchlist both hold an SSE connection open
    # per viewer (net_monitor.py / watchlist.py), which would otherwise
    # block the dev server's single request-handling thread for every
    # other route.
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=app.config.get("DEBUG", False),
        threaded=True,
    )
