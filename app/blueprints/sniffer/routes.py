from flask import jsonify, render_template

from app.blueprints.sniffer import bp
from app.services import net_monitor


@bp.route("")
def index():
    return render_template("sniffer/index.html", stats=net_monitor.get_stats())


@bp.route("/api/log")
def api_log():
    return jsonify({"entries": net_monitor.get_recent(200), "stats": net_monitor.get_stats()})
