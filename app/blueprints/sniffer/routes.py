import json
import queue

from flask import Response, jsonify, render_template

from app.blueprints.sniffer import bp
from app.services import net_monitor

SSE_KEEPALIVE_SECONDS = 15


@bp.route("")
def index():
    return render_template("sniffer/index.html", stats=net_monitor.get_stats())


@bp.route("/api/log")
def api_log():
    """Initial snapshot on page load. Live updates after that come from
    /api/stream (SSE) -- see app/static/js/sniffer.js."""
    return jsonify({"entries": net_monitor.get_recent(200), "stats": net_monitor.get_stats()})


@bp.route("/api/stream")
def api_stream():
    """Server-Sent Events: pushes each new traffic entry to the browser
    the instant net_monitor records it, instead of the client polling on
    a fixed interval. One open connection per viewer -- held open by the
    Flask/gunicorn worker serving it, so the dev server needs threaded=True
    and the Dockerfile uses a threaded gunicorn worker class (see wsgi.py /
    Dockerfile) so a live-view tab doesn't block every other request."""

    def stream():
        q = net_monitor.subscribe()
        try:
            # A comment line (":...") is a valid, ignorable SSE payload --
            # sent immediately so the browser's EventSource fires onopen
            # right away instead of waiting for the first real entry.
            yield ": connected\n\n"
            while True:
                try:
                    entry = q.get(timeout=SSE_KEEPALIVE_SECONDS)
                    yield f"data: {json.dumps(entry)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            net_monitor.unsubscribe(q)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # disable nginx buffering, if ever fronted by one
    return response
