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
# trading simulator (position tickers) and the QR scorer (scorable /
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

# Default equal-weight category weighting for the QR composite score.
# Configurable (not hardcoded logic) -- override via QR_WEIGHT_* env vars.
DEFAULT_QR_WEIGHTS = {
    "valuation": 0.25,
    "leverage": 0.25,
    "growth": 0.25,
    "profitability": 0.25,
}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret-change-me")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL")  # resolved in create_app if unset

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
    PRICE_CACHE_TTL_SECONDS = int(os.environ.get("PRICE_CACHE_TTL_SECONDS", "60"))

    NET_MONITOR_BUFFER_SIZE = int(os.environ.get("NET_MONITOR_BUFFER_SIZE", "500"))

    # Kept smaller than the full trading whitelist -- the backtest makes
    # 2 EDGAR calls + 2 yfinance calls per ticker, and both sources are
    # rate-limited on free/unauthenticated use.
    QR_BACKTEST_TICKERS = _list(
        "QR_BACKTEST_TICKERS",
        ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "JPM", "KO", "WMT", "XOM", "JNJ", "DIS", "INTC"],
    )
    QR_BACKTEST_CACHE_TTL_SECONDS = int(os.environ.get("QR_BACKTEST_CACHE_TTL_SECONDS", "3600"))
    QR_SCORE_CACHE_TTL_SECONDS = int(os.environ.get("QR_SCORE_CACHE_TTL_SECONDS", "900"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False

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
