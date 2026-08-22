import os
import uuid
from datetime import datetime, timezone

from flask import Flask, session

from app.config import CONFIG_BY_NAME
from app.extensions import db, limiter


def create_app(config_name: str | None = None) -> Flask:
    config_name = config_name or os.environ.get("FLASK_ENV", "production")
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(CONFIG_BY_NAME.get(config_name, CONFIG_BY_NAME["production"]))

    os.makedirs(app.instance_path, exist_ok=True)
    if not app.config.get("SQLALCHEMY_DATABASE_URI"):
        db_path = os.path.join(app.instance_path, "site.db")
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{db_path}"

    db.init_app(app)
    limiter.init_app(app)

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

    app.register_blueprint(main_bp)
    app.register_blueprint(about_bp, url_prefix="/about")
    app.register_blueprint(contact_bp, url_prefix="/contact")
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(trading_bp, url_prefix="/projects/trading-simulator")
    app.register_blueprint(qr_bp, url_prefix="/projects/qr-quant-scraper")
    app.register_blueprint(sniffer_bp, url_prefix="/projects/network-sniffer")
