import os
import uuid
from datetime import datetime, timezone

from flask import Flask, session

from app.config import CONFIG_BY_NAME
from app.extensions import csrf, db, limiter, login_manager, migrate, socketio


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
    csrf.init_app(app)

    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Log in to open your board."
    login_manager.login_message_category = "error"

    @login_manager.user_loader
    def load_user(user_id: str):
        from app.models import User

        return db.session.get(User, int(user_id))

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

    from app.template_filters import register_filters

    register_filters(app)

    _register_blueprints(app)
    _register_cli(app)

    @app.before_request
    def ensure_session_id():
        if "session_id" not in session:
            session["session_id"] = str(uuid.uuid4())
            session.permanent = True

    @app.after_request
    def set_security_headers(response):
        """Baseline hardening headers on every response. HTTPS itself is
        Caddy's job (automatic cert + HTTP->HTTPS redirect); this adds the
        headers Caddy doesn't set on its own.

        CSP ships as report-only for now: the site has inline bootstrap
        scripts in base.html and pulls Chart.js / socket.io / mermaid from
        jsDelivr + cdn.socket.io, so an enforcing policy needs a pass to
        add nonces/SRI first. Report-only surfaces violations without
        breaking anything.
        """
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=(), interest-cohort=()"
        )
        if not app.debug and not app.testing:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        response.headers.setdefault(
            "Content-Security-Policy-Report-Only",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdn.socket.io; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response

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
    from app.blueprints.documentation import bp as documentation_bp
    from app.blueprints.trading import bp as trading_bp
    from app.blueprints.qr import bp as qr_bp
    from app.blueprints.sniffer import bp as sniffer_bp
    from app.blueprints.pipeline_world import bp as pipeline_world_bp
    from app.blueprints.sre_infra import bp as sre_infra_bp
    from app.blueprints.timed_squares import bp as timed_squares_bp
    from app.blueprints.leetcode import bp as leetcode_bp
    from app.blueprints.auth import bp as auth_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(about_bp, url_prefix="/about")
    app.register_blueprint(contact_bp, url_prefix="/contact")
    app.register_blueprint(projects_bp, url_prefix="/projects")
    app.register_blueprint(documentation_bp, url_prefix="/documentation")
    app.register_blueprint(trading_bp, url_prefix="/projects/trading-simulator")
    app.register_blueprint(qr_bp, url_prefix="/projects/qr-quant-scraper")
    app.register_blueprint(sniffer_bp, url_prefix="/projects/network-sniffer")
    app.register_blueprint(pipeline_world_bp, url_prefix="/projects/pipeline-world")
    app.register_blueprint(sre_infra_bp, url_prefix="/projects/sre-infra")
    app.register_blueprint(timed_squares_bp, url_prefix="/projects/timed-squares")
    app.register_blueprint(leetcode_bp, url_prefix="/leetcode-150")
    app.register_blueprint(auth_bp, url_prefix="/auth")

    # CSRF is on app-wide (see extensions.csrf), but these blueprints
    # predate accounts and post without a token -- from public,
    # anonymous forms and fetch() calls. Exempt them so nothing regresses;
    # only the new auth + tracker-progress POSTs are CSRF-checked. Rolling
    # tokens out to these is a separate, later change.
    for legacy_bp in (trading_bp, pipeline_world_bp, timed_squares_bp, documentation_bp):
        csrf.exempt(legacy_bp)


def _register_cli(app: Flask) -> None:
    """`flask create-user` / `flask set-password` -- account management
    from the shell. There is no email on file and no self-serve reset, so
    a forgotten password is fixed here by the site owner."""
    import click

    from app.models import User

    def _validate_password(password: str) -> None:
        if len(password) < User.PASSWORD_MIN:
            raise click.ClickException(f"Password must be at least {User.PASSWORD_MIN} characters.")

    @app.cli.command("create-user")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def create_user(username: str, password: str) -> None:
        """Create a login account."""
        norm = User.normalize_username(username)
        if not (User.USERNAME_MIN <= len(norm) <= User.USERNAME_MAX):
            raise click.ClickException(
                f"Username must be {User.USERNAME_MIN}-{User.USERNAME_MAX} characters."
            )
        if User.query.filter_by(username_ci=norm).first():
            raise click.ClickException(f"A user named {username!r} already exists.")
        _validate_password(password)
        user = User()
        user.set_username(username)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        click.echo(f"Created user {user.username!r} (id {user.id}).")

    @app.cli.command("set-password")
    @click.argument("username")
    @click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
    def set_password(username: str, password: str) -> None:
        """Reset an existing account's password."""
        user = User.query.filter_by(username_ci=User.normalize_username(username)).first()
        if user is None:
            raise click.ClickException(f"No user named {username!r}.")
        _validate_password(password)
        user.set_password(password)
        db.session.commit()
        click.echo(f"Password updated for {user.username!r}.")
