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

    # Behind Caddy (docker-compose): trust exactly one hop of the
    # X-Forwarded-* headers it sets. Without this, Werkzeug sees the
    # request as http://web:8000 -- so `_external` URLs (canonical tag,
    # JSON-LD, og:url) carry the wrong scheme/host, and `request.remote_addr`
    # is the proxy's docker IP, which would make every per-IP rate limit
    # effectively global and the assistant's IP hash meaningless. Dev
    # (`flask run`, no proxy) leaves the WSGI app untouched.
    if config_name == "production":
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

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
    # throwaway/in-memory DB has no migration history to preserve anyway --
    # plus a dev-only SQLite column backfill so a file DB that predates a
    # new nullable model column doesn't 500 deep in a query (it did,
    # repeatedly, for the assistant analytics columns).
    if config_name != "production":
        with app.app_context():
            db.create_all()
            _dev_sqlite_add_missing_columns(app)

    return app


def _dev_sqlite_add_missing_columns(app: Flask) -> None:
    """Best-effort: for a file-backed SQLite dev DB, `ALTER TABLE ADD
    COLUMN` any nullable model column the table is missing. Never touches
    Postgres (migrations own that) or a NOT-NULL column (needs a real
    migration -- logs a warning instead)."""
    from sqlalchemy import inspect, text

    engine = db.engine
    if engine.dialect.name != "sqlite" or ":memory:" in str(engine.url):
        return

    inspector = inspect(engine)
    have_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in db.metadata.sorted_tables:
            if table.name not in have_tables:
                continue
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable and column.default is None and column.server_default is None:
                    app.logger.warning(
                        "dev DB: %s.%s is missing and can't be auto-added "
                        "(NOT NULL, no default) -- run `flask db upgrade` or "
                        "delete instance/site.db",
                        table.name,
                        column.name,
                    )
                    continue
                col_type = column.type.compile(engine.dialect)
                conn.execute(
                    text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {col_type}')
                )
                app.logger.info("dev DB: added %s.%s (%s)", table.name, column.name, col_type)


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
    from app.blueprints.job_tracker import bp as job_tracker_bp
    from app.blueprints.assistant import bp as assistant_bp
    from app.blueprints.legal import bp as legal_bp

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
    # Private job-application tracker. Password-gated (JOB_TRACKER_PASSWORD_HASH),
    # never linked anywhere, noindex. NOT csrf-exempt -- its forms carry a
    # token, unlike the older public-write blueprints below.
    app.register_blueprint(job_tracker_bp, url_prefix="/job-tracker")
    # Personal AI assistant: GET /assistant + POST /api/assistant/chat
    # (absolute rule paths, so no url_prefix). NOT csrf-exempt -- the
    # fetch() sends an X-CSRFToken header.
    app.register_blueprint(assistant_bp)
    # /legal -- terms, privacy, cookies, AI disclaimer, accessibility,
    # abuse contact. Absolute rule path, linked from the footer.
    app.register_blueprint(legal_bp)

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

    @app.cli.group("assistant")
    def assistant_cli() -> None:
        """Personal AI assistant maintenance."""

    @assistant_cli.command("reindex")
    def assistant_reindex() -> None:
        """Re-embed app/assistant_content/ into the content_chunks table."""
        from app.services import assistant as assistant_service

        info = assistant_service.reindex()
        click.echo(
            f"Reindexed {info['chunks']} chunks from {info['files']} files "
            f"(embedder: {info['embedder']})."
        )

    @app.cli.group("job-tracker")
    def job_tracker_cli() -> None:
        """Private job-application tracker maintenance."""

    @job_tracker_cli.command("sweep")
    @click.option(
        "--weeks",
        type=float,
        default=None,
        help="Override JOB_TRACKER_GHOST_AFTER_WEEKS for this run.",
    )
    @click.option(
        "--dry-run", is_flag=True, help="Show what would move without changing anything."
    )
    def job_tracker_sweep(weeks: float | None, dry_run: bool) -> None:
        """Move applications stuck in "Applied" past the threshold to "Ghosted".

        Nothing runs this automatically -- wire it to cron, e.g. daily:
            docker compose exec web flask job-tracker sweep
        """
        from app.services import job_tracker

        weeks = app.config["JOB_TRACKER_GHOST_AFTER_WEEKS"] if weeks is None else weeks

        if dry_run:
            from datetime import timedelta

            from app.models import JobApplication, utcnow

            cutoff = utcnow() - timedelta(weeks=weeks)
            stale = (
                JobApplication.query.filter(
                    JobApplication.status == "Applied",
                    JobApplication.status_updated_at < cutoff,
                )
                .order_by(JobApplication.status_updated_at.asc())
                .all()
            )
            if not stale:
                click.echo(f"Nothing has been in 'Applied' for {weeks} weeks.")
                return
            click.echo(f"Would ghost {len(stale)} application(s):")
            for a in stale:
                click.echo(f"  - {a.company_name} - {a.role_title} (since {a.status_updated_at:%Y-%m-%d})")
            return

        moved = job_tracker.sweep_stale_applications(weeks=weeks, source="cli")
        if not moved:
            click.echo(f"Nothing has been in 'Applied' for {weeks} weeks.")
            return
        click.echo(f"Ghosted {len(moved)} application(s):")
        for a in moved:
            click.echo(f"  - {a.company_name} - {a.role_title}")
