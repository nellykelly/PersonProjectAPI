import os
import uuid
from datetime import datetime, timezone

from flask import Flask, session

from app.config import CONFIG_BY_NAME
from app.extensions import db, limiter, migrate, socketio


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "production")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIG_BY_NAME.get(config_name, CONFIG_BY_NAME["production"]))

    if config_name == "production" and app.config["SECRET_KEY"] == "dev-insecure-secret-change-me":
        raise RuntimeError(
            "SECRET_KEY must be set to a real secret in production -- the "
            "insecure dev default (visible in this repo's source, and used "
            "as the .env.example placeholder) would let anyone forge session "
            "cookies. Set the SECRET_KEY environment variable to a generated "
            "value, e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`."
        )

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        db_path = os.path.join(app.instance_path, "site.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    db.init_app(app)
    migrate.init_app(app, db)
    limiter.init_app(app)
    socketio.init_app(
        app,
        async_mode="threading",
        message_queue=app.config.get("REDIS_URL") or None,
        cors_allowed_origins="*",
    )

    from app.services import queue as pipeline_queue

    pipeline_queue.init_app(app)

    from app.services.net_monitor import register_request_hooks

    register_request_hooks(app)

    _register_blueprints(app)

    @app.before_request
    def ensure_session_id():
        if "session_id" not in session:
            session["session_id"] = str(uuid.uuid4())
            session.permanent = True

    @app.context_processor
    def inject_site_links():
        return {
            "GITHUB_URL": "https://github.com/nellykelly",
            "LINKEDIN_URL": "https://www.linkedin.com/in/nelson-k-70180a101",
            "EMAIL": "koskela.nelson@gmail.com",
            "RESUME_PATH": "assets/files/Nelson_Koskela_Resume.pdf",
            "CURRENT_YEAR": datetime.now(timezone.utc).year,
        }

    @app.errorhandler(404)
    def not_found(_error):
        from flask import render_template

        return render_template("errors/404.html"), 404

    from app import models  # noqa: F401  (ensures models are registered before create_all)

    # In production, schema changes go through `flask db upgrade` (Flask-
    # Migrate) so they're tracked and reversible -- db.create_all() only
    # ever adds missing tables and can never alter an existing one, which
    # has silently masked real schema drift more than once during
    # development. Dev/test keep the create_all() convenience since a
    # throwaway/in-memory DB has no migration history to preserve anyway.
    if config_name != "production":
        with app.app_context():
            db.create_all()

    return app


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.main import bp as main_bp
    from app.blueprints.about import bp as about_bp
    from app.blueprints.contact import bp as contact_bp
    from app.blueprints.projects import bp as projects_bp
    from app.blueprints.trading import bp as trading_bp
    from app.blueprints.qr import bp as qr_bp
    from app.blueprints.sniffer import bp as sniffer_bp
    from app.blueprints.pipeline_world import bp as pipeline_world_bp
    from app.blueprints.sre_infra import bp as sre_infra_bp
    from app.blueprints.timed_squares import bp as timed_squares_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(about_bp, url_prefix="/about")
    app.register_blueprint(contact_bp, url_prefix="/contact")
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(trading_bp, url_prefix="/projects/trading-simulator")
    app.register_blueprint(qr_bp, url_prefix="/projects/qr-quant-scraper")
    app.register_blueprint(sniffer_bp, url_prefix="/projects/network-sniffer")
    app.register_blueprint(pipeline_world_bp, url_prefix="/projects/pipeline-world")
    app.register_blueprint(sre_infra_bp, url_prefix="/projects/sre-infra")
    app.register_blueprint(timed_squares_bp, url_prefix="/projects/timed-squares")
