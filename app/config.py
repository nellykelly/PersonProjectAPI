"""Environment-driven configuration (12-factor style).

Nothing here is hardcoded that should vary between dev/prod/hosting
providers -- everything comes from the environment with a safe local
default, so swapping Render/Fly.io/a VPS later is just an env change.
"""
import os
import pathlib


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


def _origin_list(name: str, default: list[str]) -> list[str]:
    # Like _list, but for URL origins -- case matters (scheme/host), so
    # this doesn't uppercase entries the way the ticker-symbol helper does.
    val = os.environ.get(name)
    if not val:
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


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

    # Flask-SocketIO's own CORS setting (separate from the plain HTTP
    # routes, which send no CORS headers at all and so are already
    # same-origin-only by default). Permissive here so a bare `flask run`
    # on any host/port -- or someone testing from a different local port --
    # just works with no setup; ProductionConfig below locks this to the
    # real site origin(s), since a live-updating public WebSocket feed
    # (Pipeline World's town/tracker) is otherwise embeddable from any
    # third-party page. Comma-separated list of origins, or "*".
    SOCKETIO_CORS_ALLOWED_ORIGINS = _origin_list(
        "SOCKETIO_CORS_ALLOWED_ORIGINS", ["*"]
    )

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

    # Password gate on /job-tracker -- Nelson's private, real job-
    # application tracker (see job-tracker-spec.md). Exactly the same
    # fail-closed model as DOCS_PASSWORD_HASH above: a Werkzeug hash lives
    # in the environment, never a plaintext or checked-in value, and unset
    # means the whole section is closed (503), never open. A *separate*
    # secret from the docs gate on purpose -- different audience, and no
    # reason for one password to unlock both. The .env `$$`-escaping
    # gotcha applies (Werkzeug hashes contain `$`) -- see the README.
    JOB_TRACKER_PASSWORD_HASH = os.environ.get("JOB_TRACKER_PASSWORD_HASH")
    JOB_TRACKER_UNLOCK_RATE_LIMIT = os.environ.get(
        "JOB_TRACKER_UNLOCK_RATE_LIMIT", "10 per hour"
    )
    # `flask job-tracker sweep` moves an application that has sat in
    # "Applied" this many weeks (measured from status_updated_at) to
    # "Ghosted". Wire the command to cron; nothing runs it automatically.
    JOB_TRACKER_GHOST_AFTER_WEEKS = float(
        os.environ.get("JOB_TRACKER_GHOST_AFTER_WEEKS", "2.5")
    )

    # Job Discovery (/job-tracker/discover) -- automated search + LLM
    # match-scoring against Nelson's resume + site projects, feeding the
    # same private /job-tracker section (see app/services/job_discovery.py).
    # Adzuna's free tier (250 calls/day, https://developer.adzuna.com/).
    # Unset ADZUNA_APP_ID/KEY -> the "Search for jobs" button fails with a
    # clean error, same fail-soft pattern as the assistant's GROQ_API_KEY,
    # never a stack trace.
    ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID", "")
    ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY", "")
    ADZUNA_COUNTRY = os.environ.get("ADZUNA_COUNTRY", "us")
    # Adzuna's documented free-tier ceiling: https://developer.adzuna.com/.
    # This is the actual hard limit -- app.services.job_discovery checks
    # real, tracked usage (see the job_discovery_runs table) against this
    # number before every run and blocks *only* a run that would cross it,
    # rather than an earlier, guessed-conservative throttle. The
    # /job-tracker/discover page shows today's usage against this same
    # number, so the limit is visible, not just silently enforced.
    ADZUNA_DAILY_CALL_LIMIT = int(os.environ.get("ADZUNA_DAILY_CALL_LIMIT", "250"))
    # LLM scoring calls per run -- independent of how wide the search
    # matrix is (see job_discovery._round_robin_by_keyword): this is the
    # only thing that bounds Groq spend, since Adzuna call volume never
    # touches Groq at all. Each call is a live sequential Groq round-trip
    # (several seconds apiece measured), so this also bounds request
    # latency -- 25 has run several minutes in testing; raise with that
    # tradeoff in mind, not just for more coverage per click.
    JOB_DISCOVERY_MAX_NEW_PER_RUN = int(os.environ.get("JOB_DISCOVERY_MAX_NEW_PER_RUN", "25"))
    # A deliberate gap between each sequential scoring call -- observed
    # directly that a burst of calls with no gap can trip Groq's
    # free-tier rate limit (a call fails, then an identical one succeeds
    # seconds later with nothing else changed). Cheaper to pace calls
    # proactively than to rely solely on _score_listing's retry-with-
    # backoff to recover from tripping it every run.
    JOB_DISCOVERY_SCORE_PACING_SECONDS = float(
        os.environ.get("JOB_DISCOVERY_SCORE_PACING_SECONDS", "1.5")
    )
    # Groq's free tier turned out to enforce a *tokens-per-day* cap per
    # model, not just a request-rate limit -- discovered directly when
    # scoring started failing mid-testing on 2026-09-13 at 198,640/200,000
    # on qwen/qwen3.8-27b (the GROQ_TOOL_MODEL used for scoring). This is
    # that account's real number for that model, not a guess -- if the
    # model or account changes, or Groq changes its limits, override this.
    GROQ_DAILY_TOKEN_LIMIT = int(os.environ.get("GROQ_DAILY_TOKEN_LIMIT", "200000"))
    # The per-minute cap, which a run hits long before the daily one: 8,000
    # on the free tier for openai/gpt-oss-120b (x-ratelimit-limit-tokens,
    # read live 2026-09-23). Scoring and drafting pace themselves to it --
    # see job_discovery.pace_for_token_budget.
    JOB_DISCOVERY_GROQ_TPM = int(os.environ.get("JOB_DISCOVERY_GROQ_TPM", "8000"))
    # gpt-oss reasons before answering and that reasoning counts against
    # max_tokens; "low" answers a score in ~150 tokens where the default
    # used 300+ and returned nothing. Set empty for non-reasoning models.
    JOB_DISCOVERY_REASONING_EFFORT = os.environ.get("JOB_DISCOVERY_REASONING_EFFORT", "low")
    JOB_DISCOVERY_SCORE_MAX_TOKENS = int(os.environ.get("JOB_DISCOVERY_SCORE_MAX_TOKENS", "1000"))

    # Jev (TypeSafe AI) first-pass evaluation -- see app/services/jev.py.
    # Unset TYPESAFE_API_KEY -> Job Discovery keeps the keyword pre-score
    # and scores up to JOB_DISCOVERY_MAX_NEW_PER_RUN listings with Groq, as
    # before. Set -> Jev grades up to JEV_MAX_NEW_PER_RUN listings (input-
    # token priced, ~$0.0001 each, no Groq spend) and only the best
    # JOB_DISCOVERY_MAX_LLM_PER_RUN at or above JEV_LLM_THRESHOLD get a
    # Groq-written note.
    TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
    TYPESAFE_BASE_URL = os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai")
    JEV_MODEL = os.environ.get("JEV_MODEL", "jev-latest")
    JEV_MAX_NEW_PER_RUN = int(os.environ.get("JEV_MAX_NEW_PER_RUN", "60"))
    JEV_LLM_THRESHOLD = int(os.environ.get("JEV_LLM_THRESHOLD", "55"))
    JOB_DISCOVERY_MAX_LLM_PER_RUN = int(os.environ.get("JOB_DISCOVERY_MAX_LLM_PER_RUN", "15"))
    # A dedicated Groq API key for scoring only, separate from GROQ_API_KEY
    # (the public /assistant chat) and GROQ_TOOL_MODEL (Hera's owner
    # job-tracker tool-calls) -- Groq's daily token cap is scoped to an
    # organization + model, not per deployment or per feature, so sharing
    # a key meant Job Discovery competed with live site chat traffic (and,
    # that first day, with dev-side testing) for the same 200k/day budget.
    # See app.services.job_discovery._build_scoring_backend, which uses
    # this key directly (bypassing the assistant's shared build_backend())
    # whenever it's set. Empty -> falls back to GROQ_API_KEY, same
    # fail-soft pattern as everything else here (and how this ran before
    # a dedicated key existed).
    JOB_DISCOVERY_GROQ_API_KEY = os.environ.get("JOB_DISCOVERY_GROQ_API_KEY", "")
    # Market Data Warehouse's live "autonomous stock analysis" demo --
    # its own separate Groq account, not just a second key on the main
    # one (Groq's daily token cap is scoped to organization, not key --
    # confirmed the hard way with JOB_DISCOVERY_GROQ_API_KEY above), so a
    # heavy testing session or traffic spike on this demo can't eat into
    # the real /assistant chat's budget. build_backend()'s cache is keyed
    # by (kind, model, api_key) specifically so this and GROQ_API_KEY can
    # safely share the same model name without colliding on one cached
    # client. Falls back to GROQ_API_KEY when unset, same pattern as
    # every other per-feature key here.
    STOCK_ANALYSIS_GROQ_API_KEY = os.environ.get("STOCK_ANALYSIS_GROQ_API_KEY", "")
    # Deliberately NOT qwen/qwen3.8-27b: a second key on the *same* Groq
    # account doesn't help (confirmed directly -- the 429 still names the
    # same organization id regardless of which key made the request), but
    # the token cap is scoped to organization + model, so a different
    # model gets its own separate, currently-untouched daily pool on the
    # same account. This is what actually fixes the shared-budget problem.
    # llama-3.3-70b-versatile (this app's other documented fallback, see
    # GROQ_TOOL_MODEL) turned out to be retired from Groq's catalog --
    # 404s outright. openai/gpt-oss-120b is confirmed live on this account
    # via client.models.list(), and confirmed to score well against the
    # rubric in _SCORING_GUIDANCE (correctly dropped an 8-year-bar
    # "Senior" posting to 32, a Product Manager posting to 35, while
    # scoring genuine fits 92-94) -- check that list again if this 404s
    # later, Groq's catalog moves fast.
    JOB_DISCOVERY_GROQ_MODEL = os.environ.get("JOB_DISCOVERY_GROQ_MODEL", "openai/gpt-oss-120b")
    JOB_DISCOVERY_RESULTS_PER_PAGE = int(os.environ.get("JOB_DISCOVERY_RESULTS_PER_PAGE", "20"))
    JOB_DISCOVERY_MAX_PAGES = int(os.environ.get("JOB_DISCOVERY_MAX_PAGES", "1"))
    # A run searches every keyword against every location (plus one more
    # nationwide "remote" sweep per keyword) -- with the default ~13
    # titles x 3 location targets x 1 page, that's ~39 Adzuna calls in one
    # click, tracked and pre-flight-checked against ADZUNA_DAILY_CALL_LIMIT
    # (see job_discovery.run_discovery). This is a second, absolute floor
    # under that check for one single run, regardless of the daily
    # picture -- keeps a single click from ever launching an unbounded
    # keywords x locations x pages matrix if the settings grow a lot.
    JOB_DISCOVERY_MAX_ADZUNA_CALLS_PER_RUN = int(
        os.environ.get("JOB_DISCOVERY_MAX_ADZUNA_CALLS_PER_RUN", "45")
    )
    # A basic anti-double-click guard, not a quota mechanism -- the actual
    # quota enforcement is the precise pre-flight check against tracked
    # daily usage described above, so this just needs to stop a runaway
    # client (double submit, browser back-and-resubmit), not approximate
    # the real limit.
    JOB_DISCOVERY_RUN_RATE_LIMIT = os.environ.get("JOB_DISCOVERY_RUN_RATE_LIMIT", "30 per hour")
    # `flask job-tracker rescore` (see job_discovery.rescore_pending_listings)
    # retries any listing still at match_grade=NULL -- almost always
    # because Groq's daily quota was already spent when execute_run first
    # tried to score it. Nothing runs this automatically; wire it to cron,
    # e.g. hourly, same as JOB_TRACKER_GHOST_AFTER_WEEKS's sweep command.
    JOB_DISCOVERY_MAX_SCORE_ATTEMPTS = int(os.environ.get("JOB_DISCOVERY_MAX_SCORE_ATTEMPTS", "5"))
    JOB_DISCOVERY_RESCORE_BATCH_SIZE = int(os.environ.get("JOB_DISCOVERY_RESCORE_BATCH_SIZE", "20"))
    # A listing whose cheap keyword pre-score (see job_discovery
    # ._keyword_prescore) lands below this never reaches the LLM at all --
    # it's stored with that heuristic score as its match_grade instead,
    # clearly labeled (graded_by="keyword") so it's never confused with a
    # real judgement. Lower this to send more postings to the LLM (higher
    # quality, more quota spent); raise it to filter harder (less quota,
    # more risk of a real fit getting keyword-filtered out).
    JOB_DISCOVERY_KEYWORD_PRESCORE_THRESHOLD = int(
        os.environ.get("JOB_DISCOVERY_KEYWORD_PRESCORE_THRESHOLD", "25")
    )
    # Remotive's own API response states this explicitly ("we advise max.
    # 4 times a day... excessive requests will be blocked") -- a real,
    # provider-stated ceiling, not a guess. See
    # job_discovery._today_remotive_calls() / JobDiscoveryRun.remotive_calls.
    REMOTIVE_DAILY_CALL_LIMIT = int(os.environ.get("REMOTIVE_DAILY_CALL_LIMIT", "4"))
    # freehire.me aggregator (app/services/job_sources/freehire.py): keyless,
    # one call per keyword. FREEHIRE_API_URL can point at a self-hosted
    # instance (github.com/strelov1/freehire); FREEHIRE_COUNTRIES is a
    # comma-separated ISO alpha-2 list.
    FREEHIRE_API_URL = os.environ.get("FREEHIRE_API_URL", "https://freehire.me")
    FREEHIRE_COUNTRIES = os.environ.get("FREEHIRE_COUNTRIES", "US")
    FREEHIRE_POSTED_WITHIN_DAYS = int(os.environ.get("FREEHIRE_POSTED_WITHIN_DAYS", "30"))

    # ------------------------------------------------------------------
    # /family -- private household suite (app/blueprints/family)
    # ------------------------------------------------------------------
    # Same fail-closed model as the docs / job-tracker gates: a Werkzeug
    # hash in the environment, never a checked-in value; unset means the
    # whole /family section returns 503, never open. `$$`-escape the `$`
    # in docker-compose's .env.
    FAMILY_PASSWORD_HASH = os.environ.get("FAMILY_PASSWORD_HASH")
    FAMILY_UNLOCK_RATE_LIMIT = os.environ.get("FAMILY_UNLOCK_RATE_LIMIT", "10 per hour")
    # The Hera chatbot at /family/chat -- its own Groq key, separate from
    # the portfolio assistant's. Unset -> the chat panel is offline (503),
    # the rest of /family still works. FAMILY_GROQ_MODEL must support tool
    # calling. FAMILY_LLM_BACKEND is "groq" in real use; TestingConfig
    # forces "fake".
    FAMILY_GROQ_API_KEY = os.environ.get("FAMILY_GROQ_API_KEY", "")
    FAMILY_GROQ_MODEL = os.environ.get("FAMILY_GROQ_MODEL", "openai/gpt-oss-120b")
    FAMILY_LLM_BACKEND = os.environ.get("FAMILY_LLM_BACKEND", "groq")
    FAMILY_CHAT_RATE_LIMIT = os.environ.get("FAMILY_CHAT_RATE_LIMIT", "60 per hour")
    FAMILY_MAX_INPUT_CHARS = int(os.environ.get("FAMILY_MAX_INPUT_CHARS", "2000"))
    FAMILY_MAX_HISTORY_TURNS = int(os.environ.get("FAMILY_MAX_HISTORY_TURNS", "8"))
    FAMILY_MAX_OUTPUT_TOKENS = int(os.environ.get("FAMILY_MAX_OUTPUT_TOKENS", "700"))
    FAMILY_MEMORY_LIMIT = int(os.environ.get("FAMILY_MEMORY_LIMIT", "60"))
    # The one account the personal AI assistant will accept job-tracker
    # tool calls from (see personal-assistant-spec.md), matched case-
    # insensitively against User.username_ci. Decided as an env var rather
    # than an is_admin column: there is one owner, admin is never self-
    # assignable, and it needs no migration. Unused until the assistant
    # ships; defined here now so the decision is recorded in code.
    ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "")

    # Accounts (login gating the LeetCode 150 tracker). Registration is
    # open by default; flip REGISTRATION_ENABLED=false to close signups
    # without a redeploy (existing accounts keep working, /register starts
    # returning 403). Login and register are rate limited per IP -- login
    # is the guessing surface, register the spam surface.
    REGISTRATION_ENABLED = _bool("REGISTRATION_ENABLED", True)
    AUTH_LOGIN_RATE_LIMIT = os.environ.get("AUTH_LOGIN_RATE_LIMIT", "10 per hour")
    AUTH_REGISTER_RATE_LIMIT = os.environ.get("AUTH_REGISTER_RATE_LIMIT", "5 per hour")

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
    # Hera's check_character_status assistant tool -- read-only, but still
    # rate limited (its own bucket, not shared with PIPELINE_JOIN_RATE_LIMIT)
    # so a script can't hammer it to fish for characters crossing into
    # status="live" (the moment their free text becomes visible; see
    # Character.to_dict).
    ASSISTANT_CHARACTER_LOOKUP_RATE_LIMIT = os.environ.get(
        "ASSISTANT_CHARACTER_LOOKUP_RATE_LIMIT", "30 per hour"
    )

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

    # Market Data Warehouse: a sibling project (market-data-warehouse/),
    # not part of this app's own package -- yfinance -> dbt -> a
    # DuckDB/MotherDuck/Snowflake star schema. This page reads the
    # DuckDB file it writes, read-only, best-effort (see
    # app/blueprints/market_warehouse). Defaults to that project's own
    # default output path so a fresh checkout works with zero config.
    MARKET_WAREHOUSE_DB_PATH = os.environ.get(
        "MARKET_WAREHOUSE_DB_PATH",
        str(
            pathlib.Path(__file__).resolve().parent.parent
            / "market-data-warehouse"
            / "warehouse"
            / "market.duckdb"
        ),
    )

    # ------------------------------------------------------------------
    # Personal AI assistant (app/services/assistant, app/blueprints/assistant)
    # ------------------------------------------------------------------
    # Chat runs on Groq's free tier (OpenAI-shaped API, no card). Unset
    # GROQ_API_KEY => the /assistant page renders an "offline" panel and
    # the chat endpoint returns a clean 503, never a stack trace.
    GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
    # Groq rotates its catalogue; check `client.models.list()` if this
    # 404s. Qwen3 27B is a good default here -- fast, accurate on grounded
    # Q&A, and (unlike the gpt-oss models) it doesn't spend the output
    # budget on a reasoning preamble.
    GROQ_MODEL = os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")
    # A separate model for the one path that needs reliable function
    # calling: the owner's job-tracker tool calls (admin, signed in,
    # /job-tracker unlocked). Normal chat stays on GROQ_MODEL. Falls back
    # to GROQ_MODEL if this is blanked.
    GROQ_TOOL_MODEL = os.environ.get("GROQ_TOOL_MODEL", "llama-3.3-70b-versatile")

    # Which generation backend the orchestrator uses. "groq" in real use;
    # TestingConfig forces "fake" so the suite never touches the network.
    ASSISTANT_LLM_BACKEND = os.environ.get("ASSISTANT_LLM_BACKEND", "groq")
    # Local ONNX embedder (fastembed). "hash" is a deterministic, model-
    # free stand-in used by the tests.
    ASSISTANT_EMBEDDER = os.environ.get("ASSISTANT_EMBEDDER", "fastembed")
    ASSISTANT_EMBED_MODEL = os.environ.get("ASSISTANT_EMBED_MODEL", "BAAI/bge-small-en-v1.5")
    ASSISTANT_EMBED_DIM = int(os.environ.get("ASSISTANT_EMBED_DIM", "384"))

    ASSISTANT_RETRIEVAL_TOP_K = int(os.environ.get("ASSISTANT_RETRIEVAL_TOP_K", "5"))
    ASSISTANT_MAX_HISTORY_TURNS = int(os.environ.get("ASSISTANT_MAX_HISTORY_TURNS", "4"))
    ASSISTANT_MAX_INPUT_CHARS = int(os.environ.get("ASSISTANT_MAX_INPUT_CHARS", "1000"))
    ASSISTANT_MAX_OUTPUT_TOKENS = int(os.environ.get("ASSISTANT_MAX_OUTPUT_TOKENS", "600"))
    # A chat message is a real LLM call against a shared free-tier quota,
    # so this is tighter than the read-only limits elsewhere on the site.
    ASSISTANT_CHAT_RATE_LIMIT = os.environ.get("ASSISTANT_CHAT_RATE_LIMIT", "20 per hour")
    # The Market Data Warehouse page's "autonomous stock analysis" demo runs
    # a full multi-tool assistant turn (up to _MAX_TOOL_ITERS Groq calls, not
    # one) per hit, so it draws from a tighter bucket than a normal chat message.
    STOCK_ANALYSIS_RATE_LIMIT = os.environ.get("STOCK_ANALYSIS_RATE_LIMIT", "10 per hour")

    # Total characters of conversation history sent with a turn, on top of
    # the per-turn 2,000-char cap and ASSISTANT_MAX_HISTORY_TURNS. Oldest
    # turns are dropped first. 4 turns x 2 messages x 2,000 chars was ~4k
    # tokens of history in the worst case -- half the chat model's 8,000
    # tokens/minute on its own. 3,000 chars is roughly 750 tokens.
    ASSISTANT_MAX_HISTORY_CHARS = int(os.environ.get("ASSISTANT_MAX_HISTORY_CHARS", "3000"))
    # Wall-clock budget for one chat turn, measured from the start of
    # answer(). Checked before every model round trip: once it's spent, the
    # turn ends with a short "that took too long, ask me to keep going"
    # reply instead of starting another call. It can't interrupt a call
    # already in flight -- ASSISTANT_GROQ_TIMEOUT_SECONDS bounds that.
    ASSISTANT_TURN_DEADLINE_SECONDS = float(
        os.environ.get("ASSISTANT_TURN_DEADLINE_SECONDS", "40")
    )

    # Rate-limit resilience (app/services/assistant/backends.py). The Groq
    # SDK's own defaults (60s read timeout, 2 retries) once held a gunicorn
    # thread for 124s on one turn; these apply to the assistant's chat path
    # only (job discovery and /family keep the SDK defaults). A 429 on the
    # primary model falls through to each ASSISTANT_FALLBACK_MODELS entry
    # in order -- each Groq model has its own tokens/minute bucket -- and
    # only when every one is rate-limited does the visitor get a "busy,
    # try again in N seconds" 429.
    ASSISTANT_GROQ_TIMEOUT_SECONDS = float(os.environ.get("ASSISTANT_GROQ_TIMEOUT_SECONDS", "15"))
    ASSISTANT_GROQ_MAX_RETRIES = int(os.environ.get("ASSISTANT_GROQ_MAX_RETRIES", "0"))
    ASSISTANT_FALLBACK_MODELS = os.environ.get(
        "ASSISTANT_FALLBACK_MODELS", "openai/gpt-oss-20b,openai/gpt-oss-120b"
    )

    # Retrieval trimming (app/services/assistant/retrieval.py). A passage
    # is kept only if it scores at least MIN_SCORE and within SCORE_MARGIN
    # of the best hit, and the whole context block is capped at
    # MAX_CONTEXT_CHARS. Greetings and "who are you" questions skip
    # retrieval entirely -- there's nothing in the corpus they need.
    ASSISTANT_RETRIEVAL_MIN_SCORE = float(os.environ.get("ASSISTANT_RETRIEVAL_MIN_SCORE", "0.4"))
    ASSISTANT_RETRIEVAL_SCORE_MARGIN = float(
        os.environ.get("ASSISTANT_RETRIEVAL_SCORE_MARGIN", "0.08")
    )
    ASSISTANT_MAX_CONTEXT_CHARS = int(os.environ.get("ASSISTANT_MAX_CONTEXT_CHARS", "2600"))
    ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK = _bool(
        "ASSISTANT_SKIP_RETRIEVAL_FOR_SMALL_TALK", True
    )

    # Prompt Guard pre-screen (app/services/assistant/guard.py): a tiny
    # classifier on Groq, billed in its own bucket, that turns away obvious
    # prompt-injection attempts before any chat-model tokens are spent.
    # Fails open -- a guard outage never blocks a visitor.
    #
    # None (not True) when the env var is unset, on purpose: guard.screen()
    # treats None as "on" in real use but "off" under TESTING, and only an
    # explicit True opts a test back in. A literal True here would be
    # inherited by TestingConfig and switch the guard on for the whole
    # suite. Not overridden in TestingConfig for the same reason.
    ASSISTANT_GUARD_ENABLED = (
        _bool("ASSISTANT_GUARD_ENABLED", True)
        if os.environ.get("ASSISTANT_GUARD_ENABLED") is not None
        else None
    )
    ASSISTANT_GUARD_MODEL = os.environ.get(
        "ASSISTANT_GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m"
    )
    ASSISTANT_GUARD_TIMEOUT_SECONDS = float(os.environ.get("ASSISTANT_GUARD_TIMEOUT_SECONDS", "3"))
    ASSISTANT_GUARD_THRESHOLD = float(os.environ.get("ASSISTANT_GUARD_THRESHOLD", "0.9"))

    # Repeat-answer cache (app/services/assistant/cache.py), on the shared
    # Redis connection. Only first-turn, anonymous, tool-free answers are
    # ever stored; the key includes the corpus and prompt versions, so a
    # reindex or a prompt edit retires every old entry on its own.
    ASSISTANT_CACHE_ENABLED = _bool("ASSISTANT_CACHE_ENABLED", True)
    ASSISTANT_CACHE_TTL_SECONDS = int(os.environ.get("ASSISTANT_CACHE_TTL_SECONDS", "21600"))

    # `flask assistant eval` paces itself to stay under this many prompt +
    # completion tokens per minute (the chat model's cap is 8,000; this
    # leaves headroom for real visitors during a live run).
    ASSISTANT_EVAL_TPM = int(os.environ.get("ASSISTANT_EVAL_TPM", "7000"))


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False

    # Not set on DevelopmentConfig: SESSION_COOKIE_SECURE requires HTTPS,
    # which a local dev server on plain http://localhost doesn't have --
    # the cookie would silently never be sent and break every session.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Flask-Login's "keep me signed in" cookie is separate from the session
    # cookie and does NOT inherit SESSION_COOKIE_* -- without these it would
    # be sent over plain HTTP and readable by JS. HttpOnly is already the
    # Flask-Login default; set explicitly so it can't regress silently.
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"

    # Locked to the real site origins by default, unlike the wide-open
    # base default above -- override via the env var if the domain ever
    # changes, but production should never silently fall back to "*".
    SOCKETIO_CORS_ALLOWED_ORIGINS = _origin_list(
        "SOCKETIO_CORS_ALLOWED_ORIGINS",
        ["https://nelsonkoskela.dev", "https://www.nelsonkoskela.dev"],
    )


class TestingConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    RATELIMIT_ENABLED = False
    WTF_CSRF_ENABLED = False

    # The assistant never touches the network or downloads a model in the
    # suite: a canned generation backend and a deterministic, model-free
    # embedder, with retrieval served from an in-memory store instead of
    # pgvector (which needs Postgres -- exercised by its own gated tests).
    ASSISTANT_LLM_BACKEND = "fake"
    ASSISTANT_EMBEDDER = "hash"
    # /family Hera chatbot: canned backend, no network. FAMILY_PASSWORD_HASH
    # is left unset so gated tests inject it per fixture (like job-tracker).
    FAMILY_LLM_BACKEND = "fake"
    # Never a live Jev call from the suite, even with a key in .env --
    # Jev tests set their own fake key and stub the HTTP call.
    TYPESAFE_API_KEY = ""

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
