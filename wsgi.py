import os

from app import create_app

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
