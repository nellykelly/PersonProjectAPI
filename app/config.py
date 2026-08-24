"""Environment-driven configuration (12-factor style).

Nothing here is hardcoded that should vary between dev/prod/hosting
providers -- everything comes from the environment with a safe local
default, so swapping Render/Fly.io/a VPS later is just an env change.
"""
import os


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: list[str]) -> list[str]:
    val = os.environ.get(name)
    if not val:
        return default
    return [item.strip().upper() for item in val.split(",") if item.strip()]


# Curated whitelist of liquid large-cap tickers/ETFs. Used by both the
# trading simulator (position tickers) and the Company Scorer (scorable /
# backtestable tickers) so that user input never reaches yfinance/SEC
# EDGAR unvalidated.
DEFAULT_TICKER_WHITELIST = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX",
    "AMD", "INTC", "CSCO", "ORCL", "CRM", "ADBE", "PYPL", "IBM",
    "JPM", "BAC", "WFC", "GS", "MS", "V", "MA", "AXP",
    "KO", "PEP", "WMT", "HD", "PG", "COST", "MCD", "DIS",
    "XOM", "CVX", "JNJ", "PFE", "UNH", "ABBV",
    "BA", "CAT", "GE", "F", "GM",
    "SPY", "QQQ", "DIA", "IWM",
]

# Default equal-weight category weighting for the Company Scorer composite score.
# Configurable (not hardcoded logic) -- override via QR_WEIGHT_* env vars.
DEFAULT_QR_WEIGHTS = {
    "valuation": 0.25,
    "leverage": 0.25,
    "growth": 0.25,
    "profitability": 0.25,
}

# Pipeline stage metadata for the build-tracker UI on /projects/pipeline-world
# (a real CI/CD-style run table -- one row per character, one status cell
# per stage -- not a spatial thing, so no x/y here). Shared source of
# truth with app/models.py's PIPELINE_STAGES ordering.
PIPELINE_STAGE_INFO = {
    "sanitize": {"label": "Sanitize", "description": "Input hygiene: format, length, charset"},
    "security_scan": {"label": "Security Scan", "description": "Scan for HTML/script tags and SQL metacharacters"},
    "test_uniqueness": {"label": "Test: Uniqueness", "description": "Does this name already exist"},
    "test_profanity": {"label": "Test: Profanity", "description": "Does the name contain a blocked word"},
    "build": {"label": "Build", "description": "Assemble the spawn payload (position, appearance, icebreaker)"},
    "deploy": {"label": "Deploy", "description": "Write the character as live"},
    "verify": {"label": "Verify", "description": "Read the row back and confirm it landed correctly"},
}

