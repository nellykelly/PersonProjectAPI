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
        # On hold: kept running and reachable at its own URL, but not
        # listed or linked anywhere on the site. Left in this list rather
        # than deleted so the metadata survives and un-holding it is a
        # one-line change -- see `listed_projects()`.
        "on_hold": True,
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
        "blurb": "A turn-based survival game on a 10x10 grid, playable right in the browser -- "
        "dodge obstacles that telegraph their next move before they make it, and outlast "
        "an escalating spawn rate. Public leaderboard, no login.",
        "endpoint": "timed_squares.index",
        "tags": ["Canvas", "Vanilla JS", "Public leaderboard"],
        "icon": "assets/img/icons/timed-squares.svg",
    },
    {
        "slug": "leetcode-150",
        "title": "Top Interview 150 Tracker",
        "blurb": "A personal interview-prep dashboard over LeetCode's official Top Interview "
        "150. Pick a problem, run the 20-minute timer, compare to the solution, then mark it "
        "Yes or No -- progress is saved in the browser, no login.",
        "endpoint": "leetcode.index",
        "tags": ["Vanilla JS", "localStorage", "No backend state"],
        "icon": "assets/img/icons/leetcode.svg",
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
