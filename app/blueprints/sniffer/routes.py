from flask import jsonify, render_template

from app.blueprints.sniffer import bp
from app.services import net_monitor


@bp.route("")
def index():
    return render_template("sniffer/index.html", analytics=net_monitor.get_analytics())


@bp.route("/api/analytics")
def api_analytics():
    """Polled by the page every few seconds for a live-feeling board
    without an open connection per viewer -- an aggregate rollup doesn't
    need per-entry push the way a raw log did, so a plain polled JSON
    endpoint replaces what used to be an SSE stream."""
    return jsonify(net_monitor.get_analytics())