# Production Town's own dedicated, large viewer (/projects/pipeline-world/town)
# gets a bigger virtual canvas space than the tracker page -- these bounds
# are in that space, not scaled down to fit alongside a join form. Shared
# source of truth between the backend (spawn position, pipeline.py) and
# the town viewer's frontend (idle-wander bounds).
PRODUCTION_TOWN_BOUNDS = (40, 40, 960, 560)  # x0, y0, x1, y1


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")  # resolved in create_app if unset

    # 1MB is generous for every form this app has (position open, pipeline
    # join, etc all post a handful of short fields) -- caps request bodies
    # so a client can't stream an arbitrarily large payload at the server.
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", str(1 * 1024 * 1024)))

    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_ENABLED = _bool("RATELIMIT_ENABLED", True)

    # SEC requires a descriptive User-Agent identifying the requester on
    # every data.sec.gov call -- see https://www.sec.gov/os/webmaster-faq#developers
    SEC_EDGAR_USER_AGENT = os.environ.get(
        "SEC_EDGAR_USER_AGENT", "PersonProjectAPI-portfolio-site koskela.nelson@gmail.com"
    )

    TICKER_WHITELIST = _list("TICKER_WHITELIST", DEFAULT_TICKER_WHITELIST)

    QR_WEIGHTS = {
        "valuation": float(os.environ.get("QR_WEIGHT_VALUATION", DEFAULT_QR_WEIGHTS["valuation"])),
        "leverage": float(os.environ.get("QR_WEIGHT_LEVERAGE", DEFAULT_QR_WEIGHTS["leverage"])),
        "growth": float(os.environ.get("QR_WEIGHT_GROWTH", DEFAULT_QR_WEIGHTS["growth"])),
        "profitability": float(os.environ.get("QR_WEIGHT_PROFITABILITY", DEFAULT_QR_WEIGHTS["profitability"])),
    }

    TRADING_MAX_OPEN_POSITIONS_PER_SESSION = int(
        os.environ.get("TRADING_MAX_OPEN_POSITIONS_PER_SESSION", "10")
    )
    TRADING_RATE_LIMIT = os.environ.get("TRADING_RATE_LIMIT", "10 per hour")
    # Read-only, but each one can hit yfinance (behind a short TTL cache,
    # see PriceCache/PRICE_CACHE_TTL_SECONDS) -- generous enough that a
    # single legitimate tab left open polling /api/quote every 15s (see
    # trading.js) never comes close, while still capping a script that
    # bypasses normal UI pacing entirely.
    TRADING_READ_RATE_LIMIT = os.environ.get("TRADING_READ_RATE_LIMIT", "300 per hour")
    # A risk request runs real Black-Scholes math and writes 2 DB rows --
    # cheap individually, but someone exploring several what-if scenarios
    # by hand still needs more headroom than the 10/hour open-position cap.
    TRADING_RISK_REQUEST_RATE_LIMIT = os.environ.get("TRADING_RISK_REQUEST_RATE_LIMIT", "30 per hour")
    PRICE_CACHE_TTL_SECONDS = int(os.environ.get("PRICE_CACHE_TTL_SECONDS", "60"))

    NET_MONITOR_BUFFER_SIZE = int(os.environ.get("NET_MONITOR_BUFFER_SIZE", "500"))

    # Password gate on /documentation/interview. A *hash* (Werkzeug's
    # PBKDF2 format), never the password itself -- this repo is public, so
    # a plaintext value here would be readable by anyone, and a hash is
    # useless to them. Generate one with:
    #   python -c "from werkzeug.security import generate_password_hash as g; print(g(input()))"
    # Unset means the section is closed entirely (see the route): failing
    # closed, rather than defaulting to open or to a checked-in fallback
    # that would then be the real password on every deployment.
    DOCS_PASSWORD_HASH = os.environ.get("DOCS_PASSWORD_HASH")
    # Deliberately tighter than every other limit on the site: this is the
    # one endpoint where repeated guessing is the entire attack.
    DOCS_UNLOCK_RATE_LIMIT = os.environ.get("DOCS_UNLOCK_RATE_LIMIT", "10 per hour")

    # Kept smaller than the full trading whitelist -- the backtest makes
    # 2 EDGAR calls + 2 yfinance calls per ticker, and both sources are
    # rate-limited on free/unauthenticated use.
    QR_BACKTEST_TICKERS = _list(
        "QR_BACKTEST_TICKERS",
        ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "JPM", "KO", "WMT", "XOM", "JNJ", "DIS", "INTC"],
    )
    QR_BACKTEST_CACHE_TTL_SECONDS = int(os.environ.get("QR_BACKTEST_CACHE_TTL_SECONDS", "3600"))
    QR_SCORE_CACHE_TTL_SECONDS = int(os.environ.get("QR_SCORE_CACHE_TTL_SECONDS", "900"))
    # One /score request costs 2 EDGAR calls + 2 yfinance calls; /backtest
    # is far heavier (that many calls per ticker across the whole basket),
    # so it gets a much tighter cap.
    QR_SCORE_RATE_LIMIT = os.environ.get("QR_SCORE_RATE_LIMIT", "20 per hour")
    QR_BACKTEST_RATE_LIMIT = os.environ.get("QR_BACKTEST_RATE_LIMIT", "5 per hour")

    # Live watchlist grid (trading simulator). The background poller only
    # runs while at least one browser tab is actually watching (see
    # app/services/watchlist.py) and only during market hours, to avoid
    # burning yfinance's free-tier rate-limit budget for no one.
    WATCHLIST_POLL_INTERVAL_SECONDS = int(os.environ.get("WATCHLIST_POLL_INTERVAL_SECONDS", "20"))
    WATCHLIST_TICKER_DELAY_SECONDS = float(os.environ.get("WATCHLIST_TICKER_DELAY_SECONDS", "0.3"))
    WATCHLIST_CLOSED_CHECK_INTERVAL_SECONDS = int(
        os.environ.get("WATCHLIST_CLOSED_CHECK_INTERVAL_SECONDS", "60")
    )

    # Pipeline World / SRE Infra Layer. REDIS_URL unset => fall back to an
    # in-memory fakeredis instance (see app/services/queue.py) so there's
    # still something to demo without a real Redis server -- real
    # deployment (docker-compose) sets this to the redis service's URL.
    REDIS_URL = os.environ.get("REDIS_URL")

    # Normal per-stage delay is a random 1-10s roll, decided fresh per
    # flow (see pipeline.py's SLOW_MODE_DELAY_RANGE) -- toggleable at
    # runtime to "fast mode" (no delay) via the Redis-backed flag in
    # pipeline.is_fast_mode(), not an env var. This is an escape-hatch
    # override only: set it to force every stage to a fixed delay
    # instead, bypassing both the random roll and the fast-mode toggle.
    # TestingConfig below uses exactly this to force 0 in tests.
    _pipeline_stage_delay_env = os.environ.get("PIPELINE_STAGE_DELAY_SECONDS", "").strip()
    PIPELINE_STAGE_DELAY_SECONDS = float(_pipeline_stage_delay_env) if _pipeline_stage_delay_env else None
    PIPELINE_JOIN_RATE_LIMIT = os.environ.get("PIPELINE_JOIN_RATE_LIMIT", "10 per hour")
    PIPELINE_FAST_MODE_RATE_LIMIT = os.environ.get("PIPELINE_FAST_MODE_RATE_LIMIT", "30 per hour")

    WORLD_CACHE_TTL_SECONDS = int(os.environ.get("WORLD_CACHE_TTL_SECONDS", "30"))

    # Timed-Squares: generous relative to the other public-write limits
    # above (trading/pipeline) on purpose -- submitting a score is the
    # normal end of every single run of an actual game, not an occasional
    # action, so a player replaying several rounds in a row shouldn't hit
    # this under normal play.
    TIMED_SQUARES_SCORE_RATE_LIMIT = os.environ.get("TIMED_SQUARES_SCORE_RATE_LIMIT", "60 per hour")
    # Sanity bound on a submitted score, not real anti-cheat (see
    # TimedSquaresScore's docstring) -- rejects an obviously-garbage
    # payload without pretending to validate that a score was legitimately
    # earned.
    TIMED_SQUARES_MAX_TURNS = int(os.environ.get("TIMED_SQUARES_MAX_TURNS", "100000"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False

    # Not set on DevelopmentConfig: SESSION_COOKIE_SECURE requires HTTPS,
    # which a local dev server on plain http://localhost doesn't have --
    # the cookie would silently never be sent and break every session.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False

    # No real per-stage delay in tests -- queue.py already makes RQ
    # execute jobs synchronously under TESTING, so this just keeps that
    # synchronous run fast (7 stages x 1.2s would otherwise slow every
    # single pipeline test down for no reason).
    PIPELINE_STAGE_DELAY_SECONDS = 0

    # An in-memory sqlite DB is otherwise per-connection -- without a
    # single shared (Static) connection, each request could see an empty
    # database. Only relevant for tests; file-based sqlite in dev/prod
    # doesn't need this.
    from sqlalchemy.pool import StaticPool

    SQLALCHEMY_ENGINE_OPTIONS = {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    }


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
}
