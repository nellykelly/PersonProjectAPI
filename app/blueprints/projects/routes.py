from flask import render_template

from app.blueprints.projects import bp

PROJECTS = [
    {
        "slug": "trading-simulator",
        "title": "Trading Simulator / PnL Tracker",
        "blurb": "A shared public trade book. Open a simulated stock or option position "
        "and watch the PnL move against live market data.",
        "endpoint": "trading.index",
        "tags": ["Flask", "yfinance", "SQLite", "Black-Scholes"],
        "icon": "assets/img/icons/trading.svg",
    },
    {
        "slug": "qr-quant-scraper",
        # Renamed from "Company Scorer". The URL slug keeps its
        # original wording on purpose: it is already linked from the docs
        # and anywhere else the site has been shared, and a display name
        # is free to change while a URL is not.
        "title": "Company Scorer",
        "blurb": "Scores a company on valuation, leverage, growth and profitability from "
        "SEC EDGAR filings and market data, then backtests whether the score "
        "predicted anything.",
        "endpoint": "qr.index",
        "tags": ["SEC EDGAR API", "yfinance", "Backtesting"],
        "icon": "assets/img/icons/qr-scorer.svg",
    },
    {
        "slug": "pipeline-world",
        "title": "Pipeline World",
        "blurb": "Submit a character and watch it move through a real queued pipeline "
        "(validate, test, build, deploy) in a top-down world. Every run is logged "
        "and queryable on the analytics page.",
        "endpoint": "pipeline_world.index",
        "tags": ["RQ + Redis", "Socket.IO", "Postgres", "Window Functions"],
        "icon": "assets/img/icons/pipeline-world.svg",
    },
    {
        "slug": "sre-infra",
        "title": "SRE Infra Layer",
        "blurb": "The Redis queue, cache-aside world state, and per-IP rate limiting that "
        "Pipeline World runs on, with a live dashboard.",
        "endpoint": "sre_infra.index",
        "tags": ["Redis", "Cache-Aside", "Rate Limiting"],
        "icon": "assets/img/icons/sre-infra.svg",
    },
    {
        "slug": "network-sniffer",
        "title": "Site Traffic Analytics",
        "blurb": "An analytics board over this app's own network traffic: request volume over "
        "time, latency percentiles, error rate, and the busiest endpoints and outbound calls. "
        "Not visitor browsing, for reasons covered on the page.",
        "endpoint": "sniffer.index",
        "tags": ["Flask hooks", "Analytics"],
        "icon": "assets/img/icons/sniffer.svg",
    },
    {
        "slug": "timed-squares",
        "title": "Timed-Squares",
        "blurb": "A turn-based survival game on a 10x10 grid, playable right in the browser. "
        "Dodge obstacles that telegraph their next move before they make it, and outlast "
        "an escalating spawn rate. Public leaderboard, no login.",
        "endpoint": "timed_squares.index",
        "tags": ["Canvas", "Vanilla JS", "Public leaderboard"],
        "icon": "assets/img/icons/timed-squares.svg",
    },
    {
        "slug": "tiny-jvm",
        "title": "Tiny JVM",
        "blurb": "A compact stack-based virtual machine written in C that runs the same "
        "bytecode in the browser (WebAssembly) and on a microcontroller. Programs are "
        "written in a small custom language and compiled to that bytecode by a Java "
        "toolchain (lexer, parser, code generator). One over-temp-alarm program even "
        "drives a simulated GPIO pin on a Wokwi board.",
        "endpoint": "tiny_jvm.index",
        "tags": ["Java", "Embedded / C", "WebAssembly", "Compilers"],
        "icon": "assets/img/icons/tiny-jvm.svg",
    },
    {
        "slug": "market-warehouse",
        "title": "Market Data Warehouse",
        "blurb": "yfinance's full price/dividend/split history pulled through an idempotent "
        "ingestion job into a dbt dimensional warehouse: a fact-table family (price, "
        "technical indicators, risk metrics) sharing conformed dimensions, plus a clearly "
        "labeled (not financial advice) trend projection. This page queries the built "
        "warehouse live, with a timeframe picker. Same dbt code runs on DuckDB, MotherDuck's "
        "free tier, or Snowflake.",
        "endpoint": "market_warehouse.index",
        "tags": ["dbt", "DuckDB", "Star Schema", "yfinance"],
        "icon": "assets/img/icons/market-warehouse.svg",
    },
    {
        "slug": "leetcode-150",
        "title": "Top Interview 150 Tracker",
        "blurb": "A personal interview-prep dashboard over LeetCode's official Top Interview "
        "150. Pick a problem, run the 20-minute timer, compare to the solution, then mark it "
        "Yes or No. Progress is saved in the browser, no login.",
        "endpoint": "leetcode.index",
        "tags": ["Vanilla JS", "localStorage", "No backend state"],
        "icon": "assets/img/icons/leetcode.svg",
    },
    {
        "slug": "assistant",
        "title": "AI Assistant",
        "blurb": "A retrieval-grounded chatbot that answers questions about this site and its "
        "projects, using only a curated content corpus: RAG over a pgvector store, local "
        "embeddings, and Groq for generation behind a rate-limited, prompt-injection-aware "
        "public endpoint. A stats page tracks tone, volume, and top questions from the logs, "
        "with no model calls.",
        "endpoint": "assistant.index",
        "tags": ["RAG", "pgvector", "Embeddings", "Groq"],
        "icon": "assets/img/icons/assistant.svg",
    },
]

# Smaller, earlier projects -- not part of this site's live demos, just
# named + linked for range (Rails/game-dev, not just Flask) and, in
# Beeznest's case, concrete social proof (a placement).
EARLIER_PROJECTS = [
    {
        "title": "Beeznest",
        "blurb": "B2B networking platform built with Ruby on Rails and SQLite. "
        "2nd place at StreetCode Accelerator Demo Day.",
        "url": "https://github.com/nellykelly/BezzNest",
    },
]

# Timed-Squares' original JS/Processing + Python/Pygame builds used to be
# listed here as a decommissioned earlier project (the old tinyurl no
# longer resolved). It's been recreated as a real, playable flagship
# project instead -- see the "timed-squares" entry in PROJECTS above --
# so the dead-link archive entry is retired rather than kept alongside a
# live version of the same game.


def listed_projects() -> list[dict]:
    """Everything shown in a project listing anywhere on the site.

    A project marked `on_hold` keeps working at its own URL -- its routes
    are registered exactly as before -- but is dropped from every listing,
    so it's reachable only by someone who already has the link. That's a
    display decision, deliberately not an access-control one: nothing here
    hides data or gates a route, so it must not be mistaken for security.
    """
    return [p for p in PROJECTS if not p.get("on_hold")]


@bp.route("")
def index():
    return render_template(
        "projects/index.html",
        projects=listed_projects(),
        earlier_projects=EARLIER_PROJECTS,
    )
