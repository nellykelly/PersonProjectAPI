from flask import render_template

from app.blueprints.projects import bp

PROJECTS = [
    {
        "slug": "trading-simulator",
        "title": "Trading Simulator / PnL Tracker",
        "blurb": "A shared, anonymous public trade book -- open a simulated stock or "
        "option position and watch PnL update against live-polled market data.",
        "endpoint": "trading.index",
        "tags": ["Flask", "yfinance", "SQLite", "Black-Scholes"],
        "icon": "assets/img/icons/trading.svg",
    },
    {
        "slug": "qr-quant-scraper",
        "title": "QR -- Quant Company Scorer",
        "blurb": "Scores a company across valuation, leverage, growth, and profitability "
        "using SEC EDGAR filings + market data, then backtests whether the score "
        "actually predicted forward returns.",
        "endpoint": "qr.index",
        "tags": ["SEC EDGAR API", "yfinance", "Backtesting"],
        "icon": "assets/img/icons/qr-scorer.svg",
    },
    {
        "slug": "pipeline-world",
        "title": "Pipeline World",
        "blurb": "Submit a character and watch it move through a real, queued CI/CD-style "
        "pipeline (validate, test, build, deploy) live in a top-down world -- with a "
        "genuine SQL analytics page over every run logged.",
        "endpoint": "pipeline_world.index",
        "tags": ["RQ + Redis", "Socket.IO", "Postgres", "Window Functions"],
        "icon": "assets/img/icons/pipeline-world.svg",
    },
    {
        "slug": "sre-infra",
        "title": "SRE Infra Layer",
        "blurb": "The Redis-backed queue, cache-aside world state, and per-IP rate limiting "
        "underneath Pipeline World -- written up and dashboarded as its own piece.",
        "endpoint": "sre_infra.index",
        "tags": ["Redis", "Cache-Aside", "Rate Limiting"],
        "icon": "assets/img/icons/sre-infra.svg",
    },
    {
        "slug": "network-sniffer",
        "title": "Network Sniffer",
        "blurb": "A live dashboard of this app's OWN network traffic -- every inbound "
        "request it serves and every outbound API call it makes -- not visitor "
        "browsing traffic (see the disclaimer on the page for why).",
        "endpoint": "sniffer.index",
        "tags": ["Flask hooks", "Server-Sent Events"],
        "icon": "assets/img/icons/sniffer.svg",
    },
]

# Smaller, earlier projects -- not part of this site's live demos, just
# named + linked for range (Rails/game-dev, not just Flask) and, in
# Beeznest's case, concrete social proof (a placement).
EARLIER_PROJECTS = [
    {
        "title": "Beeznest",
        "blurb": "B2B networking platform built with Ruby on Rails + SQLite. "
        "2nd place at StreetCode Accelerator Demo Day.",
        "url": "https://github.com/nellykelly/BezzNest",
    },
    {
        "title": "Timed-Squares",
        "blurb": "Turn-based puzzle game built with JavaScript/Processing and Python/Pygame.",
        "url": "http://tinyurl.com/nhsjcdh",
    },
]


@bp.route("")
def index():
    return render_template("projects/index.html", projects=PROJECTS, earlier_projects=EARLIER_PROJECTS)
