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


@bp.route("")
def index():
    return render_template("projects/index.html", projects=PROJECTS)
