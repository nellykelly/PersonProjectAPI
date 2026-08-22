import json
import queue

from flask import Response, current_app, jsonify, render_template, request

from app.blueprints.sniffer import bp
from app.services import net_monitor, sse_limits

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

    client_ip = request.remote_addr or "unknown"
    try:
        sse_limits.acquire_sse_slot("sniffer", client_ip)
    except sse_limits.TooManyConnections as exc:
        return jsonify({"ok": False, "error": str(exc)}), 429

    # Captured here, while the request context is still active -- by the
    # time the generator body below actually runs (Werkzeug iterates it
    # lazily, after this view function has already returned), the
    # context is gone and current_app can't be resolved anymore. This
    # route didn't need current_app before release_sse_slot needed it --
    # without pushing an app context around the generator, the release
    # call in `finally` raises "working outside of application context"
    # the moment a client disconnects, which Python silently swallows
    # during generator cleanup -- so the slot leaks forever, exactly
    # defeating the point of the cap.
    app = current_app._get_current_object()

    def stream():
        with app.app_context():
            q = net_monitor.subscribe()
            try:
                # A comment line (":...") is a valid, ignorable SSE payload
                # -- sent immediately so the browser's EventSource fires
                # onopen right away instead of waiting for the first real
                # entry.
                yield ": connected\n\n"
                while True:
                    try:
                        entry = q.get(timeout=SSE_KEEPALIVE_SECONDS)
                        yield f"data: {json.dumps(entry)}\n\n"
                    except queue.Empty:
                        yield ": keepalive\n\n"
            finally:
                net_monitor.unsubscribe(q)
                sse_limits.release_sse_slot("sniffer", client_ip)

    response = Response(stream(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # disable nginx buffering, if ever fronted by one
    return response
