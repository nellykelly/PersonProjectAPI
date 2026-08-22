from flask import current_app, jsonify, render_template

from app.blueprints.sre_infra import bp
from app.services import queue, world_cache


def _queue_stats() -> dict:
    q = queue.get_queue()
    registry = q.failed_job_registry
    return {
        "queued": q.count,
        "failed": len(registry),
        "using_real_redis": bool(current_app.config.get("REDIS_URL")),
    }


@bp.route("")
def index():
    return render_template(
        "sre_infra/index.html",
        queue_stats=_queue_stats(),
        cache_stats=world_cache.get_cache_stats(),
        rate_limit=current_app.config["PIPELINE_JOIN_RATE_LIMIT"],
        redis_url_configured=bool(current_app.config.get("REDIS_URL")),
    )


@bp.route("/api/stats")
def api_stats():
    return jsonify(
        {
            "queue": _queue_stats(),
            "cache": world_cache.get_cache_stats(),
        }
    )
